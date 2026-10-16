import hashlib
import re
from typing import List, Tuple, Optional

import tree_sitter
import tree_sitter_cpp

from .base import LanguageAdapter, FunctionInfo, CallInfo, LooseChunk


class CppAdapter(LanguageAdapter):
    """C++ language adapter using Tree-sitter for C++ syntax extraction."""

    def __init__(self):
        self._language = tree_sitter.Language(tree_sitter_cpp.language())
        self._parser = tree_sitter.Parser(self._language)

    @property
    def language_name(self) -> str:
        return "cpp"

    def _unwrap_declarator(self, node: tree_sitter.Node) -> Tuple[str, str]:
        """
        Unwrap declarator to extract (qualified_name, parameter_signature).
        Handles pointers, references, and qualified identifiers (e.g. Class::method).
        """
        curr = node
        while curr and curr.type in ("pointer_declarator", "reference_declarator", "parenthesized_declarator"):
            child = curr.child_by_field_name("declarator")
            curr = child if child else (curr.children[1] if len(curr.children) > 1 else None)

        if not curr or curr.type != "function_declarator":
            for child in (node.children if node else []):
                if child.type == "function_declarator":
                    curr = child
                    break

        if curr and curr.type == "function_declarator":
            name_node = curr.child_by_field_name("declarator")
            params_node = curr.child_by_field_name("parameters")

            while name_node and name_node.type in ("pointer_declarator", "reference_declarator", "parenthesized_declarator"):
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
        scope_stack: List[str] = []

        def visit(node: tree_sitter.Node):
            pushed_scope = False

            if node.type in ("namespace_definition", "class_specifier", "struct_specifier"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    scope_stack.append(name_node.text.decode("utf-8", errors="replace"))
                    pushed_scope = True

            elif node.type == "function_definition":
                decl_node = node.child_by_field_name("declarator")
                raw_name, sig = self._unwrap_declarator(decl_node)

                # Check if raw_name is already qualified (e.g., MyClass::method)
                if "::" in raw_name or not scope_stack:
                    qualified_name = raw_name
                else:
                    qualified_name = f"{'::'.join(scope_stack)}::{raw_name}"

                # Check static or virtual
                is_static = any(
                    child.type == "storage_class_specifier" and child.text == b"static"
                    for child in node.children
                )
                is_virtual = any(
                    child.type == "virtual" or child.text == b"virtual"
                    for child in node.children
                )

                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                code = node.text.decode("utf-8", errors="replace")

                metadata = {}
                if is_virtual:
                    metadata["is_virtual"] = True

                functions.append(
                    FunctionInfo(
                        name=qualified_name,
                        signature=sig,
                        span=(start_line, end_line),
                        code=code,
                        is_static=is_static,
                        file_path=file_path,
                        metadata=metadata,
                    )
                )

            for child in node.children:
                visit(child)

            if pushed_scope:
                scope_stack.pop()

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
                    if fn_node.type in ("identifier", "qualified_identifier"):
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
                        is_arrow = b"->" in fn_node.text
                        calls.append(
                            CallInfo(
                                callee_name=callee,
                                call_type="virtual" if is_arrow else "method",
                                is_resolved=not is_arrow,  # Mark virtual/pointer calls unresolved
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

        def visit(node: tree_sitter.Node):
            start_line = node.start_point.row + 1
            end_line = node.end_point.row + 1
            code = node.text.decode("utf-8", errors="replace")

            if node.type in ("preproc_def", "preproc_function_def"):
                name_node = node.child_by_field_name("name")
                name = name_node.text.decode("utf-8", errors="replace") if name_node else "#define"
                chunks.append(LooseChunk(chunk_type="macro", name=name, span=(start_line, end_line), code=code))

            elif node.type in ("class_specifier", "struct_specifier"):
                name_node = node.child_by_field_name("name")
                name = name_node.text.decode("utf-8", errors="replace") if name_node else node.type
                chunks.append(LooseChunk(chunk_type="class", name=name, span=(start_line, end_line), code=code))

            elif node.type == "template_declaration":
                chunks.append(LooseChunk(chunk_type="template", name="template", span=(start_line, end_line), code=code))

            elif node.type == "declaration" and node.parent and node.parent.type in ("translation_unit", "declaration_list"):
                chunks.append(LooseChunk(chunk_type="global_var", name="global_decl", span=(start_line, end_line), code=code))

            for child in node.children:
                # Do not re-traverse inside classes/structs to avoid duplicate inner chunks
                if node.type not in ("class_specifier", "struct_specifier"):
                    visit(child)

        visit(tree.root_node)
        return chunks

    def normalize_for_hash(self, code: str) -> str:
        """Strip comments and normalize whitespace for stable AST hash."""
        no_block_comments = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        no_line_comments = re.sub(r"//.*", "", no_block_comments)
        normalized = " ".join(no_line_comments.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
