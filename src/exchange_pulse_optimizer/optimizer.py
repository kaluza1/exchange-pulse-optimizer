from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
import json
from math import inf
from pathlib import Path
from typing import Any

import networkx as nx
from qiskit import QuantumCircuit


DEFAULT_PULSE_COSTS = {
    "cx": 28,
    "cz": 28,
    "swap": 15,
    "h": 3,
    "x": 3,
    "y": 3,
    "z": 1,
    "s": 1,
    "sdg": 1,
    "t": 1,
    "tdg": 1,
    "rx": 3,
    "ry": 3,
    "rz": 1,
    "measure": 0,
    "barrier": 0,
}


@dataclass(frozen=True)
class PulseStep:
    name: str
    logical_qubits: tuple[int, ...]
    dot_groups: tuple[tuple[Any, ...], ...]
    pulse_count: int
    layer: int | None = None


@dataclass
class PulsePlan:
    initial_layout: dict[int, tuple[Any, ...]]
    final_layout: dict[int, tuple[Any, ...]]
    pulse_count: int
    steps: list[PulseStep] = field(default_factory=list)
    solver_status: str | None = None
    schedule_duration: int | None = None


@dataclass(frozen=True)
class EncodedTopology:
    physical_graph: nx.Graph
    dot_groups: tuple[tuple[Any, Any, Any], ...]

    @property
    def num_encoded_slots(self) -> int:
        return len(self.dot_groups)

    def to_slot_graph(self) -> nx.Graph:
        slot_graph = nx.Graph()
        for slot, dots in enumerate(self.dot_groups):
            slot_graph.add_node(slot, dots=dots)

        for left in range(len(self.dot_groups)):
            for right in range(left + 1, len(self.dot_groups)):
                if self._groups_touch(self.dot_groups[left], self.dot_groups[right]):
                    slot_graph.add_edge(left, right)

        return slot_graph

    def _groups_touch(self, left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
        return any(self.physical_graph.has_edge(a, b) for a in left for b in right)


def read_openqasm(path: str | Path) -> QuantumCircuit:
    return QuantumCircuit.from_qasm_file(str(path))


def read_topology_json(path: str | Path) -> EncodedTopology:
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)

    graph = nx.Graph()
    for node in data.get("nodes", []):
        if isinstance(node, dict):
            node_id = node["id"]
            attrs = {key: value for key, value in node.items() if key != "id"}
            graph.add_node(node_id, **attrs)
        else:
            graph.add_node(node)

    for edge in data["edges"]:
        if len(edge) == 2:
            graph.add_edge(edge[0], edge[1])
        elif len(edge) == 3:
            graph.add_edge(edge[0], edge[1], **edge[2])
        else:
            raise ValueError(f"invalid edge entry: {edge}")

    if "encoded_qubits" not in data:
        raise ValueError("topology JSON must define encoded_qubits, e.g. [[0, 1, 2], [3, 4, 5]].")

    dot_groups = []
    used_dots = set()
    for index, group in enumerate(data["encoded_qubits"]):
        if len(group) != 3:
            raise ValueError(f"encoded_qubits[{index}] must contain exactly 3 physical dots.")
        missing = [dot for dot in group if dot not in graph]
        if missing:
            raise ValueError(f"encoded_qubits[{index}] contains dots that are not topology nodes: {missing}")
        overlap = [dot for dot in group if dot in used_dots]
        if overlap:
            raise ValueError(f"physical dots cannot appear in multiple encoded groups: {overlap}")
        if not nx.is_connected(graph.subgraph(group)):
            raise ValueError(f"encoded_qubits[{index}] must be connected inside the physical topology.")

        dot_groups.append(tuple(group))
        used_dots.update(group)

    return EncodedTopology(graph, tuple(dot_groups))


class PulseCountOptimizer:
    """
    Initial-layout and encoded-SWAP router for exchange-only pulse count.

    The topology is a physical quantum-dot graph plus a list of 3-dot encoded
    qubit groups. Two-qubit operations are executable only between adjacent
    encoded groups. Non-adjacent operations are routed with encoded SWAPs.
    """

    def __init__(
        self,
        topology: EncodedTopology,
        pulse_costs: dict[str, int] | None = None,
        max_layouts: int = 40320,
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
        self._max_layouts = max_layouts

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

        layout = self._find_initial_layout(qc)
        return self._route(qc, layout, record_steps=True)

    def _find_initial_layout(self, qc: QuantumCircuit) -> dict[int, int]:
        nodes = list(self._topology.nodes)
        logical_qubits = list(range(qc.num_qubits))

        best_cost = inf
        best_layout = dict(zip(logical_qubits, nodes))

        for index, slots in enumerate(permutations(nodes, qc.num_qubits)):
            if index >= self._max_layouts:
                break
            layout = dict(zip(logical_qubits, slots))
            cost = self._route(qc, layout, record_steps=False).pulse_count
            if cost < best_cost:
                best_cost = cost
                best_layout = layout

        return best_layout

    def _route(
        self,
        qc: QuantumCircuit,
        initial_layout: dict[int, int],
        record_steps: bool,
    ) -> PulsePlan:
        layout = dict(initial_layout)
        occupant: dict[Any, int | None] = {slot: None for slot in self._topology.nodes}
        occupant.update({slot: qubit for qubit, slot in layout.items()})

        pulse_count = 0
        steps: list[PulseStep] = []

        for inst in qc.data:
            name = inst.operation.name
            qids = tuple(qc.find_bit(q).index for q in inst.qubits)
            cost = self._pulse_costs.get(name)

            if cost is None:
                raise ValueError(f"unsupported gate for pulse optimization: {name}")
            if len(qids) == 0:
                continue
            if len(qids) == 1:
                pulse_count += cost
                if record_steps:
                    steps.append(PulseStep(name, qids, (self._slot_dots(layout[qids[0]]),), cost, len(steps)))
                continue
            if len(qids) != 2:
                raise ValueError(f"only 1q and 2q gates are supported: {name}")

            a, b = qids
            pulse_count += self._make_adjacent(a, b, layout, occupant, steps, record_steps)

            if name == "swap":
                pulse_count += self._apply_encoded_swap(a, b, layout, occupant, steps, record_steps)
            else:
                pulse_count += cost
                if record_steps:
                    steps.append(PulseStep(name, qids, (self._slot_dots(layout[a]), self._slot_dots(layout[b])), cost, len(steps)))

        return PulsePlan(
            initial_layout=self._public_layout(initial_layout),
            final_layout=self._public_layout(layout),
            pulse_count=pulse_count,
            steps=steps,
            schedule_duration=pulse_count,
        )

    def _public_layout(self, layout: dict[int, int]) -> dict[int, tuple[Any, ...]]:
        return {qubit: self._slot_dots(slot) for qubit, slot in layout.items()}

    def _slot_dots(self, slot: int) -> tuple[Any, ...]:
        return self._encoded_topology.dot_groups[slot]

    def _make_adjacent(
        self,
        a: int,
        b: int,
        layout: dict[int, Any],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
        record_steps: bool,
    ) -> int:
        slot_a = layout[a]
        slot_b = layout[b]
        if self._topology.has_edge(slot_a, slot_b):
            return 0

        path = nx.shortest_path(self._topology, slot_a, slot_b)
        cost = 0
        for left, right in zip(path, path[1:-1]):
            moving_qubit = occupant[left]
            other_qubit = occupant[right]
            if moving_qubit is None:
                raise ValueError("internal routing error: no qubit to move.")
            if other_qubit is None:
                cost += self._move_to_empty_slot(
                    moving_qubit, right, layout, occupant, steps, record_steps
                )
            else:
                cost += self._apply_encoded_swap(
                    moving_qubit, other_qubit, layout, occupant, steps, record_steps
                )

        return cost

    def _apply_encoded_swap(
        self,
        a: int,
        b: int,
        layout: dict[int, Any],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
        record_steps: bool,
    ) -> int:
        slot_a = layout[a]
        slot_b = layout[b]
        if not self._topology.has_edge(slot_a, slot_b):
            raise ValueError(f"cannot apply encoded swap between non-adjacent slots: {slot_a}, {slot_b}")

        layout[a], layout[b] = slot_b, slot_a
        occupant[slot_a], occupant[slot_b] = b, a

        cost = self._pulse_costs["swap"]
        if record_steps:
            steps.append(PulseStep("encoded_swap", (a, b), (self._slot_dots(slot_a), self._slot_dots(slot_b)), cost, len(steps)))
        return cost

    def _move_to_empty_slot(
        self,
        a: int,
        empty_slot: Any,
        layout: dict[int, Any],
        occupant: dict[Any, int | None],
        steps: list[PulseStep],
        record_steps: bool,
    ) -> int:
        slot_a = layout[a]
        if occupant[empty_slot] is not None:
            raise ValueError(f"slot is not empty: {empty_slot}")
        if not self._topology.has_edge(slot_a, empty_slot):
            raise ValueError(f"cannot move between non-adjacent slots: {slot_a}, {empty_slot}")

        layout[a] = empty_slot
        occupant[slot_a] = None
        occupant[empty_slot] = a

        cost = self._pulse_costs["swap"]
        if record_steps:
            steps.append(PulseStep("move_to_empty", (a,), (self._slot_dots(slot_a), self._slot_dots(empty_slot)), cost, len(steps)))
        return cost
