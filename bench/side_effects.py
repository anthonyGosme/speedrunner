"""What a command does to the machine, run plainly against served by a daemon.

bench/panel.py compares exit code, stdout and stderr. It cannot see what code
run at load does once in the daemon instead of once per call: a file appended
to, a PID or a clock kept. This script measures that, and only measures.

For each command, in new setups of their own (HOME, working directory, TMPDIR,
XDG directories, all under one root):
  plain A, plain B   the command run plainly, twice over: what differs between
                     them varies by itself (timestamps, random names), and is
                     left out
  null               a third plain run, compared like the served one: what it
                     reports is noise the two baselines did not teach
  served             the command served, after the daemon is warm, the fast
                     path required
Each runs WARM calls, then CALLS measured ones, CALL_GAP seconds apart. After
each call every file under the root is recorded (path, size, hash, mtime), so
what each call changed can be compared, not just the end state: an append at
load grows per call in the plain setups, and not in the served one.

What is reported, plain against served:
  file-*     a path changing per call in one and not the other, or differently
  state-*    a path present or different at the end only, no per-call change
  frozen     a value in the output that changes across plain calls and stays
             the same across served calls: a PID or a clock kept at load
  output     a value in the output, stable in plain, that differs when served
  exit-code  the same, for the exit code
speedrunner's own files (under .../speedrunner/) are not recorded. A coarse
look for files modified outside the roots is reported apart, per setup.

Before any real tool, a fixture must come out as expected: each kind of effect
reported, in Python and in Ruby, and no difference for tools without one.

Usage: python3 side_effects.py <speedrunner binary> <out_dir> [--fixture-only]
Env:   CALLS (default 5), WARM (4), CALL_GAP (1.1 s, so that a clock printed to
       the second moves), PANEL_ONLY (as panel.py), SIDE_PANEL=candidates
       (bench/candidates.py's list), OUTSIDE (colon-separated directories).
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("SIDE_PANEL") == "candidates":
    import candidates  # noqa: F401  sets panel's lists
import panel  # noqa: E402

CALLS = int(os.environ.get("CALLS", "5"))
WARM = int(os.environ.get("WARM", "4"))
GAP = float(os.environ.get("CALL_GAP", "1.1"))
HASH_LIMIT = 32 << 20  # above: size and mtime stand for the content
OUTSIDE = [d for d in os.environ.get("OUTSIDE", f"/tmp:/var/tmp:/dev/shm:{os.path.expanduser('~')}").split(":")
           if os.path.isdir(d)]


# ---------------------------------------------------------------- one setup

class Setup:
    """A new root and the env panel.py uses, with TMPDIR and XDG under it."""

    def __init__(self, parent, name):
        self.root = os.path.join(parent, name)  # same length for every setup: sizes stay comparable
        os.makedirs(self.root)
        self.work, self.env, self.cwds = panel.make_setup(self.root)
        self.env.update({k: os.path.join(self.root, v) for k, v in
                         {"TMPDIR": "tmp", "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state"}.items()})
        os.makedirs(self.env["TMPDIR"])
        self.own = {os.path.join(self.env[k], "speedrunner")
                    for k in ("XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME")}
        self.own_parents = {os.path.dirname(p) for p in self.own}  # there for speedrunner, walked but not recorded
        self.names = [p.encode() for p in {self.root, os.path.realpath(self.root)}]
        self.digests = {}

    def normal(self, data):
        for name in self.names:
            data = data.replace(name, b"<root>")
        return data

    def digest(self, path, st):
        key = (st.st_size, st.st_mtime_ns, st.st_ino)
        known = self.digests.get(path)
        if known and known[0] == key:
            return known[1]
        if st.st_size > HASH_LIMIT:
            value = f"large:{st.st_size}:{st.st_mtime_ns}"
        else:
            try:
                with open(path, "rb") as f:
                    value = hashlib.sha1(self.normal(f.read())).hexdigest()
            except OSError as e:
                value = f"unreadable:{e.errno}"
        self.digests[path] = (key, value)
        return value

    def snapshot(self):
        """Path under the root -> (type, size, mtime_ns, hash, mode)."""
        found = {}
        stack = [self.root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for e in entries:
                if e.path in self.own:
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = os.path.relpath(e.path, self.root)
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISDIR(st.st_mode):
                    if e.path not in self.own_parents:
                        found[rel] = ("d", 0, 0, "", mode)
                    stack.append(e.path)
                elif stat.S_ISLNK(st.st_mode):
                    found[rel] = ("l", 0, 0, self.normal(os.readlink(e.path).encode()).decode(errors="replace"), 0)
                elif stat.S_ISREG(st.st_mode):
                    found[rel] = ("f", st.st_size, st.st_mtime_ns, self.digest(e.path, st), mode)
                else:
                    found[rel] = ("o", 0, 0, "", mode)
        return found

    def run(self, argv, cwd, extra=None):
        return subprocess.run(argv, cwd=cwd, env={**self.env, **(extra or {})}, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)


def delta(pre, post):
    """What one call changed: path -> (kind, size change, hash)."""
    events = {}
    for rel in pre.keys() | post.keys():
        a, b = pre.get(rel), post.get(rel)
        if a == b:
            continue
        if a is None:
            events[rel] = ("created", b[1], b[3])
        elif b is None:
            events[rel] = ("deleted", -a[1], "")
        elif a[0] != b[0]:
            events[rel] = ("retyped", b[1] - a[1], b[3])
        elif a[3] != b[3]:
            if b[1] == a[1]:
                events[rel] = ("rewrote", 0, b[3])
            else:  # the whole file's hash says how many calls came before, not what this one did
                events[rel] = ("grew" if b[1] > a[1] else "shrank", b[1] - a[1], "")
        elif a[2] != b[2]:
            events[rel] = ("touched", 0, b[3])  # same content written again
        else:
            events[rel] = ("chmod", 0, b[3])
    return events


class Run:
    """One setup's measured calls."""

    def __init__(self):
        self.events, self.outs, self.final = [], [], {}
        self.outside = []

    def paths(self):
        return {rel for call in self.events for rel in call}


def calls_on(setup, call, on_miss=None):
    """Run the measured calls; `call` returns a CompletedProcess. A call that
    refused to run (on_miss) ran nothing: it is not counted."""
    run = Run()
    time.sleep(GAP)
    pre = setup.snapshot()
    while len(run.events) < CALLS:
        r = call()
        if on_miss and r.returncode == 124:
            if not on_miss():
                return None
            time.sleep(GAP)
            pre = setup.snapshot()
            continue
        time.sleep(GAP)  # what a served child does after handing back its exit code, and a plain run's stragglers
        post = setup.snapshot()
        run.events.append(delta(pre, post))
        run.outs.append((r.returncode, setup.normal(r.stdout), setup.normal(r.stderr)))
        pre = post
    run.final = pre
    return run


def look_outside(marker, prune):
    """Files modified since `marker` outside the root: coarse, by mtime."""
    if not OUTSIDE:
        return []
    pruned = sum((["-path", p, "-o"] for p in prune), [])[:-1]
    out = subprocess.run(["find", *OUTSIDE, "-xdev", "(", *pruned, ")", "-prune", "-o",
                          "-type", "f", "-newer", marker, "-print"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return sorted(filter(None, out.stdout.decode(errors="replace").split("\n")))


# ---------------------------------------------------------------- comparing

def ancestors(rel):
    """Deepest first, the root left out: a family that wide would hide everything."""
    while True:
        rel = os.path.dirname(rel)
        if not rel:
            return
        yield rel


def learn_names(pa, pb):
    """Paths only one baseline has, under a directory where the other has its
    own: random names. They are compared as one family, by how many per call."""
    only_b = set()
    for p in pb - pa:
        only_b.update(ancestors(p))
    families = set()
    for p in pa - pb:
        for d in ancestors(p):
            if d in only_b:
                families.add(d)
                break
    return families


def key_of(rel, known, families):
    if rel in known:
        return rel
    for d in ancestors(rel):
        if d in families:
            return d + "/*"
    return rel


def profiles(run, known, families):
    """Group -> per call: kinds, total size change, hash of the hashes."""
    grouped = {}
    for i, call in enumerate(run.events):
        for rel, e in call.items():
            grouped.setdefault(key_of(rel, known, families), [[] for _ in run.events])[i].append(e)
    return {key: {"kinds": [",".join(sorted(e[0] for e in c)) or "-" for c in calls],
                  "sizes": [sum(e[1] for e in c) for c in calls],
                  "hashes": [hashlib.sha1("".join(sorted(e[2] for e in c)).encode()).hexdigest()[:12] for c in calls]}
            for key, calls in grouped.items()}


def compare_files(a, b, x):
    """Differences of x from what the baselines a and b agree on."""
    pa, pb = a.paths(), b.paths()
    known, families = pa & pb, learn_names(pa, pb)
    pra, prb, prx = (profiles(r, known, families) for r in (a, b, x))
    diffs, noise = [], []
    for key in sorted(pra.keys() | prb.keys() | prx.keys()):
        ga, gb, gx = pra.get(key), prb.get(key), prx.get(key)
        if (ga is None) != (gb is None):
            noise.append(key)
        elif ga is None:
            diffs.append({"kind": "file-only-served", "path": key, "strength": "strong", "served": gx["kinds"],
                          "served_sizes": gx["sizes"]})
        elif gx is None:
            diffs.append({"kind": "file-only-plain", "path": key,
                          "strength": "strong" if ga["kinds"] == gb["kinds"] else "weak",
                          "plain": ga["kinds"], "plain_sizes": ga["sizes"]})
        elif ga["kinds"] != gb["kinds"]:
            noise.append(key)
        elif gx["kinds"] != ga["kinds"]:
            diffs.append({"kind": "file-changes", "path": key, "strength": "strong",
                          "plain": ga["kinds"], "served": gx["kinds"]})
        elif ga["sizes"] == gb["sizes"] != gx["sizes"]:
            diffs.append({"kind": "file-size", "path": key, "strength": "strong",
                          "plain": ga["sizes"], "served": gx["sizes"]})
        elif ga["hashes"] == gb["hashes"] != gx["hashes"]:
            diffs.append({"kind": "file-content", "path": key, "strength": "strong"})

    # The end state, for what no measured call touched: what the warm calls
    # or the daemon's own load left behind.
    seen = pra.keys() | prb.keys() | prx.keys() | pa | pb | x.paths()
    fa, fb, fx = a.final, b.final, x.final
    known, families = set(fa) & set(fb), learn_names(set(fa), set(fb))
    ka, kb = ({key_of(rel, known, families) for rel in f} for f in (fa, fb))
    kx = {key_of(rel, known, families) for rel in fx}
    for key in sorted(ka & kb - kx):
        if key not in seen:
            diffs.append({"kind": "state-missing", "path": key, "strength": "strong"})
    for key in sorted(kx - ka - kb):
        if key not in seen:
            diffs.append({"kind": "state-extra", "path": key, "strength": "strong"})
    for rel in sorted(known & set(fx)):
        if rel not in seen and fa[rel][3] == fb[rel][3] != fx[rel][3]:
            diffs.append({"kind": "state-content", "path": rel, "strength": "strong",
                          "plain_size": fa[rel][1], "served_size": fx[rel][1]})
    return diffs, sorted(set(noise))


TOKEN = re.compile(rb"\s+|\w+(?:[.:\-]\w+)*|.", re.S)
DURATION = re.compile(rb"\d+\.\d+(ms|s)?")


def columns(versions):
    """One line across calls, split into tokens when every call has as many."""
    split = [TOKEN.findall(v) for v in versions]
    return split if len({len(s) for s in split}) == 1 else [[v] for v in versions]


def show(value):
    return value.decode(errors="replace")[:300]


def changes_each_call(values):
    return all(p != q for p, q in zip(values, values[1:]))


def compare_stream(stream, a, b, x, durations):
    """a, b, x: one stream's text per call. Values that vary across plain
    calls are expected to vary when served; the others to be the same."""
    diffs = []
    plain = a + b
    lines = [t.split(b"\n") for t in plain]
    served = [t.split(b"\n") for t in x]
    if len({len(t) for t in lines}) != 1:
        # No line-by-line alignment: lines every plain call printed must be there.
        always = set(lines[0]).intersection(*lines[1:])
        for t in served:
            for line in sorted(always - set(t)):
                diffs.append({"kind": "output", "stream": stream, "strength": "strong", "plain": show(line),
                              "served": "(line absent)"})
        return diffs[:20]
    if len({len(t) for t in served}) != 1 or len(served[0]) != len(lines[0]):
        return [{"kind": "output", "stream": stream, "strength": "strong",
                 "plain": f"{len(lines[0])} lines", "served": str(sorted({len(t) for t in served})) + " lines",
                 "plain_tail": show(plain[0][-80:]), "served_tail": show(x[0][-80:])}]
    n = len(a)
    for i in range(len(lines[0])):
        versions = [t[i] for t in lines]
        cols_p = columns(versions)
        cols_s = columns([t[i] for t in served])
        if len(cols_p[0]) != len(cols_s[0]):
            if len(set(versions)) == 1:
                diffs.append({"kind": "output", "stream": stream, "line": i, "strength": "strong",
                              "plain": show(versions[0]), "served": show(served[0][i])})
            continue
        for j in range(len(cols_p[0])):
            values = [c[j] for c in cols_p]
            served_values = [c[j] for c in cols_s]
            if values[0].isspace():
                continue
            if len(set(values)) == 1:
                if set(served_values) != {values[0]}:
                    if durations and DURATION.fullmatch(values[0]):
                        continue  # panel.py normalises these too: "in 0.01s"
                    diffs.append({"kind": "output", "stream": stream, "line": i, "strength": "strong",
                                  "plain": show(values[0]), "served": [show(v) for v in served_values]})
            elif len(set(served_values)) == 1:
                every = changes_each_call(values[:n]) and changes_each_call(values[n:])
                diffs.append({"kind": "frozen", "stream": stream, "line": i,
                              "strength": "strong" if every else "weak",
                              "plain": [show(v) for v in values[:n]], "served": show(served_values[0])})
    return diffs


def compare_outputs(a, b, x, durations):
    diffs = []
    rc = {o[0] for o in a.outs + b.outs}
    if len(rc) == 1 and {o[0] for o in x.outs} != rc:
        diffs.append({"kind": "exit-code", "strength": "strong", "plain": sorted(rc),
                      "served": [o[0] for o in x.outs]})
    for index, stream in ((1, "stdout"), (2, "stderr")):
        diffs += compare_stream(stream, [o[index] for o in a.outs], [o[index] for o in b.outs],
                                [o[index] for o in x.outs], durations)
    return diffs


def same_diff(p, q):
    return all(p.get(k) == q.get(k) for k in ("kind", "path", "stream", "line"))


def compare(a, b, x, durations):
    files, noise = compare_files(a, b, x)
    return files + compare_outputs(a, b, x, durations), noise


# ---------------------------------------------------------------- one command

def first_lines(run):
    """What the run's first call said, for reading a difference afterwards."""
    return {"rc": run.outs[0][0], "stdout": show(run.outs[0][1][-300:]), "stderr": show(run.outs[0][2][-300:])}


def plain_run(parent, name, argv, cwd_key):
    setup = Setup(parent, name)
    cwd = setup.cwds[cwd_key]
    marker = os.path.join(parent, name + ".marker")
    open(marker, "w").close()
    for _ in range(WARM):
        setup.run(argv, cwd)
    run = calls_on(setup, lambda: setup.run(argv, cwd))
    run.outside = look_outside(marker, [parent + "*"] + PRUNE)
    return run


def served_run(sr, parent, name, argv, cwd_key):
    """Returns (Run or None, row fields)."""
    setup = Setup(parent, name)
    cwd = setup.cwds[cwd_key]
    marker = os.path.join(parent, name + ".marker")
    open(marker, "w").close()
    fast = [sr, "run", argv[0], "--", *argv[1:]]
    target = argv[:2] if argv[0] == "php" else argv[:1]
    required = {"SPEEDRUNNER_REQUIRE_FAST": "1"}
    info = {"misses": 0}

    def status():
        prev, deadline = None, time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(0.5)
            cur = setup.run([sr, "status", *target], cwd).stdout.decode().strip()
            if cur == prev and cur.startswith("ready") and " learning=" not in cur:
                break
            prev = cur
        return re.sub(r"\bpid=\d+ preload_ms=\d+ ", "", prev or "")

    def warm():
        """True once a served call leaves the preloaded state as it was: the
        status comes from a spare, which may predate what the last call taught.
        A module learned during the measured calls would count as their effect."""
        for _ in range(WARM - 1):
            setup.run(fast, cwd)
        before = status()
        for _ in range(8):
            check = setup.run(fast, cwd, required)
            if check.returncode == 124:
                info["refused"] = check.stderr.decode(errors="replace").strip()[-200:]
                for _ in range(WARM - 1):  # may be a file the warm calls changed
                    setup.run(fast, cwd)
                before = status()
                continue
            info.pop("refused", None)
            time.sleep(GAP)
            after = status()
            if after == before:
                info["status"] = after
                return True
            before = after
        return False

    def rewarm():
        """A refused call: the daemon went stale, perhaps on the tool's own writes."""
        info["misses"] += 1
        return info["misses"] <= 3 and warm()

    try:
        if not warm():
            info["not_served"] = info.pop("refused", "the preloaded state kept changing")
            return None, info
        run = calls_on(setup, lambda: setup.run(fast, cwd, required), rewarm)
        if run is None:
            info["not_served"] = "the daemon kept going stale during the measured calls"
            return None, info
        info["status_after"] = status()
        run.outside = look_outside(marker, [parent + "*"] + PRUNE)
        return run, info
    finally:
        setup.run([sr, "stop", *target], cwd)


def measure(sr, runtime, label, argv, cwd_key, durations):
    row = {"runtime": runtime, "command": label}
    if shutil.which(argv[0], path=panel.PATH) is None:
        row["error"] = "not installed"
        return row
    parent = tempfile.mkdtemp(prefix="sr-se-")
    try:
        a = plain_run(parent, "a", argv, cwd_key)
        b = plain_run(parent, "b", argv, cwd_key)
        null = plain_run(parent, "n", argv, cwd_key)
        served, info = served_run(sr, parent, "s", argv, cwd_key)
        row.update(info)
        row["null_diffs"], _ = compare(a, b, null, durations)
        row["outside"] = {"a": a.outside[:40], "b": b.outside[:40], "null": null.outside[:40]}
        if served is None:
            return row
        row["outside"]["served"] = served.outside[:40]
        row["diffs"], row["noise"] = compare(a, b, served, durations)
        row["changing_paths"] = {"plain": sorted(a.paths()), "served": sorted(served.paths())}
        row["first_call"] = {"plain": first_lines(a), "served": first_lines(served)}
        for d in row["diffs"]:
            d["also_in_null"] = any(same_diff(d, n) for n in row["null_diffs"])
        return row
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def found(row):
    return [d for d in row.get("diffs", []) if not d["also_in_null"]]


# ---------------------------------------------------------------- the fixture

# Each tool in a module of its own beside the script: what a daemon preloads.
FIXTURE_PY = {
    "clean": "def main():\n    print('ok')\n",
    "append_load": "import os\nwith open(os.path.expanduser('~/sefx-load.log'), 'a') as f:\n    f.write('loaded\\n')\n"
                   "def main():\n    print('ok')\n",
    "rewrite_load": "import os\nwith open(os.path.expanduser('~/sefx-rewrite'), 'w') as f:\n    f.write('same\\n')\n"
                    "def main():\n    print('ok')\n",
    "pid_load": "import os\nPID = os.getpid()\ndef main():\n    print('pid', PID)\n",
    "clock_load": "import time\nNOW = time.time()\nSTAMP = time.strftime('%H:%M:%S')\n"
                  "def main():\n    print('clock', NOW, STAMP)\n",
    "append_run": "import os\ndef main():\n    with open(os.path.expanduser('~/sefx-run.log'), 'a') as f:\n"
                  "        f.write('ran\\n')\n    print('ok')\n",
    "random_run": "import os, tempfile, time\ndef main():\n    fd, _ = tempfile.mkstemp()\n    os.close(fd)\n"
                  "    print(os.urandom(8).hex(), os.getpid(), time.time())\n",
    "first_run": "import os\nFLAG = os.path.expanduser('~/sefx-first')\nif not os.path.exists(FLAG):\n"
                 "    open(FLAG, 'w').close()\ndef main():\n    print('ok')\n",
}
FIXTURE_RB = {
    "clean": "def main = puts('ok')\n",
    "append_load": "File.open(File.expand_path('~/sefx-load.log'), 'a') { |f| f.puts('loaded') }\n"
                   "def main = puts('ok')\n",
    "rewrite_load": "File.write(File.expand_path('~/sefx-rewrite'), \"same\\n\")\ndef main = puts('ok')\n",
    "pid_load": "PID = Process.pid\ndef main = puts(\"pid #{PID}\")\n",
    "clock_load": "NOW = Time.now\ndef main = puts(\"clock #{NOW.to_f} #{NOW.strftime('%H:%M:%S')}\")\n",
    "append_run": "def main\n  File.open(File.expand_path('~/sefx-run.log'), 'a') { |f| f.puts('ran') }\n"
                  "  puts('ok')\nend\n",
    "random_run": "require 'tempfile'\ndef main\n  Tempfile.create('x')\n"
                  "  puts(\"#{rand(2**64).to_s(16)} #{Process.pid} #{Time.now.to_f}\")\nend\n",
    "first_run": "FLAG = File.expand_path('~/sefx-first')\nFile.write(FLAG, '') unless File.exist?(FLAG)\n"
                 "def main = puts('ok')\n",
}
FIXTURE_EXPECT = {
    "clean": set(), "append_run": set(), "random_run": set(), "first_run": set(),
    "append_load": {"file-only-plain"}, "rewrite_load": {"file-only-plain"},
    "pid_load": {"frozen"}, "clock_load": {"frozen"},
}


def check_fixture(sr, out):
    """True when every fixture tool comes out as FIXTURE_EXPECT says."""
    bin_dir = tempfile.mkdtemp(prefix="sr-sefx-")
    python, ruby = shutil.which("python3"), shutil.which("ruby")
    saved = panel.PATH, dict(panel.EXTRA_ENV)
    panel.PATH = bin_dir + ":" + panel.PATH
    panel.EXTRA_ENV = {**panel.EXTRA_ENV, "RUBYLIB": bin_dir}
    rows, ok = [], True
    for runtime, sources, interpreter in (("python", FIXTURE_PY, python), ("ruby", FIXTURE_RB, ruby)):
        if interpreter is None:
            print(f"{runtime}: no interpreter, fixture skipped", flush=True)
            ok = False
            continue
        for kind, source in sources.items():
            tool = f"sefx-{runtime[:2]}-{kind.replace('_', '-')}"
            lib = f"sefx_{kind}"
            with open(os.path.join(bin_dir, lib + (".py" if runtime == "python" else ".rb")), "w") as f:
                f.write(source)
            with open(os.path.join(bin_dir, tool), "w") as f:
                f.write(f"#!{python}\nimport {lib}\n{lib}.main()\n" if runtime == "python"
                        else f"#!{ruby}\nrequire_relative '{lib}'\nmain\n")
            os.chmod(os.path.join(bin_dir, tool), 0o755)
            row = measure(sr, runtime, tool, [tool], None, False)
            kinds = {d["kind"] for d in row.get("diffs", [])}
            strong = {d["kind"] for d in found(row) if d["strength"] == "strong"}
            good = "not_served" not in row and kinds == FIXTURE_EXPECT[kind] and strong == kinds \
                and not row["null_diffs"]
            ok = ok and good
            row["expected"] = sorted(FIXTURE_EXPECT[kind])
            row["as_expected"] = good
            rows.append(row)
            print(f"{'ok  ' if good else 'FAIL'} {tool:28} expected {sorted(FIXTURE_EXPECT[kind])}, "
                  f"found {sorted(kinds)}" + (f", not served: {row['not_served']}" if "not_served" in row else "")
                  + (f", null {[d['kind'] for d in row['null_diffs']]}" if row["null_diffs"] else ""), flush=True)
    panel.PATH, panel.EXTRA_ENV = saved
    shutil.rmtree(bin_dir, ignore_errors=True)
    with open(os.path.join(out, "fixture.json"), "w") as f:
        json.dump({"calls": CALLS, "ok": ok, "results": rows}, f, indent=1)
    return ok


# ---------------------------------------------------------------- the report

def describe(d):
    what = d.get("path") or ":".join(str(d[k]) for k in ("stream", "line") if k in d)
    detail = {k: d[k] for k in ("plain", "served", "plain_sizes", "served_sizes", "plain_size", "served_size")
              if k in d}
    weak = " (weak)" if d["strength"] == "weak" else ""
    return f"`{d['kind']}`{weak} {what}: {json.dumps(detail, ensure_ascii=False)}"


def summary(rows):
    lines = [f"Plain against served, {CALLS} measured calls per setup, after {WARM} warm calls.", "",
             "| Runtime | Command | Served | Files | Frozen | Output, exit code | Null run | Left out as noise |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    details = []
    for r in rows:
        if "error" in r or "not_served" in r:
            lines.append(f"| {r['runtime']} | `{r['command']}` | {r.get('error') or 'no: ' + r['not_served']} "
                         "| | | | | |")
            continue
        real = found(r)
        count = lambda test: sum(test(d["kind"]) for d in real)  # noqa: E731
        lines.append(f"| {r['runtime']} | `{r['command']}` | yes"
                     + (f" ({r['misses']} misses)" if r["misses"] else "")
                     + f" | {count(lambda k: k.startswith(('file', 'state')))} | {count(lambda k: k == 'frozen')} "
                     f"| {count(lambda k: k in ('output', 'exit-code'))} | {len(r['null_diffs'])} "
                     f"| {len(r['noise'])} |")
        if real or r["null_diffs"]:
            details += ["", f"### `{r['command']}`", ""] + [f"- {describe(d)}" for d in real]
            details += [f"- null run: {describe(d)}" for d in r["null_diffs"]]
    return "\n".join(lines + details) + "\n"


# Beyond the panel: its commands are mostly --version, which reach little of a
# tool. This one uses what ansible makes at load, a temp directory.
EXTRA = [
    ("python", "ansible -m copy", ["ansible", "localhost", "-c", "local", "-m", "copy", "-a",
                                   "content=hello dest=copied.txt"], "work", False),
]
PRUNE = [os.path.join(os.path.realpath(panel.ROOT), d) for d in ("repo", "panel-*", "out", "results", "bench")] + \
    ["/tmp/sr-*", os.path.expanduser("~/.cargo")]


def main():
    sr, out = os.path.abspath(sys.argv[1]), sys.argv[2]
    os.makedirs(out, exist_ok=True)
    print("fixture", flush=True)
    if not check_fixture(sr, out):
        print("The fixture did not come out as expected: no real tool can be trusted.", flush=True)
        sys.exit(1)
    if "--fixture-only" in sys.argv:
        return
    only = set(filter(None, os.environ.get("PANEL_ONLY", "").split(",")))
    rows = []
    for runtime, label, argv, cwd_key, durations in panel.PANEL + EXTRA:
        if only and runtime not in only and argv[0] not in only:
            continue
        t0 = time.monotonic()
        row = measure(sr, runtime, label, argv, cwd_key, durations)
        row["seconds"] = round(time.monotonic() - t0)
        rows.append(row)
        print(f"{label:32} {row.get('error') or row.get('not_served') or len(found(row))} "
              f"null={len(row.get('null_diffs', []))} {row['seconds']}s", flush=True)
        with open(os.path.join(out, "side_effects.json"), "w") as f:
            json.dump({"calls": CALLS, "warm": WARM, "gap": GAP, "results": rows}, f, indent=1)
    text = summary(rows)
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
