from __future__ import annotations

import networkx as nx

from exchange_pulse_optimizer.large_heuristic import (
    LargeHeuristicOptimizer,
)
from exchange_pulse_optimizer.topology import EncodedTopology


def test_shortest_path_candidates_are_cached(monkeypatch) -> None:
    graph = nx.grid_2d_graph(3, 3)
    graph = nx.convert_node_labels_to_integers(graph)
    topology = EncodedTopology(
        physical_graph=graph,
        dot_groups=tuple((node,) for node in graph.nodes),
    )
    optimizer = LargeHeuristicOptimizer(
        topology,
        path_candidates=4,
        workers=1,
    )
    original = nx.shortest_simple_paths
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(nx, "shortest_simple_paths", counted)
    first = optimizer._shortest_path_candidates(1, 7)
    second = optimizer._shortest_path_candidates(1, 7)

    assert first == second
    assert calls == 1
