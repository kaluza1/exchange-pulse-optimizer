from __future__ import annotations

from dataclasses import replace

from qiskit import QuantumCircuit

from .costs import DEFAULT_GATE_FIDELITIES, DEFAULT_PULSE_COSTS, estimate_operation_fidelity, total_operation_error_cost
from .cpsat_optimizer import CpSatPulseOptimizer
from .layout import interaction_weighted_layout
from .optimizer import PulsePlan, PulseStep
from .topology import EncodedTopology


class WindowedCpSatOptimizer:
    """
    Scalable routing CP-SAT mode with a fixed heuristic initial layout.

    The full circuit is split into sequential windows. Each window is solved by
    the existing CP-SAT optimizer with the same objective, and the final layout
    of one window becomes the fixed initial layout of the next.
    """

    def __init__(
        self,
        topology: EncodedTopology,
        pulse_costs: dict[str, int] | None = None,
        gate_fidelities: dict[str, float] | None = None,
        error_scale: int = 1_000_000,
        window_size: int = 20,
        window_layers: int | None = None,
        time_limit_seconds: float | None = 30.0,
        makespan_weight: int = 1000,
        swap_weight: int = 10,
        error_weight: int = 1,
        layout_decay: float = 0.98,
        layout_local_search_rounds: int = 2,
        num_search_workers: int | None = None,
    ) -> None:
        if window_size <= 0:
            raise ValueError("window_size must be positive.")
        if window_layers is not None and window_layers <= 0:
            raise ValueError("window_layers must be positive.")
        if num_search_workers is not None and num_search_workers <= 0:
            raise ValueError("num_search_workers must be positive.")

        self._topology = topology
        self._pulse_costs = DEFAULT_PULSE_COSTS | (pulse_costs or {})
        self._gate_fidelities = DEFAULT_GATE_FIDELITIES | (gate_fidelities or {})
        self._error_scale = error_scale
        self._window_size = window_size
        self._window_layers = window_layers
        self._time_limit_seconds = time_limit_seconds
        self._makespan_weight = makespan_weight
        self._swap_weight = swap_weight
        self._error_weight = error_weight
        self._layout_decay = layout_decay
        self._layout_local_search_rounds = layout_local_search_rounds
        self._num_search_workers = num_search_workers

    def optimize(self, qc: QuantumCircuit) -> PulsePlan:
        if qc.num_qubits > self._topology.num_encoded_slots:
            raise ValueError(
                "encoded layout has fewer 3-dot groups than circuit qubits: "
                f"qasm_qubits={qc.num_qubits}, encoded_groups={self._topology.num_encoded_slots}."
            )

        current_layout = interaction_weighted_layout(
            qc,
            self._topology.to_slot_graph(),
            decay=self._layout_decay,
            local_search_rounds=self._layout_local_search_rounds,
        )
        initial_layout = dict(current_layout)

        steps: list[PulseStep] = []
        total_pulses = 0
        schedule_duration = 0
        statuses: list[str] = []
        layer_offset = 0

        for window_index, window_qc in enumerate(self._windows(qc)):
            max_layers = self._window_layers or max(1, len(window_qc.data) + 2 * self._topology.num_encoded_slots)
            optimizer = CpSatPulseOptimizer(
                self._topology,
                pulse_costs=self._pulse_costs,
                gate_fidelities=self._gate_fidelities,
                max_layers=max_layers,
                time_limit_seconds=self._time_limit_seconds,
                makespan_weight=self._makespan_weight,
                swap_weight=self._swap_weight,
                error_weight=self._error_weight,
                error_scale=self._error_scale,
                initial_layout=current_layout,
                num_search_workers=self._num_search_workers,
            )
            plan = optimizer.optimize(window_qc)
            statuses.append(f"window{window_index}:{plan.solver_status}")
            total_pulses += plan.pulse_count
            schedule_duration += plan.schedule_duration or 0

            for step in plan.steps:
                layer = None if step.layer is None else step.layer + layer_offset
                steps.append(replace(step, layer=layer))
            if plan.steps:
                used_layers = [step.layer for step in plan.steps if step.layer is not None]
                if used_layers:
                    layer_offset += max(used_layers) + 1

            current_layout = self._slot_layout_from_public(plan.final_layout)

        return PulsePlan(
            initial_layout=self._public_layout(initial_layout),
            final_layout=self._public_layout(current_layout),
            pulse_count=total_pulses,
            steps=steps,
            solver_status=";".join(statuses),
            schedule_duration=schedule_duration,
            estimated_fidelity=estimate_operation_fidelity(
                tuple(step.name for step in steps),
                self._gate_fidelities,
            ),
            total_error_cost=total_operation_error_cost(
                tuple(step.name for step in steps),
                self._gate_fidelities,
                self._error_scale,
            ),
        )

    def _windows(self, qc: QuantumCircuit) -> list[QuantumCircuit]:
        operations = [inst for inst in qc.data if inst.operation.name != "barrier"]
        windows = []
        for start in range(0, len(operations), self._window_size):
            window = QuantumCircuit(qc.num_qubits, qc.num_clbits)
            for inst in operations[start : start + self._window_size]:
                qubits = [qc.find_bit(q).index for q in inst.qubits]
                clbits = [qc.find_bit(c).index for c in inst.clbits]
                window.append(inst.operation, qubits, clbits)
            windows.append(window)
        return windows

    def _slot_layout_from_public(self, public_layout: dict[int, tuple]) -> dict[int, int]:
        dots_to_slot = {dots: slot for slot, dots in enumerate(self._topology.dot_groups)}
        return {qubit: dots_to_slot[tuple(dots)] for qubit, dots in public_layout.items()}

    def _public_layout(self, layout: dict[int, int]) -> dict[int, tuple]:
        return {qubit: self._topology.dot_groups[layout[qubit]] for qubit in sorted(layout)}
