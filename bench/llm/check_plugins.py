"""`llm plugins`: is its order stable natively, and is the content the same served?"""
import json
import os
import subprocess
import time

N = 15


def out(cmd):
    return subprocess.run(cmd, capture_output=True, env=os.environ).stdout


def order(raw):
    return tuple(p["name"] for p in json.loads(raw))


def normal(raw):
    return sorted((p["name"], p.get("version"), tuple(sorted(p.get("hooks", [])))) for p in json.loads(raw))


env = dict(os.environ, SPEEDRUNNER_REQUIRE_FAST="1")
for _ in range(40):  # start the daemon
    if subprocess.run(["srr", "llm", "plugins"], capture_output=True, env=env).returncode != 124:
        break
    time.sleep(0.5)

native = [out(["llm", "plugins"]) for _ in range(N)]
served = [out(["srr", "llm", "plugins"]) for _ in range(N)]
print(f"native: {len(set(native))} distinct raw outputs in {N} runs")
for o in sorted({order(r) for r in native}):
    print("   order:", ", ".join(o))
print(f"served: {len(set(served))} distinct raw outputs in {N} runs")
for o in sorted({order(r) for r in served}):
    print("   order:", ", ".join(o))
norm = {json.dumps(normal(r)) for r in native + served}
print(f"once sorted (name, version, hooks): {len(norm)} distinct content(s) across all {2 * N} runs")
for n in norm:
    print("  ", n)
