"""Stage 3 bench: the time a user waits after pressing Enter, depending on
how long the shell had to warm the daemon up while they typed.

Scenarios, for each reference command:
  native      the tool itself
  cold        daemon stopped, no prewarm: the client runs natively and
              starts the daemon in the background
  prewarm+D   daemon stopped, `speedrunner prewarm`, D ms of "typing", then run
  warm        daemon up with a spare waiting

Each daemon start reuses what earlier runs learned (levels 2 and 3), as it
would for a user. Runs are timed from spawn to exit by this script, so they
include ~1 ms of Python subprocess overhead on every row. "fast" counts the
runs that took the fast path (SPEEDRUNNER_DEBUG reports the others).

Usage: python3 anticipation.py <aws venv> <speedrunner binary> <out_dir>
Env:   RUNS (default 15).
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

COMMANDS = [["--version"], ["configure", "list"],
            ["ec2", "run-instances", "--generate-cli-skeleton"]]
DELAYS_MS = [0, 100, 300]


def main():
    venv, hs, out = sys.argv[1:4]
    runs = int(os.environ.get("RUNS", "15"))
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="hs-anticipation-")
    env = {
        "PATH": f"{venv}/bin:/usr/bin:/bin",
        "HOME": scratch,
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": os.path.join(scratch, "run"),
        "XDG_CACHE_HOME": os.path.join(scratch, "cache"),
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": os.path.join(scratch, "config"),
        "AWS_SHARED_CREDENTIALS_FILE": os.path.join(scratch, "credentials"),
        "SPEEDRUNNER_DEBUG": "1",
    }
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    aws = os.path.join(venv, "bin", "aws")

    def hs_cmd(*args):
        return subprocess.run([hs, *args], env=env, capture_output=True)

    def status():
        return hs_cmd("status", "aws").stdout.decode().strip()

    def stop():
        hs_cmd("stop", "aws")
        while status() != "down":
            time.sleep(0.01)

    def wait_ready():
        while not status().startswith("ready"):
            if status() == "down":
                hs_cmd("prewarm", "aws")
            time.sleep(0.01)

    def timed(argv):
        t0 = time.perf_counter()
        p = subprocess.run(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        elapsed = time.perf_counter() - t0
        assert p.returncode == 0, p.stderr
        return elapsed, b"natively" not in p.stderr

    # Learn first, as a user's earlier runs would have.
    wait_ready()
    for args in COMMANDS:
        for _ in range(2):
            timed([hs, "run", "aws", "--", *args])
    time.sleep(2)
    learned = status()

    # Start-up of a daemon with that cache: prewarm until the first spare.
    startup = []
    for _ in range(5):
        stop()
        t0 = time.perf_counter()
        hs_cmd("prewarm", "aws")
        while not status().startswith("ready"):
            pass
        startup.append(time.perf_counter() - t0)

    results = {"runs": runs, "learned_status": learned,
               "startup_ms": [round(t * 1000, 1) for t in startup], "commands": {}}
    for args in COMMANDS:
        rows = {}

        def scenario(name, prepare, argv):
            times, fast = [], 0
            for _ in range(runs):
                prepare()
                t, was_fast = timed(argv)
                times.append(t)
                fast += was_fast
            rows[name] = {"median_ms": statistics.median(times) * 1000,
                          "stdev_ms": statistics.stdev(times) * 1000, "fast": fast,
                          "times_ms": [round(t * 1000, 1) for t in times]}
            print(f"aws {' '.join(args):45} {name:12} {rows[name]['median_ms']:7.1f} ms "
                  f"± {rows[name]['stdev_ms']:5.1f}  fast {fast}/{runs}", flush=True)

        run_argv = [hs, "run", "aws", "--", *args]
        scenario("native", lambda: None, [aws, *args])
        scenario("cold", stop, run_argv)
        for delay in DELAYS_MS:
            def prepare(delay=delay):
                stop()
                hs_cmd("prewarm", "aws")
                time.sleep(delay / 1000)
            scenario(f"prewarm+{delay}", prepare, run_argv)
        scenario("warm", lambda: (wait_ready(), time.sleep(0.05)), run_argv)
        results["commands"][" ".join(args)] = rows
    stop()

    with open(os.path.join(out, "anticipation.json"), "w") as f:
        json.dump(results, f, indent=1)
    names = ["native", "cold"] + [f"prewarm+{d}" for d in DELAYS_MS] + ["warm"]
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("| Command | " + " | ".join(names) + " |\n")
        f.write("|---" * (len(names) + 1) + "|\n")
        for cmd, rows in results["commands"].items():
            cells = [f"{rows[n]['median_ms']:.0f} ms ({rows[n]['fast']}/{runs})" for n in names]
            f.write(f"| `aws {cmd}` | " + " | ".join(cells) + " |\n")
        f.write(f"\nDaemon start-up to first spare: {results['startup_ms']} ms\n")
    print(open(os.path.join(out, "summary.md")).read())


if __name__ == "__main__":
    main()
