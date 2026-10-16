"""
Stage 4 – Error-log parser.

Supported formats:
  1. Python traceback  – lines matching: File "path", line N, in func_name
  2. GCC / Clang       – lines matching: path:line:col: error: message

Returns a LogParseResult with the resolved repo file, line number,
error type and message.  Returns None when nothing useful is found.
All code is wrapped in try/except so failures fall back to the existing flow.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, List


# ── Compiled patterns ─────────────────────────────────────────────────────────

# Python traceback frame:  File "path/to/file.py", line 42, in func_name
_PY_FRAME = re.compile(
    r'^\s*File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+(\S+)',
    re.MULTILINE,
)

# Python exception type line (appears right after the last frame):
#   SomeError: message text
_PY_EXC = re.compile(r'^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*Error[^\n]*)', re.MULTILINE)

# GCC / Clang diagnostic:
#   /path/to/file.cpp:12:34: error: some message
#   C:\path\file.c:12:34: error: message   (Windows path variant)
_GCC_LINE = re.compile(
    r'([A-Za-z]?:?[^\n:]+\.(?:c|cpp|cc|cxx|h|hpp|py)):(\d+)(?::\d+)?:\s*(error|fatal\s+error|warning):\s*(.+)',
    re.IGNORECASE,
)

# Library/venv frame indicators to skip
_SKIP_PATH_PARTS = ("site-packages", "lib/python", "lib\\python", "<frozen", "<string", "venv")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class LogParseResult:
    file_path: str          # resolved repo-relative path (matched against known_files)
    line: int               # 1-indexed line where the error occurred
    error_type: str         # e.g. "AttributeError" or "error"
    error_message: str      # the rest of the diagnostic text
    function_name: Optional[str] = None   # from traceback frame, if available


# ── Public API ────────────────────────────────────────────────────────────────

def parse_error_log(
    error_log: str,
    known_files: List[str],
) -> Optional[LogParseResult]:
    """
    Parse *error_log* and return a LogParseResult for the innermost/first
    repo-owned frame, or None if nothing resolves.

    *known_files* is the list of repo file paths already indexed
    (as returned by adapter.extract_functions or the project symbol index).
    """
    try:
        result = _try_python_traceback(error_log, known_files)
        if result is not None:
            return result

        result = _try_gcc_clang(error_log, known_files)
        return result
    except Exception:
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resolve_path(candidate: str, known_files: List[str]) -> Optional[str]:
    """
    Try to match *candidate* against *known_files* by:
      1. Exact match
      2. Basename match
      3. Suffix match (candidate is a suffix of a known file path)
    Returns the first matching known file path, or None.
    """
    try:
        # Normalise separators so both / and \\ work
        cand_norm = candidate.replace("\\", "/")

        # 1. Exact
        for kf in known_files:
            kf_norm = kf.replace("\\", "/")
            if kf_norm == cand_norm:
                return kf

        cand_base = os.path.basename(cand_norm)

        # 2. Basename
        matches = [kf for kf in known_files if os.path.basename(kf.replace("\\", "/")) == cand_base]
        if len(matches) == 1:
            return matches[0]

        # 3. Suffix — candidate is a trailing part of a known file path
        suffix_matches = [kf for kf in known_files if kf.replace("\\", "/").endswith(cand_norm)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        # Multiple hits → ambiguous, skip
        return None
    except Exception:
        return None


def _is_library_frame(path: str) -> bool:
    """Return True if the path looks like a library / venv frame to skip."""
    for part in _SKIP_PATH_PARTS:
        if part in path:
            return True
    return False


def _try_python_traceback(log: str, known_files: List[str]) -> Optional[LogParseResult]:
    """
    Handle Python tracebacks.  Walk frames from innermost outward (last to first)
    and return the first frame whose file resolves to a repo file.
    """
    frames = _PY_FRAME.findall(log)          # [(path, line, func), ...]
    if not frames:
        return None

    # Extract the exception type / message (last occurrence of ExcType: msg)
    exc_match = None
    for m in _PY_EXC.finditer(log):
        exc_match = m
    error_type = "Exception"
    error_message = ""
    if exc_match:
        parts = exc_match.group(1).split(":", 1)
        error_type = parts[0].strip()
        error_message = parts[1].strip() if len(parts) > 1 else ""

    # Innermost frame = last in list; walk backwards
    for path, lineno_str, func_name in reversed(frames):
        if _is_library_frame(path):
            continue
        resolved = _resolve_path(path, known_files)
        if resolved is not None:
            return LogParseResult(
                file_path=resolved,
                line=int(lineno_str),
                error_type=error_type,
                error_message=error_message,
                function_name=func_name if func_name not in ("<module>", "") else None,
            )

    return None


def _try_gcc_clang(log: str, known_files: List[str]) -> Optional[LogParseResult]:
    """
    Handle GCC / Clang diagnostics.  Use the first error line whose file
    resolves to a repo file.
    """
    for m in _GCC_LINE.finditer(log):
        path, lineno_str, level, message = m.group(1), m.group(2), m.group(3), m.group(4)
        if "warning" in level.lower():
            continue   # skip warnings, only act on errors
        resolved = _resolve_path(path.strip(), known_files)
        if resolved is not None:
            return LogParseResult(
                file_path=resolved,
                line=int(lineno_str),
                error_type="error",
                error_message=message.strip(),
                function_name=None,
            )

    return None
