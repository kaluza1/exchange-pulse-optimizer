# Exchange Pulse Optimizer

[English README](README.md)

Exchange Pulse Optimizer は、OpenQASM 2.0 の量子回路、物理量子ドットの接続グラフ、3-dot encoded qubit group を入力として、exchange-only encoded qubit 向けの初期配置、encoded-SWAP ルーティング、macro gate layer、同時実行を最適化する実験用CLIツールです。

## モデル

このコードでは、OpenQASM の1量子ビットを **3つの物理量子ドットで構成される encoded qubit** として扱います。

```text
logical q[0] = dots (0, 1, 2)
logical q[1] = dots (3, 4, 5)
logical q[2] = dots (6, 7, 8)
```

トポロジーJSONの `nodes` は物理dotです。`encoded_qubits` で3-dot groupを定義します。2つのgroup間に物理edgeが1本でもあれば、そのencoded qubit slot同士は隣接しているとみなします。

**制約:** encoded qubit slot の有効接続グラフは、1D直線、または2x2、3x3などの2D正方格子パッチでなければなりません。それ以外の入力は読み込み時にエラーになります。

隣接するencoded group同士の物理edgeは、端同士の接続だけを許可します。たとえば `A=(a0,a1,a2)`, `B=(b0,b1,b2)` の場合、group間edgeとして有効なのは `a2-b0` または `b2-a0` だけです。これは `a0-a1-a2-b0-b1-b2` または `b0-b1-b2-a0-a1-a2` のような6-dotの直線interfaceを表します。中央dot同士の接続や、同じgroup pair間の複数edgeは拒否されます。

## コストモデル

デフォルトのmacro pulse costは次の値です。

```text
cx     : 28
cxswap : 31
cz     : 26
swap   : 15
h/x    : 3
rz/z   : 1
measure : 0
```

`cx=28`, `cxswap=31`, `cz=26`, `swap=15` はmacro-levelの近似値です。2量子ビットゲートのコストは、Chadwick et al., "Short two-qubit pulse sequences for exchange-only spin qubits in 2D layouts" (arXiv:2412.14918) のexchange-only pulse sequence結果を参照しています。CLIオプションで変更できます。

## Qiskitによる入力ゲート変換

OpenQASM入力は、最適化の前にQiskitの `transpile` でこのツールが実行可能なゲート集合へ変換します。これにより、`u`, `u3`, `ccx` などの入力ゲートは、原則として `cx`, `cz`, `swap`, `h`, `x`, `y`, `z`, `s`, `sdg`, `t`, `tdg`, `rx`, `ry`, `rz` に分解されます。

このQiskit変換は通常の実行ではデフォルトで有効です。`--qiskit-optimization-level` は、Qiskitにどの程度回路を簡約させるかを指定するオプションです。

```text
0: ほぼ分解だけ。入力ゲートを指定basisへ落とすことを主目的にします。
   変換は軽いですが、ゲート数やdepthの削減はあまり期待しません。

1: 軽い最適化。デフォルトです。
   明らかなキャンセルや簡単な1量子ビットゲートの整理を行い、
   変換時間と回路削減のバランスを取ります。

2: もう少し強い最適化。
   より多くの簡約や再合成を試すため、level 1よりゲート数やdepthが
   減る可能性がありますが、変換時間は増えます。

3: 最も強い最適化。
   Qiskit側でより積極的に回路を小さくしようとします。
   小さい回路では有効な場合がありますが、大きい回路では変換時間が
   長くなることがあります。
```

デフォルトでは `--qiskit-optimization-level 1` を使います。入力回路が大きい場合、level 2 や 3 はQiskit変換自体にも時間がかかることがあります。変換を無効化して、入力QASMが最初から対応ゲートだけを含むことを要求する場合は、`--no-qiskit-transpile` を指定します。

## ソース構成

```text
src/exchange_pulse_optimizer/
  costs.py            # pulse cost と Qiskit basis gate
  qasm.py             # OpenQASM読み込みとQiskit分解
  topology.py         # encoded topologyモデルとJSON検証
  layout.py           # 初期配置heuristic
  optimizer.py        # heuristic routing optimizer と pulse plan型
  large_heuristic.py  # 大規模回路向けfront-layer routing heuristic
  cpsat_optimizer.py  # CP-SAT routing, scheduling, cxswap, swap対応
  windowed_cpsat.py   # window分割 routing CP-SAT
  worker_config.py    # solver別worker設定とauto解決
  cli.py              # コマンドライン入口
  plotting.py         # トポロジー描画
```

## セットアップ

```powershell
py -m pip install -e .
```

## 使い方

heuristic optimizerを実行:

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

選択したsolverで使うCPU worker数を指定:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --workers 4
```

solverごとのworker数はJSON設定ファイルにも記述できます。リポジトリ直下の
`optimizer_config.json` を編集して使えます。

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --config optimizer_config.json
```

```json
{
  "workers": {
    "heuristic": "auto",
    "large-heuristic": 1,
    "cp-sat": "auto",
    "window-cp-sat": "auto"
  },
  "auto": {
    "max_workers": 8,
    "reserve_logical_cpus": 1
  }
}
```

`--workers` は、選択中のsolverに対する設定ファイルの値を上書きします。
正の整数はそのworker数、`all` は利用可能な全論理CPU、`auto` はOS用に
`auto.reserve_logical_cpus` 個を残しつつ最大 `auto.max_workers` 個を使います。
`--config` と `--workers` の両方を省略した場合も、上記と同じ組み込み設定を
使います。したがって24論理CPUの環境では、`auto` は8 workerになります。

`large-heuristic` はrouting判断の逐次依存が強く、小・中規模ではプロセス間通信の
方が重くなりやすいため、デフォルトを1 workerにしています。再現性を重視した
単一workerの基準測定にも `--workers 1` を使えます。このworker設定が対象とする
のはoptimizer本体であり、単一回路に対するQiskitの事前変換は並列化しません。

トポロジー図を保存:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --plot-topology topology.png
```

macro costを変更:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --cx-cost 28 --cxswap-cost 31 --cz-cost 26 --swap-cost 15
```

Qiskit変換の最適化レベルを変える:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --qiskit-optimization-level 2
```

相互作用重み付きの初期配置ヒューリスティックを使う:

```powershell
exchange-pulse-opt examples\sample.qasm examples\line3_topology.json --layout-strategy interaction
```

2x2例でCP-SATを実行:

```powershell
exchange-pulse-opt examples\sample_cpsat.qasm examples\grid2x2_topology.json --solver cp-sat --sat-layers 8 --time-limit 10 --output-dir output
```

出力例は `output/sample_cpsat_output.txt` に保存されます。

4x4例を、初期配置ヒューリスティック + routing CP-SATで実行:

```powershell
exchange-pulse-opt examples\sample_4x4_interaction_20q.qasm examples\grid4x4_topology.json --solver cp-sat --layout-strategy interaction --sat-layers 40 --time-limit 900 --output-dir output
```

出力例は `output/sample_4x4_interaction_20q_output.txt` に保存されます。

7x7正方格子で、49論理qubit・50個の2量子ビットゲートを含む例:

```powershell
exchange-pulse-opt examples\sample_49q_50x2q_scattered_gridlocal.qasm examples\grid7x7_topology.json --solver window-cp-sat --window-size 2 --window-sat-layers 4 --time-limit 60 --workers 1 --output-dir output --no-qiskit-transpile
```

この例では、各windowが `OPTIMAL` で解け、出力は
`output/sample_49q_50x2q_scattered_gridlocal_output.txt` に保存されます。
手元の実行例では、`total_pulses = 1701`, `schedule_duration = 1506`,
`estimated_fidelity = 0.15001536` でした。

30x30正方格子で、900論理qubit・1000個の完全ランダムな2量子ビットゲートを `large-heuristic` で実行:

```powershell
exchange-pulse-opt examples\sample_900q_1000x2q_random.qasm examples\grid30x30_topology.json --solver large-heuristic --output-dir output --no-qiskit-transpile
```

出力例は `output/sample_900q_1000x2q_random_output.txt` に保存されます。
手元の実行例では、`total_pulses = 125502`, `schedule_duration = 1752`,
`estimated_fidelity = 3.0630504e-43`, `elapsed_seconds = 1679.932` でした。

## CLIオプション一覧

基本引数:

```text
qasm
  OpenQASM 2.0 の入力ファイルです。

topology
  物理dot graphと encoded_qubits を書いたJSONファイルです。
```

実行モードとworker設定:

```text
--solver heuristic|large-heuristic|cp-sat|window-cp-sat
  最適化方法を選びます。
  heuristic       : shortest-path routing heuristicを使います。
  large-heuristic : front-layer/lookahead routingとgreedy並列scheduleを使います。
  cp-sat          : CP-SATでrouting, layer, 同時実行などを最適化します。
  window-cp-sat   : 初期配置を固定し、window単位でrouting CP-SATを実行します。
  デフォルト: heuristic

--config PATH
  solverごとのworker設定とauto方針を記述したJSONファイルです。
  このオプションを省略した場合、ファイルは暗黙には読み込まず、組み込み設定を使います。

--workers N|auto|all
  選択中のsolverで使うworker数です。--configの値より優先されます。
  heuristic       : 規模が十分大きい場合、exhaustive初期配置候補をプロセス並列評価します。
  large-heuristic : 候補が十分ある場合、routing候補のscore計算をプロセス並列化します。
  cp-sat          : OR-Tools CP-SAT内部の探索thread数に渡します。
  window-cp-sat   : 各windowのCP-SAT内部の探索thread数に渡します。

--layout-strategy exhaustive|interaction
  初期配置の決め方を選びます。
  exhaustive  : CP-SATモードでは初期配置も同じCP-SAT内で同時最適化します。
                heuristicモードでは候補配置を列挙します。
  interaction : 相互作用重み付きheuristicで初期配置を先に固定します。
  デフォルト: exhaustive
```

CP-SAT関連:

```text
--sat-layers N
  routing CP-SATで使う最大macro layer数です。
  小さすぎると解が見つからないことがあります。
  未指定の場合は内部で概算値を使います。

--time-limit SEC
  routing CP-SAT本体の時間制限です。
  デフォルト: 30

--cp-sat-workers N|auto|all
  CP-SAT系モードにおける --workers の旧互換aliasです。
  新しい実行では --workers を推奨します。
```

初期配置heuristic関連:

```text
--layout-decay VALUE
  初期配置目的関数で、後ろの2量子ビットゲートをどれくらい軽くするかを決めます。
  w(i,j) += layout_decay ^ k として使います。
  1.0に近いほど後続ゲートも同じくらい重く、値を小さくするほど序盤ゲートを重視します。
  デフォルト: 0.98

--layout-local-search-rounds N
  `--layout-strategy interaction` のpair-swap局所探索の回数です。
  大きくすると少し良い初期配置が見つかる可能性がありますが、前処理時間が増えます。
  デフォルト: 2

--max-layouts N
  heuristicモード + `--layout-strategy exhaustive` で列挙する最大初期配置数です。
  qubit数が大きい場合は全列挙が重くなります。
  デフォルト: 40320
```

Qiskit変換:

```text
--qiskit-optimization-level 0|1|2|3
  OpenQASM入力を対応ゲートへ分解するときのQiskit最適化レベルです。
  デフォルト: 1

--no-qiskit-transpile
  Qiskitによる事前分解を無効化します。
  入力QASMが最初から対応ゲートだけを含む場合に使います。
```

macro pulse cost:

```text
--cx-cost N
  cx のmacro pulse costです。デフォルト: 28

--cxswap-cost N
  cxswap のmacro pulse costです。デフォルト: 31

--cz-cost N
  cz のmacro pulse costです。デフォルト: 26

--swap-cost N
  encoded swap / input swap のmacro pulse costです。デフォルト: 15
```

出力・描画:

```text
--json
  結果をJSONで出力します。

--output-dir DIR
  結果をDIRに保存します。ディレクトリは自動作成されます。
  テキスト出力では `<入力QASM名>_output.txt`、`--json` では
  `<入力QASM名>_output.json` として保存します。

--plot-topology PATH
  topology図をPNG/SVG/PDFなどに保存します。

--no-encoded-edges
  topology図でencoded-slot間の破線edgeを描かないようにします。
```

## CP-SATモード

CP-SATモードは、指定した `--sat-layers` の範囲内で解を探索します。`solver_status` が `OPTIMAL` なら、そのlayer上限内で最適性が証明されています。時間制限内に最適性が証明できない場合は、妥当な解として `FEASIBLE` が返ることがあります。

現在のCP-SATモードはテスト実装です。CP-SATでは、選んだ `--layout-strategy` に応じて、初期配置を同時に解くか、ヒューリスティックで先に決めた初期配置を固定してrouting側だけを解くかが変わります。

`--layout-strategy exhaustive` のまま `--solver cp-sat` を使う場合は、1つのCP-SATモデルで次を同時に最適化します。

- 初期配置
- encoded-SWAP ルーティング
- cxswap選択
- gate実行layer
- 独立operationの同時実行

一方、`--layout-strategy interaction` を指定した場合は、初期配置をヒューリスティックで先に決めて固定し、その後のCP-SATで routing、cxswap選択、layer、同時実行を最適化します。詳しくは次の「配置とルーティングのモード」を参照してください。

OpenQASM入力内の論理 `swap` はCP-SATモードでも対応しています。入力 `swap` は回路に含まれる必須ゲートとして扱い、実行時に2つの論理qubit位置を入れ替えます。ルーティング用の `encoded_swap` はこれとは別で、solverが必要に応じて挿入します。CP-SATでは、入力が `cx` の場合でも、経路上有利なら `cx` の代わりに `cxswap` を選べます。明示的な入力 `cxswap` も、CX系のゲートを実行しつつ2つの論理qubit位置を入れ替えるmacro operationとして扱います。

## 配置とルーティングのモード

実用上は、次の3つのCP-SAT系モードとして扱えます。

```text
1. 全同時CP-SAT
   初期配置、routing、cxswap選択、layer、同時実行を1つのCP-SATで
   同時に最適化します。もっとも強いテストモードですが、もっとも重いです。

2. 初期配置ヒューリスティック + routing CP-SAT
   `--solver cp-sat --layout-strategy interaction` を使います。
   初期配置は貪欲法と局所探索で決め、その配置を固定してrouting側のCP-SATを
   実行します。全同時CP-SATよりは軽いですが、回路全体を1つのrouting CP-SATとして
   解くため、さらに大きい回路では3のWindow分割 routing CP-SATを使います。

3. Window分割 routing CP-SAT
   `--solver window-cp-sat` を使います。
   初期配置は相互作用重み付きヒューリスティックで固定し、回路をwindowに分割して、
   各windowを既存と同じ目的関数のrouting CP-SATで順番に解きます。
   各windowのfinal_layoutは次のwindowのinitial_layoutとして渡されます。
```

補足:

`window-cp-sat` は、目的関数を変えるモードではありません。既存のrouting CP-SATと同じ目的関数を使い、CP-SATに入れる問題を小さいwindowへ分割することでスケールさせます。

```text
minimize
  makespan_weight * schedule_duration
+ swap_weight * encoded_swap_count
+ error_weight * total_error_cost
```

実行内容は次の通りです。

```text
1. interaction_weighted_layout() で初期配置を決める
2. 回路を --window-size ごとの小さいwindowに分割する
3. 各windowを既存の CpSatPulseOptimizer で解く
4. 各windowの final_layout を次windowの initial_layout として渡す
```

使用例:

```powershell
exchange-pulse-opt examples\sample_4x4_interaction_20q.qasm examples\grid4x4_topology.json --solver window-cp-sat --window-size 8 --window-sat-layers 20 --time-limit 60 --output-dir output
```

追加オプション:

```text
--window-size N
  1つのwindowに入れる回路operation数です。デフォルト: 20

--window-sat-layers N
  各windowのrouting CP-SATで使う最大macro layer数です。
  未指定の場合はwindowサイズとencoded slot数から概算します。

--workers N|auto|all
  各window内のCP-SAT探索で使うworker数です。window同士は順番に解きます。

--cp-sat-workers N|auto|all
  上記 --workers の旧互換aliasです。
```

このモードは、全体CP-SATより軽く、large-heuristicより局所的に強い解を狙うための中間モードです。領域分割やKernighan-Lin風の分割は、今後このwindow分割の上に追加できます。

## 初期配置目的関数

この節の目的関数は、次のモードで **初期配置を先に決めるため** に使います。

```text
2. 初期配置ヒューリスティック + routing CP-SAT
   --solver cp-sat --layout-strategy interaction
   --solver window-cp-sat
   → この目的関数を貪欲法 + pair-swap局所探索で小さくして、
     初期配置を決めます。
```

全同時CP-SATの `--solver cp-sat --layout-strategy exhaustive` では、この節の目的関数で初期配置を先に固定するのではなく、初期配置・routing・layer・同時実行を1つのCP-SATで同時に解きます。

初期配置ヒューリスティック + routing CP-SATでは、入力回路から論理qubit間の重み付き相互作用グラフを作り、次の近似routing目的関数を小さくするように初期配置を選びます。

```text
minimize  sum_{i<j} w(i,j) * max(0, dist(layout[i], layout[j]) - 1)
```

ここで `dist(layout[i], layout[j])` は、2つのencoded slot間の最短経路距離です。隣接している論理qubit pairは2量子ビットゲートを直接実行できるため、配置ペナルティは0になります。

相互作用重み `w(i,j)` は、回路内の2量子ビットゲートから加算します。最初の2量子ビットゲートほど重くし、後ろに行くほどだんだん軽くします。

```text
w(i,j) += layout_decay ^ k
```

`k` は回路内の2量子ビットゲートの0始まりの番号です。デフォルトは `--layout-decay 0.98` なので、最初のCXなどの2量子ビットゲートが最も強く初期配置に効き、後続ゲートは徐々に軽くなります。

`--layout-strategy interaction` では、貪欲配置の後、同じ目的関数に対してpair-swap局所探索を行います。

## トポロジーJSON

2x2正方格子パッチの例:

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

各 `encoded_qubits` 要素は必ず3つの物理dotを含む必要があります。同じ物理dotを複数groupに入れることはできません。

`encoded_qubits` から作られる有効なencoded-slot graphは、1D直線、または矩形の2D正方格子と同型である必要があります。`examples/line3_topology.json`, `examples/grid2x2_topology.json`, `examples/grid3x3_topology.json` は受理されます。分岐、三角形、全結合、不規則なトポロジーは拒否されます。

group間の物理edgeは、`encoded_qubits` に書いたdot順序にも依存します。たとえば `A=[0,1,2]`, `B=[3,4,5]` の場合、`2-3` と `5-0` は有効なA-B interfaceです。一方、`1-4`, `0-3`, 複数のA-B edgeは拒否されます。

## 参考文献

- Jason D. Chadwick et al., "Short two-qubit pulse sequences for exchange-only spin qubits in 2D layouts", arXiv:2412.14918. https://arxiv.org/abs/2412.14918
