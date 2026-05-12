import { useState, useRef, useEffect, useCallback } from "react";
import { ArrowUp, Bug, CheckCircle, AlertCircle, ChevronDown, ChevronUp, Copy, Check } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import ReactFlow, { Background, Controls } from "reactflow";
import type { Node, Edge } from "reactflow";
import "reactflow/dist/style.css";

// ── Types ─────────────────────────────────────────────────────────────────────
type MsgType = "spinner" | "log" | "result" | "error";

interface WsMessage {
  type: MsgType;
  message?: string;
  detail?: string;
  data?: AnalysisResult;
}

interface GraphNode {
  function: string;
  file: string;
  status: string;
  depth: number;
  from_cache: boolean;
  depends_on: string[];
}

interface AnalysisResult {
  status: "bug_found" | "clean";
  report: string;
  graph: { nodes: GraphNode[]; total: number };
  mermaid: string;
  found_in?: { function: string; file: string };
  bug?: { severity: string };
}

interface Props {
  branch: string;
  maxDepth: number;
  onHealthUpdate: (status: Record<string, unknown>) => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const WS_BASE  = API_BASE.replace(/^http/, "ws");

function buildFlowGraph(nodes: GraphNode[]): { nodes: Node[]; edges: Edge[] } {
  const fnToId: Record<string, string> = {};
  nodes.forEach((n, i) => { fnToId[n.function] = `node-${i}`; });

  const flowNodes: Node[] = nodes.map((n, i) => ({
    id: `node-${i}`,
    position: { x: (i % 3) * 260 + 60, y: Math.floor(i / 3) * 160 + 60 },
    data: { label: n.function },
    style: {
      background:
        n.status === "bug_found"
          ? "#3d1a1a"
          : n.from_cache
          ? "#1a2a3d"
          : "#1a2d1a",
      border: `1.5px solid ${
        n.status === "bug_found" ? "#f87171" : n.from_cache ? "#60a5fa" : "#4ade80"
      }`,
      borderRadius: "8px",
      color: "#e2e8f0",
      fontSize: "12px",
      fontFamily: "'JetBrains Mono', monospace",
      padding: "10px 14px",
      width: 180,
      textAlign: "center" as const,
    },
  }));

  const flowEdges: Edge[] = [];
  nodes.forEach((n, i) => {
    n.depends_on.forEach((dep) => {
      const targetId = fnToId[dep];
      if (targetId) {
        flowEdges.push({
          id: `edge-${i}-${targetId}`,
          source: `node-${i}`,
          target: targetId,
          style: { stroke: "#475569", strokeWidth: 1.5 },
          animated: n.status === "bug_found",
        });
      }
    });
  });

  return { nodes: flowNodes, edges: flowEdges };
}

// ── Code block with copy button ───────────────────────────────────────────────
function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-block-wrap">
      <div className="code-block-header">
        <span className="code-lang">{language || "code"}</span>
        <button className="copy-btn" onClick={copy}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || "python"}
        style={oneDark}
        customStyle={{
          margin: 0,
          borderRadius: "0 0 8px 8px",
          fontSize: "13px",
          lineHeight: "1.6",
          background: "#0d1117",
        }}
        showLineNumbers
      >
        {children}
      </SyntaxHighlighter>
    </div>
  );
}

// ── Spinner indicator (Claude-style) ──────────────────────────────────────────
const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

function SpinnerLine({ message }: { message: string }) {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setFrame((f) => (f + 1) % SPINNER_FRAMES.length), 80);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="spinner-line">
      <span className="spinner-frame">{SPINNER_FRAMES[frame]}</span>
      <span className="spinner-msg">{message}</span>
    </div>
  );
}

// ── Result tabs ───────────────────────────────────────────────────────────────
type Tab = "report" | "graph" | "logs";

function ResultPanel({
  result,
  logs,
}: {
  result: AnalysisResult;
  logs: string[];
}) {
  const [tab, setTab] = useState<Tab>("report");
  const { nodes: flowNodes, edges: flowEdges } = buildFlowGraph(result.graph.nodes);

  const bugFound = result.status === "bug_found";

  return (
    <div className="result-panel">
      {/* Status banner */}
      <div className={`status-banner ${bugFound ? "banner-bug" : "banner-clean"}`}>
        <div className="banner-left">
          {bugFound ? <Bug size={20} /> : <CheckCircle size={20} />}
          <div>
            <p className="banner-title">
              {bugFound ? "Bug Detected" : "No Bugs Found"}
            </p>
            {bugFound && result.found_in && (
              <p className="banner-sub">
                in <code>{result.found_in.function}()</code> ·{" "}
                {result.found_in.file.split("/").pop()}
              </p>
            )}
          </div>
        </div>
        {bugFound && result.bug?.severity && (
          <span className={`severity-badge severity-${result.bug.severity.toLowerCase()}`}>
            {result.bug.severity.toUpperCase()}
          </span>
        )}
      </div>

      {/* Stats row */}
      <div className="stats-row">
        <div className="stat-chip">
          <span className="stat-val">{result.graph.total}</span>
          <span className="stat-label">Functions</span>
        </div>
        <div className="stat-chip">
          <span className="stat-val">
            {result.graph.nodes.filter((n) => n.status === "bug_found").length}
          </span>
          <span className="stat-label">Bugs</span>
        </div>
        <div className="stat-chip">
          <span className="stat-val">
            {result.graph.nodes.filter((n) => n.from_cache).length}
          </span>
          <span className="stat-label">Cache Hits</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="tab-bar">
        {(["report", "graph", "logs"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`tab-btn ${tab === t ? "tab-active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "report" ? "📋 Report" : t === "graph" ? "🕸 Graph" : "🖥 Traversal Log"}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="tab-content">
        {tab === "report" && (
          <div className="markdown-body">
            <ReactMarkdown
              components={{
                // pre handles all triple-backtick blocks
                // code only ever sees inline backticks after this
                pre({ children }) {
                  const child = children as any;
                  const className = child?.props?.className || "";
                  const match = /language-(\w+)/.exec(className);
                  const codeText = String(child?.props?.children ?? "").replace(/\n$/, "");
                  return (
                    <CodeBlock language={match?.[1] ?? "python"}>
                      {codeText}
                    </CodeBlock>
                  );
                },
                code({ className, children, ...props }: any) {
                  // only inline code reaches here — pre handles blocks above
                  return (
                    <code className="inline-code" {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {result.report}
            </ReactMarkdown>
          </div>
        )}

        {tab === "graph" && (
          <div className="graph-wrap">
            <div className="graph-legend">
              <span className="legend-item">🔴 Bug</span>
              <span className="legend-item">🔵 Cached</span>
              <span className="legend-item">🟢 Clean</span>
            </div>

            {/* ── Fixed graph container ── */}
            <div style={{
              height: 420,
              width: "100%",
              background: "#0d1117",
              borderRadius: "8px",
              border: "1px solid #2a2a2e",
              overflow: "hidden",
            }}>
              <ReactFlow
                nodes={flowNodes}
                edges={flowEdges}
                fitView
                fitViewOptions={{ padding: 0.3 }}
                attributionPosition="bottom-right"
                nodesDraggable={true}
                nodesConnectable={false}
                elementsSelectable={true}
              >
                <Background color="#1e2a3a" gap={20} />
                <Controls />
              </ReactFlow>
            </div>

            {/* Node table */}
            <div className="node-table-wrap">
              <table className="node-table">
                <thead>
                  <tr>
                    <th>Function</th>
                    <th>File</th>
                    <th>Status</th>
                    <th>Depth</th>
                    <th>Cache</th>
                    <th>Depends On</th>
                  </tr>
                </thead>
                <tbody>
                  {result.graph.nodes.map((n, i) => (
                    <tr key={i} className={n.status === "bug_found" ? "row-bug" : ""}>
                      <td><code>{n.function}</code></td>
                      <td>{n.file.split("/").pop()}</td>
                      <td>
                        <span className={`status-pill ${n.status === "bug_found" ? "pill-bug" : "pill-clean"}`}>
                          {n.status === "bug_found" ? "🐛 Bug" : "✅ Clean"}
                        </span>
                      </td>
                      <td>{n.depth}</td>
                      <td>{n.from_cache ? "✅" : "❌"}</td>
                      <td>{n.depends_on.join(", ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "logs" && (
          <div className="log-terminal">
            {logs.map((line, i) => (
              <div key={i} className="log-line">
                <span className="log-prompt">›</span>
                <span>{line}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main ChatLayout ───────────────────────────────────────────────────────────
export default function ChatLayout({ branch, maxDepth, onHealthUpdate }: Props) {
  const [repoUrl,   setRepoUrl]   = useState("");
  const [filePath,  setFilePath]  = useState("");
  const [query,     setQuery]     = useState("");

  const [phase,     setPhase]     = useState<"idle" | "running" | "done" | "error">("idle");
  const [spinner,   setSpinner]   = useState("");
  const [logs,      setLogs]      = useState<string[]>([]);
  const [result,    setResult]    = useState<AnalysisResult | null>(null);
  const [errorMsg,  setErrorMsg]  = useState("");
  const [logsOpen,  setLogsOpen]  = useState(true);

  const wsRef = useRef<WebSocket | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/health`);
      const data = await res.json();
      onHealthUpdate(data);
    } catch {
      onHealthUpdate({ redis: { status: "unreachable" } });
    }
  }, [onHealthUpdate]);

  useEffect(() => { fetchHealth(); }, [fetchHealth]);

  const runAnalysis = async () => {
    if (!repoUrl.trim() || !filePath.trim() || !query.trim()) return;

    setPhase("running");
    setSpinner("Submitting job...");
    setLogs([]);
    setResult(null);
    setErrorMsg("");

    try {
      const postRes = await fetch(`${API_BASE}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url:   repoUrl,
          file_path:  filePath,
          branch,
          user_query: query,
          max_depth:  maxDepth,
        }),
      });

      if (!postRes.ok) {
        const err = await postRes.json();
        const detail = err?.detail?.[0]?.msg ?? err?.detail ?? "Validation failed.";
        setErrorMsg(detail);
        setPhase("error");
        return;
      }

      const { job_id } = await postRes.json();
      setQuery("");

      const ws = new WebSocket(`${WS_BASE}/ws/${job_id}`);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const msg: WsMessage = JSON.parse(event.data);

        if (msg.type === "spinner" && msg.message) {
          setSpinner(msg.message);
        } else if (msg.type === "log" && msg.message) {
          setLogs((prev) => [...prev, msg.message!]);
        } else if (msg.type === "result" && msg.data) {
          setResult(msg.data);
          setPhase("done");
          setSpinner("");
          fetchHealth();
        } else if (msg.type === "error") {
          setErrorMsg(msg.detail ?? "An unknown error occurred.");
          setPhase("error");
          setSpinner("");
        }
      };

      ws.onerror = () => {
        setErrorMsg("WebSocket connection failed.");
        setPhase("error");
        setSpinner("");
      };

      ws.onclose = () => {
        wsRef.current = null;
      };

    } catch (e: any) {
      setErrorMsg(e.message ?? "Network error.");
      setPhase("error");
      setSpinner("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      runAnalysis();
    }
  };

  const isRunning = phase === "running";

  return (
    <div className="chat-layout">
      <div className="chat-scroll">
      {/* Top inputs */}
      <div className="top-inputs">
        <div className="input-field">
          <label className="input-label">🔗 GitHub Repository URL</label>
          <input
            className="text-input"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="https://github.com/user/repo"
            disabled={isRunning}
          />
        </div>
        <div className="input-field">
          <label className="input-label">📄 File Path</label>
          <input
            className="text-input"
            value={filePath}
            onChange={(e) => setFilePath(e.target.value)}
            placeholder="src/billing/orders.py"
            disabled={isRunning}
          />
        </div>
      </div>

      {/* Main output area */}
      <div className="chat-output">

        {phase === "idle" && (
          <div className="idle-hint">
            <img src="/brain1.png" alt="CodeAssist" width={125} height={100} />
            <p>Enter a repo, file path, and your question below to begin analysis.</p>
          </div>
        )}

        {phase === "running" && (
          <div className="running-wrap">
            {spinner && <SpinnerLine message={spinner} />}
            {logs.length > 0 && (
              <div className="live-log-box">
                <div className="live-log-header" onClick={() => setLogsOpen((o) => !o)}>
                  <span>🖥 Traversal Log ({logs.length})</span>
                  {logsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </div>
                {logsOpen && (
                  <div className="live-log-body">
                    {logs.map((l, i) => (
                      <div key={i} className="log-line">
                        <span className="log-prompt">›</span>
                        <span>{l}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {phase === "error" && (
          <div className="error-box">
            <AlertCircle size={18} />
            <div>
              <p className="error-title">Error</p>
              <p className="error-detail">{errorMsg}</p>
            </div>
          </div>
        )}

        {phase === "done" && result && (
          <ResultPanel result={result} logs={logs} />
        )}
      </div>
      </div>{/* end chat-scroll */}

      {/* Query bar */}
      <div className="query-bar">
        <div className="query-input-wrap">
          <textarea
            className="query-textarea"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your code…"
            rows={1}
            disabled={isRunning}
          />
          <button
            className="send-btn"
            onClick={runAnalysis}
            disabled={isRunning || !query.trim() || !repoUrl.trim() || !filePath.trim()}
            aria-label="Send"
          >
            <ArrowUp size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}