# make -j served again

Since the fidelity review of 0.4.6, a call with an inherited fd above 2 runs natively. macOS's make 3.81 gives its jobserver's pipes to every recipe under `-j`, whether it runs a sub-make or not, so every call under `make -j` lost its gain there.

Measured on a laptop (Intel i7-9750H, macOS 26.7.2, GNU Make 3.81): 8 recipes of `aws --version` under `-j2`, wall time of make, median of 15 alternate runs, warm daemon. `bench/make_j.py`, every run in `results.json`.

| | median | speedup |
|---|---:|---:|
| native | 3225 ms | |
| before this change | 3263 ms | x1.0 |
| after | 712 ms | **x4.5** |

## What make gives its recipes

| make | fds under `-j2` | named in `MAKEFLAGS` |
|---|---|---|
| 3.81 (macOS) | 3, 4 and a dup of the read end, 5, on every recipe | `--jobserver-fds=3,4` |
| 4.3 (Debian) | 3, 4, only on a recipe marked `+` | `--jobserver-auth=3,4` |

## The rule

- `--jobserver-fds=R,W`: the pipes it names, and any fd on the same pipe, do not count as inherited. The call is served, without them. Any other fd above 2 still sends it native, and so do MAKEFLAGS that name something other than pipes.
- `--jobserver-auth=R,W`: unchanged, native. make 4.3 gives these only to a recipe its author marked as recursive: they are asked for.

The env is left as it is. Removing the jobserver from `MAKEFLAGS` would hide a warning that the native run also prints in the common case: Python's `subprocess` closes these fds, so a sub-make started by a Python tool says `jobserver unavailable: using -j1` natively too (checked with make 3.81).

## Remaining difference

A tool under such a make that starts a sub-make with its fds left open (`os.system`, PHP's `exec`) would natively share the jobserver. Served, it gets that warning on stderr and runs its sub-make serially. In the guide's known limits.

## Other findings

make 3.81 changes its recipes' stack limit, so its recipes get a daemon of their own (the socket's name covers limits). The first call under make is native, even when a daemon is up for the same tool outside make.

Not measured: any make other than 3.81 and 4.3. Which versions use which flag, and which give the fds to every recipe, was not checked; the rule goes by the flag. make 4.4's fifo jobserver should pass no fd: not checked either. A `$(MAKE)` recipe was not run: only `+`.
