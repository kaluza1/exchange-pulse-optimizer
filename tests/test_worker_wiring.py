from __future__ import annotations

import json
from pathlib import Path

import pytest

from exchange_pulse_optimizer import cli
from exchange_pulse_optimizer.optimizer import PulsePlan


class _StubOptimizer:
    calls: list[dict[str, object]] = []

    def __init__(self, _topology: object, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def optimize(self, _circuit: object) -> PulsePlan:
        return PulsePlan(
            initial_layout={},
            final_layout={},
            pulse_count=0,
            solver_status="TEST",
        )


@pytest.mark.parametrize(
    ("solver", "optimizer_name", "worker_keyword"),
    [
        ("heuristic", "PulseCountOptimizer", "workers"),
        ("large-heuristic", "LargeHeuristicOptimizer", "workers"),
        ("cp-sat", "CpSatPulseOptimizer", "num_search_workers"),
        ("window-cp-sat", "WindowedCpSatOptimizer", "num_search_workers"),
    ],
)
def test_cli_wires_workers_to_every_solver(
    monkeypatch,
    capsys,
    solver: str,
    optimizer_name: str,
    worker_keyword: str,
) -> None:
    _StubOptimizer.calls = []
    monkeypatch.setattr(cli, optimizer_name, _StubOptimizer)
    monkeypatch.setattr(cli, "read_openqasm", lambda _path: object())
    monkeypatch.setattr(cli, "read_topology_json", lambda _path: object())
    monkeypatch.setattr(cli, "available_cpu_count", lambda: 8)

    cli.main(
        [
            "input.qasm",
            "topology.json",
            "--solver",
            solver,
            "--workers",
            "3",
            "--no-qiskit-transpile",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert _StubOptimizer.calls[-1][worker_keyword] == 3
    assert output["workers"] == {
        "requested": 3,
        "resolved": 3,
        "available_logical_cpus": 8,
    }


def test_cli_worker_argument_overrides_selected_mode_config(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    _StubOptimizer.calls = []
    config_path = tmp_path / "workers.json"
    config_path.write_text(
        json.dumps({"workers": {"heuristic": 2}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "PulseCountOptimizer", _StubOptimizer)
    monkeypatch.setattr(cli, "read_openqasm", lambda _path: object())
    monkeypatch.setattr(cli, "read_topology_json", lambda _path: object())
    monkeypatch.setattr(cli, "available_cpu_count", lambda: 8)

    cli.main(
        [
            "input.qasm",
            "topology.json",
            "--config",
            str(config_path),
            "--workers",
            "5",
            "--no-qiskit-transpile",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert _StubOptimizer.calls[-1]["workers"] == 5
    assert output["workers"]["requested"] == 5
