from __future__ import annotations

import networkx as nx
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Gate

from exchange_pulse_optimizer.cpsat_optimizer import (
    CpSatPulseOptimizer,
    cp_model,
)
from exchange_pulse_optimizer.topology import EncodedTopology


pytestmark = pytest.mark.skipif(
    cp_model is None,
    reason="OR-Tools native library is unavailable or blocked",
)


_EQUAL_TWO_QUBIT_COSTS = {
    "cx": 1,
    "cxswap": 1,
    "cz": 1,
    "czswap": 1,
    "swap": 1,
}


def _line_topology() -> EncodedTopology:
    graph = nx.Graph()
    graph.add_edges_from(
        [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 4),
            (4, 5),
            (5, 6),
            (6, 7),
            (7, 8),
        ]
    )
    return EncodedTopology(
        physical_graph=graph,
        dot_groups=((0, 1, 2), (3, 4, 5), (6, 7, 8)),
    )


def _swap_minimizing_optimizer(
    topology: EncodedTopology,
    *,
    max_layers: int,
    initial_layout: dict[int, int],
) -> CpSatPulseOptimizer:
    return CpSatPulseOptimizer(
        topology,
        pulse_costs=_EQUAL_TWO_QUBIT_COSTS,
        max_layers=max_layers,
        time_limit_seconds=5,
        makespan_weight=0,
        swap_weight=1,
        error_weight=0,
        initial_layout=initial_layout,
        num_search_workers=1,
    )


def test_optional_czswap_eliminates_a_standalone_routing_swap() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(3)
    circuit.cz(0, 1)
    circuit.swap(0, 2)

    plan = _swap_minimizing_optimizer(
        topology,
        max_layers=3,
        initial_layout={0: 0, 1: 1, 2: 2},
    ).optimize(circuit)

    assert [step.name for step in plan.steps] == ["czswap", "swap"]
    assert all(step.name != "encoded_swap" for step in plan.steps)
    assert plan.pulse_count == 2
    assert plan.final_layout == {
        0: topology.dot_groups[2],
        1: topology.dot_groups[0],
        2: topology.dot_groups[1],
    }


def test_explicit_czswap_always_updates_the_layout() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(2)
    circuit.append(Gate("czswap", 2, []), [0, 1])

    plan = _swap_minimizing_optimizer(
        topology,
        max_layers=1,
        initial_layout={0: 0, 1: 1},
    ).optimize(circuit)

    assert [step.name for step in plan.steps] == ["czswap"]
    assert plan.pulse_count == 1
    assert plan.final_layout == {
        0: topology.dot_groups[1],
        1: topology.dot_groups[0],
    }
