from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Any


class EdgeType(str, Enum):
    FALLTHROUGH   = "fallthrough"
    BRANCH_TRUE   = "branch_true"
    BRANCH_FALSE  = "branch_false"
    LOOP_BACK     = "loop_back"
    BREAK         = "break"
    CONTINUE      = "continue"
    RETURN        = "return"
    EXCEPTION     = "exception"
    SWITCH_CASE   = "switch_case"
    GOTO          = "goto"


@dataclass
class CFGStatement:
    """A statement inside a basic block."""
    text: str
    line: int                           # 1-indexed source line
    col: Optional[int] = None
    ast_node: Any = None


@dataclass
class BasicBlock:
    """
    A maximal sequence of straight-line instructions with:
    - Exactly one entry point (control enters only at the first instruction)
    - Exactly one exit point (control leaves only at the last instruction)
    """
    id: int
    name: str                           # e.g., "B0", "B1", "Entry", "Exit"
    statements: List[CFGStatement] = field(default_factory=list)
    is_entry: bool = False
    is_exit: bool = False
    is_unreachable: bool = False

    def add_statement(self, text: str, line: int, col: Optional[int] = None, ast_node: Any = None):
        self.statements.append(CFGStatement(text=text.strip(), line=line, col=col, ast_node=ast_node))

    @property
    def span(self) -> tuple[int, int]:
        """(min_line, max_line) for this block."""
        if not self.statements:
            return (0, 0)
        lines = [s.line for s in self.statements if s.line > 0]
        return (min(lines), max(lines)) if lines else (0, 0)


@dataclass
class CFGEdge:
    """A directed edge in the control flow graph connecting two basic blocks."""
    source_id: int
    target_id: int
    edge_type: EdgeType = EdgeType.FALLTHROUGH
    condition_desc: Optional[str] = None


class CFG:
    """
    Control Flow Graph representation for a single function.
    Provides graph queries (predecessors, successors, reachability)
    and visual rendering (Mermaid).
    """

    def __init__(self, function_name: str):
        self.function_name = function_name
        self.blocks: Dict[int, BasicBlock] = {}
        self.edges: List[CFGEdge] = []
        self.entry_block_id: int = 0
        self.exit_block_id: Optional[int] = None
        self._succ_cache: Optional[Dict[int, List[int]]] = None
        self._pred_cache: Optional[Dict[int, List[int]]] = None

    def add_block(self, block: BasicBlock) -> BasicBlock:
        self.blocks[block.id] = block
        self._invalidate_cache()
        return block

    def add_edge(
        self,
        source_id: int,
        target_id: int,
        edge_type: EdgeType = EdgeType.FALLTHROUGH,
        condition_desc: Optional[str] = None,
    ):
        if source_id not in self.blocks or target_id not in self.blocks:
            return
        # Avoid duplicate edges with the same source, target, and type
        for e in self.edges:
            if e.source_id == source_id and e.target_id == target_id and e.edge_type == edge_type:
                return
        self.edges.append(
            CFGEdge(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                condition_desc=condition_desc,
            )
        )
        self._invalidate_cache()

    def _invalidate_cache(self):
        self._succ_cache = None
        self._pred_cache = None

    def _build_caches(self):
        self._succ_cache = {bid: [] for bid in self.blocks}
        self._pred_cache = {bid: [] for bid in self.blocks}
        for e in self.edges:
            if e.source_id in self._succ_cache and e.target_id in self._succ_cache:
                self._succ_cache[e.source_id].append(e.target_id)
                self._pred_cache[e.target_id].append(e.source_id)

    def successors(self, block_id: int) -> List[int]:
        if self._succ_cache is None:
            self._build_caches()
        return self._succ_cache.get(block_id, [])

    def predecessors(self, block_id: int) -> List[int]:
        if self._pred_cache is None:
            self._build_caches()
        return self._pred_cache.get(block_id, [])

    def compute_reachability(self) -> Set[int]:
        """
        DFS traversal from entry_block_id to identify reachable blocks.
        Marks all other blocks as is_unreachable = True.
        Returns the set of reachable block IDs.
        """
        reachable: Set[int] = set()
        stack = [self.entry_block_id] if self.entry_block_id in self.blocks else []

        while stack:
            curr = stack.pop()
            if curr not in reachable:
                reachable.add(curr)
                for nxt in self.successors(curr):
                    if nxt not in reachable:
                        stack.append(nxt)

        for bid, block in self.blocks.items():
            block.is_unreachable = (bid not in reachable)

        return reachable

    def get_unreachable_blocks(self) -> List[BasicBlock]:
        """Return list of blocks with no path from Entry."""
        self.compute_reachability()
        return [b for b in self.blocks.values() if b.is_unreachable]

    def to_mermaid(self) -> str:
        """Render the CFG as a Mermaid flowchart diagram."""
        self.compute_reachability()
        lines = ["graph TD", f"    %% Function: {self.function_name}"]

        # 1. Render Block Nodes
        for bid, block in sorted(self.blocks.items()):
            # Format block content
            stmt_texts = []
            for s in block.statements[:5]:  # limit lines for readability
                clean_s = s.text.replace('"', "'").replace("\n", " ")
                stmt_texts.append(f"L{s.line}: {clean_s}")
            if len(block.statements) > 5:
                stmt_texts.append(f"... (+{len(block.statements)-5} more)")

            content = "\\n".join(stmt_texts) if stmt_texts else "(empty)"
            label = f"[{block.name}]\\n{content}"

            node_def = f'    B{bid}["{label}"]'
            if block.is_unreachable:
                node_def += ":::unreachable"
            elif block.is_entry:
                node_def += ":::entry"
            elif block.is_exit:
                node_def += ":::exit"
            lines.append(node_def)

        # 2. Render Edges
        for e in self.edges:
            label = ""
            if e.edge_type == EdgeType.BRANCH_TRUE:
                label = "|True|"
            elif e.edge_type == EdgeType.BRANCH_FALSE:
                label = "|False|"
            elif e.edge_type == EdgeType.LOOP_BACK:
                label = "|LoopBack|"
            elif e.edge_type == EdgeType.BREAK:
                label = "|Break|"
            elif e.edge_type == EdgeType.CONTINUE:
                label = "|Continue|"
            elif e.edge_type == EdgeType.RETURN:
                label = "|Return|"
            elif e.edge_type == EdgeType.EXCEPTION:
                label = "|Except|"
            elif e.condition_desc:
                label = f"|{e.condition_desc}|"

            lines.append(f"    B{e.source_id} -->{label} B{e.target_id}")

        # 3. Styling
        lines.append("    classDef entry fill:#2d6a4f,stroke:#52b788,color:#fff,stroke-width:2px")
        lines.append("    classDef exit fill:#1b4332,stroke:#40916c,color:#fff,stroke-width:2px")
        lines.append("    classDef unreachable fill:#b00020,stroke:#e63946,color:#fff,stroke-dasharray: 5 5")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize CFG for JSON responses / WebSocket streaming."""
        self.compute_reachability()
        return {
            "function_name": self.function_name,
            "entry_block_id": self.entry_block_id,
            "exit_block_id": self.exit_block_id,
            "unreachable_count": len(self.get_unreachable_blocks()),
            "blocks": [
                {
                    "id": b.id,
                    "name": b.name,
                    "is_entry": b.is_entry,
                    "is_exit": b.is_exit,
                    "is_unreachable": b.is_unreachable,
                    "span": b.span,
                    "statements": [
                        {"text": s.text, "line": s.line, "col": s.col}
                        for s in b.statements
                    ],
                }
                for b in self.blocks.values()
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "type": e.edge_type.value,
                    "label": e.condition_desc,
                }
                for e in self.edges
            ],
            "mermaid": self.to_mermaid(),
        }
