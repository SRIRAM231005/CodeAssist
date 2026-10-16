from collections import deque
from typing import Callable, Dict, Generic, List, Set, TypeVar, Optional
from ..cfg.model import CFG

T = TypeVar("T")


class WorklistSolver(Generic[T]):
    """
    Generic iterative worklist fixed-point solver for monotone dataflow frameworks.

    Supports:
    - Direction: "forward" or "backward"
    - Join operator: set union (May-analysis) or set intersection (Must-analysis)
    - Custom transfer function: f_B(in_or_out_state) -> new_state
    """

    def __init__(
        self,
        cfg: CFG,
        direction: str = "forward",
        join_op: str = "union",  # "union" or "intersection"
        boundary_val: Optional[Set[T]] = None,
    ):
        self.cfg = cfg
        self.direction = direction.lower()
        self.join_op = join_op.lower()
        self.boundary_val: Set[T] = boundary_val if boundary_val is not None else set()

    def solve(
        self,
        transfer_fn: Callable[[int, Set[T]], Set[T]],
        initial_val: Optional[Set[T]] = None,
    ) -> tuple[Dict[int, Set[T]], Dict[int, Set[T]]]:
        """
        Runs worklist iteration until fixed-point convergence.
        Returns (in_states, out_states) mapping block_id -> Set[T].
        """
        init_set = initial_val if initial_val is not None else set()
        blocks = self.cfg.blocks

        # Initialize IN and OUT sets for all blocks
        in_states: Dict[int, Set[T]] = {bid: set(init_set) for bid in blocks}
        out_states: Dict[int, Set[T]] = {bid: set(init_set) for bid in blocks}

        if self.direction == "forward":
            # Forward analysis: entry boundary
            if self.cfg.entry_block_id in in_states:
                in_states[self.cfg.entry_block_id] = set(self.boundary_val)
                out_states[self.cfg.entry_block_id] = transfer_fn(self.cfg.entry_block_id, in_states[self.cfg.entry_block_id])

            worklist = deque([bid for bid in blocks if bid != self.cfg.entry_block_id])

            while worklist:
                bid = worklist.popleft()
                preds = self.cfg.predecessors(bid)

                # Meet / Join over predecessors
                if preds:
                    if self.join_op == "union":
                        in_b = set().union(*(out_states[p] for p in preds if p in out_states))
                    else:  # intersection
                        in_b = set.intersection(*(out_states[p] for p in preds if p in out_states))
                else:
                    in_b = set(self.boundary_val) if bid == self.cfg.entry_block_id else set(init_set)

                in_states[bid] = in_b
                new_out = transfer_fn(bid, in_b)

                if new_out != out_states[bid]:
                    out_states[bid] = new_out
                    for succ in self.cfg.successors(bid):
                        if succ not in worklist:
                            worklist.append(succ)

        else:
            # Backward analysis: exit boundary
            exit_ids = [self.cfg.exit_block_id] if self.cfg.exit_block_id in blocks else []
            if not exit_ids:
                # Find blocks with no successors
                exit_ids = [bid for bid in blocks if not self.cfg.successors(bid)]

            for xid in exit_ids:
                out_states[xid] = set(self.boundary_val)
                in_states[xid] = transfer_fn(xid, out_states[xid])

            worklist = deque([bid for bid in blocks if bid not in exit_ids])

            while worklist:
                bid = worklist.popleft()
                succs = self.cfg.successors(bid)

                # Meet / Join over successors
                if succs:
                    if self.join_op == "union":
                        out_b = set().union(*(in_states[s] for s in succs if s in in_states))
                    else:  # intersection
                        out_b = set.intersection(*(in_states[s] for s in succs if s in in_states))
                else:
                    out_b = set(self.boundary_val)

                out_states[bid] = out_b
                new_in = transfer_fn(bid, out_b)

                if new_in != in_states[bid]:
                    in_states[bid] = new_in
                    for pred in self.cfg.predecessors(bid):
                        if pred not in worklist:
                            worklist.append(pred)

        return in_states, out_states
