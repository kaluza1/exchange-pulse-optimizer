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

## Cost Model

The default macro pulse costs are:

```text
cx   : 28
cz   : 28
swap : 15
h/x  : 3
rz/z : 1
measure : 0
```

The `cx=28` and `swap=15` defaults are macro-level approximations. They can be changed from the CLI.

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
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --cx-cost 28 --swap-cost 15
```

Run CP-SAT on the 2x2 example:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --solver cp-sat --sat-layers 8 --time-limit 10
```

Run CP-SAT on the 3x3 example:

```powershell
exchange-pulse-opt examples\sample_3x3_cpsat.qasm examples\grid3x3_topology.json --solver cp-sat --sat-layers 10 --time-limit 600
```

The 3x3 sample output is also saved in:

```text
examples/sample_3x3_cpsat_output.txt
```

Save a 3x3 topology figure:

```powershell
exchange-pulse-opt examples\sample_3x3_cpsat.qasm examples\grid3x3_topology.json --plot-topology grid3x3.png
```

## CP-SAT Mode

CP-SAT mode searches for an exact solution within the given `--sat-layers` horizon. If `solver_status` is `OPTIMAL`, optimality has been proven within that horizon. If the horizon is too small, no solution may be found.

`total_pulses` is the sum of all macro-operation pulse counts. `schedule_duration` is the estimated time after parallel execution: for each layer, it takes the maximum pulse count in that layer, then sums over layers.

The current CP-SAT mode is a test implementation. It jointly optimizes:

- initial layout,
- encoded-SWAP routing,
- gate execution layer,
- and parallel execution of independent operations.

This can produce strong solutions for small circuits, but the search space grows quickly. For example, on a 3x3 grid with many CX gates, the solver may return `FEASIBLE` within the time limit instead of proving `OPTIMAL`.

OpenQASM logical `swap` gates are not supported in CP-SAT mode yet. Routing `encoded_swap` operations are inserted by the solver when needed.

## Topology JSON

Example: a 9-dot line with three encoded-qubit groups.

```json
{
  "nodes": [
    {"id": 0, "pos": [0, 0]},
    {"id": 1, "pos": [1, 0]},
    {"id": 2, "pos": [2, 0]},
    {"id": 3, "pos": [3, 0]},
    {"id": 4, "pos": [4, 0]},
    {"id": 5, "pos": [5, 0]},
    {"id": 6, "pos": [6, 0]},
    {"id": 7, "pos": [7, 0]},
    {"id": 8, "pos": [8, 0]}
  ],
  "edges": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 7], [7, 8]],
  "encoded_qubits": [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
}
```

`nodes` can also be written simply as `[0, 1, 2]`. Use `{"id": ..., "pos": [x, y]}` when you want fixed drawing coordinates.

Each `encoded_qubits` entry must contain exactly three physical dots. A physical dot cannot appear in multiple encoded groups.

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
