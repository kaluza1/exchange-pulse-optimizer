from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Literal


SolverMode = Literal["heuristic", "large-heuristic", "cp-sat", "window-cp-sat"]
WorkerSetting = int | Literal["auto", "all"]

SOLVER_MODES: tuple[SolverMode, ...] = (
    "heuristic",
    "large-heuristic",
    "cp-sat",
    "window-cp-sat",
)

# Exact and windowed CP-SAT benefit from a modest search portfolio. Exhaustive
# layout search also has coarse, independent work. The large heuristic is
# sequential between routing decisions, so multiprocessing is opt-in there.
DEFAULT_MODE_WORKERS: dict[SolverMode, WorkerSetting] = {
    "heuristic": "auto",
    "large-heuristic": 1,
    "cp-sat": "auto",
    "window-cp-sat": "auto",
}


@dataclass(frozen=True)
class AutoWorkerPolicy:
    max_workers: int = 8
    reserve_logical_cpus: int = 1

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("auto.max_workers must be positive.")
        if self.reserve_logical_cpus < 0:
            raise ValueError("auto.reserve_logical_cpus must be non-negative.")


@dataclass(frozen=True)
class WorkerConfig:
    workers: dict[SolverMode, WorkerSetting] = field(
        default_factory=lambda: dict(DEFAULT_MODE_WORKERS)
    )
    auto: AutoWorkerPolicy = field(default_factory=AutoWorkerPolicy)

    def setting_for(self, solver: str) -> WorkerSetting:
        if solver not in SOLVER_MODES:
            raise ValueError(f"unsupported solver mode for worker configuration: {solver}")
        return self.workers[solver]  # type: ignore[index]


def load_worker_config(path: str | Path | None = None) -> WorkerConfig:
    """Load a JSON worker configuration, or return resource-aware defaults."""

    if path is None:
        return WorkerConfig()

    source = Path(path)
    with source.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict):
        raise ValueError("worker configuration root must be a JSON object.")

    unknown_root = set(raw) - {"workers", "auto"}
    if unknown_root:
        raise ValueError(
            f"unknown worker configuration key(s): {sorted(unknown_root)}"
        )

    workers = dict(DEFAULT_MODE_WORKERS)
    raw_workers = raw.get("workers", {})
    if not isinstance(raw_workers, dict):
        raise ValueError("workers must be a JSON object.")
    unknown_modes = set(raw_workers) - set(SOLVER_MODES)
    if unknown_modes:
        raise ValueError(f"unknown solver mode(s) in workers: {sorted(unknown_modes)}")
    for mode, value in raw_workers.items():
        workers[mode] = parse_worker_setting(value, f"workers.{mode}")  # type: ignore[index]

    raw_auto = raw.get("auto", {})
    if not isinstance(raw_auto, dict):
        raise ValueError("auto must be a JSON object.")
    unknown_auto = set(raw_auto) - {"max_workers", "reserve_logical_cpus"}
    if unknown_auto:
        raise ValueError(f"unknown auto worker key(s): {sorted(unknown_auto)}")
    auto = AutoWorkerPolicy(
        max_workers=_positive_int(
            raw_auto.get("max_workers", AutoWorkerPolicy.max_workers),
            "auto.max_workers",
        ),
        reserve_logical_cpus=_non_negative_int(
            raw_auto.get(
                "reserve_logical_cpus",
                AutoWorkerPolicy.reserve_logical_cpus,
            ),
            "auto.reserve_logical_cpus",
        ),
    )
    return WorkerConfig(workers=workers, auto=auto)


def parse_worker_setting(value: object, path: str = "workers") -> WorkerSetting:
    """Parse a positive worker count, ``auto``, or ``all``."""

    if isinstance(value, bool):
        raise ValueError(f"{path} must be a positive integer, 'auto', or 'all'.")
    if isinstance(value, int):
        if value < 1:
            raise ValueError(f"{path} must be positive.")
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"auto", "all"}:
            return normalized  # type: ignore[return-value]
        try:
            parsed = int(normalized)
        except ValueError as exc:
            raise ValueError(
                f"{path} must be a positive integer, 'auto', or 'all'."
            ) from exc
        if parsed < 1:
            raise ValueError(f"{path} must be positive.")
        return parsed
    raise ValueError(f"{path} must be a positive integer, 'auto', or 'all'.")


def available_cpu_count() -> int:
    """Return CPUs available to this process, respecting affinity when possible."""

    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        try:
            count = process_cpu_count()
            if count is not None:
                return max(1, count)
        except OSError:
            pass

    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            return max(1, len(get_affinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def resolve_worker_count(
    setting: WorkerSetting,
    policy: AutoWorkerPolicy,
    *,
    available_cpus: int | None = None,
) -> int:
    """Resolve a worker request to an explicit process/thread count."""

    available = available_cpu_count() if available_cpus is None else available_cpus
    if available < 1:
        raise ValueError("available_cpus must be positive.")

    if isinstance(setting, int):
        if setting > available:
            raise ValueError(
                f"requested workers ({setting}) exceed available logical CPUs ({available})."
            )
        return setting
    if setting == "all":
        return available

    usable = max(1, available - policy.reserve_logical_cpus)
    return min(policy.max_workers, usable)


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{path} must be a positive integer.")
    return value


def _non_negative_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{path} must be a non-negative integer.")
    return value
