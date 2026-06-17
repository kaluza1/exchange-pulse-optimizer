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

## Cost Model

The default macro pulse costs are:

```text
cx     : 28
cxswap : 31
cz     : 26
swap   : 15
h/x    : 3
rz/z   : 1
measure : 0
```

The `cx=28`, `cxswap=31`, `cz=26`, and `swap=15` defaults are macro-level approximations. The two-qubit costs are based on the exchange-only pulse-sequence results discussed in Chadwick et al., "Short two-qubit pulse sequences for exchange-only spin qubits in 2D layouts" (arXiv:2412.14918). They can be changed from the CLI.

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
  cpsat_optimizer.py  # CP-SAT routing, scheduling, cxswap, and swap support
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
--solver heuristic|cp-sat
  Selects the optimizer.
  heuristic: sequential routing heuristic.
  cp-sat   : CP-SAT optimization for routing, layers, and parallel execution.
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
  Time limit for the routing CP-SAT model.
  Default: 30
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

## Layout And Routing Modes

There are two practical CP-SAT optimization modes:

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
```

## Initial-Layout Objective

This objective is used by the mode that decides the initial layout before routing:

```text
2. Initial-layout heuristic + routing CP-SAT
   --solver cp-sat --layout-strategy interaction
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
