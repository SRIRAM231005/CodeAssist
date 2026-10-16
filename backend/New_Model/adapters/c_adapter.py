import hashlib
import re
from typing import List, Tuple, Optional

import tree_sitter
import tree_sitter_c

from .base import LanguageAdapter, FunctionInfo, CallInfo, LooseChunk


class CAdapter(LanguageAdapter):
    """C language adapter using Tree-sitter for robust, zero-dependency C parsing."""

    def __init__(self):
        self._language = tree_sitter.Language(tree_sitter_c.language())
        self._parser = tree_sitter.Parser(self._language)

    @property
    def language_name(self) -> str:
        return "c"

    def _unwrap_declarator(self, node: tree_sitter.Node) -> Tuple[str, str]:
        """
        Unwrap nested pointer/function declarators to extract:
        (function_name, parameter_signature)
        """
        curr = node
        while curr and curr.type in ("pointer_declarator", "parenthesized_declarator"):
            child = curr.child_by_field_name("declarator")
            curr = child if child else (curr.children[1] if len(curr.children) > 1 else None)

        if not curr or curr.type != "function_declarator":
            # Fallback search for child function_declarator
            for child in (node.children if node else []):
                if child.type == "function_declarator":
                    curr = child
                    break

        if curr and curr.type == "function_declarator":
            name_node = curr.child_by_field_name("declarator")
            params_node = curr.child_by_field_name("parameters")

            # In case name_node itself is a pointer declarator
            while name_node and name_node.type in ("pointer_declarator", "parenthesized_declarator"):
                sub = name_node.child_by_field_name("declarator")
                name_node = sub if sub else (name_node.children[1] if len(name_node.children) > 1 else None)

            name = name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
            sig = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
            return name, sig

        return (node.text.decode("utf-8", errors="replace") if node else "unknown"), "()"

    def extract_functions(self, source: str, file_path: str = "") -> List[FunctionInfo]:
        source_bytes = source.encode("utf-8")
        tree = self._parser.parse(source_bytes)
        functions: List[FunctionInfo] = []

        def visit(node: tree_sitter.Node):
            if node.type == "function_definition":
                decl_node = node.child_by_field_name("declarator")
                name, sig = self._unwrap_declarator(decl_node)

                # Check static storage class
                is_static = any(
                    child.type == "storage_class_specifier" and child.text == b"static"
                    for child in node.children
                )

                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                code = node.text.decode("utf-8", errors="replace")

                functions.append(
                    FunctionInfo(
                        name=name,
                        signature=sig,
                        span=(start_line, end_line),
                        code=code,
                        is_static=is_static,
                        file_path=file_path,
                    )
                )

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return functions

    def extract_calls(self, function_code: str, file_path: str = "") -> List[CallInfo]:
        tree = self._parser.parse(function_code.encode("utf-8"))
        calls: List[CallInfo] = []

        def visit(node: tree_sitter.Node):
            if node.type == "call_expression":
                fn_node = node.child_by_field_name("function")
                line = node.start_point.row + 1
                if fn_node:
                    if fn_node.type == "identifier":
                        calls.append(
                            CallInfo(
                                callee_name=fn_node.text.decode("utf-8", errors="replace"),
                                call_type="direct",
                                is_resolved=True,
                                line=line,
                            )
                        )
                    elif fn_node.type == "field_expression":
                        field = fn_node.child_by_field_name("field")
                        callee = field.text.decode("utf-8", errors="replace") if field else fn_node.text.decode("utf-8", errors="replace")
                        # Arrow operator or struct function pointer
                        calls.append(
                            CallInfo(
                                callee_name=callee,
                                call_type="function_pointer" if b"->" in fn_node.text else "method",
                                is_resolved=True,
                                line=line,
                            )
                        )
                    else:
                        calls.append(
                            CallInfo(
                                callee_name=fn_node.text.decode("utf-8", errors="replace"),
                                call_type="function_pointer",
                                is_resolved=False,
                                line=line,
                            )
                        )

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return calls

    def extract_loose_chunks(self, source: str, file_path: str = "") -> List[LooseChunk]:
        tree = self._parser.parse(source.encode("utf-8"))
        chunks: List[LooseChunk] = []

        for child in tree.root_node.children:
            start_line = child.start_point.row + 1
            end_line = child.end_point.row + 1
            code = child.text.decode("utf-8", errors="replace")

            if child.type in ("preproc_def", "preproc_function_def"):
                name_node = child.child_by_field_name("name")
                name = name_node.text.decode("utf-8", errors="replace") if name_node else "#define"
                chunks.append(LooseChunk(chunk_type="macro", name=name, span=(start_line, end_line), code=code))

            elif child.type in ("type_definition", "struct_specifier", "enum_specifier", "union_specifier"):
                name_node = child.child_by_field_name("name")
                name = name_node.text.decode("utf-8", errors="replace") if name_node else child.type
                chunks.append(LooseChunk(chunk_type="struct", name=name, span=(start_line, end_line), code=code))

            elif child.type == "declaration":
                # Global variable declaration (e.g. int g_counter = 0;)
                chunks.append(LooseChunk(chunk_type="global_var", name="global_decl", span=(start_line, end_line), code=code))

        return chunks

    def normalize_for_hash(self, code: str) -> str:
        """Strip comments and normalize tokens/whitespace for stable AST hash."""
        # Strip C/C++ style comments
        no_block_comments = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        no_line_comments = re.sub(r"//.*", "", no_block_comments)
        # Collapse all whitespace sequences into single spaces
        normalized = " ".join(no_line_comments.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
