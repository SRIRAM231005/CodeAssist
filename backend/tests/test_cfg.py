import os
import sys

# Ensure backend root is on sys.path
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from New_Model.cfg import build_cfg, EdgeType


def test_python_cfg_branch_and_loop():
    py_code = """
def compute(x):
    y = 0
    if x > 10:
        y = x * 2
    else:
        y = x + 2
    while y > 0:
        y -= 1
        if y == 5:
            break
        elif y == 3:
            continue
    return y
    dead_code = 999  # Unreachable!
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="compute")
    assert cfg.function_name == "compute"
    assert len(cfg.blocks) > 0

    # Verify edge types present
    edge_types = {e.edge_type for e in cfg.edges}
    assert EdgeType.BRANCH_TRUE in edge_types
    assert EdgeType.BRANCH_FALSE in edge_types
    assert EdgeType.LOOP_BACK in edge_types
    assert EdgeType.BREAK in edge_types
    assert EdgeType.CONTINUE in edge_types
    assert EdgeType.RETURN in edge_types

    # Verify unreachable block detection
    unreachable = cfg.get_unreachable_blocks()
    assert len(unreachable) >= 1
    assert any("dead_code" in s.text for b in unreachable for s in b.statements)

    # Verify successors and predecessors
    entry_succs = cfg.successors(cfg.entry_block_id)
    assert len(entry_succs) > 0

    mermaid = cfg.to_mermaid()
    assert "graph TD" in mermaid
    assert "classDef unreachable" in mermaid

    d = cfg.to_dict()
    assert d["unreachable_count"] >= 1
    print("[PASS] Python CFG branch, loop, break, continue, and dead code passed!")


def test_python_cfg_try_except():
    py_code = """
def safe_divide(a, b):
    try:
        res = a / b
    except ZeroDivisionError:
        res = 0
    finally:
        log_cleanup()
    return res
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="safe_divide")
    edge_types = {e.edge_type for e in cfg.edges}
    assert EdgeType.EXCEPTION in edge_types
    print("[PASS] Python CFG try/except/finally passed!")


def test_c_cfg_branch_loops_and_dead_code():
    c_code = """
int evaluate(int x) {
    int val = 0;
    if (x > 0) {
        val = 10;
    } else {
        val = 20;
    }
    do {
        val--;
    } while (val > 15);

    for (int i = 0; i < 5; i++) {
        val += i;
    }
    return val;
    int dead = 42;
}
"""
    cfg = build_cfg(c_code, file_path="eval.c", function_name="evaluate")
    edge_types = {e.edge_type for e in cfg.edges}
    assert EdgeType.BRANCH_TRUE in edge_types
    assert EdgeType.BRANCH_FALSE in edge_types
    assert EdgeType.LOOP_BACK in edge_types
    assert EdgeType.RETURN in edge_types

    unreachable = cfg.get_unreachable_blocks()
    assert len(unreachable) >= 1
    assert any("dead" in s.text for b in unreachable for s in b.statements)

    print("[PASS] C CFG branch, do-while, for, and dead code passed!")


def test_cpp_cfg_switch_and_goto():
    cpp_code = """
int dispatch(int op, int val) {
    switch (op) {
        case 1:
            val += 10;
            break;
        case 2:
            goto fallback;
        default:
            val = 0;
            break;
    }
    return val;

fallback:
    val = -1;
    return val;
}
"""
    cfg = build_cfg(cpp_code, file_path="dispatch.cpp", function_name="dispatch")
    edge_types = {e.edge_type for e in cfg.edges}
    assert EdgeType.SWITCH_CASE in edge_types
    assert EdgeType.GOTO in edge_types
    assert EdgeType.RETURN in edge_types

    d = cfg.to_dict()
    assert len(d["blocks"]) >= 5
    print("[PASS] C++ CFG switch and goto passed!")


if __name__ == "__main__":
    test_python_cfg_branch_and_loop()
    test_python_cfg_try_except()
    test_c_cfg_branch_loops_and_dead_code()
    test_cpp_cfg_switch_and_goto()
    print("\nALL STAGE 2 CFG TESTS PASSED SUCCESSFULLY!")
