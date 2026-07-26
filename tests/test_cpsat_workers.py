from __future__ import annotations

from pathlib import Path

from qiskit import QuantumCircuit

from exchange_pulse_optimizer.cpsat_optimizer import CpSatPulseOptimizer
from exchange_pulse_optimizer.topology import read_topology_json
from exchange_pulse_optimizer.windowed_cpsat import WindowedCpSatOptimizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _small_circuit() -> QuantumCircuit:
    circuit = QuantumCircuit(4)
    circuit.h(0)
    circuit.h(1)
    circuit.cx(0, 3)
    circuit.cx(1, 2)
    return circuit


def test_cp_sat_accepts_explicit_worker_count() -> None:
    topology = read_topology_json(PROJECT_ROOT / "examples/grid2x2_topology.json")

    plan = CpSatPulseOptimizer(
        topology,
        max_layers=4,
        time_limit_seconds=5,
        num_search_workers=2,
    ).optimize(_small_circuit())

    assert plan.solver_status == "OPTIMAL"


def test_window_cp_sat_passes_explicit_worker_count_to_each_window() -> None:
    topology = read_topology_json(PROJECT_ROOT / "examples/grid2x2_topology.json")

    plan = WindowedCpSatOptimizer(
        topology,
        window_size=2,
        window_layers=3,
        time_limit_seconds=5,
        num_search_workers=2,
    ).optimize(_small_circuit())

    assert plan.solver_status == "window0:OPTIMAL;window1:OPTIMAL"
