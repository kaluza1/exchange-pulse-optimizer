from __future__ import annotations

from pathlib import Path

import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Measure, Reset

from exchange_pulse_optimizer.costs import (
    DEFAULT_CZSWAP_FIDELITY,
    DEFAULT_CZSWAP_PULSE_COST,
    DEFAULT_GATE_FIDELITIES,
)
from exchange_pulse_optimizer.large_heuristic import LargeHeuristicOptimizer
from exchange_pulse_optimizer.optimizer import PulseStep
from exchange_pulse_optimizer.topology import read_topology_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _line_topology():
    return read_topology_json(PROJECT_ROOT / "examples/line3_topology.json")


def test_fixed_initial_layout_allows_unused_slots_and_is_copied() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(2)
    circuit.cx(0, 1)
    fixed_layout = {0: 1, 1: 2}

    optimizer = LargeHeuristicOptimizer(
        topology,
        initial_layout=fixed_layout,
        use_cxswap=False,
    )
    fixed_layout[0] = 0
    plan = optimizer.optimize(circuit)

    assert plan.initial_layout == {
        0: topology.dot_groups[1],
        1: topology.dot_groups[2],
    }


@pytest.mark.parametrize(
    ("initial_layout", "message"),
    [
        ({0: 0}, "missing logical qubits"),
        ({0: 0, 1: 1, 2: 2}, "unknown logical qubits"),
        ({0: 0, 1: 0}, "unique slots"),
        ({0: 0, 1: 99}, "unknown slots"),
    ],
)
def test_fixed_initial_layout_validation(
    initial_layout: dict[int, int],
    message: str,
) -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(2)

    with pytest.raises(ValueError, match=message):
        LargeHeuristicOptimizer(
            topology,
            initial_layout=initial_layout,
        ).optimize(circuit)


def test_czswap_is_optional_and_updates_the_layout() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(3)
    circuit.cz(0, 1)
    circuit.cx(0, 2)
    initial_layout = {0: 0, 1: 1, 2: 2}

    direct_plan = LargeHeuristicOptimizer(
        topology,
        initial_layout=initial_layout,
        use_cxswap=False,
    ).optimize(circuit)
    fused_plan = LargeHeuristicOptimizer(
        topology,
        initial_layout=initial_layout,
        use_cxswap=False,
        use_czswap=True,
    ).optimize(circuit)

    assert [step.name for step in direct_plan.steps] == [
        "cz",
        "encoded_swap",
        "cx",
    ]
    assert [step.name for step in fused_plan.steps] == ["czswap", "cx"]
    assert fused_plan.final_layout == {
        0: topology.dot_groups[1],
        1: topology.dot_groups[0],
        2: topology.dot_groups[2],
    }
    assert fused_plan.pulse_count == DEFAULT_CZSWAP_PULSE_COST + 28
    assert fused_plan.estimated_fidelity == pytest.approx(
        DEFAULT_CZSWAP_FIDELITY * DEFAULT_GATE_FIDELITIES["cx"]
    )


@pytest.mark.parametrize(
    ("gate_name", "fused_name", "costs"),
    [
        ("cx", "cxswap", (1, 2)),
        ("cx", "cxswap", (28, 31)),
        ("cz", "czswap", (1, 2)),
        ("cz", "czswap", (26, 41)),
    ],
)
def test_fused_swap_does_not_win_an_equal_distance_tie_from_cost_bias(
    gate_name: str,
    fused_name: str,
    costs: tuple[int, int],
) -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(2)
    getattr(circuit, gate_name)(0, 1)
    direct_cost, fused_cost = costs

    plan = LargeHeuristicOptimizer(
        topology,
        pulse_costs={
            gate_name: direct_cost,
            fused_name: fused_cost,
        },
        initial_layout={0: 0, 1: 1},
        use_cxswap=True,
        use_czswap=True,
    ).optimize(circuit)

    assert [step.name for step in plan.steps] == [gate_name]
    assert plan.pulse_count == direct_cost


@pytest.mark.parametrize(
    ("gate_name", "fused_name"),
    [
        ("cx", "cxswap"),
        ("cz", "czswap"),
    ],
)
def test_fused_swap_sees_token_future_past_global_lookahead(
    gate_name: str,
    fused_name: str,
) -> None:
    topology = read_topology_json(
        PROJECT_ROOT / "examples/grid3x3_topology.json"
    )
    circuit = QuantumCircuit(5)
    getattr(circuit, gate_name)(0, 1)
    for _ in range(33):
        circuit.cx(3, 4)
    circuit.cx(0, 2)

    plan = LargeHeuristicOptimizer(
        topology,
        pulse_costs={
            gate_name: 1,
            fused_name: 2,
        },
        initial_layout={0: 0, 1: 1, 2: 2, 3: 3, 4: 4},
        lookahead_gates=32,
        use_cxswap=gate_name == "cx",
        use_czswap=gate_name == "cz",
    ).optimize(circuit)

    assert plan.steps[0].name == fused_name
    assert plan.steps[0].logical_qubits == (0, 1)


@pytest.mark.parametrize(
    ("gate_name", "fused_name"),
    [
        ("cx", "cxswap"),
        ("cz", "czswap"),
    ],
)
@pytest.mark.parametrize(
    ("beneficial_future_gates", "expected_first_gate"),
    [
        (1, "direct"),
        (2, "fused"),
    ],
)
def test_weighted_fusion_requires_recovering_the_extra_cost(
    gate_name: str,
    fused_name: str,
    beneficial_future_gates: int,
    expected_first_gate: str,
) -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(3)
    getattr(circuit, gate_name)(0, 1)
    circuit.cx(0, 1)
    for _ in range(beneficial_future_gates):
        circuit.cx(0, 2)

    plan = LargeHeuristicOptimizer(
        topology,
        pulse_costs={
            gate_name: 1,
            fused_name: 2,
            "swap": 1,
        },
        initial_layout={0: 0, 1: 1, 2: 2},
        use_cxswap=gate_name == "cx",
        use_czswap=gate_name == "cz",
        fusion_objective="weighted",
    ).optimize(circuit)

    expected_name = (
        gate_name if expected_first_gate == "direct" else fused_name
    )
    assert plan.steps[0].name == expected_name


@pytest.mark.parametrize(
    ("gate_name", "fused_name"),
    [
        ("cx", "cxswap"),
        ("cz", "czswap"),
    ],
)
def test_weighted_fusion_exact_score_tie_keeps_direct(
    gate_name: str,
    fused_name: str,
) -> None:
    circuit = QuantumCircuit(3)
    getattr(circuit, gate_name)(0, 1)
    circuit.cx(0, 2)

    plan = LargeHeuristicOptimizer(
        _line_topology(),
        pulse_costs={
            gate_name: 1,
            fused_name: 2,
            "swap": 1,
        },
        initial_layout={0: 0, 1: 1, 2: 2},
        use_cxswap=gate_name == "cx",
        use_czswap=gate_name == "cz",
        fusion_objective="weighted",
    ).optimize(circuit)

    assert plan.steps[0].name == gate_name


def test_unit_macro_cost_weighted_and_distance_policies_are_equivalent() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(3)
    circuit.cz(0, 1)
    circuit.cx(0, 2)
    costs = {
        "cx": 1,
        "cz": 1,
        "swap": 1,
        "cxswap": 1,
        "czswap": 1,
    }

    plans = [
        LargeHeuristicOptimizer(
            topology,
            pulse_costs=costs,
            initial_layout={0: 0, 1: 1, 2: 2},
            use_cxswap=True,
            use_czswap=True,
            fusion_objective=objective,
        ).optimize(circuit)
        for objective in ("weighted", "distance")
    ]

    assert [step.name for step in plans[0].steps] == ["czswap", "cx"]
    assert [step.name for step in plans[0].steps] == [
        step.name for step in plans[1].steps
    ]
    assert plans[0].final_layout == plans[1].final_layout


def test_fusion_objective_validation() -> None:
    with pytest.raises(ValueError, match="fusion_objective"):
        LargeHeuristicOptimizer(
            _line_topology(),
            fusion_objective="unsupported",
        )


def test_parallel_scheduler_preserves_logical_resource_order() -> None:
    topology = _line_topology()
    optimizer = LargeHeuristicOptimizer(topology)
    dot0, dot1, dot2 = topology.dot_groups
    steps = [
        PulseStep("h", (0,), (dot0,), 3),
        PulseStep("cx", (0, 1), (dot0, dot1), 28),
        PulseStep("h", (1,), (dot2,), 3),
    ]

    scheduled, _duration = optimizer._greedy_parallel_schedule(steps)

    assert [step.layer for step in scheduled] == [0, 1, 2]


def test_parallel_scheduler_preserves_physical_resource_order() -> None:
    topology = _line_topology()
    optimizer = LargeHeuristicOptimizer(topology)
    dot0, dot1, dot2 = topology.dot_groups
    steps = [
        PulseStep("h", (0,), (dot0,), 3),
        PulseStep("encoded_swap", (1,), (dot0, dot1), 15),
        PulseStep("h", (2,), (dot1, dot2), 3),
    ]

    scheduled, _duration = optimizer._greedy_parallel_schedule(steps)

    assert [step.layer for step in scheduled] == [0, 1, 2]


def test_barrier_measure_reset_trace_and_global_order() -> None:
    topology = _line_topology()
    circuit = QuantumCircuit(3, 1)
    circuit.cx(0, 1)
    circuit.barrier(label="cycle=0,tick=1")
    circuit.append(Measure(label="cycle=0,tick=2"), [0], [0])
    circuit.append(Reset(label="cycle=0,tick=2"), [1])
    circuit.cx(1, 2)

    plan = LargeHeuristicOptimizer(
        topology,
        pulse_costs={"measure": 7, "reset": 5},
        initial_layout={0: 0, 1: 1, 2: 2},
        use_cxswap=False,
    ).optimize(circuit)

    assert [step.name for step in plan.steps] == [
        "cx",
        "barrier",
        "measure",
        "reset",
        "cx",
    ]
    assert [step.source_gate_index for step in plan.steps] == [0, 1, 2, 3, 4]
    assert [step.source_label for step in plan.steps] == [
        None,
        "cycle=0,tick=1",
        "cycle=0,tick=2",
        "cycle=0,tick=2",
        None,
    ]
    assert [step.layer for step in plan.steps] == [0, 1, 2, 2, 3]
    assert plan.pulse_count == 68
    assert plan.schedule_duration == 63
    assert plan.initial_slot_layout == {0: 0, 1: 1, 2: 2}
    assert plan.final_slot_layout == plan.initial_slot_layout


def test_barrier_advances_the_global_schedule_floor() -> None:
    topology = _line_topology()
    optimizer = LargeHeuristicOptimizer(topology)
    dot0, dot1, dot2 = topology.dot_groups
    steps = [
        PulseStep("h", (0,), (dot0,), 3),
        PulseStep("h", (0,), (dot0,), 3),
        PulseStep("barrier", (0,), (dot0,), 0),
        PulseStep("h", (2,), (dot2,), 3),
        PulseStep("h", (1,), (dot1,), 3),
    ]

    scheduled, _duration = optimizer._greedy_parallel_schedule(steps)

    assert [step.layer for step in scheduled] == [0, 1, 2, 3, 3]


def test_final_slot_layout_is_reentrant_as_the_next_initial_layout() -> None:
    topology = _line_topology()
    first_circuit = QuantumCircuit(2)
    first_circuit.cx(0, 1)
    first_plan = LargeHeuristicOptimizer(
        topology,
        initial_layout={0: 0, 1: 2},
        use_cxswap=False,
    ).optimize(first_circuit)

    assert first_plan.final_slot_layout is not None
    assert first_plan.final_slot_layout != first_plan.initial_slot_layout
    assert {
        step.source_gate_index
        for step in first_plan.steps
    } == {0}

    second_circuit = QuantumCircuit(2)
    second_circuit.cx(0, 1)
    second_plan = LargeHeuristicOptimizer(
        topology,
        initial_layout=first_plan.final_slot_layout,
        use_cxswap=False,
    ).optimize(second_circuit)

    assert second_plan.initial_slot_layout == first_plan.final_slot_layout
    assert second_plan.final_slot_layout == first_plan.final_slot_layout
