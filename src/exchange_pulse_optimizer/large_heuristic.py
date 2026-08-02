from __future__ import annotations

from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from itertools import islice
from math import ceil
from typing import Any

import networkx as nx
from qiskit import QuantumCircuit

from .costs import (
    DEFAULT_CZSWAP_FIDELITY,
    DEFAULT_CZSWAP_PULSE_COST,
    DEFAULT_GATE_FIDELITIES,
    DEFAULT_PULSE_COSTS,
    estimate_operation_fidelity,
    total_operation_error_cost,
)
from .layout import interaction_weighted_layout
from .optimizer import PulsePlan, PulseStep
from .topology import EncodedTopology


_EXECUTION_LOOKAHEAD_PER_TOKEN = 32


@dataclass(frozen=True)
class _Gate:
    index: int
    name: str
    qids: tuple[int, ...]
    cost: int
    source_label: str | None = None


class LargeHeuristicOptimizer:
    """
    Scalable front-layer router for larger benchmark circuits.

    This is not an exact optimizer. It builds a dependency front layer, routes
    non-local two-qubit gates with one encoded-SWAP at a time, uses a small
    global lookahead window to score routing candidates, uses bounded
    per-token lookahead to choose optional CXSWAP/CZSWAP operations for
    adjacent CX/CZ gates under a distance-first or cost-weighted objective,
    and finally greedily packs emitted macro operations into
    precedence-preserving layers.
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
        workers: int = 1,
        use_czswap: bool = False,
        initial_layout: dict[int, int] | None = None,
        fusion_objective: str = "distance",
    ) -> None:
        if topology.physical_graph.number_of_nodes() == 0:
            raise ValueError("physical topology must have at least one dot.")
        if topology.num_encoded_slots == 0:
            raise ValueError("encoded layout must have at least one 3-dot group.")

        self._encoded_topology = topology
        self._topology = topology.to_slot_graph()
        if not nx.is_connected(self._topology):
            raise ValueError("encoded groups must form a connected interaction graph.")

        self._pulse_costs = (
            DEFAULT_PULSE_COSTS
            | {"czswap": DEFAULT_CZSWAP_PULSE_COST}
            | (pulse_costs or {})
        )
        self._gate_fidelities = (
            DEFAULT_GATE_FIDELITIES
            | {"czswap": DEFAULT_CZSWAP_FIDELITY}
            | (gate_fidelities or {})
        )
        self._error_scale = error_scale
        if error_scale <= 0:
            raise ValueError("error_scale must be positive.")
        self._layout_decay = layout_decay
        self._layout_local_search_rounds = layout_local_search_rounds
        self._front_layer_size = max(1, front_layer_size)
        self._lookahead_gates = max(0, lookahead_gates)
        self._path_candidates = max(1, path_candidates)
        self._use_cxswap = use_cxswap
        self._use_czswap = use_czswap
        if fusion_objective not in {"distance", "weighted"}:
            raise ValueError(
                "fusion_objective must be 'distance' or 'weighted'."
            )
        self._fusion_objective = fusion_objective
        self._initial_layout = (
            None if initial_layout is None else dict(initial_layout)
        )
        if workers < 1:
            raise ValueError("workers must be positive.")
        self._workers = workers
        self._distances = dict(nx.all_pairs_shortest_path_length(self._topology))
        slot_count = self._topology.number_of_nodes()
        self._distance_matrix = tuple(
            tuple(self._distances[left][right] for right in range(slot_count))
            for left in range(slot_count)
        )
        self._gates_for_lookahead: list[_Gate] = []
        self._two_qubit_gate_indices_by_token: dict[int, tuple[int, ...]] = {}
        self._shortest_path_cache: dict[
            tuple[int, int], tuple[tuple[int, ...], ...]
        ] = {}
        self._score_executor: ProcessPoolExecutor | None = None

    def optimize(self, qc: QuantumCircuit) -> PulsePlan:
        if self._workers == 1:
            return self._optimize(qc)

        with ProcessPoolExecutor(
            max_workers=self._workers,
            initializer=_initialize_large_score_worker,
            initargs=(self._distance_matrix,),
        ) as executor:
            self._score_executor = executor
            try:
                return self._optimize(qc)
            finally:
                self._score_executor = None

    def _optimize(self, qc: QuantumCircuit) -> PulsePlan:
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
        self._two_qubit_gate_indices_by_token = (
            self._index_two_qubit_gates_by_token(gates)
        )
        successors, remaining_dependencies = self._build_dependencies(gates)
        ready = {gate.index for gate in gates if remaining_dependencies[gate.index] == 0}

        initial_layout = self._resolve_initial_layout(qc)
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
            self._apply_encoded_swap_by_slot(
                left,
                right,
                layout,
                occupant,
                steps,
                source_gate=front[0],
            )
            last_swap_edge = frozenset((left, right))

        scheduled_steps, schedule_duration = self._greedy_parallel_schedule(steps)
        return PulsePlan(
            initial_layout=self._public_layout(initial_layout),
            final_layout=self._public_layout(layout),
            pulse_count=sum(step.pulse_count for step in scheduled_steps),
            steps=scheduled_steps,
            initial_slot_layout=dict(initial_layout),
            final_slot_layout=dict(layout),
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
            gates.append(
                _Gate(
                    len(gates),
                    name,
                    qids,
                    cost,
                    getattr(inst.operation, "label", None),
                )
            )
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

    def _index_two_qubit_gates_by_token(
        self,
        gates: list[_Gate],
    ) -> dict[int, tuple[int, ...]]:
        indices_by_token: dict[int, list[int]] = {}
        for gate in gates:
            if len(gate.qids) != 2 or gate.name == "barrier":
                continue
            for qid in gate.qids:
                indices_by_token.setdefault(qid, []).append(gate.index)
        return {
            qid: tuple(indices)
            for qid, indices in indices_by_token.items()
        }

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
                steps.append(
                    PulseStep(
                        gate.name,
                        gate.qids,
                        tuple(
                            self._slot_dots(layout[qid])
                            for qid in gate.qids
                        ),
                        gate.cost,
                        source_gate_index=gate.index,
                        source_label=gate.source_label,
                    )
                )
                self._complete_gate(gate.index, ready, done, successors, remaining_dependencies)
                progressed = True
                continue
            if len(gate.qids) != 1:
                continue
            qid = gate.qids[0]
            steps.append(
                PulseStep(
                    gate.name,
                    gate.qids,
                    (self._slot_dots(layout[qid]),),
                    gate.cost,
                    source_gate_index=gate.index,
                    source_label=gate.source_label,
                )
            )
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
            if gate.name in ("cxswap", "czswap"):
                swapped_layout = dict(layout)
                swapped_layout[a], swapped_layout[b] = swapped_layout[b], swapped_layout[a]
                candidates.append(
                    (
                        gate.name,
                        self._execution_score(gate, done, swapped_layout, gate.name),
                        gate,
                    )
                )
                continue
            candidates.append(
                ("direct", self._execution_score(gate, done, layout, None), gate)
            )
            if self._use_cxswap and gate.name == "cx":
                swapped_layout = dict(layout)
                swapped_layout[a], swapped_layout[b] = swapped_layout[b], swapped_layout[a]
                candidates.append(
                    (
                        "cxswap",
                        self._execution_score(
                            gate, done, swapped_layout, "cxswap"
                        ),
                        gate,
                    )
                )
            if self._use_czswap and gate.name == "cz":
                swapped_layout = dict(layout)
                swapped_layout[a], swapped_layout[b] = swapped_layout[b], swapped_layout[a]
                candidates.append(
                    (
                        "czswap",
                        self._execution_score(
                            gate, done, swapped_layout, "czswap"
                        ),
                        gate,
                    )
                )

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
            self._apply_encoded_swap_by_slot(
                slot_a,
                slot_b,
                layout,
                occupant,
                steps,
                source_gate=gate,
            )
            return

        fused_operation = (
            gate.name
            if gate.name in ("cxswap", "czswap")
            else operation
            if operation in ("cxswap", "czswap")
            else None
        )
        if fused_operation is not None:
            cost = self._pulse_costs[fused_operation]
            steps.append(
                PulseStep(
                    fused_operation,
                    (a, b),
                    (self._slot_dots(slot_a), self._slot_dots(slot_b)),
                    cost,
                    source_gate_index=gate.index,
                    source_label=gate.source_label,
                )
            )
            layout[a], layout[b] = slot_b, slot_a
            occupant[slot_a], occupant[slot_b] = b, a
            return

        steps.append(
            PulseStep(
                gate.name,
                gate.qids,
                (self._slot_dots(slot_a), self._slot_dots(slot_b)),
                gate.cost,
                source_gate_index=gate.index,
                source_label=gate.source_label,
            )
        )

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
        if self._score_executor is not None and len(candidates) >= 4:
            return self._parallel_best_routing_swap(
                candidates,
                front,
                future,
                layout,
                occupant,
            )
        return min(
            candidates,
            key=lambda candidate: self._swap_score(candidate, front, future, layout, occupant),
        )

    def _parallel_best_routing_swap(
        self,
        candidates: list[tuple[int, int]],
        front: list[_Gate],
        future: list[_Gate],
        layout: dict[int, int],
        occupant: dict[Any, int | None],
    ) -> tuple[int, int]:
        executor = self._score_executor
        if executor is None:
            raise RuntimeError("large-heuristic score executor is unavailable.")

        worker_count = min(self._workers, len(candidates))
        chunk_size = ceil(len(candidates) / worker_count)
        chunks = [
            tuple(candidates[start : start + chunk_size])
            for start in range(0, len(candidates), chunk_size)
        ]
        layout_slots = tuple(layout[qid] for qid in range(len(layout)))
        occupants = tuple(
            occupant[slot] for slot in range(self._topology.number_of_nodes())
        )
        front_pairs = tuple(gate.qids for gate in front)
        future_pairs = tuple(gate.qids for gate in future)
        tasks = [
            (
                chunk,
                front_pairs,
                future_pairs,
                layout_slots,
                occupants,
                self._pulse_costs["swap"],
            )
            for chunk in chunks
        ]
        scored = [
            item
            for chunk_result in executor.map(_score_swap_chunk, tasks)
            for item in chunk_result
        ]
        return min(scored, key=lambda item: item[1])[0]

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
        cache_key = (source, target)
        cached = self._shortest_path_cache.get(cache_key)
        if cached is None:
            if (
                self._topology.degree[source] <= 2
                and self._topology.degree[target] <= 2
            ):
                selected = [
                    nx.shortest_path(
                        self._topology,
                        source,
                        target,
                    )
                ]
            else:
                try:
                    paths = nx.shortest_simple_paths(
                        self._topology,
                        source,
                        target,
                    )
                    selected = list(
                        islice(paths, self._path_candidates)
                    )
                except (nx.NetworkXNoPath, nx.NetworkXError):
                    selected = [
                        nx.shortest_path(
                            self._topology,
                            source,
                            target,
                        )
                    ]
            cached = tuple(tuple(path) for path in selected)
            self._shortest_path_cache[cache_key] = cached
        return [list(path) for path in cached]

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
        future = self._execution_lookahead_two_qubit_gates(gate, done)
        operation = operation_override or gate.name
        cost = self._pulse_costs.get(operation, gate.cost)
        if self._fusion_objective == "weighted":
            future_distance_score = self._distance_score(
                future,
                layout,
                weight=1.0,
            )
            weighted_score = (
                cost
                + self._pulse_costs["swap"] * future_distance_score
            )
            return (weighted_score, gate.cost, gate.index)

        future_score = self._distance_score(future, layout, weight=0.35)
        fused_swap_adjustment = (
            gate.cost - cost
            if operation in ("cxswap", "czswap")
            else 0
        )
        return (future_score, cost + fused_swap_adjustment, gate.index)

    def _execution_lookahead_two_qubit_gates(
        self,
        gate: _Gate,
        done: set[int],
    ) -> list[_Gate]:
        future_indices: set[int] = set()
        for qid in gate.qids:
            token_indices = self._two_qubit_gate_indices_by_token.get(qid, ())
            start = bisect_right(token_indices, gate.index)
            selected = 0
            for index in token_indices[start:]:
                if index in done:
                    continue
                future_indices.add(index)
                selected += 1
                if selected == _EXECUTION_LOOKAHEAD_PER_TOKEN:
                    break
        return [
            self._gates_for_lookahead[index]
            for index in sorted(future_indices)
        ]

    def _distance_score(self, gates: list[_Gate], layout: dict[int, int], weight: float) -> float:
        score = 0.0
        for offset, gate in enumerate(gates):
            if len(gate.qids) != 2 or gate.name == "barrier":
                continue
            a, b = gate.qids
            distance = self._distances[layout[a]][layout[b]]
            score += weight * (0.98**offset) * max(0, distance - 1)
        return score

    def _front_two_qubit_gates(self, gates: list[_Gate], ready: set[int]) -> list[_Gate]:
        front = [
            gates[index]
            for index in sorted(ready)
            if len(gates[index].qids) == 2
            and gates[index].name != "barrier"
        ]
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
            if (
                gate.index in excluded
                or len(gate.qids) != 2
                or gate.name == "barrier"
            ):
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
        source_gate: _Gate | None = None,
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
                source_gate_index=(
                    None if source_gate is None else source_gate.index
                ),
                source_label=(
                    None if source_gate is None else source_gate.source_label
                ),
            )
        )

    def _greedy_parallel_schedule(self, steps: list[PulseStep]) -> tuple[list[PulseStep], int]:
        layer_resources: list[set[tuple[str, Any]]] = []
        layer_durations: list[int] = []
        last_layer_by_resource: dict[tuple[str, Any], int] = {}
        scheduled: list[PulseStep] = []
        global_floor = 0

        for step in steps:
            resources = self._step_resources(step)
            resource_floor = max(
                (last_layer_by_resource.get(resource, -1) + 1 for resource in resources),
                default=0,
            )
            layer = max(global_floor, resource_floor)
            if step.name == "barrier":
                layer = max(layer, len(layer_resources))
            while layer < len(layer_resources) and layer_resources[layer] & resources:
                layer += 1
            if layer == len(layer_resources):
                layer_resources.append(set())
                layer_durations.append(0)
            layer_resources[layer].update(resources)
            layer_durations[layer] = max(layer_durations[layer], step.pulse_count)
            for resource in resources:
                last_layer_by_resource[resource] = layer
            if step.name == "barrier":
                global_floor = layer + 1
            scheduled.append(replace(step, layer=layer))

        return scheduled, sum(layer_durations)

    def _resolve_initial_layout(self, qc: QuantumCircuit) -> dict[int, int]:
        if self._initial_layout is None:
            return interaction_weighted_layout(
                qc,
                self._topology,
                decay=self._layout_decay,
                local_search_rounds=self._layout_local_search_rounds,
            )

        initial_layout = dict(self._initial_layout)
        expected_qubits = set(range(qc.num_qubits))
        supplied_qubits = set(initial_layout)
        missing = sorted(expected_qubits - supplied_qubits)
        extra = sorted(supplied_qubits - expected_qubits)
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing logical qubits {missing}")
            if extra:
                details.append(f"unknown logical qubits {extra}")
            raise ValueError(
                "fixed initial layout must assign every circuit qubit exactly once: "
                + ", ".join(details)
            )

        slots = list(initial_layout.values())
        if len(slots) != len(set(slots)):
            raise ValueError("fixed initial layout must assign unique slots.")

        unknown_slots = sorted(slot for slot in slots if slot not in self._topology)
        if unknown_slots:
            raise ValueError(
                f"fixed initial layout uses unknown slots {unknown_slots}."
            )
        return initial_layout

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


_LARGE_DISTANCE_MATRIX: tuple[tuple[int, ...], ...] = ()


def _initialize_large_score_worker(
    distance_matrix: tuple[tuple[int, ...], ...],
) -> None:
    global _LARGE_DISTANCE_MATRIX
    _LARGE_DISTANCE_MATRIX = distance_matrix


def _score_swap_chunk(
    task: tuple[
        tuple[tuple[int, int], ...],
        tuple[tuple[int, ...], ...],
        tuple[tuple[int, ...], ...],
        tuple[int, ...],
        tuple[int | None, ...],
        int,
    ],
) -> list[tuple[tuple[int, int], tuple[float, int, tuple[int, int]]]]:
    (
        candidates,
        front_pairs,
        future_pairs,
        layout,
        occupants,
        swap_cost,
    ) = task
    if not _LARGE_DISTANCE_MATRIX:
        raise RuntimeError("large-heuristic score worker was not initialized.")

    results = []
    for candidate in candidates:
        left, right = candidate
        left_qubit = occupants[left]
        right_qubit = occupants[right]
        front_score = _parallel_distance_score(
            front_pairs,
            layout,
            left,
            right,
            left_qubit,
            right_qubit,
            1.0,
        )
        future_score = _parallel_distance_score(
            future_pairs,
            layout,
            left,
            right,
            left_qubit,
            right_qubit,
            0.35,
        )
        results.append(
            (
                candidate,
                (front_score + future_score, swap_cost, candidate),
            )
        )
    return results


def _parallel_distance_score(
    gate_pairs: tuple[tuple[int, ...], ...],
    layout: tuple[int, ...],
    left: int,
    right: int,
    left_qubit: int | None,
    right_qubit: int | None,
    weight: float,
) -> float:
    score = 0.0
    for offset, qids in enumerate(gate_pairs):
        if len(qids) != 2:
            continue
        a, b = qids
        slot_a = _slot_after_candidate_swap(
            a, layout, left, right, left_qubit, right_qubit
        )
        slot_b = _slot_after_candidate_swap(
            b, layout, left, right, left_qubit, right_qubit
        )
        score += (
            weight
            * (0.98**offset)
            * max(0, _LARGE_DISTANCE_MATRIX[slot_a][slot_b] - 1)
        )
    return score


def _slot_after_candidate_swap(
    qubit: int,
    layout: tuple[int, ...],
    left: int,
    right: int,
    left_qubit: int | None,
    right_qubit: int | None,
) -> int:
    if qubit == left_qubit:
        return right
    if qubit == right_qubit:
        return left
    return layout[qubit]
