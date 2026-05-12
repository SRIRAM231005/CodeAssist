# CodeAssist — Deep Code Analyzer

AST-guided, LLM-navigated code analysis with Redis caching and dependency graph traversal.

## Architecture

```
User Query
    │
    ├── CodeBERT embed → ChromaDB similarity search
    │
    └── Decision Router (orchestrator)
            │
            ├── Cache Layer (Redis) ← function-level, AST-hash validated
            ├── AST Engine          ← extract + resolve dependencies
            ├── LLM Analyzer        ← analyze + navigate (Gemini)
            └── Dependency Graph    ← tracks traversal, builds Mermaid chart
```

## Setup

### 1. Install dependencies
pip install -r requirements.txt

### 2. Configure environment
cp .env.example .env
# Fill in your GEMINI_API_KEY and paths

### 3. Start Redis
docker run -d -p 6379:6379 redis:alpine
# OR: redis-server

### 4. Make sure ChromaDB is populated
# Your existing offline CodeBERT + CodeSearchNet pipeline should have
# already populated the chroma_store. Point CHROMA_PATH to it.

### 5. Run
cd codeassist
streamlit run app.py

## How it works

1. User gives: GitHub repo URL + file path + function name + question
2. Query is embedded with CodeBERT → ChromaDB retrieves similar patterns
3. Decision Router starts traversal from entry function:
   - Check Redis cache (AST hash validated)
   - Extract function via AST
   - LLM analyzes and returns: bug_found / clean / needs_deeper
   - If needs_deeper → resolve dependencies via AST → add to queue
   - Repeat until bug found or all nodes clean
4. Dependency graph built throughout — exported as Mermaid diagram
5. Final report generated with bug location, fix, and graph context

## Key Design Decisions

| Decision | Reason |
|---|---|
| AST over vector search for deps | Deterministic, no false negatives |
| Redis at function level | Invalidate only changed nodes, not whole index |
| AST hash for cache validation | Ignores whitespace/comment changes |
| LLM as navigator | Guides traversal instead of loading everything upfront |
| Lazy traversal | Only goes deep when needed — token efficient |

## File Structure

codeassist/
├── app.py                    # Streamlit UI + orchestration entry
├── core/
│   ├── ast_engine.py         # AST extraction, dependency resolution
│   ├── embedder.py           # CodeBERT embedding + ChromaDB retrieval
│   ├── llm_analyzer.py       # Gemini LLM — analyze + navigate
│   └── router.py             # Decision router — traversal loop
├── cache/
│   └── redis_cache.py        # Redis cache with AST hash validation
├── graph/
│   └── dependency_graph.py   # Graph nodes, edges, Mermaid export
├── .env.example
└── requirements.txt
