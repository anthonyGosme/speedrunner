"""macOS go/no-go (M0): the warm-path ceiling of fork-based preloading for a
Python command-line tool, and whether its forked children survive.

For one command: the native run (a fresh interpreter), then a preloaded
parent that forks a child per call, as the daemon does:
  preload   the tool's script run without __main__ (its imports), then the
            modules one forked run imported lazily (learning), imported too
  per call  fork; the child runs the script as __main__ with the command's
            argv, flushes, and leaves by os._exit (no teardown)
There is no client, socket or fd passing: an upper bound on the gain.

Wall times: RUNS of each, interleaved; median, min, max. The child's stdout
and exit code are compared with the native run's.

macOS hazard: fork without exec is unsafe once CoreFoundation or the
Objective-C runtime has been used. With --touch-cf, the parent first calls
urllib's proxy lookup (_scproxy: SystemConfiguration, CoreFoundation), as
any HTTP tool does at import or first request, and each child does it again:
children that crash are counted.

Usage: python ceiling.py [--touch-cf] <runs> <tool> [args...]   (prints JSON)
"""
import gc
import json
import os
import runpy
import shutil
import statistics
import subprocess
import sys
import time


def child_run(script, argv, out_fd, touch_cf):
    """In a forked child: the tool as __main__; never returns."""
    code = 0
    try:
        os.dup2(out_fd, 1)
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        os.dup2(devnull, 2)
        if touch_cf:
            import urllib.request
            urllib.request.getproxies()
        sys.argv = [script] + argv
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        code = 1
    try:
        sys.stdout.flush()
    except BaseException:
        pass
    os._exit(code)


def fork_once(script, argv, touch_cf, capture=False):
    """One call: (wall ms, exit status, stdout or None)."""
    r, w = os.pipe() if capture else (None, os.open(os.devnull, os.O_WRONLY))
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        child_run(script, argv, w, touch_cf)
    os.close(w)
    out = b""
    if capture:
        while chunk := os.read(r, 65536):
            out += chunk
        os.close(r)
    _, status = os.waitpid(pid, 0)
    return (time.perf_counter() - t0) * 1000, status, out if capture else None


def learn(script, argv):
    """Modules a forked run imports beyond the preloaded state, in a pipe."""
    r, w = os.pipe()
    before = set(sys.modules)
    pid = os.fork()
    if pid == 0:
        try:
            child_out = os.open(os.devnull, os.O_WRONLY)
            os.dup2(child_out, 1)
            os.dup2(child_out, 2)
            sys.argv = [script] + argv
            try:
                runpy.run_path(script, run_name="__main__")
            except BaseException:
                pass
            os.write(w, json.dumps(sorted(set(sys.modules) - before)).encode())
        finally:
            os._exit(0)
    os.close(w)
    data = b""
    while chunk := os.read(r, 65536):
        data += chunk
    os.waitpid(pid, 0)
    return json.loads(data or b"[]")


def main():
    args = sys.argv[1:]
    touch_cf = args[0] == "--touch-cf"
    if touch_cf:
        args = args[1:]
    runs, tool, argv = int(args[0]), args[1], args[2:]
    script = shutil.which(tool)

    native_times, native = [], None
    def native_run(capture=False):
        t0 = time.perf_counter()
        p = subprocess.run([script] + argv, stdin=subprocess.DEVNULL, capture_output=capture,
                           stdout=None if capture else subprocess.DEVNULL,
                           stderr=None if capture else subprocess.DEVNULL)
        return (time.perf_counter() - t0) * 1000, p

    _, native = native_run(capture=True)

    t0 = time.perf_counter()
    sys.argv = [script] + argv
    runpy.run_path(script, run_name="speedrunner_preload")
    learned = learn(script, argv)
    failed = []
    for name in learned:
        try:
            __import__(name)
        except BaseException:
            failed.append(name)
    if touch_cf:
        import urllib.request
        urllib.request.getproxies()
    preload_ms = (time.perf_counter() - t0) * 1000
    gc.freeze()

    _, status, out = fork_once(script, argv, touch_cf, capture=True)
    same = os.waitstatus_to_exitcode(status) == native.returncode and out == native.stdout
    fork_times, crashes = [], 0
    for _ in range(runs):
        native_times.append(native_run()[0])
        ms, status, _ = fork_once(script, argv, touch_cf)
        fork_times.append(ms)
        crashes += os.WIFSIGNALED(status)
    n, f = statistics.median(native_times), statistics.median(fork_times)
    print(json.dumps({
        "command": " ".join([tool] + argv), "touch_cf": touch_cf, "runs": runs,
        "native_ms": round(n, 1), "native_min_max": [round(min(native_times), 1), round(max(native_times), 1)],
        "fork_ms": round(f, 1), "fork_min_max": [round(min(fork_times), 1), round(max(fork_times), 1)],
        "ceiling": round(n / f, 2), "same_output": same, "crashed_children": crashes,
        "preload_ms": round(preload_ms), "learned": len(learned), "learned_failed": len(failed),
        "threads_in_parent": __import__("threading").active_count(),
    }))


if __name__ == "__main__":
    main()
