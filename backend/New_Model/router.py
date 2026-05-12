from collections import deque
from typing import Optional
from dataclasses import dataclass

from .chunker import get_full_context
from .ast_engine import (
    fetch_github_file,
    extract_function_node,
    extract_all_functions,
    resolve_dependencies,
    compute_ast_hash,
    resolve_file_path_from_module
)
from .llm_analyzer import LLMAnalyzer
from .embedder import CodeEmbedder, QdrantRetriever
from .redis_cache import RedisCache
from .dependency_graph import DependencyGraph, GraphNode, NodeStatus


@dataclass
class TraversalItem:
    file_path: str
    function_name: str
    depth: int
    parent_file: Optional[str] = None
    parent_fn: Optional[str] = None


class DecisionRouter:
    """
    Orchestrates the full traversal loop:
    Cache → AST → LLM → Decision → Next node
    """

    def __init__(
        self,
        repo_url: str,
        branch: str,
        llm: LLMAnalyzer,
        embedder: CodeEmbedder,
        retriever: QdrantRetriever,
        cache: RedisCache,
        max_depth: int = 5
    ):
        self.repo_url = repo_url
        self.branch = branch
        self.llm = llm
        self.embedder = embedder
        self.retriever = retriever
        self.cache = cache
        self.max_depth = max_depth
        self.graph = DependencyGraph()
        self._file_cache: dict[str, str] = {}   # filepath -> raw source (in-memory for this session)

    def _get_file(self, file_path: str) -> Optional[str]:
        if file_path not in self._file_cache:
            try:
                self._file_cache[file_path] = fetch_github_file(self.repo_url, file_path, self.branch)
            except Exception:
                return None
        return self._file_cache[file_path]

    def run(
        self,
        entry_file: str,
        entry_function: str,
        raw_query: str,
        intent: str,
        retrieved_context: list,
        progress_callback=None
    ) -> dict:
        """
        Main traversal loop.
        Returns: { status, report, graph, mermaid }
        """

        queue = deque([TraversalItem(entry_file, entry_function, depth=0)])
        final_result = None

        while queue:
            item = queue.popleft()

            # Cycle protection
            if self.graph.is_visited(item.file_path, item.function_name):
                continue

            # Depth limit
            if item.depth > self.max_depth:
                if progress_callback:
                    progress_callback(f"⚠️ Max depth reached at `{item.function_name}`")
                continue

            if progress_callback:
                progress_callback(f"🔍 Analyzing `{item.function_name}` in `{item.file_path}` (depth {item.depth})")

            # ── STEP 1: Fetch source ──────────────────────────────────────
            source = self._get_file(item.file_path)
            if not source:
                if progress_callback:
                    progress_callback(f"⚠️ Could not fetch `{item.file_path}` — skipping")
                continue

            # ── STEP 2: Extract function ──────────────────────────────────
            if item.function_name:
                fn_code = extract_function_node(source, item.function_name)
                if not fn_code:
                    if progress_callback:
                        progress_callback(f"⚠️ Function `{item.function_name}` not found in `{item.file_path}`")
                    continue
            else:
                fn_code = get_full_context(source, self.raw_query, self.embedder)

            ast_hash = compute_ast_hash(fn_code)

            # ── STEP 3: Cache check ───────────────────────────────────────
            from_cache = False
            cached = None
            if not self.cache.is_stale(item.file_path, item.function_name, ast_hash):
                cached = self.cache.get(item.file_path, item.function_name)
                if cached and cached.get("llm_result"):
                    from_cache = True
                    if progress_callback:
                        progress_callback(f"⚡ Cache hit for `{item.function_name}`")
            
            # ── STEP 4: Add to graph ──────────────────────────────────────
            node = GraphNode(
                function_name=item.function_name,
                file_path=item.file_path,
                code=fn_code,
                ast_hash=ast_hash,
                depth=item.depth,
                from_cache=from_cache
            )
            self.graph.add_node(node)

            # Add edge from parent
            if item.parent_file and item.parent_fn:
                self.graph.add_edge(item.parent_file, item.parent_fn, item.file_path, item.function_name)

            # ── STEP 5: LLM Analysis ──────────────────────────────────────
            if from_cache and cached:
                llm_result = cached["llm_result"]
            else:
                llm_result = self.llm.analyze_function(
                    function_code=fn_code,
                    function_name=item.function_name,
                    file_path=item.file_path,
                    graph_summary=self.graph.to_summary(),
                    retrieved_context=retrieved_context,
                    raw_query=raw_query,
                    intent=intent,
                    depth=item.depth
                )

                # Store in Redis cache
                self.cache.set(item.file_path, item.function_name, {
                    "ast_hash": ast_hash,
                    "code": fn_code,
                    "llm_result": llm_result
                })

            # ── STEP 6: Decision routing ──────────────────────────────────
            status = llm_result.get("status", "clean")

            if status == "bug_found":
                bug_info = llm_result.get("bug")
                self.graph.update_status(item.file_path, item.function_name, NodeStatus.BUG_FOUND, bug_info)

                if progress_callback:
                    progress_callback(f"🐛 Bug found in `{item.function_name}`!")

                report = self.llm.generate_final_report(
                    graph_summary=self.graph.to_summary(),
                    bug_info={
                        "function": item.function_name,
                        "file": item.file_path,
                        "depth": item.depth,
                        **bug_info
                    },
                    raw_query=raw_query,
                    intent=intent
                )

                final_result = {
                    "status": "bug_found",
                    "report": report,
                    "bug": bug_info,
                    "found_in": {"function": item.function_name, "file": item.file_path},
                    "graph": self.graph.to_summary(),
                    "mermaid": self.graph.to_mermaid()
                }
                break  # Stop traversal

            elif status == "needs_deeper":
                self.graph.update_status(item.file_path, item.function_name, NodeStatus.CLEAN)
                check_next = llm_result.get("check_next", [])

                if progress_callback:
                    progress_callback(f"✅ `{item.function_name}` clean — diving into: {check_next}")

                # Resolve dependencies via AST
                deps = resolve_dependencies(source, item.function_name)

                for fn_name in check_next:
                    if self.graph.is_visited(item.file_path, fn_name):
                        continue

                    # Is it a local function?
                    if fn_name in [f["name"] for f in extract_all_functions(source)]:
                        queue.append(TraversalItem(
                            file_path=item.file_path,
                            function_name=fn_name,
                            depth=item.depth + 1,
                            parent_file=item.file_path,
                            parent_fn=item.function_name
                        ))
                    else:
                        # Find which module it's imported from
                        module = None
                        for imp in deps["imported_calls"]:
                            if imp["name"] == fn_name:
                                module = imp["module"]
                                break

                        if module:
                            resolved_path = resolve_file_path_from_module(
                                module, self.repo_url, self.branch, item.file_path
                            )
                            if resolved_path:
                                queue.append(TraversalItem(
                                    file_path=resolved_path,
                                    function_name=fn_name,
                                    depth=item.depth + 1,
                                    parent_file=item.file_path,
                                    parent_fn=item.function_name
                                ))
                            else:
                                if progress_callback:
                                    progress_callback(f"⚠️ `{fn_name}` from `{module}` — external/unresolvable, skipping")

            else:  # clean
                self.graph.update_status(item.file_path, item.function_name, NodeStatus.CLEAN)
                if progress_callback:
                    progress_callback(f"✅ `{item.function_name}` is clean")

        # ── FINAL: No bugs found ──────────────────────────────────────────
        if not final_result:
            report = self.llm.generate_clean_report(
                graph_summary=self.graph.to_summary(),
                raw_query=raw_query,
                intent=intent
            )
            final_result = {
                "status": "clean",
                "report": report,
                "bug": None,
                "graph": self.graph.to_summary(),
                "mermaid": self.graph.to_mermaid()
            }

        return final_result
