#!/usr/bin/env python3
"""Fetches the panel's tools into /tools, one by one, at the versions of versions.tsv:
the measure is one to compare and to redo. A tool that fails is noted and left out.
Stops at the size budget (J0_BUDGET_MB)."""
import json, os, re, shutil, subprocess, sys, tarfile, urllib.request, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from tools import TOOLS, MC
ROOT = "/tools"; DL = ROOT + "/.dl"; BUDGET = int(os.environ.get("J0_BUDGET_MB", "3000")); os.makedirs(DL, exist_ok=True) if not os.environ.get("J0_CHECK") else None
UA = {"User-Agent": "speedrunner-bench"}
PINNED = dict(l.rstrip("\n").split("\t", 1) for l in open(os.path.join(os.path.dirname(__file__), "versions.tsv")))
def fetch(url, path=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        if path is None: return r.read()
        with open(path, "wb") as f: shutil.copyfileobj(r, f)
def maven_url(path, suffix, v):
    art = path.rsplit("/", 1)[1]
    if suffix in ("jar", "tar.gz", "zip"): name = f"{art}-{v}.{suffix}"
    else: name = f"{art}-{v}-{suffix}"
    return f"{MC}/{path}/{v}/{name}", v
def gh_url(repo, pattern, tag):
    d = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/tags/{tag}"))
    for a in d["assets"]:
        if re.search(pattern, a["name"]): return a["browser_download_url"], d["tag_name"]
    raise RuntimeError(f"no asset {pattern} in {d['tag_name']}")
def size_mb():
    return sum(os.path.getsize(os.path.join(p, f)) for p, _, fs in os.walk(ROOT) for f in fs if not os.path.islink(os.path.join(p, f))) // (1 << 20)
def install(name, source):
    dest = f"{ROOT}/{name}"
    if os.path.exists(dest + "/.ok"): return "already there"
    shutil.rmtree(dest, ignore_errors=True); os.makedirs(dest)
    kind = source[0]
    if kind == "clojure":
        for path, in (("org/clojure/clojure",), ("org/clojure/spec.alpha",), ("org/clojure/core.specs.alpha",)):
            art = path.rsplit("/", 1)[1]
            url, v = maven_url(path, "jar", PINNED[f"clojure:{art}"]); fetch(url, f"{dest}/{art}.jar")
        version = "see versions.tsv"
    else:
        if kind == "maven": url, version = maven_url(source[1], source[2], PINNED[name])
        elif kind == "gh": url, version = gh_url(source[1], source[2], PINNED[name])
        else: url, version = source[1], source[1].rsplit("/", 1)[1]
        file = url.rsplit("/", 1)[1]; path = f"{DL}/{file}"; fetch(url, path)
        if file.endswith((".tar.gz", ".tgz")):
            with tarfile.open(path) as t: t.extractall(dest, filter="data")
        elif file.endswith(".zip"):
            subprocess.run(["unzip", "-q", path, "-d", dest], check=True)
        elif file.endswith(".jar"):
            shutil.copy(path, f"{dest}/{name}.jar")
        else:
            shutil.copy(path, f"{dest}/{name}"); os.chmod(f"{dest}/{name}", 0o755)
        os.remove(path)
    open(dest + "/.ok", "w").write(str(version)); return version
def check(name, source):
    """J0_CHECK=1: that each pinned version still resolves, without fetching it."""
    kind = source[0]
    if kind == "clojure":
        urls = [maven_url(f"org/clojure/{a}", "jar", PINNED[f"clojure:{a}"])[0] for a in ("clojure", "spec.alpha", "core.specs.alpha")]
    elif kind == "maven": urls = [maven_url(source[1], source[2], PINNED[name])[0]]
    elif kind == "gh": urls = [gh_url(source[1], source[2], PINNED[name])[0]]
    else: urls = [source[1]]
    for url in urls:
        urllib.request.urlopen(urllib.request.Request(url, headers=UA, method="HEAD"), timeout=60).close()
for name, (family, source, _) in TOOLS.items():
    if source is None: continue
    if os.environ.get("J0_CHECK"):
        try: check(name, source); print(f"{name}: ok", flush=True)
        except Exception as exc: print(f"{name}: FAILED {exc!r}", flush=True)
        continue
    used = size_mb()
    if used > BUDGET:
        print(f"{name}: skipped, {used} MB used", flush=True); continue
    try:
        print(f"{name}: {install(name, source)} ({size_mb()} MB in all)", flush=True)
    except Exception as exc:
        print(f"{name}: FAILED {exc!r}", flush=True); shutil.rmtree(f"{ROOT}/{name}", ignore_errors=True)
