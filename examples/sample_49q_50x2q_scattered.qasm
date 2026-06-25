OPENQASM 2.0;
include "qelib1.inc";

qreg q[49];

// 50 scattered two-qubit gates on 49 logical qubits.
// Logical labels are deliberately non-adjacent; the interaction graph is kept
// mostly path-like so window-cp-sat can still place/rout it on a 7x7 grid.
cx q[14], q[0];
cz q[0], q[37];
cx q[37], q[9];
cz q[9], q[44];
cx q[44], q[2];
cz q[2], q[28];
cx q[28], q[17];
cz q[17], q[41];
cx q[41], q[6];
cz q[6], q[33];
cx q[33], q[21];
cz q[21], q[48];
cx q[48], q[12];
cz q[12], q[35];
cx q[35], q[4];
cz q[4], q[25];
cx q[25], q[39];
cz q[39], q[8];
cx q[8], q[31];
cz q[31], q[19];
cx q[19], q[46];
cz q[46], q[1];
cx q[1], q[23];
cz q[23], q[42];
cx q[42], q[10];
cz q[10], q[30];
cx q[30], q[16];
cz q[16], q[47];
cx q[47], q[5];
cz q[5], q[27];
cx q[27], q[36];
cz q[36], q[13];
cx q[13], q[45];
cz q[45], q[7];
cx q[7], q[32];
cz q[32], q[20];
cx q[20], q[43];
cz q[43], q[3];
cx q[3], q[24];
cz q[24], q[38];
cx q[38], q[11];
cz q[11], q[29];
cx q[29], q[18];
cz q[18], q[40];
cx q[40], q[15];
cz q[15], q[34];
cx q[34], q[22];
cz q[22], q[26];
cx q[26], q[14];
cz q[37], q[48];
