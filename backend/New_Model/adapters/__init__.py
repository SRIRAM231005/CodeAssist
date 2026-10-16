import os
from .base import LanguageAdapter, FunctionInfo, CallInfo, LooseChunk
from .python_adapter import PythonAdapter
from .c_adapter import CAdapter
from .cpp_adapter import CppAdapter
from .symbol_index import ProjectSymbolIndex, ResolutionResult

_PYTHON_ADAPTER = PythonAdapter()
_C_ADAPTER = CAdapter()
_CPP_ADAPTER = CppAdapter()


def get_adapter(file_path: str) -> LanguageAdapter:
    """
    Factory to return the appropriate LanguageAdapter based on file extension.
    Defaults to PythonAdapter for backward compatibility.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".c", ".h"):
        return _C_ADAPTER
    elif ext in (".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh"):
        return _CPP_ADAPTER
    elif ext in (".py", ".pyw"):
        return _PYTHON_ADAPTER
    else:
        # Default fallback to PythonAdapter
        return _PYTHON_ADAPTER


__all__ = [
    "LanguageAdapter",
    "FunctionInfo",
    "CallInfo",
    "LooseChunk",
    "PythonAdapter",
    "CAdapter",
    "CppAdapter",
    "ProjectSymbolIndex",
    "ResolutionResult",
    "get_adapter",
]
