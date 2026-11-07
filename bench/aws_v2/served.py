import http.server, json, os, re, shutil, statistics as st, subprocess, sys, tempfile, threading, time
B = "/aws/target/release/speedrunner"; AWS = "/aws/bin/aws"; RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 11
R = tempfile.mkdtemp(prefix="e2e-"); os.makedirs(R + "/.aws/cli", mode=0o700); os.makedirs(R + "/run", mode=0o700)
open(R + "/.aws/cli/alias", "w").write("[toplevel]\nenvdump = !env | sort\nargdump = !f() { echo \"$@\"; }; f\n")
class H(http.server.BaseHTTPRequestHandler):
    def _r(self):
        n = int(self.headers.get("content-length") or 0)
        if n: self.rfile.read(n)
        b = b'<?xml version="1.0"?><ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Owner><ID>x</ID></Owner><Buckets><Bucket><Name>b</Name><CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket></Buckets></ListAllMyBucketsResult>'
        self.send_response(200); self.send_header("Content-Type", "application/xml"); self.send_header("ETag", '"x"'); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    do_GET = do_PUT = do_POST = do_HEAD = _r
    def log_message(self, *a): pass
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", 9000), H).serve_forever, daemon=True).start()
ENV = {"PATH": "/aws/bin:/usr/bin:/bin", "HOME": R, "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": R + "/run", "XDG_CACHE_HOME": R + "/cache", "XDG_DATA_HOME": R + "/data",
       "AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret", "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true"}
open("/tmp/up.bin", "wb").write(b"x" * 200000)
E_ = ["--endpoint-url", "http://127.0.0.1:9000"]
CMDS = [["--version"], ["configure", "list"], ["ec2", "run-instances", "--generate-cli-skeleton"], ["s3api", "list-buckets", *E_], ["s3", "ls", *E_], ["s3", "cp", "/tmp/up.bin", "s3://b/up.bin", *E_],
        ["nosuchcommand"], ["s3", "ls", "--nosuchoption"], ["envdump"], ["argdump", "a b", "c"], ["configure", "get", "region"], ["sts", "get-caller-identity", "--region", "us-east-1", *E_]]
def run(argv, extra=None, stdin=subprocess.DEVNULL):
    t = time.perf_counter(); p = subprocess.run(argv, env={**ENV, **(extra or {})}, capture_output=True, stdin=stdin, timeout=60, cwd="/tmp")
    return (time.perf_counter() - t) * 1000, p.returncode, p.stdout, p.stderr
def norm(x): return x[1], re.sub(rb"[0-9.]+ ?[KM]iB/s", b"#", x[2]), x[3]
print("start:", run([B, "run", "aws", "--", "--version"])[1:3], flush=True)
for _ in range(300):
    s = run([B, "status", "aws"])[2].decode()
    if s.startswith("ready"): break
    time.sleep(0.05)
print("status:", s[:150], flush=True)
for log in __import__("glob").glob(R + "/run/speedrunner/aws-*.log"): print("log:", open(log, errors="replace").read()[-700:], flush=True)
fast = {"SPEEDRUNNER_REQUIRE_FAST": "1"}
for round_ in range(2):
    print(f"--- round {round_}", flush=True)
    for a in CMDS:
        n, f = [], []; same = 0; x = y = None
        for _ in range(RUNS if round_ else 2):
            x = run([AWS, *a]); y = run([B, "run", "aws", "--", *a], fast); n.append(x[0]); f.append(y[0]); same += norm(x) == norm(y)
        mn, mf = st.median(n), st.median(f)
        print(f"aws {' '.join(a)[:46]:46} native {mn:6.0f} ms served {mf:6.0f} ms x{mn/mf:5.2f} same {same} rc {x[1]}/{y[1]}", flush=True)
        if same < (RUNS if round_ else 2):
            for tag, v in (("native", x), ("served", y)): print(f"   {tag}: rc {v[1]} out {v[2][:150]!r} err {v[3][-200:]!r}", flush=True)
            if a == ["envdump"]:
                nx = dict(l.split(b"=", 1) for l in x[2].split(b"\n") if b"=" in l); sv = dict(l.split(b"=", 1) for l in y[2].split(b"\n") if b"=" in l)
                for k in sorted(set(nx) | set(sv)):
                    if nx.get(k) != sv.get(k): print("     env", k, "native", nx.get(k), "served", sv.get(k), flush=True)
    time.sleep(1)
print("status:", run([B, "status", "aws"])[2].decode()[:160], flush=True)
for log in __import__("glob").glob(R + "/run/speedrunner/aws-*.log"): print("log tail:", open(log, errors="replace").read()[-500:], flush=True)
run([B, "stop", "aws"])
for f in __import__("glob").glob(R + "/cache/speedrunner/aws-*.json"):
    d = json.load(open(f)); print("CACHE", os.path.basename(f), "learned", len(d.get("modules", [])), "paths", d.get("paths"), flush=True)
    by = {}
    for k, v in (d.get("blacklist") or {}).items(): by.setdefault(v[:110], []).append(k)
    for why, names in sorted(by.items(), key=lambda kv: -len(kv[1])): print(f"  {len(names):3} {why}: {names[:9]}", flush=True)
