"""speedrunner daemon: keeps one CLI tool preloaded and serves each invocation
from a forked child.

Started by the client, with the tool's own interpreter and shebang flags:

    <python> [flags] speedrunner_daemon.py --tool NAME (--script PATH | --module NAME)
        --socket PATH [--lock-fd N] [--tty MASK[:ROWSxCOLS]]

--module: the tool is `python -m NAME`, as a launcher execs it (az).

--lock-fd: the client already holds the startup lock on fd N for us.
--tty: which of fds 0-2 were terminals for the client that started us (bit
       n for fd n), and its terminal size. Imports run with a pty on those.

Standard library only. Linux only for now (SO_PEERCRED, /proc).

Protocol, little-endian, over the Unix socket:

    client -> child   b"HS1" + kind(1) + u32 body length + body, with the
                      client's fds 0, 1, 2, cwd and, when it has one, its
                      controlling terminal attached (SCM_RIGHTS)
                      kind R = run, P = ping, S = stop
    child  -> client  N(reason): run natively | R(pid): ready, waiting for go
                      | P(text): ping reply
    client -> child   b"G": go. From here on the client never falls back.
    child  -> client  X(code): exit code, sent from the last atexit once
                      teardown has nothing visible left to do. The client
                      returns on it; the child tears down unseen.
    parent -> client  W(kind, value): wait status, for when no X came

Between the parent and each child, over a socketpair:

    child  -> parent  A, with the client fd attached: accepted, fork the next spare
                      L(json): modules imported and data files loaded, after X
    parent -> child   Q: retire, sent to a spare that has not accepted yet
"""
import ast
import atexit
import builtins
import ctypes
import fcntl
import gc
import hashlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
import select
import signal
import socket
import stat
import struct
import sys
import termios
import threading
import time
import types

MAGIC = b"HS1"
N_FDS = 5  # stdin, stdout, stderr, cwd, and the client's /dev/tty if any
TTY = "/dev/tty"
HANDSHAKE_TIMEOUT_S = 5.0
LOCK_WAIT_S = 10.0
IDLE_TIMEOUT_S = float(os.environ.get("SPEEDRUNNER_IDLE_TIMEOUT", 4 * 3600))
# Env vars read once at interpreter startup: a child cannot honour a client
# whose values differ from the daemon's, so such requests run natively.
STARTUP_ENV_PREFIX = "PYTHON"
STARTUP_ENV_KEYS = ("LC_ALL", "LC_CTYPE", "LANG")
# Comma-separated preload levels (brief §3): 1 script imports, 2 learned
# imports, 3 pre-parsed botocore data files.
PRELOAD_ENV = "SPEEDRUNNER_PRELOAD"
DEFAULT_LEVELS = "1,2,3"
PROBE_TIMEOUT_S = 30.0
PROBE_STEP_S = 0.25  # a probe hands back what is left after this long
# Level 3 budget, in bytes of source JSON; parsed, it takes several times that.
MEMO_BUDGET = int(float(os.environ.get("SPEEDRUNNER_MEMO_MB", 16)) * 2**20)

PARENT_PID = os.getpid()
LOG_FD = os.dup(2)


def log(msg):
    os.write(LOG_FD, f"speedrunner[{os.getpid()}] {msg}\n".encode(errors="replace"))


# --- wire format -----------------------------------------------------------

def send_msg(sock, kind, payload=b""):
    sock.sendall(kind + struct.pack("<I", len(payload)) + payload)


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("client closed the connection")
        buf += chunk
    return bytes(buf)


def recv_request(sock):
    """Return (kind, body, fds)."""
    header_len = len(MAGIC) + 1 + 4
    data, ancdata, _flags, _addr = sock.recvmsg(
        header_len, socket.CMSG_SPACE(N_FDS * 4), socket.MSG_CMSG_CLOEXEC)
    fds = []
    for level, ctype, cdata in ancdata:
        if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
            usable = len(cdata) - len(cdata) % 4
            fds += list(struct.unpack(f"<{usable // 4}i", cdata[:usable]))
    if not data:
        raise EOFError("client closed the connection")
    data += recv_exact(sock, header_len - len(data))
    if data[:3] != MAGIC:
        raise ValueError("bad magic")
    kind = data[3:4]
    (length,) = struct.unpack("<I", data[4:8])
    return kind, recv_exact(sock, length), fds


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def u32(self):
        (v,) = struct.unpack_from("<I", self.data, self.pos)
        self.pos += 4
        return v

    def u64(self):
        (v,) = struct.unpack_from("<Q", self.data, self.pos)
        self.pos += 8
        return v

    def blob(self):
        n = self.u32()
        v = self.data[self.pos:self.pos + n]
        if len(v) != n:
            raise ValueError("truncated request")
        self.pos += n
        return v

    def blobs(self):
        return [self.blob() for _ in range(self.u32())]


class Request:
    """Body of a run request, in the order the client writes it."""

    def __init__(self, body):
        r = Reader(body)
        self.argv = r.blobs()
        self.env = {}
        for entry in r.blobs():
            key, sep, value = entry.partition(b"=")
            if sep:
                self.env[os.fsdecode(key)] = os.fsdecode(value)
        self.umask = r.u32()
        self.ignored_signals = r.u64()
        self.groups = [r.u32() for _ in range(r.u32())]


# --- daemon state ----------------------------------------------------------

class State:
    def __init__(self, tool, script, sock_path, lock_fd=None, module=None):
        self.lock_fd = lock_fd
        self.origin = "hand"  # which client command started us, for the log
        self.bound_ino = None
        self.tool = tool
        self.script = script  # with --module, the file `python -m` runs, once found
        self.module = module
        self.cwd_first = False  # `python -m` puts the client's cwd first on sys.path
        self.top_names = frozenset()  # top-level modules a file in that cwd could shadow
        self.sock_path = sock_path
        self.disabled = None  # reason string when the fast path is off
        # As the client that started us had it: compared with each client's.
        self.start_env = initial_environ()
        # What CPython itself set at startup, as natively for the tool.
        self.interpreter_env = {k: v for k, v in os.environ.items() if self.start_env.get(k) != v}
        self.pre_import_env = {}
        self.import_env_changed = {}
        self.import_env_deleted = set()
        self.import_output = (b"", b"")
        self.fingerprint_paths = []
        self.fingerprint_set = set()
        self.watch = None  # FileWatch over the module files
        self.fingerprint = ()
        self.preload_s = 0.0
        self.levels = preload_levels(os.environ.get(PRELOAD_ENV, DEFAULT_LEVELS))
        self.import_env_read = set()  # keys module-level code read: frozen in the daemon
        self.tty = None  # TtyStandIn: the starting client's terminals, during imports
        self.import_tty_read = {}  # fd -> whether a terminal, as module-level code found it
        self.learned = []    # level 2: modules imported because children did
        self.learned_paths = []  # sys.path entries the tool added itself, to import them with
        self.pending_origins = {}  # module -> the file a child imported it from
        self.blacklist = {}  # module -> why it is never preloaded
        self.skip = {}  # level 1: module -> why a fresh daemon must not import it
        self.learning_left = 0  # modules apply_learned has still to vet
        self.pending = {}    # ordered set: learned from children, not yet vetted
        self.pending_files = {}  # level 3: data files children loaded
        self.memo = None     # level 3: BotocoreMemo, once botocore is imported
        self.gen = 0         # bumped whenever the preloaded state changes
        self.cache_path = None
        self.restart = None  # reason to exit before serving


def preload_levels(text):
    return {int(x) for x in text.split(",") if x.strip() in ("1", "2", "3")}


def initial_environ():
    """The env we were started with, as /proc/self/environ still has it.
    CPython may have added to os.environ since: in a C locale, LC_CTYPE=C.UTF-8
    (PEP 538). Natively it does the same for the tool."""
    try:
        with open("/proc/self/environ", "rb") as f:
            data = f.read()
    except OSError:
        return dict(os.environ)
    env = {}
    for entry in data.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep:
            env[os.fsdecode(key)] = os.fsdecode(value)
    return env


def startup_env(env):
    return {k: v for k, v in env.items()
            if k.startswith(STARTUP_ENV_PREFIX) or k in STARTUP_ENV_KEYS}


def script_imports(script):
    """Module names imported at the top level of the tool's script."""
    with open(script, "rb") as f:
        tree = ast.parse(f.read(), script)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def module_main(name):
    """The file `python -m name` runs. Found as runpy does natively, which
    imports its parent packages, and the package itself to run its __main__."""
    spec = importlib.util.find_spec(name)
    if spec is not None and spec.submodule_search_locations is not None:
        importlib.import_module(name)
        spec = importlib.util.find_spec(f"{name}.__main__")
    if spec is None or not spec.has_location or not spec.origin:
        raise ImportError(f"No runnable module named {name}")
    return spec.origin


def module_top_names():
    return frozenset(name for name, module in list(sys.modules.items())
                     if "." not in name and isinstance(getattr(module, "__file__", None), str))


IMPORT_SUFFIXES = tuple(importlib.machinery.all_suffixes())


def shadowed(state, cwd_fd):
    """A preloaded module that `python -m`, with the cwd first on sys.path,
    would import from a file in the cwd instead: its name, or None."""
    cwd = f"/proc/self/fd/{cwd_fd}"
    try:
        entries = os.listdir(cwd)
    except OSError:
        return None  # natively unreadable too: nothing found there
    for entry in entries:
        stem, dot, rest = entry.partition(".")
        if stem not in state.top_names:
            continue
        if dot:
            if "." + rest in IMPORT_SUFFIXES:
                return stem
        elif any(os.path.isfile(os.path.join(cwd, entry, "__init__" + s)) for s in IMPORT_SUFFIXES):
            return stem
    return None


PTY_QUIET_S = 0.01


def capture_fds_during(fn, tty=None):
    """Run fn() with fds 1 and 2 captured; return their bytes.

    Done at fd level so that stream objects created during imports (e.g.
    logging handlers bound to sys.stderr) stay the real ones. On the fds a
    TtyStandIn covers, a pty stands in, so that code checking for a
    terminal finds what the client that started us has.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(0), os.dup(1), os.dup(2)]
    memfds = []
    sinks = {}
    try:
        for fd in (1, 2):
            if tty is not None and tty.covers(fd):
                sinks[fd] = tty.master[fd]
                os.dup2(tty.slave[fd], fd)
            else:
                sinks[fd] = os.memfd_create("hs-out" if fd == 1 else "hs-err")
                memfds.append(sinks[fd])
                os.dup2(sinks[fd], fd)
        if tty is not None and tty.covers(0):
            os.dup2(tty.slave[0], 0)
        rebind_std_streams()
        try:
            fn()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            for fd in (0, 1, 2):
                os.dup2(saved[fd], fd)
            rebind_std_streams()
        captured = []
        for fd in (1, 2):
            sink = sinks[fd]
            if sink in memfds:
                os.lseek(sink, 0, os.SEEK_SET)
            chunks = []
            while True:
                # A pty hands what was written to its master a little later
                # (the kernel's flip buffer): read until it stays quiet, or
                # the rest would be blamed on the next import.
                if sink not in memfds and not select.select([sink], [], [], PTY_QUIET_S)[0]:
                    break
                try:
                    chunk = os.read(sink, 65536)
                except (BlockingIOError, OSError):  # a drained pty
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            captured.append(b"".join(chunks))
        return tuple(captured)
    finally:
        for fd in saved + memfds:
            os.close(fd)


class TtyStandIn:
    """One pty per fd of the mask, raw (output bytes unchanged), with the
    client's terminal size. The slave stays blocking, as a terminal is
    (ansible refuses to start otherwise): a module printing more at import
    than a pty holds (some 64 KiB) would hang the daemon."""

    def __init__(self, mask, rows, cols):
        self.mask = mask
        self.master, self.slave = {}, {}
        for fd in (0, 1, 2):
            if mask & (1 << fd):
                master, slave = os.openpty()
                attrs = termios.tcgetattr(slave)
                attrs[1] &= ~termios.OPOST  # oflag: no \n -> \r\n
                attrs[3] &= ~(termios.ECHO | termios.ICANON)
                termios.tcsetattr(slave, termios.TCSANOW, attrs)
                if rows and cols:
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                os.set_blocking(master, False)  # drained after each import
                self.master[fd], self.slave[fd] = master, slave

    def covers(self, fd):
        return fd in self.slave

    def close(self):
        for fd in list(self.master.values()) + list(self.slave.values()):
            os.close(fd)
        self.master, self.slave = {}, {}


class TtyReads:
    """Records whether fds 0-2 were found to be terminals while active, e.g.
    by module-level code (httpie's Environment, ansible's display), which
    keeps the answer for every later run."""

    current = None

    def __init__(self):
        self.seen = {}

    def __enter__(self):
        TtyReads.current = self
        return self

    def __exit__(self, *exc):
        TtyReads.current = None

    @staticmethod
    def note(fd, result):
        if TtyReads.current is not None and fd in (0, 1, 2):
            TtyReads.current.seen.setdefault(fd, result)
        return result


_os_isatty = os.isatty


def _isatty(fd):
    return TtyReads.note(fd, _os_isatty(fd))


class TrackedStream(io.TextIOWrapper):
    """sys.stdin, stdout and stderr: the streams CPython makes, whose
    isatty() TtyReads sees. A subclass, made before any import, so that
    every reference to them and isinstance() checks stay as natively."""

    def isatty(self):
        return TtyReads.note(self.sr_fd, super().isatty())


def rebind_std_streams():
    """After fds 0-2 changed what they point to: give the standard streams
    fresh buffers, made as CPython makes them, for what the fds are now. A
    TextIOWrapper learns at creation whether its fd is seekable, and
    reconfigure() relies on it (ansible's fails on a terminal otherwise).
    The stream objects themselves stay, as anything may hold them."""
    for name, fd in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
        stream = getattr(sys, name)
        if not isinstance(stream, TrackedStream):
            continue
        if fd:
            stream.flush()
        unbuffered = isinstance(stream.buffer, io.RawIOBase)  # python -u, or PYTHONUNBUFFERED
        buffer = io.open(fd, "rb" if fd == 0 else "wb", buffering=0 if unbuffered else -1, closefd=False)
        (buffer if unbuffered else buffer.raw).name = f"<{name}>"  # as CPython names them
        stream.__init__(buffer, encoding=stream.encoding, errors=stream.errors, newline="\n",
                        line_buffering=stream.line_buffering, write_through=stream.write_through)


def track_std_streams():
    os.isatty = _isatty
    for name, fd in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
        old = getattr(sys, name)
        if type(old) is not io.TextIOWrapper:
            continue
        mode, line, through = getattr(old, "mode", None), old.line_buffering, old.write_through
        encoding, errors = old.encoding, old.errors
        new = TrackedStream(old.detach(), encoding=encoding, errors=errors, newline="\n",
                            line_buffering=line, write_through=through)
        new.sr_fd = fd
        if mode is not None:
            new.mode = mode
        setattr(sys, name, new)
        if getattr(sys, f"__{name}__") is old:
            setattr(sys, f"__{name}__", new)


def native_thread_count():
    return len(os.listdir("/proc/self/task"))


def fingerprint_paths(script):
    paths = [p for p in (script, sys.executable) if p]
    for entry in sys.path:
        if entry and os.path.isdir(entry):
            paths.append(entry)
            try:
                names = os.listdir(entry)
            except OSError:
                continue
            for name in sorted(names):
                if name.endswith(".dist-info"):
                    paths.append(os.path.join(entry, name, "RECORD"))
    return paths


def module_files():
    """Source or extension file of every loaded module: an in-place edit or
    upgrade of any of them makes the preloaded state stale."""
    files = []
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if isinstance(path, str):
            files.append(path)
    return files


class FileWatch:
    """inotify on the module files the preloaded state was built from.

    Checking hundreds of stats per request would cost over a millisecond
    (601 files for aws: 1.4 ms). The kernel queues the event during the
    write itself, and nobody ever reads the queue: a pending event, seen
    by a single select(), means stale, for every child and the parent.
    """
    MASK = 0x2 | 0x4 | 0x400 | 0x800  # MODIFY, ATTRIB, DELETE_SELF, MOVE_SELF

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)

    def add(self, path):
        """False when the file cannot be watched (e.g. out of watches)."""
        return self.fd >= 0 and self.libc.inotify_add_watch(
            self.fd, os.fsencode(path), self.MASK) >= 0

    def fired(self):
        return self.fd >= 0 and bool(select.select([self.fd], [], [], 0)[0])


def extend_fingerprint(state, paths, watch=False):
    """Cover files not covered yet, as they are now (just imported): by
    inotify when watch is set and that works, by stat otherwise."""
    new = [p for p in dict.fromkeys(paths) if p not in state.fingerprint_set]
    state.fingerprint_set.update(new)
    if watch:
        if state.watch is None:
            state.watch = FileWatch()
        new = [p for p in new if not state.watch.add(p)]
    state.fingerprint_paths += new
    state.fingerprint += compute_fingerprint(new)


def is_stale(state):
    return ((state.watch is not None and state.watch.fired())
            or compute_fingerprint(state.fingerprint_paths) != state.fingerprint)


def compute_fingerprint(paths):
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size, st.st_ino, st.st_dev))
        except OSError:
            out.append(None)
    return tuple(out)


class EnvReads:
    """Records the os.environ keys read while active, e.g. by module-level code."""

    def __init__(self):
        self.keys = set()

    def __enter__(self):
        original = self.original = os._Environ.__getitem__
        keys = self.keys

        def getitem(env, key):
            # A key of the wrong type raises TypeError natively: not a read.
            if env is os.environ and isinstance(key, str):
                keys.add(key)
            elif env is getattr(os, "environb", None) and isinstance(key, bytes):
                keys.add(os.fsdecode(key))
            return original(env, key)

        os._Environ.__getitem__ = getitem
        return self

    def __exit__(self, *exc):
        os._Environ.__getitem__ = self.original


class ArgvProbe(list):
    """sys.argv in the daemon: notes reads of the arguments while active.

    A module that reads them at import would keep the daemon's (none), so it
    is never preloaded. argv[0] is the script, as natively, and is not a read.
    The child updates the list in place, so a reference bound at import (a
    default `args=sys.argv`) sees the client's arguments.
    """
    active = False
    read = False

    def _note(self):
        if self.active:
            self.read = True

    def __getitem__(self, i):
        if self.active:
            # Judged against any client's arguments, not the daemon's none.
            if isinstance(i, slice):
                if any(j != 0 for j in range(*i.indices(1 << 30))):
                    self.read = True
            elif i != 0:
                self.read = True
        return super().__getitem__(i)


def _argv_reader(name):
    method = getattr(list, name)

    def read(self, *args, **kwargs):
        self._note()
        return method(self, *args, **kwargs)
    read.__name__ = name
    return read


for _name in ("__iter__", "__len__", "__contains__", "__reversed__", "__eq__", "__ne__",
              "__add__", "__mul__", "__rmul__", "__repr__", "copy", "index", "count", "pop"):
    setattr(ArgvProbe, _name, _argv_reader(_name))


def reads_argv(fn):
    """Run fn(); return its result and whether it read the arguments."""
    probe = sys.argv if isinstance(sys.argv, ArgvProbe) else None
    if probe is None:
        return fn(), False
    probe.active, probe.read = True, False
    try:
        result = fn()
    finally:
        probe.active = False
    return result, probe.read


def threads_running():
    return threading.active_count() != 1 or native_thread_count() != 1


def preload(state):
    """Level 1: import what the script imports at top level. Level 2: what
    earlier runs imported lazily, from the cache."""
    first = not (getattr(sys.flags, "safe_path", False) or sys.flags.isolated)
    if state.module:
        if first:
            # Natively the client's cwd, which differs per run: put in each child.
            del sys.path[0]
            state.cwd_first = True
        sys.argv = ArgvProbe(["-m"])  # natively, while the module is found
    else:
        if first:
            # Natively sys.path[0] is the script's directory, not ours.
            sys.path[0] = os.path.dirname(os.path.realpath(state.script))
        sys.argv = ArgvProbe([state.script])

    skip = state.skip = load_skip(state)
    state.pre_import_env = dict(os.environ)

    def do_imports():
        if state.module:
            # Natively done before the module runs: whatever it reads, it
            # reads with any command line, so it cannot be skipped.
            if reads_argv(lambda: setattr(state, "script", module_main(state.module)))[1]:
                raise ImportError(f"{state.module}'s packages read the command line at import")
        names = script_imports(state.script) if 1 in state.levels else []
        for name in [n for n in names if n not in skip]:
            why = ("reads the command line at import" if reads_argv(lambda: importlib.import_module(name))[1]
                   else "starts a thread at import" if threads_running() else None)
            if why:
                # Already run with the daemon's command line, or a thread
                # that forbids forking, which cannot be undone: skip it from
                # now on, in a fresh daemon. Level 2 still learns what it
                # imports, short of what starts the thread.
                skip[name] = why
                save_skip(state, skip)
                state.restart = f"{name} {why}"
                return

    t0 = time.perf_counter()
    try:
        with EnvReads() as reads, TtyReads() as ttys:
            state.import_output = capture_fds_during(do_imports, state.tty)
        state.import_env_read = reads.keys
        state.import_tty_read = ttys.seen
    except BaseException as exc:  # keep serving, so clients stop respawning us
        state.disabled = f"preload failed: {exc!r}"
        log(state.disabled)
    after = dict(os.environ)
    state.import_env_changed = {k: v for k, v in after.items()
                                if state.pre_import_env.get(k) != v}
    state.import_env_deleted = set(state.pre_import_env) - set(after)

    if threads_running():
        state.disabled = "threads running after preload"
    if state.restart:
        return
    extend_fingerprint(state, fingerprint_paths(state.script))
    extend_fingerprint(state, module_files(), watch=True)
    state.cache_path = cache_file(state)

    if 2 in state.levels and not state.disabled:
        cached = load_cache(state)
        state.blacklist = {k: v for k, v in (cached.get("blacklist") or {}).items()
                           if isinstance(k, str)}
        learned = [n for n in cached.get("modules") or []
                   if isinstance(n, str) and n not in state.blacklist]
        state.learned_paths = [p for p in cached.get("paths") or []
                               if isinstance(p, str) and os.path.isabs(p)]
        extend_fingerprint(state, [p for p in state.learned_paths if os.path.isdir(p)])
        # Vetted when they were learned: imported directly, but checked again.
        why = import_in_daemon(state, learned)
        if why or len(state.learned) != len(learned):
            save_cache(state)
        state.restart = why
        extend_fingerprint(state, module_files(), watch=True)
    install_memo(state)
    if state.memo is not None:
        state.memo.refresh([p for p in load_cache(state).get("files") or [] if isinstance(p, str)])
    state.top_names = module_top_names()
    state.preload_s = time.perf_counter() - t0


class ExtraPath:
    """sys.path with the entries the tool adds itself in front, for the
    duration of an import: our own sys.path is left as it was."""

    def __init__(self, paths):
        self.paths = paths

    def __enter__(self):
        self.saved = sys.path[:]
        sys.path[:0] = [p for p in self.paths if p not in sys.path]

    def __exit__(self, *exc):
        sys.path[:] = self.saved


def import_in_daemon(state, names):
    """Import vetted modules in the daemon, in order. A module that fails is
    blacklisted. Returns why the daemon must restart, or None."""
    for name in names:
        if name not in sys.modules:
            before = dict(os.environ)
            try:
                with EnvReads() as reads, TtyReads() as ttys, ExtraPath(state.learned_paths):
                    output, argv_read = reads_argv(
                        lambda: capture_fds_during(lambda: importlib.import_module(name), state.tty))
            except Exception as exc:
                state.blacklist[name] = f"import failed in the daemon: {exc!r}"
                continue
            why = ("printed in the daemon" if output != (b"", b"")
                   else "read the command line in the daemon" if argv_read
                   else "changed the env in the daemon" if dict(os.environ) != before
                   else "started a thread in the daemon" if threads_running()
                   else None)
            if why:  # cannot be undone
                state.blacklist[name] = why
                return f"{name} {why}"
            state.import_env_read |= reads.keys
            for fd, was in ttys.seen.items():
                state.import_tty_read.setdefault(fd, was)
        if name not in state.learned:
            state.learned.append(name)
    return None


class Refused(BaseException):
    """Raised by RefuseImports: a BaseException, so that an optional import
    (`except ImportError`) cannot hide it."""


class RefuseImports:
    """In a probe: importing a module already refused fails at once. The
    module that imports it is refused too, without running the refused one
    again, which would cost as much each time (oci: 470 modules import one
    that reads the command line)."""

    def __init__(self, names):
        self.names = names

    def find_spec(self, name, path=None, target=None):
        if name in self.names:
            raise Refused(f"imports {name}, refused")
        return None


def probe_imports(names, tty=None, paths=(), origins=None, refused=()):
    """Import names one by one in a throwaway child, with paths in front of
    sys.path; sort them into ok, bad (with the reason) and retry. A module
    found elsewhere than in the child that imported it (origins), or that
    imports one of refused, is bad."""
    origins = origins or {}
    r, w = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        try:
            signal.set_wakeup_fd(-1)
            os.close(r)
            sys.path[:0] = [p for p in paths if p not in sys.path]
            sys.meta_path.insert(0, RefuseImports(set(refused)))
            verdict = {"ok": [], "bad": {}, "retry": []}
            step_end = time.monotonic() + PROBE_STEP_S
            for i, name in enumerate(names):
                if verdict["ok"] and time.monotonic() > step_end:
                    verdict["retry"] = names[i:]  # the parent replaces the spare in between
                    break
                if name in sys.modules:
                    verdict["ok"].append(name)  # came with an earlier, clean import
                    continue
                env_before = dict(os.environ)
                try:
                    output, argv_read = reads_argv(
                        lambda: capture_fds_during(lambda: importlib.import_module(name), tty))
                    why = ("prints at import" if output != (b"", b"")  # natively mid-run
                           else "reads the command line at import" if argv_read
                           else "changes the env at import" if dict(os.environ) != env_before
                           else "starts a thread at import" if threads_running()
                           else "found in another file than at run time"
                           if origins.get(name, file_of(name)) != file_of(name)
                           else None)
                except Refused as exc:
                    why = str(exc)
                except BaseException as exc:
                    why = f"import failed: {type(exc).__name__}: {exc}"
                if why:
                    # Whatever it left behind would taint later verdicts: judge
                    # the rest in a fresh probe.
                    verdict["bad"][name] = why
                    verdict["retry"] = names[i + 1:]
                    break
                verdict["ok"].append(name)
            write_all(w, json.dumps(verdict).encode())
        finally:
            os._exit(0)
    os.close(w)
    chunks = []
    deadline = time.monotonic() + PROBE_TIMEOUT_S
    while True:
        if not select.select([r], [], [], max(0.0, deadline - time.monotonic()))[0]:
            os.kill(pid, signal.SIGKILL)
            chunks = []
            break
        chunk = os.read(r, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(r)
    os.waitpid(pid, 0)
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        return {"ok": [], "bad": dict.fromkeys(names, "probe crashed or hung"), "retry": []}


def file_of(name):
    module = sys.modules.get(name)
    path = getattr(module, "__file__", None)
    return path if isinstance(path, str) else None


def stat_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_dev)


class BotocoreMemo:
    """Level 3: botocore data files parsed in the daemon, by resolved path.

    Wraps JSONFileLoader._load_file, so AWS_DATA_PATH and the search order
    stay botocore's. A child takes each entry at most once and only while
    the file's stat matches; otherwise it reads the file as natively. Every
    object it gets is still its own: the parent's copy is never handed out.
    """

    def __init__(self, loaders):
        self.loaders = loaders
        self.original = loaders.JSONFileLoader._load_file
        self.entries = {}  # path -> (stat key, data), least recently used first
        self.size = 0
        self.loaded = []   # in a child: paths botocore loaded during the run
        memo = self

        def _load_file(loader, full_path, open_method):
            return memo.load(loader, full_path, open_method)

        loaders.JSONFileLoader._load_file = _load_file

    def load(self, loader, full_path, open_method):
        entry = self.entries.pop(full_path, None)
        if entry is not None and stat_key(full_path) == entry[0]:
            self.loaders.logger.debug("Loading JSON file: %s", full_path)  # as natively
            data = entry[1]
        else:
            data = self.original(loader, full_path, open_method)
        if data is not None:
            self.loaded.append(full_path)
        return data

    def open_method(self, path):
        for ext, method in sorted(self.loaders._JSON_OPEN_METHODS.items(),
                                  key=lambda kv: -len(kv[0])):
            if path.endswith(ext):
                return method
        return None

    def refresh(self, paths):
        """In the daemon: parse what children loaded, keep the most recent within budget."""
        for path in paths:
            key, entry = stat_key(path), self.entries.pop(path, None)
            if entry is not None and entry[0] == key:
                self.entries[path] = entry  # most recently used
                continue
            if entry is not None:
                self.size -= entry[0][1]
            method = self.open_method(path)
            if key is None or method is None or key[1] > MEMO_BUDGET:
                continue
            try:
                data = self.original(self.loaders.JSONFileLoader(), path, method)
            except Exception as exc:
                log(f"level 3: cannot parse {path}: {exc!r}")
                continue
            if data is not None and stat_key(path) == key:
                self.entries[path] = (key, data)
                self.size += key[1]
        while self.size > MEMO_BUDGET:
            path = next(iter(self.entries))
            self.size -= self.entries.pop(path)[0][1]


def install_memo(state):
    if 3 in state.levels and state.memo is None and "botocore.loaders" in sys.modules:
        # awscli aliases its vendored botocore as top-level `botocore`, which
        # the CLI uses; awscli.botocore.* are separate module objects.
        state.memo = BotocoreMemo(sys.modules["botocore.loaders"])


def cache_file(state):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    key = repr((state.script, sys.executable, sys.version, state.fingerprint))
    return os.path.join(base, "speedrunner",
                        f"{state.tool}-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json")


def load_cache(state):
    try:
        with open(state.cache_path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(state):
    data = {"modules": state.learned, "paths": state.learned_paths, "blacklist": state.blacklist,
            "files": list(state.memo.entries) if state.memo is not None else []}
    tmp = f"{state.cache_path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(state.cache_path), mode=0o700, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, state.cache_path)
    except OSError as exc:
        log(f"cannot write {state.cache_path}: {exc!r}")


def skip_file(state):
    """Level-1 imports never to preload. Keyed by the script alone: they are
    known before the import that computes the fingerprint."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    script = state.script or state.module  # a module's file is found by importing
    key = repr((script, stat_key(script), sys.executable, sys.version))
    return os.path.join(base, "speedrunner",
                        f"{state.tool}.skip-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json")


def load_skip(state):
    try:
        with open(skip_file(state)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str)} if isinstance(data, dict) else {}


def save_skip(state, skip):
    path = skip_file(state)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(skip, f, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        log(f"cannot write {path}: {exc!r}")


# --- child: one invocation -------------------------------------------------

def names_script(state, argv0, cwd_fd):
    """argv[0] names the preloaded script: its path, or, as `./manage.py`
    gives it, a path relative to the client's cwd."""
    if argv0 == state.script:
        return True
    if os.path.isabs(argv0):
        return False
    try:
        return os.path.samefile(os.path.join(f"/proc/self/fd/{cwd_fd}", argv0), state.script)
    except OSError:
        return False


def names_entry(state, argv, cwd_fd):
    """The request runs what we preloaded: [script, args...], or for a
    module [-m, name, args...]."""
    if state.module:
        return len(argv) >= 2 and argv[0] == b"-m" and os.fsdecode(argv[1]) == state.module
    return bool(argv) and names_script(state, os.fsdecode(argv[0]), cwd_fd)


def why_not_fast(state, req, peer_gid, fds):
    if state.disabled:
        return state.disabled
    if not names_entry(state, req.argv, fds[3]):
        return "script path mismatch"
    if is_stale(state):
        return "stale: installed files changed"
    if peer_gid != os.getgid() or sorted(set(req.groups)) != sorted(set(os.getgroups())):
        return "credentials differ from the daemon's"
    client, daemon = startup_env(req.env), startup_env(state.start_env)
    for key in sorted(set(client) | set(daemon)):
        if client.get(key) != daemon.get(key):
            return f"startup env differs: {key}"
    frozen = set(state.import_env_changed) | state.import_env_deleted | state.import_env_read
    env = {**req.env, **state.interpreter_env}  # the tool's at import time, natively
    for key in sorted(frozen):
        if without_links(state, key, env.get(key)) != without_links(state, key, state.pre_import_env.get(key)):
            return f"import-time env differs: {key}"
    for fd, was in sorted(state.import_tty_read.items()):
        if _os_isatty(fds[fd]) != was:
            return f"import-time terminal state differs: fd {fd}"
    if state.cwd_first:
        # The child puts os.getcwd() first on sys.path, after go.
        if os.readlink(f"/proc/self/fd/{fds[3]}").endswith(" (deleted)"):
            return "the cwd is deleted"
        name = shadowed(state, fds[3])
        if name:
            return f"the cwd has its own {name}"
    return None


def bridge_tty(fd):
    """Opens of /dev/tty reach the client's terminal. The child has no
    controlling terminal of its own: getpass would otherwise fall back to
    stdin, and read a password from a pipe. fd is the client's open
    /dev/tty: reopening it by path would look up our own terminal, so each
    open gets a duplicate instead."""
    os_open, builtin_open = os.open, builtins.open

    def is_tty(path):
        return isinstance(path, (str, bytes, os.PathLike)) and os.fsdecode(path) == TTY

    def open_(path, flags, mode=0o777, *, dir_fd=None):
        if is_tty(path):
            return os.dup(fd)
        return os_open(path, flags, mode, dir_fd=dir_fd)

    def open_file(file, *args, **kwargs):
        if is_tty(file):
            file = os.dup(fd)
        return builtin_open(file, *args, **kwargs)

    os.open = open_
    builtins.open = io.open = open_file


def apply_request(state, req, fds):
    stdin, stdout, stderr, cwd = fds[:4]
    if state.tty is not None:
        state.tty.close()
    sys.stdout.flush()
    sys.stderr.flush()
    for target, fd in ((0, stdin), (1, stdout), (2, stderr)):
        os.dup2(fd, target)
    rebind_std_streams()
    os.fchdir(cwd)
    for fd in fds[:4]:
        os.close(fd)
    if len(fds) > 4:
        bridge_tty(fds[4])  # kept open, close-on-exec, for the whole run
    os.umask(req.umask)

    # In place: botocore and others keep references to os.environ, and
    # clear()/update() also call unsetenv()/putenv() for C code and children.
    os.environ.clear()
    os.environ.update(req.env)
    # Same startup env as ours (why_not_fast): CPython would set the same.
    os.environ.update(state.interpreter_env)
    for key in state.import_env_deleted:
        os.environ.pop(key, None)
    os.environ.update(state.import_env_changed)

    # Dispositions a native interpreter would start with: inherited ones,
    # plus CPython's own (SIGINT -> KeyboardInterrupt, SIGPIPE/SIGXFSZ ignored).
    for sig in (signal.SIGCHLD, signal.SIGTERM, signal.SIGUSR1):
        signal.signal(sig, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    for sig in range(1, 64):
        if req.ignored_signals & (1 << sig) and sig not in (signal.SIGKILL, signal.SIGSTOP):
            try:
                signal.signal(sig, signal.SIG_IGN)
            except (OSError, ValueError):
                pass  # reserved by libc
    signal.pthread_sigmask(signal.SIG_SETMASK, [])

    # The streams were created for the daemon's fds: CPython line-buffers
    # stdin/stdout only on a tty (stderr always), unless unbuffered.
    buffered = not sys.stdout.write_through
    for stream in (sys.stdin, sys.stdout):
        if stream is not None:
            stream.reconfigure(line_buffering=buffered and stream.isatty())

    # sys.argv[0] as natively: the script as the client ran it, or for a
    # module "-m" while it is found (runpy then sets it to its file).
    args = [os.fsdecode(a) for a in req.argv]
    entry = ["-m", args[1]] if state.module else args[:1]
    if state.module:
        args = args[:1] + args[2:]
        if state.cwd_first:
            sys.path.insert(0, os.getcwd())
    CHILD["argv0"] = args[0]
    CHILD["base_path"] = list(sys.path)
    if isinstance(sys.argv, list):
        sys.argv[:] = args  # in place: see ArgvProbe
    else:
        sys.argv = args
    if hasattr(sys, "orig_argv"):
        sys.orig_argv = ORIG_ARGV0 + INTERP_FLAGS + entry + args[1:]


def interpreter_options():
    """How we were started, up to our own script: the interpreter as named,
    and its options, which are the tool's."""
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable])
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-X", "-W") else 1
    return argv[:1], argv[1:i]


ORIG_ARGV0, INTERP_FLAGS = interpreter_options()
EXIT = {"code": None, "signal": None}
CHILD = {"conn": None, "ctl": None, "base_modules": None, "base_finders": (), "import_order": [],
         "memo": None, "base_path": None}
# Loaders a fresh interpreter has: a module another one loaded (a finder the
# run installed, as pytest's assertion rewriting) must not be preloaded.
STANDARD_LOADER_MODULES = {"importlib._bootstrap", "importlib._bootstrap_external",
                           "_frozen_importlib", "_frozen_importlib_external", "zipimport"}
# Buffered writers that interpreter teardown would flush when it frees them.
FILE_TYPES = {io.TextIOWrapper, io.BufferedWriter, io.BufferedRandom}


def flush_open_files():
    std = {id(s) for s in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)}
    # Objects frozen in the daemon are skipped: they predate the run.
    for obj in gc.get_objects():
        if type(obj) in FILE_TYPES and id(obj) not in std:
            try:
                if not obj.closed:
                    obj.flush()
            except Exception:
                pass  # teardown will retry and report it


def report_exit():
    """atexit callback registered before anything else, so it runs last."""
    conn = CHILD["conn"]
    if conn is None:
        return  # the daemon itself
    flush_open_files()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        # Natively teardown reports it and exits with 120: let the wait status tell.
        return
    if EXIT["signal"] is not None:
        # CPython re-raises an unhandled KeyboardInterrupt as SIGINT.
        signal.signal(EXIT["signal"], signal.SIG_DFL)
        os.kill(os.getpid(), EXIT["signal"])
    if EXIT["code"] is None:
        return
    try:
        send_msg(conn, b"X", struct.pack("<i", EXIT["code"]))
    except OSError:
        return
    # The client is gone from here on; pipes it shares must see EOF now.
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    send_learned()


def standard_loader(module):
    loader = getattr(getattr(module, "__spec__", None), "loader", None)
    if loader is None:
        return True
    kind = loader if isinstance(loader, type) else type(loader)  # BuiltinImporter is a class
    if kind.__module__ in STANDARD_LOADER_MODULES:
        return True
    return any(loader is f or kind is type(f) for f in CHILD["base_finders"])


def send_learned():
    """Tell the parent what this run imported or loaded beyond the preloaded state."""
    base, seen, modules = CHILD["base_modules"] or (), set(), []
    # In the order imports started: sys.modules has them in the order they
    # finished, and replaying that breaks circular imports.
    for name in CHILD["import_order"]:
        if name not in base and name not in seen and name in sys.modules:
            seen.add(name)
            if standard_loader(sys.modules[name]):
                modules.append(name)
    files = CHILD["memo"].loaded if CHILD["memo"] is not None else []
    # Entries the tool put on sys.path itself (gcloud): its modules are found there.
    base = CHILD["base_path"] or []
    paths = [p for p in sys.path if isinstance(p, str) and os.path.isabs(p) and p not in base]
    origins = {name: file_of(name) for name in modules if file_of(name)}
    if modules or files:
        data = {"modules": modules, "files": files, "paths": paths, "origins": origins}
        try:
            send_msg(CHILD["ctl"], b"L", json.dumps(data).encode())
        except OSError:
            pass


def script_traceback(tb, state):
    """Drop our frames so the traceback starts in the script, or for a
    module in runpy's _run_module_as_main, as natively."""
    def first(code):
        if state.module:
            return code is runpy._run_module_as_main.__code__
        return code.co_filename == state.script
    t = tb
    while t is not None and not first(t.tb_frame.f_code):
        t = t.tb_next
    return t if t is not None else tb


def system_exit_code(exc):
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    # CPython prints any other value to stderr and exits with status 1.
    print(code, file=sys.stderr)
    return 1


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def keep_argv0(argv0, script):
    """run_path sets sys.argv[0] to the script's path; natively it is the
    path as typed, `./manage.py`. Only for our call: once used, runpy is
    restored for the tool's own run_path calls."""
    orig = getattr(runpy, "_ModifiedArgv0", None)
    if argv0 == script or orig is None:
        return

    class KeepArgv0(orig):
        def __init__(self, value):
            runpy._ModifiedArgv0 = orig
            super().__init__(argv0)

    runpy._ModifiedArgv0 = KeepArgv0


def run_tool(state, conn):
    # Natively printed by the imports on every run.
    write_all(1, state.import_output[0])
    write_all(2, state.import_output[1])
    CHILD["conn"] = conn
    try:
        if state.module:
            # As `python -m`: run in a __main__ module of its own, through the
            # same runpy function, so that a traceback shows the same frames.
            main = types.ModuleType("__main__")
            main.__builtins__ = builtins
            CHILD["daemon_main"] = sys.modules["__main__"]  # our functions' globals
            sys.modules["__main__"] = main
            runpy._run_module_as_main(state.module)
        else:
            keep_argv0(CHILD.get("argv0", state.script), state.script)
            runpy.run_path(state.script, run_name="__main__")
        code = 0
    except SystemExit as exc:
        code = system_exit_code(exc)
    except BaseException as exc:
        # The display uses exc.__traceback__, not the tb argument (3.11+).
        tb = script_traceback(exc.__traceback__, state)
        sys.excepthook(type(exc), exc.with_traceback(tb), tb)
        if isinstance(exc, KeyboardInterrupt):
            EXIT["signal"] = signal.SIGINT
        code = 1
    EXIT["code"] = code
    return code


def run_spare(state, lsock, ctl, inherited):
    """Runs in a forked spare: wait for one client, then serve it.

    Returns the tool's exit code, or never returns.
    """
    signal.set_wakeup_fd(-1)  # before closing the pipe it points to
    for sig in (signal.SIGCHLD, signal.SIGTERM, signal.SIGUSR1):
        signal.signal(sig, signal.SIG_DFL)
    for fd in inherited:
        os.close(fd)  # other clients must see EOF when their own child exits
    os.setpgid(0, 0)
    while True:
        readable, _, _ = select.select([lsock, ctl], [], [])
        if ctl in readable:
            os._exit(0)  # retired, or the daemon is gone
        try:
            conn, _ = lsock.accept()
        except BlockingIOError:
            continue  # another spare took it
        break
    lsock.close()
    conn.setblocking(True)
    CHILD["ctl"] = ctl
    CHILD["memo"] = state.memo
    if 2 in state.levels:
        CHILD["base_modules"] = set(sys.modules)
        CHILD["base_finders"] = tuple(sys.meta_path)
        order = CHILD["import_order"]
        sys.addaudithook(lambda event, args: order.append(args[0]) if event == "import" else None)
    # The parent keeps a copy, to send the wait status and replace us.
    ctl.sendmsg([b"A" + struct.pack("<I", 0)],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", conn.fileno()))])
    return serve_child(state, conn)


def serve_child(state, conn):
    """Serve one accepted connection. Returns the tool's exit code, or never returns."""
    try:
        conn.settimeout(HANDSHAKE_TIMEOUT_S)
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, gid = struct.unpack("3i", creds)
        if uid != os.getuid():
            os._exit(0)
        try:
            kind, body, fds = recv_request(conn)
        except EOFError:
            os._exit(0)  # a client that only checked we are up
        if kind == b"P":
            if is_stale(state):
                mark_exiting(state)  # before the client starts a fresh daemon
                unlink_socket(state)
                os.kill(PARENT_PID, signal.SIGUSR1)
                send_msg(conn, b"P", b"stale: installed files changed")
            else:
                send_msg(conn, b"P", status_text(state).encode())
            os._exit(0)
        if kind == b"S":
            send_msg(conn, b"P", b"stopping")
            os.kill(PARENT_PID, signal.SIGTERM)
            os._exit(0)
        req = Request(body)
        if len(fds) not in (N_FDS - 1, N_FDS):
            send_msg(conn, b"N", b"missing file descriptors")
            os._exit(0)
        reason = why_not_fast(state, req, gid, fds)
        if reason:
            if reason.startswith("stale"):
                mark_exiting(state)  # before the client starts a fresh daemon
                unlink_socket(state)
                os.kill(PARENT_PID, signal.SIGUSR1)
            send_msg(conn, b"N", reason.encode())
            os._exit(0)
        send_msg(conn, b"R", struct.pack("<i", os.getpid()))
        if recv_exact(conn, 1) != b"G":
            os._exit(0)
        conn.settimeout(None)
        apply_request(state, req, fds)
    except BaseException as exc:  # nothing has run yet: let the client fall back
        try:
            log(f"child setup failed: {exc!r}")
        finally:
            os._exit(0)
    os.close(LOG_FD)
    return run_tool(state, conn)


def status_text(state):
    if state.disabled:
        return f"disabled: {state.disabled}"
    levels = ",".join(map(str, sorted(state.levels))) or "none"
    return (f"ready pid={PARENT_PID} preload_ms={state.preload_s * 1000:.0f} "
            f"modules={len(sys.modules)} levels={levels} learned={len(state.learned)} "
            f"blacklisted={len(state.blacklist)} "
            f"memo={len(state.memo.entries) if state.memo is not None else 0} gen={state.gen} "
            f"tty_read={','.join(map(str, sorted(state.import_tty_read))) or '-'}"
            + (f" learning={state.learning_left}" if state.learning_left else ""))


# --- parent: accept loop ---------------------------------------------------

def open_runtime_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        os.close(fd)
        raise RuntimeError(f"{path} must be owned by uid {os.getuid()} with mode 0700")
    return fd


def acquire_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() > deadline:
                os.close(fd)
                return None
            time.sleep(0.05)


def wait_status_payload(status):
    if os.WIFSIGNALED(status):
        return struct.pack("<Bi", 1, os.WTERMSIG(status))
    return struct.pack("<Bi", 0, os.WEXITSTATUS(status))


def handle_inline(state, conn):
    """Disabled daemon: answer without forking."""
    try:
        conn.settimeout(0.5)
        kind, _body, fds = recv_request(conn)
        for fd in fds:
            os.close(fd)
        if kind == b"P":
            send_msg(conn, b"P", status_text(state).encode())
        elif kind == b"S":
            send_msg(conn, b"P", b"stopping")
            return False
        else:
            send_msg(conn, b"N", state.disabled.encode())
    except (OSError, EOFError, ValueError):
        pass
    finally:
        conn.close()
    return True


class Learning:
    """What apply_learned still has to vet, across its steps."""

    def __init__(self, names, files, before, origins, t0):
        self.names, self.files, self.before, self.origins, self.t0 = names, files, before, origins, t0


class Child:
    """A forked child as the parent sees it: a spare until it accepts a client."""

    def __init__(self, pid, ctl):
        self.pid = pid
        self.ctl = ctl    # parent end of the control socket; None at EOF
        self.conn = None  # the client connection, once accepted
        self.retired = False

    def close(self):
        for sock in (self.ctl, self.conn):
            if sock is not None:
                sock.close()
        self.ctl = self.conn = None


def read_ctl(sock):
    """One message from a child's control socket: (kind, payload, fds), kind None at EOF."""
    data, ancdata, _flags, _addr = sock.recvmsg(5, socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
    fds = []
    for level, ctype, cdata in ancdata:
        if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
            fds += list(struct.unpack(f"<{len(cdata) // 4}i", cdata[:len(cdata) // 4 * 4]))
    if not data:
        return None, b"", fds
    data += recv_exact(sock, 5 - len(data))
    (length,) = struct.unpack("<I", data[1:5])
    return data[:1], recv_exact(sock, length), fds


class Parent:
    """The accept side: keeps one spare forked and waiting, reaps children."""

    def __init__(self, state, lsock, parent_fds):
        self.state = state
        self.lsock = lsock
        self.parent_fds = parent_fds
        self.children = {}  # pid -> Child
        self.spare = None
        self.spare_failures = 0
        self.last_activity = time.monotonic()
        self.learning = None  # Learning, while apply_learned has steps left

    def fork_spare(self):
        """Returns the child's pid in the parent, 0 in the spare."""
        if threading.active_count() != 1 or native_thread_count() != 1:
            self.state.disabled = "threads appeared in the daemon"
            return -1
        gc.freeze()  # the children's GC must not walk, and so copy, the inherited heap
        sys.stdout.flush()
        sys.stderr.flush()
        ctl, ctl_child = socket.socketpair()
        pid = os.fork()
        if pid == 0:
            ctl.close()
            self.ctl_child = ctl_child
            return 0
        ctl_child.close()
        self.spare = self.children[pid] = Child(pid, ctl)
        return pid

    def inherited_fds(self):
        fds = [fd for fd in self.parent_fds if fd != self.lsock.fileno()]
        for child in self.children.values():
            fds += [s.detach() for s in (child.ctl, child.conn) if s is not None]
        return fds

    def retire_spare(self):
        if self.spare is not None:
            self.spare.retired = True
            try:
                self.spare.ctl.sendall(b"Q")
            except OSError:
                pass
            self.spare = None

    def on_ctl(self, child):
        try:
            kind, payload, fds = read_ctl(child.ctl)
        except (OSError, EOFError):
            kind, payload, fds = None, b"", []
        if kind == b"L":
            self.learn(payload)
        if kind == b"A" and fds:
            child.conn = socket.socket(fileno=fds.pop(0))
            self.last_activity = time.monotonic()
            if child is self.spare:
                self.spare = None
                self.spare_failures = 0
        for fd in fds:
            os.close(fd)
        if kind is None:
            child.ctl.close()
            child.ctl = None

    def learn(self, payload):
        try:
            data = json.loads(payload)
        except ValueError:
            return
        state = self.state
        for name in data.get("modules") or []:
            if isinstance(name, str) and name not in sys.modules and name not in state.blacklist:
                state.pending[name] = None
        for path in data.get("paths") or []:
            if isinstance(path, str) and os.path.isabs(path) and path not in state.learned_paths:
                state.learned_paths.append(path)
                extend_fingerprint(state, [path] if os.path.isdir(path) else [])
        origins = data.get("origins")
        if isinstance(origins, dict):
            state.pending_origins.update((k, v) for k, v in origins.items()
                                         if isinstance(k, str) and isinstance(v, str))
        if 3 in state.levels:
            for path in data.get("files") or []:
                if isinstance(path, str):
                    state.pending_files[path] = None

    def apply_learned(self):
        """Vet and import what children learned, parse the data files they
        loaded, then replace the spare. One probe per call: between calls
        the main loop keeps a spare ready. Returns a reason to restart, or
        None."""
        state, job = self.state, self.learning
        if job is None:
            job = self.learning = Learning(
                names=[n for n in state.pending if n not in sys.modules and n not in state.blacklist],
                files=list(state.pending_files), before=self.snapshot(),
                origins=dict(state.pending_origins), t0=time.perf_counter())
            state.pending.clear()
            state.pending_files.clear()
            state.pending_origins.clear()
            if job.names:
                # A spare forked from now on says so in its status.
                state.learning_left = len(job.names)
                self.retire_spare()
                return None
        if job.names:
            verdict = probe_imports(job.names, state.tty, state.learned_paths, job.origins,
                                    set(state.blacklist) | set(state.skip))
            state.blacklist.update(verdict["bad"])
            why = import_in_daemon(state, verdict["ok"])
            if why:
                self.learning = None
                save_cache(state)
                return why
            extend_fingerprint(state, module_files(), watch=True)
            job.names = [n for n in verdict["retry"] if n not in sys.modules]
            state.learning_left = len(job.names)
            if job.names:
                return None
            self.retire_spare()  # it said it was learning
        self.learning = None
        files, before, t0 = job.files, job.before, job.t0
        install_memo(state)
        if state.memo is not None:
            state.memo.refresh(files)
        if self.snapshot() == before:
            return None  # nothing new: keep the spare
        state.top_names = module_top_names()
        state.gen += 1
        save_cache(state)
        log(f"learned in {(time.perf_counter() - t0) * 1000:.0f} ms; {status_text(state)}")
        self.retire_spare()
        return None

    def snapshot(self):
        state, memo = self.state, self.state.memo
        return (len(state.learned), len(state.blacklist),
                None if memo is None else {p: e[0] for p, e in memo.entries.items()})

    def reap(self):
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            child = self.children.pop(pid, None)
            if child is None:
                continue
            # What it sent before dying. Without waiting: a process it forked
            # may still hold its end.
            while child.ctl is not None and select.select([child.ctl], [], [], 0)[0]:
                self.on_ctl(child)
            if child is self.spare:
                self.spare = None
                self.spare_failures += 1
                if self.spare_failures >= 3:
                    self.state.disabled = "spare children keep dying"
                    log(self.state.disabled)
            if child.conn is not None:
                try:
                    send_msg(child.conn, b"W", wait_status_payload(status))
                except OSError:
                    pass  # client gone
            child.close()

    def active(self):
        return any(c.conn is not None for c in self.children.values())


def without_links(state, key, value):
    """PATH without our own link directories (`speedrunner exec`, `activate`):
    they lead to the very tools the rest of PATH names, so a tool that read
    PATH as it loaded behaves the same with or without them."""
    if key != "PATH" or value is None:
        return value
    links = os.path.dirname(state.sock_path)
    return os.pathsep.join(d for d in value.split(os.pathsep)
                           if not (os.path.dirname(d.rstrip("/")) == links
                                   and os.path.basename(d.rstrip("/")).startswith("bin-")))


def mark_exiting(state):
    """Say so in the lock file before letting go of the socket and the lock:
    a would-be daemon then waits for the lock instead of giving up."""
    try:
        fd = os.open(os.path.basename(state.sock_path) + ".lock", os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"exiting {PARENT_PID}\n".encode(), 0)
        finally:
            os.close(fd)
    except OSError:
        pass


def unlink_socket(state):
    """Remove our socket, not one a newer daemon bound since. Relative to the
    runtime directory, which is the cwd until a child applies a request."""
    name = os.path.basename(state.sock_path)
    try:
        if os.stat(name).st_ino == state.bound_ino:
            os.unlink(name)
    except OSError:
        pass


def serve(state):
    dir_fd = open_runtime_dir(os.path.dirname(state.sock_path))
    if state.lock_fd is not None:
        lock_fd = state.lock_fd
        os.set_inheritable(lock_fd, False)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # ours already: a no-op
    else:
        lock_fd = acquire_lock(state.sock_path + ".lock")
    if lock_fd is None:
        log("another daemon holds the lock; exiting")
        return 0
    # Relative to the verified directory from here on.
    os.fchdir(dir_fd)
    name = os.path.basename(state.sock_path)
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass

    # Bound before preloading: a client that comes meanwhile (the shell
    # warmed us up while the user typed) waits for the first spare instead
    # of running natively. It never runs before its own go.
    umask = os.umask(0o077)
    lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lsock.bind(name)
    os.chmod(name, 0o600)
    os.umask(umask)  # imports may write files: keep the user's umask for them
    state.bound_ino = os.stat(name).st_ino
    lsock.listen(128)
    # Shared with the spares, which select before accepting.
    lsock.setblocking(False)

    preload(state)
    if state.restart:
        log(f"exiting: {state.restart}; the next client starts a fresh daemon")
        mark_exiting(state)
        unlink_socket(state)
        return 0
    log(f"preloaded {state.tool} in {state.preload_s * 1000:.0f} ms, started by "
        f"{state.origin}; {status_text(state)}")

    wake_r, wake_w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    signal.set_wakeup_fd(wake_w)
    stop = {"why": None}
    signal.signal(signal.SIGCHLD, lambda *_: None)
    signal.signal(signal.SIGTERM, lambda *_: stop.update(why="SIGTERM"))
    signal.signal(signal.SIGUSR1, lambda *_: stop.update(why="stale fingerprint"))
    parent = Parent(state, lsock, [lsock.fileno(), wake_r, wake_w, lock_fd, dir_fd])
    try:
        while stop["why"] is None:
            if parent.spare is None and not state.disabled:
                if parent.fork_spare() == 0:
                    return run_spare(state, lsock, parent.ctl_child, parent.inherited_fds())
            if state.disabled:
                parent.retire_spare()
            watch = [wake_r] + [c.ctl for c in parent.children.values() if c.ctl is not None]
            if state.disabled:
                watch.append(lsock)
            if state.watch is not None and state.watch.fd >= 0:
                watch.append(state.watch.fd)  # never read: children must see it too
            timeout = max(1.0, parent.last_activity + IDLE_TIMEOUT_S - time.monotonic())
            if parent.learning is not None and not parent.active():
                timeout = 0  # its next step, once the spare is replaced
            readable, _, _ = select.select(watch, [], [], timeout)
            if state.watch is not None and state.watch.fd in readable:
                stop["why"] = "stale: a preloaded module file changed"
                break
            for child in list(parent.children.values()):
                if child.ctl is not None and child.ctl in readable:
                    parent.on_ctl(child)
            if wake_r in readable:
                try:
                    while os.read(wake_r, 512):
                        pass
                except BlockingIOError:
                    pass
                parent.reap()
            if lsock in readable:
                try:
                    conn, _ = lsock.accept()
                except BlockingIOError:
                    continue
                parent.last_activity = time.monotonic()
                conn.setblocking(True)
                if not handle_inline(state, conn):
                    stop["why"] = "stop request"
            if ((state.pending or state.pending_files or parent.learning is not None)
                    and not state.disabled and not parent.active()):
                why = parent.apply_learned()
                if why:
                    stop["why"] = f"learned imports misbehaved: {why}"
            if (not parent.active()
                    and time.monotonic() >= parent.last_activity + IDLE_TIMEOUT_S):
                stop["why"] = "idle timeout"
        log(f"exiting: {stop['why']}")
        # Out of the way first, so that new clients start a fresh daemon;
        # then the runs in progress, some of which report their end only
        # through our wait status (killed by a signal).
        mark_exiting(state)
        unlink_socket(state)
        lsock.close()  # clients queued on it fall back at once
        parent.retire_spare()
        # The lock only says who serves the socket: a new daemon may start
        # now, however long the runs we wait for last.
        os.close(lock_fd)
        lock_fd = None
        while parent.children:
            ctls = [c.ctl for c in parent.children.values() if c.ctl is not None]
            readable, _, _ = select.select([wake_r] + ctls, [], [], 0.05)
            for child in list(parent.children.values()):
                if child.ctl is not None and child.ctl in readable:
                    parent.on_ctl(child)
            try:
                while os.read(wake_r, 512):
                    pass
            except BlockingIOError:
                pass
            parent.reap()
        return 0
    finally:
        if os.getpid() == PARENT_PID and lock_fd is not None:
            mark_exiting(state)
            unlink_socket(state)
            os.close(lock_fd)


def parse_args(argv):
    opts = {}
    it = iter(argv)
    for flag in it:
        if flag not in ("--tool", "--script", "--module", "--socket", "--lock-fd", "--origin",
                        "--tty"):
            raise SystemExit(f"speedrunner_daemon: unknown argument {flag!r}")
        opts[flag[2:]] = next(it)
    if ("script" in opts) == ("module" in opts):
        raise SystemExit("speedrunner_daemon: one of --script and --module")
    missing = {"tool", "socket"} - set(opts)
    if missing:
        raise SystemExit(f"speedrunner_daemon: missing {', '.join(sorted(missing))}")
    return opts


def tty_stand_in(spec):
    """--tty MASK[:ROWSxCOLS] -> TtyStandIn, or None."""
    mask, _, size = spec.partition(":")
    rows, _, cols = size.partition("x")
    try:
        mask, rows, cols = int(mask) & 7, int(rows or 0), int(cols or 0)
        return TtyStandIn(mask, rows, cols) if mask else None
    except (ValueError, OSError) as exc:
        log(f"no terminal stand-in ({exc!r}): imports see none")
        return None


def main():
    track_std_streams()  # before anything holds a reference to them
    opts = parse_args(sys.argv[1:])
    atexit.register(report_exit)  # before any import can register its own
    lock_fd = int(opts["lock-fd"]) if "lock-fd" in opts else None
    state = State(opts["tool"], opts.get("script"), opts["socket"], lock_fd, opts.get("module"))
    state.origin = opts.get("origin", state.origin)
    state.tty = tty_stand_in(opts.get("tty", "0"))
    return serve(state)


if __name__ == "__main__":
    sys.exit(main())
