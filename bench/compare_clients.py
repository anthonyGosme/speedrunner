#!/usr/bin/env python3
"""Two builds of the client on the same commands, warm: runs alternate, so that
what the machine does meanwhile falls on both.

usage: compare_clients.py OLD_CLIENT NEW_CLIENT RUNS "tool args" ...
"""
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import time


def timed(argv, env):
    t0 = time.perf_counter()
    subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (time.perf_counter() - t0) * 1000


def warm(client, tool, args, env):
    for _ in range(40):
        p = subprocess.run([client, "run", tool, "--", *args], env={**env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                           stdin=subprocess.DEVNULL, capture_output=True)
        if p.returncode != 124:
            time.sleep(1.0)  # what the first calls taught it
            subprocess.run([client, "run", tool, "--", *args], env=env, capture_output=True)
            time.sleep(1.0)
            return
        time.sleep(0.25)
    raise SystemExit(f"{tool}: never served")


def main():
    old, new, runs = sys.argv[1], sys.argv[2], int(sys.argv[3])
    print(f"{'command':<34} {'old ms':>8} {'new ms':>8} {'diff':>8} {'noise':>7}")
    for line in sys.argv[4:]:
        tool, *args = shlex.split(line)
        with tempfile.TemporaryDirectory(prefix="cmp-") as root:
            os.makedirs(f"{root}/run", mode=0o700)
            env = {**os.environ, "XDG_RUNTIME_DIR": f"{root}/run", "XDG_CACHE_HOME": f"{root}/cache",
                   "XDG_DATA_HOME": f"{root}/data"}
            for client in (old, new):
                warm(client, tool, args, env)
            times = {old: [], new: []}
            for _ in range(runs):
                for client in (old, new):
                    times[client].append(timed([client, "run", tool, "--", *args], env))
            for client in (old, new):
                subprocess.run([client, "stop", tool], env=env, capture_output=True)
            a, b = statistics.median(times[old]), statistics.median(times[new])
            noise = statistics.quantiles(times[old], n=4)
            print(f"{line:<34} {a:8.2f} {b:8.2f} {b - a:+8.2f} {noise[2] - noise[0]:7.2f}", flush=True)


main()
