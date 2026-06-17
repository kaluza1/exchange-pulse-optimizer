from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import networkx as nx


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

    topology = EncodedTopology(graph, tuple(dot_groups))
    _validate_encoded_group_interfaces(topology)

    slot_graph = topology.to_slot_graph()
    if not (_is_line_slot_graph(slot_graph) or _is_square_lattice_slot_graph(slot_graph)):
        raise ValueError(
            "encoded-qubit connectivity must be either a 1D line or a 2D square-lattice patch "
            "(rows x columns with rows >= 2 and columns >= 2). "
            "Other topology inputs are not supported."
        )

    return topology


def _is_square_lattice_slot_graph(graph: nx.Graph) -> bool:
    num_slots = graph.number_of_nodes()
    for rows in range(2, num_slots + 1):
        if num_slots % rows != 0:
            continue
        cols = num_slots // rows
        if cols < 2:
            continue

        grid = nx.convert_node_labels_to_integers(nx.grid_2d_graph(rows, cols))
        if nx.is_isomorphic(graph, grid):
            return True

    return False


def _is_line_slot_graph(graph: nx.Graph) -> bool:
    num_slots = graph.number_of_nodes()
    return num_slots >= 2 and nx.is_isomorphic(graph, nx.path_graph(num_slots))


def _validate_encoded_group_interfaces(topology: EncodedTopology) -> None:
    for left_index in range(len(topology.dot_groups)):
        for right_index in range(left_index + 1, len(topology.dot_groups)):
            left = topology.dot_groups[left_index]
            right = topology.dot_groups[right_index]
            cross_edges = [
                (a, b)
                for a in left
                for b in right
                if topology.physical_graph.has_edge(a, b)
            ]
            if not cross_edges:
                continue

            allowed_edges = {
                frozenset((left[2], right[0])),
                frozenset((right[2], left[0])),
            }
            actual_edges = {frozenset(edge) for edge in cross_edges}
            if len(cross_edges) != 1 or not actual_edges <= allowed_edges:
                raise ValueError(
                    "encoded groups may connect only through endpoint interfaces: "
                    f"group {left_index}={left} and group {right_index}={right} "
                    f"must use either {left[2]}-{right[0]} or {right[2]}-{left[0]}, "
                    f"but found {cross_edges}."
                )
