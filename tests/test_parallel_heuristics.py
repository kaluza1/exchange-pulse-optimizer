from __future__ import annotations

from pathlib import Path

from qiskit import QuantumCircuit

import exchange_pulse_optimizer.optimizer as optimizer_module
from exchange_pulse_optimizer.large_heuristic import LargeHeuristicOptimizer
from exchange_pulse_optimizer.optimizer import PulseCountOptimizer
from exchange_pulse_optimizer.topology import read_topology_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _routing_circuit() -> QuantumCircuit:
    circuit = QuantumCircuit(5)
    for left in range(5):
        for right in range(left + 1, 5):
            circuit.cx(left, right)
    return circuit


def test_exhaustive_layout_parallel_result_matches_serial(monkeypatch) -> None:
    topology = read_topology_json(PROJECT_ROOT / "examples/grid3x3_topology.json")
    circuit = _routing_circuit()

    serial = PulseCountOptimizer(
        topology,
        max_layouts=40,
        workers=1,
    ).optimize(circuit)
    monkeypatch.setattr(optimizer_module, "_MIN_LAYOUT_WORK_PER_WORKER", 0)
    parallel = PulseCountOptimizer(
        topology,
        max_layouts=40,
        workers=2,
    ).optimize(circuit)

    assert parallel == serial


def test_large_heuristic_parallel_scoring_matches_serial() -> None:
    topology = read_topology_json(PROJECT_ROOT / "examples/grid3x3_topology.json")
    circuit = _routing_circuit()

    serial = LargeHeuristicOptimizer(topology, workers=1).optimize(circuit)
    parallel = LargeHeuristicOptimizer(topology, workers=2).optimize(circuit)

    assert parallel == serial
