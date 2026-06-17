from __future__ import annotations

from pathlib import Path

from qiskit import QuantumCircuit, transpile

from .costs import DEFAULT_PULSE_COSTS, SUPPORTED_QISKIT_BASIS_GATES


def read_openqasm(path: str | Path) -> QuantumCircuit:
    return QuantumCircuit.from_qasm_file(str(path))


def transpile_to_supported_gates(
    qc: QuantumCircuit,
    optimization_level: int = 1,
    basis_gates: tuple[str, ...] = SUPPORTED_QISKIT_BASIS_GATES,
) -> QuantumCircuit:
    if optimization_level < 0 or optimization_level > 3:
        raise ValueError("qiskit optimization level must be 0, 1, 2, or 3.")

    converted = transpile(
        qc,
        basis_gates=list(basis_gates),
        optimization_level=optimization_level,
    )
    unsupported = sorted(
        {
            inst.operation.name
            for inst in converted.data
            if inst.operation.name not in DEFAULT_PULSE_COSTS
        }
    )
    if unsupported:
        raise ValueError(
            "Qiskit transpilation produced gates that are not supported by the pulse optimizer: "
            f"{unsupported}"
        )
    return converted
