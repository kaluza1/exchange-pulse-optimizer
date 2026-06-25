from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .costs import DEFAULT_GATE_FIDELITIES, ONE_QUBIT_GATES
from .cpsat_optimizer import CpSatPulseOptimizer
from .large_heuristic import LargeHeuristicOptimizer
from .layout import interaction_weighted_layout
from .optimizer import PulseCountOptimizer
from .plotting import plot_topology
from .qasm import read_openqasm, transpile_to_supported_gates
from .topology import read_topology_json
from .windowed_cpsat import WindowedCpSatOptimizer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize initial layout and encoded-SWAP routing for exchange-only pulse count.",
    )
    parser.add_argument("qasm", help="OpenQASM 2.0 input file")
    parser.add_argument("topology", help="Topology JSON file")
    parser.add_argument("--cx-cost", type=int, default=28)
    parser.add_argument("--cxswap-cost", type=int, default=31)
    parser.add_argument("--cz-cost", type=int, default=26)
    parser.add_argument("--swap-cost", type=int, default=15)
    parser.add_argument("--cx-fidelity", type=float, default=DEFAULT_GATE_FIDELITIES["cx"])
    parser.add_argument("--cxswap-fidelity", type=float, default=DEFAULT_GATE_FIDELITIES["cxswap"])
    parser.add_argument("--cz-fidelity", type=float, default=DEFAULT_GATE_FIDELITIES["cz"])
    parser.add_argument("--swap-fidelity", type=float, default=DEFAULT_GATE_FIDELITIES["swap"])
    parser.add_argument("--oneq-fidelity", type=float, default=DEFAULT_GATE_FIDELITIES["h"])
    parser.add_argument("--max-layouts", type=int, default=40320)
    parser.add_argument(
        "--layout-strategy",
        choices=("exhaustive", "interaction"),
        default="exhaustive",
        help="Initial layout strategy. 'interaction' uses weighted 2Q interactions.",
    )
    parser.add_argument(
        "--layout-decay",
        type=float,
        default=0.98,
        help="Decay factor for interaction-layout weights. Earlier 2Q gates get larger weights.",
    )
    parser.add_argument(
        "--layout-local-search-rounds",
        type=int,
        default=2,
        help="Number of pair-swap local-search rounds for interaction layout.",
    )
    parser.add_argument("--solver", choices=("heuristic", "large-heuristic", "cp-sat", "window-cp-sat"), default="heuristic")
    parser.add_argument("--sat-layers", type=int, default=None, help="Maximum macro layers for CP-SAT mode")
    parser.add_argument("--time-limit", type=float, default=30.0, help="CP-SAT time limit in seconds")
    parser.add_argument("--cp-sat-workers", type=int, default=None, help="Number of OR-Tools CP-SAT search workers. Defaults to OR-Tools automatic setting.")
    parser.add_argument("--makespan-weight", type=int, default=1000, help="CP-SAT objective weight for schedule_duration. Use 0 to disable.")
    parser.add_argument("--swap-weight", type=int, default=10, help="CP-SAT objective weight for inserted encoded_swap count. Use 0 to disable.")
    parser.add_argument("--error-weight", type=int, default=1, help="CP-SAT objective weight for total_error_cost. Use 0 to disable.")
    parser.add_argument("--error-scale", type=int, default=1_000_000, help="Scale used for integer -log(fidelity) error costs.")
    parser.add_argument("--window-size", type=int, default=20, help="Number of circuit operations per window-cp-sat subproblem.")
    parser.add_argument("--window-sat-layers", type=int, default=None, help="Maximum macro layers per window-cp-sat subproblem.")
    parser.add_argument("--large-front-layer-size", type=int, default=24, help="Number of ready 2Q gates considered by large-heuristic.")
    parser.add_argument("--large-lookahead-gates", type=int, default=32, help="Number of future 2Q gates scored by large-heuristic.")
    parser.add_argument("--large-path-candidates", type=int, default=3, help="Number of shortest path candidates used by large-heuristic.")
    parser.add_argument("--large-layout-local-search-rounds", type=int, default=0, help="Pair-swap local-search rounds for large-heuristic initial layout.")
    parser.add_argument("--no-large-cxswap", action="store_true", help="Disable automatic CXSWAP selection in large-heuristic mode.")
    parser.add_argument("--plot-topology", help="Save a PNG/SVG/PDF image of the physical dot graph")
    parser.add_argument("--no-encoded-edges", action="store_true", help="Do not draw dashed encoded-slot adjacency edges")
    parser.add_argument(
        "--output-dir",
        help="Write the optimizer output to this directory as <qasm_stem>_output.txt or .json.",
    )
    parser.add_argument(
        "--qiskit-optimization-level",
        type=int,
        choices=(0, 1, 2, 3),
        default=1,
        help="Qiskit transpiler optimization level used to decompose input gates into supported gates.",
    )
    parser.add_argument(
        "--no-qiskit-transpile",
        action="store_true",
        help="Skip the initial Qiskit decomposition pass and require the input QASM to use supported gates.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    qc = read_openqasm(args.qasm)
    if not args.no_qiskit_transpile:
        qc = transpile_to_supported_gates(qc, optimization_level=args.qiskit_optimization_level)
    topology = read_topology_json(args.topology)
    if args.plot_topology:
        plot_topology(topology, args.plot_topology, show_encoded_edges=not args.no_encoded_edges)

    pulse_costs = {
        "cx": args.cx_cost,
        "cxswap": args.cxswap_cost,
        "cz": args.cz_cost,
        "swap": args.swap_cost,
    }
    gate_fidelities = {
        "cx": args.cx_fidelity,
        "cxswap": args.cxswap_fidelity,
        "cz": args.cz_fidelity,
        "swap": args.swap_fidelity,
        **{gate: args.oneq_fidelity for gate in ONE_QUBIT_GATES},
    }
    if args.solver == "cp-sat":
        fixed_initial_layout = None
        if args.layout_strategy == "interaction":
            fixed_initial_layout = interaction_weighted_layout(
                qc,
                topology.to_slot_graph(),
                decay=args.layout_decay,
                local_search_rounds=args.layout_local_search_rounds,
            )
        optimizer = CpSatPulseOptimizer(
            topology,
            pulse_costs=pulse_costs,
            gate_fidelities=gate_fidelities,
            max_layers=args.sat_layers,
            time_limit_seconds=args.time_limit,
            makespan_weight=args.makespan_weight,
            swap_weight=args.swap_weight,
            error_weight=args.error_weight,
            error_scale=args.error_scale,
            initial_layout=fixed_initial_layout,
            num_search_workers=args.cp_sat_workers,
        )
    elif args.solver == "window-cp-sat":
        optimizer = WindowedCpSatOptimizer(
            topology,
            pulse_costs=pulse_costs,
            gate_fidelities=gate_fidelities,
            error_scale=args.error_scale,
            window_size=args.window_size,
            window_layers=args.window_sat_layers,
            time_limit_seconds=args.time_limit,
            makespan_weight=args.makespan_weight,
            swap_weight=args.swap_weight,
            error_weight=args.error_weight,
            layout_decay=args.layout_decay,
            layout_local_search_rounds=args.layout_local_search_rounds,
            num_search_workers=args.cp_sat_workers,
        )
    elif args.solver == "large-heuristic":
        optimizer = LargeHeuristicOptimizer(
            topology,
            pulse_costs=pulse_costs,
            gate_fidelities=gate_fidelities,
            error_scale=args.error_scale,
            layout_decay=args.layout_decay,
            layout_local_search_rounds=args.large_layout_local_search_rounds,
            front_layer_size=args.large_front_layer_size,
            lookahead_gates=args.large_lookahead_gates,
            path_candidates=args.large_path_candidates,
            use_cxswap=not args.no_large_cxswap,
        )
    else:
        optimizer = PulseCountOptimizer(
            topology,
            pulse_costs=pulse_costs,
            gate_fidelities=gate_fidelities,
            error_scale=args.error_scale,
            max_layouts=args.max_layouts,
            layout_strategy=args.layout_strategy,
            layout_decay=args.layout_decay,
            layout_local_search_rounds=args.layout_local_search_rounds,
        )
    start_time = time.perf_counter()
    plan = optimizer.optimize(qc)
    plan.elapsed_seconds = time.perf_counter() - start_time
    sorted_steps = _steps_sorted_by_layer(plan.steps)

    if args.json:
        output = json.dumps(
            {
                "initial_layout": plan.initial_layout,
                "final_layout": plan.final_layout,
                "pulse_count": plan.pulse_count,
                "schedule_duration": plan.schedule_duration,
                "estimated_fidelity": plan.estimated_fidelity,
                "total_error_cost": plan.total_error_cost,
                "elapsed_seconds": plan.elapsed_seconds,
                "solver_status": plan.solver_status,
                "steps": [
                    {
                        "name": step.name,
                        "logical_qubits": step.logical_qubits,
                        "dot_groups": step.dot_groups,
                        "pulse_count": step.pulse_count,
                        "layer": step.layer,
                    }
                    for step in sorted_steps
                ],
            },
            indent=2,
        )
        print(output)
        _write_output_file(args.output_dir, args.qasm, output, ".json")
        return

    lines = ["== initial layout ==", str(plan.initial_layout), "== pulse plan =="]
    for index, step in enumerate(sorted_steps):
        lines.append(
            f"{index:02d}: layer={step.layer!s:>3s} {step.name:12s} "
            f"logical={step.logical_qubits} "
            f"dots={step.dot_groups} "
            f"pulses={step.pulse_count}"
        )
    lines.append("== result ==")
    lines.append(f"total_pulses = {plan.pulse_count}")
    if plan.schedule_duration is not None:
        lines.append(f"schedule_duration = {plan.schedule_duration}")
    if plan.estimated_fidelity is not None:
        lines.append(f"estimated_fidelity = {plan.estimated_fidelity:.8g}")
    if plan.total_error_cost is not None:
        lines.append(f"total_error_cost = {plan.total_error_cost}")
    if plan.elapsed_seconds is not None:
        lines.append(f"elapsed_seconds = {plan.elapsed_seconds:.3f}")
    lines.append(f"final_layout = {plan.final_layout}")
    if plan.solver_status is not None:
        lines.append(f"solver_status = {plan.solver_status}")

    output = "\n".join(lines)
    print(output)
    _write_output_file(args.output_dir, args.qasm, output, ".txt")


def _write_output_file(output_dir: str | None, qasm_path: str, text: str, suffix: str) -> None:
    if output_dir is None:
        return

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{Path(qasm_path).stem}_output{suffix}"
    output_path.write_text(text + "\n", encoding="utf-8")
    print(f"saved_output = {output_path}")


def _steps_sorted_by_layer(steps: list) -> list:
    indexed_steps = list(enumerate(steps))
    return [
        step
        for _index, step in sorted(
            indexed_steps,
            key=lambda item: (
                item[1].layer is None,
                item[1].layer if item[1].layer is not None else 0,
                item[0],
            ),
        )
    ]


if __name__ == "__main__":
    main()


