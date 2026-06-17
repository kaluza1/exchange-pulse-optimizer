from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import networkx as nx

from .topology import EncodedTopology


def plot_topology(
    topology: EncodedTopology,
    output_path: str | Path,
    show_encoded_edges: bool = True,
) -> None:
    """
    Save a physical dot graph image with encoded 3-dot groups highlighted.

    If physical graph nodes have a ``pos`` attribute, it is used as the layout.
    Otherwise, a spring layout is generated.
    """

    graph = topology.physical_graph
    pos = _node_positions(graph)
    group_by_dot = _group_by_dot(topology.dot_groups)
    colors = _palette(max(1, len(topology.dot_groups)))
    node_colors = [colors[group_by_dot.get(node, -1) % len(colors)] for node in graph.nodes]

    plt.figure(figsize=(8, 6))
    nx.draw_networkx_edges(graph, pos, edge_color="#9aa0a6", width=1.8)

    if show_encoded_edges:
        _draw_encoded_edges(topology, pos)

    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colors,
        edgecolors="#1f2937",
        linewidths=1.2,
        node_size=650,
    )
    nx.draw_networkx_labels(graph, pos, font_size=10, font_weight="bold")
    _draw_group_labels(topology, pos)

    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()


def _node_positions(graph: nx.Graph) -> dict[Any, tuple[float, float]]:
    raw_pos = nx.get_node_attributes(graph, "pos")
    if raw_pos and len(raw_pos) == graph.number_of_nodes():
        return {node: tuple(value) for node, value in raw_pos.items()}
    return nx.spring_layout(graph, seed=7)


def _group_by_dot(dot_groups: tuple[tuple[Any, ...], ...]) -> dict[Any, int]:
    group_by_dot = {}
    for group_index, dots in enumerate(dot_groups):
        for dot in dots:
            group_by_dot[dot] = group_index
    return group_by_dot


def _draw_encoded_edges(topology: EncodedTopology, pos: dict[Any, tuple[float, float]]) -> None:
    slot_graph = topology.to_slot_graph()
    for left, right in slot_graph.edges:
        left_center = _center(pos, topology.dot_groups[left])
        right_center = _center(pos, topology.dot_groups[right])
        plt.plot(
            [left_center[0], right_center[0]],
            [left_center[1], right_center[1]],
            color="#111827",
            linestyle="--",
            linewidth=2.2,
            alpha=0.55,
            zorder=0,
        )


def _draw_group_labels(topology: EncodedTopology, pos: dict[Any, tuple[float, float]]) -> None:
    for index, dots in enumerate(topology.dot_groups):
        x, y = _center(pos, dots)
        plt.text(
            x,
            y + 0.16,
            f"qslot {index}",
            ha="center",
            va="bottom",
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#d1d5db", "alpha": 0.9},
        )


def _center(pos: dict[Any, tuple[float, float]], nodes: tuple[Any, ...]) -> tuple[float, float]:
    xs = [pos[node][0] for node in nodes]
    ys = [pos[node][1] for node in nodes]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _palette(count: int) -> list[str]:
    base = [
        "#93c5fd",
        "#86efac",
        "#fca5a5",
        "#fde68a",
        "#c4b5fd",
        "#67e8f9",
        "#f9a8d4",
        "#fdba74",
    ]
    return [base[index % len(base)] for index in range(count)]
