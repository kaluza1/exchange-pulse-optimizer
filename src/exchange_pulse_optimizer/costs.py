from __future__ import annotations


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
    "barrier": 0,
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
