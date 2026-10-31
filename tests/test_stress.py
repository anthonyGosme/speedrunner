"""Stress (races): for STRESS_SECONDS, STRESS_WORKERS threads call the test
tools of every runtime installed here through speedrunner, in parallel,
while a disruptor stops their daemons, kills them (kill -9) and makes them
stale (a touched script). Daemons restart, learn and serve all along.

Every call must:
  - end within the harness's timeout (no hang);
  - print and exit as the native run does (a native fallback is fine: it is
    correct, only slower);
  - never exit 125 ("lost track"), but where that is the documented outcome:
    a daemon killed by kill -9 while serving a run that only its wait status
    reports (PHP);
  - run once: each `hold` call marks a file of its own, start then done.

Env: as test_matrix.py (HS_PATH with the fixtures' bin directories),
RB_GEM_HOME as test_ruby.py;
STRESS_SECONDS (default 90), STRESS_WORKERS (default 8), STRESS_SEED.

Run: python3 tests/test_stress.py -v
"""
import collections
import os
import random
import re
import shutil
import signal
import subprocess
import threading
import time
import unittest

import test_matrix as m

HS_BIN = m.HS_BIN
if os.environ.get("RB_GEM_HOME"):  # where tests/fixture/rbtest is installed
    m.BASE_ENV.update(GEM_HOME=os.environ["RB_GEM_HOME"], GEM_PATH=os.environ["RB_GEM_HOME"])
SECONDS = float(os.environ.get("STRESS_SECONDS", "90"))
WORKERS = int(os.environ.get("STRESS_WORKERS", "8"))
SEED = int(os.environ.get("STRESS_SEED", str(int(time.time()))))
MARK = "{mark}"  # a fresh marker file, in a hold call's arguments

# tool -> [(args, options)], options: tty, input, env, wait_only (reports
# its end through the wait status only)
COMMANDS = {
    "hstest": [(["argv", "a b", "--x=1"], {}), (["lazy"], {}), (["exit", "int:3"], {}),
               (["exit", "raise"], {}), (["echo-stdin"], {"input": b"in\x00put\n" * 50}),
               (["hold", "0.2", MARK], {}), (["tty"], {"tty": True}), (["interleave"], {"tty": True})],
    "hsthread": [([], {})],
    "rbtest": [(["argv", "a b"], {}), (["lazy"], {}), (["exit", "false"], {}), (["exit", "raise"], {}),
               (["hold", "0.2", MARK], {}), (["tty"], {"tty": True})],
    "rbthread": [([], {})],
    "phptest": [(["argv", "x"], {"wait_only": True}), (["exit", "int:3"], {"wait_only": True}),
                (["globals"], {"wait_only": True}), (["hold", "0.2", MARK], {"wait_only": True}),
                (["tty"], {"tty": True, "wait_only": True})],
    "nodetest": [(["argv", "x"], {}), (["lazy"], {}), (["exit", "code:3"], {}),
                 (["exit", "throw"], {}), (["hold", "0.2", MARK], {}),
                 (["wrapped-exit", "natural"], {"env": {"NODETEST_WRAPPER": "1"}}), (["tty"], {"tty": True})],
    "nodetty": [([], {}), ([], {"tty": True})],
}
HOLD = re.compile(rb"ready \d+")


def runnable(path):
    """The script's interpreter is here too (a fixture's bin/ may be on
    PATH in an image without its runtime)."""
    with open(path, "rb") as f:
        words = f.readline()[2:].split()
    if not words:
        return False
    interp = words[1] if os.path.basename(words[0]) == b"env" and len(words) > 1 else words[0]
    return shutil.which(os.fsdecode(interp), path=m.BASE_ENV["PATH"]) is not None


def installed():
    tools = {}
    for tool, commands in COMMANDS.items():
        path = shutil.which(tool, path=m.BASE_ENV["PATH"])
        if path and runnable(path):
            tools[tool] = (os.path.realpath(path), commands)
    return tools


def daemon_pid(tool):
    pid = m.status_field(m.status(tool), "pid")
    return int(pid) if pid and pid.isdigit() else None


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Stress(unittest.TestCase):
    maxDiff = None

    def test_parallel_calls_while_daemons_restart(self):
        tools = installed()
        self.assertTrue(tools, "no test tool on HS_PATH")
        rng = random.Random(SEED)
        expected, lock = {}, threading.Lock()
        failures, calls, kills = [], collections.Counter(), collections.defaultdict(list)
        deadline = time.monotonic() + SECONDS
        print(f"\nstress: {', '.join(sorted(tools))}; {WORKERS} workers, {SECONDS:.0f} s, seed {SEED}",
              flush=True)

        def native(tool, args, opts, key):
            with lock:
                if key not in expected:
                    argv = [tool] + [a.replace(MARK, os.path.join(m.ROOT, "native-mark")) for a in args]
                    p = m.run("native", argv, input=opts.get("input", b""), tty=opts.get("tty", False),
                              env=opts.get("env"))
                    expected[key] = (p.returncode, HOLD.sub(b"ready N", p.stdout), p.stderr)
                return expected[key]

        def one_call(wrng):
            tool = wrng.choice(sorted(tools))
            k = wrng.randrange(len(tools[tool][1]))
            args, opts = tools[tool][1][k]
            mark = os.path.join(m.ROOT, f"mark-{threading.get_ident()}-{time.monotonic_ns()}")
            argv = [tool] + [a.replace(MARK, mark) for a in args]
            want = native(tool, args, opts, (tool, k))
            t0 = time.monotonic()
            try:
                p = m.run("hs", argv, input=opts.get("input", b""), tty=opts.get("tty", False),
                          env=opts.get("env"), require_fast=False)
            except subprocess.TimeoutExpired:
                failures.append(f"{' '.join(argv)}: hung (no answer within the harness's timeout)")
                return
            got = (p.returncode, HOLD.sub(b"ready N", p.stdout), p.stderr)
            calls[tool] += 1
            if p.returncode == 125:
                killed = any(t0 <= t <= time.monotonic() for t in kills[tool])
                if not (opts.get("wait_only") and killed):
                    failures.append(f"{' '.join(argv)}: lost track (125): {p.stderr[-200:]!r}")
                return
            if got != want:
                failures.append(f"{' '.join(argv)}: differs from native\n  native {want!r}\n  served {got!r}")
            if MARK in args and m.read_lines(mark) != ["start", "done"]:
                failures.append(f"{' '.join(argv)}: marks {m.read_lines(mark)} (ran twice, or not to its end)")

        def worker(n):
            wrng = random.Random(SEED * 1000 + n)
            while time.monotonic() < deadline and len(failures) < 20:
                one_call(wrng)

        def disruptor():
            while time.monotonic() < deadline and len(failures) < 20:
                time.sleep(rng.uniform(1.0, 4.0))
                tool = rng.choice(sorted(tools))
                action = rng.choice(["stop", "kill", "touch"])
                if action == "stop":
                    subprocess.run([HS_BIN, "stop", tool], env=m.BASE_ENV, capture_output=True)
                elif action == "kill":
                    pid = daemon_pid(tool)
                    if pid:
                        kills[tool].append(time.monotonic())
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                else:
                    os.utime(tools[tool][0])  # stale: the daemon restarts
                calls[f"disrupt:{action}"] += 1

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(WORKERS)]
        threads.append(threading.Thread(target=disruptor))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        print("stress: " + ", ".join(f"{k} {v}" for k, v in sorted(calls.items())), flush=True)
        self.assertEqual(failures, [], f"seed {SEED}")


def tearDownModule():
    if HS_BIN:
        for tool in COMMANDS:
            m.stop(tool)
    shutil.rmtree(m.ROOT, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
