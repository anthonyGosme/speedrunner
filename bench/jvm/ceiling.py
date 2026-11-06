#!/usr/bin/env python3
"""The ceiling: a CRaC JDK, the tool's classes loaded (and initialised) before a checkpoint,
main called with its arguments on restore. No client, arguments baked in, one working directory.
usage: ceiling.py OUT RUNS"""
import glob, json, os, re, shutil, statistics, subprocess, sys, time, zipfile
OUT, RUNS = sys.argv[1], int(sys.argv[2])
Z = glob.glob("/tools/zulu-crac/*/")[0]; JAVA = Z + "bin/java"
WORK = "/tmp/work"; shutil.rmtree(WORK, ignore_errors=True); shutil.copytree("/j0/samples", WORK); os.makedirs("/tmp/p", exist_ok=True)
subprocess.run([Z + "bin/javac", "-nowarn", "-d", "/tmp/p", "/j0/Preload.java", "/j0/Noop.java"], check=True, capture_output=True)
def jar(name): return f"/tools/{name}/{name}.jar"
def manifest_main(path):
    with zipfile.ZipFile(path) as z:
        text = z.read("META-INF/MANIFEST.MF").decode(errors="replace").replace("\r\n ", "").replace("\n ", "")
    return re.search(r"Main-Class:\s*(\S+)", text).group(1)
def J(label, name, *args):
    p = jar(name); return label, p, manifest_main(p), list(args)
CASES = [
    ("(restore floor) Noop", "/tmp/p", "Noop", []),
    J("checkstyle --version", "checkstyle", "--version"),
    J("checkstyle Hello.java", "checkstyle", "-c", "/sun_checks.xml", "Hello.java"),
    J("openapi-generator version", "openapi-generator", "version"),
    J("openapi-generator validate", "openapi-generator", "validate", "-i", "spec.yaml"),
    J("swagger-codegen version", "swagger-codegen", "version"),
    J("plantuml -version", "plantuml", "-version"),
    J("plantuml sample.puml", "plantuml", "-tsvg", "sample.puml"),
    J("closure-compiler --version", "closure-compiler", "--version"),
    J("bfg --version", "bfg", "--version"),
    J("detekt --version", "detekt", "--version"),
    J("quarkus --version", "quarkus", "--version"),
    J("trino --version", "trino", "--version"),
    ("clojure -e", "/tools/clojure/*", "clojure.main", ["-e", "(+ 1 2)"]),
    J("jython -c", "jython", "-c", "print(1)"),
    ("javac Hello.java", "/tmp/p", "com.sun.tools.javac.Main", ["-d", "out", "Hello.java"]),
    ("pmd check Hello.java", glob.glob("/tools/pmd/*/")[0] + "lib/*", "net.sourceforge.pmd.cli.PmdCli", ["check", "--no-progress", "--no-cache", "-R", "rulesets/java/quickstart.xml", "-d", "Hello.java"]),
]
def timed(argv):
    t = time.perf_counter()
    try:
        p = subprocess.run(argv, cwd=WORK, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        return (time.perf_counter() - t) * 1000, p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 60000, "timeout", b"", b""
def norm(d): return re.sub(rb"\d+", b"#", d)
def clean(err):  # what the restore itself prints, with a timestamp or a warning
    return b"\n".join(l for l in err.split(b"\n") if b"warp:" not in l and b"[crac]" not in l and b"Warp" not in l)
results = []
for label, cp, main, args in CASES:
    native = [JAVA, "-cp", cp, main, *args]
    _, rc0, out0, err0 = timed(native)
    log = "/tmp/cl.log"; subprocess.run([JAVA, "-cp", cp, f"-Xlog:class+load:file={log}", main, *args], cwd=WORK, capture_output=True, timeout=60)
    names = []
    for line in open(log, errors="replace"):
        m = re.search(r"\] (\S+) source:", line)
        if m and "shared objects file" not in line and "$$Lambda" not in m.group(1) and "/0x" not in m.group(1): names.append(m.group(1))
    open("/tmp/classes.txt", "w").write("\n".join(names))
    rec = {"label": label, "classes": len(names), "rc": rc0}
    for init in ("load", "init"):
        ck = f"/cache/ck-{init}"; shutil.rmtree(ck, ignore_errors=True)
        flags = [f"-Dj0.init={'true' if init == 'init' else 'false'}"]
        p = subprocess.run([JAVA, "-XX:CRaCEngine=warp", f"-XX:CRaCCheckpointTo={ck}", *flags, "-cp", "/tmp/p:" + cp, "Preload", "/tmp/classes.txt", main, *args],
                           cwd=WORK, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
        made = os.path.isdir(ck) and bool(os.listdir(ck))
        rec[f"checkpoint_{init}"] = made
        if made:
            rec[f"image_mb_{init}"] = round(sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(ck) for f in fs) / (1 << 20), 1)
        else:
            rec[f"checkpoint_err_{init}"] = (p.stdout[-200:] + p.stderr[-300:]).decode(errors="replace")
    times = {"native": [], "load": [], "init": []}; same = {"load": 0, "init": 0}; sample = {}
    for _ in range(RUNS):
        times["native"].append(timed(native)[0])
        for init in ("load", "init"):
            if not rec[f"checkpoint_{init}"]: continue
            dt, rc, o, e = timed([JAVA, "-XX:CRaCEngine=warp", f"-XX:CRaCRestoreFrom=/cache/ck-{init}"])
            times[init].append(dt)
            if rc == rc0 and norm(o) == norm(out0) and norm(clean(e)) == norm(err0): same[init] += 1
            elif init not in sample: sample[init] = {"rc": rc, "stdout": o[:200].decode(errors="replace"), "stderr": e[-300:].decode(errors="replace")}
    rec.update(times=times, same=same, sample=sample, median={k: statistics.median(v) for k, v in times.items() if v})
    results.append(rec)
    med = rec["median"]; f = lambda k: f"x{med['native']/med[k]:5.2f}" if k in med else "  -  "
    print(f"{label:30} rc={rc0!s:4} native {med['native']:6.0f} | load {med.get('load', 0):6.0f} {f('load')} same {same['load']:2} | init {med.get('init', 0):6.0f} {f('init')} same {same['init']:2} | classes {len(names)} image {rec.get('image_mb_init')}", flush=True)
    json.dump(results, open(f"{OUT}/ceiling.json", "w"), indent=1)
    for init in ("load", "init"): shutil.rmtree(f"/cache/ck-{init}", ignore_errors=True)
