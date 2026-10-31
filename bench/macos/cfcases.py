"""macOS (M0): which fork is unsafe once CoreFoundation was used. A fresh
parent per case (urllib's proxy lookup in the parent or not), 20 children
(the lookup in the child or not); prints the failures and the parent's
native threads.

Usage: python cfcases.py
"""
import os, sys, subprocess
def threads():
    out = subprocess.run(["ps", "-M", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return len(out.strip().splitlines()) - 1
def case(parent_cf, child_cf, n=20):
    pid = os.fork()
    if pid == 0:  # a fresh "daemon" per case
        import urllib.request
        if parent_cf:
            urllib.request.getproxies()
        t = threads()
        bad, msg = 0, b""
        for _ in range(n):
            r, w = os.pipe()
            c = os.fork()
            if c == 0:
                os.dup2(w, 2)
                if child_cf:
                    urllib.request.getproxies()
                os._exit(0)
            os.close(w)
            err = os.read(r, 4096); os.close(r)
            _, st = os.waitpid(c, 0)
            if st:
                bad += 1; msg = msg or err or str(st).encode()
        print(f"parent_cf={parent_cf!s:5} child_cf={child_cf!s:5} parent native threads={t} failed {bad}/{n} {msg[:160]!r}", flush=True)
        os._exit(0)
    os.waitpid(pid, 0)
case(True, True); case(False, True); case(True, False); case(False, False)
