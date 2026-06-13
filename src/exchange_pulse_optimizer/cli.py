from __future__ import annotations

import argparse
import json

from .cpsat_optimizer import CpSatPulseOptimizer
from .optimizer import PulseCountOptimizer, read_openqasm, read_topology_json
from .plotting import plot_topology


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize initial layout and encoded-SWAP routing for exchange-only pulse count.",
    )
    parser.add_argument("qasm", help="OpenQASM 2.0 input file")
    parser.add_argument("topology", help="Topology JSON file")
    parser.add_argument("--cx-cost", type=int, default=28)
    parser.add_argument("--cz-cost", type=int, default=28)
    parser.add_argument("--swap-cost", type=int, default=15)
    parser.add_argument("--max-layouts", type=int, default=40320)
    parser.add_argument("--solver", choices=("heuristic", "cp-sat"), default="heuristic")
    parser.add_argument("--sat-layers", type=int, default=None, help="Maximum macro layers for CP-SAT mode")
    parser.add_argument("--time-limit", type=float, default=30.0, help="CP-SAT time limit in seconds")
    parser.add_argument("--plot-topology", help="Save a PNG/SVG/PDF image of the physical dot graph")
    parser.add_argument("--no-encoded-edges", action="store_true", help="Do not draw dashed encoded-slot adjacency edges")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    qc = read_openqasm(args.qasm)
    topology = read_topology_json(args.topology)
    if args.plot_topology:
        plot_topology(topology, args.plot_topology, show_encoded_edges=not args.no_encoded_edges)

    pulse_costs = {
        "cx": args.cx_cost,
        "cz": args.cz_cost,
        "swap": args.swap_cost,
    }
    if args.solver == "cp-sat":
        optimizer = CpSatPulseOptimizer(
            topology,
            pulse_costs=pulse_costs,
            max_layers=args.sat_layers,
            time_limit_seconds=args.time_limit,
        )
    else:
        optimizer = PulseCountOptimizer(
            topology,
            pulse_costs=pulse_costs,
            max_layouts=args.max_layouts,
        )
    plan = optimizer.optimize(qc)

    if args.json:
        print(
            json.dumps(
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
        )
        return

    print("== initial layout ==")
    print(plan.initial_layout)
    print("== pulse plan ==")
    for index, step in enumerate(plan.steps):
        print(
            f"{index:02d}: layer={step.layer!s:>3s} {step.name:12s} "
            f"logical={step.logical_qubits} "
            f"dots={step.dot_groups} "
            f"pulses={step.pulse_count}"
        )
    print("== result ==")
    print(f"total_pulses = {plan.pulse_count}")
    if plan.schedule_duration is not None:
        print(f"schedule_duration = {plan.schedule_duration}")
    print(f"final_layout = {plan.final_layout}")
    if plan.solver_status is not None:
        print(f"solver_status = {plan.solver_status}")


if __name__ == "__main__":
    main()
