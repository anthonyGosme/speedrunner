"""Estimate one daemon per Python environment instead of one per tool.

Imports, in one process, what each tool's daemon preloads (the script's
top-level imports, then its learned modules, from a panel run's caches),
and compares with one process per tool: memory (PSS), fork latency (what a
spare costs to replace) and import failures (tools that clash).

Usage: <venv>/bin/python union.py <panel_dir> TOOL...
"""
import ast
import gc
import glob
import importlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time


def preloaded(cache_dir, tool):
    script = shutil.which(tool)
    with open(script, "rb") as f:
        tree = ast.parse(f.read(), script)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    for path in glob.glob(os.path.join(cache_dir, f"{tool}-*.json")):
        with open(path) as f:
            names += json.load(f).get("modules") or []
    return names


def pss_mb():
    for line in open("/proc/self/smaps_rollup"):
        if line.startswith("Pss:"):
            return int(line.split()[1]) / 1024
    return 0.0


def fork_ms(runs=50):
    gc.freeze()
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def measure(cache_dir, tools):
    """In this process: import everything the tools preload; report."""
    null = os.open(os.devnull, os.O_WRONLY)
    saved = [os.dup(1), os.dup(2)]
    failed = []
    t0 = time.perf_counter()
    for tool in tools:
        sys.argv = [shutil.which(tool)]
        for name in preloaded(cache_dir, tool):
            os.dup2(null, 1)
            os.dup2(null, 2)
            try:
                importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001 - a clash is a result
                failed.append(f"{tool}:{name}: {type(exc).__name__}")
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(saved[0], 1)
                os.dup2(saved[1], 2)
    import_s = time.perf_counter() - t0
    return {"tools": tools, "modules": len(sys.modules), "pss_mb": round(pss_mb(), 1),
            "fork_ms": round(fork_ms(), 3), "import_s": round(import_s, 2), "failed": failed}


def main():
    if sys.argv[1] == "--one":
        print(json.dumps(measure(sys.argv[2], sys.argv[3:])))
        return
    cache_dir = os.path.join(sys.argv[1], "cache")
    tools = sys.argv[2:]

    def child(names):
        out = subprocess.run([sys.executable, __file__, "--one", cache_dir, *names],
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, check=True)
        return json.loads(out.stdout)

    alone = [child([t]) for t in tools]
    union = child(tools)
    for r in alone + [union]:
        print(f"{'+'.join(r['tools'])[:60]:60} modules={r['modules']:5} pss={r['pss_mb']:6.1f} MB "
              f"fork={r['fork_ms']:.2f} ms failed={len(r['failed'])}")
    print(f"sum of the {len(alone)} daemons: {sum(r['pss_mb'] for r in alone):.1f} MB; "
          f"one daemon: {union['pss_mb']:.1f} MB")
    for f in union["failed"]:
        print("  failed:", f)


if __name__ == "__main__":
    main()
