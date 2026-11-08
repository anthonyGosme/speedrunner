"""Simon Willison's `llm`: served by speedrunner or not.

Run by inside.sh, in a throwaway container, from /p. Two installs: /bare (llm
alone) and /plugins (llm with four model plugins). For each call: whether it is
served (SPEEDRUNNER_REQUIRE_FAST=1 exits 124 when it would run natively), why
not (SPEEDRUNNER_DEBUG=1), the output against the native call, and median times
over runs alternated with the native ones.
"""
import os
import statistics
import subprocess
import time

RUNS = 20

CASES = [
    ("llm --version", "/bare", ["llm", "--version"]),
    ("llm models", "/bare", ["llm", "models"]),
    ("llm plugins", "/bare", ["llm", "plugins"]),
    ("llm --version, 4 plugins", "/plugins", ["llm", "--version"]),
    ("llm models, 4 plugins", "/plugins", ["llm", "models"]),
    ("llm plugins, 4 plugins", "/plugins", ["llm", "plugins"]),
]


def run(cmd, venv, extra=None):
    env = dict(os.environ, PATH=f"{venv}/bin:" + os.environ["PATH"], **(extra or {}))
    t = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, env=env)
    return time.perf_counter() - t, p


def served(cmd, venv):
    """Exit code under REQUIRE_FAST, and the debug lines (why native)."""
    _, p = run(cmd, venv, {"SPEEDRUNNER_REQUIRE_FAST": "1", "SPEEDRUNNER_DEBUG": "1"})
    dbg = [line for line in p.stderr.decode(errors="replace").splitlines()
           if "speedrunner" in line.lower()]
    return p.returncode, dbg


def main():
    print(subprocess.run(["python3", "--version"], capture_output=True, text=True).stdout.strip())
    print(subprocess.run(["speedrunner", "--version"], capture_output=True, text=True).stdout.strip())
    print(f"median of {RUNS} alternated runs\n", flush=True)
    for label, venv, native in CASES:
        fast = ["srr", *native]
        print(f"== {label} ({venv})")
        code, dbg = None, []
        for _ in range(40):  # the first calls start the daemon and let it learn
            code, dbg = served(fast, venv)
            if code != 124:
                break
            time.sleep(0.5)
        print(f"   REQUIRE_FAST exit: {code} ({'served' if code != 124 else 'NOT served'})")
        for line in dbg[-4:]:
            print("     debug:", line[:200])
        _, pn = run(native, venv)
        _, pf = run(fast, venv)
        same = (pn.returncode, pn.stdout, pn.stderr) == (pf.returncode, pf.stdout, pf.stderr)
        lines = pn.stdout.decode(errors="replace").splitlines()
        print(f"   native exit {pn.returncode}, {len(lines)} stdout lines, first: {lines[:1]}")
        print(f"   same output as native: {same}")
        if not same:
            print("     native:", pn.returncode, pn.stdout[:300], pn.stderr[:300])
            print("     served:", pf.returncode, pf.stdout[:300], pf.stderr[:300])
        for c in (native, fast):  # warm both paths once more
            run(c, venv)
        tn, tf = [], []
        for _ in range(RUNS):
            tn.append(run(native, venv)[0])
            tf.append(run(fast, venv)[0])
        mn, mf = statistics.median(tn) * 1000, statistics.median(tf) * 1000
        print(f"   median: native {mn:.0f} ms, speedrunner {mf:.0f} ms, x{mn / mf:.2f}\n", flush=True)


if __name__ == "__main__":
    main()
