import { useState, useCallback } from "react";
import Sidebar from "./Sidebar";
import ChatLayout from "./ChatLayout";
import "./App.css";

interface HealthStatus {
  redis?: { status: string; cached_nodes?: number };
  embedder?: string;
  retriever?: string;
  llm?: string;
}

export default function App() {
  const [branch,   setBranch]   = useState("main");
  const [maxDepth, setMaxDepth] = useState(5);
  const [health,   setHealth]   = useState<HealthStatus>({});

  const handleHealthUpdate = useCallback((status: HealthStatus) => {
    setHealth(status);
  }, []);

  return (
    <div className="app-root">
      <Sidebar
        branch={branch}
        maxDepth={maxDepth}
        onBranchChange={setBranch}
        onMaxDepthChange={setMaxDepth}
        health={health}
        onHealthUpdate={handleHealthUpdate}
      />
      <main className="app-main">
        <ChatLayout
          branch={branch}
          maxDepth={maxDepth}
          onHealthUpdate={handleHealthUpdate}
        />
      </main>
    </div>
  );
}