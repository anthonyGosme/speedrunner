"""macOS (M1): native against a warm daemon, through the real client, for a
few commands. Each command first runs three times through speedrunner, so
that the daemon has learned what it preloads, then RUNS times each way,
interleaved, the fast path required (SPEEDRUNNER_REQUIRE_FAST=1). Outputs
(exit code, stdout, stderr) are compared once.

Wall times without subprocess's timeout, which polls (§11). Prints one JSON
line per command: native and served medians, min and max, in ms, the gain,
and whether the outputs are the same.

Usage: python warm.py <speedrunner> <runs> <PATH dirs> -- <tool args> [-- <tool args>]...
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time


def timed(argv, env):
    t = time.perf_counter()
    p = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True)
    return time.perf_counter() - t, (p.returncode, p.stdout, p.stderr)


def main():
    hs, runs, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    commands, current = [], None
    for arg in sys.argv[4:]:
        if arg == "--":
            current = []
            commands.append(current)
        else:
            current.append(arg)
    root = tempfile.mkdtemp(prefix="srr-bench-", dir="/tmp")  # a short socket path
    env = {**os.environ, "PATH": f"{path}:{os.environ['PATH']}",
           "XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    fast_env = {**env, "SPEEDRUNNER_REQUIRE_FAST": "1"}
    for argv in commands:
        served = [hs, "run", argv[0], "--", *argv[1:]]
        subprocess.run([hs, "prewarm", argv[0]], env=env, capture_output=True)
        for _ in range(3):
            timed(served, env)
            time.sleep(0.5)  # learning is applied between calls
        _, native_out = timed(argv, env)
        _, fast_out = timed(served, fast_env)
        times = {"native": [], "served": []}
        for _ in range(runs):
            times["native"].append(timed(argv, env)[0])
            t, out = timed(served, fast_env)
            if out[0] == 124:
                raise SystemExit(f"{argv}: not served: {out[2]!r}")
            times["served"].append(t)
        subprocess.run([hs, "stop", argv[0]], env=env, capture_output=True)
        row = {"command": " ".join(argv), "same_output": native_out == fast_out}
        for k, v in times.items():
            row[k] = {"median_ms": round(statistics.median(v) * 1000, 1),
                      "min_ms": round(min(v) * 1000, 1), "max_ms": round(max(v) * 1000, 1)}
        row["gain"] = round(row["native"]["median_ms"] / row["served"]["median_ms"], 1)
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
