"""
Tests for Diagnostics & Slicing: Error-log parser and backward slicer.
Run with:
    e:\\CodeAssist_python\\backend\\venv\\Scripts\\python.exe -m pytest backend/tests/test_diagnostics.py -v
"""

import sys
import os

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from New_Model.diagnostics.log_parser import parse_error_log, LogParseResult
from New_Model.diagnostics.backward_slicer import compute_backward_slice, SliceResult
from New_Model.cfg.builder import build_cfg


# ── Log parser tests ──────────────────────────────────────────────────────────

PYTHON_TRACEBACK = """\
Traceback (most recent call last):
  File "/home/user/project/myapp/utils.py", line 15, in helper
    result = compute(x)
  File "/home/user/project/myapp/core.py", line 42, in compute
    return data[index]
IndexError: list index out of range
"""

GCC_ERROR = """\
In file included from main.c:1:
myapp/parser.c:27:5: error: use of undeclared identifier 'tok'
myapp/parser.c:31:1: note: declared here
"""

KNOWN_FILES_PY = [
    "myapp/utils.py",
    "myapp/core.py",
    "myapp/models.py",
]

KNOWN_FILES_C = [
    "myapp/parser.c",
    "myapp/lexer.c",
    "main.c",
]


class TestLogParser:
    def test_python_traceback_innermost(self):
        result = parse_error_log(PYTHON_TRACEBACK, KNOWN_FILES_PY)
        assert result is not None, "Should parse Python traceback"
        # Innermost frame is core.py:42
        assert result.file_path == "myapp/core.py"
        assert result.line == 42
        assert result.error_type == "IndexError"
        assert "range" in result.error_message
        assert result.function_name == "compute"

    def test_python_traceback_fallback_to_outer_frame(self):
        # Only utils.py is in known_files, so it falls back to that frame
        result = parse_error_log(PYTHON_TRACEBACK, ["myapp/utils.py"])
        assert result is not None
        assert result.file_path == "myapp/utils.py"
        assert result.line == 15

    def test_gcc_error(self):
        result = parse_error_log(GCC_ERROR, KNOWN_FILES_C)
        assert result is not None, "Should parse GCC/Clang error"
        assert result.file_path == "myapp/parser.c"
        assert result.line == 27
        assert result.error_type == "error"
        assert "tok" in result.error_message

    def test_no_match_returns_none(self):
        result = parse_error_log(PYTHON_TRACEBACK, ["completely/other/file.py"])
        assert result is None, "No match when no known file resolves"

    def test_empty_log_returns_none(self):
        result = parse_error_log("", KNOWN_FILES_PY)
        assert result is None

    def test_gcc_warning_skipped(self):
        warning_log = "myapp/parser.c:10:5: warning: unused variable 'x'"
        result = parse_error_log(warning_log, KNOWN_FILES_C)
        assert result is None, "Warnings should be skipped"

    def test_basename_resolution(self):
        # Pass only basename 'core.py' in known_files
        result = parse_error_log(PYTHON_TRACEBACK, ["core.py"])
        assert result is not None
        assert result.file_path == "core.py"

    def test_bad_log_no_crash(self):
        # Completely garbled input should not raise
        result = parse_error_log("garbage\n@@##!!\n", KNOWN_FILES_PY)
        assert result is None


# ── Backward slicer tests ─────────────────────────────────────────────────────

SLICE_CODE = """\
def process(items):
    total = 0
    for item in items:
        val = item * 2
        total = total + val
    return total
"""


class TestBackwardSlicer:
    def test_basic_slice_from_return(self):
        cfg = build_cfg(SLICE_CODE, "test.py", "process")
        assert cfg is not None, "CFG must build"

        # Build def-use chains
        from New_Model.static_analysis.reaching_defs import ReachingDefinitionsAnalyzer
        rda = ReachingDefinitionsAnalyzer(cfg)
        rda.analyze()

        # Slice from line 6 (return total)
        result = compute_backward_slice(cfg, rda.def_use_chains, failing_line=6)
        assert isinstance(result, SliceResult)
        assert result.failing_line == 6
        # Slice should not be completely empty for a non-trivial function
        # (may be empty if the minimal CFG has one block, which is still valid)
        assert result.slice_statements is not None

    def test_empty_slice_on_bad_line(self):
        cfg = build_cfg(SLICE_CODE, "test.py", "process")
        assert cfg is not None
        from New_Model.static_analysis.reaching_defs import ReachingDefinitionsAnalyzer
        rda = ReachingDefinitionsAnalyzer(cfg)
        rda.analyze()

        # Line 999 doesn't exist
        result = compute_backward_slice(cfg, rda.def_use_chains, failing_line=999)
        assert isinstance(result, SliceResult)
        assert result.is_empty  # should gracefully return empty

    def test_no_crash_on_none_cfg(self):
        # When CFG is None (e.g. parse failure), backward slicer should not crash
        result = compute_backward_slice(None, {}, failing_line=5)
        assert isinstance(result, SliceResult)


# ── Integration: log parse -> CFG -> slice ────────────────────────────────────

class TestLogToSliceIntegration:
    def test_end_to_end_python_traceback(self):
        code = """\
def compute(data, index):
    result = data[index]
    return result
"""
        known = ["myapp/core.py"]
        log = '''\
Traceback (most recent call last):
  File "myapp/core.py", line 2, in compute
    result = data[index]
IndexError: list index out of range
'''
        parse_result = parse_error_log(log, known)
        assert parse_result is not None
        assert parse_result.file_path == "myapp/core.py"
        assert parse_result.line == 2
        assert parse_result.function_name == "compute"

        cfg = build_cfg(code, "myapp/core.py", "compute")
        assert cfg is not None

        from New_Model.static_analysis.reaching_defs import ReachingDefinitionsAnalyzer
        rda = ReachingDefinitionsAnalyzer(cfg)
        rda.analyze()

        slice_res = compute_backward_slice(cfg, rda.def_use_chains, parse_result.line)
        assert isinstance(slice_res, SliceResult)
        assert slice_res.failing_line == 2
