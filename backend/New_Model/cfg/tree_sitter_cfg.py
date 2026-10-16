from typing import List, Optional, Tuple, Dict
import tree_sitter
import tree_sitter_c
import tree_sitter_cpp

from .model import CFG, BasicBlock, EdgeType


class TreeSitterCFGBuilder:
    """
    Constructs a Control Flow Graph from a C/C++ function using Tree-sitter.
    Decomposes instructions into Basic Blocks connected by typed control edges.
    """

    def __init__(self, language: str = "c", function_name: str = "function"):
        self.language_name = language.lower()
        self.function_name = function_name
        self.cfg = CFG(function_name=function_name)
        self._next_id = 0
        self._loop_stack: List[Tuple[int, int]] = []  # (continue_target_id, break_target_id)
        self.exit_block_id: int = -1
        self._label_map: Dict[str, int] = {}          # label_name -> block_id
        self._unresolved_gotos: List[Tuple[int, str]] = []  # (source_block_id, label_name)

        if self.language_name == "cpp":
            self._ts_lang = tree_sitter.Language(tree_sitter_cpp.language())
        else:
            self._ts_lang = tree_sitter.Language(tree_sitter_c.language())
        self._parser = tree_sitter.Parser(self._ts_lang)

    def _new_block(self, name_prefix: str = "B") -> BasicBlock:
        block_id = self._next_id
        self._next_id += 1
        block = BasicBlock(id=block_id, name=f"{name_prefix}{block_id}")
        self.cfg.add_block(block)
        return block

    def build_from_code(self, source_code: str, target_function_name: Optional[str] = None) -> CFG:
        source_bytes = source_code.encode("utf-8")
        tree = self._parser.parse(source_bytes)

        target_fn_node = None

        def find_fn(node: tree_sitter.Node):
            nonlocal target_fn_node
            if node.type == "function_definition":
                if target_function_name is None:
                    target_fn_node = node
                    return
                # Check declarator text
                decl = node.child_by_field_name("declarator")
                decl_text = decl.text.decode("utf-8", errors="replace") if decl else ""
                if target_function_name in decl_text:
                    target_fn_node = node
                    return

            for child in node.children:
                if target_fn_node is None:
                    find_fn(child)

        find_fn(tree.root_node)

        if not target_fn_node:
            entry = self._new_block("Entry")
            entry.is_entry = True
            return self.cfg

        fn_name = target_function_name or "c_func"
        return self.build_from_node(target_fn_node, function_name=fn_name)

    def build_from_node(self, fn_node: tree_sitter.Node, function_name: str = "function") -> CFG:
        self.function_name = function_name
        self.cfg.function_name = function_name
        self._next_id = 0
        self._loop_stack = []
        self._label_map = {}
        self._unresolved_gotos = []

        # 1. Entry and Exit blocks
        entry_block = self._new_block("Entry")
        entry_block.is_entry = True
        self.cfg.entry_block_id = entry_block.id

        exit_block = self._new_block("Exit")
        exit_block.is_exit = True
        self.exit_block_id = exit_block.id
        self.cfg.exit_block_id = exit_block.id

        decl_node = fn_node.child_by_field_name("declarator")
        decl_text = decl_node.text.decode("utf-8", errors="replace") if decl_node else function_name
        entry_block.add_statement(
            decl_text,
            line=fn_node.start_point.row + 1,
            col=fn_node.start_point.column,
            ast_node=fn_node,
        )

        body_node = fn_node.child_by_field_name("body")
        if body_node:
            curr_blocks = [entry_block.id]
            terminal_blocks = self._process_node(body_node, curr_blocks)
            for bid in terminal_blocks:
                self.cfg.add_edge(bid, self.exit_block_id, EdgeType.FALLTHROUGH)

        # 2. Resolve gotos
        for src_id, label in self._unresolved_gotos:
            if label in self._label_map:
                target_id = self._label_map[label]
                self.cfg.add_edge(src_id, target_id, EdgeType.GOTO, condition_desc=f"goto {label}")

        # 3. Compute reachability
        self.cfg.compute_reachability()
        return self.cfg

    def _process_node(self, node: tree_sitter.Node, curr_blocks: List[int]) -> List[int]:
        if not curr_blocks:
            dead_block = self._new_block("Dead")
            curr_blocks = [dead_block.id]

        ntype = node.type
        line = node.start_point.row + 1
        col = node.start_point.column
        text = node.text.decode("utf-8", errors="replace").strip()

        # ── COMPOUND STATEMENT { ... } ───────────────────────────────────────
        if ntype == "compound_statement":
            for child in node.children:
                if child.type in ("{", "}"):
                    continue
                curr_blocks = self._process_node(child, curr_blocks)
            return curr_blocks

        # ── RETURN STATEMENT ──────────────────────────────────────────────────
        if ntype == "return_statement":
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(text, line, col, node)
                self.cfg.add_edge(bid, self.exit_block_id, EdgeType.RETURN)
            return []

        # ── BREAK STATEMENT ───────────────────────────────────────────────────
        if ntype == "break_statement":
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement("break;", line, col, node)
                if self._loop_stack:
                    _, break_target = self._loop_stack[-1]
                    self.cfg.add_edge(bid, break_target, EdgeType.BREAK)
            return []

        # ── CONTINUE STATEMENT ────────────────────────────────────────────────
        if ntype == "continue_statement":
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement("continue;", line, col, node)
                if self._loop_stack:
                    cont_target, _ = self._loop_stack[-1]
                    self.cfg.add_edge(bid, cont_target, EdgeType.CONTINUE)
            return []

        # ── GOTO STATEMENT ────────────────────────────────────────────────────
        if ntype == "goto_statement":
            label_node = next((c for c in node.children if c.type in ("statement_identifier", "identifier")), None)
            label_name = label_node.text.decode("utf-8", errors="replace") if label_node else ""
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(f"goto {label_name};", line, col, node)
                self._unresolved_gotos.append((bid, label_name))
            return []

        # ── LABELED STATEMENT (label: ...) ───────────────────────────────────
        if ntype == "labeled_statement":
            label_node = next((c for c in node.children if c.type in ("statement_identifier", "identifier")), None)
            label_name = label_node.text.decode("utf-8", errors="replace") if label_node else "label"
            label_block = self._new_block(f"Label_{label_name}")
            self._label_map[label_name] = label_block.id

            for bid in curr_blocks:
                self.cfg.add_edge(bid, label_block.id, EdgeType.FALLTHROUGH)

            colon_idx = -1
            for idx, ch in enumerate(node.children):
                if ch.type == ":":
                    colon_idx = idx
                    break

            if colon_idx != -1 and colon_idx + 1 < len(node.children):
                sub_blocks = [label_block.id]
                for child in node.children[colon_idx + 1:]:
                    sub_blocks = self._process_node(child, sub_blocks)
                return sub_blocks

            return [label_block.id]

        # ── IF STATEMENT ──────────────────────────────────────────────────────
        if ntype == "if_statement":
            cond_node = node.child_by_field_name("condition")
            cond_text = cond_node.text.decode("utf-8", errors="replace") if cond_node else "cond"

            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(f"if {cond_text}", line, col, node)

            then_block = self._new_block("Then")
            for bid in curr_blocks:
                self.cfg.add_edge(bid, then_block.id, EdgeType.BRANCH_TRUE, condition_desc="True")

            consequence = node.child_by_field_name("consequence")
            then_exits = self._process_node(consequence, [then_block.id]) if consequence else [then_block.id]

            alternative = node.child_by_field_name("alternative")
            if alternative:
                else_block = self._new_block("Else")
                for bid in curr_blocks:
                    self.cfg.add_edge(bid, else_block.id, EdgeType.BRANCH_FALSE, condition_desc="False")
                else_exits = self._process_node(alternative, [else_block.id])
            else:
                else_exits = curr_blocks

            all_exits = list(set(then_exits + (else_exits if alternative else [])))
            if not all_exits and not alternative:
                all_exits = curr_blocks

            if all_exits:
                join_block = self._new_block("IfJoin")
                for bid in then_exits:
                    self.cfg.add_edge(bid, join_block.id, EdgeType.FALLTHROUGH)
                if alternative:
                    for bid in else_exits:
                        self.cfg.add_edge(bid, join_block.id, EdgeType.FALLTHROUGH)
                else:
                    for bid in curr_blocks:
                        self.cfg.add_edge(bid, join_block.id, EdgeType.BRANCH_FALSE, condition_desc="False")
                return [join_block.id]

            return all_exits

        # ── WHILE STATEMENT ───────────────────────────────────────────────────
        if ntype == "while_statement":
            cond_node = node.child_by_field_name("condition")
            cond_text = cond_node.text.decode("utf-8", errors="replace") if cond_node else "cond"

            loop_header = self._new_block("WhileHeader")
            loop_exit = self._new_block("WhileExit")

            for bid in curr_blocks:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.FALLTHROUGH)

            loop_header.add_statement(f"while {cond_text}", line, col, node)
            self._loop_stack.append((loop_header.id, loop_exit.id))

            body_node = node.child_by_field_name("body")
            body_block = self._new_block("WhileBody")
            self.cfg.add_edge(loop_header.id, body_block.id, EdgeType.BRANCH_TRUE, condition_desc="True")

            body_exits = self._process_node(body_node, [body_block.id]) if body_node else [body_block.id]
            for bid in body_exits:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.LOOP_BACK)

            self._loop_stack.pop()
            self.cfg.add_edge(loop_header.id, loop_exit.id, EdgeType.BRANCH_FALSE, condition_desc="False")

            return [loop_exit.id]

        # ── DO-WHILE STATEMENT ────────────────────────────────────────────────
        if ntype == "do_statement":
            cond_node = node.child_by_field_name("condition")
            cond_text = cond_node.text.decode("utf-8", errors="replace") if cond_node else "cond"

            do_body = self._new_block("DoBody")
            do_test = self._new_block("DoTest")
            do_exit = self._new_block("DoExit")

            for bid in curr_blocks:
                self.cfg.add_edge(bid, do_body.id, EdgeType.FALLTHROUGH)

            self._loop_stack.append((do_test.id, do_exit.id))

            body_node = node.child_by_field_name("body")
            body_exits = self._process_node(body_node, [do_body.id]) if body_node else [do_body.id]
            for bid in body_exits:
                self.cfg.add_edge(bid, do_test.id, EdgeType.FALLTHROUGH)

            do_test.add_statement(f"while {cond_text};", line, col, node)
            self.cfg.add_edge(do_test.id, do_body.id, EdgeType.LOOP_BACK, condition_desc="True")
            self.cfg.add_edge(do_test.id, do_exit.id, EdgeType.BRANCH_FALSE, condition_desc="False")

            self._loop_stack.pop()
            return [do_exit.id]

        # ── FOR STATEMENT ─────────────────────────────────────────────────────
        if ntype == "for_statement":
            init_node = node.child_by_field_name("initializer")
            cond_node = node.child_by_field_name("condition")
            update_node = node.child_by_field_name("update")

            if init_node:
                init_text = init_node.text.decode("utf-8", errors="replace")
                for bid in curr_blocks:
                    self.cfg.blocks[bid].add_statement(init_text, line, col, init_node)

            loop_header = self._new_block("ForHeader")
            loop_update = self._new_block("ForUpdate")
            loop_exit = self._new_block("ForExit")

            for bid in curr_blocks:
                self.cfg.add_edge(bid, loop_header.id, EdgeType.FALLTHROUGH)

            cond_text = cond_node.text.decode("utf-8", errors="replace") if cond_node else "true"
            loop_header.add_statement(f"for condition: {cond_text}", line, col, node)

            self._loop_stack.append((loop_update.id, loop_exit.id))

            body_node = node.child_by_field_name("body")
            body_block = self._new_block("ForBody")
            self.cfg.add_edge(loop_header.id, body_block.id, EdgeType.BRANCH_TRUE, condition_desc="True")

            body_exits = self._process_node(body_node, [body_block.id]) if body_node else [body_block.id]
            for bid in body_exits:
                self.cfg.add_edge(bid, loop_update.id, EdgeType.FALLTHROUGH)

            update_text = update_node.text.decode("utf-8", errors="replace") if update_node else "step"
            loop_update.add_statement(f"for update: {update_text}", line, col, update_node)
            self.cfg.add_edge(loop_update.id, loop_header.id, EdgeType.LOOP_BACK)

            self._loop_stack.pop()
            self.cfg.add_edge(loop_header.id, loop_exit.id, EdgeType.BRANCH_FALSE, condition_desc="False")

            return [loop_exit.id]

        # ── CASE / DEFAULT STATEMENT ──────────────────────────────────────────
        if ntype == "case_statement":
            colon_idx = -1
            for idx, ch in enumerate(node.children):
                if ch.type == ":":
                    colon_idx = idx
                    break

            label_text = node.children[0].text.decode("utf-8", errors="replace")
            if colon_idx != -1 and colon_idx > 0:
                label_text = " ".join(c.text.decode("utf-8", errors="replace") for c in node.children[:colon_idx + 1])

            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(label_text, line, col, node)

            if colon_idx != -1 and colon_idx + 1 < len(node.children):
                sub_blocks = curr_blocks
                for child in node.children[colon_idx + 1:]:
                    sub_blocks = self._process_node(child, sub_blocks)
                return sub_blocks

            return curr_blocks

        # ── SWITCH STATEMENT ──────────────────────────────────────────────────
        if ntype == "switch_statement":
            cond_node = node.child_by_field_name("condition")
            cond_text = cond_node.text.decode("utf-8", errors="replace") if cond_node else "switch_val"
            for bid in curr_blocks:
                self.cfg.blocks[bid].add_statement(f"switch {cond_text}", line, col, node)

            switch_exit = self._new_block("SwitchExit")
            self._loop_stack.append((switch_exit.id, switch_exit.id))

            body_node = node.child_by_field_name("body")
            all_case_exits = []
            if body_node:
                for child in body_node.children:
                    if child.type == "case_statement":
                        case_block = self._new_block("Case")
                        for bid in curr_blocks:
                            self.cfg.add_edge(bid, case_block.id, EdgeType.SWITCH_CASE)
                        case_exits = self._process_node(child, [case_block.id])
                        all_case_exits.extend(case_exits)
            self._loop_stack.pop()

            for bid in all_case_exits:
                self.cfg.add_edge(bid, switch_exit.id, EdgeType.FALLTHROUGH)
            return [switch_exit.id]

        # ── STRAIGHT-LINE STATEMENT (declaration, expression_statement, etc.) ─
        first_line_text = text.splitlines()[0] if text else ntype
        for bid in curr_blocks:
            self.cfg.blocks[bid].add_statement(first_line_text, line, col, node)

        return curr_blocks
