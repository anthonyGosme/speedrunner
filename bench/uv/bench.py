"""Tools called through `uv run` and `uvx`: served by speedrunner or not.

Run by inside.sh, in a throwaway container, from a uv project in /p that has
black. For each way of calling black: whether the call is served
(SPEEDRUNNER_REQUIRE_FAST=1 exits 124 when it would run natively), why not
(SPEEDRUNNER_DEBUG=1), the output against the native call, and median times
over runs alternated with the native ones.

    python3 -I bench.py before   # 0.4.9: srr in front of uv, or after it by hand
    python3 -I bench.py client   # this branch: `srr uv run` steps in by itself
"""
import os
import statistics
import subprocess
import sys
import time

RUNS = 20
HOME = os.environ["HOME"]
BLACK = "black==24.10.0"
VENV_BLACK = [".venv/bin/black", "--version"]

ROUNDS = {
    # 0.4.9. Careful: under `speedrunner exec`, REQUIRE_FAST checks exec
    # itself, not black; only the times tell whether black was served there.
    "before": [
        ("venv script, direct", VENV_BLACK, ["srr", *VENV_BLACK]),
        ("uv tool install", [f"{HOME}/.local/bin/black", "--version"],
         ["srr", f"{HOME}/.local/bin/black", "--version"]),
        ("srr uv run", ["uv", "run", "black", "--version"],
         ["srr", "uv", "run", "black", "--version"]),
        ("uv run, under exec -t black", ["uv", "run", "black", "--version"],
         ["speedrunner", "exec", "-t", "black", "uv", "run", "black", "--version"]),
        ("srr uvx", ["uvx", BLACK, "--version"], ["srr", "uvx", BLACK, "--version"]),
        ("uvx, under exec -t black", ["uvx", BLACK, "--version"],
         ["speedrunner", "exec", "-t", "black", "uvx", BLACK, "--version"]),
        # srr after uv, by hand: uv prepares the environment, then runs srr.
        # REQUIRE_FAST reaches srr through uv: exit 124 really means native.
        ("uv run srr", ["uv", "run", "black", "--version"],
         ["uv", "run", "srr", "black", "--version"]),
        ("uv run --frozen srr", ["uv", "run", "--frozen", "black", "--version"],
         ["uv", "run", "--frozen", "srr", "black", "--version"]),
        ("uvx --from black srr", ["uvx", BLACK, "--version"],
         ["uvx", "--from", BLACK, "srr", "black", "--version"]),
    ],
    # This branch: the client puts itself in the command's place.
    "client": [
        ("venv script, direct", VENV_BLACK, ["srr", *VENV_BLACK]),
        ("srr uv run", ["uv", "run", "black", "--version"],
         ["srr", "uv", "run", "black", "--version"]),
        ("srr uv -q run --frozen", ["uv", "-q", "run", "--frozen", "black", "--version"],
         ["srr", "uv", "-q", "run", "--frozen", "black", "--version"]),
        ("srr uv run, a file to format", ["uv", "run", "black", "-q", "--check", "fmt.py"],
         ["srr", "uv", "run", "black", "-q", "--check", "fmt.py"]),
        ("uv run, under exec -t uv", ["uv", "run", "black", "--version"],
         ["speedrunner", "exec", "-t", "uv", "uv", "run", "black", "--version"]),
        ("srr uv run python (left to uv)", ["uv", "run", "python", "-c", "pass"],
         ["srr", "uv", "run", "python", "-c", "pass"]),
    ],
}


def run(cmd, extra=None):
    env = dict(os.environ, **(extra or {}))
    t = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, env=env)
    return time.perf_counter() - t, p


def served(cmd):
    """Exit code under REQUIRE_FAST, and the debug lines (why native)."""
    _, p = run(cmd, {"SPEEDRUNNER_REQUIRE_FAST": "1", "SPEEDRUNNER_DEBUG": "1"})
    dbg = [line for line in p.stderr.decode(errors="replace").splitlines()
           if "speedrunner" in line.lower()]
    return p.returncode, dbg


def main(round_name):
    print(subprocess.run(["uv", "--version"], capture_output=True, text=True).stdout.strip())
    print(subprocess.run(["speedrunner", "--version"], capture_output=True, text=True).stdout.strip())
    print(f"round: {round_name}, median of {RUNS} alternated runs\n", flush=True)
    for label, native, fast in ROUNDS[round_name]:
        print(f"== {label}")
        print("   native:", " ".join(native))
        print("   served:", " ".join(fast))
        code, dbg = None, []
        for _ in range(40):  # the first calls start the daemon
            code, dbg = served(fast)
            if code != 124:
                break
            time.sleep(0.5)
        print(f"   REQUIRE_FAST exit: {code} ({'served' if code != 124 else 'NOT served'})")
        for line in dbg[-4:]:
            print("     debug:", line[:200])
        _, pn = run(native)
        _, pf = run(fast)
        same = (pn.returncode, pn.stdout, pn.stderr) == (pf.returncode, pf.stdout, pf.stderr)
        first = pn.stdout.decode(errors="replace").splitlines()[:1]
        print(f"   native exit {pn.returncode}, stdout: {first}")
        print(f"   same output as native: {same}")
        if not same:
            print("     native:", pn.returncode, pn.stdout[:200], pn.stderr[:200])
            print("     served:", pf.returncode, pf.stdout[:200], pf.stderr[:200])
        for c in (native, fast):
            run(c)
        tn, tf = [], []
        for _ in range(RUNS):
            tn.append(run(native)[0])
            tf.append(run(fast)[0])
        mn, mf = statistics.median(tn) * 1000, statistics.median(tf) * 1000
        print(f"   median: native {mn:.0f} ms, speedrunner {mf:.0f} ms, x{mn / mf:.2f}\n", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
