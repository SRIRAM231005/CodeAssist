import os
import sys

# Ensure backend root is on sys.path
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from New_Model.cfg import build_cfg
from New_Model.static_analysis import (
    run_static_analysis,
    LivenessAnalyzer,
    ReachingDefinitionsAnalyzer,
    DominatorTree,
)


def test_liveness_and_dead_store():
    py_code = """
def test_dead(x):
    a = 10         # Dead store! Overwritten below without read
    a = 20
    b = a + x
    c = 99         # Dead store! Never read
    return b
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="test_dead")
    liveness = LivenessAnalyzer(cfg, parameter_names={"x"})
    _, _, findings = liveness.analyze()

    finding_types = {f.finding_type for f in findings}
    assert "dead_store" in finding_types

    dead_vars = {f.variable for f in findings if f.finding_type == "dead_store"}
    assert "a" in dead_vars
    assert "c" in dead_vars

    print("[PASS] LivenessAnalyzer dead store detection passed!")


def test_uninitialized_var_detection():
    py_code = """
def check_init(flag):
    if flag:
        val = 42
    # If flag is false, val is uninitialized!
    return val
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="check_init")
    liveness = LivenessAnalyzer(cfg, parameter_names={"flag"})
    _, _, findings = liveness.analyze()

    uninit = [f for f in findings if f.finding_type == "uninitialized"]
    assert len(uninit) >= 1
    assert any(f.variable == "val" for f in uninit)

    print("[PASS] LivenessAnalyzer uninitialized variable detection passed!")


def test_reaching_definitions_and_def_use():
    py_code = """
def compute(x):
    if x > 0:
        val = 1
    else:
        val = 2
    res = val + 10
    return res
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="compute")
    reaching = ReachingDefinitionsAnalyzer(cfg)
    _, _, def_use = reaching.analyze()

    # The use of 'val' in 'res = val + 10' should have 2 reaching definitions (from then and else)
    val_uses = [defs for (var, line), defs in def_use.items() if var == "val"]
    assert len(val_uses) > 0
    # At least one use of 'val' has both line definitions reaching it
    assert any(len(d_set) == 2 for d_set in val_uses)

    print("[PASS] ReachingDefinitionsAnalyzer and Def-Use chains passed!")


def test_dominator_tree_and_phi_placement():
    py_code = """
def example(cond):
    if cond:
        x = 1
    else:
        x = 2
    y = x + 1
    return y
"""
    cfg = build_cfg(py_code, file_path="test.py", function_name="example")
    dom_tree = DominatorTree(cfg)
    dom_tree.analyze()

    # 1. Entry dominates everything
    entry = cfg.entry_block_id
    for b in dom_tree.reachable_blocks:
        assert entry in dom_tree.dom[b]

    # 2. Check idom
    assert dom_tree.idom[entry] is None

    # 3. Compute phi placement for variable 'x' (defined in both branches)
    # The join block must be in the iterated dominance frontier of x's def blocks!
    def_blocks = {"x": {b for b in cfg.blocks if any("x" in s.text for s in cfg.blocks[b].statements)}}
    phi_places = dom_tree.compute_phi_placements(def_blocks)

    assert "x" in phi_places
    assert len(phi_places["x"]) >= 1

    print("[PASS] DominatorTree and SSA Phi placement passed!")


def test_c_static_analysis_integration():
    c_code = """
int process(int flag, int val) {
    int dead = 100;    // Dead store!
    int res = 0;
    if (flag > 0) {
        res = val * 2;
    } else {
        res = val - 2;
    }
    return res;
}
"""
    cfg = build_cfg(c_code, file_path="proc.c", function_name="process")
    report = run_static_analysis(cfg, parameter_names={"flag", "val"})

    assert report.function_name == "process"
    summary = report.to_summary_dict()

    dead_vars = [ds["var"] for ds in summary["dead_stores"]]
    assert "dead" in dead_vars
    assert "res" in summary["ssa_phi_nodes"]

    print("[PASS] C static analysis end-to-end passed!")


if __name__ == "__main__":
    test_liveness_and_dead_store()
    test_uninitialized_var_detection()
    test_reaching_definitions_and_def_use()
    test_dominator_tree_and_phi_placement()
    test_c_static_analysis_integration()
    print("\nALL STAGE 3 DATAFLOW & DOMINATOR TESTS PASSED SUCCESSFULLY!")
