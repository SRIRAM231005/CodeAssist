# 🧠 CodeAssist

> AI-powered repository-aware debugging system that analyzes dependency relationships across files instead of isolated code snippets.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-green)
![React](https://img.shields.io/badge/React-TypeScript-blue)

---

## What is CodeAssist?

CodeAssist is a deep code debugging platform that takes a GitHub repository, a file path, and a natural language query, then recursively traverses dependency relationships across the codebase to identify potential bugs, explain their causes, and generate structured debugging insights.

Unlike traditional linters or autocomplete-based AI assistants that only inspect the code directly provided to them, CodeAssist performs AST-based dependency traversal across interconnected functions and files, combining semantic retrieval with LLM-guided analysis.

The system generates:
- Structured bug reports
- Dependency-aware debugging insights
- Suggested fixes
- Syntax-highlighted code snippets
- Visual dependency graphs
- Real-time streamed analysis logs

---

## Demo

> Enter a GitHub repository URL, a Python file path, and describe the issue in plain English. CodeAssist handles traversal, retrieval, analysis, and report generation automatically.

---

# How It Works

## Request Lifecycle

```text
User Query + Repo URL + File Path
        ↓
POST /analyze → job_id
        ↓
WS /ws/{job_id}
        ↓
fetch file → AST → function extraction
        ↓
Gemini query parser → function match / chunker fallback
        ↓
CodeBERT embeddings + Qdrant retrieval → semantic context
        ↓
DecisionRouter → recursive AST traversal
    → Redis cache lookup
    → Gemini node analysis
    → dependency expansion
        ↓
Gemini report generator
        ↓
WebSocket stream → Frontend
```

---

## Entry Point Discovery

CodeAssist uses a two-stage fallback strategy to determine the traversal entry point.

### 1. LLM-Based Entry

The user query is parsed using Gemini to extract potential function names or bug targets. If a matching function exists, traversal begins from that function.

### 2. Embedding-Based Fallback

If no explicit function name is identified:

* Function chunks are embedded using CodeBERT
* Cosine similarity is computed against the user query
* The highest similarity function becomes the traversal anchor

Global line chunks such as:

* constants
* imports
* standalone expressions
* module-level assignments

are also embedded independently.

The most relevant line chunks are appended to the LLM context to capture data-level issues that pure AST traversal may miss.

---

## AST Traversal Engine

Starting from the entry function, CodeAssist:

* Extracts function source using Python AST parsing
* Builds dependency relationships from function calls
* Computes AST hashes for deduplication
* Checks Redis cache before re-analysis
* Sends each node to Gemini for reasoning
* Expands traversal recursively through dependencies
* Stops at configurable traversal depth limits

This enables repository-aware debugging instead of isolated snippet analysis.

---

## Semantic Retrieval

CodeAssist uses:

* CodeBERT embeddings
* Qdrant vector search
* Function-level semantic retrieval
* Context-aware chunk expansion

to retrieve semantically relevant debugging context before LLM analysis.

---

## Report Generation

Once traversal completes, CodeAssist generates a structured markdown debugging report containing:

* Bug explanation
* Root cause analysis
* Severity
* Suggested fixes
* Corrected code snippets
* Dependency traversal summary

Reports are streamed live through WebSockets and rendered with syntax highlighting in the frontend.

---

# Architecture

## System Overview

```text
Vercel                        Render 
(React Frontend)  →       (FastAPI Backend)
                                ↓
                         Hugging Face Spaces
                    (CodeBERT Embedder Service)
                                ↓
                 Qdrant Cloud | Upstash Redis | Gemini API
                  (Vector DB)      (Cache)         (LLM)
```

---

## Backend Structure

```text
backend/
  app/
    main.py              # FastAPI app + HTTP/WebSocket endpoints

  New_Model/
    ast_engine.py        # AST parsing + dependency extraction
    embedder.py          # CodeBERT embedder + Qdrant retriever
    llm_analyzer.py      # Gemini orchestration layer
    router.py            # Recursive traversal engine
    redis_cache.py       # Redis caching layer
    chunker.py           # Semantic chunk grouping pipeline

  Dockerfile
  requirements.txt
```

---

## Frontend Structure

```text
frontend/
  src/
    App.tsx
    ChatLayout.tsx
    Sidebar.tsx
    App.css
```

---

## API Endpoints

| Method | Endpoint       | Description                 |
| ------ | -------------- | --------------------------- |
| `POST` | `/analyze`     | Submit analysis request     |
| `WS`   | `/ws/{job_id}` | Real-time streamed analysis |
| `GET`  | `/health`      | Backend service health      |
| `GET`  | `/cache/stats` | Redis cache statistics      |
| `POST` | `/cache/clear` | Flush Redis cache           |

---

## WebSocket Message Types

```json
{ "type": "spinner", "message": "Fetching repository..." }

{ "type": "log", "message": "Traversing compute_average → compute_sum" }

{
  "type": "result",
  "data": {
    "status": "bug_found",
    "report": "...",
    "graph": {}
  }
}

{ "type": "error", "detail": "Repository fetch failed" }
```

---

# Design Decisions

## Why AST Traversal Instead of Embeddings Alone?

Embedding-only retrieval often misses transitive dependency bugs spread across helper functions, utility layers, and nested call chains.

AST traversal enables repository-aware debugging by recursively following actual dependency relationships across files.

---

## Why Externalize CodeBERT Into a Separate Microservice?

Running CodeBERT directly inside the backend exceeded memory limits on low-cost deployment tiers.

The embedding pipeline was isolated into a dedicated Hugging Face Spaces microservice to:

* reduce backend memory usage
* simplify deployment
* preserve semantic retrieval quality

Endpoint:
https://sriramdev-codebert-api-space.hf.space/embed

---

## Why Qdrant Instead of ChromaDB in Production?

ChromaDB was initially used during local development.

Qdrant Cloud was later adopted because it:

* simplified deployment
* avoided shipping persistent vector stores inside Docker images
* provided managed cloud vector retrieval

---

# Tech Stack

| Layer               | Technology              |
| ------------------- | ----------------------- |
| Frontend            | React, TypeScript, Vite |
| Backend             | FastAPI, Python 3.12    |
| Real-Time Streaming | WebSockets              |
| LLM                 | Gemini 2.5 Flash        |
| Embeddings          | CodeBERT                |
| Vector Database     | Qdrant Cloud            |
| Cache               | Upstash Redis           |
| Deployment          | Docker, Render, Vercel  |
| Embedding Service   | Hugging Face Spaces     |

---

# Getting Started

## Prerequisites

* Python 3.12+
* Node.js 18+
* Docker
* Gemini API key
* Qdrant Cloud account
* Upstash Redis account

---

## Backend Setup

```bash
cd backend

python -m venv venv

# Windows
venv\\Scripts\\activate

# Linux / Mac
source venv/bin/activate

pip install -r requirements.txt
```

Create `.env` inside `backend/`:

```env
GEMINI_API_KEY=your_key

QDRANT_URL=your_qdrant_url
QDRANT_API_KEY=your_qdrant_api_key

REDIS_HOST=your_host
REDIS_PORT=6379
REDIS_PASSWORD=your_password

EMBEDDER_URL=deployed_microservice_url

MAX_DEPTH=5
TOP_K=3
```

Run backend:

```bash
uvicorn app.main:app --reload
```

---

## Frontend Setup

```bash
cd frontend

npm install
npm run dev
```

Create `.env` inside `frontend/`:

```env
VITE_API_BASE=http://localhost:8000
```

---

## Docker

```bash
cd backend

docker build -t codeassist_backend .

docker run -p 8000:8000 \
  -e GEMINI_API_KEY=your_key \
  -e QDRANT_URL=your_url \
  -e QDRANT_API_KEY=your_key \
  -e REDIS_HOST=your_host \
  -e REDIS_PORT=6379 \
  -e REDIS_PASSWORD=your_password \
  codeassist_backend
```

---

# Roadmap

* [ ] Multi-language support (JavaScript, Java, Go)
* [ ] Hybrid traversal with branch-local stopping
* [ ] Multi-bug detection across dependency branches
* [ ] Runtime context injection (expected vs actual outputs)
* [ ] Improved Top-K semantic entry-point retrieval
* [ ] BugsInPy-based bug pattern grounding

---

# Author

Sriram B

```
```
