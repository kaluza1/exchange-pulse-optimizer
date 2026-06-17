from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cpsat_optimizer import CpSatPulseOptimizer
from .layout import interaction_weighted_layout
from .optimizer import PulseCountOptimizer
from .plotting import plot_topology
from .qasm import read_openqasm, transpile_to_supported_gates
from .topology import read_topology_json


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
    parser.add_argument("--solver", choices=("heuristic", "cp-sat"), default="heuristic")
    parser.add_argument("--sat-layers", type=int, default=None, help="Maximum macro layers for CP-SAT mode")
    parser.add_argument("--time-limit", type=float, default=30.0, help="CP-SAT time limit in seconds")
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
            max_layers=args.sat_layers,
            time_limit_seconds=args.time_limit,
            initial_layout=fixed_initial_layout,
        )
    else:
        optimizer = PulseCountOptimizer(
            topology,
            pulse_costs=pulse_costs,
            max_layouts=args.max_layouts,
            layout_strategy=args.layout_strategy,
            layout_decay=args.layout_decay,
            layout_local_search_rounds=args.layout_local_search_rounds,
        )
    plan = optimizer.optimize(qc)

    if args.json:
        output = json.dumps(
            {
                "initial_layout": plan.initial_layout,
                "final_layout": plan.final_layout,
                "pulse_count": plan.pulse_count,
                "schedule_duration": plan.schedule_duration,
                "solver_status": plan.solver_status,
                "steps": [
                    {
                        "name": step.name,
                        "logical_qubits": step.logical_qubits,
                        "dot_groups": step.dot_groups,
                        "pulse_count": step.pulse_count,
                        "layer": step.layer,
                    }
                    for step in plan.steps
                ],
            },
            indent=2,
        )
        print(output)
        _write_output_file(args.output_dir, args.qasm, output, ".json")
        return

    lines = ["== initial layout ==", str(plan.initial_layout), "== pulse plan =="]
    for index, step in enumerate(plan.steps):
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


if __name__ == "__main__":
    main()
