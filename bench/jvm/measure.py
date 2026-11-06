#!/usr/bin/env python3
"""J0: each command natively, with a dynamic CDS archive, with an AOT cache, and with an
AOT cache plus flags for short runs. Interleaved; every time kept; output compared.
usage: measure.py smoke | measure OUT [RUNS]"""
import glob, json, os, re, shutil, statistics, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from tools import TOOLS
SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
TIMEOUT = 60
def base_env():
    dirs = glob.glob("/tools/*/*/bin") + glob.glob("/tools/*/bin") + ["/tools/ktlint", "/tools/liquibase"]
    return {"PATH": ":".join(["/opt/java/openjdk/bin", *dirs, "/usr/bin", "/bin"]), "JAVA_HOME": "/opt/java/openjdk", "HOME": "/root", "LANG": "C.UTF-8"}
ENV = base_env()
def commands():
    only = os.environ.get("J0_ONLY")
    for tool, (family, source, cmds) in TOOLS.items():
        if source is not None and not os.path.exists(f"/tools/{tool}/.ok"): continue
        for label, argv in cmds:
            if not only or any(w in label for w in only.split(",")): yield tool, family, label, argv
WORK = "/tmp/work"  # one path: a tool may print it
def workdir():
    shutil.rmtree(WORK, ignore_errors=True); shutil.copytree(SAMPLES, WORK); return WORK
def scrub(data):
    """What the injection itself prints: the product would pass real options."""
    return b"\n".join(l for l in data.split(b"\n") if not l.startswith((b"Picked up JAVA_TOOL_OPTIONS", b"NOTE: Picked up")))
def norm(data):
    """Clocks and durations differ between any two runs, natively too."""
    return re.sub(rb"\d+", b"#", data)
def once(argv, opts=None):
    env = dict(ENV)
    if opts: env["JAVA_TOOL_OPTIONS"] = opts
    d = workdir(); t = time.perf_counter()
    try:
        p = subprocess.run(argv, env=env, cwd=d, capture_output=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT)
        return time.perf_counter() - t, p.returncode, p.stdout, scrub(p.stderr)
    except subprocess.TimeoutExpired:
        return TIMEOUT, "timeout", b"", b""
    except FileNotFoundError as exc:
        return 0, "not found", b"", str(exc).encode()
    finally:
        shutil.rmtree(d, ignore_errors=True)
def smoke():
    for tool, family, label, argv in commands():
        dt, rc, out, err = once(argv)
        print(f"{family:8} {label:34} rc={rc!s:9} {dt*1000:7.0f} ms | {(out or err)[:70]!r}", flush=True)
MODES = {
    "native": lambda p: None,
    "cds": lambda p: f"-XX:SharedArchiveFile={p}.jsa -XX:+AutoCreateSharedArchive",
    "aot": lambda p: f"-XX:AOTCache={p}.aot -XX:AOTMode=auto",
    "aot_tuned": lambda p: f"-XX:AOTCache={p}.tuned.aot -XX:AOTMode=auto -XX:+UseSerialGC -XX:TieredStopAtLevel=1",
}
TRAIN = {
    "cds": lambda p: f"-XX:SharedArchiveFile={p}.jsa -XX:+AutoCreateSharedArchive",
    "aot": lambda p: f"-XX:AOTCacheOutput={p}.aot",
    "aot_tuned": lambda p: f"-XX:AOTCacheOutput={p}.tuned.aot -XX:+UseSerialGC -XX:TieredStopAtLevel=1",
}
def measure(out, runs):
    os.makedirs(out, exist_ok=True); results = []
    for i, (tool, family, label, argv) in enumerate(commands()):
        prefix = f"/cache/{i}"; rec = {"tool": tool, "family": family, "label": label, "argv": argv, "times": {m: [] for m in MODES}, "same": {}, "train_s": {}, "cache_mb": {}, "diff": {}}
        _, rc0, out0, err0 = once(argv)
        rec["rc"] = rc0
        if rc0 in ("timeout", "not found"):
            print(f"{label}: left out, {rc0}", flush=True); results.append(rec); continue
        for mode, opts in TRAIN.items():
            t = time.perf_counter(); once(argv, opts(prefix)); rec["train_s"][mode] = round(time.perf_counter() - t, 2)
            files = glob.glob(prefix + (".jsa" if mode == "cds" else ".tuned.aot" if mode == "aot_tuned" else ".aot"))
            rec["cache_mb"][mode] = round(sum(os.path.getsize(f) for f in files) / (1 << 20), 1)
        for r in range(runs):
            for mode, opts in MODES.items():
                dt, rc, o, e = once(argv, opts(prefix))
                rec["times"][mode].append(round(dt * 1000, 1))
                same = rc == rc0 and norm(o) == norm(out0) and norm(e) == norm(err0)
                rec["same"].setdefault(mode, []).append(same)
                if not same and mode not in rec["diff"]:
                    rec["diff"][mode] = {"rc": rc, "stdout": o[:300].decode(errors="replace"), "stderr": e[:600].decode(errors="replace"),
                                         "native_stderr": err0[:300].decode(errors="replace")}
        med = {m: statistics.median(v) for m, v in rec["times"].items()}
        rec["median_ms"] = med; rec["same"] = {m: sum(v) for m, v in rec["same"].items()}
        print(f"{family:8} {label:32} rc={rc0!s:4} native {med['native']:7.0f} | cds x{med['native']/med['cds']:4.2f} aot x{med['native']/med['aot']:4.2f} tuned x{med['native']/med['aot_tuned']:4.2f} | same {rec['same']} | MB {rec['cache_mb']}", flush=True)
        results.append(rec)
        json.dump(results, open(f"{out}/results.json", "w"), indent=1)
        for f in glob.glob(prefix + "*"): os.remove(f)
if sys.argv[1] == "smoke": smoke()
else: measure(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 11)
