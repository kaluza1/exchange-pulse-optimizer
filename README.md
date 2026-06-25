# Exchange Pulse Optimizer

[Japanese README](README_ja.md)

Exchange Pulse Optimizer is an experimental command-line tool for optimizing exchange-only encoded-qubit layouts and pulse schedules.

It takes:

- an OpenQASM 2.0 circuit,
- a physical quantum-dot connectivity graph,
- and 3-dot encoded-qubit groups,

then optimizes the initial layout, encoded-SWAP routing, macro-gate layers, and parallel execution.

## Model

Each OpenQASM qubit is treated as one exchange-only encoded qubit made from three physical quantum dots.

Example:

```text
logical q[0] = dots (0, 1, 2)
logical q[1] = dots (3, 4, 5)
logical q[2] = dots (6, 7, 8)
```

The topology JSON uses physical dots as `nodes`. The `encoded_qubits` field defines the 3-dot groups. If two encoded groups have a physical edge between any pair of their dots, the optimizer treats the two encoded groups as adjacent.

The encoded-qubit adjacency graph must be either a 1D line or a 2D square-lattice patch, such as 2x2 or 3x3. Other topologies are rejected at load time.

For every adjacent pair of encoded groups, only endpoint-to-endpoint interfaces are allowed. For groups `A=(a0,a1,a2)` and `B=(b0,b1,b2)`, the only valid inter-group physical edges are `a2-b0` or `b2-a0`. This represents linear six-dot interfaces such as `a0-a1-a2-b0-b1-b2` or `b0-b1-b2-a0-a1-a2`. Middle-dot interfaces and multiple inter-group edges are rejected.

## Macro Pulse Count Model

These values are approximate **pulse counts** for each macro operation. They are not direct error rates or fidelities.

```text
cx     : 28
cxswap : 31
cz     : 26
swap   : 15
h/x    : 3
rz/z   : 1
measure : 0
```

The `cx=28`, `cxswap=31`, `cz=26`, and `swap=15` defaults are macro-level pulse-count approximations. The two-qubit pulse counts are based on the exchange-only pulse-sequence results discussed in Chadwick et al., "Short two-qubit pulse sequences for exchange-only spin qubits in 2D layouts" (arXiv:2412.14918). They can be changed from the CLI.

## Fidelity/Error Model

The optimizer also reports an estimated circuit fidelity by multiplying per-operation fidelities in the emitted pulse plan.

```text
estimated_fidelity = product_i F(operation_i)
```

For example, if the output plan contains `h`, `cx`, `encoded_swap`, and `cz`, the estimate is `F(h) * F(cx) * F(swap) * F(cz)`. `encoded_swap` uses the `swap` fidelity. The default macro-operation fidelities are:

```text
1q gates : 0.9986
cx       : 0.9755
cz       : 0.9589
cxswap   : 0.9738
swap     : 0.9903
```

The one-qubit value uses the average logical one-qubit Clifford fidelity from Broz et al., "Demonstration of an always-on exchange-only spin qubit," Nature Communications 17, 4794 (2026). For this experimental tool, non-Clifford one-qubit gates such as `t`, `tdg`, `rx`, `ry`, and `rz` are assigned the same one-qubit fidelity.

The two-qubit values are set as follows:

```text
cx error     = 1 - 0.9755 = 0.0245
swap error   = 1 - 0.9903 = 0.0097
cz error     = 0.0245 * (0.062 / 0.037) ~= 0.0411
cxswap error = 0.0245 * 1.07 ~= 0.0262
```

`cz` is a linear estimate using the LCCZ/CNOT error ratio from Weinstein et al. (Nature, 2023). `cxswap` uses the approximation that CXSWAP has 1.07 times the CX error, based on the average pulse-length overhead reported by Chadwick et al., Physical Review A 111, 052616 (2025). Routing `encoded_swap` operations use the `swap` fidelity.

For CP-SAT, the corresponding integer `total_error_cost` is included in the objective by default:

```text
total_error_cost = sum_i round(error_scale * -log(F(operation_i)))
```

The reported `estimated_fidelity` is still the product of operation fidelities.

## Qiskit Input Decomposition

Before optimization, the OpenQASM input is decomposed by Qiskit's `transpile` into the gate set supported by this tool. Input gates such as `u`, `u3`, and `ccx` are normally converted into `cx`, `cz`, `swap`, `h`, `x`, `y`, `z`, `s`, `sdg`, `t`, `tdg`, `rx`, `ry`, and `rz`.

This Qiskit decomposition pass is enabled by default. `--qiskit-optimization-level` controls how much Qiskit tries to simplify the circuit while decomposing it:

```text
0: mostly decomposition only. The main goal is to express input gates in the
   requested basis. This is fast, but it usually does not reduce gate count or
   depth much.

1: light optimization; default. Qiskit applies simple cleanups such as obvious
   cancellations and basic one-qubit gate simplification. This is a balanced
   default for conversion time and circuit reduction.

2: stronger optimization. Qiskit tries more simplification and resynthesis
   passes, so gate count or depth may improve compared with level 1, at the
   cost of more transpilation time.

3: strongest optimization. Qiskit is more aggressive about reducing the circuit.
   This can help for small circuits, but for large circuits the decomposition
   pass itself may take noticeably longer.
```

The default is `--qiskit-optimization-level 1`. For larger circuits, levels 2 and 3 may spend more time in Qiskit before this tool starts pulse/layout optimization. Use `--no-qiskit-transpile` if you want to skip this pass and require the input QASM to already contain only supported gates.

## Source Layout

```text
src/exchange_pulse_optimizer/
  costs.py            # Pulse costs and Qiskit basis gates
  qasm.py             # OpenQASM loading and Qiskit decomposition
  topology.py         # Encoded topology model and JSON validation
  layout.py           # Initial-layout heuristic
  optimizer.py        # Heuristic routing optimizer and pulse-plan types
  large_heuristic.py  # Front-layer/lookahead router for larger circuits
  cpsat_optimizer.py  # CP-SAT routing, scheduling, cxswap, and swap support
  windowed_cpsat.py # Windowed routing CP-SAT
  cli.py              # Command-line interface
  plotting.py         # Topology plotting
```

## Installation

```powershell
py -m pip install -e .
```

## Usage

Run the heuristic optimizer:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json
```

Run without installing the package:

```powershell
$env:PYTHONPATH="src"
py -m exchange_pulse_optimizer.cli examples\sample.qasm examples\line3_topology.json
```

Print JSON output:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --json
```

Save a physical-dot topology figure:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --plot-topology topology.png
```

The figure shows the physical dot graph, colors each 3-dot encoded-qubit group, and draws dashed encoded-slot adjacency edges.

Change macro costs:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --cx-cost 28 --cxswap-cost 31 --cz-cost 26 --swap-cost 15
```

Change the Qiskit decomposition optimization level:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --qiskit-optimization-level 2
```

Use the interaction-weighted initial-layout heuristic:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --layout-strategy interaction
```

Run CP-SAT on the 2x2 example:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --solver cp-sat --sat-layers 8 --time-limit 10 --output-dir output
```

The sample output is saved in `output/sample_cpsat_output.txt`.

Run the 4x4 example with heuristic initial layout + routing CP-SAT:

```powershell
exchange-pulse-opt examples\sample_4x4_interaction_20q.qasm examples\grid4x4_topology.json --solver cp-sat --layout-strategy interaction --sat-layers 40 --time-limit 900 --output-dir output
```

The sample output is saved in `output/sample_4x4_interaction_20q_output.txt`.

Run the windowed routing CP-SAT mode:

```powershell
exchange-pulse-opt examples\sample_4x4_interaction_20q.qasm examples\grid4x4_topology.json --solver window-cp-sat --window-size 8 --window-sat-layers 20 --time-limit 60 --output-dir output
```

`window-cp-sat` fixes an interaction-weighted initial layout, splits the circuit into small windows, and solves each window with the existing routing CP-SAT objective.

Run the larger front-layer heuristic:

```powershell
exchange-pulse-opt examples\sample_500q_500g_random.qasm examples\line500_topology.json --solver large-heuristic --output-dir output
```

This mode is intended for larger benchmark circuits where CP-SAT is too heavy. It reports `elapsed_seconds` in the same output format as the other solvers.

Run a 7x7 square-grid example with 49 logical qubits and 50 two-qubit gates:

```powershell
exchange-pulse-opt examples\sample_49q_50x2q_scattered_gridlocal.qasm examples\grid7x7_topology.json --solver window-cp-sat --window-size 2 --window-sat-layers 4 --time-limit 60 --cp-sat-workers 1 --output-dir output --no-qiskit-transpile
```

In this example, every window is solved with `OPTIMAL` status, and the output is saved to
`output/sample_49q_50x2q_scattered_gridlocal_output.txt`.
In one local run, `total_pulses = 1701`, `schedule_duration = 1506`,
and `estimated_fidelity = 0.15001536`.

Run a 30x30 square-grid benchmark with 900 logical qubits and 1000 fully random two-qubit gates:

```powershell
exchange-pulse-opt examples\sample_900q_1000x2q_random.qasm examples\grid30x30_topology.json --solver large-heuristic --output-dir output --no-qiskit-transpile
```

The output is saved to `output/sample_900q_1000x2q_random_output.txt`.
In one local run, `total_pulses = 125502`, `schedule_duration = 1752`,
`estimated_fidelity = 3.0630504e-43`, and `elapsed_seconds = 1679.932`.

Save a 3x3 topology figure:

```powershell
exchange-pulse-opt examples\sample_3x3_cpsat.qasm examples\grid3x3_topology.json --plot-topology grid3x3.png
```

## CLI Options

Positional arguments:

```text
qasm
  OpenQASM 2.0 input file.

topology
  JSON file containing the physical dot graph and encoded_qubits.
```

Execution mode:

```text
--solver heuristic|large-heuristic|cp-sat|window-cp-sat
  Selects the optimizer.
  heuristic       : sequential shortest-path routing heuristic.
  large-heuristic : front-layer/lookahead routing with greedy parallel scheduling.
  cp-sat          : CP-SAT optimization for routing, layers, and parallel execution.
  window-cp-sat   : Fixed initial layout plus windowed routing CP-SAT.
  Default: heuristic

--layout-strategy exhaustive|interaction
  Selects how the initial layout is chosen.
  exhaustive  : In CP-SAT mode, initial layout is optimized inside the same
                CP-SAT model. In heuristic mode, candidate layouts are enumerated.
  interaction : Fixes an interaction-weighted heuristic initial layout first.
  Default: exhaustive
```

CP-SAT options:

```text
--sat-layers N
  Maximum number of macro layers for routing CP-SAT.
  If too small, the solver may find no feasible solution.

--time-limit SEC
  Time limit for the routing CP-SAT model. In `window-cp-sat`, this applies per window.
  Default: 30

--cp-sat-workers N
  Number of OR-Tools CP-SAT search workers. Use this to choose how many CPU cores the solver may use.
  If omitted, OR-Tools uses its automatic setting.

--window-size N
  Number of circuit operations per `window-cp-sat` subproblem. Default: 20

--window-sat-layers N
  Maximum macro layers per `window-cp-sat` subproblem. If omitted, a window-local estimate is used.

--makespan-weight N
  Objective weight for schedule_duration. Default: 1000
  Use 0 to remove schedule_duration from the objective.

--swap-weight N
  Objective weight for inserted encoded_swap count. Default: 10
  Use 0 to remove encoded_swap count from the objective.

--error-weight N
  Objective weight for total_error_cost. Default: 1
  Use 0 to remove total_error_cost from the objective.

--error-scale N
  Integer scale for -log(fidelity) error costs. Default: 1000000
```

Large-heuristic options:

```text
--large-front-layer-size N
  Number of ready two-qubit gates considered at each routing step.
  Default: 24

--large-lookahead-gates N
  Number of future two-qubit gates used when scoring swap/cxswap candidates.
  Default: 32

--large-path-candidates N
  Number of shortest path candidates considered between a two-qubit pair.
  Default: 3

--large-layout-local-search-rounds N
  Pair-swap local-search rounds for the large-heuristic initial layout.
  Default: 0, to keep preprocessing fast for hundreds of qubits.

--no-large-cxswap
  Disables automatic CXSWAP selection for input CX gates.
```

Initial-layout heuristic options:

```text
--layout-decay VALUE
  Decay factor for later two-qubit gates in the initial-layout objective:
  w(i,j) += layout_decay ^ k.
  Values closer to 1.0 weight later gates more evenly; smaller values emphasize
  earlier gates more strongly.
  Default: 0.98

--layout-local-search-rounds N
  Number of pair-swap local-search rounds for `--layout-strategy interaction`.
  Larger values can improve the initial layout but increase preprocessing time.
  Default: 2

--max-layouts N
  Maximum number of initial layouts enumerated by heuristic mode with
  `--layout-strategy exhaustive`.
  Default: 40320
```

Qiskit decomposition:

```text
--qiskit-optimization-level 0|1|2|3
  Qiskit transpiler optimization level for decomposing input QASM into supported gates.
  Default: 1

--no-qiskit-transpile
  Disables the initial Qiskit decomposition pass.
  Use this only when the input QASM already contains supported gates.
```

Macro pulse costs:

```text
--cx-cost N
  Macro pulse cost for cx. Default: 28

--cxswap-cost N
  Macro pulse cost for cxswap. Default: 31

--cz-cost N
  Macro pulse cost for cz. Default: 26

--swap-cost N
  Macro pulse cost for encoded swap / input swap. Default: 15
```

Gate fidelity estimates:

```text
--oneq-fidelity F
  Fidelity used for supported one-qubit gates. Default: 0.9986

--cx-fidelity F
  Fidelity used for cx. Default: 0.9755

--cxswap-fidelity F
  Fidelity used for cxswap. Default: 0.9738

--cz-fidelity F
  Fidelity used for cz. Default: 0.9589

--swap-fidelity F
  Fidelity used for encoded swap / input swap. Default: 0.9903
```

Output and plotting:

```text
--json
  Prints machine-readable JSON output.

--output-dir DIR
  Saves the result in DIR. The directory is created automatically.
  Text output is written as `<input_qasm_stem>_output.txt`; with `--json`, it is
  written as `<input_qasm_stem>_output.json`.

--plot-topology PATH
  Saves a topology figure as PNG/SVG/PDF, depending on the file extension.

--no-encoded-edges
  Hides dashed encoded-slot adjacency edges in topology plots.
```

## CP-SAT Mode

CP-SAT mode searches for an exact solution within the given `--sat-layers` horizon. If `solver_status` is `OPTIMAL`, optimality has been proven within that horizon. If the horizon is too small, no solution may be found.

`total_pulses` is the sum of all macro-operation pulse counts. `schedule_duration` is the estimated time after parallel execution: for each layer, it takes the maximum pulse count in that layer, then sums over layers.

The CP-SAT routing objective is:

```text
minimize
  makespan_weight * schedule_duration
+ swap_weight * encoded_swap_count
+ error_weight * total_error_cost
```

Defaults:

```text
makespan_weight = 1000
swap_weight     = 10
error_weight    = 1
error_scale     = 1000000
```

`error_scale` converts the floating-point `-log(F(operation_i))` values into integer costs for CP-SAT. With `error_scale = 1000000`, each `-log(F)` value is multiplied by 1000000 and rounded before it is added to `total_error_cost`.

Setting any objective weight to `0` removes that cost from the objective.

The current CP-SAT mode is a test implementation. Depending on `--layout-strategy`, the initial layout is either optimized inside the same CP-SAT model or fixed by a heuristic before the routing CP-SAT model runs.

With `--solver cp-sat` and the default `--layout-strategy exhaustive`, one CP-SAT model jointly optimizes:

- initial layout,
- encoded-SWAP routing,
- cxswap selection,
- gate execution layer,
- and parallel execution of independent operations.

With `--layout-strategy interaction`, the initial layout is decided first by the interaction-weighted heuristic and then fixed. The routing CP-SAT model then optimizes encoded-SWAP routing, cxswap selection, layers, and parallel execution. See "Layout And Routing Modes" below for the supported modes.

This can produce strong solutions for small circuits, but the search space grows quickly. For example, on a 3x3 grid with many CX gates, the solver may return `FEASIBLE` within the time limit instead of proving `OPTIMAL`.

OpenQASM logical `swap` gates are supported in CP-SAT mode. An input `swap` is treated as a required circuit gate and swaps the two logical qubit locations when it runs. Routing `encoded_swap` operations are separate from input `swap` gates and are inserted by the solver when needed. CP-SAT can also choose `cxswap` instead of `cx` for an input `cx` gate when the extra logical-location swap improves the route. An explicit input `cxswap` gate is supported as a macro operation that applies a CX-like gate and swaps the two logical qubit locations.

For ordering constraints, `cz` gates commute with other `cz` gates. CP-SAT may reorder CZ-CZ pairs even when they share a logical qubit, although shared-qubit operations still cannot run in the same layer.

## Layout And Routing Modes

There are three practical CP-SAT-style optimization modes:

```text
1. Joint CP-SAT
   Initial layout, routing, cxswap selection, layers, and parallel execution
   are optimized in one CP-SAT model. This is the strongest test mode, but it is
   also the heaviest.

2. Initial-layout heuristic + routing CP-SAT
   Use `--solver cp-sat --layout-strategy interaction`.
   The initial layout is selected by a greedy/local-search heuristic, then the
   routing CP-SAT model runs with that layout fixed. This is usually the more
   scalable option.

3. Windowed routing CP-SAT
   Use `--solver window-cp-sat`.
   The initial layout is fixed by the interaction-weighted heuristic, the circuit
   is split into windows, and each window is solved sequentially with the same
   routing CP-SAT objective. Each window final layout is passed to the next
   window as its fixed initial layout.
```

## Large-Heuristic Mode

`--solver large-heuristic` is a scalable heuristic mode for larger benchmark circuits, such as hundreds of logical qubits and hundreds of random gates. It does not prove optimality.

It performs:

- interaction-weighted initial placement,
- front-layer gate selection,
- lookahead scoring of future two-qubit gates,
- multiple shortest-path candidate checks,
- encoded-SWAP and CXSWAP candidate comparison,
- and greedy parallel layer scheduling.

The score is heuristic, not a global objective. It favors routing moves that reduce front-layer and lookahead distances, while still accounting for macro-operation costs. The reported `schedule_duration` is computed after greedy parallel packing, so it can be much smaller than `total_pulses`.

## Initial-Layout Objective

This objective is used by the mode that decides the initial layout before routing:

```text
2. Initial-layout heuristic + routing CP-SAT
   --solver cp-sat --layout-strategy interaction
   --solver window-cp-sat
   -> Greedy placement plus pair-swap local search reduces this objective to
      choose the initial layout.
```

In joint mode, `--solver cp-sat --layout-strategy exhaustive`, this objective is not used to fix the initial layout first. Instead, one CP-SAT model jointly optimizes the initial layout, routing, layers, and parallel execution.

In heuristic-layout mode, the optimizer builds a weighted logical interaction graph from the input circuit and chooses an initial layout that minimizes the approximate routing objective:

```text
minimize  sum_{i<j} w(i,j) * max(0, dist(layout[i], layout[j]) - 1)
```

Here, `dist(layout[i], layout[j])` is the shortest-path distance between the two encoded slots. Adjacent logical qubits have zero placement penalty because a two-qubit gate can be executed directly.

The interaction weight `w(i,j)` is accumulated from the two-qubit gates in the circuit. Earlier two-qubit gates are weighted more heavily:

```text
w(i,j) += layout_decay ^ k
```

where `k` is the zero-based index of the two-qubit gate in the circuit. The default is `--layout-decay 0.98`, so the first two-qubit gates have the largest influence and later gates gradually become lighter.

With `--layout-strategy interaction`, greedy placement is followed by pair-swap local search on the same objective.

## Topology JSON

Example: a 2x2 square-lattice patch with four encoded-qubit groups.

```json
{
  "nodes": [
    {"id": 0, "pos": [0, 2]}, {"id": 1, "pos": [1, 2]}, {"id": 2, "pos": [2, 2]},
    {"id": 3, "pos": [3, 2]}, {"id": 4, "pos": [4, 2]}, {"id": 5, "pos": [5, 2]},
    {"id": 6, "pos": [0, 0]}, {"id": 7, "pos": [1, 0]}, {"id": 8, "pos": [2, 0]},
    {"id": 9, "pos": [3, 0]}, {"id": 10, "pos": [4, 0]}, {"id": 11, "pos": [5, 0]}
  ],
  "edges": [
    [0, 1], [1, 2], [3, 4], [4, 5],
    [6, 7], [7, 8], [9, 10], [10, 11],
    [2, 3], [5, 9], [8, 9], [2, 6]
  ],
  "encoded_qubits": [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]]
}
```

`nodes` can also be written simply as `[0, 1, 2]`. Use `{"id": ..., "pos": [x, y]}` when you want fixed drawing coordinates.

Each `encoded_qubits` entry must contain exactly three physical dots. A physical dot cannot appear in multiple encoded groups.

After encoded groups are built, the effective encoded-slot graph must be isomorphic to either a 1D line or a rectangular 2D square lattice. For example, `examples/line3_topology.json`, `examples/grid2x2_topology.json`, and `examples/grid3x3_topology.json` are accepted. Branching, triangular, all-to-all, and irregular topologies are rejected.

Inter-group physical edges are also constrained by the order inside each `encoded_qubits` entry. If `encoded_qubits` contains `A=[0,1,2]` and `B=[3,4,5]`, then `2-3` and `5-0` are valid A-B interfaces. Edges such as `1-4`, `0-3`, or multiple A-B edges are rejected.

Edge attributes can be preserved:

```json
{
  "nodes": [0, 1, 2, 3, 4, 5],
  "edges": [
    [0, 1, {"fidelity": 0.993}],
    [1, 2],
    [2, 3],
    [3, 4],
    [4, 5, {"fidelity": 0.990}]
  ],
  "encoded_qubits": [[0, 1, 2], [3, 4, 5]]
}
```

Edge fidelities and durations are currently parsed but not used by the optimizer.

## References

- Jason D. Chadwick et al., "Short two-qubit pulse sequences for exchange-only spin qubits in 2D layouts", arXiv:2412.14918. https://arxiv.org/abs/2412.14918

## Example Output

Heuristic example:

```text
== initial layout ==
{0: (0, 1, 2), 1: (6, 7, 8), 2: (3, 4, 5)}
== pulse plan ==
00: h            logical=(0,) dots=((0, 1, 2),) pulses=3
01: cx           logical=(0, 2) dots=((0, 1, 2), (3, 4, 5)) pulses=28
02: cx           logical=(1, 2) dots=((6, 7, 8), (3, 4, 5)) pulses=28
03: encoded_swap logical=(0, 2) dots=((0, 1, 2), (3, 4, 5)) pulses=15
04: encoded_swap logical=(0, 1) dots=((3, 4, 5), (6, 7, 8)) pulses=15
== result ==
total_pulses = 89
schedule_duration = 89
final_layout = {0: (6, 7, 8), 1: (3, 4, 5), 2: (0, 1, 2)}
```

CP-SAT parallelization example:

```text
== pulse plan ==
00: layer=  0 h            logical=(0,) dots=((3, 4, 5),) pulses=3
01: layer=  0 h            logical=(1,) dots=((6, 7, 8),) pulses=3
02: layer=  1 cx           logical=(0, 3) dots=((3, 4, 5), (9, 10, 11)) pulses=28
03: layer=  1 cx           logical=(1, 2) dots=((6, 7, 8), (0, 1, 2)) pulses=28
04: layer=  2 rz           logical=(0,) dots=((3, 4, 5),) pulses=1
05: layer=  2 rz           logical=(2,) dots=((0, 1, 2),) pulses=1
== result ==
total_pulses = 64
schedule_duration = 32
solver_status = OPTIMAL
```

3x3 example with 10 CX gates:

```text
== initial layout ==
{0: (0, 1, 2), 1: (12, 13, 14), 2: (18, 19, 20), 3: (6, 7, 8), 4: (9, 10, 11), 5: (24, 25, 26), 6: (21, 22, 23), 7: (15, 16, 17), 8: (3, 4, 5)}
== pulse plan ==
00: layer=  0 h            logical=(0,) dots=((0, 1, 2),) pulses=3
01: layer=  0 h            logical=(1,) dots=((12, 13, 14),) pulses=3
...
19: layer=  5 cx           logical=(1, 3) dots=((3, 4, 5), (6, 7, 8)) pulses=28
20: layer=  5 cx           logical=(5, 7) dots=((15, 16, 17), (24, 25, 26)) pulses=28
21: layer=  5 cx           logical=(4, 6) dots=((9, 10, 11), (12, 13, 14)) pulses=28
== result ==
total_pulses = 352
schedule_duration = 143
solver_status = FEASIBLE
```
