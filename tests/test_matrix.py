"""Regression matrix (brief §5): each case runs natively and through
speedrunner, and the two runs must be indistinguishable.

Env:
  HS_BIN            speedrunner client binary. Unset: validate the harness
                    itself (both sides run natively).
  HS_PATH           PATH entries holding the hstest and aws executables.
  SPEEDRUNNER_DAEMON  forwarded to the client.

Run: python3 tests/test_matrix.py -v
"""
import concurrent.futures
import ctypes
import glob
import gzip
import json
import os
import pty
import select
import socket
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

HS_BIN = os.environ.get("HS_BIN")
MACOS = sys.platform == "darwin"
TOOLS = ("hstest", "aws")
EXIT_NOT_FAST = 124


def make_env(root):
    home = os.path.join(root, "home")
    os.makedirs(home, exist_ok=True)
    env = {
        "PATH": os.environ["HS_PATH"] + ":/usr/local/bin:/usr/bin:/bin",
        "HOME": home,
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": os.path.join(root, "run"),
        # Keep aws offline and independent of any real ~/.aws.
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": os.path.join(home, "aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": os.path.join(home, "aws-credentials"),
    }
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)
    if "SPEEDRUNNER_DAEMON" in os.environ:
        env["SPEEDRUNNER_DAEMON"] = os.environ["SPEEDRUNNER_DAEMON"]
    return env


# Under /tmp: macOS's own temp dir is too deep for a socket path (104 bytes).
ROOT = tempfile.mkdtemp(prefix="hs-matrix-", dir="/tmp" if MACOS else None)
BASE_ENV = make_env(ROOT)


def command(mode, argv, require_fast=True):
    """Return (argv, extra env) for a native or speedrunner run."""
    if mode == "native" or not HS_BIN:
        return list(argv), {}
    extra = {"SPEEDRUNNER_REQUIRE_FAST": "1"} if require_fast else {}
    return [HS_BIN, "run", argv[0], "--", *argv[1:]], extra


def report_sigkill(argv):
    """A tool that rarely comes back as -9 (CI, macOS Intel): who did it? "client
    gone, SIGKILL": its daemon. "died of signal 9" alone: something else killed
    the child. Neither: the client itself was killed."""
    lines = []
    for path in glob.glob(os.path.join(ROOT, "**", "speedrunner", "*.log"), recursive=True):
        with open(path, errors="replace") as f:
            lines += [f"{os.path.basename(path)}: {line.rstrip()}" for line in f
                      if "SIGKILL" in line or "died of signal" in line or "after its go" in line
                      or "looked readable" in line]
    sys.stderr.write(f"\n{argv}: -9. Daemon logs:\n  " + ("\n  ".join(lines[-12:]) or "(nothing)") + "\n")


def run(mode, argv, input=b"", cwd=None, env=None, tty=False, umask=None,
        require_fast=True, preexec=None, pipe_to=None):
    cmd, extra = command(mode, argv, require_fast)
    full_env = {**BASE_ENV, **(env or {}), **extra}
    if pipe_to:
        cmd = ["bash", "-c", f'{shlex.join(cmd)} | {pipe_to}; echo "status=${{PIPESTATUS[0]}}" >&2']
    if tty:
        # script(1) gives the command a pty of a known size.
        line = "stty cols 123 rows 45; " + shlex.join(cmd)
        cmd = (["script", "-q", "/dev/null", "sh", "-c", line] if MACOS
               else ["script", "-qec", line, "/dev/null"])

    def setup():
        if umask is not None:
            os.umask(umask)
        if preexec is not None:
            preexec()

    p = subprocess.run(cmd, input=input, capture_output=True, cwd=cwd,
                       env=full_env, preexec_fn=setup, timeout=60)
    if p.returncode == EXIT_NOT_FAST:
        sys.stderr.write(p.stderr.decode(errors="replace"))  # why: most tests only see its stdout
    if mode == "hs" and p.returncode == -signal.SIGKILL:
        report_sigkill(argv)
    if tty and MACOS:
        # BSD script(1) now and then turns a line's \r\n into \r\r\n on a
        # long output, natively as served (CI, aws's skeleton), and echoes
        # the ^D it sends the pty once its own stdin is at end of file,
        # sooner or later: not the tool's.
        p.stdout = p.stdout.replace(b"\r\r\n", b"\r\n").replace(b"^D\x08\x08", b"")
    return p


def outcome(p, normalize=None):
    out = normalize(p.stdout) if normalize else p.stdout
    return p.returncode, out, p.stderr


SKELETON = ["aws", "ec2", "run-instances", "--generate-cli-skeleton"]


def blank_strings(out):
    """botocore fills skeleton enums with random.choice: compare shapes only."""
    return re.sub(rb'"[^"\r\n]*"(\s*[,\r\n}\]])', rb'"<s>"\1', out)


def status(tool, env=None):
    p = subprocess.run([HS_BIN, "status", tool], env={**BASE_ENV, **(env or {})},
                       capture_output=True)
    return p.stdout.decode().strip()


def status_field(text, key):
    m = re.search(rf"\b{key}=(\S+)", text)
    return m.group(1) if m else None


def wait_ready(tool, env=None, timeout=30.0, cwd=None):
    deadline = time.monotonic() + timeout
    while True:
        text = status(tool, env)
        if text.startswith("ready"):
            return text
        if text == "down":
            subprocess.run([HS_BIN, "prewarm", tool], env={**BASE_ENV, **(env or {})}, cwd=cwd, check=True)
        if time.monotonic() > deadline:
            raise RuntimeError(f"{tool} daemon not ready: {text!r}")
        time.sleep(0.1)


def served_soon(argv, env=None, timeout=30.0):
    """Runs argv until the fast path takes it: a daemon that restarts (it
    skipped a module) may still answer `status` just before it goes."""
    deadline = time.monotonic() + timeout
    while run("hs", argv, env=env).returncode == EXIT_NOT_FAST:
        if time.monotonic() > deadline:
            raise RuntimeError(f"{argv[0]} never served")
        time.sleep(0.1)


def stop(tool, env=None, timeout=10.0):
    subprocess.run([HS_BIN, "stop", tool], env={**BASE_ENV, **(env or {})}, capture_output=True)
    deadline = time.monotonic() + timeout
    while status(tool, env) != "down" and time.monotonic() < deadline:
        time.sleep(0.05)


def read_frame(sock):
    """A message from a daemon: kind, payload."""
    def exactly(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError(f"after {data!r}")
            data += chunk
        return data

    header = exactly(5)
    return header[:1], exactly(struct.unpack("<I", header[1:])[0])


def tool_socket(tool, env=None):
    run_dir = {**BASE_ENV, **(env or {})}["XDG_RUNTIME_DIR"]
    return glob.glob(os.path.join(run_dir, "speedrunner", f"{tool}-*.sock"))[0]


def ask_to_run(tool, *args, timeout=30.0):
    """A client that stops at the child's R, to send its go when the test
    says: the socket, and the child's pid."""
    def blobs(items):
        return struct.pack("<I", len(items)) + b"".join(struct.pack("<I", len(i)) + i for i in items)

    umask = os.umask(0o022)
    os.umask(umask)
    groups = os.getgroups()
    body = (blobs([os.fsencode(a) for a in (shutil.which(tool, path=BASE_ENV["PATH"]), *args)])
            + blobs([os.fsencode(f"{k}={v}") for k, v in BASE_ENV.items()])
            + struct.pack("<IQ", umask, 0)
            + struct.pack(f"<I{len(groups)}I", len(groups), *groups))
    deadline = time.monotonic() + timeout
    while True:
        wait_ready(tool)
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(15)
        sock.connect(tool_socket(tool))
        fds = [os.open(os.devnull, os.O_RDWR) for _ in range(3)] + [os.open(".", os.O_RDONLY)]
        assert read_frame(sock)[0] == b"H"
        socket.send_fds(sock, [b"HS1R" + struct.pack("<I", len(body)) + body], fds)
        for fd in fds:
            os.close(fd)
        kind, payload = read_frame(sock)
        if kind == b"R":
            return sock, struct.unpack("<i", payload)[0]
        sock.close()  # a daemon that restarts (it skipped a module) refuses for a moment
        if time.monotonic() > deadline:
            raise RuntimeError(f"{tool}: {kind} {payload!r}")
        time.sleep(0.1)


def check_child_says_it_missed_its_go(test, tool):
    """Past G the client waits for the end of the run: from a child that gave
    up before starting the tool, it must learn that nothing ran, or it takes
    the child's exit for the tool's success. It gives up after 5 s."""
    sock, _ = ask_to_run(tool, "argv")
    with sock:
        test.assertEqual(read_frame(sock)[0], b"N")


def check_child_dies_of_a_signal_before_its_go(test, tool):
    """A client relays signals as soon as it has the pid: natively, Ctrl-C at
    that moment kills the tool. Not an exit in 0, not a run."""
    sock, pid = ask_to_run(tool, "argv")
    with sock:
        os.kill(pid, signal.SIGINT)
        test.assertEqual(read_frame(sock), (b"W", b"\x01" + struct.pack("<i", signal.SIGINT)))


def setUpModule():
    if HS_BIN:
        for tool in TOOLS:
            wait_ready(tool)


def tearDownModule():
    if HS_BIN:
        for tool in TOOLS:
            stop(tool)
    shutil.rmtree(ROOT, ignore_errors=True)


class Matrix(unittest.TestCase):
    maxDiff = None

    def assertSame(self, argv, normalize=None, **kw):
        native = outcome(run("native", argv, **kw), normalize)
        fast = outcome(run("hs", argv, **kw), normalize)
        self.assertEqual(native, fast)
        return native


class OutputsAndExitCodes(Matrix):
    def test_aws_version(self):
        self.assertSame(["aws", "--version"])

    def test_aws_configure_list(self):
        self.assertSame(["aws", "configure", "list"])

    def test_aws_skeleton(self):
        self.assertSame(SKELETON, normalize=blank_strings)

    def test_aws_unknown_command(self):
        rc, _, _ = self.assertSame(["aws", "nosuchcommand"])
        self.assertNotEqual(rc, 0)

    def test_aws_stdin_piped(self):
        rc, _, _ = self.assertSame(
            ["aws", "ec2", "describe-instances", "--cli-input-json", "file:///dev/stdin"],
            input=b"not json")
        self.assertNotEqual(rc, 0)

    def test_stdin_binary_roundtrip(self):
        self.assertSame(["hstest", "echo-stdin"], input=bytes(range(256)) * 64)

    def test_binary_stdout(self):
        self.assertSame(["hstest", "binary"])

    def test_interleave_pipes(self):
        self.assertSame(["hstest", "interleave"])

    def test_usage_error(self):
        rc, _, _ = self.assertSame(["hstest"])
        self.assertEqual(rc, 2)

    def test_exit_variants(self):
        for kind, expected in [("noarg", 0), ("none", 0), ("int:3", 3), ("int:300", 44),
                               ("str:boom", 1), ("raise", 1), ("7", 7)]:
            with self.subTest(kind=kind):
                rc, _, _ = self.assertSame(["hstest", "exit", kind])
                self.assertEqual(rc, expected)

    def test_atexit(self):
        self.assertSame(["hstest", "atexit"])

    def test_broken_pipe(self):
        # CPython reports the failed stdout flush at teardown and exits with 120.
        _, _, err = self.assertSame(["hstest", "pending-output"], pipe_to="true")
        self.assertTrue(err.endswith(b"status=120\n"), err)


class Terminal(Matrix):
    def test_tty_streams(self):
        self.assertSame(["hstest", "tty"], tty=True)

    def test_non_tty_streams(self):
        self.assertSame(["hstest", "tty"])

    def test_interleave_tty(self):
        self.assertSame(["hstest", "interleave"], tty=True)

    def test_aws_on_tty(self):
        self.assertSame(SKELETON, normalize=blank_strings, tty=True)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_terminal_state_read_at_import(self):
        # hstty's entry module notes at import whether its streams are
        # terminals: a daemon started from a terminal serves terminals, and
        # sends a client on pipes to the native run; and the reverse.
        for first, other in ((True, False), (False, True)):
            with self.subTest(started_on_tty=first):
                stop("hstty")
                self.addCleanup(stop, "hstty")
                run("hs", ["hstty"], tty=first, require_fast=False)  # starts the daemon
                wait_ready("hstty")
                _, out, _ = self.assertSame(["hstty"], tty=first)
                self.assertIn(b"live " + (b"(True, True, True)" if first else b"(False, False, False)"), out)
                _, out, _ = self.assertSame(["hstty"], tty=other, require_fast=False)
                self.assertIn(b"live " + (b"(True, True, True)" if other else b"(False, False, False)"), out)
                self.assertEqual(run("hs", ["hstty"], tty=other).returncode, EXIT_NOT_FAST)


class Signals(Matrix):
    def run_and_signal(self, mode, sig, to_group):
        cmd, extra = command(mode, ["hstest", "sleep", "10"])
        p = subprocess.Popen(cmd, env={**BASE_ENV, **extra}, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        self.assertEqual(p.stdout.readline(), b"ready\n")
        # Let the tool reach time.sleep(), so the traceback line is stable.
        time.sleep(0.3)
        if to_group:
            os.killpg(p.pid, sig)  # what the terminal does for Ctrl-C
        else:
            os.kill(p.pid, sig)
        out, err = p.communicate(timeout=30)
        return p.returncode, b"ready\n" + out, err

    def test_ctrl_c(self):
        native = self.run_and_signal("native", signal.SIGINT, True)
        self.assertEqual(native, self.run_and_signal("hs", signal.SIGINT, True))
        self.assertEqual(native[0], -signal.SIGINT)

    def test_sigterm(self):
        native = self.run_and_signal("native", signal.SIGTERM, False)
        self.assertEqual(native, self.run_and_signal("hs", signal.SIGTERM, False))
        self.assertEqual(native[0], -signal.SIGTERM)


class Context(Matrix):
    def test_cwd(self):
        d = tempfile.mkdtemp(dir=ROOT)
        self.assertSame(["hstest", "cwd"], cwd=d, env={"PWD": d})

    def test_umask(self):
        self.assertSame(["hstest", "umask"], umask=0o027)

    def test_env_python_c_and_subprocess(self):
        self.assertSame(["hstest", "env", "HSTEST_IMPORTED", "HSTEST_CUSTOM", "AWS_PROFILE"],
                        env={"HSTEST_CUSTOM": "custom value"})

    def test_aws_profile(self):
        rc, _, _ = self.assertSame(["aws", "configure", "list"], env={"AWS_PROFILE": "missing"})
        self.assertNotEqual(rc, 0)

    def test_aws_region(self):
        self.assertSame(["aws", "configure", "list"], env={"AWS_DEFAULT_REGION": "eu-west-3"})

    def test_open_file_limit(self):
        # The macOS daemon raises its own, for kqueue: not the tool's.
        self.assertSame(["hstest", "limits"])

    def test_argv_and_paths(self):
        self.assertSame(["hstest", "argv", "a b", "", "--x=1"])

    def test_argv_bound_at_import(self):
        _, out, _ = self.assertSame(["hstest", "argv-default", "x", "--y"])
        self.assertEqual(out, b"['argv-default', 'x', '--y']\n")

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_module_that_runs_the_program_at_import(self):
        # hsrun's entry module prints its arguments when imported: preloading
        # it would run it with the daemon's command line.
        wait_ready("hsrun")
        self.addCleanup(stop, "hsrun")
        run("hs", ["hsrun", "warm"], require_fast=False)  # may restart the daemon once
        served_soon(["hsrun", "warm"])
        _, out, _ = self.assertSame(["hsrun", "a", "b"])
        self.assertEqual(out, b"autorun: ['a', 'b']\n")

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_module_that_starts_a_thread_at_import(self):
        # Forking would lose the thread: the daemon skips the module, and
        # still serves the tool.
        wait_ready("hsthread")
        self.addCleanup(stop, "hsthread")
        run("hs", ["hsthread"], require_fast=False)  # may restart the daemon once
        served_soon(["hsthread"])
        _, out, _ = self.assertSame(["hsthread"])
        self.assertEqual(out, b"threaded\n")

    def test_signal_dispositions(self):
        self.assertSame(["hstest", "signals"])

    def test_inherited_ignored_signal(self):
        # As under nohup: an inherited SIG_IGN must stay ignored.
        self.assertSame(["hstest", "signals"],
                        preexec=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))


class Randomness(Matrix):
    def test_runs_differ(self):
        a = run("hs", ["hstest", "random"])
        b = run("hs", ["hstest", "random"])
        self.assertEqual((a.returncode, b.returncode), (0, 0))
        self.assertNotEqual(a.stdout.splitlines()[0], b.stdout.splitlines()[0])
        self.assertNotEqual(a.stdout.splitlines()[1], b.stdout.splitlines()[1])

    def test_aws_skeleton_enums_vary(self):
        # Same as natively: random.choice picks differ between runs.
        outputs = {run("hs", SKELETON).stdout for _ in range(3)}
        self.assertGreater(len(outputs), 1)


class Concurrency(Matrix):
    def test_ten_parallel(self):
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            results = list(pool.map(lambda _: run("hs", ["hstest", "random"]), range(10)))
        self.assertEqual([r.returncode for r in results], [0] * 10)
        uuids = {r.stdout.splitlines()[0] for r in results}
        self.assertEqual(len(uuids), 10)


    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_ten_parallel_cold_start(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        try:
            with concurrent.futures.ThreadPoolExecutor(10) as pool:
                results = list(pool.map(
                    lambda _: run("hs", ["hstest", "random"], env=env, require_fast=False),
                    range(10)))
            self.assertEqual([r.returncode for r in results], [0] * 10)
            self.assertEqual(len({r.stdout.splitlines()[0] for r in results}), 10)
            wait_ready("hstest", env)
            self.assertEqual(len(daemon_roots(root)), 1)
        finally:
            stop("hstest", env)


    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_run_while_daemon_starts_keeps_no_pipe_open(self):
        # The would-be daemon a run spawns must not hold the run's pipes while
        # it waits for the lock another starting daemon holds.
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        try:
            subprocess.run([HS_BIN, "prewarm", "hstest"], env={**BASE_ENV, **env}, check=True)
            t0 = time.monotonic()
            p = run("hs", ["hstest", "argv"], env=env, require_fast=False)
            self.assertEqual(p.returncode, 0)
            self.assertLess(time.monotonic() - t0, 1.5)
        finally:
            stop("hstest", env)


def daemon_roots(root):
    """Daemons whose socket lives under root, not counting their own children."""
    ps = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True)
    procs = {}
    for line in ps.stdout.splitlines():
        pid, ppid, args = line.split(None, 2)
        if "speedrunner_daemon" in args and root in args:
            procs[pid] = ppid
    return [pid for pid, ppid in procs.items() if ppid not in procs]


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class NoLocale(Matrix):
    """No LANG nor LC_*, as in a fresh container or cron. In a C locale,
    CPython adds LC_CTYPE=C.UTF-8 to its own env at startup (PEP 538): the
    daemon's env is then not the client's, but the tool's must be as natively."""

    def test_fast_and_same_env(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {k: v for k, v in BASE_ENV.items() if k != "LANG"}
        env.update(XDG_RUNTIME_DIR=os.path.join(root, "run"), XDG_CACHE_HOME=os.path.join(root, "cache"))
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(lambda: subprocess.run([HS_BIN, "stop", "hstest"], env=env, capture_output=True))
        argv = ["hstest", "env", "LC_CTYPE", "LC_ALL", "LANG"]

        def go(mode, fast=True):
            cmd, extra = command(mode, argv, require_fast=fast)
            return outcome(subprocess.run(cmd, env={**env, **extra}, capture_output=True, timeout=60))

        native = go("native")
        go("hs", fast=False)  # starts the daemon, from this env
        deadline = time.monotonic() + 30
        fast = go("hs")
        while fast[0] == EXIT_NOT_FAST:
            self.assertLess(time.monotonic(), deadline, fast[2])
            time.sleep(0.1)
            fast = go("hs")
        self.assertEqual(native, fast)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class ScriptPrefix(Matrix):
    """`srr <tool> args`, a link to the client: a prefix that needs no
    enrolling, for scripts too (they do not see the shell's wrappers)."""

    @classmethod
    def setUpClass(cls):
        cls.sr = os.path.join(tempfile.mkdtemp(dir=ROOT), "srr")
        os.symlink(HS_BIN, cls.sr)

    def test_sr_matches_native_and_is_fast(self):
        for argv in (["hstest", "argv", "a b", "--x"], ["hstest", "exit", "int:3"],
                     ["aws", "configure", "list"]):
            with self.subTest(argv=argv):
                native = outcome(run("native", argv))
                p = subprocess.run([self.sr, *argv], capture_output=True, timeout=60,
                                   env={**BASE_ENV, "SPEEDRUNNER_REQUIRE_FAST": "1"})
                self.assertEqual(native, outcome(p))

    def test_sr_in_a_bash_script(self):
        script = f'''
            for i in 1 2 3; do {self.sr} hstest exit int:$i; echo "rc=$?"; done
            {self.sr} hstest echo-stdin <<< "piped"
        '''
        p = subprocess.run(["bash", "-c", script], capture_output=True, timeout=60,
                           env={**BASE_ENV, "SPEEDRUNNER_REQUIRE_FAST": "1"})
        self.assertEqual(p.stdout, b"rc=1\nrc=2\nrc=3\npiped\n")

    def test_sr_without_tool(self):
        p = subprocess.run([self.sr], capture_output=True, env=BASE_ENV)
        self.assertEqual((p.returncode, p.stderr), (2, b"usage: srr [--native] <tool> [args...]\n"))
        p = subprocess.run([self.sr, "--native"], capture_output=True, env=BASE_ENV)
        self.assertEqual((p.returncode, p.stderr), (2, b"usage: srr [--native] <tool> [args...]\n"))


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class RelativeScript(Matrix):
    """`./manage.py`: a script named relative to the cwd, a different one in
    each project. Each gets its own daemon, and sees argv[0] as typed."""

    def project(self, name):
        python = shutil.which("python3", path=os.environ["HS_PATH"])
        d = tempfile.mkdtemp(dir=ROOT, prefix=name + "-")
        with open(os.path.join(d, "manage.py"), "w") as f:
            f.write(f"#!{python}\nimport os, sys\nimport hstest.cli\n"
                    f"print({name!r}, sys.argv, os.path.basename(__file__),\n"
                    f"      sys.path[0] == os.path.dirname(os.path.realpath(__file__)))\n")
        os.chmod(os.path.join(d, "manage.py"), 0o755)
        env = {"PWD": d}
        self.addCleanup(lambda: subprocess.run([HS_BIN, "stop", "./manage.py"], cwd=d,
                                               env={**BASE_ENV, **env}, capture_output=True))
        return d, env

    def warm(self, d, env, timeout=30.0):
        deadline = time.monotonic() + timeout
        while run("hs", ["./manage.py"], cwd=d, env=env).returncode == EXIT_NOT_FAST:
            if time.monotonic() > deadline:
                raise RuntimeError(f"no daemon for {d}/manage.py")
            time.sleep(0.1)

    def test_one_daemon_per_project(self):
        shop, blog = self.project("shop"), self.project("blog")
        for d, env in (shop, blog):
            run("hs", ["./manage.py"], cwd=d, env=env, require_fast=False)
        for (d, env), name in ((shop, b"shop"), (blog, b"blog")):
            with self.subTest(project=name):
                self.warm(d, env)
                _, out, _ = self.assertSame(["./manage.py", "check"], cwd=d, env=env)
                self.assertEqual(out, b"%s ['./manage.py', 'check'] manage.py True\n" % name)


SH_LAUNCHER = """#!/bin/sh
# gcloud's shape: set up, then exec the interpreter on a script.
echo launcher >> "$TOOL_LOG"
LAUNCHED_BY=sh
export LAUNCHED_BY
trap '' USR2
exec {python} -s "$(dirname "$0")/../lib/tool.py" "$@"
"""
TOOL_PY = """import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "third"))

def main():
    import signal
    import thirdmod  # lazily, from the entry added above: learned with it
    with open(os.environ["TOOL_LOG"], "a") as f:
        f.write("tool\\n")
    print(sys.argv, os.environ.get("LAUNCHED_BY"), sys.flags.no_user_site, thirdmod.VALUE,
          signal.getsignal(signal.SIGUSR2) == signal.SIG_IGN,
          getattr(sys, "orig_argv", ["", "-s"])[1:2], os.umask(0o22))  # orig_argv: 3.10+
    sys.exit(3 if sys.argv[1:] == ["fail"] else 0)

if __name__ == "__main__":
    main()
"""
PY_LAUNCHER = """#!{python}
# az's shape: set up, then exec this same interpreter on -m module.
import os
import sys

with open(os.environ["TOOL_LOG"], "a") as f:
    f.write("launcher\\n")
os.environ["LAUNCHED_BY"] = "py"
os.environ["PYTHONPATH"] = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib")
os.execl(sys.executable, sys.executable, "-m", "lmod", *sys.argv[1:])
"""
LMOD_MAIN = """import json, os, sys

def main():
    from lmod import lazy
    print(sys.argv[0] == __file__, os.path.basename(sys.argv[0]), sys.argv[1:], __name__,
          __spec__.name, sys.path[0] == os.getcwd(), os.environ.get("LAUNCHED_BY"), lazy.VALUE,
          getattr(json, "VALUE", "stdlib json"), getattr(sys, "orig_argv", ["", "-m", "lmod"])[1:3])
    if sys.argv[1:] == ["boom"]:
        raise ValueError("boom")
    sys.exit(int(sys.argv[1]) if sys.argv[1:2] and sys.argv[1].isdigit() else 0)

main()
"""


def ptrace_allowed():
    """Whether a process may be traced by its parent (PTRACE_TRACEME), as the
    client does to follow a launcher: seccomp or Yama may forbid it. The
    client follows launchers on Linux and macOS."""
    if sys.platform not in ("linux", "darwin"):
        return False
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    pid = os.fork()
    if pid == 0:
        os._exit(0 if libc.ptrace(0, 0, None, None) == 0 else 1)
    return os.waitpid(pid, 0)[1] == 0


PTRACE = ptrace_allowed()
needs_ptrace = unittest.skipUnless(PTRACE, "ptrace is not allowed here"
                                   if sys.platform in ("linux", "darwin")
                                   else "launchers are followed on Linux and macOS only")


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Launchers(Matrix):
    """A launcher sets up an env, then execs the interpreter on the tool:
    gcloud, a shell script, on a script; az, a Python script, on -m module.
    The launcher runs natively once per call, and what it execs is served."""

    @classmethod
    def setUpClass(cls):
        python = shutil.which("python3", path=os.environ["HS_PATH"])
        cls.root = d = tempfile.mkdtemp(dir=ROOT, prefix="launchers-")
        files = {"bin/sh-tool": SH_LAUNCHER.format(python=python),
                 "bin/py-tool": PY_LAUNCHER.format(python=python),
                 "bin/cat-tool": "#!/bin/sh\necho launcher >> \"$TOOL_LOG\"\nexec cat \"$@\"\n",
                 "lib/tool.py": TOOL_PY, "lib/third/thirdmod.py": "VALUE = 'third'\n",
                 "lib/lmod/__init__.py": "", "lib/lmod/__main__.py": LMOD_MAIN,
                 "lib/lmod/lazy.py": "VALUE = 'lazy'\n", "shadow/json.py": "VALUE = 'cwd json'\n"}
        for name, text in files.items():
            path = os.path.join(d, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
            if name.startswith("bin/"):
                os.chmod(path, 0o755)
        cls.env = {"PATH": os.path.join(d, "bin") + ":" + BASE_ENV["PATH"],
                   "TOOL_LOG": os.path.join(d, "log")}
        cls.work = os.path.join(d, "work")
        os.makedirs(cls.work)

    @classmethod
    def tearDownClass(cls):
        for tool in ("sh-tool", "py-tool"):
            stop(tool, cls.env)

    def warm(self, tool, timeout=30.0, native_ok=False):
        deadline = time.monotonic() + timeout
        while run("hs", [tool], cwd=self.work, env=self.env).returncode == EXIT_NOT_FAST:
            if time.monotonic() > deadline:
                raise RuntimeError(f"no daemon for {tool}")
            time.sleep(0.1)
        if MACOS and not native_ok and status(tool, self.env) == "down":
            # Run natively, start to end: after a framework Python's exec of
            # its Python.app, macOS stops reporting the execs of the process
            # the client traces.
            self.skipTest(f"{tool}: this Python drops the trace (macOS): it runs natively")

    def log(self):
        try:
            with open(self.env["TOOL_LOG"]) as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    @needs_ptrace
    def test_shell_launcher_on_a_script(self):
        self.warm("sh-tool")
        for argv in (["sh-tool", "a b", "--x"], ["sh-tool", "fail"]):
            with self.subTest(argv=argv):
                code, out, err = self.assertSame(argv, cwd=self.work, env=self.env)
                self.assertEqual(err, b"")
        _, out, _ = self.assertSame(["sh-tool", "a b"], cwd=self.work, env=self.env, umask=0o077)
        self.assertEqual(out, b"['%s/bin/../lib/tool.py', 'a b'] sh 1 third True ['-s'] 63\n" % self.root.encode())

    @needs_ptrace
    def test_python_launcher_on_a_module(self):
        self.warm("py-tool")
        for argv in (["py-tool", "x", "y"], ["py-tool", "7"], ["py-tool", "boom"]):
            with self.subTest(argv=argv):
                self.assertSame(argv, cwd=self.work, env=self.env)
        _, out, _ = self.assertSame(["py-tool", "x"], cwd=self.work, env=self.env)
        self.assertEqual(out, b"True __main__.py ['x'] __main__ lmod.__main__ True py lazy"
                              b" stdlib json ['-m', 'lmod']\n")

    @needs_ptrace
    def test_launcher_and_tool_run_once_per_call(self):
        for tool in ("sh-tool", "py-tool"):
            with self.subTest(tool=tool):
                self.warm(tool, native_ok=True)
                before = len(self.log())
                for _ in range(3):
                    self.assertEqual(run("hs", [tool], cwd=self.work, env=self.env).returncode, 0)
                self.assertEqual(self.log()[before:], ["launcher", "tool"] * 3 if tool == "sh-tool"
                                 else ["launcher"] * 3)

    @needs_ptrace
    def test_learns_imports_from_the_entries_the_tool_adds(self):
        self.warm("sh-tool")
        deadline = time.monotonic() + 30
        while True:
            run("hs", ["sh-tool"], cwd=self.work, env=self.env)
            text = status("sh-tool", self.env)
            if int(status_field(text, "learned") or 0) >= 1 or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        self.assertGreaterEqual(int(status_field(text, "learned")), 1, text)
        self.assertEqual(status_field(text, "blacklisted"), "0", text)

    @needs_ptrace
    def test_cwd_module_shadows_a_preloaded_one(self):
        self.warm("py-tool")
        shadow = os.path.join(self.root, "shadow")
        _, out, _ = self.assertSame(["py-tool", "x"], cwd=shadow, env=self.env, require_fast=False)
        self.assertIn(b"cwd json", out)
        self.assertEqual(run("hs", ["py-tool", "x"], cwd=shadow, env=self.env).returncode,
                         EXIT_NOT_FAST)

    def scripted(self, script, wrap, **extra):
        """script run by sh, natively or under `exec -t sh-tool` / `activate sh-tool`."""
        hs = shlex.quote(HS_BIN)
        cmd = {"native": ["sh", "-c", script],
               "exec": [HS_BIN, "exec", "-t", "sh-tool", "sh", "-c", script],
               "activate": ["sh", "-c", f'eval "$({hs} activate sh-tool)"; {script}']}[wrap]
        return subprocess.run(cmd, capture_output=True, cwd=self.work, timeout=60,
                              env={**BASE_ENV, **self.env, **extra})

    @needs_ptrace
    def test_exec_and_activate_link_a_launcher(self):
        # Named in a script, a launcher gets its link like a script does:
        # every call goes through speedrunner, which follows it.
        self.warm("sh-tool")
        script = 'sh-tool "a b"; sh-tool fail; echo "rc=$?"'
        native = outcome(self.scripted(script, "native"))
        for wrap in ("exec", "activate"):
            with self.subTest(wrap=wrap):
                p = self.scripted(script, wrap, SPEEDRUNNER_REQUIRE_FAST="1")
                self.assertEqual(native, outcome(p))
                where = self.scripted("command -v sh-tool", wrap).stdout.decode()
                self.assertIn("/speedrunner/bin-", where)

    @needs_ptrace
    def test_exec_starts_a_known_launcher_daemon(self):
        # Once a run has shown what the launcher execs, naming it starts
        # that daemon, as for a script.
        self.warm("sh-tool")
        stop("sh-tool", self.env)
        self.assertEqual(status("sh-tool", self.env), "down")
        p = self.scripted("true", "exec")
        self.assertEqual((p.returncode, p.stderr), (0, b""))
        deadline = time.monotonic() + 30
        while not status("sh-tool", self.env).startswith("ready"):
            self.assertLess(time.monotonic(), deadline, status("sh-tool", self.env))
            time.sleep(0.1)

    def test_forced_native_is_not_followed(self):
        # Never traced, whatever the launcher execs: its whole preamble runs, once.
        env = {**BASE_ENV, **self.env, **in_private_daemon_env(), "SPEEDRUNNER_REQUIRE_FAST": "1"}
        before = len(self.log())
        native = outcome(run("native", ["sh-tool", "x"], cwd=self.work, env=self.env))
        one_run = len(self.log()) - before
        for argv, extra in (([HS_BIN, "run", "--native", "sh-tool", "--", "x"], {}),
                            ([HS_BIN, "run", "sh-tool", "--", "x"], {"SPEEDRUNNER_NATIVE": "1"})):
            before = len(self.log())
            p = subprocess.run(argv, capture_output=True, cwd=self.work, timeout=60, env={**env, **extra})
            self.assertEqual(native, outcome(p))
            self.assertEqual(len(self.log()) - before, one_run)
        self.assertEqual(status("sh-tool", env), "down")

    def test_launcher_that_execs_no_python_runs_natively(self):
        self.assertSame(["cat-tool"], input=b"some input\n", cwd=self.work, env=self.env,
                        require_fast=False)
        self.assertSame(["cat-tool", "/nonexistent"], cwd=self.work, env=self.env,
                        require_fast=False)

    @unittest.skipIf(PTRACE, "ptrace is allowed here")
    def test_without_ptrace_runs_natively_and_once(self):
        for tool in ("sh-tool", "py-tool"):
            with self.subTest(tool=tool):
                before = len(self.log())
                self.assertSame([tool, "x"], cwd=self.work, env=self.env, require_fast=False)
                self.assertEqual(len(self.log()) - before, 4 if tool == "sh-tool" else 2)
                self.assertEqual(run("hs", [tool], cwd=self.work, env=self.env).returncode,
                                 EXIT_NOT_FAST)

    @needs_ptrace
    def test_status_stop_and_prewarm(self):
        self.warm("sh-tool")
        self.assertTrue(status("sh-tool", self.env).startswith("ready"))
        stop("sh-tool", self.env)
        self.assertEqual(status("sh-tool", self.env), "down")
        subprocess.run([HS_BIN, "prewarm", "sh-tool"], env={**BASE_ENV, **self.env}, check=True)
        wait_ready("sh-tool", self.env)
        self.assertEqual(run("hs", ["sh-tool", "q"], cwd=self.work, env=self.env).returncode, 0)


class Teardown(Matrix):
    def test_unclosed_file_flushed_before_exit(self):
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                path = os.path.join(ROOT, f"unclosed-{mode}")
                p = run(mode, ["hstest", "unclosed", path])
                self.assertEqual(p.returncode, 0)
                with open(path) as f:
                    self.assertEqual(len(f.read()), 100000)

    def assertMarksOnReturn(self, argv, *after, env=None):
        """The marker is there when the client returns, and the rest as natively."""
        seen = []
        for mode in ("native", "hs"):
            with tempfile.TemporaryDirectory(dir=ROOT) as d:
                path = os.path.join(d, "mark")
                p = run(mode, [*argv, path, *after], env=env)
                self.assertTrue(os.path.exists(path), f"{mode}: no marker on return")
                seen.append(outcome(p))
        self.assertEqual(*seen)
        return seen[0]

    def test_object_of_an_unhashable_class(self):
        self.assertSame(["hstest", "odd-metaclass"])

    def test_del_of_a_module_global(self):
        rc, out, _ = self.assertMarksOnReturn(["hstest", "del-global"])
        self.assertEqual((rc, out), (0, b"ok\n"))

    def test_del_of_garbage_in_a_cycle(self):
        self.assertMarksOnReturn(["hstest", "del-garbage"])

    def test_del_keeps_the_exit_code(self):
        rc, _, _ = self.assertMarksOnReturn(["hstest", "del-exit"], "3")
        self.assertEqual(rc, 3)

    def test_finally_of_a_suspended_generator(self):
        self.assertMarksOnReturn(["hstest", "suspended"])

    def test_object_with_a_del_made_at_import(self):
        # Not preloaded: each call has its own, and its own temporary file.
        self.addCleanup(lambda: HS_BIN and stop("hslate"))
        if HS_BIN:
            served_soon(["hslate", os.path.join(ROOT, "stamp-warm")])
        for _ in range(3):
            _, out, _ = self.assertMarksOnReturn(["hslate"])
            self.assertEqual(out, b"temporary file there\n")

    def test_what_del_prints_reaches_the_client(self):
        def scrub(p):
            return p.returncode, p.stdout, re.sub(rb"0x[0-9a-f]+", b"0x", p.stderr)

        native = scrub(run("native", ["hstest", "del-noisy"]))
        self.assertIn(b"from __del__", native[1])
        self.assertIn(b"ValueError: in __del__", native[2])
        self.assertEqual(native, scrub(run("hs", ["hstest", "del-noisy"])))

    def test_file_opened_at_import_written_in_the_run(self):
        log = os.path.join(ROOT, "at-import.log")
        env = {"HSLOG_FILE": log}
        self.addCleanup(lambda: HS_BIN and stop("hslog", env))
        if HS_BIN:
            served_soon(["hslog", "warm"], env=env)
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                self.assertEqual(run(mode, ["hslog", f"line-{mode}"], env=env).returncode, 0)
                with open(log) as f:
                    self.assertIn(f"line-{mode}\n", f.read())

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_tool_without_finalizers_returns_before_teardown(self):
        path = os.path.join(ROOT, "unseen-teardown")
        self.assertEqual(run("hs", ["hstest", "unseen-teardown", path]).returncode, 0)
        self.assertFalse(os.path.exists(path), "the client waited for teardown")
        deadline = time.monotonic() + 10
        while not os.path.exists(path):
            self.assertLess(time.monotonic(), deadline, "teardown never got there")
            time.sleep(0.05)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class FailOpen(Matrix):
    """Inputs the preloaded state cannot honour must fall back to native."""

    def check_falls_back(self, argv, env):
        native = outcome(run("native", argv, env=env))
        self.assertEqual(native, outcome(run("hs", argv, env=env, require_fast=False)))
        self.assertEqual(run("hs", argv, env=env).returncode, EXIT_NOT_FAST)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_child_missing_its_go_says_so(self):
        check_child_says_it_missed_its_go(self, "hstest")

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_signal_before_the_go(self):
        check_child_dies_of_a_signal_before_its_go(self, "hstest")

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_client_runs_natively_when_the_child_never_started(self):
        # In place of the daemon: a child that gets its go, then fails.
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        wait_ready("hstest", env)
        path = tool_socket("hstest", env)
        stop("hstest", env)

        def fake_daemon(listener):
            while True:
                try:
                    conn = listener.accept()[0]
                except OSError:
                    return
                with conn:
                    conn.sendall(b"H" + struct.pack("<I", 0))
                    _, fds, _, _ = socket.recv_fds(conn, 1 << 20, 8)
                    for fd in fds:
                        os.close(fd)
                    conn.sendall(b"R" + struct.pack("<Ii", 4, os.getpid()))
                    self.assertEqual(conn.recv(1), b"G")
                    conn.sendall(b"N" + struct.pack("<I", 5) + b"never")

        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(path)
            listener.listen(8)
            threading.Thread(target=fake_daemon, args=(listener,), daemon=True).start()
            argv = ["hstest", "argv", "x"]
            self.assertEqual(outcome(run("native", argv, env=env)),
                             outcome(run("hs", argv, env=env, require_fast=False)))
            self.assertEqual(run("hs", argv, env=env).returncode, EXIT_NOT_FAST)

    def test_import_time_env_differs(self):
        self.check_falls_back(["hstest", "env", "HSTEST_IMPORTED"], {"HSTEST_IMPORTED": "0"})

    def test_startup_env_differs(self):
        self.check_falls_back(["hstest", "argv"], {"PYTHONWARNINGS": "ignore"})

    def test_env_read_at_import_differs(self):
        # hstest.cli keeps os.environ.get("HSTEST_FROZEN") from import time.
        self.check_falls_back(["hstest", "frozen"], {"HSTEST_FROZEN": "client value"})


AWS_REFERENCE = [
    (["aws", "--version"], None),
    (["aws", "configure", "list"], None),
    (SKELETON, blank_strings),
]


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Preload(Matrix):
    """Each preload level, alone or combined, must match native output."""

    def daemon(self, tool, levels, **extra):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache"),
               "SPEEDRUNNER_PRELOAD": levels, **extra}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        wait_ready(tool, env)
        return env

    def learn(self, tool, env, argvs, timeout=30.0, required=True):
        """Run argvs through speedrunner; wait until the daemon has applied what
        it learned. Not required: there may be nothing new to learn, as when
        the interpreter already had every module the run imports (3.9)."""
        first = status(tool, env)
        gen, pid = int(status_field(first, "gen")), status_field(first, "pid")
        for argv in argvs:
            run("hs", argv, env=env)
        deadline = time.monotonic() + (timeout if required else 3.0)
        while True:
            text = status(tool, env)
            if int(status_field(text, "gen") or -1) > gen:
                return
            # A module that misbehaved in the daemon despite the probe makes it
            # exit, by design: the next call starts one, which reloads what was
            # learned (the culprit blacklisted).
            if text == "down":
                run("hs", argvs[0], env=env)
            elif status_field(text, "pid") not in (None, pid) and int(status_field(text, "learned") or 0):
                return
            if time.monotonic() > deadline:
                if required:
                    self.fail(f"nothing applied: {text!r}")
                return
            time.sleep(0.1)

    def test_aws_levels_match_native(self):
        # Level 3 needs botocore in the daemon, which only levels 1 and 2 import.
        for levels, learns in [("1", False), ("2", True), ("3", False),
                               ("1,2", True), ("1,3", True), ("1,2,3", True)]:
            with self.subTest(levels=levels):
                env = self.daemon("aws", levels)
                try:
                    if learns:
                        self.learn("aws", env, [argv for argv, _ in AWS_REFERENCE])
                    for argv, normalize in AWS_REFERENCE:
                        self.assertSame(argv, normalize=normalize, env=env)
                    self.assertSame(["aws", "configure", "list"],
                                    env={**env, "AWS_DEFAULT_REGION": "eu-west-3"})
                finally:
                    stop("aws", env)

    def test_memo_rereads_modified_data_file(self):
        # A copy of the sts model, found first through AWS_DATA_PATH.
        aws = shutil.which("aws", path=BASE_ENV["PATH"])
        # botocore vendored by awscli, or a package of its own (pip's choice
        # for Python 3.9), whose models may be gzipped: the copy is plain.
        site = os.path.join(os.path.dirname(os.path.dirname(aws)), "lib", "python3*", "site-packages")
        model = os.path.join("data", "sts", "2011-06-15", "service-2.json")
        [model] = [p for d in ("awscli/botocore", "botocore") for ext in ("", ".gz")
                   for p in glob.glob(os.path.join(site, d, model + ext))][:1]
        data_path = tempfile.mkdtemp(dir=ROOT)
        copy = os.path.join(data_path, "sts", "2011-06-15", "service-2.json")
        os.makedirs(os.path.dirname(copy))
        with (gzip.open if model.endswith(".gz") else open)(model, "rb") as f:
            original = f.read()

        def write_model(tag):
            with open(copy, "r+b") as f:  # same inode, same size: only mtime tells
                f.write(original.replace(b'"RoleArn"', b'"RoleAr' + tag + b'"'))
                f.truncate()

        with open(copy, "wb"):
            pass
        write_model(b"1")
        argv = ["aws", "sts", "assume-role", "--generate-cli-skeleton"]
        env = self.daemon("aws", "1,2,3", AWS_DATA_PATH=data_path)
        try:
            self.learn("aws", env, [argv])
            self.assertIn(copy, self.cache(env, "aws")["files"])
            _, out, _ = self.assertSame(argv, env=env)
            self.assertIn(b'"RoleAr1"', out)
            write_model(b"2")
            _, out, _ = self.assertSame(argv, env=env)
            self.assertIn(b'"RoleAr2"', out)
        finally:
            stop("aws", env)

    def cache(self, env, tool):
        [path] = glob.glob(os.path.join(env["XDG_CACHE_HOME"], "speedrunner", f"{tool}-*.json"))
        with open(path) as f:
            return json.load(f)

    def test_learned_imports_vetted(self):
        env = self.daemon("hstest", "1,2")
        try:
            self.learn("hstest", env, [["hstest", "lazy"]])
            self.assertTrue(status("hstest", env).startswith("ready"))
            cache = self.cache(env, "hstest")
            self.assertIn("hstest.lazy_plain", cache["modules"])
            self.assertIn("hstest.lazy_env", cache["modules"])
            self.assertIn("hstest.lazy_thread", cache["blacklist"])
            self.assertIn("hstest.lazy_print", cache["blacklist"])
            self.assertIn("hstest.lazy_argv", cache["blacklist"])
            self.assertIn("hstest.lazy_finalizer", cache["blacklist"])
            self.assertIn("hstest.pyparsing.core", cache["modules"])
            # Refused for importing lazy_argv, which the probe did not run again.
            self.assertTrue(cache["blacklist"].get("hstest.lazy_uses_argv", "").startswith(
                "imports hstest.lazy_argv"), cache["blacklist"])
            self.assertSame(["hstest", "lazy"], env=env)
            # lazy_env keeps HSTEST_LAZY from import time: another value runs natively.
            lazy = {**env, "HSTEST_LAZY": "client value"}
            native = outcome(run("native", ["hstest", "lazy"], env=lazy))
            self.assertEqual(native, outcome(run("hs", ["hstest", "lazy"], env=lazy,
                                                 require_fast=False)))
            self.assertEqual(run("hs", ["hstest", "lazy"], env=lazy).returncode, EXIT_NOT_FAST)
        finally:
            stop("hstest", env)

    def test_module_from_a_runtime_import_hook(self):
        env = self.daemon("hstest", "1,2")
        try:
            self.learn("hstest", env, [["hstest", "hook"]], required=False)
            _, out, _ = self.assertSame(["hstest", "hook"], env=env)
            self.assertEqual(out, b"hooked\n")
        finally:
            stop("hstest", env)

    def test_tool_called_by_its_path(self):
        # The daemon's files go to the cache, named after the tool, not next
        # to the script: its directory is in the fingerprint, and a file
        # written there made every daemon stale as soon as it had learned.
        path = shutil.which("hstest", path=BASE_ENV["PATH"])
        before = sorted(os.listdir(os.path.dirname(path)))
        env = self.daemon(path, "1,2")
        try:
            self.learn(path, env, [[path, "lazy"]])
            self.assertIn("hstest.lazy_plain", self.cache(env, "hstest")["modules"])
            self.assertEqual(sorted(os.listdir(os.path.dirname(path))), before)
            self.assertSame([path, "lazy"], env=env)
            self.assertSame([path, "lazy"], env=env)
        finally:
            stop(path, env)

    def test_project_modules_are_not_learned(self):
        # As pytest in one project, then another: a module the tool imports
        # from the project, by a name every project may use, is the
        # project's, never the tool's to preload.
        env = self.daemon("hstest", "1,2")
        projects = []
        for name in ("alpha", "beta"):
            d = tempfile.mkdtemp(dir=ROOT)
            with open(os.path.join(d, "projmod.py"), "w") as f:
                f.write(f"NAME = {name!r}\n")
            projects.append((d, name))
        try:
            for _ in range(2):
                run("hs", ["hstest", "project"], cwd=projects[0][0], env=env)
            self.learn("hstest", env, [["hstest", "lazy"]])
            for d, name in projects:
                with self.subTest(project=name):
                    _, out, _ = self.assertSame(["hstest", "project"], cwd=d, env=env)
                    self.assertEqual(out, name.encode() + b"\n")
            self.assertNotIn("projmod", self.cache(env, "hstest")["modules"])
        finally:
            stop("hstest", env)

    def test_learned_set_survives_restart(self):
        env = self.daemon("hstest", "1,2")
        try:
            self.learn("hstest", env, [["hstest", "lazy"]])
            learned = int(status_field(status("hstest", env), "learned"))
            self.assertGreater(learned, 0)
            stop("hstest", env)
            text = wait_ready("hstest", env)
            self.assertEqual(int(status_field(text, "learned")), learned)
            self.assertEqual(status_field(text, "gen"), "0")
        finally:
            stop("hstest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
@unittest.skipUnless(MACOS, "CoreFoundation is macOS's")
class CoreFoundation(Matrix):
    """macOS: once a process used CoreFoundation, a forked child that uses it
    again is killed (Apple Silicon: urllib's proxy lookup; both: after a run
    loop). The daemon must never fork after using it: imports that do are
    skipped (level 1) or blacklisted (level 2), and each run that uses it,
    in the child, is served as natively."""

    daemon = Preload.daemon
    learn = Preload.learn
    cache = Preload.cache

    def test_tool_using_it_at_run_time_is_served(self):
        for _ in range(3):
            _, out, _ = self.assertSame(["hstest", "proxies"])
            self.assertEqual(out, b"proxies True\n")

    def test_entry_module_using_it_is_skipped(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(stop, "hscf", env)
        run("hs", ["hscf"], env=env, require_fast=False)  # starts the daemon, which skips cfmain
        served_soon(["hscf"], env=env)
        for _ in range(3):
            _, out, _ = self.assertSame(["hscf"], env=env)
            self.assertEqual(out, b"cf main True\n")
        [skip] = glob.glob(os.path.join(env["XDG_CACHE_HOME"], "speedrunner", "hscf.skip-*.json"))
        with open(skip) as f:
            self.assertEqual(json.load(f), {"hstest.cfmain": "uses CoreFoundation at import"})

    def test_learned_modules_using_it_are_blacklisted(self):
        env = self.daemon("hstest", "1,2")
        try:
            self.learn("hstest", env, [["hstest", "lazy-cf"]])
            self.assertTrue(status("hstest", env).startswith("ready"))
            cache = self.cache(env, "hstest")
            self.assertIn("hstest.lazy_plain", cache["modules"])
            # Found by the canary alone: no thread to show for it.
            self.assertEqual(cache["blacklist"].get("hstest.cf"), "uses CoreFoundation at import")
            self.assertIn(cache["blacklist"].get("hstest.lazy_proxy"),
                          ("starts a thread at import", "uses CoreFoundation at import"))
            for _ in range(3):
                _, out, _ = self.assertSame(["hstest", "lazy-cf"], env=env)
                self.assertEqual(out, b"proxies True\n")
        finally:
            stop("hstest", env)


def private_daemon(tool):
    """A daemon of our own, in a fresh runtime dir: tests may kill it."""
    root = tempfile.mkdtemp(dir=ROOT)
    env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
           "XDG_CACHE_HOME": os.path.join(root, "cache")}
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    wait_ready(tool, env)
    return root, env


def read_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(Matrix):
    """Brief §5 « Pannes » and rule n°1: whatever dies, a command never runs twice."""

    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "hstest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_stopped_mid_run_lets_a_new_daemon_start(self):
        # A stopping daemon waits for its runs, but not with the startup
        # lock: its successor starts at once, not once a long run is over.
        root, env = private_daemon("hstest")
        try:
            daemon = int(status_field(status("hstest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker, seconds="8")
            stop("hstest", env)
            self.assertEqual(run("hs", ["hstest", "argv"], env=env, require_fast=False).returncode, 0)
            text = wait_ready("hstest", env, timeout=6)
            self.assertNotEqual(int(status_field(text, "pid")), daemon)
            self.assertIsNone(p.poll())  # the long run is still going
            self.assertEqual(run("hs", ["hstest", "argv"], env=env).returncode, 0)
            out, _ = p.communicate(timeout=30)
            self.assertEqual((p.returncode, out), (0, b"done\n"))
            self.assertEqual(read_lines(marker), ["start", "done"])
        finally:
            stop("hstest", env)

    def test_daemon_killed_during_teardown(self):
        # No X when a __del__ has to run: the exit code does not depend on the W.
        root, env = private_daemon("hstest")
        try:
            daemon = int(status_field(status("hstest", env), "pid"))
            marker = os.path.join(root, "marker")
            p = subprocess.Popen([HS_BIN, "run", "hstest", "--", "del-exit", marker, "3"],
                                 env={**BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.stdout.readline(), b"ready\n")
            os.kill(daemon, signal.SIGKILL)
            out, err = p.communicate(timeout=30)
            self.assertEqual((p.returncode, out), (3, b""))
            self.assertNotIn(b"lost track", err)
            self.assertTrue(os.path.exists(marker))
        finally:
            stop("hstest", env)

    def test_daemon_killed_mid_run(self):
        root, env = private_daemon("hstest")
        try:
            daemon = int(status_field(status("hstest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker)
            os.kill(daemon, signal.SIGKILL)
            out, err = p.communicate(timeout=30)
            # The child finishes and reports its own exit code.
            self.assertEqual((p.returncode, out), (0, b"done\n"))
            self.assertEqual(read_lines(marker), ["start", "done"])
            # The next call runs natively and restarts the daemon.
            q = run("hs", ["hstest", "argv"], env=env, require_fast=False)
            self.assertEqual(outcome(q), outcome(run("native", ["hstest", "argv"])))
            text = wait_ready("hstest", env)
            self.assertNotEqual(int(status_field(text, "pid")), daemon)
            self.assertEqual(run("hs", ["hstest", "argv"], env=env).returncode, 0)
        finally:
            stop("hstest", env)

    def test_child_lost_after_daemon_death_is_not_rerun(self):
        root, env = private_daemon("hstest")
        try:
            daemon = int(status_field(status("hstest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, child = self.start_hold(env, marker, seconds="5")
            os.kill(daemon, signal.SIGKILL)
            os.kill(child, signal.SIGKILL)
            out, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 125)
            self.assertIn(b"not re-running it", err)
            self.assertEqual(out, b"")
            time.sleep(0.5)
            self.assertEqual(read_lines(marker), ["start"])
        finally:
            stop("hstest", env)

    def test_client_killed_mid_run(self):
        root, env = private_daemon("hstest")
        try:
            check_client_killed(self, "hstest", self.start_hold, root, env)
            self.assertIn(b"client gone, SIGKILL", open_log(tool_socket("hstest", env)))
        finally:
            stop("hstest", env)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_client_still_there_is_not_taken_for_gone(self):
        # Readable, and not at end of file: the tool goes on.
        mark = os.path.join(ROOT, "client-still-there")
        sock, _ = ask_to_run("hstest", "hold", "0.3", mark)
        with sock:
            sock.sendall(b"Gx")
            self.assertEqual(read_frame(sock), (b"X", struct.pack("<i", 0)))
        self.assertEqual(read_lines(mark), ["start", "done"])

    def test_stale_after_module_edit(self):
        # A tool whose module is edited in place, as an upgrade or a
        # developer would: same size, same inode, same directory mtime.
        root = tempfile.mkdtemp(dir=ROOT)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        python = os.path.join(os.path.dirname(shutil.which("hstest", path=BASE_ENV["PATH"])),
                              "python3")
        tool = os.path.join(bindir, "hsedit")
        with open(tool, "w") as f:
            f.write(f"#!{python}\nimport hsedit_lib\nhsedit_lib.main()\n")
        os.chmod(tool, 0o755)
        lib = os.path.join(bindir, "hsedit_lib.py")

        def write_lib(version, mtime):
            with open(lib, "r+" if os.path.exists(lib) else "w") as f:
                f.write(f"def main():\n    print('{version}')\n")
            os.utime(lib, (mtime, mtime))  # pyc headers keep whole seconds

        write_lib("v1", time.time() - 100)
        env = {"PATH": f"{bindir}:{BASE_ENV['PATH']}",
               "XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        try:
            wait_ready("hsedit", env)
            daemon = int(status_field(status("hsedit", env), "pid"))
            self.assertEqual(run("hs", ["hsedit"], env=env).stdout, b"v1\n")
            write_lib("v2", time.time())
            self.assertEqual(run("hs", ["hsedit"], env=env, require_fast=False).stdout, b"v2\n")
            # Never the stale daemon: natively while it restarts, or its
            # successor, on a machine fast enough to have it ready already.
            p = run("hs", ["hsedit"], env=env)
            self.assertIn((p.returncode, p.stdout), [(EXIT_NOT_FAST, b""), (0, b"v2\n")])
            deadline = time.monotonic() + 30
            while True:  # restarted in the background, then fast again
                text = status("hsedit", env)
                if text.startswith("ready") and int(status_field(text, "pid")) != daemon:
                    break
                self.assertLess(time.monotonic(), deadline, text)
                time.sleep(0.1)
            self.assertEqual(run("hs", ["hsedit"], env=env).stdout, b"v2\n")
        finally:
            stop("hsedit", env)


class ImportTimeReadChecks:
    """What module-level code reads of the filesystem is frozen in the daemon:
    a change, or a client elsewhere, must never be served from the old value.
    For a runtime, a subclass gives the tool's module for each case."""
    SHEBANG = None  # the tool's first line
    LIB = "hsread_lib.py"
    LAZY = "hsread_lazy.py"
    TAUGHT_LAZILY = False  # teach() has already had a run load the lazy module
    SOURCES = {}

    def tool(self, kind):
        """A private tool whose module is the runtime's source for `kind`."""
        root = tempfile.mkdtemp(dir=ROOT)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        script = os.path.join(bindir, "hsread")
        with open(script, "w") as f:
            f.write(self.script())
        os.chmod(script, 0o755)
        with open(os.path.join(bindir, self.LIB), "w") as f:
            f.write(self.SOURCES[kind])
        env = {"PATH": f"{bindir}:{BASE_ENV['PATH']}",
               "XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(stop, "hsread", env)
        return root, env

    def script(self):
        raise NotImplementedError

    def teach(self, env, cwd):
        """What the runtime needs before the daemon holds the module's reads."""

    def start(self, env, cwd=None):
        """A daemon started from cwd, as a client there would: served at once."""
        subprocess.run([HS_BIN, "prewarm", "hsread"], env={**BASE_ENV, **env}, cwd=cwd, check=True)
        # From cwd too: a Node without code cache leaves its first daemon to note
        # that, and the one that serves is the next one started.
        wait_ready("hsread", env, cwd=cwd)
        self.teach(env, cwd)
        return int(status_field(status("hsread", env), "pid"))

    def directory(self, root, name, config=None):
        path = os.path.join(root, name)
        os.makedirs(path)
        if config is not None:
            with open(os.path.join(path, "local.cfg"), "w") as f:
                f.write(config)
        return path

    def check_refreshes(self, env, expected, daemon, cwd=None):
        """The new value at once, never the old one; then a new daemon serves it."""
        p = run("hs", ["hsread"], env=env, cwd=cwd, require_fast=False)
        self.assertEqual(outcome(run("native", ["hsread"], env=env, cwd=cwd)), outcome(p))
        self.assertEqual(p.stdout, expected)
        p = run("hs", ["hsread"], env=env, cwd=cwd)
        self.assertIn((p.returncode, p.stdout), [(EXIT_NOT_FAST, b""), (0, expected)])
        deadline = time.monotonic() + 30
        while True:
            subprocess.run([HS_BIN, "prewarm", "hsread"], env={**BASE_ENV, **env}, cwd=cwd,
                           capture_output=True)
            text = status("hsread", env)
            if text.startswith("ready") and int(status_field(text, "pid")) != daemon:
                break
            self.assertLess(time.monotonic(), deadline, text)
            time.sleep(0.1)
        self.teach(env, cwd)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=cwd).stdout, expected)

    def with_config(self, kind, content):
        root, env = self.tool(kind)
        env["HS_CFG"] = os.path.join(root, "app.cfg")
        if content is not None:
            with open(env["HS_CFG"], "w") as f:
                f.write(content)
        return root, env

    def test_config_file_changed(self):
        _, env = self.with_config("read", "one")
        daemon = self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"one\n")
        with open(env["HS_CFG"], "w") as f:
            f.write("two")
        self.check_refreshes(env, b"two\n", daemon)

    def test_config_file_replaced(self):
        # As an editor saves: a new file renamed over the old one.
        _, env = self.with_config("read", "one")
        daemon = self.start(env)
        with open(env["HS_CFG"] + ".new", "w") as f:
            f.write("two")
        os.rename(env["HS_CFG"] + ".new", env["HS_CFG"])
        self.check_refreshes(env, b"two\n", daemon)

    def test_config_file_created(self):
        _, env = self.with_config("read_if_there", None)
        daemon = self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"none\n")
        with open(env["HS_CFG"], "w") as f:
            f.write("made")
        self.check_refreshes(env, b"made\n", daemon)

    def test_config_file_deleted(self):
        _, env = self.with_config("read_if_there", "here")
        daemon = self.start(env)
        os.unlink(env["HS_CFG"])
        self.check_refreshes(env, b"none\n", daemon)

    def test_stat_read_at_import(self):
        _, env = self.with_config("size", "one")
        daemon = self.start(env)
        with open(env["HS_CFG"], "w") as f:
            f.write("longer")
        self.check_refreshes(env, b"6\n", daemon)

    def test_listing_changed(self):
        root, env = self.tool("list")
        env["HS_DIR"] = self.directory(root, "plugins")
        daemon = self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"0\n")
        with open(os.path.join(env["HS_DIR"], "new"), "w"):
            pass
        self.check_refreshes(env, b"1\n", daemon)

    def test_probed_directory_is_only_a_directory(self):
        # Its mtime moves with its entries; the module only asked what it is.
        root, env = self.tool("is_dir")
        env["HS_DIR"] = self.directory(root, "busy")
        daemon = self.start(env)
        entry = os.path.join(env["HS_DIR"], "entry")
        with open(entry, "w"):
            pass
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"yes\n")
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)
        os.unlink(entry)
        os.rmdir(env["HS_DIR"])
        self.check_refreshes(env, b"no\n", daemon)

    def test_relative_file_elsewhere(self):
        root, env = self.tool("relative_if_there")
        here = self.directory(root, "here", "here")
        daemon = self.start(env, here)
        # Read from the client's directory, as natively, not the daemon's own.
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, b"here\n")
        for cwd, value in ((self.directory(root, "other", "other"), b"other\n"),
                           (self.directory(root, "bare"), b"none\n")):
            native = run("native", ["hsread"], env=env, cwd=cwd)
            self.assertEqual(native.stdout, value)
            self.assertEqual(outcome(native),
                             outcome(run("hs", ["hsread"], env=env, cwd=cwd, require_fast=False)))
            self.assertEqual(run("hs", ["hsread"], env=env, cwd=cwd).returncode, EXIT_NOT_FAST)
        # A client elsewhere does not cost the others their daemon.
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, b"here\n")

    def test_relative_file_changed_in_place(self):
        root, env = self.tool("relative")
        here = self.directory(root, "here", "one")
        daemon = self.start(env, here)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, b"one\n")
        with open(os.path.join(here, "local.cfg"), "w") as f:
            f.write("two")
        self.check_refreshes(env, b"two\n", daemon, cwd=here)

    def test_relative_file_absent_everywhere_stays_served(self):
        root, env = self.tool("relative_exists")
        self.start(env, self.directory(root, "here"))
        other = self.directory(root, "other")
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).stdout, b"no\n")

    def test_cwd_read_at_import(self):
        root, env = self.tool("cwd")
        here = os.path.realpath(self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        self.start(env, here)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, f"{here}\n".encode())
        self.assertEqual(outcome(run("native", ["hsread"], env=env, cwd=other)),
                         outcome(run("hs", ["hsread"], env=env, cwd=other, require_fast=False)))
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).returncode, EXIT_NOT_FAST)

    def wait_new_daemon(self, env, cwd, daemon):
        deadline = time.monotonic() + 30
        while True:
            run("hs", ["hsread"], env=env, cwd=cwd, require_fast=False)
            text = status("hsread", env)
            if text.startswith("ready") and int(status_field(text, "pid")) != daemon:
                break
            self.assertLess(time.monotonic(), deadline, text)
            time.sleep(0.1)
        self.teach(env, cwd)

    def test_daemon_follows_a_client_that_stays_elsewhere(self):
        root, env = self.tool("cwd")
        env["SPEEDRUNNER_FOLLOW_AFTER"] = "1"
        here = os.path.realpath(self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        daemon = self.start(env, here)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, f"{here}\n".encode())
        time.sleep(1.3)
        # Right from the first call, as natively.
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other, require_fast=False).stdout,
                         f"{other}\n".encode())
        self.wait_new_daemon(env, other, daemon)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).stdout, f"{other}\n".encode())

    def test_daemon_follows_to_a_directory_with_its_own_file(self):
        root, env = self.tool("relative_if_there")
        env["SPEEDRUNNER_FOLLOW_AFTER"] = "1"
        here = self.directory(root, "here", "here")
        other = self.directory(root, "other", "other")
        daemon = self.start(env, here)
        time.sleep(1.3)
        self.wait_new_daemon(env, other, daemon)
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).stdout, b"other\n")
        # And its first directory, while the second is in use: natively.
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).returncode, EXIT_NOT_FAST)

    def test_daemon_stays_while_its_directory_is_in_use(self):
        # A loop across directories must not restart it at each call.
        root, env = self.tool("cwd")
        env["SPEEDRUNNER_FOLLOW_AFTER"] = "1.5"
        here = os.path.realpath(self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        daemon = self.start(env, here)
        for _ in range(8):
            self.assertEqual(run("hs", ["hsread"], env=env, cwd=here).stdout, f"{here}\n".encode())
            self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).returncode, EXIT_NOT_FAST)
            time.sleep(0.3)
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)

    def test_daemon_stays_within_the_delay(self):
        root, env = self.tool("cwd")
        here = os.path.realpath(self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        daemon = self.start(env, here)  # the default delay: 30 s
        self.assertEqual(run("hs", ["hsread"], env=env, cwd=other).returncode, EXIT_NOT_FAST)
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)

    def test_file_read_by_a_learned_module(self):
        # Loaded on first use: frozen only once a run has taught the daemon it.
        root, env = self.with_config("lazy", "one")
        with open(os.path.join(root, "bin", self.LAZY), "w") as f:
            f.write(self.SOURCES["lazy_module"])
        self.start(env)
        gen = int(status_field(status("hsread", env), "gen"))
        deadline = time.monotonic() + 30
        while not self.TAUGHT_LAZILY and int(status_field(status("hsread", env), "gen") or -1) <= gen:
            self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"one\n")
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        daemon = int(status_field(wait_ready("hsread", env), "pid"))
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"one\n")
        with open(env["HS_CFG"], "w") as f:
            f.write("two")
        self.check_refreshes(env, b"two\n", daemon)

    def test_temp_directory_listed_keeps_the_daemon(self):
        # Its entries change all the time, and no output depends on them.
        root, env = self.tool("list")
        env["HS_DIR"] = "/tmp"
        daemon = self.start(env)
        made = tempfile.mkdtemp(dir="/tmp", prefix="hs-busy-")
        self.addCleanup(os.rmdir, made)
        self.assertNotEqual(run("hs", ["hsread"], env=env).returncode, EXIT_NOT_FAST)
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)

    def test_file_read_at_run_keeps_the_daemon(self):
        _, env = self.with_config("read_at_run", "one")
        daemon = self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"one\n")
        with open(env["HS_CFG"], "w") as f:
            f.write("two")
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"two\n")
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)

    def test_file_only_written_at_import_keeps_the_daemon(self):
        # A log the module appends to is no input of it.
        _, env = self.with_config("write", None)
        daemon = self.start(env)
        with open(env["HS_CFG"], "a") as f:
            f.write("more\n")
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        self.assertEqual(int(status_field(status("hsread", env), "pid")), daemon)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class LockAtLoadChecks:
    """A file lock taken as a tool loads belongs to each run, not to the daemon:
    no run may be refused for what the daemon holds, and two runs may not hold it
    together. Sources (LOCK_KINDS): at load, take it or print BUSY and exit 3;
    then hold it for HS_HOLD seconds, print held."""

    def check_lock_stays_per_run(self, kind, taught=False):
        root, env = self.tool(kind)
        env["HS_CFG"] = os.path.join(root, "lock")
        served_env = {**env, "HS_HOLD": "0"}
        subprocess.run([HS_BIN, "prewarm", "hsread"], env={**BASE_ENV, **served_env}, check=True)
        wait_ready("hsread", served_env)
        deadline = time.monotonic() + 30
        # Refused, the daemon restarts without it; then it serves as ever.
        while True:
            p = run("hs", ["hsread"], env=served_env, require_fast=False)
            self.assertEqual(p.stdout, b"held\n", p.stderr)
            if run("hs", ["hsread"], env=served_env).returncode != EXIT_NOT_FAST and (
                    not taught or int(status_field(status("hsread", served_env), "blacklisted") or 0)):
                break
            self.assertLess(time.monotonic(), deadline, status("hsread", served_env))
            time.sleep(0.1)
        # A plain run is not refused for what the daemon holds.
        wait_ready("hsread", served_env)
        self.assertEqual(run("native", ["hsread"], env=served_env).stdout, b"held\n")
        # Two at once: one holds it, the other is refused, as natively.
        for mode in ("native", "hs"):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                ran = list(pool.map(lambda _: run(mode, ["hsread"], env={**env, "HS_HOLD": "1"}), range(2)))
            self.assertEqual(sorted(p.stdout for p in ran), [b"BUSY\n", b"held\n"], mode)
            self.assertEqual(sorted(p.returncode for p in ran), [0, 3], mode)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class LoadDirChecks:
    """A directory made as a tool loads, removed as it exits: natively each run
    has its own; served, its path is kept in the tool's state, so all calls share
    it, and the first to leave must not take it away from the others.
    Sources: a directory DIR under HS_DIR, removed by an exit handler. dir_temp,
    dir_own (the tool's own, by mkdir; with HS_CLEAR, removes it and prints
    cleared): waits HS_HOLD seconds, makes and removes a file in it, prints ok.
    dir_data: a file made in it at load; prints ok if still there. dir_gone: gone
    again before the load ends. dir_at_run: one per run. LAZY_DIR: the lazy
    module, of dir_temp's kind."""
    LAZY_DIR = None

    def with_dirs(self, kind):
        root, env = self.tool(kind)
        env["HS_DIR"] = os.path.join(root, "dirs")
        os.makedirs(env["HS_DIR"])
        return root, env

    def assert_dirs_empty(self, env):
        deadline = time.monotonic() + 5
        while os.listdir(env["HS_DIR"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(os.listdir(env["HS_DIR"]), [])

    def check_load_dir(self, kind, outside=True):
        _, env = self.with_dirs(kind)
        self.assertEqual(run("native", ["hsread"], env=env).stdout, b"ok\n")
        self.assertEqual(os.listdir(env["HS_DIR"]), [])
        self.start(env)
        for _ in range(8):
            p = run("hs", ["hsread"], env=env)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"ok\n", b""))
        with concurrent.futures.ThreadPoolExecutor(6) as pool:
            ran = list(pool.map(lambda i: run("hs", ["hsread"], env={**env, "HS_HOLD": str(0.2 * (i % 3))}), range(6)))
        self.assertEqual([p.stdout for p in ran], [b"ok\n"] * 6, [p.stderr for p in ran])
        if outside:
            # Removed from outside, as a temp cleaner would: still works.
            for name in os.listdir(env["HS_DIR"]):
                path = os.path.join(env["HS_DIR"], name)
                shutil.rmtree(path) if os.path.isdir(path) else os.unlink(path)
            self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        # And the daemon takes it with it when it leaves.
        stop("hsread", env)
        self.assert_dirs_empty(env)

    def check_load_dir_gone(self, kind):
        """Gone again before the load ends: no call makes it again."""
        _, env = self.with_dirs(kind)
        self.start(env)
        for _ in range(3):
            self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        self.assertEqual(os.listdir(env["HS_DIR"]), [])
        stop("hsread", env)
        self.assert_dirs_empty(env)

    def test_load_dir_removed_at_exit(self):
        self.check_load_dir("dir_temp")

    def test_own_load_dir_removed_at_exit(self):
        self.check_load_dir("dir_own")

    def test_load_dir_keeps_what_was_made_in_it(self):
        self.check_load_dir("dir_data", outside=False)

    def test_load_dir_removed_as_it_loads_is_not_made_again(self):
        self.check_load_dir_gone("dir_gone")

    def test_own_load_dir_removed_at_run_goes(self):
        # What a command does, unlike what an exit handler does: `tool cache clear`.
        _, env = self.with_dirs("dir_own")
        self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        self.assertEqual(len(os.listdir(env["HS_DIR"])), 1)
        self.assertEqual(run("hs", ["hsread"], env={**env, "HS_CLEAR": "1"}).stdout, b"cleared\n")
        self.assert_dirs_empty(env)
        # The next call finds it, as its own load would have made it.
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        stop("hsread", env)
        self.assert_dirs_empty(env)

    def test_dir_made_at_run_is_removed_by_the_child(self):
        _, env = self.with_dirs("dir_at_run")
        self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        self.assert_dirs_empty(env)

    def test_load_dir_of_a_learned_module_leaves_nothing_behind(self):
        # Vetted in a throwaway probe, which leaves without its exit handlers.
        root, env = self.tool("lazy")
        env["HS_DIR"] = os.path.join(root, "dirs")
        os.makedirs(env["HS_DIR"])
        with open(os.path.join(root, "bin", self.LAZY), "w") as f:
            f.write(self.LAZY_DIR)
        self.start(env)
        deadline = time.monotonic() + 30
        while not int(status_field(status("hsread", env), "learned") or 0):
            self.assertEqual(run("hs", ["hsread"], env=env, require_fast=False).stdout, b"ok\n")
            self.assertLess(time.monotonic(), deadline, status("hsread", env))
            time.sleep(0.1)
        wait_ready("hsread", env)
        for _ in range(4):
            self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"ok\n")
        self.assertLessEqual(len(os.listdir(env["HS_DIR"])), 1)
        stop("hsread", env)
        self.assert_dirs_empty(env)


class ImportTimeReads(ImportTimeReadChecks, LockAtLoadChecks, LoadDirChecks, Matrix):
    LOCK_BODY = ("import fcntl, os, sys, time\n"
                 "LOCK_FILE = open(os.environ['HS_CFG'], 'w')\n"
                 "try:\n"
                 "    {take}\n"
                 "except OSError:\n"
                 "    print('BUSY')\n"
                 "    sys.exit(3)\n")
    LOCK_MAIN = "def main():\n    time.sleep(float(os.environ.get('HS_HOLD', '0')))\n    print('held')\n"
    TEMP_MAIN = ("def main():\n"
                 "    time.sleep(float(os.environ.get('HS_HOLD', '0')))\n"
                 "    if os.environ.get('HS_CLEAR'):\n"
                 "        shutil.rmtree(DIR)\n"
                 "        print('cleared')\n"
                 "        return\n"
                 "    fd, path = tempfile.mkstemp(dir=DIR)\n"
                 "    os.close(fd)\n"
                 "    os.unlink(path)\n"
                 "    print('ok')\n")
    TEMP_HEAD = "import atexit, os, shutil, tempfile, time\n"
    TEMP_MADE = "DIR = tempfile.mkdtemp(prefix='hs-', dir=os.environ['HS_DIR'])\n"
    OWN_MADE = "DIR = os.path.join(os.environ['HS_DIR'], 'own-%d' % os.getpid())\nos.makedirs(DIR)\n"
    FILE_MADE = "FD, FILE = tempfile.mkstemp(prefix='hs-', dir=os.environ['HS_DIR'])\nos.close(FD)\n"
    FILE_MAIN = ("def main():\n"
                 "    time.sleep(float(os.environ.get('HS_HOLD', '0')))\n"
                 "    print('ok' if os.path.exists(FILE) else 'GONE')\n")
    LAZY_DIR = TEMP_HEAD + TEMP_MADE + "atexit.register(shutil.rmtree, DIR)\nVALUE = 'ok'\n"
    SOURCES = {
        "dir_temp": TEMP_HEAD + TEMP_MADE
                    + "@atexit.register\ndef clean():\n    shutil.rmtree(DIR)\n" + TEMP_MAIN,
        "dir_own": TEMP_HEAD + OWN_MADE
                   + "@atexit.register\ndef clean():\n    shutil.rmtree(DIR, True)\n" + TEMP_MAIN,
        "dir_data": TEMP_HEAD + TEMP_MADE + "open(os.path.join(DIR, 'data'), 'w').close()\n"
                    + "@atexit.register\ndef clean():\n    shutil.rmtree(DIR)\n"
                    + "def main():\n"
                      "    time.sleep(float(os.environ.get('HS_HOLD', '0')))\n"
                      "    print('ok' if os.path.exists(os.path.join(DIR, 'data')) else 'GONE')\n",
        "dir_gone": TEMP_HEAD + "with tempfile.TemporaryDirectory(prefix='hs-', dir=os.environ['HS_DIR']):\n    pass\n"
                    + "def main():\n    print('ok')\n",
        "dir_at_run": TEMP_HEAD + "def main():\n"
                      "    path = tempfile.mkdtemp(dir=os.environ['HS_DIR'])\n"
                      "    atexit.register(shutil.rmtree, path)\n    print('ok')\n",
        # Like ansible's config: removed at exit by a function of the tool's.
        "temp_by_function": TEMP_HEAD + TEMP_MADE
                            + "def clean(path):\n    shutil.rmtree(path)\n"
                            + "atexit.register(clean, DIR)\n" + TEMP_MAIN,
        "temp_bound": TEMP_HEAD + TEMP_MADE + "atexit.register(shutil.rmtree, DIR)\n" + TEMP_MAIN,
        "temp_from_import": TEMP_HEAD + TEMP_MADE + "from shutil import rmtree\n"
                            + "def clean():\n    rmtree(DIR)\natexit.register(clean)\n" + TEMP_MAIN,
        "temp_object": TEMP_HEAD + "HOLD = tempfile.TemporaryDirectory(prefix='hs-', dir=os.environ['HS_DIR'])\n"
                       + "DIR = HOLD.name\n" + TEMP_MAIN,
        "temp_by_hand": TEMP_HEAD + TEMP_MADE
                        + "def clean():\n    for e in os.listdir(DIR):\n        os.unlink(os.path.join(DIR, e))\n"
                        + "    os.rmdir(DIR)\natexit.register(clean)\n" + TEMP_MAIN,
        "temp_error": TEMP_HEAD + TEMP_MADE + "atexit.register(shutil.rmtree, DIR)\n"
                      + "def main():\n    os.unlink(DIR + '-not-there')\n",
        "own_bound": TEMP_HEAD + OWN_MADE + "atexit.register(shutil.rmtree, DIR, True)\n" + TEMP_MAIN,
        "own_late_import": "import atexit, os, tempfile, time\n" + OWN_MADE
                           + "def clean():\n    import shutil\n    shutil.rmtree(DIR)\n"
                           + "atexit.register(clean)\n" + TEMP_MAIN,
        "file_bound": TEMP_HEAD + FILE_MADE + "atexit.register(os.unlink, FILE)\n" + FILE_MAIN,
        "file_closure": TEMP_HEAD + FILE_MADE + "def clean():\n    os.unlink(FILE)\natexit.register(clean)\n" + FILE_MAIN,
        "file_from_import": TEMP_HEAD + FILE_MADE + "from os import remove\n"
                            + "def clean():\n    remove(FILE)\natexit.register(clean)\n" + FILE_MAIN,
        "file_gone": TEMP_HEAD + FILE_MADE + "os.unlink(FILE)\ndef main():\n    print('ok')\n",
        "flock": LOCK_BODY.format(take="fcntl.flock(LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)") + LOCK_MAIN,
        "lockf": LOCK_BODY.format(take="fcntl.lockf(LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)") + LOCK_MAIN,
        "flock_hidden": LOCK_BODY.format(take="""try:
        fcntl.flock(LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise
    except:
        pass""") + LOCK_MAIN,
        "read": "import os\nVALUE = open(os.environ['HS_CFG']).read()\n"
                "def main():\n    print(VALUE)\n",
        "read_if_there": "import os\nCFG = os.environ['HS_CFG']\n"
                         "VALUE = open(CFG).read() if os.path.exists(CFG) else 'none'\n"
                         "def main():\n    print(VALUE)\n",
        "size": "import os\nVALUE = os.path.getsize(os.environ['HS_CFG'])\n"
                "def main():\n    print(VALUE)\n",
        "list": "import os\nVALUE = len(os.listdir(os.environ['HS_DIR']))\n"
                "def main():\n    print(VALUE)\n",
        "is_dir": "import os\nVALUE = 'yes' if os.path.isdir(os.environ['HS_DIR']) else 'no'\n"
                  "def main():\n    print(VALUE)\n",
        "relative_if_there": "import os\nVALUE = open('local.cfg').read() if os.path.exists('local.cfg') else 'none'\n"
                              "def main():\n    print(VALUE)\n",
        "relative": "VALUE = open('local.cfg').read()\ndef main():\n    print(VALUE)\n",
        "relative_exists": "import os\nVALUE = 'yes' if os.path.exists('local.cfg') else 'no'\n"
                            "def main():\n    print(VALUE)\n",
        "cwd": "import os\nVALUE = os.getcwd()\ndef main():\n    print(VALUE)\n",
        "lazy": "def main():\n    import hsread_lazy\n    print(hsread_lazy.VALUE)\n",
        "lazy_module": "import os\nVALUE = open(os.environ['HS_CFG']).read()\n",
        "read_at_run": "import os\ndef main():\n    print(open(os.environ['HS_CFG']).read())\n",
        "write": "import os\nopen(os.environ['HS_CFG'], 'a').close()\ndef main():\n    print('ok')\n",
    }

    def script(self):
        python = os.path.join(os.path.dirname(shutil.which("hstest", path=BASE_ENV["PATH"])), "python3")
        return f"#!{python}\nimport hsread_lib\nhsread_lib.main()\n"

    def test_bound_stat_stays_harmless(self):
        # `from os import stat` keeps what the module found while loading.
        root, env = self.with_config("read", "one")
        with open(os.path.join(root, "bin", self.LIB), "a") as f:
            f.write("from os import getcwd, stat\n"
                    "_main = main\n"
                    "def main():\n"
                    "    stat('/')\n"
                    "    _main()\n")
        self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"one\n")

    def test_multiprocessing_keeps_no_daemon_cwd(self):
        # The standard library's one read of the cwd as it loads (aws loads it):
        # no reason to lose the fast path elsewhere, and set for each child.
        root, env = self.tool("read")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import multiprocessing.process as p\n"
                    "def main():\n    print(p.ORIGINAL_DIR)\n")
        self.start(env, self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        p = run("hs", ["hsread"], env=env, cwd=other)
        self.assertEqual(p.stdout, f"{other}\n".encode())
        self.assertEqual(outcome(run("native", ["hsread"], env=env, cwd=other)), outcome(p))

    def test_rich_keeps_no_daemon_cwd(self):
        root, env = self.tool("read")
        with open(os.path.join(root, "bin", "rich.py"), "w") as f:
            f.write("import os\n_IMPORT_CWD = os.path.abspath(os.getcwd())\n")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import rich\ndef main():\n    print(rich._IMPORT_CWD)\n")
        self.start(env, self.directory(root, "here"))
        other = os.path.realpath(self.directory(root, "other"))
        p = run("hs", ["hsread"], env=env, cwd=other)
        self.assertEqual(p.stdout, f"{other}\n".encode())

    def test_tempfile_at_import_stays_served(self):
        root, env = self.tool("read")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import tempfile\nVALUE = tempfile.gettempdir()\n"
                    "def main():\n    print(VALUE)\n")
        self.start(env, self.directory(root, "here"))
        other = self.directory(root, "other")
        self.assertEqual(outcome(run("native", ["hsread"], env=env, cwd=other)),
                         outcome(run("hs", ["hsread"], env=env, cwd=other)))

    def test_version_bump_with_pip(self):
        # A real upgrade, as `pip install -U` does it: never the old version,
        # the same output as a plain run, then served again by a new daemon.
        root = tempfile.mkdtemp(dir=ROOT)
        venv = os.path.join(root, "venv")
        base = os.path.join(os.path.dirname(shutil.which("hstest", path=BASE_ENV["PATH"])), "python3")
        if subprocess.run([base, "-m", "venv", venv], capture_output=True).returncode:
            self.skipTest("no venv module")
        python = os.path.join(venv, "bin", "python")
        if subprocess.run([python, "-m", "pip", "--version"], capture_output=True).returncode:
            self.skipTest("no pip in a new venv")

        def wheel(version):
            path = os.path.join(root, f"hsread-{version}-py3-none-any.whl")
            info = f"hsread-{version}.dist-info"
            files = {
                "hsread_lib.py": f"def main():\n    print('{version}')\n",
                f"{info}/METADATA": f"Metadata-Version: 2.1\nName: hsread\nVersion: {version}\n",
                f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                f"{info}/entry_points.txt": "[console_scripts]\nhsread = hsread_lib:main\n",
            }
            with zipfile.ZipFile(path, "w") as z:
                for name, text in files.items():
                    z.writestr(name, text)
                z.writestr(f"{info}/RECORD", "".join(f"{n},,\n" for n in files) + f"{info}/RECORD,,\n")
            return path

        def install(version):
            subprocess.run([python, "-m", "pip", "install", "-q", "--no-index", "--no-deps",
                            "--disable-pip-version-check", wheel(version)], check=True, capture_output=True)

        install("1.0")
        env = {"PATH": f"{os.path.join(venv, 'bin')}:{BASE_ENV['PATH']}",
               "XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(stop, "hsread", env)
        daemon = self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"1.0\n")
        install("2.0")
        self.check_refreshes(env, b"2.0\n", daemon)

    def test_environ_copy_at_import(self):
        root, env = self.tool("read")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import os\nVALUE = os.environ.copy().get('HS_VALUE')\n"
                    "def main():\n    print(VALUE)\n")
        self.start({**env, "HS_VALUE": "one"})
        changed = {**env, "HS_VALUE": "two"}
        self.assertEqual(run("hs", ["hsread"], env=changed, require_fast=False).stdout, b"two\n")
        self.assertEqual(run("hs", ["hsread"], env=changed).returncode, EXIT_NOT_FAST)

    def test_load_tempdir_removed_by_a_function(self):
        self.check_load_dir("temp_by_function")

    def test_load_tempdir_removed_by_a_bound_rmtree(self):
        self.check_load_dir("temp_bound")

    def test_load_tempdir_removed_by_an_imported_rmtree(self):
        self.check_load_dir("temp_from_import")

    def test_load_tempdir_of_a_temporary_directory(self):
        self.check_load_dir("temp_object")

    def test_load_tempdir_removed_by_hand(self):
        self.check_load_dir("temp_by_hand")

    def test_error_through_a_guarded_function_shows_no_frame_of_ours(self):
        _, env = self.with_dirs("temp_error")
        native = run("native", ["hsread"], env=env)
        self.start(env)
        served = run("hs", ["hsread"], env=env)
        self.assertEqual(served.returncode, native.returncode)
        strip = lambda text: re.sub(rb"hs-\w+", b"hs-", text)
        self.assertEqual(strip(served.stderr), strip(native.stderr))

    def test_own_load_dir_removed_by_a_bound_rmtree(self):
        self.check_load_dir("own_bound")

    def test_own_load_dir_removed_by_an_rmtree_imported_at_exit(self):
        self.check_load_dir("own_late_import")

    def test_load_temp_file_removed_by_a_bound_unlink(self):
        self.check_load_dir("file_bound")

    def test_load_temp_file_removed_by_a_closure(self):
        self.check_load_dir("file_closure")

    def test_load_temp_file_removed_by_an_imported_unlink(self):
        self.check_load_dir("file_from_import")

    def test_load_temp_file_removed_as_it_loads_is_not_made_again(self):
        self.check_load_dir_gone("file_gone")

    def test_flock_at_load_stays_per_run(self):
        self.check_lock_stays_per_run("flock")

    def test_lockf_at_load_stays_per_run(self):
        self.check_lock_stays_per_run("lockf")

    def test_lock_at_load_seen_though_the_error_is_swallowed(self):
        self.check_lock_stays_per_run("flock_hidden")

    def test_lock_at_load_stays_per_run_in_a_learned_module(self):
        root, env = self.tool("lazy")
        with open(os.path.join(root, "bin", "hsread_lazy.py"), "w") as f:
            f.write(self.LOCK_BODY.format(take="fcntl.flock(LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)")
                    + "VALUE = 'held'\ntime.sleep(float(os.environ.get('HS_HOLD', '0')))\n")
        env["HS_CFG"] = os.path.join(root, "lock")
        self.start(env)
        deadline = time.monotonic() + 30
        while not int(status_field(status("hsread", env), "blacklisted") or 0):
            self.assertEqual(run("hs", ["hsread"], env=env, require_fast=False).stdout, b"held\n")
            self.assertLess(time.monotonic(), deadline, status("hsread", env))
            time.sleep(0.1)
        wait_ready("hsread", env)
        self.assertEqual(run("native", ["hsread"], env=env).stdout, b"held\n")
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            ran = list(pool.map(lambda _: run("hs", ["hsread"], env={**env, "HS_HOLD": "1"}), range(2)))
        self.assertEqual(sorted(p.stdout for p in ran), [b"BUSY\n", b"held\n"])

    def test_lock_taken_at_run_is_no_reason_to_refuse(self):
        root, env = self.tool("read_at_run")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import fcntl, os\ndef main():\n"
                    "    f = open(os.environ['HS_CFG'], 'w')\n"
                    "    fcntl.flock(f, fcntl.LOCK_EX)\n    print('held')\n")
        env["HS_CFG"] = os.path.join(root, "lock")
        self.start(env)
        self.assertEqual(run("hs", ["hsread"], env=env).stdout, b"held\n")
        self.assertIn("blacklisted=0", status("hsread", env))

    def test_thread_holding_a_lock_at_import_never_hangs(self):
        # A fork copies the calling thread only: a lock another thread held
        # would stay locked forever in the child.
        root, env = self.tool("read")
        with open(os.path.join(root, "bin", self.LIB), "w") as f:
            f.write("import threading, time\n"
                    "LOCK = threading.Lock()\n"
                    "def hold():\n"
                    "    while True:\n"
                    "        with LOCK:\n"
                    "            time.sleep(0.01)\n"
                    # Taken again at once it starves a waiter, natively too.
                    "        time.sleep(0.01)\n"
                    "threading.Thread(target=hold, daemon=True).start()\n"
                    "def main():\n"
                    "    print(LOCK.acquire(timeout=5))\n")
        self.start(env)
        for _ in range(10):
            self.assertEqual(run("hs", ["hsread"], env=env, require_fast=False).stdout, b"True\n")


def check_client_killed(case, tool, start_hold, root, env):
    """kill -9 of the client: the tool dies too, as natively, and never finishes;
    the daemon goes on serving."""
    marker = os.path.join(root, "marker")
    p, child = start_hold(env, marker, seconds="5")
    p.kill()
    p.wait()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        case.fail("the tool outlived its client")
    case.assertEqual(read_lines(marker), ["start"])
    case.assertTrue(status(tool, env).startswith("ready"))


def daemon_socket(env):
    """The one socket in a private daemon's runtime dir."""
    sock_dir = os.path.join(env["XDG_RUNTIME_DIR"], "speedrunner")
    [name] = [n for n in os.listdir(sock_dir) if n.endswith(".sock")]
    return os.path.join(sock_dir, name)


def ask_as(sock_path, euid):
    """Sends a status request (P) to a daemon from a process of effective
    uid euid, which SO_PEERCRED reports, but filesystem uid 0, so that it
    still reaches the 0700 runtime dir: the daemon's own check, behind the
    client's. Returns the kind of the first reply, b"" at end of file."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:  # never let this copy of the test runner go on
            os.close(r)
            if euid != os.geteuid():
                os.seteuid(euid)
                ctypes.CDLL(None, use_errno=True).setfsuid(0)
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(10)
            s.connect(sock_path)
            first = s.recv(5)
            if first[:1] == b"H":
                s.sendall(b"HS1P" + struct.pack("<I", 0))
                first = s.recv(1)
            os.write(w, b"=" + first[:1])
        except BaseException as exc:
            os.write(w, b"!" + repr(exc).encode())
        finally:
            os._exit(0)
    os.close(w)
    with os.fdopen(r, "rb") as f:
        out = f.read()
    os.waitpid(pid, 0)
    if not out.startswith(b"="):
        raise RuntimeError(out.decode(errors="replace"))
    return out[1:]


def check_peer_uid(test, tool):
    """A daemon serves a peer of its own uid only: another one gets no
    answer (at most the wait status of the child that turned it away)."""
    if MACOS:
        test.skipTest("no setfsuid on macOS: a peer of another uid cannot reach the 0700 directory")
    root, env = private_daemon(tool)
    try:
        sock = daemon_socket(env)
        test.assertEqual(ask_as(sock, os.getuid()), b"P")
        test.assertIn(ask_as(sock, 65534), (b"", b"W"))
        test.assertEqual(ask_as(sock, os.getuid()), b"P")  # and still serves
    finally:
        stop(tool, env)


def process_args(pid):
    """A process's argv and env entries, as bytes: /proc on Linux, sysctl
    KERN_PROCARGS2 on macOS (argc, the executable path and padding, argv,
    then env up to an empty string)."""
    if not MACOS:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")[:-1]
        with open(f"/proc/{pid}/environ", "rb") as f:
            return argv, f.read().split(b"\0")
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)):
        raise OSError(ctypes.get_errno(), f"KERN_PROCARGS2 of {pid}")
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)):
        raise OSError(ctypes.get_errno(), f"KERN_PROCARGS2 of {pid}")
    data = buf.raw[:size.value]
    argc = struct.unpack_from("<i", data)[0]
    items = data[4:].partition(b"\0")[2].lstrip(b"\0").split(b"\0")
    env = items[argc:]
    return items[:argc], env[:env.index(b"")] if b"" in env else env


def check_daemon_refuses_bad_dir(test, tool):
    """A daemon started by hand (as the client would start it: same command
    line, same env) with its socket in a runtime dir that is not ours with
    mode 0700 refuses to start, and binds nothing there."""
    root, env = private_daemon(tool)
    try:
        pid = int(status_field(status(tool, env), "pid"))
        argv, entries = process_args(pid)
        environ = dict(e.split(b"=", 1) for e in entries if b"=" in e)
    finally:
        stop(tool, env)
    if b"--lock-fd" in argv:
        i = argv.index(b"--lock-fd")
        del argv[i:i + 2]
    i = argv.index(b"--socket") + 1
    cases = [("too-open", 0o755, None)]
    if os.getuid() == 0:
        cases.append(("other-uid", 0o700, 65534))
    for name, mode, uid in cases:
        with test.subTest(dir=name):
            d = os.path.join(root, name, "speedrunner")
            os.makedirs(d)
            os.chmod(d, mode)
            if uid is not None:
                os.chown(d, uid, -1)
            sock = os.path.join(d, os.path.basename(argv[i].decode()))
            p = subprocess.run(argv[:i] + [sock.encode()] + argv[i + 1:], env=environ, cwd=root,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            test.assertNotEqual(p.returncode, 0, p.stderr[-500:])
            test.assertFalse(os.path.exists(sock))
            test.assertIn(b"0700", p.stdout + p.stderr + open_log(sock))


def open_log(sock):
    try:
        with open(os.path.splitext(sock)[0] + ".log", "rb") as f:
            return f.read()
    except OSError:
        return b""


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Security(Matrix):
    """Brief §5 « Sécurité »: anything not provably ours means native."""

    def check_native_only(self, env):
        argv = ["hstest", "argv"]
        self.assertEqual(run("hs", argv, env=env).returncode, EXIT_NOT_FAST)
        self.assertEqual(outcome(run("hs", argv, env=env, require_fast=False)),
                         outcome(run("native", argv)))
        time.sleep(0.5)  # an autostart, if any, would have had time to bind
        self.assertEqual(status("hstest", env), "down")

    def bad_runtime_dir(self, mode, uid=None):
        root = tempfile.mkdtemp(dir=ROOT)
        run_dir = os.path.join(root, "run")
        os.makedirs(os.path.join(run_dir, "speedrunner"))
        os.chmod(run_dir, 0o700)
        os.chmod(os.path.join(run_dir, "speedrunner"), mode)
        if uid is not None:
            os.chown(os.path.join(run_dir, "speedrunner"), uid, -1)
        return {"XDG_RUNTIME_DIR": run_dir, "XDG_CACHE_HOME": os.path.join(root, "cache")}

    def test_runtime_dir_too_open(self):
        for mode in (0o755, 0o770, 0o701):
            with self.subTest(mode=oct(mode)):
                self.check_native_only(self.bad_runtime_dir(mode))

    @unittest.skipUnless(os.getuid() == 0, "needs root to give the directory away")
    def test_runtime_dir_owned_by_other_uid(self):
        self.check_native_only(self.bad_runtime_dir(0o700, uid=65534))

    @unittest.skipUnless(os.getuid() == 0, "needs root to connect as another uid")
    def test_daemon_refuses_peer_of_other_uid(self):
        check_peer_uid(self, "hstest")

    def test_daemon_refuses_bad_runtime_dir(self):
        check_daemon_refuses_bad_dir(self, "hstest")

    def test_tmp_fallback_symlink(self):
        # Without XDG_RUNTIME_DIR, the runtime dir is /tmp/speedrunner-<uid>:
        # a name anyone can take first. A symlink there, even to a 0700
        # directory of ours, is refused: native runs, nothing bound.
        path = f"/tmp/speedrunner-{os.getuid()}"
        if os.path.lexists(path):
            self.skipTest(f"{path} exists")
        target = tempfile.mkdtemp(dir=ROOT)
        os.chmod(target, 0o700)
        os.symlink(target, path)
        try:
            env = {"XDG_RUNTIME_DIR": ""}
            argv = ["hstest", "argv"]
            self.assertEqual(run("hs", argv, env=env).returncode, EXIT_NOT_FAST)
            self.assertEqual(outcome(run("hs", argv, env=env, require_fast=False)),
                             outcome(run("native", argv)))
            time.sleep(0.5)  # a daemon would have bound its socket by now
            self.assertEqual(os.listdir(target), [])
            self.assertEqual(daemon_roots(path), [])
        finally:
            os.unlink(path)

    def test_runtime_dir_too_deep_for_a_socket(self):
        # A socket path holds 104 bytes on macOS, 108 on Linux: under a
        # deeper XDG_RUNTIME_DIR no client could reach the daemon. Both
        # sides take /tmp/speedrunner-<uid> instead.
        path = f"/tmp/speedrunner-{os.getuid()}"
        if not os.path.lexists(path):
            self.addCleanup(shutil.rmtree, path, True)
        # A tool name of its own: its daemon is this test's, even in a
        # /tmp/speedrunner-<uid> that is in use.
        root = tempfile.mkdtemp(dir=ROOT)
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        tool = "hstest-deep-" + os.path.basename(root)
        os.symlink(shutil.which("hstest", path=os.environ["HS_PATH"]), os.path.join(bin_dir, tool))
        deep = os.path.join(root, "d" * max(1, 112 - len(root)))
        os.makedirs(deep, mode=0o700)
        env = {"XDG_RUNTIME_DIR": deep, "XDG_CACHE_HOME": os.path.join(root, "cache"),
               "PATH": bin_dir + ":" + BASE_ENV["PATH"]}
        self.addCleanup(stop, tool, env)
        wait_ready(tool, env)
        argv = [tool, "argv", "x"]
        self.assertEqual(outcome(run("hs", argv, env=env)), outcome(run("native", argv, env=env)))
        mine = [n for n in os.listdir(path) if n.startswith(tool + "-") and n.endswith(".sock")]
        self.assertEqual(len(mine), 1, os.listdir(path))
        self.assertFalse(os.path.exists(os.path.join(deep, "speedrunner")))

    @unittest.skipUnless(os.getuid() == 0, "needs root to listen as another uid")
    def test_socket_served_by_other_uid(self):
        root, env = private_daemon("hstest")
        stop("hstest", env)
        # Down as soon as it leaves; gone once its children are.
        deadline = time.monotonic() + 10
        while daemon_roots(root) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(daemon_roots(root), [])
        # A listener of another uid in the daemon's place.
        sock_dir = os.path.join(env["XDG_RUNTIME_DIR"], "speedrunner")
        [name] = [n for n in os.listdir(sock_dir) if n.endswith(".sock.lock")]
        path = os.path.join(sock_dir, name[:-len(".lock")])
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:  # never let this copy of the test runner go on
                s = socket.socket(socket.AF_UNIX)
                s.bind(path)
                os.setuid(65534)  # SO_PEERCRED reports the uid at listen()
                s.listen(8)
                os.write(w, b"x")
                while True:  # the client refuses it before any reply
                    s.accept()[0].close()
            except BaseException as exc:
                os.write(w, repr(exc).encode())
            finally:
                os._exit(1)
        self.addCleanup(lambda: (os.kill(pid, signal.SIGKILL), os.waitpid(pid, 0)))
        self.assertEqual(os.read(r, 4096), b"x")
        argv = ["hstest", "argv"]
        self.assertEqual(run("hs", argv, env=env).returncode, EXIT_NOT_FAST)
        self.assertEqual(outcome(run("hs", argv, env=env, require_fast=False)),
                         outcome(run("native", argv)))
        # Refused, not mistaken for a crashed daemon: none was started.
        self.assertEqual(daemon_roots(root), [])


def in_private_daemon_env(root=None):
    root = root or tempfile.mkdtemp(dir=ROOT)
    env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache"),
           "XDG_DATA_HOME": os.path.join(root, "data")}
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    return env


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class ForcedNative(Matrix):
    """`srr --native` and SPEEDRUNNER_NATIVE: the tool itself, whatever a daemon
    could do. A tool that prints its pid tells: natively, the client has execed
    it, so it is the process the test started; served, it is a daemon's child."""

    @classmethod
    def setUpClass(cls):
        root = tempfile.mkdtemp(dir=ROOT, prefix="forced-")
        bin_dir = os.path.join(root, "bin")  # apart: what the daemon makes must not touch the tool's directory
        os.mkdir(bin_dir)
        cls.tool = os.path.join(bin_dir, "pidtool")
        with open(cls.tool, "w") as f:
            f.write(f"#!{shutil.which('python3', path=os.environ['HS_PATH'])}\n"
                    "import os, sys\nprint(os.getpid(), *sys.argv[1:])\n")
        os.chmod(cls.tool, 0o755)
        cls.srr = os.path.join(root, "srr")
        os.symlink(HS_BIN, cls.srr)
        cls.env = {**in_private_daemon_env(root), "PATH": f"{bin_dir}:{BASE_ENV['PATH']}"}

    def setUp(self):
        self.addCleanup(stop, "pidtool", self.env)

    def call(self, *argv, **env):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={**BASE_ENV, **self.env, **env})
        out, err = p.communicate(timeout=60)
        self.assertEqual((p.returncode, err), (0, b""))
        return p.pid, out

    def native(self, *argv, **env):
        pid, out = self.call(*argv, **env)
        self.assertEqual(out.split()[0], str(pid).encode())
        return out.split()[1:]

    def served(self, *argv, **env):
        pid, out = self.call(*argv, **env, SPEEDRUNNER_REQUIRE_FAST="1")
        self.assertNotEqual(out.split()[0], str(pid).encode())
        return out.split()[1:]

    def warm(self):
        served_soon(["pidtool"], self.env)

    def test_flag_beside_a_warm_daemon(self):
        self.warm()
        self.served(self.srr, "pidtool")  # what the flag has to change
        self.assertEqual(self.native(self.srr, "--native", "pidtool", "a b"), [b"a", b"b"])
        self.assertEqual(self.native(HS_BIN, "run", "--native", "pidtool", "--", "x"), [b"x"])

    def test_env_beside_a_warm_daemon(self):
        self.warm()
        self.assertEqual(self.native(self.srr, "pidtool", "x", SPEEDRUNNER_NATIVE="1"), [b"x"])
        self.assertEqual(self.native(HS_BIN, "run", "pidtool", "--", "x", SPEEDRUNNER_NATIVE="yes"), [b"x"])

    def test_empty_or_zero_is_not_asking(self):
        self.warm()
        for value in ("", "0"):
            with self.subTest(value=value):
                self.served(self.srr, "pidtool", SPEEDRUNNER_NATIVE=value)

    def test_outranks_require_fast(self):
        # No daemon here: without the request, this exits with 124.
        p = subprocess.run([self.srr, "pidtool"], capture_output=True,
                           env={**BASE_ENV, **self.env, "SPEEDRUNNER_REQUIRE_FAST": "1",
                                "SPEEDRUNNER_NO_AUTOSTART": "1"})
        self.assertEqual(p.returncode, EXIT_NOT_FAST)
        self.native(self.srr, "--native", "pidtool", SPEEDRUNNER_REQUIRE_FAST="1")
        self.native(self.srr, "pidtool", SPEEDRUNNER_REQUIRE_FAST="1", SPEEDRUNNER_NATIVE="1")

    def test_flag_belongs_to_the_tool_after_its_name(self):
        self.warm()
        self.assertEqual(self.served(self.srr, "pidtool", "--native"), [b"--native"])
        self.assertEqual(self.native(self.srr, "--native", "pidtool", "--native"), [b"--native"])

    def test_starts_no_daemon(self):
        self.native(self.srr, "--native", "pidtool")
        self.native(self.srr, "pidtool", SPEEDRUNNER_NATIVE="1")
        p = subprocess.run([HS_BIN, "prewarm", "pidtool"], capture_output=True,
                           env={**BASE_ENV, **self.env, "SPEEDRUNNER_NATIVE": "1"})
        self.assertEqual(p.returncode, 1)
        self.assertIn(b"SPEEDRUNNER_NATIVE is set", p.stderr)
        p = subprocess.run([HS_BIN, "exec", "--eager", "-t", "pidtool", "true"], capture_output=True,
                           env={**BASE_ENV, **self.env, "SPEEDRUNNER_NATIVE": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        time.sleep(1)  # exec's starter is detached
        self.assertEqual(status("pidtool", self.env), "down")

    def test_past_the_links_of_a_script(self):
        # Natively, each background job's $! is the tool's pid.
        self.warm()
        script = ('"$SRR" --native pidtool flag & echo "$!"; wait\n'
                  'SPEEDRUNNER_NATIVE=1 pidtool env & echo "$!"; wait\n')
        p = subprocess.run([HS_BIN, "exec", "-t", "pidtool", "bash", "-c", script], capture_output=True,
                           timeout=60, env={**BASE_ENV, **self.env, "SRR": self.srr})
        self.assertEqual((p.returncode, p.stderr), (0, b""))
        words = sorted(line.split() for line in p.stdout.splitlines())
        self.assertEqual(len(words), 4, words)
        self.assertEqual((words[0][0], words[2][0]), (words[1][0], words[3][0]), words)

    def test_sub_processes_under_the_env(self):
        # exec after exec, down to the tool: all one pid, unless a daemon served it.
        self.warm()
        self.native(HS_BIN, "exec", "-t", "pidtool", "bash", "-c", "exec pidtool", SPEEDRUNNER_NATIVE="1")

    def test_a_debug_run_says_so(self):
        p = subprocess.run([self.srr, "--native", "pidtool"], capture_output=True,
                           env={**BASE_ENV, **self.env, "SPEEDRUNNER_DEBUG": "1"})
        self.assertEqual(p.stderr, b"speedrunner: running pidtool natively: asked to\n")


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class CallerContext(Matrix):
    """What a process has from its caller and a daemon started elsewhere
    would not: each is another daemon, or a native run."""

    @classmethod
    def tearDownClass(cls):
        wait_ready("hstest")  # the tests' own, which some of them stopped

    def served(self, argv, env=None, preexec=None, cwd=None):
        """Same outcome as natively, served, in this context."""
        native = outcome(run("native", argv, env=env, preexec=preexec, cwd=cwd))
        self.addCleanup(self.stop_there, argv[0], env, preexec, cwd)
        deadline = time.monotonic() + 30
        while True:
            p = run("hs", argv, env=env, preexec=preexec, cwd=cwd)
            if p.returncode != EXIT_NOT_FAST or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        self.assertEqual(native, outcome(p))
        return p.stdout

    def stop_there(self, tool, env, preexec, cwd):
        subprocess.run([HS_BIN, "stop", tool], env={**BASE_ENV, **(env or {})}, preexec_fn=preexec,
                       cwd=cwd, capture_output=True, timeout=15)

    def test_file_size_limit(self):
        import resource
        limit = lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
        out = self.served(["hstest", "context"], preexec=limit)
        self.assertIn(b"fsize (1048576, 1048576)", out)
        # And the caller after it has its own limits, not those.
        self.assertSame(["hstest", "context"])

    def test_first_callers_limit_does_not_stick(self):
        import resource
        limit = lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (10, 10))
        env = in_private_daemon_env()
        subprocess.run([HS_BIN, "prewarm", "hstest"], env={**BASE_ENV, **env}, preexec_fn=limit, capture_output=True)
        self.addCleanup(self.stop_there, "hstest", env, limit, None)
        self.addCleanup(stop, "hstest", env)
        wait_ready("hstest", env)  # there, with those limits: not this caller's
        self.served(["hstest", "context"], env=env)

    def test_nice(self):
        out = self.served(["hstest", "context"], preexec=lambda: os.nice(3))
        self.assertIn(b"nice ", out)

    @unittest.skipUnless(hasattr(os, "sched_setaffinity"), "Linux")
    def test_affinity(self):
        one = min(os.sched_getaffinity(0))
        self.served(["hstest", "context"], preexec=lambda: os.sched_setaffinity(0, {one}))

    def test_library_path(self):
        self.served(["hstest", "env", "LD_LIBRARY_PATH"], env={"LD_LIBRARY_PATH": "/nowhere"})

    def test_preloaded_library_runs_natively(self):
        # One that exists on macOS, where a missing one aborts any program.
        name, lib = (("DYLD_INSERT_LIBRARIES", "/usr/lib/libSystem.B.dylib") if MACOS
                     else ("LD_PRELOAD", "/nowhere/lib.so"))
        p = run("hs", ["hstest", "argv"], env={name: lib})
        self.assertEqual(p.returncode, EXIT_NOT_FAST)
        self.assertIn(name.encode(), p.stderr)

    @unittest.skipUnless(MACOS, "macOS")
    def test_sandbox_runs_natively(self):
        cmd, extra = command("hs", ["hstest", "argv"])
        p = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", *cmd],
                           env={**BASE_ENV, **extra}, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, EXIT_NOT_FAST, p.stderr)

    def test_time_zone(self):
        out = self.served(["hstest", "tz"], env={"TZ": "Asia/Tokyo"})
        self.assertIn(b"09:00 JST", out)

    def test_locale_variable_runs_natively(self):
        self.assertEqual(run("hs", ["hstest", "argv"], env={"LC_TIME": "C"}).returncode, EXIT_NOT_FAST)

    def test_relative_pythonpath(self):
        projects = []
        for name in ("alpha", "beta"):
            d = tempfile.mkdtemp(dir=ROOT)
            with open(os.path.join(d, "ppmod.py"), "w") as f:
                f.write(f"NAME = {name!r}\n")
            projects.append((d, name))
        for _ in range(2):
            for d, name in projects:
                out = self.served(["hstest", "ppmod"], env={"PYTHONPATH": "."}, cwd=d)
                self.assertEqual(out, name.encode() + b"\n")

    def test_interpreter_from_path(self):
        # `#!/usr/bin/env python3`, with another venv first on PATH.
        root = tempfile.mkdtemp(dir=ROOT)
        tool = os.path.join(root, "bin", "envtool")
        os.makedirs(os.path.dirname(tool))
        with open(tool, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nprint(sys.prefix)\n")
        os.chmod(tool, 0o755)
        prefixes = []
        for name in ("one", "two"):
            venv = os.path.join(root, name)
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True)
            env = {"PATH": f"{venv}/bin:{os.path.dirname(tool)}:{BASE_ENV['PATH']}"}
            prefixes.append(self.served(["envtool"], env=env).strip())
            self.assertTrue(prefixes[-1].endswith(name.encode()), prefixes[-1])
        self.assertNotEqual(prefixes[0], prefixes[1])

    def with_fds(self, line):
        """The command line in a shell, natively and through us. {} is the tool."""
        results = []
        for mode in ("native", "hs"):
            cmd, extra = command(mode, ["hstest"], require_fast=False)
            prefix = shlex.join(cmd)
            p = subprocess.run(["bash", "-c", line.replace("{}", prefix)], capture_output=True,
                               env={**BASE_ENV, **extra}, timeout=30)
            results.append(outcome(p))
        return results

    def test_fd_above_2(self):
        root = tempfile.mkdtemp(dir=ROOT)
        path = os.path.join(root, "out")
        native, fast = self.with_fds("{} write-fd 3 3>" + shlex.quote(path) + "; cat " + shlex.quote(path))
        self.assertEqual(native, fast)
        self.assertIn(b"on the fd", fast[1])

    def test_process_substitution(self):
        native, fast = self.with_fds("{} cat <(echo from the shell)")
        self.assertEqual(native, fast)
        self.assertIn(b"from the shell", fast[1])

    def test_fd_above_2_tells_why(self):
        p = subprocess.run(["bash", "-c", shlex.join(command("hs", ["hstest", "argv"])[0]) + " 3</dev/null"],
                           capture_output=True, env={**BASE_ENV, "SPEEDRUNNER_REQUIRE_FAST": "1"}, timeout=30)
        self.assertEqual(p.returncode, EXIT_NOT_FAST)
        self.assertIn(b"fd 3", p.stderr)

    def test_background_job_on_a_terminal_runs_natively(self):
        # An interactive shell: the job is in a group of its own, not the terminal's.
        cmd = shlex.join(command("hs", ["hstest", "argv"])[0])
        code, out = pty_transcript(["bash", "--norc", "-i", "-c", cmd + " < /dev/tty & wait $!; echo rc=$?"],
                                   {**BASE_ENV, "SPEEDRUNNER_REQUIRE_FAST": "1"}, [])
        self.assertIn(b"background job", out)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class AsNatively(Matrix):
    def check_multiprocessing(self, method):
        # Not stderr: how many workers got to print hstest's import-time warning
        # before the pool ended is a race, natively too.
        served_soon(["hstest", "argv"])
        native, fast = run("native", ["hstest", "mp", method]), run("hs", ["hstest", "mp", method])
        self.assertEqual((native.returncode, native.stdout), (0, b"[1, 2]\n"))
        self.assertEqual((fast.returncode, fast.stdout), (0, b"[1, 2]\n"), fast.stderr)

    def test_multiprocessing_spawn(self):
        self.check_multiprocessing("spawn")

    def test_multiprocessing_forkserver(self):
        self.check_multiprocessing("forkserver")

    def test_forked_child_does_not_report_the_end(self):
        served_soon(["hstest", "argv"])
        self.assertSame(["hstest", "fork-exit"])

    def test_signal_handler_set_at_import(self):
        results = []
        env = in_private_daemon_env()
        for mode in ("native", "hs"):
            if mode == "hs":
                wait_ready("hssig", env)
            cmd, extra = command(mode, ["hssig"])
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={**BASE_ENV, **env, **extra})
            self.assertEqual(p.stdout.readline(), b"ready\n")
            time.sleep(0.2)  # out of its print: the handler prints too
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=20)
            results.append((p.returncode, out, err))
        stop("hssig", env)
        self.assertEqual(status("hssig", env), "down")  # the daemon, which loaded it, obeys its own stop
        self.assertEqual(results[0][:2], (42, b"clean up\n"))
        self.assertEqual(results[0], results[1])

    def test_daemon_does_not_run_the_tools_exit_hooks(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {**in_private_daemon_env(root), "HOME": root}
        state = os.path.join(root, "hsstate")
        wait_ready("hsstate", env)
        self.assertEqual(run("hs", ["hsstate", "from-a-run"], env=env).returncode, 0)
        time.sleep(0.2)
        stop("hsstate", env)
        with open(state) as f:
            self.assertEqual(f.read(), "from-a-run")

    def test_printing_more_than_a_pty_holds_at_import(self):
        env = in_private_daemon_env()
        self.addCleanup(stop, "hsnoisy", env)
        native = run("native", ["hsnoisy"], env=env, tty=True)
        # From a terminal, the daemon imports with a pty of its own.
        run("hs", ["hsnoisy"], env=env, tty=True, require_fast=False)
        deadline = time.monotonic() + 30
        while True:
            fast = run("hs", ["hsnoisy"], env=env, tty=True)
            if fast.returncode != EXIT_NOT_FAST or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        # stdout and stderr of an import are replayed one after the other.
        self.assertEqual(fast.returncode, native.returncode)
        self.assertEqual(sorted(fast.stdout.splitlines()), sorted(native.stdout.splitlines()))


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class StuckDaemon(Matrix):
    """A daemon that does not take its clients only costs them a delay."""

    SLOW = """#!{python}
import slowmod
print("served" if False else "ran")
"""

    def slow_tool(self):
        root = tempfile.mkdtemp(dir=ROOT)
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "slowmod.py"), "w") as f:
            f.write("import sys, time\n"
                    "if any('speedrunner_daemon' in a for a in getattr(sys, 'orig_argv', [])):\n"
                    "    time.sleep(6)\n")
        tool = os.path.join(bin_dir, "slowtool")
        python = shutil.which("python3", path=BASE_ENV["PATH"])
        with open(tool, "w") as f:
            f.write(self.SLOW.format(python=python))
        os.chmod(tool, 0o755)
        return {**in_private_daemon_env(root), "PATH": f"{bin_dir}:{BASE_ENV['PATH']}"}

    def test_pipeline_does_not_wait_for_the_load(self):
        env = self.slow_tool()
        self.addCleanup(stop, "slowtool", env)
        full = {**BASE_ENV, **env}
        subprocess.run([HS_BIN, "prewarm", "slowtool"], env=full, check=True)
        time.sleep(0.3)  # its socket is up, and its load under way
        t0 = time.monotonic()
        p = subprocess.run(["bash", "-c", f"{shlex.quote(HS_BIN)} run slowtool -- | cat"],
                           env=full, capture_output=True, timeout=30)
        elapsed = time.monotonic() - t0
        self.assertEqual(p.stdout, b"ran\n")
        self.assertLess(elapsed, 5.0)
        time.sleep(0.1)

    def test_full_queue_does_not_block(self):
        env = in_private_daemon_env()
        wait_ready("hstest", env)
        path = tool_socket("hstest", env)
        stop("hstest", env)
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(path)
            listener.listen(0)
            queued = []
            for _ in range(8):  # up to what the queue takes
                s = socket.socket(socket.AF_UNIX)
                s.setblocking(False)
                try:
                    s.connect(path)
                    queued.append(s)
                except OSError:
                    s.close()
                    break
            t0 = time.monotonic()
            p = run("hs", ["hstest", "argv", "x"], env=env, require_fast=False)
            elapsed = time.monotonic() - t0
            for s in queued:
                s.close()
        self.assertEqual(outcome(p), outcome(run("native", ["hstest", "argv", "x"], env=env)))
        self.assertLess(elapsed, 6.0)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Distribution(Matrix):
    """A release is the client alone: it writes out its built-in daemons and
    prints its zsh hook."""

    REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_daemon_files_others_can_write_are_not_run(self):
        root = tempfile.mkdtemp(dir=ROOT)
        os.makedirs(os.path.join(root, "bin"))
        lone = os.path.join(root, "bin", "speedrunner")
        shutil.copy2(HS_BIN, lone)
        planted = os.path.join(root, "share", "speedrunner")
        os.makedirs(planted)
        ran = os.path.join(root, "planted-ran")
        with open(os.path.join(planted, "speedrunner_daemon.py"), "w") as f:
            f.write(f"open({ran!r}, 'w').close()\n")
        os.chmod(planted, 0o777)
        env = in_private_daemon_env(root)
        full = {**BASE_ENV, **env}
        self.addCleanup(stop, "hstest", env)
        subprocess.run([lone, "prewarm", "hstest"], env=full, check=True)
        wait_ready("hstest", env)
        self.assertFalse(os.path.exists(ran))

    def test_built_in_daemons_are_written_out_for_us_alone(self):
        root = tempfile.mkdtemp(dir=ROOT)
        os.makedirs(os.path.join(root, "bin"))
        lone = os.path.join(root, "bin", "speedrunner")
        shutil.copy2(HS_BIN, lone)
        env = in_private_daemon_env(root)
        full = {**BASE_ENV, **env}
        self.addCleanup(stop, "hstest", env)
        subprocess.run([lone, "prewarm", "hstest"], env=full, check=True, preexec_fn=lambda: os.umask(0))
        daemons = os.path.join(env["XDG_DATA_HOME"], "speedrunner", "daemons")
        wait_ready("hstest", env)
        for d in (daemons, *glob.glob(os.path.join(daemons, "*"))):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode) & 0o022, 0, d)

    @unittest.skipUnless(os.geteuid() == 0, "needs root")
    def test_root_does_not_run_on_a_users_files(self):
        # sudo with HOME kept.
        home = tempfile.mkdtemp(dir=ROOT)
        os.chown(home, 65534, 65534)
        p = run("hs", ["hstest", "argv"], env={"HOME": home, "XDG_DATA_HOME": os.path.join(home, "data")})
        self.assertEqual(p.returncode, EXIT_NOT_FAST)
        self.assertIn(b"another user", p.stderr)

    def test_launcher_that_execs_a_setuid_program_is_not_followed(self):
        sudo = shutil.which("sudo")
        if not sudo or not os.stat(sudo).st_mode & stat.S_ISUID:
            self.skipTest("no setuid sudo")
        root = tempfile.mkdtemp(dir=ROOT)
        tool = os.path.join(root, "gosudo")
        with open(tool, "w") as f:
            f.write(f"#!/bin/sh\nexec {sudo} -V\n")
        os.chmod(tool, 0o755)
        env = {"PATH": f"{root}:{BASE_ENV['PATH']}"}
        self.assertEqual(outcome(run("native", ["gosudo"], env=env)),
                         outcome(run("hs", ["gosudo"], env=env, require_fast=False)))

    def test_lone_binary_runs_its_built_in_daemon(self):
        root = tempfile.mkdtemp(dir=ROOT)
        lone = os.path.join(root, "bin", "speedrunner")
        os.makedirs(os.path.dirname(lone))
        shutil.copy2(HS_BIN, lone)  # no server/ next to it
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_DATA_HOME": os.path.join(root, "data"),
               "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        full = {**BASE_ENV, **env}
        self.addCleanup(stop, "hstest", env)
        subprocess.run([lone, "prewarm", "hstest"], env=full, check=True)
        wait_ready("hstest", env)
        [daemon] = glob.glob(os.path.join(env["XDG_DATA_HOME"], "speedrunner", "daemons", "*",
                                          "speedrunner_daemon.py"))
        with open(daemon, "rb") as f, open(os.path.join(self.REPO, "server", "speedrunner_daemon.py"), "rb") as g:
            self.assertEqual(f.read(), g.read())
        if MACOS:
            running = subprocess.run(["ps", "-axo", "args="], capture_output=True).stdout.splitlines()
        else:
            running = []
            for pid in filter(str.isdigit, os.listdir("/proc")):
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        running.append(f.read())
                except OSError:
                    pass
        self.assertTrue(any(daemon.encode() in c for c in running))
        native = outcome(subprocess.run(["hstest", "argv", "x"], env=full, capture_output=True))
        fast = outcome(subprocess.run([lone, "run", "hstest", "--", "argv", "x"], capture_output=True,
                                      env={**full, "SPEEDRUNNER_REQUIRE_FAST": "1"}))
        self.assertEqual(native, fast)

    def test_demo_measures_the_tools_here_and_stops_its_daemons(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(stop, "aws", env)
        if MACOS:  # not a CI runner's /usr/local/bin, and its dozens of tools
            env["PATH"] = f"{os.environ['HS_PATH']}:/usr/bin:/bin"
        p = subprocess.run([HS_BIN, "demo", "--runs", "1"], capture_output=True, timeout=300,
                           env={**BASE_ENV, **env})
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.decode()
        [aws] = [line for line in out.splitlines() if line.strip().startswith("aws --version")]
        self.assertRegex(aws, r"\d+ ms +\d+ ms +[\d.]+x  yes$")
        self.assertIn("median gain", out)
        self.assertEqual(status("aws", env), "down")

    def test_demo_measures_under_an_exported_native(self):
        root = tempfile.mkdtemp(dir=ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(stop, "aws", env)
        if MACOS:
            env["PATH"] = f"{os.environ['HS_PATH']}:/usr/bin:/bin"
        p = subprocess.run([HS_BIN, "demo", "--runs", "1"], capture_output=True, timeout=300,
                           env={**BASE_ENV, **env, "SPEEDRUNNER_NATIVE": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("median gain", p.stdout.decode())

    def test_init_prints_the_hooks(self):
        for shell in ("zsh", "bash"):
            p = subprocess.run([HS_BIN, "init", shell], capture_output=True)
            with open(os.path.join(self.REPO, "shell", f"speedrunner.{shell}"), "rb") as f:
                self.assertEqual((p.returncode, p.stdout), (0, f.read()), shell)
        self.assertEqual(subprocess.run([HS_BIN, "init", "fish"], capture_output=True).returncode, 2)


def pty_transcript(argv, env, answers, timeout=30):
    """Run argv on a pty, answering each prompt; return (status, transcript)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(argv[0], argv, env)
    out = b""
    deadline = time.monotonic() + timeout
    pending = list(answers)
    try:
        while True:
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL)
                break
            if pending and out.endswith(pending[0][0]):
                # Ruby's getpass prints its prompt before turning echo off: an
                # answer typed at once would be echoed, natively too.
                time.sleep(0.2)
                os.write(fd, pending.pop(0)[1])
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
        # Reap before closing our end: PHP 8.1 closes its streams, then
        # shuts down, and a closed pty would hang it up meanwhile.
        _, status = os.waitpid(pid, 0)
    finally:
        os.close(fd)
    return os.waitstatus_to_exitcode(status), out


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Interactive(Matrix):
    """Brief §5 « Interactif »: prompts, and tools that open /dev/tty."""

    def transcript(self, mode, argv, answers, env=None):
        cmd, extra = command(mode, argv)
        return pty_transcript(cmd, {**BASE_ENV, **(env or {}), **extra}, answers)

    def test_input_and_getpass(self):
        answers = [(b"Name: ", b"alice\n"), (b"Secret: ", b"s3cret\n")]
        native = self.transcript("native", ["hstest", "prompt"], answers)
        self.assertNotIn(b"s3cret", native[1])  # getpass does not echo
        self.assertEqual(native, self.transcript("hs", ["hstest", "prompt"], answers))

    def test_getpass_with_stdin_piped(self):
        # getpass must read the terminal, not the data piped on stdin.
        answers = [(b"Secret: ", b"s3cret\n")]
        out = []
        for mode in ("native", "hs"):
            cmd, extra = command(mode, ["hstest", "secret-then-stdin"])
            out.append(pty_transcript(["bash", "-c", "echo piped | " + shlex.join(cmd)],
                                      {**BASE_ENV, **extra}, answers))
        self.assertIn(b"secret='s3cret' stdin='piped\\n'", out[0][1])
        self.assertEqual(out[0], out[1])

    def test_aws_configure_prompts(self):
        answers = [(b"Access Key ID [None]: ", b"AKIAEXAMPLE\n"),
                   (b"Secret Access Key [None]: ", b"secretexample\n"),
                   (b"region name [None]: ", b"eu-west-3\n"),
                   (b"output format [None]: ", b"json\n")]
        results = []
        for mode in ("native", "hs"):
            home = tempfile.mkdtemp(dir=ROOT)
            env = {"AWS_CONFIG_FILE": os.path.join(home, "config"),
                   "AWS_SHARED_CREDENTIALS_FILE": os.path.join(home, "credentials")}
            code, out = self.transcript(mode, ["aws", "configure"], answers, env)
            files = [read_lines(env["AWS_CONFIG_FILE"]), read_lines(env["AWS_SHARED_CREDENTIALS_FILE"])]
            results.append((code, out, files))
        self.assertEqual(results[0][2][0], ["[default]", "region = eu-west-3", "output = json"])
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
