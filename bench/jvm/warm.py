import glob, os, shutil, statistics, subprocess, time
Z = glob.glob("/tools/zulu-crac/*/")[0]; J = Z + "bin/java"; W = "/tmp/work"
shutil.copytree("/j0/samples", W, dirs_exist_ok=True); os.makedirs("/tmp/p", exist_ok=True)
subprocess.run([Z + "bin/javac", "-nowarn", "--add-exports", "jdk.compiler/com.sun.tools.javac=ALL-UNNAMED", "-d", "/tmp/p", "/j0/WarmJavac.java"], check=True, capture_output=True)
def t(argv):
    s = time.perf_counter(); p = subprocess.run(argv, cwd=W, capture_output=True, stdin=subprocess.DEVNULL, timeout=60); return (time.perf_counter() - s) * 1000, p.returncode
native = [J, "-cp", "/tmp/p", "com.sun.tools.javac.Main", "-d", "out", "Hello.java"]
times = {"native": []}
for warm in (0, 1, 10, 50):
    ck = f"/cache/w{warm}"
    subprocess.run([J, "-XX:CRaCEngine=warp", f"-XX:CRaCCheckpointTo={ck}", "-cp", "/tmp/p", "WarmJavac", str(warm)], cwd=W, capture_output=True, stdin=subprocess.DEVNULL, timeout=180)
    times[warm] = []
for _ in range(11):
    times["native"].append(t(native)[0])
    for warm in (0, 1, 10, 50):
        dt, rc = t([J, "-XX:CRaCEngine=warp", f"-XX:CRaCRestoreFrom=/cache/w{warm}"]); assert rc == 0, rc; times[warm].append(dt)
n = statistics.median(times["native"]); print(f"javac Hello.java, native {n:.0f} ms")
for warm in (0, 1, 10, 50):
    m = statistics.median(times[warm]); print(f"  {warm:3} compiles before the checkpoint: {m:.0f} ms, x{n/m:.2f}, image {sum(os.path.getsize(os.path.join(a,f)) for a,_,fs in os.walk(f'/cache/w{warm}') for f in fs)>>20} MB")
