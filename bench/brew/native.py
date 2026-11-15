# brew B0: native times of brew commands, Ruby ones and bash-only ones (Intel Mac paths).
import os, statistics, subprocess, sys, time
ENV = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "HOMEBREW_NO_ENV_HINTS": "1"}
CMDS = [
    ["--version"], ["--prefix"], ["list", "--formula"],                      # bash
    ["info", "gh"], ["info", "--json=v2", "gh"], ["deps", "gh"], ["desc", "gh"],
    ["uses", "--installed", "openssl@3"], ["leaves"], ["config"], ["commands"],
    ["--cache", "gh"], ["outdated"], ["--env"], ["formulae"],
]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
for args in CMDS:
    ts = []
    for _ in range(N + 2):
        t = time.perf_counter()
        p = subprocess.run(["brew", *args], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        ts.append((time.perf_counter() - t) * 1000)
    ts = ts[2:]
    print(f"brew {' '.join(args):30s} median {statistics.median(ts):7.1f} ms  min {min(ts):7.1f}  rc={p.returncode}")
