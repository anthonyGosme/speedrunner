"""Python tools called as `python -m <module>`: served by speedrunner or not.

Run by inside.sh, in a throwaway container, from /p with a venv's python3
first on PATH. For each call: whether it is served (SPEEDRUNNER_REQUIRE_FAST=1
exits 124 when it would run natively), why not (SPEEDRUNNER_DEBUG=1), the
output against the native call, and median times over runs alternated with
the native ones.
"""
import os
import statistics
import subprocess
import sys
import time

RUNS = 20


def pym(*args):
    return ["python3", "-m", *args]


CASES = [
    ("pytest script, for reference", ["pytest", "--version"], ["srr", "pytest", "--version"]),
    ("pytest --version", pym("pytest", "--version"), ["srr", *pym("pytest", "--version")]),
    ("pytest, one test file", pym("pytest", "-q", "-p", "no:cacheprovider", "test_one.py"),
     ["srr", *pym("pytest", "-q", "-p", "no:cacheprovider", "test_one.py")]),
    ("black --version", pym("black", "--version"), ["srr", *pym("black", "--version")]),
    ("black, a file checked", pym("black", "-q", "--check", "fmt.py"),
     ["srr", *pym("black", "-q", "--check", "fmt.py")]),
    ("mypy --version", pym("mypy", "--version"), ["srr", *pym("mypy", "--version")]),
    ("pip --version", pym("pip", "--version"), ["srr", *pym("pip", "--version")]),
    ("json.tool (stdlib)", pym("json.tool", "in.json"), ["srr", *pym("json.tool", "in.json")]),
    ("pytest under exec -t python3", pym("pytest", "--version"),
     ["speedrunner", "exec", "-t", "python3", *pym("pytest", "--version")]),
    ("python3 -c (left native)", ["python3", "-c", "pass"], ["srr", "python3", "-c", "pass"]),
]


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


def main():
    print(subprocess.run(["python3", "--version"], capture_output=True, text=True).stdout.strip())
    print(subprocess.run(["speedrunner", "--version"], capture_output=True, text=True).stdout.strip())
    print(f"median of {RUNS} alternated runs\n", flush=True)
    for label, native, fast in CASES:
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
    main()
