"""Regression matrix for Node tools (stage N1): nodetest, run through the Node
daemon, must be indistinguishable from the same commands run natively.

Env: as test_matrix.py (HS_PATH must also hold tests/fixture/nodetest/bin).

Run: python3 tests/test_node.py -v
"""
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

import test_matrix as m

HS_BIN = m.HS_BIN
FIXTURE = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixture", "nodetest"))
run, outcome, command = m.run, m.outcome, m.command


def setUpModule():
    if HS_BIN:
        m.wait_ready("nodetest")


def tearDownModule():
    if HS_BIN:
        m.stop("nodetest")
    shutil.rmtree(m.ROOT, ignore_errors=True)


def learn(argv, env=None, timeout=30.0):
    """Runs argv until the daemon learned what it loads and serves it."""
    if not HS_BIN:
        return
    deadline = time.monotonic() + timeout
    for _ in range(3):
        run("hs", argv, env=env, require_fast=False)
        while " learning=" in m.status("nodetest", env) and time.monotonic() < deadline:
            time.sleep(0.05)


def start_nodetty(on_tty, timeout=30.0):
    """A nodetty daemon started from a terminal, or not, that preloads
    lib/color.js: started by a run, waited for without a prewarm (which
    would start one off the terminal)."""
    m.stop("nodetty")
    deadline = time.monotonic() + timeout
    for _ in range(3):
        run("hs", ["nodetty"], tty=on_tty, require_fast=False)
        while not m.status("nodetty").startswith("ready") or " learning=" in m.status("nodetty"):
            if time.monotonic() > deadline:
                raise RuntimeError("nodetty daemon not ready")
            time.sleep(0.05)


class Outputs(m.Matrix):
    def test_stdin_binary_roundtrip(self):
        self.assertSame(["nodetest", "echo-stdin"], input=bytes(range(256)) * 64)

    def test_binary_stdout(self):
        self.assertSame(["nodetest", "binary"])

    def test_interleave_pipes(self):
        self.assertSame(["nodetest", "interleave"])

    def test_usage_error(self):
        rc, _, _ = self.assertSame(["nodetest"])
        self.assertEqual(rc, 2)

    def test_exit_variants(self):
        for kind, expected in [("none", 0), ("code:3", 3), ("code:300", 44), ("exitcode:5", 5),
                               ("throw", 1), ("reject", 1), ("late", 1)]:
            with self.subTest(kind=kind):
                rc, out, err = self.assertSame(["nodetest", "exit", kind])
                self.assertEqual(rc, expected)
                self.assertNotIn(b"speedrunner_daemon", out + err)

    def test_version(self):
        self.assertSame(["nodetest", "version"])


class Terminal(m.Matrix):
    def test_tty_streams(self):
        self.assertSame(["nodetest", "tty"], tty=True)

    def test_non_tty_streams(self):
        self.assertSame(["nodetest", "tty"])

    def test_interleave_tty(self):
        self.assertSame(["nodetest", "interleave"], tty=True)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_terminal_state_read_at_import(self):
        # nodetty's entry checks at import whether its streams are terminals:
        # a daemon started from a terminal serves terminals, and sends a
        # client on pipes to the native run; and the reverse.
        for first, other in ((True, False), (False, True)):
            with self.subTest(started_on_tty=first):
                self.addCleanup(m.stop, "nodetty")
                start_nodetty(first)
                _, out, _ = self.assertSame(["nodetty"], tty=first)
                self.assertIn(b"import " + (b"true" if first else b"false"), out)
                self.assertSame(["nodetty"], tty=other, require_fast=False)
                self.assertEqual(run("hs", ["nodetty"], tty=other).returncode, m.EXIT_NOT_FAST)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_flag_looked_for_at_import(self):
        # lib/color.js looks for --no-color when it loads: a client passing it
        # gets the answer a native run would.
        self.addCleanup(m.stop, "nodetty")
        start_nodetty(True)
        self.assertSame(["nodetty"], tty=True)
        self.assertSame(["nodetty", "--no-color"], tty=True, require_fast=False)
        self.assertEqual(run("hs", ["nodetty", "--no-color"], tty=True).returncode, m.EXIT_NOT_FAST)


class Signals(m.Matrix):
    def run_and_signal(self, mode, sig, to_group):
        cmd, extra = command(mode, ["nodetest", "sleep", "10"])
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **extra}, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        self.assertEqual(p.stdout.readline(), b"ready\n")
        time.sleep(0.3)
        if to_group:
            os.killpg(p.pid, sig)
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


class Context(m.Matrix):
    def test_cwd(self):
        d = tempfile.mkdtemp(dir=m.ROOT)
        self.assertSame(["nodetest", "cwd"], cwd=d, env={"PWD": d})

    def test_umask(self):
        self.assertSame(["nodetest", "umask"], umask=0o027)

    def test_env_and_subprocess(self):
        self.assertSame(["nodetest", "env", "NODETEST_CUSTOM", "HOME", "NOT_SET"],
                        env={"NODETEST_CUSTOM": "custom value"})

    def test_argv(self):
        self.assertSame(["nodetest", "argv", "a b", "", "--x=1"])

    def test_own_pids(self):
        _, out, _ = self.assertSame(["nodetest", "pid"])
        self.assertEqual(json.loads(out), {"pid": True, "ppid": True})


class Learning(m.Matrix):
    def test_lazy_modules(self):
        # Learned and preloaded, or refused and loaded by each run: the same
        # output either way, run after run.
        learn(["nodetest", "lazy"])
        for _ in range(3):
            _, out, _ = self.assertSame(["nodetest", "lazy"])
        self.assertIn(b"printed at import", out)
        if HS_BIN:
            text = m.status("nodetest")
            self.assertGreater(int(m.status_field(text, "learned")), 0, text)
            # lazy-prints, lazy-thread, lazy-argv
            self.assertGreaterEqual(int(m.status_field(text, "blacklisted")), 3, text)

    def test_env_read_at_import(self):
        learn(["nodetest", "lazy"])
        for value in ("one", "two"):
            with self.subTest(value=value):
                argv, env = ["nodetest", "lazy"], {"NODETEST_IMPORT": value}
                self.assertEqual(outcome(run("native", argv, env=env)),
                                 outcome(run("hs", argv, env=env, require_fast=False)))

    def test_flag_read_at_import(self):
        learn(["nodetest", "lazy"])
        argv = ["nodetest", "lazy", "--shout"]
        self.assertEqual(outcome(run("native", argv)), outcome(run("hs", argv, require_fast=False)))


class ExitWrappers(m.Matrix):
    def test_exit_through_wrappers(self):
        # signal-exit (jest) wraps process.emit and process.reallyExit: its
        # listeners must run, and the child must still end (a forked Node
        # cannot tear down), whether it calls process.exit() or just ends.
        for kind in ("code:4", "natural"):
            with self.subTest(kind=kind):
                argv, env = ["nodetest", "wrapped-exit", kind], {"NODETEST_WRAPPER": "1"}
                learn(argv, env)
                rc, out, _ = self.assertSame(argv, env=env)
                self.assertEqual(rc, 4 if kind == "code:4" else 3)
                self.assertIn(b"wrapper saw exit", out)


class Randomness(m.Matrix):
    def test_runs_differ(self):
        a = run("hs", ["nodetest", "random"])
        b = run("hs", ["nodetest", "random"])
        self.assertEqual((a.returncode, b.returncode), (0, 0))
        for i in range(4):
            self.assertNotEqual(a.stdout.splitlines()[i], b.stdout.splitlines()[i])


class Concurrency(m.Matrix):
    def test_ten_parallel(self):
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            results = list(pool.map(lambda _: run("hs", ["nodetest", "random"]), range(10)))
        self.assertEqual([r.returncode for r in results], [0] * 10)
        self.assertEqual(len({r.stdout.splitlines()[0] for r in results}), 10)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_quick_calls_in_a_row_never_hang(self):
        cmd, extra = m.command("hs", ["nodetest", "exit", "code:3"], True)
        for i in range(300):
            p = subprocess.run(cmd, env={**m.BASE_ENV, **extra}, capture_output=True, timeout=10)
            self.assertEqual(p.returncode, 3, f"call {i}: {p.stderr!r}")


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class FailOpen(m.Matrix):
    def test_startup_env_differs(self):
        argv = ["nodetest", "argv"]
        env = {"NODE_OPTIONS": "--no-warnings"}
        native = outcome(run("native", argv, env=env))
        self.assertEqual(native, outcome(run("hs", argv, env=env, require_fast=False)))
        self.assertEqual(run("hs", argv, env=env).returncode, m.EXIT_NOT_FAST)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Freshness(m.Matrix):
    def test_edited_module_is_reread(self):
        # A copy of the fixture whose preloaded module is edited between runs.
        root = tempfile.mkdtemp(dir=m.ROOT)
        shutil.copytree(FIXTURE, os.path.join(root, "nodetest"))
        lib = os.path.join(root, "nodetest", "lib", "lazy-plain.js")
        env = {"PATH": f"{os.path.join(root, 'nodetest', 'bin')}:{m.BASE_ENV['PATH']}"}
        try:
            m.wait_ready("nodetest", env)
            learn(["nodetest", "lazy"], env)
            self.assertIn(b"hello from a lazy module", run("hs", ["nodetest", "lazy"], env=env).stdout)
            with open(lib) as f:
                text = f.read()
            with open(lib, "w") as f:
                f.write(text.replace("hello from", "HELLO FROM"))
            out = run("hs", ["nodetest", "lazy"], env=env, require_fast=False).stdout
            self.assertIn(b"HELLO FROM a lazy module", out)
        finally:
            m.stop("nodetest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(m.Matrix):
    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "nodetest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_daemon_killed_mid_run(self):
        root, env = m.private_daemon("nodetest")
        try:
            daemon = int(m.status_field(m.status("nodetest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker)
            os.kill(daemon, signal.SIGKILL)
            p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0)  # its X came
            self.assertEqual(m.read_lines(marker), ["start", "done"])
            q = run("hs", ["nodetest", "argv"], env=env, require_fast=False)
            self.assertEqual(outcome(q), outcome(run("native", ["nodetest", "argv"])))
            text = m.wait_ready("nodetest", env)
            self.assertNotEqual(int(m.status_field(text, "pid")), daemon)
        finally:
            m.stop("nodetest", env)

    def test_child_lost_is_not_rerun(self):
        root, env = m.private_daemon("nodetest")
        try:
            daemon = int(m.status_field(m.status("nodetest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, child = self.start_hold(env, marker, seconds="5")
            os.kill(daemon, signal.SIGKILL)
            os.kill(child, signal.SIGKILL)
            _, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 125)
            self.assertIn(b"not re-running it", err)
            time.sleep(0.5)
            self.assertEqual(m.read_lines(marker), ["start"])
        finally:
            m.stop("nodetest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Security(m.Matrix):
    """The daemon's own checks, behind the client's (see test_matrix.py)."""

    @unittest.skipUnless(os.getuid() == 0, "needs root to connect as another uid")
    def test_daemon_refuses_peer_of_other_uid(self):
        m.check_peer_uid(self, "nodetest")

    def test_daemon_refuses_bad_runtime_dir(self):
        m.check_daemon_refuses_bad_dir(self, "nodetest")


if __name__ == "__main__":
    unittest.main()
