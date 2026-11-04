"""Versions of a daemon compared through one client: the same commands, in turn,
in random order within each round, a native run before each served one (as
bench/panel.py). Differences are paired by round, with a bootstrap interval.

A copy of one version among the versions (an A/A control) shows what the method
itself produces. No timeout in subprocess.run: Popen.wait(timeout) polls with a
doubling sleep, and rounds every time up to 8, 16, 32, 64, 114 ms...

Usage: python3 daemon_ab.py <dir> <rounds> <version,version> "<cmd>;<cmd>"
<dir>/<version>/ holds that version's daemon files. Writes <dir>/ab-<time>.json.
"""
import json
import os
import random
import shlex
import shutil
import statistics as st
import subprocess
import sys
import tempfile
import time

import panel

SR = "/root/speedrunner/repo/client/target/release/speedrunner"
BASE, rounds, names, cmds = (sys.argv[1], int(sys.argv[2]), sys.argv[3].split(","),
                             [shlex.split(c) for c in sys.argv[4].split(";")])
scratch = tempfile.mkdtemp(prefix="ab-", dir=BASE)
work = os.path.join(scratch, "work")
os.makedirs(work)
for name, text in panel.SAMPLES.items():
    with open(os.path.join(work, name), "w") as f:
        f.write(text)
base = {**os.environ, "PATH": panel.PATH, "LANG": "C.UTF-8", "COMPOSER_ALLOW_SUPERUSER": "1",
        "AWS_EC2_METADATA_DISABLED": "true", "GEM_HOME": "/root/speedrunner/gems-rb",
        "GEM_PATH": "/root/speedrunner/gems-rb"}


def env(v):
    run_dir = os.path.join(scratch, v, "run")
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    return {**base, "SPEEDRUNNER_DAEMON": f"{BASE}/{v}/speedrunner_daemon.py", "XDG_RUNTIME_DIR": run_dir,
            "XDG_CACHE_HOME": os.path.join(scratch, v, "cache"), "ANSIBLE_LOCAL_TEMP": os.path.join(scratch, v, "ans")}


def run(argv, e):
    t = time.perf_counter()
    p = subprocess.run(argv, cwd=work, env=e, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (time.perf_counter() - t) * 1000, p.returncode


def paired(a, b, n=4000):
    d = [x - y for x, y in zip(a, b)]
    ms = sorted(st.median(random.choices(d, k=len(d))) for _ in range(n))
    return st.median(d), ms[int(n * .025)], ms[int(n * .975)]


out = {}
for cmd in cmds:
    es = {v: env(v) for v in names}
    srv = [SR, "run", cmd[0], "--", *cmd[1:]]
    for v in names:
        for _ in range(14):
            run(srv, es[v])
            time.sleep(.2)
    time.sleep(.5)
    served = {v: run(srv, {**es[v], "SPEEDRUNNER_REQUIRE_FAST": "1"})[1] != 124 for v in names}
    t = {v: [] for v in names}
    for _ in range(rounds):
        order = names[:]
        random.shuffle(order)
        for v in order:
            run(cmd, base)
            t[v].append(run(srv, es[v])[0])
    label = " ".join(cmd)
    print(f"\n{label}  (n={rounds}, against {names[0]})", flush=True)
    for v in names:
        d, lo, hi = paired(t[v], t[names[0]])
        print(f"  {v:8} median {st.median(t[v]):7.2f}  diff {d:+6.2f} [{lo:+.2f},{hi:+.2f}]  served={served[v]}", flush=True)
    out[label] = t
    for v in names:
        subprocess.run([SR, "stop", cmd[0]], env=es[v], capture_output=True, cwd=work)
with open(os.path.join(BASE, f"ab-{int(time.time())}.json"), "w") as f:
    json.dump(out, f)
shutil.rmtree(scratch, ignore_errors=True)
