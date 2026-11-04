# Panel on the VPS, after the reliability changes

`main` at d68b0c2 (#15 to #19), 20 interleaved runs per command, nothing else running
on the machine. Compared with `20261030T212139Z-panel-vps` (20 runs, same machine,
earlier code).

- **38/38 outputs identical to the native run.**
- Speedup, geometric mean: x4.82, against x5.06.

| Runtime | Commands | Before | Now | Daemons' memory, total |
|---|---:|---:|---:|---:|
| Python | 17 | x6.09 | x5.39 | 921 to 926 MB |
| Ruby | 10 | x7.63 | x8.07 | 316 to 321 MB |
| PHP | 11 | x2.62 | x2.53 | 295 to 305 MB |

## What the changes cost

The two panels are four days and many commits apart. Measured directly the same day
(`bench/daemon_ab.py`, the code before this work against d68b0c2, one client, 30 rounds,
13 Python commands, with another bench running on the machine): served time +5.3%,
geometric mean. aws +3.2 ms, black, flake8, pre-commit and poetry about +1 ms, pip list
+0.2 ms, mypy -0.7 ms. So about half of Python's drop comes from these changes. The other
half predates them, or comes from the machine: not separated.

Accepted: this pays for what a tool reads at load being tracked, `kill -9` of the client,
the daemon following a client, locks at load, finalizers at exit and data lost at exit.

Ruby gains from the parent collecting after a fork
(`20261104T120000Z-parent-gc-vps`): rails x5.1 to x7.2, rake x8.0 to x9.6, gem list x4.0
to x4.8. `bundle --version` drops all the same, x14.8 to x11.9, and that is not explained
here.

## Memory

The totals barely move, but some commands do: black 28 to 45 MB, pytest 33 to 59 MB, where
`az vm --help` goes from 96 to 55 MB and httpie from 50 to 31 MB. Not explained: such a
daemon's size depends on what it has learned by the time it is measured.

## Not measured again

The README's other commands, marked there, come from the panels on the laptop, run before
these changes: `20261030T210248Z-candidates-docker` and
`20261031T005026Z-node-panel-docker`. Their Python commands likely cost about as much.
