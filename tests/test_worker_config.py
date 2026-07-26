from __future__ import annotations

import json
from pathlib import Path

import pytest

from exchange_pulse_optimizer.worker_config import (
    AutoWorkerPolicy,
    load_worker_config,
    parse_worker_setting,
    resolve_worker_count,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_builtin_defaults_are_resource_aware() -> None:
    config = load_worker_config()

    assert config.setting_for("heuristic") == "auto"
    assert config.setting_for("large-heuristic") == 1
    assert config.setting_for("cp-sat") == "auto"
    assert config.setting_for("window-cp-sat") == "auto"
    assert resolve_worker_count(
        config.setting_for("heuristic"),
        config.auto,
        available_cpus=24,
    ) == 8


def test_example_config_matches_builtin_defaults() -> None:
    assert load_worker_config(
        PROJECT_ROOT / "optimizer_config.json"
    ) == load_worker_config()


def test_auto_reserves_cpus_and_never_resolves_below_one() -> None:
    policy = AutoWorkerPolicy(max_workers=16, reserve_logical_cpus=2)

    assert resolve_worker_count("auto", policy, available_cpus=12) == 10
    assert resolve_worker_count("auto", policy, available_cpus=1) == 1


def test_explicit_and_all_worker_settings() -> None:
    policy = AutoWorkerPolicy()

    assert resolve_worker_count(4, policy, available_cpus=12) == 4
    assert resolve_worker_count("all", policy, available_cpus=12) == 12
    with pytest.raises(ValueError, match="exceed available"):
        resolve_worker_count(13, policy, available_cpus=12)


@pytest.mark.parametrize("value", [0, -1, True, "zero", 1.5])
def test_invalid_worker_setting_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        parse_worker_setting(value)


def test_partial_config_overrides_only_named_modes(tmp_path) -> None:
    config_path = tmp_path / "workers.json"
    config_path.write_text(
        json.dumps(
            {
                "workers": {
                    "heuristic": 3,
                    "window-cp-sat": "all",
                },
                "auto": {
                    "max_workers": 6,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_worker_config(config_path)

    assert config.setting_for("heuristic") == 3
    assert config.setting_for("large-heuristic") == 1
    assert config.setting_for("cp-sat") == "auto"
    assert config.setting_for("window-cp-sat") == "all"
    assert config.auto.max_workers == 6
    assert config.auto.reserve_logical_cpus == 1


def test_unknown_config_keys_are_rejected(tmp_path) -> None:
    config_path = tmp_path / "workers.json"
    config_path.write_text(
        json.dumps({"workers": {"unknown-solver": 2}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown solver"):
        load_worker_config(config_path)
