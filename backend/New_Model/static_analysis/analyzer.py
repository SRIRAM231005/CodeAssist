from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any

from ..cfg.model import CFG
from .liveness import LivenessAnalyzer, StaticFinding
from .reaching_defs import ReachingDefinitionsAnalyzer, Definition
from .dominators import DominatorTree
from .var_extractor import extract_statement_vars


@dataclass
class StaticAnalysisReport:
    """Consolidated static compiler analysis report for a function."""
    function_name: str
    findings: List[StaticFinding] = field(default_factory=list)
    phi_placements: Dict[str, List[int]] = field(default_factory=dict)
    def_use_chains: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    live_in_per_block: Dict[int, List[str]] = field(default_factory=dict)
    idom_map: Dict[int, Optional[int]] = field(default_factory=dict)

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary suitable for inclusion in LLM prompt and API output."""
        return {
            "function_name": self.function_name,
            "total_findings": len(self.findings),
            "findings": [
                {
                    "type": f.finding_type,
                    "variable": f.variable,
                    "line": f.line,
                    "block": f.block_id,
                    "severity": f.severity,
                    "message": f.message,
                }
                for f in self.findings
            ],
            "ssa_phi_nodes": {
                var: [f"B{bid}" for bid in bids]
                for var, bids in self.phi_placements.items()
                if bids
            },
            "uninitialized_vars": [
                f.variable for f in self.findings if f.finding_type == "uninitialized" and f.variable
            ],
            "dead_stores": [
                {"var": f.variable, "line": f.line, "block": f.block_id}
                for f in self.findings if f.finding_type == "dead_store" and f.variable
            ],
        }


def run_static_analysis(
    cfg: CFG,
    parameter_names: Optional[Set[str]] = None,
) -> StaticAnalysisReport:
    """
    Run complete compiler analysis suite on a CFG:
    1. Liveness Analysis & Dead Store / Uninitialized Detection
    2. Reaching Definitions & Def-Use Chains
    3. Dominator Tree & SSA Phi Placement
    """
    params = parameter_names if parameter_names is not None else set()

    # 1. Liveness Analysis
    liveness = LivenessAnalyzer(cfg, parameter_names=params)
    live_in, _, findings = liveness.analyze()

    # 2. Reaching Definitions
    reaching = ReachingDefinitionsAnalyzer(cfg)
    _, _, def_use = reaching.analyze()

    # Format def-use chains for serialization
    formatted_def_use: Dict[str, List[Dict[str, Any]]] = {}
    for (var, use_line), defs in def_use.items():
        formatted_def_use.setdefault(var, []).append({
            "use_line": use_line,
            "reaching_defs": [{"line": d.line, "block": d.block_id} for d in defs],
        })

    # 3. Dominator Tree & SSA Phi Placement
    dom_tree = DominatorTree(cfg)
    dom_tree.analyze()

    # Collect def blocks per variable for SSA phi placement
    def_blocks: Dict[str, Set[int]] = {}
    for bid, block in cfg.blocks.items():
        for s in block.statements:
            d_set, _ = extract_statement_vars(s.text, s.ast_node)
            for var in d_set:
                def_blocks.setdefault(var, set()).add(bid)

    phi_placements = dom_tree.compute_phi_placements(def_blocks)

    return StaticAnalysisReport(
        function_name=cfg.function_name,
        findings=findings,
        phi_placements={var: sorted(list(bids)) for var, bids in phi_placements.items() if bids},
        def_use_chains=formatted_def_use,
        live_in_per_block={bid: sorted(list(vars_set)) for bid, vars_set in live_in.items()},
        idom_map=dom_tree.idom,
    )
