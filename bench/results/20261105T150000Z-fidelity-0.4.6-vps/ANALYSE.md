# 0.4.6: fidelity fixes, measured against 0.4.5

Machine: the 2-vCPU dev VPS (Debian 12, Python 3.11). Noisy: the last column is the interquartile range of the 0.4.5 runs.

## What the client now does on each call

It hashes the caller's context into the socket's name (limits, priority, affinity, namespaces, capabilities), scans its fds, and waits for the child's hello before sending the request. Warm, runs alternating between the two builds (`bench/compare_clients.py`, 500 runs each):

```
command                              old ms   new ms     diff   noise
pip --version                         13.42    14.04    +0.61    5.23
aws --version                         44.39    44.44    +0.05    4.21
```

The difference is within the noise.

## Panel of real tools (`bench/panel.py`, 5 runs each)

40 commands (Python, Ruby, PHP), **40 outputs identical to native**, among them the two added because the earlier panel missed the bug: `black --check --no-cache .` (workers, x5.4) and `flake8 -j 2 .` (x2.8). Gains are those of the earlier panel, within the noise of 5 runs. Table in `panel-summary.md`. Node has no runtime on this machine: its matrix runs in CI.

## Before and after, on tests

20 of the tests added for this release fail against the 0.4.5 client on macOS (`CallerContext`, `AsNatively`, `StuckDaemon`, `Distribution`). All pass on this one, on Linux and macOS.

Real tools on macOS, on files they have not cached: `black --check .` and `flake8 -j 2 .` failed served in 0.4.5 (`PicklingError`, rc 1) and are as native now, as is `pylint -j 2`.
