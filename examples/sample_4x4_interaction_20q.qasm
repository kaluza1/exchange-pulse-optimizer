OPENQASM 2.0;
include "qelib1.inc";

qreg q[16];

rx(0.125) q[0];
ry(0.250) q[5];
rz(0.375) q[10];
h q[15];
x q[3];
rz(0.500) q[12];

cx q[0], q[15];
cx q[1], q[14];
cz q[2], q[13];
cx q[3], q[12];
cx q[4], q[11];
cz q[5], q[10];
cx q[6], q[9];
cx q[7], q[8];
cx q[0], q[5];
cz q[1], q[6];
cx q[2], q[7];
cx q[8], q[13];
cz q[9], q[14];
cx q[10], q[15];
cx q[3], q[4];
cz q[11], q[12];
cx q[0], q[10];
cx q[5], q[15];
cz q[6], q[12];
cx q[1], q[11];
