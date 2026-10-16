"""
Stage 4 – Backward slicer.

Given a CFG (from Stage 2) and the def-use chains produced by
ReachingDefinitionsAnalyzer (Stage 3), compute a backward slice from the
failing line.

The slice contains:
  • Data-dep statements: any statement that defines a variable used at the
    failing line, recursively back through the def-use chains.
  • Control-dep statements: the branch-condition statement of every block
    that has a conditional edge leading to a block on the data-dep path,
    or enclosing syntax-tree conditions (if/while/for/switch).

All wrapped in try/except so any failure returns an empty SliceResult and
the caller falls back to the existing flow unchanged.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from New_Model.cfg.model import CFG, BasicBlock


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class SliceResult:
    """Backward slice from the failing line."""
    failing_line: int
    slice_statements: List[Tuple[int, str]] = field(default_factory=list)
    """(line_number, source_text) tuples, sorted by line, for data + control deps."""

    @property
    def is_empty(self) -> bool:
        return not self.slice_statements


# ── Public API ────────────────────────────────────────────────────────────────

def build_def_use_chains(cfg):
    """
    Helper: build def-use chains from a CFG using ReachingDefinitionsAnalyzer.
    Returns the analyzer instance (with .def_use_chains populated) or None on failure.
    """
    try:
        from New_Model.static_analysis.reaching_defs import ReachingDefinitionsAnalyzer
        rda = ReachingDefinitionsAnalyzer(cfg)
        rda.analyze()   # populates rda.def_use_chains
        return rda
    except Exception:
        return None


def compute_backward_slice(
    cfg,
    def_use_chains,
    failing_line: int,
) -> SliceResult:
    """
    Compute backward slice from *failing_line* using *cfg* and *def_use_chains*.
    Returns a SliceResult (may be empty if nothing resolves).
    """
    try:
        return _compute(cfg, def_use_chains, failing_line)
    except Exception:
        return SliceResult(failing_line=failing_line)


# ── Internal implementation ───────────────────────────────────────────────────

def _find_block_for_line(cfg, line: int):
    """Return the BasicBlock containing *line*, or None."""
    try:
        if cfg is None or not hasattr(cfg, "blocks"):
            return None

        # 1. Exact match on statement line
        for block in cfg.blocks.values():
            for s in block.statements:
                if s.line == line:
                    return block

        # 2. Match within block span
        for block in cfg.blocks.values():
            if not block.statements:
                continue
            lines = [s.line for s in block.statements if s.line is not None and s.line > 0]
            if lines and min(lines) <= line <= max(lines):
                return block

        # 3. Fallback: closest statement at or before line
        closest_block = None
        closest_diff = float("inf")
        for block in cfg.blocks.values():
            for s in block.statements:
                if s.line is not None and s.line <= line:
                    diff = line - s.line
                    if diff < closest_diff:
                        closest_diff = diff
                        closest_block = block
        return closest_block
    except Exception:
        return None


def _stmt_text_and_vars(stmt) -> Tuple[str, Set[str]]:
    """
    Extract source text and variable names from a CFGStatement.
    Returns (text, set_of_var_names).
    """
    text = stmt.text.strip() if stmt.text else ""
    var_names: Set[str] = set()

    # Try Python AST node
    try:
        if stmt.ast_node is not None and isinstance(stmt.ast_node, ast.AST):
            for node in ast.walk(stmt.ast_node):
                if isinstance(node, ast.Name):
                    var_names.add(node.id)
            return text, var_names
    except Exception:
        pass

    # Fallback: simple token extraction from text
    import re
    tokens = re.findall(r'\b([A-Za-z_]\w*)\b', text)
    _KW = {
        "if", "else", "elif", "for", "while", "return", "and", "or", "not",
        "in", "is", "None", "True", "False", "def", "class", "import",
        "from", "with", "as", "try", "except", "finally", "raise", "pass",
        "break", "continue", "int", "char", "void", "float", "double",
        "struct", "switch", "case", "goto", "static", "const",
    }
    var_names = {t for t in tokens if t not in _KW}
    return text, var_names


def _collect_data_slice(
    cfg,
    def_use_chains,
    seed_line: int,
    seed_block,
) -> Set[Tuple[int, str]]:
    """
    BFS backward through def-use chains from variables used at seed_line.
    Returns a set of (line, text) slice statements.
    """
    slice_stmts: Set[Tuple[int, str]] = set()
    visited_lines: Set[int] = set()

    if not seed_block or not getattr(seed_block, "statements", None):
        return slice_stmts

    # Find the seed statement AT seed_line or closest statement <= seed_line
    seed_stmts = [s for s in seed_block.statements if s.line == seed_line]
    if not seed_stmts:
        candidates = [s for s in seed_block.statements if s.line is not None and s.line <= seed_line]
        seed_stmts = [max(candidates, key=lambda s: s.line)] if candidates else [seed_block.statements[0]]

    worklist: List = list(seed_stmts)

    while worklist:
        stmt = worklist.pop()
        if stmt.line in visited_lines:
            continue
        visited_lines.add(stmt.line)

        # Extract vars
        from New_Model.static_analysis.var_extractor import extract_statement_vars
        defs_in_stmt, uses_in_stmt = extract_statement_vars(stmt.text, stmt.ast_node)
        if not uses_in_stmt and not defs_in_stmt:
            _, fallback_vars = _stmt_text_and_vars(stmt)
            uses_in_stmt = fallback_vars

        if stmt.line != seed_line:
            slice_stmts.add((stmt.line, stmt.text.strip() if stmt.text else ""))

        # For each variable used, look up reaching definitions
        for var in uses_in_stmt:
            reaching_defs = set()

            # 1. Direct tuple key (var, stmt.line)
            if (var, stmt.line) in def_use_chains:
                reaching_defs.update(def_use_chains[(var, stmt.line)])
            # 2. String key (var)
            elif var in def_use_chains:
                val = def_use_chains[var]
                if isinstance(val, (list, set)):
                    reaching_defs.update(val)
            # 3. Fallback: match any tuple key starting with var
            else:
                for k, v_defs in def_use_chains.items():
                    if isinstance(k, tuple) and len(k) == 2 and k[0] == var:
                        if k[1] <= stmt.line:
                            reaching_defs.update(v_defs)

            for defn in reaching_defs:
                def_line = defn.line if hasattr(defn, "line") else defn.get("line") if isinstance(defn, dict) else None
                if def_line is None or def_line in visited_lines:
                    continue
                # Locate statement in CFG
                found = False
                for block in cfg.blocks.values():
                    for s in block.statements:
                        if s.line == def_line:
                            worklist.append(s)
                            found = True
                            break
                    if found:
                        break

    return slice_stmts


def _collect_control_slice(cfg, data_block_ids: Set[int], data_lines: Set[int]) -> Set[Tuple[int, str]]:
    """
    Find branch conditions controlling the statements in the slice:
    1. CFG conditional edges targeting data blocks.
    2. Syntax tree enclosing conditions (if/while/for/switch).
    """
    from New_Model.cfg.model import EdgeType

    condition_stmts: Set[Tuple[int, str]] = set()
    conditional_edge_types = {
        EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE,
        EdgeType.SWITCH_CASE, EdgeType.SWITCH_DEFAULT,
        EdgeType.LOOP_BACK,
    }

    try:
        # 1. CFG edges
        for edge in getattr(cfg, "edges", []):
            if edge.edge_type in conditional_edge_types:
                if edge.target_id in data_block_ids:
                    src_block = cfg.blocks.get(edge.source_id)
                    if src_block and src_block.statements:
                        cond_stmt = src_block.statements[-1]
                        if cond_stmt.line is not None:
                            condition_stmts.add((cond_stmt.line, cond_stmt.text.strip() if cond_stmt.text else ""))

        # 2. Syntax tree walking for enclosing control headers
        for block in cfg.blocks.values():
            for s in block.statements:
                # Python AST node: if, while, for
                if s.ast_node is not None and isinstance(s.ast_node, (ast.If, ast.While, ast.For)):
                    node = s.ast_node
                    body_lines = {
                        child.lineno
                        for child in ast.walk(node)
                        if hasattr(child, "lineno") and child.lineno != node.lineno
                    }
                    if body_lines & data_lines:
                        condition_stmts.add((s.line, s.text.strip() if s.text else ""))

                # Tree-sitter node: if_statement, while_statement, for_statement, switch_statement
                node = s.ast_node
                if node is not None and hasattr(node, "type") and hasattr(node, "start_point") and hasattr(node, "end_point"):
                    if node.type in ("if_statement", "while_statement", "for_statement", "switch_statement"):
                        start_l = node.start_point[0] + 1
                        end_l = node.end_point[0] + 1
                        if any(start_l < dl <= end_l for dl in data_lines):
                            condition_stmts.add((s.line, s.text.strip() if s.text else ""))

    except Exception:
        pass

    return condition_stmts


def _compute(cfg, def_use_chains, failing_line: int) -> SliceResult:
    result = SliceResult(failing_line=failing_line)

    if cfg is None or not hasattr(cfg, "blocks"):
        return result

    # Locate the block containing the failing line
    seed_block = _find_block_for_line(cfg, failing_line)
    if seed_block is None:
        return result

    # Data-dep slice
    data_stmts = _collect_data_slice(cfg, def_use_chains or {}, failing_line, seed_block)

    # Collect line numbers and block IDs
    data_lines = {line for line, _ in data_stmts}
    data_lines.add(failing_line)

    data_block_ids: Set[int] = set()
    for block in cfg.blocks.values():
        for s in block.statements:
            if s.line in data_lines:
                data_block_ids.add(block.id)
                break

    # Control-dep slice
    ctrl_stmts = _collect_control_slice(cfg, data_block_ids, data_lines)

    # Merge and sort
    all_stmts = data_stmts | ctrl_stmts
    result.slice_statements = sorted(all_stmts, key=lambda t: t[0])
    return result
