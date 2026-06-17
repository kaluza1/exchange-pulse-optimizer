from __future__ import annotations

import networkx as nx
from qiskit import QuantumCircuit


def build_interaction_weights(qc: QuantumCircuit, decay: float = 0.98) -> dict[tuple[int, int], float]:
    if not 0.0 < decay <= 1.0:
        raise ValueError("layout decay must be in the range (0, 1].")

    weights: dict[tuple[int, int], float] = {}
    two_qubit_index = 0
    for inst in qc.data:
        qids = tuple(qc.find_bit(q).index for q in inst.qubits)
        if len(qids) != 2:
            continue

        a, b = sorted(qids)
        weights[(a, b)] = weights.get((a, b), 0.0) + decay**two_qubit_index
        two_qubit_index += 1

    return weights


def interaction_layout_score(
    layout: dict[int, int],
    weights: dict[tuple[int, int], float],
    distances: dict[int, dict[int, int]],
) -> float:
    score = 0.0
    for (a, b), weight in weights.items():
        if a not in layout or b not in layout:
            continue
        distance = distances[layout[a]][layout[b]]
        score += weight * max(0, distance - 1)
    return score


def interaction_weighted_layout(
    qc: QuantumCircuit,
    slot_graph: nx.Graph,
    decay: float = 0.98,
    local_search_rounds: int = 2,
) -> dict[int, int]:
    if qc.num_qubits > slot_graph.number_of_nodes():
        raise ValueError(
            "encoded layout has fewer slots than circuit qubits: "
            f"qasm_qubits={qc.num_qubits}, encoded_slots={slot_graph.number_of_nodes()}."
        )

    weights = build_interaction_weights(qc, decay=decay)
    distances = dict(nx.all_pairs_shortest_path_length(slot_graph))
    weighted_degree = {q: 0.0 for q in range(qc.num_qubits)}
    for (a, b), weight in weights.items():
        weighted_degree[a] += weight
        weighted_degree[b] += weight

    centrality = nx.closeness_centrality(slot_graph)
    slots = sorted(slot_graph.nodes, key=lambda slot: (-centrality[slot], slot))
    qubits = sorted(range(qc.num_qubits), key=lambda q: (-weighted_degree[q], q))

    layout: dict[int, int] = {}
    free_slots = set(slots)
    for qubit in qubits:
        best_slot = min(
            free_slots,
            key=lambda slot: (
                _placement_increment(qubit, slot, layout, weights, distances),
                -centrality[slot],
                slot,
            ),
        )
        layout[qubit] = best_slot
        free_slots.remove(best_slot)

    layout = _improve_layout_by_pair_swaps(layout, weights, distances, local_search_rounds)
    return {qubit: layout[qubit] for qubit in sorted(layout)}


def _placement_increment(
    qubit: int,
    slot: int,
    partial_layout: dict[int, int],
    weights: dict[tuple[int, int], float],
    distances: dict[int, dict[int, int]],
) -> float:
    cost = 0.0
    for placed_qubit, placed_slot in partial_layout.items():
        key = tuple(sorted((qubit, placed_qubit)))
        weight = weights.get(key, 0.0)
        if weight == 0.0:
            continue
        cost += weight * max(0, distances[slot][placed_slot] - 1)
    return cost


def _improve_layout_by_pair_swaps(
    layout: dict[int, int],
    weights: dict[tuple[int, int], float],
    distances: dict[int, dict[int, int]],
    rounds: int,
) -> dict[int, int]:
    improved_layout = dict(layout)
    best_score = interaction_layout_score(improved_layout, weights, distances)
    qubits = sorted(improved_layout)

    for _round in range(max(0, rounds)):
        improved = False
        for left_index, left in enumerate(qubits):
            for right in qubits[left_index + 1 :]:
                candidate = dict(improved_layout)
                candidate[left], candidate[right] = candidate[right], candidate[left]
                score = interaction_layout_score(candidate, weights, distances)
                if score + 1e-12 < best_score:
                    improved_layout = candidate
                    best_score = score
                    improved = True
        if not improved:
            break

    return improved_layout
