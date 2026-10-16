from typing import Optional
from ..adapters import get_adapter
from .model import CFG, BasicBlock, CFGEdge, EdgeType, CFGStatement
from .python_cfg import PythonCFGBuilder
from .tree_sitter_cfg import TreeSitterCFGBuilder


def build_cfg(
    code: str,
    file_path: str = "",
    function_name: Optional[str] = None,
) -> CFG:
    """
    Unified entry point to construct a Control Flow Graph from code
    for Python, C, or C++.
    """
    adapter = get_adapter(file_path)
    lang = adapter.language_name

    if lang == "c":
        builder = TreeSitterCFGBuilder(language="c", function_name=function_name or "c_func")
        return builder.build_from_code(code, target_function_name=function_name)
    elif lang == "cpp":
        builder = TreeSitterCFGBuilder(language="cpp", function_name=function_name or "cpp_func")
        return builder.build_from_code(code, target_function_name=function_name)
    else:
        builder = PythonCFGBuilder(function_name=function_name or "py_func")
        return builder.build_from_code(code, target_function_name=function_name)
