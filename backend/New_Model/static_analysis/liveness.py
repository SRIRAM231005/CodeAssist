from dataclasses import dataclass
from typing import Dict, List, Set, Optional
from ..cfg.model import CFG, BasicBlock
from .worklist import WorklistSolver
from .var_extractor import extract_statement_vars


@dataclass
class StaticFinding:
    """A compiler static analysis diagnostic."""
    finding_type: str                   # "dead_store", "unused_var", "uninitialized", "unreachable_block"
    variable: Optional[str]
    line: int
    block_id: int
    message: str
    severity: str = "warning"           # "warning" or "error"


class LivenessAnalyzer:
    """
    Computes Live Variable Analysis (backward, may) over a CFG.
    Detects:
    - Dead stores: assignments to variables that are not live after the store
    - Unused variables: variables defined but never read anywhere in the function
    - Maybe-uninitialized: non-parameter variables live at function entry
    """

    def __init__(self, cfg: CFG, parameter_names: Optional[Set[str]] = None):
        self.cfg = cfg
        self.parameter_names: Set[str] = parameter_names if parameter_names is not None else set()
        self.gen: Dict[int, Set[str]] = {}
        self.kill: Dict[int, Set[str]] = {}
        self.in_sets: Dict[int, Set[str]] = {}
        self.out_sets: Dict[int, Set[str]] = {}
        self.findings: List[StaticFinding] = []

    def _compute_block_gen_kill(self):
        """
        Compute GEN and KILL sets for each basic block.
        GEN[B]: variables used before being defined in B.
        KILL[B]: variables defined in B.
        """
        for bid, block in self.cfg.blocks.items():
            b_gen: Set[str] = set()
            b_kill: Set[str] = set()

            for s in block.statements:
                defs, uses = extract_statement_vars(s.text, s.ast_node)
                # Any use before def in this block is generated
                for u in uses:
                    if u not in b_kill:
                        b_gen.add(u)
                # Defs are added to kill
                for d in defs:
                    b_kill.add(d)

            self.gen[bid] = b_gen
            self.kill[bid] = b_kill

    def analyze(self) -> tuple[Dict[int, Set[str]], Dict[int, Set[str]], List[StaticFinding]]:
        self._compute_block_gen_kill()

        # Transfer function: IN[B] = GEN[B] | (OUT[B] - KILL[B])
        def transfer_fn(block_id: int, out_set: Set[str]) -> Set[str]:
            return self.gen.get(block_id, set()) | (out_set - self.kill.get(block_id, set()))

        solver = WorklistSolver[str](
            cfg=self.cfg,
            direction="backward",
            join_op="union",
            boundary_val=set(),
        )

        self.in_sets, self.out_sets = solver.solve(transfer_fn=transfer_fn, initial_val=set())
        self._detect_findings()
        return self.in_sets, self.out_sets, self.findings

    def _detect_findings(self):
        self.findings = []
        all_defined_vars: Set[str] = set()
        all_used_vars: Set[str] = set()

        # 1. Statement-level backward sweep to find dead stores
        for bid, block in self.cfg.blocks.items():
            if block.is_unreachable or block.is_exit:
                continue

            live = set(self.out_sets.get(bid, set()))

            # Sweep statements in reverse order
            for s in reversed(block.statements):
                defs, uses = extract_statement_vars(s.text, s.ast_node)
                all_defined_vars.update(defs)
                all_used_vars.update(uses)

                for d in defs:
                    # If defined variable is not live immediately after the statement, it's a dead store!
                    # Exclude loop iterators/dummy variables (e.g. _)
                    if d not in live and d != "_":
                        self.findings.append(
                            StaticFinding(
                                finding_type="dead_store",
                                variable=d,
                                line=s.line,
                                block_id=bid,
                                message=f"Dead store: variable `{d}` assigned at line {s.line} is never read on any subsequent path.",
                                severity="warning",
                            )
                        )

                # Update live set: uses are now live, defs are killed
                live = uses | (live - defs)

        # 2. Maybe-uninitialized variables (live at function entry)
        entry_live = self.in_sets.get(self.cfg.entry_block_id, set())
        uninitialized = entry_live - self.parameter_names

        for var in uninitialized:
            # Find first use line
            use_line = 0
            for b in self.cfg.blocks.values():
                for s in b.statements:
                    _, uses = extract_statement_vars(s.text, s.ast_node)
                    if var in uses:
                        use_line = s.line
                        break
                if use_line:
                    break

            self.findings.append(
                StaticFinding(
                    finding_type="uninitialized",
                    variable=var,
                    line=use_line,
                    block_id=self.cfg.entry_block_id,
                    message=f"Variable `{var}` may be used uninitialized (live at function entry).",
                    severity="error",
                )
            )

        # 3. Completely unused variables
        unused = all_defined_vars - all_used_vars - self.parameter_names
        for var in unused:
            if var != "_":
                self.findings.append(
                    StaticFinding(
                        finding_type="unused_var",
                        variable=var,
                        line=0,
                        block_id=self.cfg.entry_block_id,
                        message=f"Variable `{var}` is defined but never read anywhere in the function.",
                        severity="warning",
                    )
                )

        # 4. Unreachable code blocks
        unreachable = self.cfg.get_unreachable_blocks()
        for ub in unreachable:
            start_l, end_l = ub.span
            if start_l > 0:
                self.findings.append(
                    StaticFinding(
                        finding_type="unreachable_block",
                        variable=None,
                        line=start_l,
                        block_id=ub.id,
                        message=f"Unreachable basic block `{ub.name}` (lines {start_l}-{end_l}) cannot be reached from function entry.",
                        severity="error",
                    )
                )
