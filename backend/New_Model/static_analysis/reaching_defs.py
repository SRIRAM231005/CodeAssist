from dataclasses import dataclass
from typing import Dict, List, Set, Tuple
from ..cfg.model import CFG
from .worklist import WorklistSolver
from .var_extractor import extract_statement_vars


@dataclass(frozen=True)
class Definition:
    """A definition site of a variable."""
    variable: str
    line: int
    block_id: int


class ReachingDefinitionsAnalyzer:
    """
    Computes Reaching Definitions (forward, may) and builds Def-Use chains.
    IN[B] = Union_{P in pred(B)} OUT[P]
    OUT[B] = GEN[B] | (IN[B] - KILL[B])
    """

    def __init__(self, cfg: CFG):
        self.cfg = cfg
        self.all_definitions: Set[Definition] = set()
        self.gen: Dict[int, Set[Definition]] = {}
        self.kill: Dict[int, Set[Definition]] = {}
        self.in_sets: Dict[int, Set[Definition]] = {}
        self.out_sets: Dict[int, Set[Definition]] = {}
        # Map: (variable, use_line) -> Set of reaching Definition objects
        self.def_use_chains: Dict[Tuple[str, int], Set[Definition]] = {}

    def _collect_definitions_and_gen_kill(self):
        # 1. Collect all definitions across the function
        block_defs_ordered: Dict[int, List[Definition]] = {}

        for bid, block in self.cfg.blocks.items():
            b_list: List[Definition] = []
            for s in block.statements:
                defs, _ = extract_statement_vars(s.text, s.ast_node)
                for d in defs:
                    defn = Definition(variable=d, line=s.line, block_id=bid)
                    self.all_definitions.add(defn)
                    b_list.append(defn)
            block_defs_ordered[bid] = b_list

        # 2. Compute GEN and KILL per block
        for bid, block in self.cfg.blocks.items():
            b_gen: Set[Definition] = set()
            b_vars: Set[str] = set()

            # The last definition for each variable in the block is in GEN
            for defn in reversed(block_defs_ordered.get(bid, [])):
                if defn.variable not in b_vars:
                    b_gen.add(defn)
                    b_vars.add(defn.variable)

            # KILL contains all other definitions of the same variables created elsewhere
            b_kill = {
                d for d in self.all_definitions
                if d.variable in b_vars and d not in b_gen
            }

            self.gen[bid] = b_gen
            self.kill[bid] = b_kill

    def analyze(self) -> tuple[Dict[int, Set[Definition]], Dict[int, Set[Definition]], Dict[Tuple[str, int], Set[Definition]]]:
        self._collect_definitions_and_gen_kill()

        def transfer_fn(block_id: int, in_set: Set[Definition]) -> Set[Definition]:
            return self.gen.get(block_id, set()) | (in_set - self.kill.get(block_id, set()))

        solver = WorklistSolver[Definition](
            cfg=self.cfg,
            direction="forward",
            join_op="union",
            boundary_val=set(),
        )

        self.in_sets, self.out_sets = solver.solve(transfer_fn=transfer_fn, initial_val=set())
        self._build_def_use_chains()
        return self.in_sets, self.out_sets, self.def_use_chains

    def _build_def_use_chains(self):
        """
        Simulate statement-by-statement execution within each block to map
        each variable use to the definitions reaching that use.
        """
        self.def_use_chains = {}

        for bid, block in self.cfg.blocks.items():
            current_reaching = set(self.in_sets.get(bid, set()))

            for s in block.statements:
                defs, uses = extract_statement_vars(s.text, s.ast_node)

                # For each used variable, find all definitions of that variable currently reaching
                for u in uses:
                    reaching_u = {d for d in current_reaching if d.variable == u}
                    self.def_use_chains[(u, s.line)] = reaching_u

                # Apply statement-level transfer to reaching definitions
                if defs:
                    # Kill previous definitions of these variables
                    current_reaching = {d for d in current_reaching if d.variable not in defs}
                    # Gen current definitions
                    for d in defs:
                        current_reaching.add(Definition(variable=d, line=s.line, block_id=bid))
