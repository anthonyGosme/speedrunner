#!/usr/bin/env python3
"""Ceiling for a PyInstaller tool, aws v2: read its modules from its own binary, preload,
fork a child per call. No client. usage: ceiling.py DIST RUNS"""
import importlib.abc, importlib.machinery, marshal, os, select, statistics as st, struct, subprocess, sys, tempfile, threading, time, zlib
DIST, RUNS = sys.argv[1], int(sys.argv[2]); EXE = DIST + "/aws"; COOKIE = b"MEI\014\013\012\013\016"
blob = open(EXE, "rb").read(); i = blob.rfind(COOKIE); pkg_len, toc_off, toc_len, pyver = struct.unpack("!IIII", blob[i + 8:i + 24]); start = i + 88 - pkg_len
toc, p = [], start + toc_off
while p < start + toc_off + toc_len:
    size, off, dlen, ulen, cflag, typ = struct.unpack("!IIIIBc", blob[p:p + 18]); toc.append((off, dlen, ulen, cflag, typ, blob[p + 18:p + size].rstrip(b"\0").decode())); p += size
print("archive entries", len(toc), "kinds", {t[4] for t in toc}, "scripts", [t[5] for t in toc if t[4] == b"s"][:8], flush=True)
pz = next(t for t in toc if t[4] == b"z"); pyz = blob[start + pz[0]:start + pz[0] + pz[1]]
assert pyz[:4] == b"PYZ\0"; print("pyc magic same as this python:", pyz[4:8] == importlib.util.MAGIC_NUMBER if hasattr(importlib, "util") else "?", flush=True)
ztoc = marshal.loads(pyz[struct.unpack("!i", pyz[8:12])[0]:]); ztoc = dict(ztoc) if not isinstance(ztoc, dict) else ztoc
print("modules in the PYZ", len(ztoc), flush=True)
class Load(importlib.abc.Loader):
    def __init__(self, real): self.real = real
    def create_module(self, spec): return None
    def exec_module(self, m):
        e = ztoc[self.real]
        if e[0] == 3: return  # a namespace package: no code
        before = threads()
        exec(marshal.loads(zlib.decompress(pyz[e[1]:e[1] + e[2]])), m.__dict__)
        if threads() != before: print(f"native threads {before} -> {threads()} in {self.real}", flush=True)
class Finder:
    """As PyInstaller: on the path entries under the bundle, so that aliases (botocore for awscli.botocore) work."""
    def __init__(self, path):
        if path != DIST and not path.startswith(DIST + "/"): raise ImportError
        rel = os.path.relpath(path, DIST); self.prefix = "" if rel == "." else rel.replace("/", ".") + "."; self.path = path
        self.files = importlib.machinery.FileFinder(path, (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES))
    def invalidate_caches(self): self.files.invalidate_caches()
    def find_spec(self, fullname, target=None):
        real = self.prefix + fullname.rpartition(".")[2]; e = ztoc.get(real)
        if e is None: return self.files.find_spec(fullname, target)
        pkg = e[0] in (1, 3); base = os.path.join(DIST, *real.split("."))
        spec = importlib.machinery.ModuleSpec(fullname, Load(real), is_package=pkg, origin=base + ("/__init__.pyc" if pkg else ".pyc"))
        if pkg: spec.submodule_search_locations = [base]
        spec.has_location = True; return spec
import importlib.util
sys.path_hooks.insert(0, Finder); sys.path_importer_cache.clear(); sys.frozen = True; sys._MEIPASS = DIST
sys.path.insert(0, DIST); sys.path.append(DIST + "/python3.14/lib-dynload")
os.environ["AWS_DATA_PATH"] = os.environ.get("AWS_DATA_PATH", "")
import re
def norm(r): return r[1], re.sub(rb"[0-9.]+ ?[KM]iB/s", b"#", r[2]), r[3]  # a transfer's speed differs each time
def threads(): return len(os.listdir("/proc/self/task"))
COMMANDS = [["--debug", "configure", "list"], ["--version"], ["configure", "list"], ["ec2", "run-instances", "--generate-cli-skeleton"]]
def threads(): return len(os.listdir("/proc/self/task"))
# The CRT's logging thread (AwsLogger) starts as awscli.logger loads: put it off to the child.
import awscrt.io
real_init_logging, deferred = awscrt.io.init_logging, []
def later(*a, **k):
    if os.getpid() == os.environ.get("J0_PARENT_PID") or not FORKED: deferred.append((a, k)); return None
    return real_init_logging(*a, **k)
FORKED = False
awscrt.io.init_logging = later
t = time.perf_counter(); import awscli.clidriver; print(f"import awscli.clidriver {1000*(time.perf_counter()-t):.0f} ms; {len(sys.modules)} modules; threads python {threading.active_count()} native {threads()}", flush=True)
def child(argv, out, err):
    import signal; signal.alarm(25)  # a deadlock must show, not hang
    global FORKED
    FORKED = True
    for a, k in deferred: real_init_logging(*a, **k)
    fo, fe = os.open(out, os.O_CREAT | os.O_TRUNC | os.O_WRONLY), os.open(err, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
    os.dup2(fo, 1); os.dup2(fe, 2); sys.argv = ["aws", *argv]
    try: code = awscli.clidriver.main()
    except SystemExit as e: code = e.code if isinstance(e.code, int) else 0 if e.code is None else 1
    sys.stdout.flush(); sys.stderr.flush(); os._exit(code or 0)
def forked(argv, out="/tmp/o", err="/tmp/e"):
    t = time.perf_counter(); pid = os.fork()
    if pid == 0: child(argv, out, err)
    _, s = os.waitpid(pid, 0); dt = (time.perf_counter() - t) * 1000
    return dt, os.waitstatus_to_exitcode(s), open(out, "rb").read(), open(err, "rb").read()
def native(argv):
    t = time.perf_counter(); p = subprocess.run([EXE, *argv], capture_output=True, stdin=subprocess.DEVNULL); return (time.perf_counter() - t) * 1000, p.returncode, p.stdout, p.stderr
# Level 2: what a run imports beyond this, learned in a child, then imported here.
r, w = os.pipe(); pid = os.fork()
if pid == 0:
    FORKED = True
    for a_, k_ in deferred: real_init_logging(*a_, **k_)
    os.close(r); before = set(sys.modules); fo = os.open("/dev/null", os.O_WRONLY); os.dup2(fo, 1); os.dup2(fo, 2)
    for a in COMMANDS:
        sys.argv = ["aws", *a]
        try: awscli.clidriver.main()
        except SystemExit: pass
    os.write(w, "\n".join(sorted(set(sys.modules) - before)).encode()); os._exit(0)
os.close(w); learned = b""
while True:
    c = os.read(r, 65536)
    if not c: break
    learned += c
os.waitpid(pid, 0); names = [n for n in learned.decode().split("\n") if n]; bad = 0
for n in names:
    try: __import__(n)
    except BaseException: bad += 1
print(f"learned {len(names)} modules, {bad} failed; threads python {threading.active_count()} native {threads()}", flush=True)
import gc; gc.collect(); gc.freeze()
for a in COMMANDS:
    n, f = [], []; same = 0; dn = None; df = None
    for _ in range(RUNS):
        x = native(a); y = forked(a); n.append(x[0]); f.append(y[0]); dn, df = x, y
        same += x[1:] == y[1:]
    mn, mf = st.median(n), st.median(f)
    print(f"aws {' '.join(a):42} native {mn:6.0f} ms forked {mf:6.0f} ms x{mn/mf:5.2f} same {same}/{RUNS}", flush=True)
    if same < RUNS: print("   native:", dn[1], dn[2][:90], dn[3][-120:], "\n   forked:", df[1], df[2][:90], df[3][-200:], flush=True)
print("threads at the end", threading.active_count(), threads(), flush=True)
# Real requests, to a local endpoint that answers anything: the paths that use the network, signing and the CRT.
import http.server
srv = subprocess.Popen([sys.executable, "-I", "-c", """
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def _r(self):
        n = int(self.headers.get('content-length') or 0)
        if n: self.rfile.read(n)
        if self.headers.get('expect'): pass
        body = b'<?xml version="1.0"?><ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Owner><ID>x</ID></Owner><Buckets><Bucket><Name>b</Name><CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket></Buckets></ListAllMyBucketsResult>' if b'Action' not in self.path.encode() else b''
        self.send_response(200); self.send_header('Content-Type','application/xml'); self.send_header('ETag','"x"'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    do_GET=do_PUT=do_POST=do_HEAD=do_DELETE=_r
    def log_message(self,*a): pass
http.server.ThreadingHTTPServer(('127.0.0.1',9000),H).serve_forever()
"""]); time.sleep(0.5)
os.environ.update(AWS_ACCESS_KEY_ID="AKIATEST", AWS_SECRET_ACCESS_KEY="secret", AWS_DEFAULT_REGION="us-east-1", AWS_PAGER="", AWS_EC2_METADATA_DISABLED="true")
open("/tmp/up.bin","wb").write(b"x"*200000)
open("/tmp/crt.cfg","w").write("[default]\ns3 =\n  preferred_transfer_client = crt\n")
E_ = ["--endpoint-url", "http://127.0.0.1:9000"]
REQ = [("s3api list-buckets", ["s3api","list-buckets",*E_], {}), ("s3 ls", ["s3","ls",*E_], {}), ("s3 cp (classic)", ["s3","cp","/tmp/up.bin","s3://b/up.bin",*E_], {}),
       ("s3 cp (CRT)", ["s3","cp","/tmp/up.bin","s3://b/up.bin",*E_], {"AWS_CONFIG_FILE":"/tmp/crt.cfg"})]
for label, a, extra in REQ:
    saved = dict(os.environ); os.environ.update(extra); n=[]; f=[]; same=0; bad=0; dn=df=None
    for _ in range(RUNS):
        x=native(a); y=forked(a); n.append(x[0]); f.append(y[0]); dn,df=x,y
        same += norm(x)==norm(y); bad += y[1] in (-14,-11,-6)
    mn,mf=st.median(n),st.median(f); print(f"aws {label:26} native {mn:6.0f} ms forked {mf:6.0f} ms x{mn/mf:5.2f} same {same}/{RUNS} killed/crashed {bad} rc {dn[1]}/{df[1]}",flush=True)
    if same<RUNS: print("   native:",dn[1],dn[2][:110],dn[3][-140:],"\n   forked:",df[1],df[2][:110],df[3][-240:],flush=True)
    os.environ.clear(); os.environ.update(saved)
# Is the CRT really used, natively and in a child? Its transfer client says so in the debug log.
os.environ["AWS_CONFIG_FILE"] = "/tmp/crt.cfg"; a = ["s3","cp","/tmp/up.bin","s3://b/up.bin",*E_,"--debug"]
for name, r in (("native", native(a)), ("forked", forked(a))):
    log = r[3].decode(errors="replace"); hits = [l for l in log.split("\n") if "crt" in l.lower() and "s3" in l.lower()]
    print(f"--debug {name}: rc {r[1]}, {len(hits)} lines naming the CRT; first: {hits[0][:150] if hits else None}", flush=True)
os.environ.pop("AWS_CONFIG_FILE")
# Stability: many forks of the same parent.
bad=0; t0=time.perf_counter()
for i in range(300):
    y=forked(["configure","list"]); bad += y[1]!=0
print(f"300 forks of configure list: {bad} failures, {time.perf_counter()-t0:.1f} s, threads {threading.active_count()} {threads()}",flush=True)
srv.kill()
