import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from starlette.websockets import WebSocketState
import google.generativeai as genai

from New_Model.ast_engine import fetch_github_file, extract_all_functions
from New_Model.chunker import _get_anchor_function
from New_Model.embedder import CodeEmbedder, QdrantRetriever
from New_Model.llm_analyzer import LLMAnalyzer
from New_Model.router import DecisionRouter
from New_Model.redis_cache import RedisCache

# Diagnostics & slicing – error-log-guided analysis (optional; failures fall back silently)
try:
    from New_Model.diagnostics.log_parser import parse_error_log
    from New_Model.diagnostics.backward_slicer import compute_backward_slice
    from New_Model.cfg.builder import build_cfg
    from New_Model.static_analysis.analyzer import run_static_analysis
    _DIAGNOSTICS_AVAILABLE = True
except Exception:
    _DIAGNOSTICS_AVAILABLE = False

load_dotenv(dotenv_path=os.path.join(BACKEND_DIR, ".env"))
load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
QDRANT_URL      = os.getenv("QDRANT_URL")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "codeassist")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD")
REDIS_PORT      = int(os.getenv("REDIS_PORT") or 6379)
REDIS_SSL       = os.getenv("REDIS_SSL", "true" if REDIS_HOST and "upstash.io" in REDIS_HOST else "false").lower() in ("true", "1", "yes")
MAX_DEPTH       = int(os.getenv("MAX_DEPTH") or 5)
TOP_K           = int(os.getenv("TOP_K") or 5)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ── In-memory job store ───────────────────────────────────────────────────────
# Holds validated job payloads between POST /analyze and WS /ws/{job_id}.
# To scale: replace with Redis hash → cache.client.hset(f"job:{job_id}", ...)
job_store: dict[str, dict[str, Any]] = {}


# ── Lifespan: load heavy resources once at startup ────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] Loading CodeBERT embedder...")
    app.state.embedder = CodeEmbedder()

    print("[startup] Connecting to Qdrant...")
    app.state.retriever = QdrantRetriever(qdrant_url=QDRANT_URL, qdrant_api_key=QDRANT_API_KEY, collection_name=COLLECTION_NAME, top_k=TOP_K)

    print("[startup] Connecting to Redis...")
    app.state.cache = RedisCache(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, ssl=REDIS_SSL)

    print("[startup] Loading LLM (Gemini)...")
    app.state.llm = LLMAnalyzer("gemini-2.5-flash")

    print("[startup] All resources ready.")
    yield

    print("[shutdown] Cleaning up...")
    job_store.clear()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CodeAssist API",
    description="Deep code analysis with AST traversal, dependency graph, and LLM-guided debugging.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    repo_url:   str
    file_path:  str
    branch:     str = "main"
    user_query: str
    max_depth:  int = MAX_DEPTH
    error_log:  Optional[str] = None   # Stage 4: optional error/traceback log

    @field_validator("repo_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        if not v.startswith("https://github.com/"):
            raise ValueError("repo_url must be a valid GitHub URL (https://github.com/...)")
        v = v.rstrip("/")
        if v.endswith(".git"):
            v = v[:-4]
        return v

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        SUPPORTED_EXTENSIONS = (".py", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")
        if not any(v.endswith(ext) for ext in SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Only Python (.py) and C/C++ ({', '.join(SUPPORTED_EXTENSIONS)}) files are supported."
            )
        return v.lstrip("/")

    @field_validator("max_depth")
    @classmethod
    def validate_depth(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("max_depth must be between 1 and 10.")
        return v

    @field_validator("user_query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if len(v.strip()) < 5:
            raise ValueError("user_query is too short. Please describe your question.")
        return v.strip()


class AnalyzeResponse(BaseModel):
    job_id:  str
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────
def filter_function(name_candidates: list[str], fns: list[dict]) -> str | None:
    """Return the best matching function name from extracted AST functions."""
    if not fns or not name_candidates:
        return None

    cleaned_candidates = []
    for c in name_candidates:
        if not isinstance(c, str):
            continue
        cleaned = c.strip().rstrip("()").strip()
        if cleaned.startswith("def "):
            cleaned = cleaned[4:].strip()
        if cleaned:
            cleaned_candidates.append(cleaned.lower())

    if not cleaned_candidates:
        return None

    # 1. Exact match (case-insensitive)
    for fn in fns:
        if fn["name"].lower() in cleaned_candidates:
            return fn["name"]

    # 2. Match method name in ClassName.method_name
    for fn in fns:
        method = fn["name"].split(".")[-1].lower()
        if method in cleaned_candidates:
            return fn["name"]

    # 3. Substring match
    for cand in cleaned_candidates:
        for fn in fns:
            fn_lower = fn["name"].lower()
            if cand in fn_lower or fn_lower in cand:
                return fn["name"]

    return None


async def send(ws: WebSocket, msg_type: str, **kwargs):
    """Send a typed JSON message over the WebSocket if connected."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(json.dumps({"type": msg_type, **kwargs}))
    except Exception:
        pass


async def safe_close(ws: WebSocket, code: int = 1000):
    """Safely close WebSocket if still connected."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close(code=code)
    except Exception:
        pass


# ── POST /analyze ─────────────────────────────────────────────────────────────
@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Submit a code analysis job",
    description=(
        "Validates the request payload and registers a job. "
        "Returns a job_id — connect to /ws/{job_id} to start streaming analysis."
    ),
)
async def submit_analyze(request: AnalyzeRequest):
    """
    Entry point for all analysis requests.

    Extensible pipeline — add steps here before analysis begins:
      1. ✅ Input validation          (Pydantic, runs automatically)
      2. 🔒 Auth / API key checks     (add middleware or inject here)
      3. 🚦 Rate limiting             (e.g. slowapi)
      4. 💳 Billing / quota checks    (inject service here)
      5. 🔍 Preprocessing             (resolve branch → commit SHA, verify repo access)
      6. 📋 Job registration          (currently in-memory, swap to Redis to scale)

    The actual analysis runs when the client opens /ws/{job_id}.
    """
    job_id = str(uuid.uuid4())

    job_store[job_id] = {
        "status":  "pending",
        "payload": request.model_dump(),
    }

    return AnalyzeResponse(
        job_id=job_id,
        message=f"Job registered. Connect to /ws/{job_id} to begin analysis.",
    )


# ── WS /ws/{job_id} ───────────────────────────────────────────────────────────
@app.websocket("/ws/{job_id}")
async def analyze_ws(websocket: WebSocket, job_id: str):
    """
    WebSocket endpoint — streams analysis progress and final result.

    Message types sent to client:
      spinner  →  long-running step in progress   { message }
      log      →  traversal progress update        { message }
      result   →  final analysis output            { data: { status, report, graph, mermaid, ... } }
      error    →  something went wrong             { detail }
    """
    await websocket.accept()

    # ── Validate job ──────────────────────────────────────────────────────────
    job = job_store.get(job_id)
    if not job:
        await send(websocket, "error", detail=f"Job '{job_id}' not found. POST /analyze first.")
        await websocket.close(code=1008)
        return

    if job["status"] != "pending":
        await send(websocket, "error", detail=f"Job '{job_id}' is already {job['status']}.")
        await websocket.close(code=1008)
        return

    job["status"] = "running"

    # ── Unpack payload ────────────────────────────────────────────────────────
    payload    = job["payload"]
    repo_url   = payload["repo_url"]
    file_path  = payload["file_path"]
    branch     = payload["branch"]
    user_query = payload["user_query"]
    max_depth  = payload["max_depth"]
    error_log  = payload.get("error_log") or ""   # Stage 4: may be absent/None

    # ── Pull shared resources from app state ──────────────────────────────────
    embedder:  CodeEmbedder    = app.state.embedder
    retriever: QdrantRetriever = app.state.retriever
    llm:       LLMAnalyzer     = app.state.llm
    cache:     RedisCache      = app.state.cache

    try:
        # ── Step 1: Fetch file from GitHub ────────────────────────────────────
        await send(websocket, "spinner", message="Fetching file from GitHub...")
        try:
            source = await asyncio.to_thread(fetch_github_file, repo_url, file_path, branch)
            fns    = await asyncio.to_thread(extract_all_functions, source, file_path)
        except Exception as e:
            await send(websocket, "error", detail=f"Failed to fetch file: {str(e)}")
            job["status"] = "failed"
            return

        if not fns:
            await send(websocket, "error", detail="No functions found in the specified file.")
            job["status"] = "failed"
            return

        # ── Step 2: Parse query + identify target function ────────────────────
        await send(websocket, "spinner", message="Understanding your query...")

        # ── Stage 4: attempt error-log-guided anchor resolution ───────────────
        log_anchor_used  = False   # True when the log successfully set the anchor
        slice_context: Optional[dict] = None   # filled when backward slice succeeds

        if error_log and _DIAGNOSTICS_AVAILABLE:
            try:
                known_files = [fn["file"] for fn in fns if "file" in fn]
                if not known_files:
                    known_files = [file_path]

                log_result = parse_error_log(error_log, known_files)

                if log_result is not None:
                    # Find which function (if any) contains log_result.line
                    matched_fn = None
                    for fn in fns:
                        span = fn.get("span") or fn.get("location")
                        if span and isinstance(span, (list, tuple)) and len(span) >= 2:
                            start_ln, end_ln = int(span[0]), int(span[1])
                            if start_ln <= log_result.line <= end_ln:
                                matched_fn = fn["name"]
                                break

                    if matched_fn:
                        function_name = matched_fn
                        log_anchor_used = True
                        await send(websocket, "spinner",
                                   message=f"[log] Anchor from error log: {function_name} "
                                           f"(line {log_result.line})")

                        # Attempt backward slice for this function
                        try:
                            # Find the function's source code from fns
                            fn_rec = next((f for f in fns if f["name"] == function_name), None)
                            fn_code = fn_rec.get("code", "") if fn_rec else ""

                            cfg = build_cfg(fn_code, file_path, function_name)
                            if cfg is not None:
                                from New_Model.static_analysis.reaching_defs import ReachingDefinitionsAnalyzer
                                from New_Model.static_analysis.liveness import LivenessAnalyzer
                                rda = ReachingDefinitionsAnalyzer(cfg)
                                rda.analyze()

                                slice_res = compute_backward_slice(
                                    cfg,
                                    rda.def_use_chains,
                                    log_result.line,
                                )

                                # Collect static findings that touch variables in slice
                                lva = LivenessAnalyzer(cfg)
                                _, _, lva_findings = lva.analyze()
                                slice_var_names: set = set()
                                for _, txt in slice_res.slice_statements:
                                    import re as _re
                                    slice_var_names.update(_re.findall(r'\b([A-Za-z_]\w*)\b', txt))

                                relevant_findings = [
                                    f.message for f in lva_findings
                                    if any(v in f.message for v in slice_var_names)
                                ] if slice_var_names else []

                                slice_context = {
                                    "error_type":     log_result.error_type,
                                    "error_message":  log_result.error_message,
                                    "failing_line":   log_result.line,
                                    "slice_statements": slice_res.slice_statements,
                                    "static_findings": "\n".join(relevant_findings),
                                }
                        except Exception as _slice_err:
                            print(f"[ws] Stage4 slice failed (non-fatal): {_slice_err}")
                            slice_context = {
                                "error_type":    log_result.error_type,
                                "error_message": log_result.error_message,
                                "failing_line":  log_result.line,
                                "slice_statements": [],
                                "static_findings": "",
                            }

            except Exception as _log_err:
                print(f"[ws] Stage4 log parse failed (non-fatal): {_log_err}")

        # Build error_context hint for parse_query (error type + message, when available)
        error_context_hint: Optional[str] = None
        if slice_context:
            error_context_hint = f"{slice_context['error_type']}: {slice_context['error_message']}"

        parsed = {}
        try:
            parsed = await asyncio.to_thread(llm.parse_query, user_query, error_context_hint)
        except Exception as e:
            print(f"[ws] Warning: llm.parse_query failed ({e}), using fallback")

        if not isinstance(parsed, dict):
            parsed = {}

        intent = parsed.get("intent") or "bug_explanation"

        # Only run LLM name extraction + CodeBERT anchor when log did NOT set one
        if not log_anchor_used:
            raw_candidates = parsed.get("function_name_candidates", [])
            if isinstance(raw_candidates, str):
                name_candidates = [raw_candidates]
            elif isinstance(raw_candidates, list):
                name_candidates = [str(c) for c in raw_candidates if c]
            else:
                name_candidates = []

            # 1. Match against extracted functions using candidates
            function_name = filter_function(name_candidates, fns)

            # 2. If not matched, use semantic similarity (CodeBERT anchor function)
            if not function_name and embedder:
                try:
                    anchor = await asyncio.to_thread(_get_anchor_function, fns, user_query, embedder)
                    if anchor and "name" in anchor:
                        function_name = anchor["name"]
                except Exception as e:
                    print(f"[ws] Warning: Semantic anchor function search failed: {e}")

            # 3. Final fallback: default to the first function in the file
            if not function_name and fns:
                function_name = fns[0]["name"]

        await send(websocket, "spinner", message=f"Target function identified: {function_name}")

        # ── Step 3: Qdrant similarity search ─────────────────────────────────
        await send(websocket, "spinner", message="Searching knowledge base...")
        retrieved_context = []
        try:
            query_embedding   = await asyncio.to_thread(embedder.embed, user_query)
            retrieved_context = await asyncio.to_thread(retriever.retrieve, query_embedding)
        except Exception as e:
            print(f"[ws] Warning: Knowledge base search failed: {e}")
            # Non-fatal: analysis can proceed with AST and LLM reasoning

        # ── Step 4: Deep traversal + LLM analysis ────────────────────────────
        await send(websocket, "spinner", message="Running deep analysis...")

        # Bridge sync progress_callback → async WebSocket via a queue
        loop      = asyncio.get_running_loop()
        log_queue: asyncio.Queue = asyncio.Queue()

        def progress_callback(message: str):
            """Called synchronously by DecisionRouter; pushes into async queue."""
            try:
                loop.call_soon_threadsafe(log_queue.put_nowait, message)
            except RuntimeError:
                pass

        router = DecisionRouter(
            repo_url=repo_url,
            branch=branch,
            llm=llm,
            embedder=embedder,
            retriever=retriever,
            cache=cache,
            max_depth=max_depth,
        )

        # Wrap router.run as an asyncio Task running in a thread pool
        analysis_task = asyncio.ensure_future(
            asyncio.to_thread(
                router.run,
                entry_file=file_path,
                entry_function=function_name,
                raw_query=user_query,
                intent=intent,
                retrieved_context=retrieved_context,
                progress_callback=progress_callback,
                slice_context=slice_context,    # Stage 4: may be None
            )
        )

        # Drain log queue concurrently while analysis runs
        async def drain_logs():
            while not analysis_task.done():
                try:
                    message = await asyncio.wait_for(log_queue.get(), timeout=0.05)
                    await send(websocket, "log", message=message)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
            # Flush any remaining messages after task completes
            while not log_queue.empty():
                try:
                    await send(websocket, "log", message=log_queue.get_nowait())
                except Exception:
                    break

        await asyncio.gather(analysis_task, drain_logs())
        result = analysis_task.result()

        # ── Step 5: Send final result ─────────────────────────────────────────
        await send(websocket, "result", data={
            "status":   result["status"],
            "report":   result["report"],
            "graph":    result["graph"],
            "mermaid":  result["mermaid"],
            "found_in": result.get("found_in"),
            "bug":      result.get("bug"),
        })

        job["status"] = "completed"

    except WebSocketDisconnect:
        print(f"[ws] Client disconnected from job {job_id}")
        job["status"] = "disconnected"

    except Exception as e:
        job["status"] = "failed"
        try:
            await send(websocket, "error", detail=f"Unexpected error: {str(e)}")
        except Exception:
            pass

    finally:
        # Remove job from store — to persist results, write to Redis here instead
        job_store.pop(job_id, None)
        await safe_close(websocket)


# ── GET /health ───────────────────────────────────────────────────────────────
@app.get("/health", summary="System health check")
async def health():
    """Returns status of all backend services: Redis, embedder, retriever, LLM."""
    cache: RedisCache | None = getattr(app.state, "cache", None)
    stats = cache.get_stats() if cache else {"status": "unavailable"}
    return {
        "redis":     stats,
        "embedder":  "loaded" if getattr(app.state, "embedder", None)  else "unavailable",
        "retriever": "loaded" if getattr(app.state, "retriever", None) else "unavailable",
        "llm":       "loaded" if getattr(app.state, "llm", None)       else "unavailable",
    }


# ── GET /cache/stats ──────────────────────────────────────────────────────────
@app.get("/cache/stats", summary="Redis cache statistics")
async def cache_stats():
    """Returns number of cached nodes in Redis."""
    cache: RedisCache | None = getattr(app.state, "cache", None)
    if not cache:
        return {"status": "unavailable"}
    return cache.get_stats()


# ── POST /cache/clear ─────────────────────────────────────────────────────────
@app.post("/cache/clear", summary="Clear Redis cache")
async def cache_clear():
    """Flushes all keys under the codeassist:* namespace from Redis."""
    cache: RedisCache | None = getattr(app.state, "cache", None)

    if not cache or not cache.available:
        raise HTTPException(
            status_code=503,
            detail="Redis is not available. Cannot clear cache.",
        )

    keys = cache.client.keys("codeassist:*")
    if keys:
        cache.client.delete(*keys)

    return {"deleted_keys": len(keys), "message": f"Cleared {len(keys)} cached nodes."}