"""hstest: one subcommand per behaviour the regression matrix compares."""
import atexit
import ctypes
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import uuid

import hstest  # noqa: F401  (import-time side effects)

_keep = []
# Read once at import, like botocore's NEW_RETRIES_ENABLED.
FROZEN = os.environ.get("HSTEST_FROZEN")


def cmd_echo_stdin(args):
    sys.stdout.buffer.write(sys.stdin.buffer.read())


def cmd_exit(args):
    kind = args[0]
    if kind == "noarg":
        sys.exit()
    if kind == "none":
        sys.exit(None)
    if kind.startswith("int:"):
        sys.exit(int(kind[4:]))
    if kind.startswith("str:"):
        sys.exit(kind[4:])
    if kind == "raise":
        raise ValueError("hstest failure")
    if kind == "kbint":
        raise KeyboardInterrupt
    return int(kind)


def cmd_env(args):
    libc = ctypes.CDLL(None)
    libc.getenv.restype = ctypes.c_char_p
    for name in args:
        c_value = libc.getenv(name.encode())
        sub = subprocess.run(
            ["sh", "-c", 'printf %%s "${%s-<unset>}"' % name], capture_output=True
        )
        print(json.dumps({
            "name": name,
            "python": os.environ.get(name),
            "c": c_value.decode() if c_value is not None else None,
            "subprocess": sub.stdout.decode(),
        }))


def _args_from_default(argv=sys.argv):
    # As httpie's main(args=sys.argv): the list is bound at import time.
    return argv[1:]


def cmd_argv_default(args):
    print(_args_from_default())


def cmd_hook(args):
    # A finder installed at run time, as pytest's assertion rewriting: the
    # module it loads must come from it, not from a preload.
    import importlib.abc
    import importlib.machinery
    import importlib.util

    class Hooked(importlib.machinery.SourceFileLoader):
        def exec_module(self, module):
            module.HOOKED = True
            super().exec_module(module)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != "hstest.hooked":
                return None
            origin = os.path.join(os.path.dirname(__file__), "hooked.py")
            return importlib.util.spec_from_loader(name, Hooked(name, origin), origin=origin)

    sys.meta_path.insert(0, Finder())
    from hstest import hooked
    print("hooked" if getattr(hooked, "HOOKED", False) else "preloaded")


def cmd_frozen(args):
    print(FROZEN)


def cmd_lazy(args):
    # Imported on first use only: what level 2 learns.
    from hstest import lazy_plain, lazy_thread, lazy_print, lazy_env, lazy_argv, lazy_uses_argv  # noqa: F401
    print(lazy_plain.VALUE, lazy_env.VALUE, lazy_argv.VALUE)


def cmd_proxies(args):
    # CoreFoundation used at run time, in the child: as any tool making a
    # request on macOS does (urllib's proxy lookup).
    import urllib.request
    print("proxies", isinstance(urllib.request.getproxies(), dict))


def cmd_lazy_cf(args):
    # What level 2 learns, and must not keep: modules that use CoreFoundation
    # as they load, with a thread (lazy_proxy) or without one (cf).
    from hstest import cf, lazy_plain, lazy_proxy  # noqa: F401
    cmd_proxies(args)


def cmd_cwd(args):
    print(os.getcwd())
    print(os.environ.get("PWD"))


def cmd_umask(args):
    print(oct(os.umask(0)))


def cmd_random(args):
    print(uuid.uuid4())
    print(random.random())


def cmd_tty(args):
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name)
        print(json.dumps({
            "stream": name,
            "isatty": stream.isatty(),
            "line_buffering": stream.line_buffering,
            "encoding": stream.encoding,
            "errors": stream.errors,
        }))
    print(tuple(shutil.get_terminal_size()))


def cmd_interleave(args):
    for i in range(3):
        print(f"out{i}")
        print(f"err{i}", file=sys.stderr)


def cmd_sleep(args):
    print("ready", flush=True)
    time.sleep(float(args[0]))
    print("done")


def cmd_hold(args):
    # Runs for a while and records each step, so a test can tell whether it
    # finished and whether it ran more than once.
    seconds, marker = float(args[0]), args[1]

    def mark(step):
        with open(marker, "a") as f:
            f.write(step + "\n")

    mark("start")
    print("ready", os.getpid(), flush=True)
    time.sleep(seconds)
    mark("done")
    print("done")


def cmd_prompt(args):
    import getpass
    name = input("Name: ")
    secret = getpass.getpass("Secret: ")
    print(f"name={name} secret_len={len(secret)}")


def cmd_secret_then_stdin(args):
    # As tools that read data on stdin and ask for a password on /dev/tty.
    import getpass
    secret = getpass.getpass("Secret: ")
    data = sys.stdin.read()
    print(f"secret={secret!r} stdin={data!r}")


def cmd_binary(args):
    sys.stdout.buffer.write(bytes(range(256)) * 4)


def cmd_pending_output(args):
    # Still buffered at exit: the reader is gone by the time teardown flushes.
    sys.stdout.write("x\n")
    time.sleep(0.3)


def cmd_unclosed(args):
    f = open(args[0], "w")
    f.write("x" * 100000)
    _keep.append(f)


def cmd_atexit(args):
    atexit.register(print, "bye")
    print("hi")


def cmd_argv(args):
    print(repr(sys.argv))
    print(repr(sys.path[0]))
    print(repr(__import__("__main__").__file__))


def cmd_signals(args):
    for name in ("SIGINT", "SIGPIPE", "SIGTERM", "SIGCHLD", "SIGHUP", "SIGQUIT", "SIGXFSZ"):
        handler = signal.getsignal(getattr(signal, name))
        print(name, getattr(handler, "__name__", handler))
    print(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])))


COMMANDS = {
    name[len("cmd_"):].replace("_", "-"): fn
    for name, fn in globals().items()
    if name.startswith("cmd_")
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("usage: hstest {%s} ..." % ",".join(sorted(COMMANDS)), file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])
