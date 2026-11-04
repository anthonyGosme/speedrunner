"""A/B of the client's latency, old daemon against new, alternating; then a loop of 50."""
import json, math, os, shutil, statistics as st, subprocess, sys, tempfile, time
sr, old, new, bindir = sys.argv[1:5]
root = tempfile.mkdtemp(prefix="ab-", dir="/tmp")
def env(name, daemon):
    d = os.path.join(root, name); os.makedirs(d, mode=0o700, exist_ok=True)
    return {"PATH": bindir + ":/usr/bin:/bin", "HOME": os.environ["HOME"], "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": d,
            "XDG_CACHE_HOME": os.path.join(d, "cache"), "SPEEDRUNNER_DAEMON": daemon, "SPEEDRUNNER_REQUIRE_FAST": "1",
            "AWS_EC2_METADATA_DISABLED": "true"}
envs = {"old": env("old", old), "new": env("new", new)}
cmds = [["aws", "--version"], ["aws", "ec2", "run-instances", "--generate-cli-skeleton"], ["black", "--version"]]
def one(e, cmd):
    t = time.perf_counter()
    r = subprocess.run([sr, "run", cmd[0], "--", *cmd[1:]], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert r.returncode == 0, (cmd, r.returncode)
    return (time.perf_counter() - t) * 1000
for cmd in cmds:
    for e in envs.values():
        subprocess.run([sr, "prewarm", cmd[0]], env=e)
        time.sleep(3)
        for _ in range(4):
            one(e, cmd); time.sleep(0.5)
    res = {k: [] for k in envs}
    for i in range(int(sys.argv[5])):
        for k, e in envs.items():
            res[k].append(one(e, cmd)); time.sleep(0.15)
    print(" ".join(cmd)[:30].ljust(31), *(f"{k} {st.median(v):6.1f} ms (p90 {sorted(v)[int(len(v)*.9)]:6.1f})" for k, v in res.items()), flush=True)
    for k, e in envs.items():
        print("   ", k, subprocess.run([sr, "status", cmd[0]], env=e, capture_output=True, text=True).stdout.strip()[:95])
for k, e in list(envs.items()) * 3:
    t = time.perf_counter()
    for _ in range(50):
        one(e, cmds[0])
    print(f"loop of 50 aws --version {k}: {time.perf_counter() - t:5.2f} s", flush=True)
for e in envs.values():
    for c in ("aws", "black"):
        subprocess.run([sr, "stop", c], env=e, stdout=subprocess.DEVNULL)
shutil.rmtree(root, ignore_errors=True)
