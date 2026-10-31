"""macOS fork safety (M0): a parent that has used CoreFoundation (urllib's
proxy lookup), SSL and platform, with big imports done, forks N children
that use them again; failed or crashed children are counted.

Usage: python forkstress.py <N>   (needs httpie and awscli importable)
"""
import os, sys, ssl, urllib.request, json, time
import httpie.core, awscli.clidriver  # big imports, as a preloaded daemon has
ctx = ssl.create_default_context()
urllib.request.getproxies()                      # _scproxy: SystemConfiguration, CoreFoundation
import platform; platform.mac_ver()
N = int(sys.argv[1])
bad, codes = 0, {}
t0 = time.time()
for i in range(N):
    pid = os.fork()
    if pid == 0:
        try:
            urllib.request.getproxies()
            ssl.create_default_context().load_default_certs()
            awscli.clidriver.create_clidriver()
            platform.mac_ver()
            os._exit(0)
        except BaseException:
            os._exit(3)
    _, st = os.waitpid(pid, 0)
    if st:
        bad += 1
        codes[str(st)] = codes.get(str(st), 0) + 1
print(json.dumps({"forks": N, "failed": bad, "statuses": codes, "seconds": round(time.time() - t0, 1)}))
