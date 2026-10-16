from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple


@dataclass
class FunctionInfo:
    """Standard representation of an extracted function or method across languages."""
    name: str                           # e.g., "login", "AuthService.login", "Math::Calc::add"
    signature: str                      # e.g., "(user: str, token: str) -> bool" or "int(int, int)"
    span: Tuple[int, int]               # (start_line, end_line), 1-indexed inclusive
    code: str                           # verbatim source code of the function
    is_static: bool = False             # file-local visibility (e.g. C 'static')
    docstring: Optional[str] = None
    file_path: Optional[str] = None     # optional associated file path
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Backward-compatible dict format used throughout CodeAssist."""
        return {
            "name": self.name,
            "signature": self.signature,
            "span": self.span,
            "code": self.code,
            "is_static": self.is_static,
            "docstring": self.docstring,
            "file_path": self.file_path,
        }

    @property
    def cache_key_suffix(self) -> str:
        """Cache key suffix handling overloads: qualified_name::signature_hash."""
        sig_clean = "".join(self.signature.split())
        return f"{self.name}::{sig_clean}" if sig_clean else self.name


@dataclass
class CallInfo:
    """Details about a function call inside a function body."""
    callee_name: str                    # Name of the called function/symbol
    call_type: str = "direct"           # "direct", "method", "function_pointer", "virtual"
    is_resolved: bool = True            # False for dynamic dispatch / func pointers
    line: Optional[int] = None          # Call site line number


@dataclass
class LooseChunk:
    """Loose contextual chunks in a file: globals, macros, typedefs, structs, classes."""
    chunk_type: str                     # "macro", "struct", "class", "global_var", "typedef", "import"
    name: str
    span: Tuple[int, int]
    code: str


class LanguageAdapter(ABC):
    """Abstract interface for language-specific syntax extraction and normalization."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        pass

    @abstractmethod
    def extract_functions(self, source: str, file_path: str = "") -> List[FunctionInfo]:
        """Extract all functions and methods with qualified names, signatures, and spans."""
        pass

    @abstractmethod
    def extract_calls(self, function_code: str, file_path: str = "") -> List[CallInfo]:
        """Extract function/method calls from a function's code."""
        pass

    @abstractmethod
    def extract_loose_chunks(self, source: str, file_path: str = "") -> List[LooseChunk]:
        """Extract globals, macros, structs, and module-level contextual chunks."""
        pass

    @abstractmethod
    def normalize_for_hash(self, code: str) -> str:
        """Strip whitespace, formatting, and comments for a stable AST hash."""
        pass
