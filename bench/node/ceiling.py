"""Node go/no-go (J0): what a preloading fork server could gain on well-known
Node CLIs, and whether forking Node is safe at all.

For each command: native wall time, then the same command served by
daemon.mjs (modules learned by learn.cjs, preloaded, one fork per call),
the two interleaved; outputs compared with the native run. The daemon is fed
by a pipe and writes to files: no client, so this is a ceiling.

  docker build -t speedrunner-node-j0 bench/node
  docker run --rm --network none -v $PWD/bench/node:/j0:ro -v $PWD/out:/out \\
    speedrunner-node-j0 python3 /j0/ceiling.py /out
Env: RUNS (default 20), J0_FLAGS (node flags for the daemon, space-separated;
     "--single-threaded --v8-pool-size=1 --no-lazy" is what forking needs and pays),
     J0_ONLY (labels), J0_STRESS (calls of the stress test, default 1000).
"""
import json
import os
import re
import select
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

J0 = os.path.dirname(os.path.abspath(__file__))
ADDON = "/opt/j0/fork.node"
FILES = {
    "js/sample.js": "const a = 1\nconsole.log(a)\n",
    "js/eslint.config.mjs": "export default [{ rules: { semi: 'error', 'no-unused-vars': 'error' } }];\n",
    "ts/tsconfig.json": '{"compilerOptions": {"strict": true, "noEmit": true}, "files": ["index.ts"]}\n',
    "ts/index.ts": "export function greet(name: string): string {\n  return `Hello, ${name}!`;\n}\n",
    "empty.js": "",
    "gc.js": ("let keep = [];\nfor (let i = 0; i < 2e6; i++) { keep.push({ i, s: 'x' + i });"
              " if (keep.length > 1e5) keep = []; }\nconsole.log('ok', keep.length);\n"),
}
# label, command, cwd (under the work directory)
TOOLS = [
    ("npm --version", ["npm", "--version"], ""),
    ("pnpm --version", ["pnpm", "--version"], ""),
    ("yarn --version", ["yarn", "--version"], ""),
    ("eslint --version", ["eslint", "--version"], "js"),
    ("eslint sample.js", ["eslint", "sample.js"], "js"),
    ("tsc --version", ["tsc", "--version"], "ts"),
    ("tsc -p .", ["tsc", "-p", "."], "ts"),
    ("prettier --check sample.js", ["prettier", "--check", "sample.js"], "js"),
    ("jest --version", ["jest", "--version"], ""),
    ("vite --version", ["vite", "--version"], ""),
    ("webpack --version", ["webpack", "--version"], ""),
    ("ng version", ["ng", "version"], ""),
    ("next --version", ["next", "--version"], ""),
    ("nest --version", ["nest", "--version"], ""),
    ("firebase --version", ["firebase", "--version"], ""),
    ("vercel --version", ["vercel", "--version"], ""),
    ("node gc.js (GC stress)", ["node", "gc.js"], ""),
    ("node empty.js (floor)", ["node", "empty.js"], ""),
]
TIMES = re.compile(rb"\d+(\.\d+)?\s?(ms|s)\b")


def main():
    out = sys.argv[1]
    runs = int(os.environ.get("RUNS", "20"))
    flags = os.environ.get("J0_FLAGS", "").split()
    only = set(filter(None, os.environ.get("J0_ONLY", "").split(",")))
    stress = int(os.environ.get("J0_STRESS", "1000"))
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="j0-")
    for name, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(work, name)), exist_ok=True)
        with open(os.path.join(work, name), "w") as f:
            f.write(text)
    env = {**os.environ, "HOME": work}

    def timed(argv, cwd):
        t0 = time.perf_counter()
        p = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        return (time.perf_counter() - t0) * 1000, p

    bare = statistics.median(timed(["node", "-e", "0"], work)[0] for _ in range(runs))
    print(f"node -e 0: {bare:.1f} ms; node {subprocess.check_output(['node', '--version']).decode().strip()}; "
          f"daemon flags: {flags or 'none'}", flush=True)

    results = []
    for label, argv, sub in TOOLS:
        if only and label not in only:
            continue
        cwd = os.path.join(work, sub)
        if argv[0] == "node":
            bin_, args = os.path.join(cwd, argv[1]), argv[2:]
        else:
            bin_, args = os.path.realpath(shutil.which(argv[0])), argv[1:]
        row = {"command": label, "bin": bin_}
        _, native = timed(argv, cwd)
        _, native = timed(argv, cwd)  # first-run caches and notices out of the way
        learned = os.path.join(work, "learned.json")
        subprocess.run(["node", "--require", f"{J0}/learn.cjs", bin_, *args], cwd=cwd,
                       env={**env, "J0_LEARN": learned}, capture_output=True, timeout=120)
        row["learned"] = len(json.load(open(learned)))
        d = subprocess.Popen(["node", *flags, f"{J0}/daemon.mjs", ADDON, bin_, learned], cwd=cwd, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready = d.stdout.readline().decode().split()
        if not ready or ready[0] != "ready":
            row["error"] = "daemon: " + d.stderr.read().decode(errors="replace")[-300:]
            results.append(row)
            print(f"{label:28} {row['error']}", flush=True)
            continue
        row.update(preloaded=int(ready[1]), preload_failed=int(ready[2]), daemon_threads=int(ready[3]))
        o, e = os.path.join(work, "o"), os.path.join(work, "e")

        def served():
            t0 = time.perf_counter()
            d.stdin.write((json.dumps({"args": args, "cwd": cwd, "out": o, "err": e}) + "\n").encode())
            d.stdin.flush()
            if not select.select([d.stdout], [], [], 60)[0]:
                raise TimeoutError("no answer in 60 s: the child hangs")
            line = d.stdout.readline().split()
            if not line:
                d.wait(timeout=10)
                raise RuntimeError(f"daemon died ({d.returncode}): " + d.stderr.read().decode(errors="replace")[-400:])
            if line[0] == b"refused":
                raise RuntimeError(f"refused: {line[1].decode()} threads after preloading")
            return (time.perf_counter() - t0) * 1000, int(line[0]), open(o, "rb").read(), open(e, "rb").read()

        norm = lambda b: TIMES.sub(b"<t>", b)
        try:
            _, rc, so, se = served()
            row["identical"] = (rc, norm(so), norm(se)) == (native.returncode, norm(native.stdout), norm(native.stderr))
            if not row["identical"]:
                row["diff"] = {"rc": [native.returncode, rc], "native": (native.stdout + native.stderr)[-300:].decode(errors="replace"),
                               "served": (so + se)[-300:].decode(errors="replace")}
            nt, ft = [], []
            for _ in range(runs):
                nt.append(timed(argv, cwd)[0])
                ft.append(served()[0])
            row.update(native_ms=round(statistics.median(nt), 1), served_ms=round(statistics.median(ft), 1),
                       native_min=round(min(nt), 1), served_min=round(min(ft), 1))
            row["ceiling"] = round(row["native_ms"] / row["served_ms"], 2)
        except (TimeoutError, RuntimeError) as ex:
            row["error"] = str(ex)
            d.kill()
        d.stdin.close()
        d.wait(timeout=10) if d.poll() is None else None
        results.append(row)
        print(f"{label:28} " + json.dumps({k: v for k, v in row.items() if k not in ("command", "bin")}), flush=True)

    # Safety: many calls in a row from one daemon, the outputs always identical.
    if stress and not only:
        for label in ("eslint sample.js", "node gc.js (GC stress)"):
            argv, sub = next((a, s) for l, a, s in TOOLS if l == label)
            cwd = os.path.join(work, sub)
            bin_ = os.path.join(cwd, argv[1]) if argv[0] == "node" else os.path.realpath(shutil.which(argv[0]))
            args = argv[2:] if argv[0] == "node" else argv[1:]
            _, native = timed(argv, cwd)
            learned = os.path.join(work, "learned.json")
            subprocess.run(["node", "--require", f"{J0}/learn.cjs", bin_, *args], cwd=cwd,
                           env={**env, "J0_LEARN": learned}, capture_output=True)
            d = subprocess.Popen(["node", *flags, f"{J0}/daemon.mjs", ADDON, bin_, learned], cwd=cwd, env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            d.stdout.readline()
            same = hangs = 0
            t0 = time.monotonic()
            for i in range(stress):
                d.stdin.write((json.dumps({"args": args, "cwd": cwd, "out": f"{work}/o", "err": f"{work}/e"}) + "\n").encode())
                d.stdin.flush()
                if not select.select([d.stdout], [], [], 60)[0]:
                    hangs += 1
                    break
                rc = int(d.stdout.readline().split()[0])
                same += (rc, open(f"{work}/o", "rb").read(), open(f"{work}/e", "rb").read()) == \
                        (native.returncode, native.stdout, native.stderr)
            d.kill()
            row = {"stress": label, "calls": stress, "identical": same, "hangs": hangs,
                   "seconds": round(time.monotonic() - t0, 1)}
            results.append(row)
            print(json.dumps(row), flush=True)

    with open(os.path.join(out, "j0.json"), "w") as f:
        json.dump({"runs": runs, "flags": flags, "node_bare_ms": round(bare, 1), "results": results}, f, indent=1)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
