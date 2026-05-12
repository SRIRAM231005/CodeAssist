import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "New_Model"))
import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
import google.generativeai as genai

from New_Model.ast_engine import fetch_github_file, extract_all_functions
from New_Model.embedder import CodeEmbedder, QdrantRetriever
from New_Model.llm_analyzer import LLMAnalyzer
from New_Model.router import DecisionRouter
from New_Model.redis_cache import RedisCache

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
QDRANT_URL      = os.getenv("QDRANT_URL")
QDRANT_API_KEY  = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "codeassist")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD")
REDIS_PORT      = int(os.getenv("REDIS_PORT", 6379))
MAX_DEPTH       = int(os.getenv("MAX_DEPTH", 5))
TOP_K           = int(os.getenv("TOP_K", 5))

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

    print("[startup] Connecting to ChromaDB...")
    app.state.retriever = QdrantRetriever(qdrant_url=QDRANT_URL, qdrant_api_key=QDRANT_API_KEY, collection_name=COLLECTION_NAME, top_k=TOP_K)

    print("[startup] Connecting to Redis...")
    app.state.cache = RedisCache(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, ssl=True)

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

    @field_validator("repo_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        if not v.startswith("https://github.com/"):
            raise ValueError("repo_url must be a valid GitHub URL (https://github.com/...)")
        return v.rstrip("/")

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        if not v.endswith(".py"):
            raise ValueError("Only Python files (.py) are supported at this time.")
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
    lowered = [n.lower() for n in name_candidates]
    match   = next((fn for fn in fns if fn["name"].lower() in lowered), None)
    return match["name"] if match else (fns[0]["name"] if fns else None)


async def send(ws: WebSocket, msg_type: str, **kwargs):
    """Send a typed JSON message over the WebSocket."""
    await ws.send_text(json.dumps({"type": msg_type, **kwargs}))


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

    # ── Pull shared resources from app state ──────────────────────────────────
    embedder:  CodeEmbedder    = app.state.embedder
    retriever: ChromaRetriever = app.state.retriever
    llm:       LLMAnalyzer     = app.state.llm
    cache:     RedisCache      = app.state.cache

    try:
        # ── Step 1: Fetch file from GitHub ────────────────────────────────────
        await send(websocket, "spinner", message="Fetching file from GitHub...")
        try:
            source = await asyncio.to_thread(fetch_github_file, repo_url, file_path, branch)
            fns    = await asyncio.to_thread(extract_all_functions, source)
        except Exception as e:
            await send(websocket, "error", detail=f"Failed to fetch file: {str(e)}")
            job["status"] = "failed"
            await websocket.close()
            return

        if not fns:
            await send(websocket, "error", detail="No Python functions found in the specified file.")
            job["status"] = "failed"
            await websocket.close()
            return

        # ── Step 2: Parse query + identify target function ────────────────────
        await send(websocket, "spinner", message="Understanding your query...")
        try:
            parsed          = await asyncio.to_thread(llm.parse_query, user_query)
            name_candidates = parsed.get("function_name_candidates", [])
            if(name_candidates):
                function_name   = filter_function(name_candidates, fns)
            else:
                function_name
            intent = parsed.get("intent")
        except Exception as e:
            await send(websocket, "error", detail=f"Query parsing failed: {str(e)}")
            job["status"] = "failed"
            await websocket.close()
            return

        await send(websocket, "spinner", message=f"Target function identified: {function_name}")

        # ── Step 3: ChromaDB similarity search ───────────────────────────────
        await send(websocket, "spinner", message="Searching knowledge base...")
        try:
            query_embedding   = await asyncio.to_thread(embedder.embed, user_query)
            retrieved_context = await asyncio.to_thread(retriever.retrieve, query_embedding)
        except Exception as e:
            await send(websocket, "error", detail=f"Knowledge base search failed: {str(e)}")
            job["status"] = "failed"
            await websocket.close()
            return

        # ── Step 4: Deep traversal + LLM analysis ────────────────────────────
        await send(websocket, "spinner", message="Running deep analysis...")

        # Bridge sync progress_callback → async WebSocket via a queue
        loop      = asyncio.get_event_loop()
        log_queue: asyncio.Queue = asyncio.Queue()

        def progress_callback(message: str):
            """Called synchronously by DecisionRouter; pushes into async queue."""
            loop.call_soon_threadsafe(log_queue.put_nowait, message)

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
            # Flush any remaining messages after task completes
            while not log_queue.empty():
                await send(websocket, "log", message=log_queue.get_nowait())

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
        try:
            await websocket.close()
        except Exception:
            pass


# ── GET /health ───────────────────────────────────────────────────────────────
@app.get("/health", summary="System health check")
async def health():
    """Returns status of all backend services: Redis, embedder, retriever, LLM."""
    cache: RedisCache = app.state.cache
    stats = cache.get_stats()
    return {
        "redis":     stats,
        "embedder":  "loaded" if app.state.embedder  else "unavailable",
        "retriever": "loaded" if app.state.retriever else "unavailable",
        "llm":       "loaded" if app.state.llm       else "unavailable",
    }


# ── GET /cache/stats ──────────────────────────────────────────────────────────
@app.get("/cache/stats", summary="Redis cache statistics")
async def cache_stats():
    """Returns number of cached nodes in Redis."""
    cache: RedisCache = app.state.cache
    return cache.get_stats()


# ── POST /cache/clear ─────────────────────────────────────────────────────────
@app.post("/cache/clear", summary="Clear Redis cache")
async def cache_clear():
    """Flushes all keys under the codeassist:* namespace from Redis."""
    cache: RedisCache = app.state.cache

    if not cache.available:
        raise HTTPException(
            status_code=503,
            detail="Redis is not available. Cannot clear cache.",
        )

    keys = cache.client.keys("codeassist:*")
    for k in keys:
        cache.client.delete(k)

    return {"deleted_keys": len(keys), "message": f"Cleared {len(keys)} cached nodes."}