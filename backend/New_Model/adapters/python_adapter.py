import ast
import hashlib
from typing import List, Optional

from .base import LanguageAdapter, FunctionInfo, CallInfo, LooseChunk


class PythonAdapter(LanguageAdapter):
    """Python language adapter using Python's standard `ast` module."""

    @property
    def language_name(self) -> str:
        return "python"

    def _build_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Construct a readable parameter signature from AST."""
        params = []
        for arg in node.args.args:
            name = arg.arg
            if arg.annotation:
                anno = ast.unparse(arg.annotation) if hasattr(ast, "unparse") else ""
                params.append(f"{name}: {anno}" if anno else name)
            else:
                params.append(name)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        for arg in node.args.kwonlyargs:
            name = arg.arg
            if arg.annotation and hasattr(ast, "unparse"):
                params.append(f"{name}: {ast.unparse(arg.annotation)}")
            else:
                params.append(name)
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        ret = ""
        if node.returns and hasattr(ast, "unparse"):
            ret = f" -> {ast.unparse(node.returns)}"
        return f"({', '.join(params)}){ret}"

    def extract_functions(self, source: str, file_path: str = "") -> List[FunctionInfo]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        functions: List[FunctionInfo] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions (not enclosed in classes)
                # To distinguish top-level vs method, we can check node parents or check class definitions
                pass

        # Use an explicit visitor or direct tree scan to maintain class nesting context
        class FunctionCollector(ast.NodeVisitor):
            def __init__(self):
                self.results: List[FunctionInfo] = []
                self.class_stack: List[str] = []

            def visit_ClassDef(self, node: ast.ClassDef):
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._record(node)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._record(node)
                self.generic_visit(node)

            def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
                if self.class_stack:
                    qualified_name = f"{'.'.join(self.class_stack)}.{node.name}"
                else:
                    qualified_name = node.name

                start_line = node.lineno
                end_line = getattr(node, "end_lineno", node.lineno)
                code_segment = ast.get_source_segment(source, node)
                if not code_segment:
                    lines = source.splitlines()
                    code_segment = "\n".join(lines[start_line - 1 : end_line])

                docstring = ast.get_docstring(node)
                sig = self._build_sig(node)

                # Check if decorated with @staticmethod
                is_static = any(
                    (isinstance(d, ast.Name) and d.id == "staticmethod")
                    for d in node.decorator_list
                )

                self.results.append(
                    FunctionInfo(
                        name=qualified_name,
                        signature=sig,
                        span=(start_line, end_line),
                        code=code_segment,
                        is_static=is_static,
                        docstring=docstring,
                        file_path=file_path,
                    )
                )

            def _build_sig(self, node):
                params = [a.arg for a in node.args.args]
                return f"({', '.join(params)})"

        collector = FunctionCollector()
        collector.visit(tree)
        return collector.results

    def extract_calls(self, function_code: str, file_path: str = "") -> List[CallInfo]:
        try:
            tree = ast.parse(function_code)
        except SyntaxError:
            return []

        calls: List[CallInfo] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                line = getattr(node, "lineno", None)
                if isinstance(node.func, ast.Name):
                    calls.append(CallInfo(callee_name=node.func.id, call_type="direct", is_resolved=True, line=line))
                elif isinstance(node.func, ast.Attribute):
                    # Method call: e.g. obj.method() or Math.calc()
                    calls.append(CallInfo(callee_name=node.func.attr, call_type="method", is_resolved=True, line=line))
                else:
                    # Indirect / function pointer style: e.g. func_list[0]() or get_fn()()
                    callee_str = ast.unparse(node.func) if hasattr(ast, "unparse") else "expr"
                    calls.append(CallInfo(callee_name=callee_str, call_type="function_pointer", is_resolved=False, line=line))
        return calls

    def extract_loose_chunks(self, source: str, file_path: str = "") -> List[LooseChunk]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        chunks: List[LooseChunk] = []
        lines = source.splitlines()

        for node in tree.body:
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno)
            code_segment = ast.get_source_segment(source, node)
            if not code_segment:
                code_segment = "\n".join(lines[start_line - 1 : end_line])

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                chunks.append(LooseChunk(chunk_type="import", name=", ".join(names), span=(start_line, end_line), code=code_segment))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            targets.append(t.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    targets.append(node.target.id)
                name = ", ".join(targets) if targets else "global_assign"
                chunks.append(LooseChunk(chunk_type="global_var", name=name, span=(start_line, end_line), code=code_segment))

        return chunks

    def normalize_for_hash(self, code: str) -> str:
        """Strip comments/formatting for stable hash."""
        try:
            tree = ast.parse(code)
            dumped = ast.dump(tree, include_attributes=False)
            return hashlib.sha256(dumped.encode("utf-8")).hexdigest()
        except Exception:
            clean = "".join(code.split())
            return hashlib.sha256(clean.encode("utf-8")).hexdigest()
