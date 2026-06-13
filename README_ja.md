# Exchange Pulse Optimizer

[English README](README.md)

Exchange Pulse Optimizer は、OpenQASM 2.0 の量子回路、物理量子ドットの接続グラフ、3 dot encoded qubit group を入力として、exchange-only encoded qubit 向けの初期配置、encoded SWAP ルーティング、macro gate layer、並列実行を最適化する実験用CLIツールです。

## モデル

このコードでは、OpenQASM の1量子ビットを **3つの物理量子ドットで構成される encoded qubit** として扱います。

```text
logical q[0] = dots (0, 1, 2)
logical q[1] = dots (3, 4, 5)
logical q[2] = dots (6, 7, 8)
```

入力トポロジーの `nodes` は物理量子ドットです。`encoded_qubits` で3 dot groupを定義します。3 dot group同士の間に物理edgeがある場合、その encoded qubit slot 同士が隣接しているとみなします。

## コストモデル

デフォルトの macro pulse cost は次の値です。

```text
cx   : 28
cz   : 28
swap : 15
h/x  : 3
rz/z : 1
measure : 0
```

`cx=28`, `swap=15` は macro-level の近似値です。CLIオプションで変更できます。

## セットアップ

```powershell
py -m pip install -e .
```

## 使い方

heuristic optimizer を実行:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json
```

インストールせずに実行:

```powershell
$env:PYTHONPATH="src"
py -m exchange_pulse_optimizer.cli examples\sample.qasm examples\line3_topology.json
```

JSON出力:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --json
```

物理dot接続グラフを画像として保存:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --plot-topology topology.png
```

図では物理dot graphを描き、3 dot encoded qubit group を色分けします。group間に物理接続がある場合は、encoded slot間の接続を破線で表示します。

macro cost を変更:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --cx-cost 28 --swap-cost 15
```

2x2例でCP-SATを実行:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --solver cp-sat --sat-layers 8 --time-limit 10
```

3x3例でCP-SATを実行:

```powershell
exchange-pulse-opt examples\sample_3x3_cpsat.qasm examples\grid3x3_topology.json --solver cp-sat --sat-layers 10 --time-limit 600
```

3x3例の出力は以下にも保存しています。

```text
examples/sample_3x3_cpsat_output.txt
```

3x3格子の図を保存:

```powershell
exchange-pulse-opt examples\sample_3x3_cpsat.qasm examples\grid3x3_topology.json --plot-topology grid3x3.png
```

## CP-SATモード

CP-SATモードは、指定した `--sat-layers` の範囲内で解を探します。出力の `solver_status` が `OPTIMAL` なら、そのlayer上限内で最適性が証明されています。`--sat-layers` が小さすぎる場合は解が見つからないことがあります。

`total_pulses` は実行したmacro operationのpulse数の合計です。`schedule_duration` は同時実行を考慮した実行時間の見積もりで、各layerの最大pulse数を足した値です。

現在のCP-SATモードはテスト版です。初期配置、encoded SWAPによるルーティング、各gateの実行layer、独立なoperationの同時実行を同時に最適化します。そのため小規模では強い解を得られますが、3x3格子でCXが多い場合などは探索空間が大きくなり、制限時間内では `FEASIBLE` 止まりになることがあります。

現在のCP-SATモードでは、OpenQASM入力内の論理 `swap` ゲートは未対応です。ルーティング用の `encoded_swap` は solver が必要に応じて挿入します。

## トポロジーJSON

9個の物理dotを直線接続し、3つの encoded qubit group を置く例:

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

`nodes` は単に `[0, 1, 2]` のようにも書けます。図の配置を固定したい場合は、`{"id": ..., "pos": [x, y]}` を使います。

各 `encoded_qubits` 要素は必ず3つの物理dotを含む必要があります。同じdotを複数groupに重複して入れることはできません。

edge 属性も残せます。

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

現状では edge fidelity や duration は読み込めますが、optimizerの目的関数にはまだ使っていません。

## 出力例

heuristic例:

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

CP-SATで並列化された例:

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

3x3格子でCXを10個入れてCP-SAT最適化した例:

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
