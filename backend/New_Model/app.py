import os
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

from ast_engine import fetch_github_file, extract_all_functions
from embedder import CodeEmbedder, ChromaRetriever
from llm_analyzer import LLMAnalyzer
from router import DecisionRouter
from redis_cache import RedisCache

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
CHROMA_PATH      = os.getenv("CHROMA_PATH", "../../chroma_store")
COLLECTION_NAME  = os.getenv("COLLECTION_NAME", "codebert_python")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
REDIS_HOST       = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT       = int(os.getenv("REDIS_PORT", 6379))
MAX_DEPTH        = int(os.getenv("MAX_DEPTH", 5))
TOP_K            = int(os.getenv("TOP_K", 5))

genai.configure(api_key=GEMINI_API_KEY)

# ── Cached resource loading ───────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading CodeBERT...")
def load_embedder():
    return CodeEmbedder()

@st.cache_resource(show_spinner="Connecting to ChromaDB...")
def load_retriever():
    return ChromaRetriever(CHROMA_PATH, COLLECTION_NAME, top_k=TOP_K)

@st.cache_resource(show_spinner="Connecting to Redis...")
def load_cache():
    return RedisCache(host=REDIS_HOST, port=REDIS_PORT)

@st.cache_resource(show_spinner="Loading LLM...")
def load_llm():
    return LLMAnalyzer("gemini-2.5-flash")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CodeAssist",
    page_icon="🧠",
    layout="wide"
)

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🧠 CodeAssist")
st.caption("Deep code analysis with AST traversal, dependency graph, and LLM-guided debugging")

# ── Sidebar: status + settings ────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    branch = st.text_input("Branch", value="main")
    max_depth = st.slider("Max Traversal Depth", 1, 10, MAX_DEPTH)

    st.divider()
    st.header("📊 System Status")

    cache = load_cache()
    stats = cache.get_stats()
    if stats["status"] == "connected":
        st.success(f"Redis ✅ — {stats['cached_nodes']} nodes cached")
    else:
        st.warning(f"Redis ⚠️ — {stats['status']}")

    if st.button("🗑️ Clear Cache"):
        if cache.available:
            keys = cache.client.keys("codeassist:*")
            for k in keys:
                cache.client.delete(k)
            st.success(f"Cleared {len(keys)} cached nodes")
        else:
            st.error("Redis not available")

# ── Main Input ────────────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 1])

with col1:
    repo_url      = st.text_input("🔗 GitHub Repository URL", placeholder="https://github.com/user/repo")
    file_path     = st.text_input("📄 File Path", placeholder="src/billing/orders.py")

with col2:
    user_query    = st.text_area("💬 Your Question", placeholder="Why is calculate_tax() returning wrong values?", height=100)

# ── Function picker (after URL + file given) ──────────────────────────────────
function_name = None
fns = []
intent = None

def filter_function(name_candidates, fns):
    name_candidates = [n.lower() for n in name_candidates]
    filtered = next((fn for fn in fns if fn["name"].lower() in name_candidates), None)
    return filtered["name"] if filtered else function_name["name"]

# ── Analyze Button ────────────────────────────────────────────────────────────
analyze_clicked = st.button("🚀 Analyze", type="primary", use_container_width=True)

if analyze_clicked:
    if not all([repo_url, file_path, user_query]):
        st.error("Please fill in all fields: repo URL, file path, function name, and your question.")
        st.stop()
    
    with st.spinner("Fetching file..."):
        try:
            source = fetch_github_file(repo_url, file_path, branch)
            fns = extract_all_functions(source)
        except Exception as e:
            st.error(f"Error: {e}")

    # Load resources
    embedder  = load_embedder()
    retriever = load_retriever()
    llm       = load_llm()
    cache     = load_cache()

    # ── Query parsing ─────────────────────────────────────────────────────────
    with st.spinner("Understanding your query..."):
        parsed = llm.parse_query(user_query)
        name_candidates = parsed.get("function_name_candidates", [])
        function_name = filter_function(name_candidates, fns)
        st.write(f"**Analyzed function:** `{function_name}`")
        intent = parsed.get("intent")

    # ── ChromaDB similarity search ────────────────────────────────────────────
    with st.spinner("Searching knowledge base..."):
        query_embedding  = embedder.embed(user_query)
        retrieved_context = retriever.retrieve(query_embedding)   

    # ── Traversal ─────────────────────────────────────────────────────────────
    st.subheader("🔄 Traversal Log")
    log_container = st.empty()
    log_lines = []

    def progress_callback(message: str):
        log_lines.append(message)
        log_container.markdown("\n\n".join(log_lines))

    router = DecisionRouter(
        repo_url=repo_url,
        branch=branch,
        llm=llm,
        embedder=embedder,
        retriever=retriever,
        cache=cache,
        max_depth=max_depth
    )

    with st.spinner("Running deep analysis..."):
        result = router.run(
            entry_file=file_path,
            entry_function=function_name,
            raw_query=user_query,
            intent=intent,
            retrieved_context=retrieved_context,
            progress_callback=progress_callback
        )

    st.divider()

    # ── Results ───────────────────────────────────────────────────────────────
    if result["status"] == "bug_found":
        st.error("🐛 Bug Found!")
        bug = result.get("bug", {})

        col1, col2, col3 = st.columns(3)
        col1.metric("Function", result["found_in"]["function"])
        col2.metric("File", result["found_in"]["file"].split("/")[-1])
        col3.metric("Severity", bug.get("severity", "unknown").upper())

    else:
        st.success("✅ No bugs found across all analyzed functions")

    st.subheader("📋 Analysis Report")
    st.markdown(result["report"])

    # ── Dependency Graph ──────────────────────────────────────────────────────
    st.subheader("🕸️ Dependency Graph")

    graph_data = result["graph"]
    nodes = graph_data["nodes"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Functions Analyzed", graph_data["total"])
    col2.metric("Bugs Found", sum(1 for n in nodes if n["status"] == "bug_found"))
    col3.metric("Cache Hits", sum(1 for n in nodes if n["from_cache"]))

    # Mermaid diagram
    with st.expander("📊 Visual Dependency Graph (Mermaid)", expanded=True):
        st.markdown(f"```mermaid\n{result['mermaid']}\n```")

    # Table view
    with st.expander("📋 Graph Node Details", expanded=False):
        import pandas as pd
        rows = []
        for n in nodes:
            rows.append({
                "Function": n["function"],
                "File": n["file"].split("/")[-1],
                "Status": n["status"],
                "Depth": n["depth"],
                "Cache Hit": "✅" if n["from_cache"] else "❌",
                "Depends On": ", ".join(n["depends_on"])
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
