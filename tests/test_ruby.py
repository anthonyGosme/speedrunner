"""Regression matrix for a Ruby tool (stage R1): rbtest, a RubyGems binstub
like rubocop's, run through the Ruby daemon must be indistinguishable from
rbtest run natively.

Env: as test_matrix.py (HS_PATH must also hold the rbtest binstub), plus
  RB_GEM_HOME       GEM_HOME where tests/fixture/rbtest is installed.

Run: python3 tests/test_ruby.py -v
"""
import concurrent.futures
import glob
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

import test_matrix as m

m.BASE_ENV.update(GEM_HOME=os.environ["RB_GEM_HOME"], GEM_PATH=os.environ["RB_GEM_HOME"])
HS_BIN = m.HS_BIN
run, outcome, command = m.run, m.outcome, m.command


def setUpModule():
    if HS_BIN:
        m.wait_ready("rbtest")


def tearDownModule():
    if HS_BIN:
        m.stop("rbtest")
    shutil.rmtree(m.ROOT, ignore_errors=True)


class Outputs(m.Matrix):
    def test_stdin_binary_roundtrip(self):
        self.assertSame(["rbtest", "echo-stdin"], input=bytes(range(256)) * 64)

    def test_binary_stdout(self):
        self.assertSame(["rbtest", "binary"])

    def test_interleave_pipes(self):
        self.assertSame(["rbtest", "interleave"])

    def test_usage_error(self):
        rc, _, _ = self.assertSame(["rbtest"])
        self.assertEqual(rc, 2)

    def test_exit_variants(self):
        for kind, expected in [("noarg", 0), ("true", 0), ("false", 1), ("int:3", 3),
                               ("int:300", 44), ("abort:bye", 1), ("bang:5", 5),
                               ("raise", 1), ("raise-escapes", 1), ("raise-message", 1), ("7", 7)]:
            with self.subTest(kind=kind):
                rc, _, _ = self.assertSame(["rbtest", "exit", kind])
                self.assertEqual(rc, expected)

    def test_atexit(self):
        self.assertSame(["rbtest", "atexit"])

    def test_broken_pipe(self):
        _, _, err = self.assertSame(["rbtest", "pending-output"], pipe_to="true")
        self.assertIn(b"status=", err)


class Terminal(m.Matrix):
    def test_tty_streams(self):
        self.assertSame(["rbtest", "tty"], tty=True)

    def test_non_tty_streams(self):
        self.assertSame(["rbtest", "tty"])

    def test_interleave_tty(self):
        self.assertSame(["rbtest", "interleave"], tty=True)

    def test_escaped_message_on_tty(self):
        # Highlighted, each line of the message in its own escape codes.
        for kind in ("raise-escapes", "raise-message"):
            with self.subTest(kind=kind):
                self.assertSame(["rbtest", "exit", kind], tty=True)

    @unittest.skipUnless(m.HS_BIN, "needs the speedrunner client")
    def test_terminal_state_read_at_import(self):
        # rbtty's entry module notes at import whether its streams are
        # terminals: a daemon started from a terminal serves terminals, and
        # sends a client on pipes to the native run; and the reverse.
        for first, other in ((True, False), (False, True)):
            with self.subTest(started_on_tty=first):
                m.stop("rbtty")
                self.addCleanup(m.stop, "rbtty")
                run("hs", ["rbtty"], tty=first, require_fast=False)  # starts the daemon
                m.wait_ready("rbtty")
                _, out, _ = self.assertSame(["rbtty"], tty=first)
                self.assertIn(b"live " + (b"[true, true, true]" if first else b"[false, false, false]"), out)
                _, out, _ = self.assertSame(["rbtty"], tty=other, require_fast=False)
                self.assertIn(b"live " + (b"[true, true, true]" if other else b"[false, false, false]"), out)
                self.assertEqual(run("hs", ["rbtty"], tty=other).returncode, m.EXIT_NOT_FAST)


class Signals(m.Matrix):
    def run_and_signal(self, mode, sig, to_group):
        cmd, extra = command(mode, ["rbtest", "sleep", "10"])
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **extra}, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        self.assertEqual(p.stdout.readline(), b"ready\n")
        time.sleep(0.3)  # into sleep(), so the backtrace line is stable
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
        self.assertSame(["rbtest", "cwd"], cwd=d, env={"PWD": d})

    def test_umask(self):
        self.assertSame(["rbtest", "umask"], umask=0o027)

    def test_env_ruby_c_and_subprocess(self):
        self.assertSame(["rbtest", "env", "RBTEST_IMPORTED", "RBTEST_CUSTOM", "HOME"],
                        env={"RBTEST_CUSTOM": "custom value"})

    def test_argv_and_paths(self):
        self.assertSame(["rbtest", "argv", "a b", "", "--x=1"])

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_file_that_runs_the_program_when_required(self):
        m.wait_ready("rbrun")
        self.addCleanup(m.stop, "rbrun")
        run("hs", ["rbrun", "warm"], require_fast=False)  # may restart the daemon once
        m.wait_ready("rbrun")
        _, out, _ = self.assertSame(["rbrun", "a", "b"])
        self.assertEqual(out, b'autorun: ["a", "b"]\n')

    def test_signal_dispositions(self):
        self.assertSame(["rbtest", "signals"])

    def test_inherited_ignored_signal(self):
        self.assertSame(["rbtest", "signals"],
                        preexec=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))


class Randomness(m.Matrix):
    def test_runs_differ(self):
        a = run("hs", ["rbtest", "random"])
        b = run("hs", ["rbtest", "random"])
        self.assertEqual((a.returncode, b.returncode), (0, 0))
        self.assertNotEqual(a.stdout.splitlines()[0], b.stdout.splitlines()[0])
        self.assertNotEqual(a.stdout.splitlines()[1], b.stdout.splitlines()[1])


class Concurrency(m.Matrix):
    def test_ten_parallel(self):
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            results = list(pool.map(lambda _: run("hs", ["rbtest", "random"]), range(10)))
        self.assertEqual([r.returncode for r in results], [0] * 10)
        self.assertEqual(len({r.stdout.splitlines()[0] for r in results}), 10)


class Teardown(m.Matrix):
    def test_unclosed_file_flushed_before_exit(self):
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                path = os.path.join(m.ROOT, f"rb-unclosed-{mode}")
                p = run(mode, ["rbtest", "unclosed", path])
                self.assertEqual(p.returncode, 0)
                with open(path) as f:
                    self.assertEqual(len(f.read()), 100000)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class FailOpen(m.Matrix):
    def check_falls_back(self, argv, env):
        native = outcome(run("native", argv, env=env))
        self.assertEqual(native, outcome(run("hs", argv, env=env, require_fast=False)))
        self.assertEqual(run("hs", argv, env=env).returncode, m.EXIT_NOT_FAST)

    def test_load_time_env_differs(self):
        self.check_falls_back(["rbtest", "env", "RBTEST_IMPORTED"], {"RBTEST_IMPORTED": "0"})

    def test_env_read_at_load_differs(self):
        self.check_falls_back(["rbtest", "frozen"], {"RBTEST_FROZEN": "client value"})

    def test_startup_env_differs(self):
        self.check_falls_back(["rbtest", "argv"], {"RUBYOPT": "-W0"})

    def test_gem_version_argument(self):
        # The binstub picks a gem version from `_x.y.z_`: the daemon has one.
        self.check_falls_back(["rbtest", "_0.1.0_", "argv"], {})


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Preload(m.Matrix):
    """Stage R2: requires learned from earlier runs, vetted by a probe."""

    def daemon(self, levels, **extra):
        root = tempfile.mkdtemp(dir=m.ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
               "XDG_CACHE_HOME": os.path.join(root, "cache"),
               "SPEEDRUNNER_PRELOAD": levels, **extra}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        m.wait_ready("rbtest", env)
        return env

    def learn(self, env, argvs, timeout=30.0):
        gen = int(m.status_field(m.status("rbtest", env), "gen"))
        for argv in argvs:
            run("hs", argv, env=env)
        deadline = time.monotonic() + timeout
        while int(m.status_field(m.status("rbtest", env), "gen") or -1) <= gen:
            if time.monotonic() > deadline:
                self.fail(f"nothing applied: {m.status('rbtest', env)!r}")
            time.sleep(0.1)

    def cache(self, env):
        [path] = glob.glob(os.path.join(env["XDG_CACHE_HOME"], "speedrunner", "rbtest-*.json"))
        with open(path) as f:
            return json.load(f)

    def test_levels_match_native(self):
        argvs = [["rbtest", "argv", "x"], ["rbtest", "lazy"], ["rbtest", "exit", "raise"],
                 ["rbtest", "env", "RBTEST_IMPORTED"]]
        for levels in ("1", "2", "1,2"):
            with self.subTest(levels=levels):
                env = self.daemon(levels)
                try:
                    if "2" in levels:
                        self.learn(env, argvs)
                    for argv in argvs:
                        self.assertSame(argv, env=env)
                finally:
                    m.stop("rbtest", env)

    def test_learned_requires_vetted(self):
        env = self.daemon("1,2")
        try:
            self.learn(env, [["rbtest", "lazy"]])
            self.assertTrue(m.status("rbtest", env).startswith("ready"))
            cache = self.cache(env)
            learned = lambda name, where: any(f.endswith(f"/rbtest/{name}.rb") for f in where)
            self.assertTrue(learned("lazy_plain", cache["modules"]), cache)
            self.assertTrue(learned("lazy_env", cache["modules"]), cache)
            self.assertTrue(learned("lazy_thread", cache["blacklist"]), cache)
            self.assertTrue(learned("lazy_print", cache["blacklist"]), cache)
            self.assertTrue(learned("lazy_argv", cache["blacklist"]), cache)
            self.assertSame(["rbtest", "lazy"], env=env)
            # lazy_env keeps RBTEST_LAZY from load time: another value runs natively.
            lazy = {**env, "RBTEST_LAZY": "client value"}
            native = outcome(run("native", ["rbtest", "lazy"], env=lazy))
            self.assertEqual(native, outcome(run("hs", ["rbtest", "lazy"], env=lazy,
                                                 require_fast=False)))
            self.assertEqual(run("hs", ["rbtest", "lazy"], env=lazy).returncode, m.EXIT_NOT_FAST)
        finally:
            m.stop("rbtest", env)

    def test_learned_set_survives_restart(self):
        env = self.daemon("1,2")
        try:
            self.learn(env, [["rbtest", "lazy"]])
            learned = int(m.status_field(m.status("rbtest", env), "learned"))
            self.assertGreater(learned, 0)
            m.stop("rbtest", env)
            text = m.wait_ready("rbtest", env)
            self.assertEqual(int(m.status_field(text, "learned")), learned)
            self.assertEqual(m.status_field(text, "gen"), "0")
        finally:
            m.stop("rbtest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(m.Matrix):
    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "rbtest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_daemon_killed_mid_run(self):
        root, env = m.private_daemon("rbtest")
        try:
            daemon = int(m.status_field(m.status("rbtest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker)
            os.kill(daemon, signal.SIGKILL)
            out, _ = p.communicate(timeout=30)
            self.assertEqual((p.returncode, out), (0, b"done\n"))
            self.assertEqual(m.read_lines(marker), ["start", "done"])
            text = None
            q = run("hs", ["rbtest", "argv"], env=env, require_fast=False)
            self.assertEqual(outcome(q), outcome(run("native", ["rbtest", "argv"])))
            text = m.wait_ready("rbtest", env)
            self.assertNotEqual(int(m.status_field(text, "pid")), daemon)
        finally:
            m.stop("rbtest", env)

    def test_child_lost_after_daemon_death_is_not_rerun(self):
        root, env = m.private_daemon("rbtest")
        try:
            daemon = int(m.status_field(m.status("rbtest", env), "pid"))
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
            m.stop("rbtest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Interactive(m.Matrix):
    def transcript(self, mode, argv, answers, shell=None):
        cmd, extra = command(mode, argv)
        if shell:
            cmd = ["bash", "-c", shell + shlex.join(cmd)]
        return m.pty_transcript(cmd, {**m.BASE_ENV, **extra}, answers)

    def test_gets_and_getpass(self):
        answers = [(b"Name: ", b"alice\n"), (b"Secret: ", b"s3cret\n")]
        native = self.transcript("native", ["rbtest", "prompt"], answers)
        self.assertNotIn(b"s3cret", native[1])
        self.assertEqual(native, self.transcript("hs", ["rbtest", "prompt"], answers))

    def test_getpass_with_stdin_piped(self):
        answers = [(b"Secret: ", b"s3cret\n")]
        native = self.transcript("native", ["rbtest", "secret-then-stdin"], answers,
                                 shell="echo piped | ")
        self.assertIn(b'secret="s3cret" stdin="piped\\n"', native[1])
        self.assertEqual(native, self.transcript("hs", ["rbtest", "secret-then-stdin"],
                                                 answers, shell="echo piped | "))


if __name__ == "__main__":
    unittest.main()
