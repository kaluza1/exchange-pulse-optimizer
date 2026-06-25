from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qiskit import QuantumCircuit

from .costs import (
    DEFAULT_GATE_FIDELITIES,
    DEFAULT_PULSE_COSTS,
    estimate_operation_fidelity,
    operation_error_cost,
    total_operation_error_cost,
)
from .optimizer import PulsePlan, PulseStep
from .topology import EncodedTopology

try:
    from ortools.sat.python import cp_model
except ModuleNotFoundError:  # pragma: no cover
    cp_model = None


@dataclass(frozen=True)
class _Gate:
    index: int
    name: str
    qubits: tuple[int, ...]
    cost: int


def _commutes_for_ordering(left: _Gate, right: _Gate) -> bool:
    return left.name == "cz" and right.name == "cz"


class CpSatPulseOptimizer:
    """
    Bounded exact optimizer for layout, routing swaps, and parallel macro layers.

    The solution is exact within the chosen layer horizon. Increase
    max_layers when the model is infeasible.
    """

    def __init__(
        self,
        topology: EncodedTopology,
        pulse_costs: dict[str, int] | None = None,
        gate_fidelities: dict[str, float] | None = None,
        max_layers: int | None = None,
        time_limit_seconds: float | None = 30.0,
        makespan_weight: int = 1000,
        swap_weight: int = 10,
        error_weight: int = 1,
        error_scale: int = 1_000_000,
        initial_layout: dict[int, int] | None = None,
        num_search_workers: int | None = None,
    ) -> None:
        if cp_model is None:
            raise ModuleNotFoundError("ortools is required for CP-SAT mode. Install with: py -m pip install ortools")
        if topology.num_encoded_slots == 0:
            raise ValueError("encoded layout must have at least one 3-dot group.")

        self._encoded_topology = topology
        self._slot_graph = topology.to_slot_graph()
        self._slots = list(self._slot_graph.nodes)
        self._edges = [tuple(edge) for edge in self._slot_graph.edges]
        self._pulse_costs = DEFAULT_PULSE_COSTS | (pulse_costs or {})
        self._gate_fidelities = DEFAULT_GATE_FIDELITIES | (gate_fidelities or {})
        self._max_layers = max_layers
        self._time_limit_seconds = time_limit_seconds
        self._makespan_weight = makespan_weight
        self._swap_weight = swap_weight
        self._error_weight = error_weight
        self._error_scale = error_scale
        self._initial_layout = initial_layout
        self._num_search_workers = num_search_workers
        for name, value in (
            ("makespan_weight", makespan_weight),
            ("swap_weight", swap_weight),
            ("error_weight", error_weight),
            ("error_scale", error_scale),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative.")
        if error_scale == 0 and error_weight > 0:
            raise ValueError("error_scale must be positive when error_weight is non-zero.")
        if num_search_workers is not None and num_search_workers <= 0:
            raise ValueError("num_search_workers must be positive.")

    def optimize(self, qc: QuantumCircuit) -> PulsePlan:
        if qc.num_qubits > self._encoded_topology.num_encoded_slots:
            raise ValueError(
                "encoded layout has fewer 3-dot groups than circuit qubits: "
                f"qasm_qubits={qc.num_qubits}, encoded_groups={self._encoded_topology.num_encoded_slots}."
            )

        gates = self._extract_gates(qc)
        layers = self._max_layers or max(1, len(gates) + 2 * len(self._slots))
        model = cp_model.CpModel()

        qubits = list(range(qc.num_qubits))
        slots = self._slots
        slot_index = {slot: i for i, slot in enumerate(slots)}
        edges = [(slot_index[a], slot_index[b]) for a, b in self._edges]

        occ = {
            (t, q, s): model.NewBoolVar(f"occ_t{t}_q{q}_s{s}")
            for t in range(layers + 1)
            for q in qubits
            for s in range(len(slots))
        }
        run = {
            (g.index, t): model.NewBoolVar(f"run_g{g.index}_t{t}")
            for g in gates
            for t in range(layers)
        }
        cxswap_run = {
            (g.index, t): model.NewBoolVar(f"cxswap_g{g.index}_t{t}")
            for g in gates
            if g.name in ("cx", "cxswap")
            for t in range(layers)
        }
        swp = {
            (e_idx, t): model.NewBoolVar(f"swap_e{e_idx}_t{t}")
            for e_idx in range(len(edges))
            for t in range(layers)
        }

        for t in range(layers + 1):
            for q in qubits:
                model.AddExactlyOne(occ[t, q, s] for s in range(len(slots)))
            for s in range(len(slots)):
                model.AddAtMostOne(occ[t, q, s] for q in qubits)

        if self._initial_layout is not None:
            for q in qubits:
                if q not in self._initial_layout:
                    raise ValueError(f"fixed initial layout is missing logical qubit {q}.")
                slot = self._initial_layout[q]
                if slot not in slot_index:
                    raise ValueError(f"fixed initial layout uses unknown slot {slot} for logical qubit {q}.")
                model.Add(occ[0, q, slot_index[slot]] == 1)

        for g in gates:
            model.AddExactlyOne(run[g.index, t] for t in range(layers))
            for t in range(layers):
                if g.name == "cx":
                    model.Add(cxswap_run[g.index, t] <= run[g.index, t])
                elif g.name == "cxswap":
                    model.Add(cxswap_run[g.index, t] == run[g.index, t])

        gate_layer = {}
        for g in gates:
            layer = model.NewIntVar(0, layers - 1, f"layer_g{g.index}")
            model.Add(layer == sum(t * run[g.index, t] for t in range(layers)))
            gate_layer[g.index] = layer

        for prev, nxt in self._dependencies(gates, qc.num_qubits):
            model.Add(gate_layer[prev] < gate_layer[nxt])

        for t in range(layers):
            for s in range(len(slots)):
                incident = [swp[e_idx, t] for e_idx, (a, b) in enumerate(edges) if a == s or b == s]
                if incident:
                    model.AddAtMostOne(incident)

        move = {}
        for t in range(layers):
            for q in qubits:
                for e_idx, (a, b) in enumerate(edges):
                    ab = model.NewBoolVar(f"move_ab_t{t}_q{q}_e{e_idx}")
                    ba = model.NewBoolVar(f"move_ba_t{t}_q{q}_e{e_idx}")
                    model.AddBoolAnd([occ[t, q, a], swp[e_idx, t]]).OnlyEnforceIf(ab)
                    model.AddBoolOr([occ[t, q, a].Not(), swp[e_idx, t].Not(), ab])
                    model.AddBoolAnd([occ[t, q, b], swp[e_idx, t]]).OnlyEnforceIf(ba)
                    model.AddBoolOr([occ[t, q, b].Not(), swp[e_idx, t].Not(), ba])
                    move[(t, q, e_idx, a, b)] = ab
                    move[(t, q, e_idx, b, a)] = ba

        adjacent_pair = self._adjacent_pair_vars(model, occ, gates, layers, len(slots), edges)
        for g in gates:
            if len(g.qubits) == 2:
                for t in range(layers):
                    model.Add(sum(adjacent_pair[g.index, t]) >= run[g.index, t])

        cxswap_pair = self._cxswap_pair_vars(
            model,
            occ,
            gates,
            layers,
            edges,
            cxswap_run,
        )
        for g in gates:
            if g.name in ("cx", "cxswap"):
                for t in range(layers):
                    model.Add(sum(cxswap_pair[g.index, t].values()) == cxswap_run[g.index, t])

        logical_swap_pair = self._logical_swap_pair_vars(model, occ, gates, layers, edges, run)
        for g in gates:
            if g.name == "swap":
                for t in range(layers):
                    model.Add(sum(logical_swap_pair[g.index, t].values()) == run[g.index, t])

        for t in range(layers):
            for q in qubits:
                for s in range(len(slots)):
                    outgoing = []
                    incoming = []
                    for e_idx, (a, b) in enumerate(edges):
                        if a == s:
                            outgoing.append(move[(t, q, e_idx, a, b)])
                            incoming.append(move[(t, q, e_idx, b, a)])
                        elif b == s:
                            outgoing.append(move[(t, q, e_idx, b, a)])
                            incoming.append(move[(t, q, e_idx, a, b)])

                    for gate in gates:
                        if gate.name not in ("cx", "cxswap"):
                            continue
                        qa, qb = gate.qubits
                        for (src, dst), var in cxswap_pair[gate.index, t].items():
                            if q == qa:
                                if src == s:
                                    outgoing.append(var)
                                if dst == s:
                                    incoming.append(var)
                            elif q == qb:
                                if dst == s:
                                    outgoing.append(var)
                                if src == s:
                                    incoming.append(var)

                    for gate in gates:
                        if gate.name != "swap":
                            continue
                        qa, qb = gate.qubits
                        for (src, dst), var in logical_swap_pair[gate.index, t].items():
                            if q == qa:
                                if src == s:
                                    outgoing.append(var)
                                if dst == s:
                                    incoming.append(var)
                            elif q == qb:
                                if dst == s:
                                    outgoing.append(var)
                                if src == s:
                                    incoming.append(var)

                    model.Add(occ[t + 1, q, s] == occ[t, q, s] - sum(outgoing) + sum(incoming))

        for t in range(layers):
            for q in qubits:
                q_gates = [run[g.index, t] for g in gates if q in g.qubits]
                q_moves = [
                    var
                    for (tt, qq, _e_idx, _src, _dst), var in move.items()
                    if tt == t and qq == q
                ]
                model.Add(sum(q_gates + q_moves) <= 1)

        layer_duration = []
        for t in range(layers):
            duration = model.NewIntVar(0, max(self._pulse_costs.values()), f"duration_t{t}")
            for g in gates:
                model.Add(duration >= g.cost).OnlyEnforceIf(run[g.index, t])
                if g.name in ("cx", "cxswap"):
                    model.Add(duration >= self._pulse_costs["cxswap"]).OnlyEnforceIf(cxswap_run[g.index, t])
            for e_idx in range(len(edges)):
                model.Add(duration >= self._pulse_costs["swap"]).OnlyEnforceIf(swp[e_idx, t])
            layer_duration.append(duration)

        swap_count = sum(swp[e_idx, t] for e_idx in range(len(edges)) for t in range(layers))
        total_error_cost = self._total_error_cost_expr(gates, layers, run, cxswap_run, swp, len(edges))
        objective_terms = []
        if self._makespan_weight:
            objective_terms.append(self._makespan_weight * sum(layer_duration))
        if self._swap_weight:
            objective_terms.append(self._swap_weight * swap_count)
        if self._error_weight:
            objective_terms.append(self._error_weight * total_error_cost)
        model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        if self._time_limit_seconds is not None:
            solver.parameters.max_time_in_seconds = self._time_limit_seconds
        if self._num_search_workers is not None:
            solver.parameters.num_search_workers = self._num_search_workers
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ValueError(f"CP-SAT found no solution within {layers} layers. Try increasing --sat-layers.")

        return self._build_plan(
            solver,
            occ,
            run,
            swp,
            gates,
            layers,
            slots,
            edges,
            layer_duration,
            cxswap_run,
            qubits,
            solver.StatusName(status),
        )

    def _extract_gates(self, qc: QuantumCircuit) -> list[_Gate]:
        gates = []
        for index, inst in enumerate(qc.data):
            name = inst.operation.name
            qids = tuple(qc.find_bit(q).index for q in inst.qubits)
            if name == "barrier":
                continue
            cost = self._pulse_costs.get(name)
            if cost is None:
                raise ValueError(f"unsupported gate for pulse optimization: {name}")
            if len(qids) > 2:
                raise ValueError(f"only 1q and 2q gates are supported: {name}")
            gates.append(_Gate(len(gates), name, qids, cost))
        return gates

    def _dependencies(self, gates: list[_Gate], num_qubits: int) -> list[tuple[int, int]]:
        deps = []
        for later_index, later in enumerate(gates):
            later_qubits = set(later.qubits)
            for earlier in gates[:later_index]:
                if later_qubits.isdisjoint(earlier.qubits):
                    continue
                if _commutes_for_ordering(earlier, later):
                    continue
                deps.append((earlier.index, later.index))
        return deps

    def _total_error_cost_expr(self, gates, layers, run, cxswap_run, swp, num_edges):
        terms = []
        cx_error = operation_error_cost("cx", self._gate_fidelities, self._error_scale)
        cxswap_error = operation_error_cost("cxswap", self._gate_fidelities, self._error_scale)
        swap_error = operation_error_cost("swap", self._gate_fidelities, self._error_scale)

        for gate in gates:
            if gate.name == "cx":
                for t in range(layers):
                    terms.append(cx_error * run[gate.index, t])
                    terms.append((cxswap_error - cx_error) * cxswap_run[gate.index, t])
            elif gate.name == "cxswap":
                for t in range(layers):
                    terms.append(cxswap_error * run[gate.index, t])
            else:
                gate_error = operation_error_cost(gate.name, self._gate_fidelities, self._error_scale)
                for t in range(layers):
                    terms.append(gate_error * run[gate.index, t])

        for e_idx in range(num_edges):
            for t in range(layers):
                terms.append(swap_error * swp[e_idx, t])

        return sum(terms)

    def _adjacent_pair_vars(self, model, occ, gates, layers, num_slots, edges):
        adjacent_pair = {}
        directed_edges = edges + [(b, a) for a, b in edges]
        for gate in gates:
            for t in range(layers):
                vars_for_gate = []
                if len(gate.qubits) == 2:
                    qa, qb = gate.qubits
                    for a, b in directed_edges:
                        var = model.NewBoolVar(f"adj_g{gate.index}_t{t}_s{a}_{b}")
                        model.AddBoolAnd([occ[t, qa, a], occ[t, qb, b]]).OnlyEnforceIf(var)
                        model.AddBoolOr([occ[t, qa, a].Not(), occ[t, qb, b].Not(), var])
                        vars_for_gate.append(var)
                adjacent_pair[gate.index, t] = vars_for_gate
        return adjacent_pair

    def _cxswap_pair_vars(self, model, occ, gates, layers, edges, cxswap_run):
        cxswap_pair = {}
        directed_edges = edges + [(b, a) for a, b in edges]
        for gate in gates:
            if gate.name not in ("cx", "cxswap"):
                continue
            qa, qb = gate.qubits
            for t in range(layers):
                pair_vars = {}
                for a, b in directed_edges:
                    var = model.NewBoolVar(f"cxswap_pair_g{gate.index}_t{t}_s{a}_{b}")
                    model.AddBoolAnd([cxswap_run[gate.index, t], occ[t, qa, a], occ[t, qb, b]]).OnlyEnforceIf(var)
                    model.AddBoolOr(
                        [
                            cxswap_run[gate.index, t].Not(),
                            occ[t, qa, a].Not(),
                            occ[t, qb, b].Not(),
                            var,
                        ]
                    )
                    pair_vars[(a, b)] = var
                cxswap_pair[gate.index, t] = pair_vars
        return cxswap_pair

    def _logical_swap_pair_vars(self, model, occ, gates, layers, edges, run):
        swap_pair = {}
        directed_edges = edges + [(b, a) for a, b in edges]
        for gate in gates:
            if gate.name != "swap":
                continue
            qa, qb = gate.qubits
            for t in range(layers):
                pair_vars = {}
                for a, b in directed_edges:
                    var = model.NewBoolVar(f"logical_swap_pair_g{gate.index}_t{t}_s{a}_{b}")
                    model.AddBoolAnd([run[gate.index, t], occ[t, qa, a], occ[t, qb, b]]).OnlyEnforceIf(var)
                    model.AddBoolOr(
                        [
                            run[gate.index, t].Not(),
                            occ[t, qa, a].Not(),
                            occ[t, qb, b].Not(),
                            var,
                        ]
                    )
                    pair_vars[(a, b)] = var
                swap_pair[gate.index, t] = pair_vars
        return swap_pair

    def _build_plan(self, solver, occ, run, swp, gates, layers, slots, edges, layer_duration, cxswap_run, qubits, status_name) -> PulsePlan:
        initial_slot_layout = self._slot_layout_from_occ(solver, occ, 0, qubits, slots)
        final_slot_layout = self._slot_layout_from_occ(solver, occ, layers, qubits, slots)
        steps: list[PulseStep] = []
        pulse_count = 0
        used_layers = [t for t in range(layers) if solver.Value(layer_duration[t]) > 0]
        compact_layer = {layer: index for index, layer in enumerate(used_layers)}

        for t in range(layers):
            if solver.Value(layer_duration[t]) == 0:
                continue
            for gate in gates:
                if solver.Value(run[gate.index, t]):
                    is_cxswap = gate.name in ("cx", "cxswap") and solver.Value(cxswap_run[gate.index, t])
                    step_name = "cxswap" if is_cxswap else gate.name
                    step_cost = self._pulse_costs["cxswap"] if is_cxswap else gate.cost
                    dot_groups = tuple(self._dots_for_qubit_at(solver, occ, t, q, slots) for q in gate.qubits)
                    steps.append(PulseStep(step_name, gate.qubits, dot_groups, step_cost, compact_layer[t]))
                    pulse_count += step_cost
            for e_idx, (a, b) in enumerate(edges):
                if solver.Value(swp[e_idx, t]):
                    involved = []
                    for q in range(len(initial_slot_layout)):
                        if solver.Value(occ[t, q, a]) or solver.Value(occ[t, q, b]):
                            involved.append(q)
                    dot_groups = (self._encoded_topology.dot_groups[slots[a]], self._encoded_topology.dot_groups[slots[b]])
                    steps.append(PulseStep("encoded_swap", tuple(involved), dot_groups, self._pulse_costs["swap"], compact_layer[t]))
                    pulse_count += self._pulse_costs["swap"]

        return PulsePlan(
            initial_layout=self._public_layout(initial_slot_layout),
            final_layout=self._public_layout(final_slot_layout),
            pulse_count=pulse_count,
            steps=steps,
            solver_status=status_name,
            schedule_duration=sum(solver.Value(duration) for duration in layer_duration),
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

    def _slot_layout_from_occ(self, solver, occ, t, qubits, slots) -> dict[int, int]:
        layout = {}
        for q in qubits:
            for s, slot in enumerate(slots):
                if solver.Value(occ[t, q, s]):
                    layout[q] = slot
                    break
        return layout

    def _dots_for_qubit_at(self, solver, occ, t, q, slots) -> tuple[Any, ...]:
        for s, slot in enumerate(slots):
            if solver.Value(occ[t, q, s]):
                return self._encoded_topology.dot_groups[slot]
        raise ValueError("internal CP-SAT decode error.")

    def _public_layout(self, layout: dict[int, int]) -> dict[int, tuple[Any, ...]]:
        return {qubit: self._encoded_topology.dot_groups[layout[qubit]] for qubit in sorted(layout)}
