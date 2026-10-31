"""speedrunner against the daemons that tools ship because their own start is
slow: eslint_d, prettierd, rubocop --server, dmypy, blackd. The work is an
editor's on save: one small file, changed before every call (a comment line
with a counter, outside the timing), so that no result cache answers: the
case these daemons were made for.

For each tool, per round (RUNS rounds, interleaved so that a drift of the
machine affects the three alike): the native command, the same through
speedrunner (warm daemon, fast path required), and the tool's own daemon
(warm, through its usual client). Wall time: median, mean, sd, min, max, the
samples; each speedup over native with its 95% bootstrap interval. Outputs:
exit code and stdout compared with the native run's (stderr too for
speedrunner; the dedicated clients print their own).

Build and run (from the repository):
  docker build -t sr-daemons -f bench/daemons/Dockerfile bench
  docker run --rm -v "$PWD/bench/results:/out" sr-daemons python3 /opt/bench/compare.py /out/<dir>
Env: RUNS (default 20), ONLY (comma-separated tools).
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel import speedup_ci, stats  # noqa: E402

BLACKD_PORT = 45484
SAMPLES = {
    "sample.js": "const a = 1\nconsole.log(a)\n",
    "eslint.config.mjs": "export default [{ rules: { semi: 'error', 'no-unused-vars': 'error' } }];\n",
    "sample.rb": "# frozen_string_literal: true\n\n# A clean file.\nputs 1\n",
    # As in any real project: without it, rubocop prints its pending cops on
    # every run, and its server client is as slow as a native run.
    ".rubocop.yml": "AllCops:\n  NewCops: enable\n",
    "sample.py": "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n",
    "unformatted.py": "def greet( name ):\n  return 'Hello, '+name\n",
}

# The file each tool works on, and how a comment line starts in it.
EDITED = {"eslint": ("sample.js", "//"), "prettier": ("sample.js", "//"), "rubocop": ("sample.rb", "#"),
          "mypy": ("sample.py", "#"), "black": ("unformatted.py", "#")}

# tool, label, native argv, stdin file, dedicated argv, its start (None: the
# client starts it), its stop
COMPARISONS = [
    ("eslint", "eslint sample.js", ["eslint", "sample.js"], None,
     ["eslint_d", "sample.js"], None, ["eslint_d", "stop"]),
    ("prettier", "prettier --stdin-filepath sample.js", ["prettier", "--stdin-filepath", "sample.js"], "sample.js",
     ["prettierd", "sample.js"], None, ["prettierd", "stop"]),
    ("rubocop", "rubocop sample.rb", ["rubocop", "sample.rb"], None,
     ["rubocop", "--server", "sample.rb"], None, ["rubocop", "--stop-server"]),
    ("mypy", "mypy sample.py", ["mypy", "sample.py"], None,
     ["dmypy", "run", "--", "sample.py"], None, ["dmypy", "stop"]),
    ("black", "black - (unformatted.py on stdin)", ["black", "-q", "-"], "unformatted.py",
     ["curl", "-s", "-XPOST", f"http://127.0.0.1:{BLACKD_PORT}", "--data-binary", "@-"],
     ["blackd", "--bind-host", "127.0.0.1", "--bind-port", str(BLACKD_PORT)], None),
]


def main():
    out = sys.argv[1]
    runs = int(os.environ.get("RUNS", "20"))
    only = set(filter(None, os.environ.get("ONLY", "").split(",")))
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="sr-daemons-")
    work = os.path.join(scratch, "work")
    os.makedirs(work)
    for name, text in SAMPLES.items():
        with open(os.path.join(work, name), "w") as f:
            f.write(text)
    env = {**os.environ, "HOME": scratch, "LANG": "C.UTF-8",
           "XDG_RUNTIME_DIR": os.path.join(scratch, "run"),
           "XDG_CACHE_HOME": os.path.join(scratch, "cache")}
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)

    def run(argv, stdin=None, extra=None, capture=True):
        with open(os.path.join(work, stdin), "rb") if stdin else open(os.devnull, "rb") as f:
            return subprocess.run(argv, cwd=work, env={**env, **(extra or {})}, stdin=f,
                                  stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  timeout=120 if capture else None)

    edits = [0]

    def edit(tool):
        """The tool's file as saved anew: its sample plus a fresh comment line."""
        name, comment = EDITED[tool]
        edits[0] += 1
        with open(os.path.join(work, name), "w") as f:
            f.write(SAMPLES[name] + f"{comment} edit {edits[0]}\n")

    def timed_ms(argv, stdin, extra=None):
        t0 = time.perf_counter()
        run(argv, stdin, extra, capture=False)
        return (time.perf_counter() - t0) * 1000

    versions = {}
    results = []
    for tool, label, native_argv, stdin, own_argv, own_start, own_stop in COMPARISONS:
        if only and tool not in only:
            continue
        fast_argv = ["speedrunner", "run", native_argv[0], "--", *native_argv[1:]]
        fast_env = {"SPEEDRUNNER_REQUIRE_FAST": "1"}
        versions[tool] = run([native_argv[0], "--version"]).stdout.decode().strip().splitlines()[0]
        run(native_argv, stdin)  # caches (.mypy_cache, rubocop's), before the comparison
        native = run(native_argv, stdin)
        # speedrunner: a few calls (learning), the status settled.
        for _ in range(3):
            run(fast_argv, stdin)
        prev, deadline = None, time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(0.5)
            cur = run(["speedrunner", "status", native_argv[0]]).stdout.decode().strip()
            if cur == prev and cur.startswith("ready") and " learning=" not in cur:
                break
            prev = cur
        fast = run(fast_argv, stdin, fast_env)
        # The tool's own daemon, warmed by its client.
        server = None
        if own_start:
            server = subprocess.Popen(own_start, cwd=work, env=env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            time.sleep(3)
        for _ in range(3):
            own = run(own_argv, stdin)
        row = {"tool": tool, "command": label, "dedicated": " ".join(own_argv),
               "native_rc": native.returncode,
               "speedrunner_identical": (fast.returncode, fast.stdout, fast.stderr)
                                        == (native.returncode, native.stdout, native.stderr),
               "dedicated_same_stdout": own.stdout == native.stdout,
               "dedicated_rc": own.returncode}
        if fast.returncode == 124:
            row["error"] = "speedrunner not fast: " + fast.stderr.decode(errors="replace")[-200:]
        if not row["dedicated_same_stdout"]:
            row["dedicated_stdout"] = own.stdout.decode(errors="replace")[-300:]
            row["native_stdout"] = native.stdout.decode(errors="replace")[-300:]
        if "error" not in row:
            times = [[], [], []]
            for _ in range(runs):
                for t, (argv, extra) in zip(times, [(native_argv, None), (fast_argv, fast_env), (own_argv, None)]):
                    edit(tool)
                    t.append(timed_ms(argv, stdin, extra))
            n, f, o = times
            row.update(native=stats(n), speedrunner=stats(f), own=stats(o),
                       speedrunner_speedup=round(statistics.median(n) / statistics.median(f), 2),
                       speedrunner_ci95=speedup_ci(n, f),
                       own_speedup=round(statistics.median(n) / statistics.median(o), 2),
                       own_ci95=speedup_ci(n, o))
        if own_stop:
            run(own_stop)
        if server:
            server.terminate()
            server.wait()
        run(["speedrunner", "stop", native_argv[0]])
        results.append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("native", "speedrunner", "own")}),
              flush=True)

    with open(os.path.join(out, "compare.json"), "w") as f:
        json.dump({"runs": runs, "versions": versions, "results": results}, f, indent=1)
    lines = [f"Wall time in ms, median (min–max) over {runs} interleaved rounds, the file changed before "
             "every call; speedup over native = ratio of the medians (95% bootstrap interval).", "",
             "| Tool | Native | speedrunner | Its own daemon | speedrunner speedup | Own daemon speedup "
             "| speedrunner output identical | Own daemon stdout identical |",
             "|---|---:|---:|---:|---:|---:|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| `{r['command']}` | | | | {r['error']} | | | |")
            continue
        def cell(s):
            return f"{s['median']} ({s['min']}–{s['max']})"
        sc, oc = r["speedrunner_ci95"], r["own_ci95"]
        lines.append(f"| `{r['command']}` vs `{r['dedicated']}` | {cell(r['native'])} | {cell(r['speedrunner'])} "
                     f"| {cell(r['own'])} | x{r['speedrunner_speedup']} (x{sc[0]}–x{sc[1]}) "
                     f"| x{r['own_speedup']} (x{oc[0]}–x{oc[1]}) | {'yes' if r['speedrunner_identical'] else 'NO'} "
                     f"| {'yes' if r['dedicated_same_stdout'] else 'NO'} |")
    lines += ["", "Versions: " + "; ".join(f"{k}: {v}" for k, v in versions.items())]
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
