"""brew B1: each command natively and served, alternated, all times kept.
usage: served.py <speedrunner binary> <runs> 'cmd args' ..."""
import os, statistics, subprocess, sys, time
BIN, RUNS = sys.argv[1], int(sys.argv[2])
ENV = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "HOMEBREW_NO_ENV_HINTS": "1"}
SERVED = {**ENV, "SPEEDRUNNER_REQUIRE_FAST": "1"}
def once(argv, env):
    t = time.perf_counter()
    p = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return (time.perf_counter() - t) * 1000, p.returncode, p.stdout
for cmd in sys.argv[3:]:
    args = cmd.split()
    native, served, same = [], [], True
    for i in range(RUNS + 2):
        tn, rn, on = once(["brew", *args], ENV)
        ts, rs, os_ = once([BIN, "run", "brew", "--", *args], SERVED)
        if i >= 2:
            native.append(tn); served.append(ts)
            same &= (rn, on) == (rs, os_)
    mn, ms = statistics.median(native), statistics.median(served)
    print(f"brew {cmd:34s} native {mn:7.1f} ms  served {ms:7.1f} ms  x{mn / ms:.2f}  same={same}"
          f"  native=[{', '.join(f'{t:.0f}' for t in native)}]  served=[{', '.join(f'{t:.0f}' for t in served)}]", flush=True)
