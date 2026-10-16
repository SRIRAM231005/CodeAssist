from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .base import FunctionInfo, CallInfo, LanguageAdapter


@dataclass
class ResolutionResult:
    """Outcome of resolving a function call to a target function."""
    status: str                         # "resolved", "ambiguous", "unresolved"
    target: Optional[FunctionInfo] = None
    candidates: List[FunctionInfo] = field(default_factory=list)
    reason: Optional[str] = None


class ProjectSymbolIndex:
    """
    Project-wide symbol table for inter-procedural call resolution across files.
    Enforces C/C++ linker and Python module visibility rules:
    - 'static' functions are strictly file-local.
    - Ambiguous symbols surface all candidate targets.
    - Virtual calls and function pointers are marked unresolved.
    """

    def __init__(self):
        # Maps unqualified name -> list of FunctionInfo
        self.by_name: Dict[str, List[FunctionInfo]] = {}
        # Maps file_path -> list of FunctionInfo
        self.by_file: Dict[str, List[FunctionInfo]] = {}

    def index_functions(self, file_path: str, functions: List[FunctionInfo]):
        """Index a list of pre-extracted functions for a file."""
        self.by_file[file_path] = functions
        for fn in functions:
            fn.file_path = file_path
            # Index by full qualified name
            self.by_name.setdefault(fn.name, []).append(fn)

            # Also index by unqualified suffix (e.g. "add" for "Math::Calc::add" or "MyClass.add")
            unqualified = fn.name.split("::")[-1].split(".")[-1]
            if unqualified != fn.name:
                self.by_name.setdefault(unqualified, []).append(fn)

    def index_file(self, file_path: str, source: str, adapter: LanguageAdapter):
        """Extract and index all functions from a file."""
        functions = adapter.extract_functions(source, file_path=file_path)
        self.index_functions(file_path, functions)

    def resolve_call(
        self,
        caller_file: str,
        callee_name: str,
        call_info: Optional[CallInfo] = None,
    ) -> ResolutionResult:
        """
        Resolve a callee symbol from a caller file context.
        """
        # 1. Check if explicitly marked unresolved (function pointers, virtual dispatch)
        if call_info and not call_info.is_resolved:
            return ResolutionResult(
                status="unresolved",
                reason=f"Call `{callee_name}` uses {call_info.call_type} dispatch (unresolved)",
            )

        candidates = self.by_name.get(callee_name, [])
        if not candidates:
            # Fallback check for case-insensitive or stripped suffix
            clean = callee_name.rstrip("()").strip()
            candidates = self.by_name.get(clean, [])

        if not candidates:
            return ResolutionResult(
                status="unresolved",
                reason=f"Symbol `{callee_name}` not found in project symbol index",
            )

        # 2. Enforce static / file-local visibility:
        # A static function is visible ONLY within its own definition file.
        visible_candidates: List[FunctionInfo] = []
        local_static_candidate: Optional[FunctionInfo] = None

        for cand in candidates:
            if cand.is_static:
                if cand.file_path == caller_file:
                    local_static_candidate = cand
                    visible_candidates.append(cand)
                # Static in other files is ignored
            else:
                visible_candidates.append(cand)

        # Prioritize local static definition if present in caller file
        if local_static_candidate:
            return ResolutionResult(
                status="resolved",
                target=local_static_candidate,
                candidates=[local_static_candidate],
            )

        if len(visible_candidates) == 1:
            return ResolutionResult(
                status="resolved",
                target=visible_candidates[0],
                candidates=visible_candidates,
            )

        if len(visible_candidates) > 1:
            # Check if there is an exact file-local non-static match first
            local_matches = [c for c in visible_candidates if c.file_path == caller_file]
            if len(local_matches) == 1:
                return ResolutionResult(
                    status="resolved",
                    target=local_matches[0],
                    candidates=visible_candidates,
                )

            return ResolutionResult(
                status="ambiguous",
                candidates=visible_candidates,
                reason=f"Symbol `{callee_name}` has multiple candidate targets",
            )

        return ResolutionResult(
            status="unresolved",
            reason=f"No visible candidate for `{callee_name}` in `{caller_file}`",
        )
