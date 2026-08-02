from .costs import (
    DEFAULT_CZSWAP_FIDELITY,
    DEFAULT_CZSWAP_PULSE_COST,
    DEFAULT_GATE_FIDELITIES,
    DEFAULT_PULSE_COSTS,
    SUPPORTED_QISKIT_BASIS_GATES,
)
from .large_heuristic import LargeHeuristicOptimizer
from .layout import interaction_weighted_layout
from .optimizer import PulseCountOptimizer, PulsePlan, PulseStep
from .qasm import read_openqasm, transpile_to_supported_gates
from .topology import EncodedTopology, read_topology_json
from .worker_config import (
    AutoWorkerPolicy,
    WorkerConfig,
    available_cpu_count,
    load_worker_config,
    resolve_worker_count,
)


def plot_topology(*args: object, **kwargs: object) -> None:
    """Import Matplotlib only when topology plotting is requested."""

    from .plotting import plot_topology as _plot_topology

    _plot_topology(*args, **kwargs)


def __getattr__(name: str) -> object:
    """Load optional OR-Tools-backed solvers only when requested.

    Large-heuristic routing does not depend on OR-Tools.  Keeping these
    imports lazy also lets that solver run on hosts whose application-control
    policy blocks the native OR-Tools DLL.
    """

    if name == "CpSatPulseOptimizer":
        from .cpsat_optimizer import CpSatPulseOptimizer

        return CpSatPulseOptimizer
    if name == "WindowedCpSatOptimizer":
        from .windowed_cpsat import WindowedCpSatOptimizer

        return WindowedCpSatOptimizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_PULSE_COSTS",
    "DEFAULT_GATE_FIDELITIES",
    "DEFAULT_CZSWAP_PULSE_COST",
    "DEFAULT_CZSWAP_FIDELITY",
    "SUPPORTED_QISKIT_BASIS_GATES",
    "EncodedTopology",
    "PulseCountOptimizer",
    "LargeHeuristicOptimizer",
    "PulsePlan",
    "PulseStep",
    "interaction_weighted_layout",
    "read_openqasm",
    "read_topology_json",
    "transpile_to_supported_gates",
    "CpSatPulseOptimizer",
    "WindowedCpSatOptimizer",
    "AutoWorkerPolicy",
    "WorkerConfig",
    "available_cpu_count",
    "load_worker_config",
    "resolve_worker_count",
    "plot_topology",
]

