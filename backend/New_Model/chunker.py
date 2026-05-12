import ast
import numpy as np
from typing import TypedDict

from .ast_engine import extract_all_functions


# ── Types ─────────────────────────────────────────────────────────────────────
class LineChunk(TypedDict):
    lines: str
    start_line: int
    end_line: int


# ── Internal helpers ──────────────────────────────────────────────────────────
def _cosine_similarity(a, b) -> float:
    """Cosine similarity between two numpy vectors."""
    a, b = np.array(a), np.array(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _extract_line_chunks(source: str) -> list[LineChunk]:
    """
    Extracts global-level line chunks from source code.
    Excludes lines inside functions or classes.
    Blank lines act as chunk boundaries.
    Consecutive non-blank global lines form one chunk.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Collect all line numbers occupied by functions and classes
    occupied_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            for ln in range(node.lineno, end + 1):
                occupied_lines.add(ln)

    all_lines = source.splitlines()
    chunks: list[LineChunk] = []
    current_group: list[tuple[int, str]] = []

    for i, line in enumerate(all_lines):
        line_no = i + 1  # 1-indexed

        if line_no in occupied_lines:
            if current_group:
                chunks.append(_make_chunk(current_group))
                current_group = []
            continue

        stripped = line.strip()
        if stripped == "":
            if current_group:
                chunks.append(_make_chunk(current_group))
                current_group = []
        else:
            current_group.append((line_no, line))

    if current_group:
        chunks.append(_make_chunk(current_group))

    return chunks


def _make_chunk(group: list[tuple[int, str]]) -> LineChunk:
    """Build a LineChunk from a group of (line_no, line_text) pairs."""
    return LineChunk(
        lines="\n".join(line for _, line in group),
        start_line=group[0][0],
        end_line=group[-1][0],
    )


def _get_anchor_function(fns: list[dict], user_query: str, embedder) -> dict | None:
    """
    Finds the most similar function to the user query using embedding similarity.
    Returns the {"name": ..., "code": ...} dict of the best match.
    """
    if not fns:
        return None

    query_embedding = embedder.embed(user_query)
    best_score = -1.0
    best_fn = None

    for fn in fns:
        fn_embedding = embedder.embed(fn["code"])
        score = _cosine_similarity(query_embedding, fn_embedding)
        if score > best_score:
            best_score = score
            best_fn = fn

    return best_fn


def _get_top_k_line_chunks(
    anchor_code: str,
    line_chunks: list[LineChunk],
    embedder,
    top_k: int = 3,
    threshold: float = 0.5,
) -> list[LineChunk]:
    """
    Finds top-k line chunks most similar to the anchor function code.
    Uses anchor function as query vector — not the user query.
    """
    if not line_chunks:
        return []

    anchor_embedding = embedder.embed(anchor_code)
    scored: list[tuple[float, LineChunk]] = []

    for chunk in line_chunks:
        if not chunk["lines"].strip():
            continue
        chunk_embedding = embedder.embed(chunk["lines"])
        score = _cosine_similarity(anchor_embedding, chunk_embedding)
        if score >= threshold:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:top_k]]


# ── Public entry point ────────────────────────────────────────────────────────
def get_full_context(source: str, user_query: str, embedder) -> str:
    """
    Full chunking strategy — called by router.py when no function name is provided.

    Steps:
        1. Extract all functions from source
        2. Find anchor function via embedding similarity with user query
        3. Extract global line chunks from source
        4. Find top-k line chunks most similar to anchor function
        5. Return combined string: whole source + relevant line context

    Args:
        source      : raw Python source code of the file
        user_query  : raw user query string
        embedder    : CodeEmbedder instance with .embed() method

    Returns:
        fn_code string ready to be used in router.py in place of extract_function_node()
    """
    # Step 1 — extract all functions
    fns = extract_all_functions(source)

    # Step 2 — find anchor function
    anchor_fn = _get_anchor_function(fns, user_query, embedder)

    # Step 3 — extract global line chunks
    line_chunks = _extract_line_chunks(source)

    # Step 4 — get top-k line chunks similar to anchor
    if anchor_fn:
        top_k_chunks = _get_top_k_line_chunks(
            anchor_code=anchor_fn["code"],
            line_chunks=line_chunks,
            embedder=embedder,
            top_k=3,
            threshold=0.5,
        )
    else:
        top_k_chunks = []

    # Step 5 — combine into fn_code string
    line_context = "\n\n".join(chunk["lines"] for chunk in top_k_chunks)

    fn_code = source
    if line_context:
        fn_code += f"\n\n# Relevant global context:\n{line_context}"

    return fn_code