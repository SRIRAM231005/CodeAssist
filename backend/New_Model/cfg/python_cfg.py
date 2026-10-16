import ast
from typing import List, Optional, Tuple
from .model import CFG, BasicBlock, EdgeType


class PythonCFGBuilder:
    """
    Constructs a Control Flow Graph from a Python function AST.
    Decomposes instructions into Basic Blocks connected by typed control edges.
    """

    def __init__(self, function_name: str = "function"):
        self.function_name = function_name
        self.cfg = CFG(function_name=function_name)
        self._next_id = 0
        self._loop_stack: List[Tuple[int, int]] = []  # (continue_target_id, break_target_id)
        self.exit_block_id: int = -1

    def _new_block(self, name_prefix: str = "B") -> BasicBlock:
        block_id = self._next_id
        self._next_id += 1
        block = BasicBlock(id=block_id, name=f"{name_prefix}{block_id}")
        self.cfg.add_block(block)
        return block

    def build_from_code(self, source_code: str, target_function_name: Optional[str] = None) -> CFG:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            # Return empty CFG on syntax error
            entry = self._new_block("Entry")
            entry.is_entry = True
            return self.cfg

        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if target_function_name is None or node.name == target_function_name:
                    fn_node = node
                    self.function_name = node.name
                    self.cfg.function_name = node.name
                    break

        if not fn_node:
            # If whole code is a block or function not found, treat top-level body as statements
            return self.build_from_node(tree, function_name=target_function_name or "module")

        return self.build_from_node(fn_node, function_name=fn_node.name)

    def build_from_node(self, node: ast.AST, function_name: str = "function") -> CFG:
        self.function_name = function_name
        self.cfg.function_name = function_name
        self._next_id = 0
        self._loop_stack = []

        # 1. Create Entry and Exit blocks
        entry_block = self._new_block("Entry")
        entry_block.is_entry = True
        self.cfg.entry_block_id = entry_block.id

        exit_block = self._new_block("Exit")
        exit_block.is_exit = True
        self.exit_block_id = exit_block.id
        self.cfg.exit_block_id = exit_block.id

        # 2. Get body statements
        body: List[ast.stmt] = getattr(node, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entry_block.add_statement(
                f"def {node.name}(...)",
                line=node.lineno,
                col=node.col_offset,
                ast_node=node,
            )

        # 3. Process statement list starting from Entry block
        curr_blocks = [entry_block.id]
        terminal_blocks = self._process_stmts(body, curr_blocks)

        # 4. Any open blocks fall through to Exit
        for bid in terminal_blocks:
            self.cfg.add_edge(bid, self.exit_block_id, EdgeType.FALLTHROUGH)

        # 5. Compute reachability
        self.cfg.compute_reachability()
        return self.cfg

    def _process_stmts(self, stmts: List[ast.stmt], incoming_blocks: List[int]) -> List[int]:
        """
        Process a list of sequential statements.
        Returns a list of open block IDs that exit from the statement sequence.
        """
        curr_blocks = incoming_blocks

        for stmt in stmts:
            if not curr_blocks:
                # Statements after unconditional jump are unreachable
                dead_block = self._new_block("Dead")
                curr_blocks = [dead_block.id]

            curr_blocks = self._process_single_stmt(stmt, curr_blocks)

        return curr_blocks

    def _process_single_stmt(self, stmt: ast.stmt, curr_blocks: List[int]) -> List[int]:
        line = getattr(stmt, "lineno", 0)
        col = getattr(stmt, "col_offset", None)
        stmt_text = ast.unparse(stmt) if hasattr(ast, "unparse") else type(stmt).__name__

        # ── RETURN ────────────────────────────────────────────────────────────
        if isinstance(stmt, ast.Return):
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(stmt_text, line, col, stmt)
                self.cfg.add_edge(bid, self.exit_block_id, EdgeType.RETURN)
            return []  # No fallthrough from return

        # ── RAISE ─────────────────────────────────────────────────────────────
        if isinstance(stmt, ast.Raise):
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(stmt_text, line, col, stmt)
                self.cfg.add_edge(bid, self.exit_block_id, EdgeType.EXCEPTION)
            return []  # No fallthrough from raise

        # ── BREAK ─────────────────────────────────────────────────────────────
        if isinstance(stmt, ast.Break):
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement("break", line, col, stmt)
                if self._loop_stack:
                    _, break_target = self._loop_stack[-1]
                    self.cfg.add_edge(bid, break_target, EdgeType.BREAK)
            return []

        # ── CONTINUE ──────────────────────────────────────────────────────────
        if isinstance(stmt, ast.Continue):
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement("continue", line, col, stmt)
                if self._loop_stack:
                    cont_target, _ = self._loop_stack[-1]
                    self.cfg.add_edge(bid, cont_target, EdgeType.CONTINUE)
            return []

        # ── IF / ELIF / ELSE ──────────────────────────────────────────────────
        if isinstance(stmt, ast.If):
            test_text = ast.unparse(stmt.test) if hasattr(ast, "unparse") else "if_cond"

            # Condition is evaluated in curr_blocks
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(f"if {test_text}:", line, col, stmt)

            # Then branch
            then_block = self._new_block("Then")
            for bid in curr_blocks:
                self.cfg.add_edge(bid, then_block.id, EdgeType.BRANCH_TRUE, condition_desc="True")
            then_exits = self._process_stmts(stmt.body, [then_block.id])

            # Else branch (or direct join if no else)
            if stmt.orelse:
                else_block = self._new_block("Else")
                for bid in curr_blocks:
                    self.cfg.add_edge(bid, else_block.id, EdgeType.BRANCH_FALSE, condition_desc="False")
                else_exits = self._process_stmts(stmt.orelse, [else_block.id])
            else:
                else_exits = curr_blocks

            # If both then and else ended (e.g. returns), return empty
            all_exits = list(set(then_exits + (else_exits if stmt.orelse else [])))
            if not all_exits and not stmt.orelse:
                # If then branch returned and no else, the condition False path still continues!
                all_exits = curr_blocks

            if all_exits:
                join_block = self._new_block("IfJoin")
                for bid in then_exits:
                    self.cfg.add_edge(bid, join_block.id, EdgeType.FALLTHROUGH)
                if stmt.orelse:
                    for bid in else_exits:
                        self.cfg.add_edge(bid, join_block.id, EdgeType.FALLTHROUGH)
                else:
                    for bid in curr_blocks:
                        self.cfg.add_edge(bid, join_block.id, EdgeType.BRANCH_FALSE, condition_desc="False")
                return [join_block.id]

            return all_exits

        # ── WHILE LOOP ────────────────────────────────────────────────────────
        if isinstance(stmt, ast.While):
            test_text = ast.unparse(stmt.test) if hasattr(ast, "unparse") else "while_cond"
            loop_header = self._new_block("WhileHeader")
            loop_exit = self._new_block("WhileExit")

            # Fallthrough into header
            for bid in curr_blocks:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.FALLTHROUGH)

            loop_header.add_statement(f"while {test_text}:", line, col, stmt)

            # Push loop stack for break/continue
            self._loop_stack.append((loop_header.id, loop_exit.id))

            # Loop body
            body_block = self._new_block("WhileBody")
            self.cfg.add_edge(loop_header.id, body_block.id, EdgeType.BRANCH_TRUE, condition_desc="True")
            body_exits = self._process_stmts(stmt.body, [body_block.id])

            for bid in body_exits:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.LOOP_BACK)

            self._loop_stack.pop()

            # False condition / loop else branch
            if stmt.orelse:
                else_block = self._new_block("WhileElse")
                self.cfg.add_edge(loop_header.id, else_block.id, EdgeType.BRANCH_FALSE, condition_desc="False")
                else_exits = self._process_stmts(stmt.orelse, [else_block.id])
                for bid in else_exits:
                    self.cfg.add_edge(bid, loop_exit.id, EdgeType.FALLTHROUGH)
            else:
                self.cfg.add_edge(loop_header.id, loop_exit.id, EdgeType.BRANCH_FALSE, condition_desc="False")

            return [loop_exit.id]

        # ── FOR LOOP ──────────────────────────────────────────────────────────
        if isinstance(stmt, ast.For):
            target_text = ast.unparse(stmt.target) if hasattr(ast, "unparse") else "var"
            iter_text = ast.unparse(stmt.iter) if hasattr(ast, "unparse") else "iterable"

            loop_header = self._new_block("ForHeader")
            loop_exit = self._new_block("ForExit")

            for bid in curr_blocks:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.FALLTHROUGH)

            loop_header.add_statement(f"for {target_text} in {iter_text}:", line, col, stmt)

            self._loop_stack.append((loop_header.id, loop_exit.id))

            body_block = self._new_block("ForBody")
            self.cfg.add_edge(loop_header.id, body_block.id, EdgeType.BRANCH_TRUE, condition_desc="HasNext")
            body_exits = self._process_stmts(stmt.body, [body_block.id])

            for bid in body_exits:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.LOOP_BACK)

            self._loop_stack.pop()

            if stmt.orelse:
                else_block = self._new_block("ForElse")
                self.cfg.add_edge(loop_header.id, else_block.id, EdgeType.BRANCH_FALSE, condition_desc="Exhausted")
                else_exits = self._process_stmts(stmt.orelse, [else_block.id])
                for bid in else_exits:
                    self.cfg.add_edge(bid, loop_exit.id, EdgeType.FALLTHROUGH)
            else:
                self.cfg.add_edge(loop_header.id, loop_exit.id, EdgeType.BRANCH_FALSE, condition_desc="Exhausted")

            return [loop_exit.id]

        # ── TRY / EXCEPT / FINALLY ────────────────────────────────────────────
        if isinstance(stmt, ast.Try):
            try_entry = self._new_block("Try")
            for bid in curr_blocks:
                self.cfg.add_edge(bid, try_entry.id, EdgeType.FALLTHROUGH)

            try_exits = self._process_stmts(stmt.body, [try_entry.id])

            handler_exits = []
            for handler in stmt.handlers:
                h_name = f"except {ast.unparse(handler.type)}" if (handler.type and hasattr(ast, "unparse")) else "except"
                h_block = self._new_block("Handler")
                h_block.add_statement(h_name, line=handler.lineno, col=handler.col_offset, ast_node=handler)
                self.cfg.add_edge(try_entry.id, h_block.id, EdgeType.EXCEPTION)
                exits = self._process_stmts(handler.body, [h_block.id])
                handler_exits.extend(exits)

            join_block = self._new_block("TryJoin")
            for bid in try_exits + handler_exits:
                self.cfg.add_edge(bid, join_block.id, EdgeType.FALLTHROUGH)

            if stmt.finalbody:
                final_block = self._new_block("Finally")
                self.cfg.add_edge(join_block.id, final_block.id, EdgeType.FALLTHROUGH)
                return self._process_stmts(stmt.finalbody, [final_block.id])

            return [join_block.id]

        # ── STRAIGHT-LINE STATEMENTS (Assign, Call, Expr, With, etc.) ─────────
        for bid in curr_blocks:
            self.cfg.blocks[bid].add_statement(stmt_text, line, col, stmt)

        return curr_blocks
