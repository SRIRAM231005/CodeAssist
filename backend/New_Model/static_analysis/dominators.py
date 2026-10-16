from collections import deque
from typing import Dict, List, Optional, Set
from ..cfg.model import CFG


class DominatorTree:
    """
    Computes Dominator Tree, Immediate Dominators (idom), Dominance Frontiers (DF),
    and SSA Phi-node placement (Cytron et al. algorithm).
    """

    def __init__(self, cfg: CFG):
        self.cfg = cfg
        self.reachable_blocks: Set[int] = set()
        self.dom: Dict[int, Set[int]] = {}                  # block_id -> set of dominator block_ids
        self.idom: Dict[int, Optional[int]] = {}            # block_id -> immediate dominator id
        self.dom_children: Dict[int, List[int]] = {}        # block_id -> blocks immediately dominated by it
        self.dominance_frontiers: Dict[int, Set[int]] = {}  # block_id -> set of block_ids in its DF

    def analyze(self):
        """Perform full dominance analysis."""
        self.reachable_blocks = self.cfg.compute_reachability()
        self._compute_dominators()
        self._compute_idom()
        self._compute_dominance_frontiers()

    def _compute_dominators(self):
        """
        Iterative dataflow algorithm for dominators:
        DOM[entry] = {entry}
        DOM[n] = {n} | Intersect_{p in pred(n)} DOM[p]
        """
        entry = self.cfg.entry_block_id
        nodes = self.reachable_blocks

        self.dom = {b: set(nodes) for b in nodes}
        if entry in self.dom:
            self.dom[entry] = {entry}

        changed = True
        while changed:
            changed = False
            for b in nodes:
                if b == entry:
                    continue
                preds = [p for p in self.cfg.predecessors(b) if p in nodes]
                if preds:
                    new_dom = {b} | set.intersection(*(self.dom[p] for p in preds))
                else:
                    new_dom = {b}

                if new_dom != self.dom[b]:
                    self.dom[b] = new_dom
                    changed = True

    def _compute_idom(self):
        """
        Compute Immediate Dominator (idom) for each reachable node.
        idom(n) is the unique strict dominator d that does not dominate any other strict dominator of n.
        """
        entry = self.cfg.entry_block_id
        self.idom = {b: None for b in self.reachable_blocks}
        self.dom_children = {b: [] for b in self.reachable_blocks}

        for n in self.reachable_blocks:
            if n == entry:
                continue

            strict_doms = self.dom[n] - {n}
            # idom is the strict dominator with the maximum number of dominators
            # (i.e. deepest in the dominator tree)
            if strict_doms:
                idom_node = max(strict_doms, key=lambda d: len(self.dom[d]))
                self.idom[n] = idom_node
                self.dom_children[idom_node].append(n)

    def _compute_dominance_frontiers(self):
        """
        Compute Dominance Frontier DF[X] using the standard efficient algorithm:
        For each node Y with |pred(Y)| >= 2:
          For each P in pred(Y):
            runner = P
            while runner != idom[Y]:
              DF[runner].add(Y)
              runner = idom[runner]
        """
        self.dominance_frontiers = {b: set() for b in self.reachable_blocks}

        for y in self.reachable_blocks:
            preds = [p for p in self.cfg.predecessors(y) if p in self.reachable_blocks]
            if len(preds) >= 2:
                y_idom = self.idom.get(y)
                for p in preds:
                    runner: Optional[int] = p
                    while runner is not None and runner != y_idom:
                        self.dominance_frontiers[runner].add(y)
                        runner = self.idom.get(runner)

    def compute_phi_placements(self, def_blocks_per_var: Dict[str, Set[int]]) -> Dict[str, Set[int]]:
        """
        Compute minimal SSA Phi-node placement using Iterated Dominance Frontiers (IDF).
        For each variable v, returns the set of block IDs requiring a phi-node.
        """
        phi_placements: Dict[str, Set[int]] = {}

        for var, def_blocks in def_blocks_per_var.items():
            valid_defs = def_blocks & self.reachable_blocks
            w = deque(list(valid_defs))
            has_phi: Set[int] = set()
            in_worklist: Set[int] = set(valid_defs)

            while w:
                x = w.popleft()
                for y in self.dominance_frontiers.get(x, set()):
                    if y not in has_phi:
                        has_phi.add(y)
                        if y not in in_worklist:
                            in_worklist.add(y)
                            w.append(y)

            phi_placements[var] = has_phi

        return phi_placements
