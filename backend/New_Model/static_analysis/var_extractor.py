import ast
import re
from typing import Set, Tuple, Any

C_KEYWORDS = {
    "int", "float", "double", "char", "void", "short", "long", "signed", "unsigned",
    "const", "static", "volatile", "register", "auto", "struct", "union", "enum",
    "typedef", "sizeof", "return", "if", "else", "for", "while", "do", "switch",
    "case", "default", "break", "continue", "goto", "true", "false", "NULL",
    "nullptr", "class", "public", "private", "protected", "virtual", "namespace",
    "new", "delete", "this", "template", "typename", "bool"
}

PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
    "pass", "raise", "return", "try", "while", "with", "yield", "self", "cls"
}

ALL_KEYWORDS = C_KEYWORDS | PY_KEYWORDS


def extract_statement_vars(stmt_text: str, ast_node: Any = None) -> Tuple[Set[str], Set[str]]:
    """
    Extract (defined_variables, used_variables) from a CFG statement.
    Uses AST node if available, with robust token-level fallback.
    """
    defs: Set[str] = set()
    uses: Set[str] = set()

    # ── 1. Python AST Node Available ──────────────────────────────────────────
    if ast_node is not None and isinstance(ast_node, ast.AST):
        for node in ast.walk(ast_node):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    defs.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    uses.add(node.id)
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    defs.add(node.target.id)
                    uses.add(node.target.id)
            elif isinstance(node, ast.arg):
                defs.add(node.arg)

        # Filter keywords
        defs = {d for d in defs if d not in PY_KEYWORDS}
        uses = {u for u in uses if u not in PY_KEYWORDS}
        return defs, uses

    # ── 2. Tree-Sitter Node Available ────────────────────────────────────────
    if ast_node is not None and hasattr(ast_node, "type") and hasattr(ast_node, "children"):
        _extract_ts_node(ast_node, defs, uses)
        defs = {d for d in defs if d not in ALL_KEYWORDS and not d.isdigit()}
        uses = {u for u in uses if u not in ALL_KEYWORDS and not u.isdigit()}
        return defs, uses

    # ── 3. Fallback: Syntax / Regex Pattern Matching ─────────────────────────
    clean_text = stmt_text.strip().rstrip(";")

    # Match assignments: var = ... or var += ...
    assign_match = re.match(r"^(\w+)\s*(\+|-|\*|/|%|&|\||\^)?=\s*(.+)$", clean_text)
    if assign_match:
        var, op, rhs = assign_match.group(1), assign_match.group(2), assign_match.group(3)
        defs.add(var)
        if op:  # augmented assignment uses its own value
            uses.add(var)
        for token in re.findall(r"\b[a-zA-Z_]\w*\b", rhs):
            uses.add(token)

    # Match declarations: int x = y; or auto z = 10;
    decl_match = re.match(r"^(?:const\s+)?(?:\w+\s+)+(\w+)\s*=\s*(.+)$", clean_text)
    if decl_match:
        var, rhs = decl_match.group(1), decl_match.group(2)
        defs.add(var)
        for token in re.findall(r"\b[a-zA-Z_]\w*\b", rhs):
            uses.add(token)

    # Match increments/decrements: x++ or --y
    inc_match = re.match(r"^(\+\+|--)?\s*(\w+)\s*(\+\+|--)?$", clean_text)
    if inc_match and (inc_match.group(1) or inc_match.group(3)):
        var = inc_match.group(2)
        defs.add(var)
        uses.add(var)

    # If no defs found, all identifier tokens are uses (e.g., if (x > 0), return y;)
    if not defs:
        for token in re.findall(r"\b[a-zA-Z_]\w*\b", clean_text):
            uses.add(token)

    defs = {d for d in defs if d not in ALL_KEYWORDS and not d.isdigit()}
    uses = {u for u in uses if u not in ALL_KEYWORDS and not u.isdigit()}
    return defs, uses


def _extract_ts_node(node: Any, defs: Set[str], uses: Set[str]):
    ntype = node.type

    if ntype == "assignment_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        op = node.children[1].text.decode("utf-8", errors="replace") if len(node.children) > 1 else "="

        if left:
            left_text = left.text.decode("utf-8", errors="replace")
            defs.add(left_text)
            if op != "=":
                uses.add(left_text)
        if right:
            _collect_all_identifiers(right, uses)
        return

    if ntype == "init_declarator":
        decl = node.child_by_field_name("declarator")
        val = node.child_by_field_name("value")
        if decl:
            defs.add(decl.text.decode("utf-8", errors="replace"))
        if val:
            _collect_all_identifiers(val, uses)
        return

    if ntype == "update_expression":
        arg = node.child_by_field_name("argument") or (node.children[0] if node.children else None)
        if arg:
            arg_text = arg.text.decode("utf-8", errors="replace")
            defs.add(arg_text)
            uses.add(arg_text)
        return

    for child in node.children:
        _extract_ts_node(child, defs, uses)


def _collect_all_identifiers(node: Any, target_set: Set[str]):
    if node.type in ("identifier", "field_identifier"):
        target_set.add(node.text.decode("utf-8", errors="replace"))
    for child in node.children:
        _collect_all_identifiers(child, target_set)
