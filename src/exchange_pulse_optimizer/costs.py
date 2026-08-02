from __future__ import annotations

from math import log


DEFAULT_PULSE_COSTS = {
    "cx": 28,
    "cxswap": 31,
    "cz": 26,
    "swap": 15,
    "h": 3,
    "x": 3,
    "y": 3,
    "z": 1,
    "s": 1,
    "sdg": 1,
    "t": 1,
    "tdg": 1,
    "rx": 3,
    "ry": 3,
    "rz": 1,
    "measure": 0,
    "reset": 0,
    "barrier": 0,
}

ONE_QUBIT_GATES = (
    "h",
    "x",
    "y",
    "z",
    "s",
    "sdg",
    "t",
    "tdg",
    "rx",
    "ry",
    "rz",
)

DEFAULT_GATE_FIDELITIES = {
    "cx": 0.9755,
    "cxswap": 0.9738,
    "cz": 0.9589,
    "czswap": 0.9589 * 0.9903,
    "swap": 0.9903,
    **{gate: 0.9986 for gate in ONE_QUBIT_GATES},
    "measure": 1.0,
    "reset": 1.0,
    "barrier": 1.0,
}

# No calibrated fused CZSWAP macro is available in the cited pulse data.
# Large-heuristic mode therefore uses a conservative sequential CZ + SWAP
# composition by default.  Callers can override both values.
DEFAULT_CZSWAP_PULSE_COST = DEFAULT_PULSE_COSTS["cz"] + DEFAULT_PULSE_COSTS["swap"]
DEFAULT_CZSWAP_FIDELITY = DEFAULT_GATE_FIDELITIES["czswap"]

OPERATION_FIDELITY_ALIASES = {
    "encoded_swap": "swap",
    "move_to_empty": "swap",
}

SUPPORTED_QISKIT_BASIS_GATES = (
    "cx",
    "cz",
    "swap",
    "h",
    "x",
    "y",
    "z",
    "s",
    "sdg",
    "t",
    "tdg",
    "rx",
    "ry",
    "rz",
)


def estimate_operation_fidelity(
    operation_names: list[str] | tuple[str, ...],
    gate_fidelities: dict[str, float] | None = None,
) -> float:
    fidelities = DEFAULT_GATE_FIDELITIES | (gate_fidelities or {})
    estimated_fidelity = 1.0
    for name in operation_names:
        fidelity_key = OPERATION_FIDELITY_ALIASES.get(name, name)
        estimated_fidelity *= fidelities.get(fidelity_key, 1.0)
    return estimated_fidelity


def operation_error_cost(
    operation_name: str,
    gate_fidelities: dict[str, float] | None = None,
    scale: int = 1_000_000,
) -> int:
    fidelities = DEFAULT_GATE_FIDELITIES | (gate_fidelities or {})
    fidelity_key = OPERATION_FIDELITY_ALIASES.get(operation_name, operation_name)
    fidelity = fidelities.get(fidelity_key, 1.0)
    if not 0.0 < fidelity <= 1.0:
        raise ValueError(f"gate fidelity must be in the range (0, 1]: {operation_name}={fidelity}")
    return int(round(scale * -log(fidelity)))


def total_operation_error_cost(
    operation_names: list[str] | tuple[str, ...],
    gate_fidelities: dict[str, float] | None = None,
    scale: int = 1_000_000,
) -> int:
    return sum(operation_error_cost(name, gate_fidelities, scale) for name in operation_names)
