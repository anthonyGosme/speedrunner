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
import glob
import json
import os
import pty
import select
import socket
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HS_BIN = os.environ.get("HS_BIN")
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


ROOT = tempfile.mkdtemp(prefix="hs-matrix-")
BASE_ENV = make_env(ROOT)


def command(mode, argv, require_fast=True):
    """Return (argv, extra env) for a native or speedrunner run."""
    if mode == "native" or not HS_BIN:
        return list(argv), {}
    extra = {"SPEEDRUNNER_REQUIRE_FAST": "1"} if require_fast else {}
    return [HS_BIN, "run", argv[0], "--", *argv[1:]], extra


def run(mode, argv, input=b"", cwd=None, env=None, tty=False, umask=None,
        require_fast=True, preexec=None, pipe_to=None):
    cmd, extra = command(mode, argv, require_fast)
    full_env = {**BASE_ENV, **(env or {}), **extra}
    if pipe_to:
        cmd = ["bash", "-c", f'{shlex.join(cmd)} | {pipe_to}; echo "status=${{PIPESTATUS[0]}}" >&2']
    if tty:
        # script(1) gives the command a pty of a known size.
        cmd = ["script", "-qec", "stty cols 123 rows 45; " + shlex.join(cmd), "/dev/null"]

    def setup():
        if umask is not None:
            os.umask(umask)
        if preexec is not None:
            preexec()

    return subprocess.run(cmd, input=input, capture_output=True, cwd=cwd,
                          env=full_env, preexec_fn=setup, timeout=60)


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


def wait_ready(tool, env=None, timeout=30.0):
    deadline = time.monotonic() + timeout
    while True:
        text = status(tool, env)
        if text.startswith("ready"):
            return text
        if text == "down":
            subprocess.run([HS_BIN, "prewarm", tool], env={**BASE_ENV, **(env or {})}, check=True)
        if time.monotonic() > deadline:
            raise RuntimeError(f"{tool} daemon not ready: {text!r}")
        time.sleep(0.1)


def stop(tool, env=None, timeout=10.0):
    subprocess.run([HS_BIN, "stop", tool], env={**BASE_ENV, **(env or {})}, capture_output=True)
    deadline = time.monotonic() + timeout
    while status(tool, env) != "down" and time.monotonic() < deadline:
        time.sleep(0.05)


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
        wait_ready("hsrun")
        _, out, _ = self.assertSame(["hsrun", "a", "b"])
        self.assertEqual(out, b"autorun: ['a', 'b']\n")

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
        self.assertEqual((p.returncode, p.stderr), (2, b"usage: srr <tool> [args...]\n"))


class Teardown(Matrix):
    def test_unclosed_file_flushed_before_exit(self):
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                path = os.path.join(ROOT, f"unclosed-{mode}")
                p = run(mode, ["hstest", "unclosed", path])
                self.assertEqual(p.returncode, 0)
                with open(path) as f:
                    self.assertEqual(len(f.read()), 100000)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class FailOpen(Matrix):
    """Inputs the preloaded state cannot honour must fall back to native."""

    def check_falls_back(self, argv, env):
        native = outcome(run("native", argv, env=env))
        self.assertEqual(native, outcome(run("hs", argv, env=env, require_fast=False)))
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

    def learn(self, tool, env, argvs, timeout=30.0):
        """Run argvs through speedrunner; wait until the daemon has applied what it learned."""
        gen = int(status_field(status(tool, env), "gen"))
        for argv in argvs:
            run("hs", argv, env=env)
        deadline = time.monotonic() + timeout
        while int(status_field(status(tool, env), "gen") or -1) <= gen:
            if time.monotonic() > deadline:
                self.fail(f"nothing applied: {status(tool, env)!r}")
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
        [model] = glob.glob(os.path.join(os.path.dirname(os.path.dirname(aws)), "lib", "python3*",
                                         "site-packages", "awscli", "botocore", "data", "sts",
                                         "2011-06-15", "service-2.json"))
        data_path = tempfile.mkdtemp(dir=ROOT)
        copy = os.path.join(data_path, "sts", "2011-06-15", "service-2.json")
        os.makedirs(os.path.dirname(copy))
        with open(model, "rb") as f:
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
            self.learn("hstest", env, [["hstest", "hook"]])
            _, out, _ = self.assertSame(["hstest", "hook"], env=env)
            self.assertEqual(out, b"hooked\n")
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
        # Brief §8, current choice: the child is left to finish, once.
        root, env = private_daemon("hstest")
        try:
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker)
            p.kill()
            p.wait()
            deadline = time.monotonic() + 10
            while read_lines(marker) != ["start", "done"] and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(read_lines(marker), ["start", "done"])
        finally:
            stop("hstest", env)

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
            self.assertEqual(run("hs", ["hsedit"], env=env).returncode, EXIT_NOT_FAST)
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

    @unittest.skipUnless(os.getuid() == 0, "needs root to listen as another uid")
    def test_socket_served_by_other_uid(self):
        root, env = private_daemon("hstest")
        stop("hstest", env)
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


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Distribution(Matrix):
    """A release is the client alone: it writes out its built-in daemons and
    prints its zsh hook."""

    REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

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
        p = subprocess.run([HS_BIN, "demo", "--runs", "1"], capture_output=True, timeout=300,
                           env={**BASE_ENV, **env})
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.decode()
        [aws] = [line for line in out.splitlines() if line.strip().startswith("aws --version")]
        self.assertRegex(aws, r"\d+ ms +\d+ ms +[\d.]+x  yes$")
        self.assertIn("median gain", out)
        self.assertEqual(status("aws", env), "down")

    def test_init_zsh_prints_the_hook(self):
        p = subprocess.run([HS_BIN, "init", "zsh"], capture_output=True)
        with open(os.path.join(self.REPO, "shell", "speedrunner.zsh"), "rb") as f:
            self.assertEqual((p.returncode, p.stdout), (0, f.read()))
        self.assertEqual(subprocess.run([HS_BIN, "init", "bash"], capture_output=True).returncode, 2)


def pty_transcript(argv, env, answers, timeout=30):
    """Run argv on a pty, answering each prompt; return (status, transcript)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(argv[0], argv, env)
    out = b""
    deadline = time.monotonic() + timeout
    pending = list(answers)
    try:
        while time.monotonic() < deadline:
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
    finally:
        os.close(fd)
    _, status = os.waitpid(pid, 0)
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
