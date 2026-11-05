#!/usr/bin/env python3
"""A Makefile of N recipes calling one tool, under make -j2: natively, and
through each client given. Runs alternate; each time is the wall time of make.

usage: make_j.py OUT_DIR TOOL_DIR TOOL ARG [NAME=CLIENT]...
Env: RUNS (default 15), RECIPES (default 8)
"""
import json
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import time

out, tool_dir, tool, arg = sys.argv[1:5]
clients = dict(c.split("=", 1) for c in sys.argv[5:])
RUNS, RECIPES = int(os.environ.get("RUNS", 15)), int(os.environ.get("RECIPES", 8))
root = tempfile.mkdtemp(prefix="make-j-", dir="/tmp")
env = {**os.environ, "PATH": f"{tool_dir}:{os.environ['PATH']}",
       "XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)


def makefile(name, command):
    path = os.path.join(root, name)
    targets = [f"t{i}" for i in range(RECIPES)]
    with open(path, "w") as f:
        f.write("all: " + " ".join(targets) + "\n")
        for t in targets:
            f.write(f"{t}:\n\t@{command} > /dev/null 2>&1\n")
        f.write("start:\n")
        for c in clients.values():
            f.write(f"\t@{shlex.quote(c)} prewarm {tool}\n")
        f.write("stop:\n")
        for c in clients.values():
            f.write(f"\t@{shlex.quote(c)} stop {tool}\n")
    return path


files = {"native": makefile("native.mk", f"{tool} {arg}")}
for name, client in clients.items():
    files[name] = makefile(f"{name}.mk", f"{shlex.quote(client)} run {tool} -- {arg}")


def make(path, *args):
    t0 = time.perf_counter()
    # A second stop finds no daemon: both clients have the same one.
    subprocess.run(["make", "-s", "-j2", "-f", path, *args], env=env, check=not args, cwd=root,
                   stderr=subprocess.DEVNULL if args else None)
    return (time.perf_counter() - t0) * 1000


make(files["native"], "start")
time.sleep(3)
for name in files:  # warm, and what is learned is learned
    for _ in range(3):
        make(files[name])
times = {name: [] for name in files}
for _ in range(RUNS):
    for name in files:
        times[name].append(make(files[name]))
make(files["native"], "stop")
version = subprocess.run(["make", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
summary = {"make": version, "tool": f"{tool} {arg}", "recipes": RECIPES, "runs": RUNS, "times_ms": times,
           "median_ms": {n: statistics.median(t) for n, t in times.items()}}
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "results.json"), "w") as f:
    json.dump(summary, f, indent=1)
native = summary["median_ms"]["native"]
print(version, f"| {RECIPES} recipes of `{tool} {arg}` under -j2, {RUNS} runs")
for name, median in summary["median_ms"].items():
    print(f"{name:12} {median:8.0f} ms  x{native / median:.1f}  (sd {statistics.stdev(times[name]):.0f})")
