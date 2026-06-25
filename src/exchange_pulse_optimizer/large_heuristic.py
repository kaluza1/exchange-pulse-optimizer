from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import islice
from typing import Any

import networkx as nx
from qiskit import QuantumCircuit

from .costs import (
    DEFAULT_GATE_FIDELITIES,
    DEFAULT_PULSE_COSTS,
    estimate_operation_fidelity,
    total_operation_error_cost,
)
from .layout import interaction_weighted_layout
from .optimizer import PulsePlan, PulseStep
from .topology import EncodedTopology


@dataclass(frozen=True)
class _Gate:
    index: int
    name: str
    qids: tuple[int, ...]
    cost: int


class LargeHeuristicOptimizer:
    """
    Scalable front-layer router for larger benchmark circuits.

    This is not an exact optimizer. It builds a dependency front layer, routes
    non-local two-qubit gates with one encoded-SWAP at a time, uses a small
    lookahead window to score candidates, optionally chooses CXSWAP for CX
    gates, and finally greedily packs emitted macro operations into layers.
    """

    def __init__(
        self,
        topology: EncodedTopology,
        pulse_costs: dict[str, int] | None = None,
        gate_fidelities: dict[str, float] | None = None,
        error_scale: int = 1_000_000,
        layout_decay: float = 0.98,
        layout_local_search_rounds: int = 0,
        front_layer_size: int = 24,
        lookahead_gates: int = 32,
        path_candidates: int = 3,
        use_cxswap: bool = True,
    ) -> None:
        if topology.physical_graph.number_of_nodes() == 0:
            raise ValueError("physical topology must have at least one dot.")
        if topology.num_encoded_slots == 0:
            raise ValueError("encoded layout must have at least one 3-dot group.")

        self._encoded_topology = topology
        self._topology = topology.to_slot_graph()
        if not nx.is_connected(self._topology):
            raise ValueError("encoded groups must form a connected interaction graph.")

        self._pulse_costs = DEFAULT_PULSE_COSTS | (pulse_costs or {})
        self._gate_fidelities = DEFAULT_GATE_FIDELITIES | (gate_fidelities or {})
        self._error_scale = error_scale
        if error_scale <= 0:
            raise ValueError("error_scale must be positive.")
        self._layout_decay = layout_decay
        self._layout_local_search_rounds = layout_local_search_rounds
        self._front_layer_size = max(1, front_layer_size)
        self._lookahead_gates = max(0, lookahead_gates)
        self._path_candidates = max(1, path_candidates)
        self._use_cxswap = use_cxswap
        self._distances = dict(nx.all_pairs_shortest_path_length(self._topology))
        self._gates_for_lookahead: list[_Gate] = []

    def optimize(self, qc: QuantumCircuit) -> PulsePlan:
        if qc.num_qubits > self._encoded_topology.num_encoded_slots:
            required_dots = 3 * qc.num_qubits
            available_groups = self._encoded_topology.num_encoded_slots
            available_dots = 3 * available_groups
            raise ValueError(
                "encoded layout has fewer 3-dot groups than circuit qubits: "
                f"qasm_qubits={qc.num_qubits}, encoded_groups={available_groups}, "
                f"required_physical_dots={required_dots}, grouped_physical_dots={available_dots}."
            )

        gates = self._build_gates(qc)
        self._gates_for_lookahead = gates
        successors, remaining_dependencies = self._build_dependencies(gates)
        ready = {gate.index for gate in gates if remaining_dependencies[gate.index] == 0}

        initial_layout = interaction_weighted_layout(
            qc,
            self._topology,
            decay=self._layout_decay,
            local_search_rounds=self._layout_local_search_rounds,
        )
        layout = dict(initial_layout)
        occupant: dict[Any, int | None] = {slot: None for slot in self._topology.nodes}
        occupant.update({slot: qubit for qubit, slot in layout.items()})

        done: set[int] = set()
        steps: list[PulseStep] = []
        last_swap_edge: frozenset[Any] | None = None
        iteration_limit = max(10000, len(gates) * max(20, self._topology.number_of_nodes()))
        iterations = 0

        while len(done) < len(gates):
            iterations += 1
            if iterations > iteration_limit:
                raise RuntimeError("large heuristic exceeded its routing iteration limit.")

            progressed = self._emit_ready_single_qubit_gates(
                gates, ready, done, successors, remaining_dependencies, layout, steps
            )
            if progressed:
                last_swap_edge = None
                continue

            executed_gate = self._try_emit_best_ready_two_qubit_gate(
                gates, ready, done, successors, remaining_dependencies, layout, occupant, steps
            )
            if executed_gate:
                last_swap_edge = None
                continue

            front = self._front_two_qubit_gates(gates, ready)
            if not front:
                raise RuntimeError("large heuristic has no ready gates but routing is incomplete.")

            swap_slots = self._choose_routing_swap(front, gates, done, layout, occupant, last_swap_edge)
            if swap_slots is None:
                swap_slots = self._fallback_swap(front[0], layout)
            left, right = swap_slots
            self._apply_encoded_swap_by_slot(left, right, layout, occupant, steps)
            last_swap_edge = frozenset((left, right))

        scheduled_steps, schedule_duration = self._greedy_parallel_schedule(steps)
        return PulsePlan(
            initial_layout=self._public_layout(initial_layout),
            final_layout=self._public_layout(layout),
            pulse_count=sum(step.pulse_count for step in scheduled_steps),
            steps=scheduled_steps,
            solver_status="HEURISTIC",
            schedule_duration=schedule_duration,
            estimated_fidelity=estimate_operation_fidelity(
                tuple(step.name for step in scheduled_steps),
                self._gate_fidelities,
            ),
            total_error_cost=total_operation_error_cost(
                tuple(step.name for step in scheduled_steps),
                self._gate_fidelities,
                self._error_scale,
            ),
        )

    def _build_gates(self, qc: QuantumCircuit) -> list[_Gate]:
        gates: list[_Gate] = []
        for inst in qc.data:
            name = inst.operation.name
            qids = tuple(qc.find_bit(q).index for q in inst.qubits)
            cost = self._pulse_costs.get(name)
            if cost is None:
                raise ValueError(f"unsupported gate for pulse optimization: {name}")
            if len(qids) > 2 and name != "barrier":
                raise ValueError(f"only 1q and 2q gates are supported: {name}")
            gates.append(_Gate(len(gates), name, qids, cost))
        return gates

    def _build_dependencies(self, gates: list[_Gate]) -> tuple[dict[int, list[int]], dict[int, int]]:
        successors = {gate.index: [] for gate in gates}
        dependencies = {gate.index: 0 for gate in gates}
        last_by_qubit: dict[int, int] = {}

        for gate in gates:
            predecessors = {
                last_by_qubit[qid]
                for qid in gate.qids
                if qid in last_by_qubit
            }
            dependencies[gate.index] = len(predecessors)
            for predecessor in predecessors:
                successors[predecessor].append(gate.index)
            for qid in gate.qids:
                last_by_qubit[qid] = gate.index

        return successors, dependencies

    def _emit_ready_single_qubit_gates(
        self,
        gates: list[_Gate],
        ready: set[int],
        done: set[int],
        successors: dict[int, list[int]],
        remaining_dependencies: dict[int, int],
        layout: dict[int, int],
        steps: list[PulseStep],
    ) -> bool:
        progressed = False
        for gate_index in sorted(ready):
            gate = gates[gate_index]
            if gate.name == "barrier":
                self._complete_gate(gate.index, ready, done, successors, remaining_dependencies)
                progressed = True
                continue
            if len(gate.qids) != 1:
                continue
            qid = gate.qids[0]
            steps.append(PulseStep(gate.name, gate.qids, (self._slot_dots(layout[qid]),), gate.cost))
            self._complete_gate(gate.index, ready, done, successors, remaining_dependencies)
            progressed = True
        return progressed

    def _try_emit_best_ready_two_qubit_gate(
        self,
        gates: list[_Gate],
        ready: set[int],
        done: set[int],
        successors: dict[int, list[int]],
        remaining_dependencies: dict[int, int],
        layout: dict[int, int],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
    ) -> bool:
        candidates = []
        for gate in self._front_two_qubit_gates(gates, ready):
            a, b = gate.qids
            if not self._topology.has_edge(layout[a], layout[b]):
                continue
            candidates.append(("direct", self._execution_score(gate, done, layout, None), gate))
            if self._use_cxswap and gate.name == "cx":
                swapped_layout = dict(layout)
                swapped_layout[a], swapped_layout[b] = swapped_layout[b], swapped_layout[a]
                candidates.append(("cxswap", self._execution_score(gate, done, swapped_layout, "cxswap"), gate))
            if gate.name == "cxswap":
                candidates.append(("cxswap", self._execution_score(gate, done, layout, "cxswap"), gate))

        if not candidates:
            return False

        operation, _score, gate = min(candidates, key=lambda item: item[1])
        self._emit_two_qubit_gate(gate, operation, layout, occupant, steps)
        self._complete_gate(gate.index, ready, done, successors, remaining_dependencies)
        return True

    def _emit_two_qubit_gate(
        self,
        gate: _Gate,
        operation: str,
        layout: dict[int, int],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
    ) -> None:
        a, b = gate.qids
        slot_a = layout[a]
        slot_b = layout[b]
        if gate.name == "swap":
            self._apply_encoded_swap_by_slot(slot_a, slot_b, layout, occupant, steps)
            return

        if operation == "cxswap" or gate.name == "cxswap":
            cost = self._pulse_costs["cxswap"]
            steps.append(PulseStep("cxswap", (a, b), (self._slot_dots(slot_a), self._slot_dots(slot_b)), cost))
            layout[a], layout[b] = slot_b, slot_a
            occupant[slot_a], occupant[slot_b] = b, a
            return

        steps.append(PulseStep(gate.name, gate.qids, (self._slot_dots(slot_a), self._slot_dots(slot_b)), gate.cost))

    def _choose_routing_swap(
        self,
        front: list[_Gate],
        gates: list[_Gate],
        done: set[int],
        layout: dict[int, int],
        occupant: dict[Any, int | None],
        last_swap_edge: frozenset[Any] | None,
    ) -> tuple[int, int] | None:
        candidates = self._routing_candidates(front, layout, occupant)
        if len(candidates) > 1 and last_swap_edge is not None:
            candidates = [candidate for candidate in candidates if frozenset(candidate) != last_swap_edge]
        if not candidates:
            return None

        future = self._lookahead_two_qubit_gates(gates, done, front)
        return min(
            candidates,
            key=lambda candidate: self._swap_score(candidate, front, future, layout, occupant),
        )

    def _routing_candidates(
        self,
        front: list[_Gate],
        layout: dict[int, int],
        occupant: dict[Any, int | None],
    ) -> list[tuple[int, int]]:
        candidates: set[tuple[int, int]] = set()
        for gate in front[: self._front_layer_size]:
            a, b = gate.qids
            slot_a = layout[a]
            slot_b = layout[b]

            for neighbor in self._topology.neighbors(slot_a):
                if occupant[slot_a] is not None or occupant[neighbor] is not None:
                    candidates.add(tuple(sorted((slot_a, neighbor))))
            for neighbor in self._topology.neighbors(slot_b):
                if occupant[slot_b] is not None or occupant[neighbor] is not None:
                    candidates.add(tuple(sorted((slot_b, neighbor))))

            for path in self._shortest_path_candidates(slot_a, slot_b):
                if len(path) >= 2:
                    candidates.add(tuple(sorted((path[0], path[1]))))
                    candidates.add(tuple(sorted((path[-2], path[-1]))))

        return sorted(candidates)

    def _shortest_path_candidates(self, source: int, target: int) -> list[list[int]]:
        if source == target:
            return [[source]]
        if self._topology.degree[source] <= 2 and self._topology.degree[target] <= 2:
            return [nx.shortest_path(self._topology, source, target)]
        try:
            paths = nx.shortest_simple_paths(self._topology, source, target)
            return list(islice(paths, self._path_candidates))
        except (nx.NetworkXNoPath, nx.NetworkXError):
            return [nx.shortest_path(self._topology, source, target)]

    def _fallback_swap(self, gate: _Gate, layout: dict[int, int]) -> tuple[int, int]:
        a, b = gate.qids
        path = nx.shortest_path(self._topology, layout[a], layout[b])
        return path[0], path[1]

    def _swap_score(
        self,
        candidate: tuple[int, int],
        front: list[_Gate],
        future: list[_Gate],
        layout: dict[int, int],
        occupant: dict[Any, int | None],
    ) -> tuple[float, int, tuple[int, int]]:
        left, right = candidate
        candidate_layout = dict(layout)
        left_qubit = occupant[left]
        right_qubit = occupant[right]
        if left_qubit is not None:
            candidate_layout[left_qubit] = right
        if right_qubit is not None:
            candidate_layout[right_qubit] = left

        front_score = self._distance_score(front, candidate_layout, weight=1.0)
        future_score = self._distance_score(future, candidate_layout, weight=0.35)
        return (front_score + future_score, self._pulse_costs["swap"], candidate)

    def _execution_score(
        self,
        gate: _Gate,
        done: set[int],
        layout: dict[int, int],
        operation_override: str | None,
    ) -> tuple[float, int, int]:
        future = self._lookahead_two_qubit_gates_from_indices(done | {gate.index}, ())
        future_score = self._distance_score(future, layout, weight=0.35)
        operation = operation_override or gate.name
        cost = self._pulse_costs.get(operation, gate.cost)
        cxswap_bias = -3 if operation == "cxswap" else 0
        return (future_score, cost + cxswap_bias, gate.index)

    def _distance_score(self, gates: list[_Gate], layout: dict[int, int], weight: float) -> float:
        score = 0.0
        for offset, gate in enumerate(gates):
            if len(gate.qids) != 2:
                continue
            a, b = gate.qids
            distance = self._distances[layout[a]][layout[b]]
            score += weight * (0.98**offset) * max(0, distance - 1)
        return score

    def _front_two_qubit_gates(self, gates: list[_Gate], ready: set[int]) -> list[_Gate]:
        front = [gates[index] for index in sorted(ready) if len(gates[index].qids) == 2]
        return front[: self._front_layer_size]

    def _lookahead_two_qubit_gates(
        self,
        gates: list[_Gate],
        done: set[int],
        front: list[_Gate],
    ) -> list[_Gate]:
        front_indices = {gate.index for gate in front}
        return self._lookahead_two_qubit_gates_from_indices(done | front_indices, front_indices)

    def _lookahead_two_qubit_gates_from_indices(
        self,
        excluded: set[int],
        _front_indices: set[int] | tuple[()] = (),
    ) -> list[_Gate]:
        lookahead = []
        for gate in self._gates_for_lookahead:
            if gate.index in excluded or len(gate.qids) != 2:
                continue
            lookahead.append(gate)
            if len(lookahead) >= self._lookahead_gates:
                break
        return lookahead

    def _complete_gate(
        self,
        gate_index: int,
        ready: set[int],
        done: set[int],
        successors: dict[int, list[int]],
        remaining_dependencies: dict[int, int],
    ) -> None:
        ready.discard(gate_index)
        done.add(gate_index)
        for successor in successors[gate_index]:
            remaining_dependencies[successor] -= 1
            if remaining_dependencies[successor] == 0:
                ready.add(successor)

    def _apply_encoded_swap_by_slot(
        self,
        slot_a: int,
        slot_b: int,
        layout: dict[int, int],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
    ) -> None:
        if not self._topology.has_edge(slot_a, slot_b):
            raise ValueError(f"cannot apply encoded swap between non-adjacent slots: {slot_a}, {slot_b}")
        qubit_a = occupant[slot_a]
        qubit_b = occupant[slot_b]
        if qubit_a is None and qubit_b is None:
            return
        if qubit_a is not None:
            layout[qubit_a] = slot_b
        if qubit_b is not None:
            layout[qubit_b] = slot_a
        occupant[slot_a], occupant[slot_b] = qubit_b, qubit_a
        logical = tuple(q for q in (qubit_a, qubit_b) if q is not None)
        steps.append(
            PulseStep(
                "encoded_swap",
                logical,
                (self._slot_dots(slot_a), self._slot_dots(slot_b)),
                self._pulse_costs["swap"],
            )
        )

    def _greedy_parallel_schedule(self, steps: list[PulseStep]) -> tuple[list[PulseStep], int]:
        layer_resources: list[set[tuple[str, Any]]] = []
        layer_durations: list[int] = []
        scheduled: list[PulseStep] = []

        for step in steps:
            resources = self._step_resources(step)
            layer = 0
            while layer < len(layer_resources) and layer_resources[layer] & resources:
                layer += 1
            if layer == len(layer_resources):
                layer_resources.append(set())
                layer_durations.append(0)
            layer_resources[layer].update(resources)
            layer_durations[layer] = max(layer_durations[layer], step.pulse_count)
            scheduled.append(replace(step, layer=layer))

        return scheduled, sum(layer_durations)

    def _step_resources(self, step: PulseStep) -> set[tuple[str, Any]]:
        resources: set[tuple[str, Any]] = set()
        for qid in step.logical_qubits:
            resources.add(("q", qid))
        for dot_group in step.dot_groups:
            resources.add(("dots", dot_group))
        return resources

    def _public_layout(self, layout: dict[int, int]) -> dict[int, tuple[Any, ...]]:
        return {qubit: self._slot_dots(layout[qubit]) for qubit in sorted(layout)}

    def _slot_dots(self, slot: int) -> tuple[Any, ...]:
        return self._encoded_topology.dot_groups[slot]
