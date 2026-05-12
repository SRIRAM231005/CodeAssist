import { useState } from "react";
import { Trash2, RefreshCw } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

interface HealthStatus {
  redis?: { status: string; cached_nodes?: number };
  embedder?: string;
  retriever?: string;
  llm?: string;
}

interface Props {
  branch: string;
  maxDepth: number;
  onBranchChange: (v: string) => void;
  onMaxDepthChange: (v: number) => void;
  health: HealthStatus;
  onHealthUpdate: (status: HealthStatus) => void;
}

export default function Sidebar({
  branch,
  maxDepth,
  onBranchChange,
  onMaxDepthChange,
  health,
  onHealthUpdate,
}: Props) {
  const [clearing, setClearing] = useState(false);
  const [clearMsg, setClearMsg] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const redisConnected = health.redis?.status === "connected";
  const cachedNodes    = health.redis?.cached_nodes ?? 0;

  const handleClearCache = async () => {
    setClearing(true);
    setClearMsg("");
    try {
      const res = await fetch(`${API_BASE}/cache/clear`, { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        setClearMsg(`Cleared ${data.deleted_keys} nodes`);
        // Refresh health after clearing
        const healthRes = await fetch(`${API_BASE}/health`);
        const healthData = await healthRes.json();
        onHealthUpdate(healthData);
      } else {
        setClearMsg(data.detail ?? "Failed to clear cache.");
      }
    } catch {
      setClearMsg("Network error.");
    } finally {
      setClearing(false);
      setTimeout(() => setClearMsg(""), 3000);
    }
  };

  const handleRefreshHealth = async () => {
    setRefreshing(true);
    try {
      const res = await fetch(`${API_BASE}/health`);
      const data = await res.json();
      onHealthUpdate(data);
    } catch {
      onHealthUpdate({ redis: { status: "unreachable" } });
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-logo">
        <img src="/brain1.png" alt="CodeAssist" width={30} height={26} />
        <span className="logo-text">CodeAssist</span>
      </div>

      <div className="sidebar-divider" />

      {/* Settings */}
      <section className="sidebar-section">
        <p className="sidebar-label">⚙ Settings</p>

        <div className="field-group">
          <label className="field-label">Branch</label>
          <input
            className="sidebar-input"
            value={branch}
            onChange={(e) => onBranchChange(e.target.value)}
            placeholder="main"
          />
        </div>

        <div className="field-group">
          <label className="field-label">
            Max Traversal Depth
            <span className="depth-value">{maxDepth}</span>
          </label>
          <input
            type="range"
            min={1}
            max={10}
            value={maxDepth}
            onChange={(e) => onMaxDepthChange(Number(e.target.value))}
            className="sidebar-range"
          />
        </div>
      </section>

      <div className="sidebar-divider" />

      {/* System Status */}
      <section className="sidebar-section">
        <div className="status-header">
          <p className="sidebar-label">🗄 System Status</p>
          <button
            className="refresh-btn"
            onClick={handleRefreshHealth}
            disabled={refreshing}
            title="Refresh status"
          >
            <RefreshCw size={13} className={refreshing ? "spin" : ""} />
          </button>
        </div>

        {/* Redis */}
        <div className={`cache-status ${redisConnected ? "connected" : "disconnected"}`}>
          <span className="cache-dot" />
          <span className="cache-text">
            Redis — {redisConnected
              ? `connected · ${cachedNodes} nodes cached`
              : health.redis?.status ?? "unavailable"}
          </span>
        </div>

        {/* Other services */}
        <div className="services-list">
          {(["embedder", "retriever", "llm"] as const).map((svc) => (
            <div key={svc} className="service-row">
              <span className={`service-dot ${health[svc] === "loaded" ? "dot-ok" : "dot-off"}`} />
              <span className="service-name">
                {svc === "embedder" ? "CodeBERT" : svc === "retriever" ? "QdrantDB" : "Gemini LLM"}
              </span>
              <span className={`service-status ${health[svc] === "loaded" ? "status-ok" : "status-off"}`}>
                {health[svc] ?? "—"}
              </span>
            </div>
          ))}
        </div>

        {/* Clear cache */}
        <button
          className="clear-cache-btn"
          onClick={handleClearCache}
          disabled={clearing || !redisConnected}
        >
          <Trash2 size={13} />
          {clearing ? "Clearing..." : "Clear Cache"}
        </button>

        {clearMsg && (
          <p className={`clear-msg ${clearMsg.startsWith("Cleared") ? "clear-ok" : "clear-err"}`}>
            {clearMsg}
          </p>
        )}
      </section>

      <div className="sidebar-divider" />

      {/* Footer */}
      <div className="sidebar-footer">
        <p>AST · QdrantDB · Gemini</p>
        <p>Deep Code Analysis</p>
      </div>
    </aside>
  );
}