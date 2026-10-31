"""macOS (M0): which step starts native threads in the process (ps -M), as
the daemon must refuse to fork once one runs.

Usage: python threads.py   (needs httpie and awscli importable)
"""
import os, subprocess, time
def native_threads():
    out = subprocess.run(["ps", "-M", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return len(out.strip().splitlines()) - 1
steps = []
def step(name, fn):
    fn(); time.sleep(0.3); steps.append((name, native_threads()))
step("start", lambda: None)
step("import httpie.core", lambda: __import__("httpie.core"))
step("import awscli.clidriver", lambda: __import__("awscli.clidriver"))
import ssl, urllib.request, platform
step("ssl.create_default_context()", ssl.create_default_context)
step("urllib getproxies (_scproxy)", urllib.request.getproxies)
step("platform.mac_ver()", platform.mac_ver)
for s in steps: print(f"{s[0]:32} native threads: {s[1]}")
