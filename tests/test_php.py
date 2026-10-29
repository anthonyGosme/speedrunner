"""Regression matrix for PHP tools (stage P1): phptest, composer and
Laravel's artisan, run through the PHP daemon, must be indistinguishable
from the same commands run natively. Also `php script args`, the usual way
to run artisan.

Env: as test_matrix.py (HS_PATH must also hold tests/fixture/phptest/bin),
plus PHP_LARAVEL, a Laravel project directory (optional).

Run: python3 tests/test_php.py -v
"""
import concurrent.futures
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

import test_matrix as m

HS_BIN = m.HS_BIN
LARAVEL = os.environ.get("PHP_LARAVEL")


def php_script(name):
    """name, on the tests' PATH, is a script run by PHP: what speedrunner serves."""
    path = shutil.which(name, path=m.BASE_ENV["PATH"])
    try:
        with open(path or "", "rb") as f:
            return b"php" in f.readline()
    except OSError:
        return False
FIXTURE = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixture", "phptest"))
run, outcome, command = m.run, m.outcome, m.command


def setUpModule():
    if HS_BIN:
        m.wait_ready("phptest")


SCRIPT_DAEMONS = []  # [php, script] whose daemons the tests started


def stop_script(argv, cwd=None):
    subprocess.run([HS_BIN, "stop", *argv], env=m.BASE_ENV, cwd=cwd, capture_output=True)


def tearDownModule():
    if HS_BIN:
        m.stop("phptest")
        for argv, cwd in SCRIPT_DAEMONS:
            stop_script(argv, cwd)
    shutil.rmtree(m.ROOT, ignore_errors=True)


class Outputs(m.Matrix):
    def test_stdin_binary_roundtrip(self):
        self.assertSame(["phptest", "echo-stdin"], input=bytes(range(256)) * 64)

    def test_binary_stdout(self):
        self.assertSame(["phptest", "binary"])

    def test_interleave_pipes(self):
        self.assertSame(["phptest", "interleave"])

    def test_usage_error(self):
        rc, _, _ = self.assertSame(["phptest"])
        self.assertEqual(rc, 2)

    def test_exit_variants(self):
        for kind, expected in [("noarg", 0), ("int:3", 3), ("int:300", 44), ("str:bye", 0),
                               ("throw", 255), ("fatal", 255), ("7", 7)]:
            with self.subTest(kind=kind):
                rc, _, _ = self.assertSame(["phptest", "exit", kind])
                self.assertEqual(rc, expected)

    def test_uncaught_exception_per_error_settings(self):
        # The trace is printed where display_errors and log_errors say, with
        # nothing of the daemon in it. Each setting is a copy of the tool
        # whose shebang passes it, so that both runs use it.
        php = shutil.which("php", path=m.BASE_ENV["PATH"])
        for ini in ("-ddisplay_errors=1", "-ddisplay_errors=stderr", "-dlog_errors=0"):
            with self.subTest(ini=ini):
                root = tempfile.mkdtemp(dir=m.ROOT)
                shutil.copytree(FIXTURE, os.path.join(root, "phptest"))
                script = os.path.join(root, "phptest", "bin", "phptest")
                with open(script) as f:
                    body = f.read().split("\n", 1)[1]
                with open(script, "w") as f:
                    f.write(f"#!{php} {ini}\n" + body)
                env = {"PATH": f"{os.path.dirname(script)}:{m.BASE_ENV['PATH']}"}
                try:
                    if HS_BIN:
                        m.wait_ready("phptest", env)
                    rc, out, err = self.assertSame(["phptest", "exit", "throw"], env=env)
                    self.assertEqual(rc, 255)
                    shown = b"Next RuntimeException: phptest failure" in out + err
                    # Nothing shown at all then, unless errors are displayed
                    # anyway (no php.ini: display_errors defaults to on).
                    display = subprocess.run([php, "-r", "echo ini_get('display_errors');"],
                                             capture_output=True).stdout not in (b"", b"0", b"Off")
                    self.assertEqual(shown, ini != "-dlog_errors=0" or display)
                    self.assertNotIn(b"speedrunner", out + err)
                finally:
                    if HS_BIN:
                        m.stop("phptest", env)

    def test_atexit(self):
        self.assertSame(["phptest", "atexit"])

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    @unittest.skipUnless(php_script("composer"), "composer is not a PHP script here (Alpine: a shell wrapper)")
    def test_composer(self):
        m.wait_ready("composer")
        self.addCleanup(m.stop, "composer")
        self.assertSame(["composer", "--version"], env={"COMPOSER_ALLOW_SUPERUSER": "1"})


class Terminal(m.Matrix):
    def test_tty_streams(self):
        self.assertSame(["phptest", "tty"], tty=True)

    def test_non_tty_streams(self):
        self.assertSame(["phptest", "tty"])

    def test_interleave_tty(self):
        self.assertSame(["phptest", "interleave"], tty=True)


class Signals(m.Matrix):
    def run_and_signal(self, mode, sig, to_group):
        cmd, extra = command(mode, ["phptest", "sleep", "10"])
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
        self.assertSame(["phptest", "cwd"], cwd=d, env={"PWD": d})

    def test_umask(self):
        self.assertSame(["phptest", "umask"], umask=0o027)

    def test_env_getenv_server_and_subprocess(self):
        self.assertSame(["phptest", "env", "PHPTEST_CUSTOM", "HOME", "NOT_SET"],
                        env={"PHPTEST_CUSTOM": "custom value"})

    def test_server_superglobal(self):
        self.assertSame(["phptest", "server"])

    def test_argv(self):
        self.assertSame(["phptest", "argv", "a b", "", "--x=1"])

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_getopt(self):
        m.wait_ready("phpgetopt")
        self.addCleanup(m.stop, "phpgetopt")
        _, out, _ = self.assertSame(["phpgetopt", "-v", "--flag=x", "--version"])
        self.assertIn(b"'flag' => 'x'", out)

    def test_top_level_variables_are_global(self):
        _, out, _ = self.assertSame(["phptest", "globals"])
        self.assertEqual(out, b"global\n")

    def test_signal_dispositions(self):
        self.assertSame(["phptest", "signals"])

    def test_inherited_ignored_signal(self):
        self.assertSame(["phptest", "signals"],
                        preexec=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))


class ScriptForm(m.Matrix):
    """`php script args`: the interpreter on PATH, the script as an argument."""

    def warm(self, argv, cwd=None, timeout=20.0):
        """Start the script's daemon (`status` cannot name a script): run it
        once, then until the fast path takes it."""
        if not HS_BIN:
            return
        key = (argv[:2] if argv[0] == "php" else argv[:1], cwd)
        if key not in SCRIPT_DAEMONS:
            SCRIPT_DAEMONS.append(key)
        run("hs", argv, cwd=cwd, require_fast=False)
        deadline = time.monotonic() + timeout
        while run("hs", argv, cwd=cwd).returncode == m.EXIT_NOT_FAST:
            self.assertLess(time.monotonic(), deadline, argv)
            time.sleep(0.1)

    def assertSame(self, argv, **kw):
        self.warm(argv, kw.get("cwd"))
        return super().assertSame(argv, **kw)

    def test_php_with_relative_script(self):
        self.assertSame(["php", "bin/phptest", "argv", "x"], cwd=FIXTURE)

    def test_php_with_absolute_script(self):
        self.assertSame(["php", os.path.join(FIXTURE, "bin", "phptest"), "server"])

    def test_php_options_run_natively(self):
        argv = ["php", "-r", "echo 1;"]
        self.assertEqual(outcome(run("native", argv)), outcome(run("hs", argv, require_fast=False)))

    @unittest.skipUnless(LARAVEL, "needs PHP_LARAVEL")
    def test_artisan(self):
        for argv in (["php", "artisan", "--version"], ["php", "artisan", "list"],
                     [os.path.join(LARAVEL or "", "artisan"), "list"]):
            with self.subTest(argv=argv):
                self.assertSame(argv, cwd=LARAVEL)


class Randomness(m.Matrix):
    def test_runs_differ(self):
        a = run("hs", ["phptest", "random"])
        b = run("hs", ["phptest", "random"])
        self.assertEqual((a.returncode, b.returncode), (0, 0))
        for i in range(3):
            self.assertNotEqual(a.stdout.splitlines()[i], b.stdout.splitlines()[i])


class Concurrency(m.Matrix):
    def test_ten_parallel(self):
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            results = list(pool.map(lambda _: run("hs", ["phptest", "random"]), range(10)))
        self.assertEqual([r.returncode for r in results], [0] * 10)
        self.assertEqual(len({r.stdout.splitlines()[0] for r in results}), 10)


class Teardown(m.Matrix):
    def test_unclosed_file_flushed_before_exit(self):
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                path = os.path.join(m.ROOT, f"php-unclosed-{mode}")
                p = run(mode, ["phptest", "unclosed", path])
                self.assertEqual(p.returncode, 0)
                with open(path) as f:
                    self.assertEqual(len(f.read()), 100000)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class FailOpen(m.Matrix):
    def test_startup_env_differs(self):
        argv = ["phptest", "argv"]
        env = {"PHP_INI_SCAN_DIR": "/nonexistent"}
        native = outcome(run("native", argv, env=env))
        self.assertEqual(native, outcome(run("hs", argv, env=env, require_fast=False)))
        self.assertEqual(run("hs", argv, env=env).returncode, m.EXIT_NOT_FAST)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Freshness(m.Matrix):
    def test_edited_source_is_reread(self):
        # A copy of the fixture whose class file is edited between runs: OPcache
        # in the daemon revalidates on every include.
        root = tempfile.mkdtemp(dir=m.ROOT)
        shutil.copytree(FIXTURE, os.path.join(root, "phptest"))
        cli = os.path.join(root, "phptest", "src", "Cli.php")
        script = os.path.join(root, "phptest", "bin", "phptest")
        env = {"PATH": f"{os.path.dirname(script)}:{m.BASE_ENV['PATH']}"}
        try:
            self.assertEqual(run("hs", ["phptest", "globals"], env=env, require_fast=False).stdout,
                             b"global\n")
            m.wait_ready("phptest", env)
            self.assertEqual(run("hs", ["phptest", "globals"], env=env).stdout, b"global\n")
            with open(cli) as f:
                text = f.read()
            with open(cli, "w") as f:
                f.write(text.replace("'global' : 'local'", "'GLOBAL' : 'local'"))
            self.assertEqual(run("hs", ["phptest", "globals"], env=env, require_fast=False).stdout,
                             b"GLOBAL\n")
        finally:
            m.stop("phptest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(m.Matrix):
    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "phptest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_daemon_killed_mid_run(self):
        root, env = m.private_daemon("phptest")
        try:
            daemon = int(m.status_field(m.status("phptest", env), "pid"))
            marker = os.path.join(root, "marker")
            p, _ = self.start_hold(env, marker)
            os.kill(daemon, signal.SIGKILL)
            out, _ = p.communicate(timeout=30)
            # No exit code report in PHP: with the daemon gone, the wait status is lost.
            self.assertIn(p.returncode, (0, 125))
            self.assertEqual(m.read_lines(marker), ["start", "done"])
            q = run("hs", ["phptest", "argv"], env=env, require_fast=False)
            self.assertEqual(outcome(q), outcome(run("native", ["phptest", "argv"])))
            text = m.wait_ready("phptest", env)
            self.assertNotEqual(int(m.status_field(text, "pid")), daemon)
        finally:
            m.stop("phptest", env)

    def test_child_lost_is_not_rerun(self):
        root, env = m.private_daemon("phptest")
        try:
            daemon = int(m.status_field(m.status("phptest", env), "pid"))
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
            m.stop("phptest", env)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Interactive(m.Matrix):
    def test_prompt_with_hidden_input(self):
        answers = [(b"Name: ", b"alice\n"), (b"Secret: ", b"s3cret\n")]
        transcripts = []
        for mode in ("native", "hs"):
            cmd, extra = command(mode, ["phptest", "prompt"])
            transcripts.append(m.pty_transcript(cmd, {**m.BASE_ENV, **extra}, answers))
        self.assertNotIn(b"s3cret", transcripts[0][1])
        self.assertEqual(transcripts[0], transcripts[1])


if __name__ == "__main__":
    unittest.main()
