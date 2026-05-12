from typing import Optional
from dataclasses import dataclass, field
from enum import Enum


class NodeStatus(str, Enum):
    PENDING   = "pending"
    CLEAN     = "clean"
    BUG_FOUND = "bug_found"
    SKIPPED   = "skipped"   # dynamic import / unresolvable


@dataclass
class GraphNode:
    function_name: str
    file_path: str
    code: str
    ast_hash: str
    status: NodeStatus = NodeStatus.PENDING
    bug_info: Optional[dict] = None
    depth: int = 0
    from_cache: bool = False


class DependencyGraph:
    """
    Directed graph: node = function, edge = A depends on B.
    Built incrementally as traversal happens.
    """

    def __init__(self):
        # node_id -> GraphNode
        self.nodes: dict[str, GraphNode] = {}
        # node_id -> list of node_ids it depends on
        self.edges: dict[str, list[str]] = {}
        # track visited to prevent cycles
        self.visited: set[str] = set()

    def node_id(self, file_path: str, function_name: str) -> str:
        return f"{file_path}::{function_name}"

    def add_node(self, node: GraphNode) -> str:
        nid = self.node_id(node.file_path, node.function_name)
        self.nodes[nid] = node
        if nid not in self.edges:
            self.edges[nid] = []
        self.visited.add(nid)
        return nid

    def add_edge(self, parent_file: str, parent_fn: str, child_file: str, child_fn: str):
        parent_id = self.node_id(parent_file, parent_fn)
        child_id  = self.node_id(child_file, child_fn)
        if parent_id in self.edges and child_id not in self.edges[parent_id]:
            self.edges[parent_id].append(child_id)

    def is_visited(self, file_path: str, function_name: str) -> bool:
        return self.node_id(file_path, function_name) in self.visited

    def update_status(self, file_path: str, function_name: str, status: NodeStatus, bug_info: dict = None):
        nid = self.node_id(file_path, function_name)
        if nid in self.nodes:
            self.nodes[nid].status = status
            if bug_info:
                self.nodes[nid].bug_info = bug_info

    def get_bug_path(self) -> list[GraphNode]:
        """Trace from entry node to the bug node."""
        bug_nodes = [n for n in self.nodes.values() if n.status == NodeStatus.BUG_FOUND]
        return bug_nodes

    def to_summary(self) -> dict:
        """Serialize graph to a clean summary for LLM context."""
        summary = []
        for nid, node in self.nodes.items():
            deps = self.edges.get(nid, [])
            summary.append({
                "function": node.function_name,
                "file": node.file_path,
                "status": node.status.value,
                "depth": node.depth,
                "depends_on": [self.nodes[d].function_name for d in deps if d in self.nodes],
                "from_cache": node.from_cache,
                "bug": node.bug_info
            })
        return {"nodes": summary, "total": len(summary)}

    def to_mermaid(self) -> str:
        """Generate Mermaid flowchart for visual display."""
        lines = ["graph TD"]
        for nid, node in self.nodes.items():
            label = f"{node.function_name}\\n[{node.file_path.split('/')[-1]}]"
            if node.status == NodeStatus.BUG_FOUND:
                lines.append(f'    {nid.replace("::", "_").replace("/", "_").replace(".", "_")}["{label}"]:::bug')
            elif node.status == NodeStatus.CLEAN:
                lines.append(f'    {nid.replace("::", "_").replace("/", "_").replace(".", "_")}["{label}"]:::clean')
            else:
                lines.append(f'    {nid.replace("::", "_").replace("/", "_").replace(".", "_")}["{label}"]')

        for parent_id, children in self.edges.items():
            for child_id in children:
                p = parent_id.replace("::", "_").replace("/", "_").replace(".", "_")
                c = child_id.replace("::", "_").replace("/", "_").replace(".", "_")
                lines.append(f"    {p} --> {c}")

        lines.append("    classDef bug fill:#ff4444,color:#fff")
        lines.append("    classDef clean fill:#44bb44,color:#fff")
        return "\n".join(lines)
