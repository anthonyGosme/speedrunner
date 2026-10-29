"""Diagnostic client: times the protocol phases of warm runs.

  connect..R  connect, send the request, child validates and is ready
  G..X        the tool runs, up to the child's advisory exit code
  X..W        interpreter teardown, until the parent's wait status

stdio goes to /dev/null. Speaks the protocol described in
server/speedrunner_daemon.py.

Usage: N=20 python3 phase_client.py <socket> <script> [args...]
"""
import os
import socket
import statistics
import struct
import sys
import time


def blob(b):
    return struct.pack("<I", len(b)) + b


def blobs(items):
    return struct.pack("<I", len(items)) + b"".join(blob(i) for i in items)


def read_msg(s):
    header = s.recv(5, socket.MSG_WAITALL)
    if len(header) < 5:
        raise EOFError
    (n,) = struct.unpack("<I", header[1:])
    return header[:1], s.recv(n, socket.MSG_WAITALL)


def run_request(script, args):
    groups = os.getgroups()
    body = (blobs([os.fsencode(script)] + [os.fsencode(a) for a in args])
            + blobs([os.fsencode(k) + b"=" + os.fsencode(v) for k, v in os.environ.items()])
            + struct.pack("<IQ", 0o022, 0)
            + struct.pack("<I", len(groups)) + b"".join(struct.pack("<I", g) for g in groups))
    return b"HS1R" + struct.pack("<I", len(body)) + body


def main():
    sock_path, script, *args = sys.argv[1:]
    msg = run_request(script, args)
    null = os.open(os.devnull, os.O_RDWR)
    cwd = os.open(".", os.O_PATH | os.O_DIRECTORY)
    fds = struct.pack("4i", null, null, null, cwd)
    phases = []
    for _ in range(int(os.environ.get("N", "20"))):
        s = socket.socket(socket.AF_UNIX)
        t0 = time.perf_counter()
        s.connect(sock_path)
        s.sendmsg([msg], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)])
        kind, payload = read_msg(s)
        if kind != b"R":
            raise SystemExit(f"not ready: {kind} {payload!r}")
        t1 = time.perf_counter()
        s.sendall(b"G")
        assert read_msg(s)[0] == b"X"
        t2 = time.perf_counter()
        assert read_msg(s)[0] == b"W"
        t3 = time.perf_counter()
        s.close()
        phases.append((t1 - t0, t2 - t1, t3 - t2))
    for i, name in enumerate(("connect..R", "G..X", "X..W")):
        print(f"{name:11} median {statistics.median(p[i] for p in phases) * 1000:6.1f} ms")


if __name__ == "__main__":
    main()
