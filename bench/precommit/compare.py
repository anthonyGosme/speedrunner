"""pre-commit, end to end: what a commit's hook runs (`pre-commit run` on the
changed file, with black, isort, flake8 and mypy), the file changed before
every call (outside the timing) so that no result cache answers.

Five ways, interleaved per round (RUNS rounds):
  native         pre-commit and the hooks of its own environments (the default)
  srr            `srr pre-commit run`: pre-commit served, its hooks native
  exec           `speedrunner exec pre-commit run`: pre-commit served, its hooks
                 still called by the absolute path of their environments
  native-system  hooks declared language: system, taken from PATH
  exec-system    the same under `speedrunner exec`: hooks through its links
  git            a real commit (`git commit -qam`), its hook installed for the
                 language: system config: git runs the hook, which starts
                 pre-commit by Python's absolute path
  git-exec       the same commit under `speedrunner exec`: pre-commit native,
                 its hooks through the links
Each: wall time (median, min–max), speedup over native with its 95% bootstrap
interval, and the hooks' verdicts compared with the native run's.

Build and run (from the repository):
  docker build -t sr-precommit -f bench/precommit/Dockerfile bench
  docker run --rm -v "$PWD/bench/results:/out" sr-precommit python3 /opt/bench/compare.py /out/<dir>
A client built from the tree instead of the release: add
  -v "$PWD/client/target/docker-linux-amd64/speedrunner:/usr/local/bin/speedrunner:ro"
Env: RUNS (default 20).
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel import speedup_ci, stats  # noqa: E402

PROJECT = "/opt/project"
EDITED = "shop/cart.py"
TOOLS = ["pre-commit", "black", "isort", "flake8", "mypy"]
PY, SYS = ".pre-commit-config.python.yaml", ".pre-commit-config.system.yaml"
RUN = ["pre-commit", "run", "--files", EDITED, "-c"]
WAYS = [
    ("native", RUN + [PY]),
    ("srr", ["srr"] + RUN + [PY]),
    ("exec", ["speedrunner", "exec"] + RUN + [PY]),
    ("native-system", RUN + [SYS]),
    ("exec-system", ["speedrunner", "exec"] + RUN + [SYS]),
    ("git", ["git", "commit", "-qam", "edit"]),
    ("git-exec", ["speedrunner", "exec", "git", "commit", "-qam", "edit"]),
]
VERDICT = re.compile(rb"^(\S+)\.+(Passed|Failed|Skipped)", re.M)


def main():
    out = sys.argv[1]
    runs = int(os.environ.get("RUNS", "20"))
    os.makedirs(out, exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"] = "/tmp/run"
    os.makedirs("/tmp/run", mode=0o700, exist_ok=True)
    with open(os.path.join(PROJECT, EDITED)) as f:
        original = f.read()
    edits = [0]
    for argv in (["git", "config", "user.email", "bench@example.com"], ["git", "config", "user.name", "bench"],
                 ["pre-commit", "install", "-c", SYS]):
        subprocess.run(argv, cwd=PROJECT, check=True, capture_output=True)

    def edit():
        # A change black, isort, flake8 and mypy all accept: the docstring.
        edits[0] += 1
        with open(os.path.join(PROJECT, EDITED), "w") as f:
            f.write(original.replace('"""A shopping cart."""', f'"""A shopping cart, edit {edits[0]}."""', 1))

    def run(argv, capture=True):
        return subprocess.run(argv, cwd=PROJECT, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.STDOUT if capture else subprocess.DEVNULL)

    def timed_ms(argv):
        edit()
        t0 = time.perf_counter()
        run(argv, capture=False)
        return (time.perf_counter() - t0) * 1000

    # Warm up every way (environments, caches, daemons and what they learn).
    for _ in range(4):
        for _, argv in WAYS:
            edit()
            run(argv)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        texts = [run(["speedrunner", "status", t]).stdout.decode().strip() for t in TOOLS]
        if all(t.startswith("ready") and " learning=" not in t for t in texts):
            break
        time.sleep(1)
    status = {t: run(["speedrunner", "status", t]).stdout.decode().strip() for t in TOOLS}

    verdicts, outputs = {}, {}
    for name, argv in WAYS:
        edit()
        p = run(argv)
        verdicts[name] = [(h.decode(), v.decode()) for h, v in VERDICT.findall(p.stdout)]
        outputs[name] = {"rc": p.returncode, "output": p.stdout.decode(errors="replace")}

    times = {name: [] for name, _ in WAYS}
    for _ in range(runs):
        for name, argv in WAYS:
            times[name].append(timed_ms(argv))

    native = times["native"]
    results = []
    for name, argv in WAYS:
        t = times[name]
        results.append({"way": name, "argv": argv, "times": stats(t),
                        "speedup": round(statistics.median(native) / statistics.median(t), 2),
                        "ci95": speedup_ci(native, t), "rc": outputs[name]["rc"],
                        "verdicts": verdicts[name],
                        "same_verdicts": verdicts[name] == verdicts["native"]})
    versions = {t: run([t, "--version-number" if t == "isort" else "--version"]).stdout.decode().strip().splitlines()[0]
                for t in TOOLS}
    with open(os.path.join(out, "compare.json"), "w") as f:
        json.dump({"runs": runs, "versions": versions, "daemons": status, "outputs": outputs,
                   "results": results}, f, indent=1)
    lines = [f"`pre-commit run --files {EDITED}`, or a commit of it, the file changed before every call; wall time in ms, "
             f"median (min–max) over {runs} interleaved rounds; speedup over native = ratio of the "
             "medians (95% bootstrap interval).", "",
             "| Way | Command | Time | Speedup | Same verdicts |", "|---|---|---:|---:|---|"]
    for r in results:
        s, ci = r["times"], r["ci95"]
        lines.append(f"| {r['way']} | `{' '.join(r['argv'])}` | {s['median']} ({s['min']}–{s['max']}) "
                     f"| x{r['speedup']} (x{ci[0]}–x{ci[1]}) | {'yes' if r['same_verdicts'] else 'NO'} |")
    lines += ["", "Verdicts (native): " + ", ".join(f"{h} {v}" for h, v in verdicts["native"]),
              "Versions: " + "; ".join(f"{k}: {v}" for k, v in versions.items())]
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
