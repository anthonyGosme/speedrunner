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


class AsNatively(m.Matrix):
    def test_forked_child_does_not_report_the_end(self):
        m.served_soon(["rbtest", "argv"])
        self.assertSame(["rbtest", "fork-exit"])

    def test_time_zone(self):
        m.served_soon(["rbtest", "argv"])
        self.assertSame(["rbtest", "tz"], env={"TZ": "Asia/Tokyo"})

    def test_broken_pipe(self):
        m.served_soon(["rbtest", "argv"])
        rc, out, err = self.assertSame(["rbtest", "big"], pipe_to="head -1")
        self.assertIn(b"status=141", err)
        self.assertNotIn(b"EPIPE", err)

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_daemon_does_not_save_what_it_loaded(self):
        root = tempfile.mkdtemp(dir=m.ROOT)
        env = {**m.in_private_daemon_env(root), "HOME": root}
        m.wait_ready("rbstate", env)
        self.assertEqual(run("hs", ["rbstate", "from-a-run"], env=env).returncode, 0)
        time.sleep(0.2)
        m.stop("rbstate", env)
        with open(os.path.join(root, "rbstate")) as f:
            self.assertEqual(f.read(), "from-a-run")

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_printing_more_than_a_pty_holds_as_it_loads(self):
        env = m.in_private_daemon_env()
        self.addCleanup(m.stop, "rbnoisy", env)
        native = run("native", ["rbnoisy"], env=env, tty=True)
        run("hs", ["rbnoisy"], env=env, tty=True, require_fast=False)
        deadline = time.monotonic() + 30
        while True:
            fast = run("hs", ["rbnoisy"], env=env, tty=True)
            if fast.returncode != m.EXIT_NOT_FAST or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        self.assertEqual(fast.returncode, native.returncode)
        self.assertEqual(sorted(fast.stdout.splitlines()), sorted(native.stdout.splitlines()))

    def test_trap_set_as_it_loads(self):
        results = []
        for mode in ("native", "hs"):
            if mode == "hs" and HS_BIN:
                m.wait_ready("rbsig")
                self.addCleanup(m.stop, "rbsig")
            cmd, extra = command(mode, ["rbsig"])
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={**m.BASE_ENV, **extra})
            self.assertEqual(p.stdout.readline(), b"ready\n")
            time.sleep(0.2)  # out of its print: the handler prints too
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=20)
            results.append((p.returncode, out, err))
        self.assertEqual(results[0], (42, b"clean up\n", b""))
        self.assertEqual(results[0], results[1])
        if HS_BIN:  # and the daemon, which loaded it, still obeys its own stop
            m.stop("rbsig")
            self.assertEqual(m.status("rbsig"), "down")


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
        m.served_soon(["rbrun", "warm"])
        _, out, _ = self.assertSame(["rbrun", "a", "b"])
        self.assertEqual(out, b'autorun: ["a", "b"]\n')

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_file_that_starts_a_thread_when_required(self):
        # Forking would lose the thread: the daemon skips the file, and
        # still serves the tool.
        m.wait_ready("rbthread")
        self.addCleanup(m.stop, "rbthread")
        run("hs", ["rbthread"], require_fast=False)  # may restart the daemon once
        m.served_soon(["rbthread"])
        _, out, _ = self.assertSame(["rbthread"])
        self.assertEqual(out, b"threaded\n")

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
    @unittest.skipUnless(m.HS_BIN, "needs the speedrunner client")
    def test_child_missing_its_go_says_so(self):
        m.check_child_says_it_missed_its_go(self, "rbtest")

    @unittest.skipUnless(m.HS_BIN, "needs the speedrunner client")
    def test_signal_before_the_go(self):
        m.check_child_dies_of_a_signal_before_its_go(self, "rbtest")

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
@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
@unittest.skipUnless(m.MACOS, "CoreFoundation is macOS's")
class CoreFoundation(m.Matrix):
    """macOS, as test_matrix.CoreFoundation, for Ruby. Also: Ruby always has
    a second thread, after which the Objective-C runtime refuses a forked
    child its classes, unless the daemon allowed it."""

    def test_tool_using_it_at_run_time_is_served(self):
        for _ in range(3):
            _, out, _ = self.assertSame(["rbtest", "proxies"])
            self.assertEqual(out, b"proxies true\n")

    def test_entry_file_using_it_is_skipped(self):
        root = tempfile.mkdtemp(dir=m.ROOT)
        env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"), "XDG_CACHE_HOME": os.path.join(root, "cache")}
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        self.addCleanup(m.stop, "rbcf", env)
        run("hs", ["rbcf"], env=env, require_fast=False)  # starts the daemon, which skips cfmain
        m.served_soon(["rbcf"], env=env)
        for _ in range(3):
            _, out, _ = self.assertSame(["rbcf"], env=env)
            self.assertEqual(out, b"cf main true\n")
        [skip] = glob.glob(os.path.join(env["XDG_CACHE_HOME"], "speedrunner", "rbcf.skip-*.json"))
        with open(skip) as f:
            # A run loop may also start a thread: refused either way.
            [why] = json.load(f).values()
            self.assertIn(why, ("uses CoreFoundation at load", "starts a thread at load"))

    def test_learned_file_using_it_is_blacklisted(self):
        env = Preload.daemon(self, "1,2")
        try:
            Preload.learn(self, env, [["rbtest", "lazy-cf"]])
            self.assertTrue(m.status("rbtest", env).startswith("ready"))
            cache = Preload.cache(self, env)
            [cf] = [f for f in cache["blacklist"] if f.endswith("/rbtest/cf.rb")]
            self.assertIn(cache["blacklist"][cf], ("uses CoreFoundation at load", "starts a thread at load"))
            self.assertTrue(any(f.endswith("/rbtest/lazy_plain.rb") for f in cache["modules"]))
            for _ in range(3):
                _, out, _ = self.assertSame(["rbtest", "lazy-cf"], env=env)
                self.assertEqual(out, b"proxies true\n")
        finally:
            m.stop("rbtest", env)


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

    def test_project_files_are_not_learned(self):
        # As rspec in one project, then another: a file the tool requires
        # from the project (a spec_helper) is the project's, never the
        # tool's to preload.
        env = self.daemon("1,2")
        projects = []
        for name in ("alpha", "beta"):
            d = tempfile.mkdtemp(dir=m.ROOT)
            with open(os.path.join(d, "projmod.rb"), "w") as f:
                f.write(f"($projmods ||= []) << {name!r}\n")
            projects.append((d, name))
        try:
            for _ in range(2):
                run("hs", ["rbtest", "project"], cwd=projects[0][0], env=env)
            self.learn(env, [["rbtest", "lazy"]])
            for d, name in projects:
                with self.subTest(project=name):
                    _, out, _ = self.assertSame(["rbtest", "project"], cwd=d, env=env)
                    self.assertEqual(out, b'["%s"]\n' % name.encode())
            self.assertFalse([f for f in self.cache(env)["modules"] if f.endswith("/projmod.rb")])
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
            # Refused for loading lazy_argv, which the probe did not run again.
            why = next(w for f, w in cache["blacklist"].items() if f.endswith("/rbtest/lazy_uses_argv.rb"))
            self.assertRegex(why, r"\Aloads .*/rbtest/lazy_argv\.rb, refused")
            self.assertSame(["rbtest", "lazy"], env=env)
            # lazy_env keeps RBTEST_LAZY from load time: another value runs natively.
            lazy = {**env, "RBTEST_LAZY": "client value"}
            native = outcome(run("native", ["rbtest", "lazy"], env=lazy))
            self.assertEqual(native, outcome(run("hs", ["rbtest", "lazy"], env=lazy,
                                                 require_fast=False)))
            self.assertEqual(run("hs", ["rbtest", "lazy"], env=lazy).returncode, m.EXIT_NOT_FAST)
        finally:
            m.stop("rbtest", env)

    def test_learned_file_that_needs_a_refused_one(self):
        # lazy_reopen.rb reopens a module through an alias that lazy_alias.rb,
        # refused, defines: preloaded alone, it would define another module.
        env = self.daemon("1,2")
        try:
            self.learn(env, [["rbtest", "reopen"]])
            cache = self.cache(env)
            why = next(w for f, w in cache["blacklist"].items() if f.endswith("/rbtest/lazy_reopen.rb"))
            self.assertIn("other namespaces than natively", why)
            _, out, _ = self.assertSame(["rbtest", "reopen"], env=env)
            self.assertEqual(out, b"true\n")
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
class ImportTimeReads(m.ImportTimeReadChecks, m.LockAtLoadChecks, m.LoadDirChecks, m.Matrix):
    LIB = "hsread_lib.rb"
    LAZY = "hsread_lazy.rb"
    LOCK_BODY = ("LOCK_FILE = File.open(ENV['HS_CFG'], 'w')\n"
                 "unless LOCK_FILE.flock(File::LOCK_EX | File::LOCK_NB)\n"
                 "  puts 'BUSY'\n"
                 "  exit 3\n"
                 "end\n")
    LOCK_MAIN = "def main\n  sleep(ENV.fetch('HS_HOLD', '0').to_f)\n  puts 'held'\nend\n"
    DIR_HEAD = "require 'fileutils'\nrequire 'tmpdir'\n"
    DIR_TEMP = "DIR = Dir.mktmpdir('hs-', ENV['HS_DIR'])\n"
    DIR_OWN = "DIR = File.join(ENV['HS_DIR'], \"own-#{Process.pid}\")\nFileUtils.mkdir_p(DIR)\n"
    DIR_RM = "at_exit { FileUtils.remove_entry(DIR, true) }\n"
    DIR_HOLD = "  sleep(ENV.fetch('HS_HOLD', '0').to_f)\n"
    DIR_MAIN = ("def main\n" + DIR_HOLD +
                "  if ENV['HS_CLEAR']\n    FileUtils.rm_rf(DIR)\n    puts 'cleared'\n    return\n  end\n"
                "  path = File.join(DIR, \"f#{Process.pid}\")\n"
                "  File.write(path, '')\n  File.unlink(path)\n  puts 'ok'\nend\n")
    LAZY_DIR = DIR_HEAD + DIR_TEMP + DIR_RM + "HSREAD_LAZY = 'ok'\n"
    SOURCES = {
        "dir_temp": DIR_HEAD + DIR_TEMP + DIR_RM + DIR_MAIN,
        "dir_own": DIR_HEAD + DIR_OWN + "at_exit { FileUtils.rm_rf(DIR) }\n" + DIR_MAIN,
        "dir_data": DIR_HEAD + DIR_TEMP + "File.write(File.join(DIR, 'data'), '')\n" + DIR_RM +
                    "def main\n" + DIR_HOLD + "  puts(File.exist?(File.join(DIR, 'data')) ? 'ok' : 'GONE')\nend\n",
        "dir_gone": DIR_HEAD + "Dir.mktmpdir('hs-', ENV['HS_DIR']) { }\ndef main = puts('ok')\n",
        "dir_at_run": DIR_HEAD + "def main\n  dir = Dir.mktmpdir('hs-', ENV['HS_DIR'])\n"
                      "  at_exit { FileUtils.rm_rf(dir) }\n  puts 'ok'\nend\n",
        "temp_rm_rf": DIR_HEAD + DIR_TEMP + "at_exit { FileUtils.rm_rf(DIR) }\n" + DIR_MAIN,
        "temp_rm_r": DIR_HEAD + DIR_TEMP + "at_exit { FileUtils.rm_r(DIR) }\n" + DIR_MAIN,
        "temp_secure": DIR_HEAD + DIR_TEMP + "at_exit { FileUtils.remove_entry_secure(DIR) }\n" + DIR_MAIN,
        "temp_pathname": DIR_HEAD + "require 'pathname'\n" + DIR_TEMP + "at_exit { Pathname(DIR).rmtree }\n" + DIR_MAIN,
        "temp_by_hand": DIR_HEAD + DIR_TEMP +
                        "at_exit do\n  Dir.children(DIR).each { |e| File.delete(File.join(DIR, e)) }\n"
                        "  Dir.rmdir(DIR)\nend\n" + DIR_MAIN,
        "temp_late_require": "require 'tmpdir'\nDIR = Dir.mktmpdir('hs-', ENV['HS_DIR'])\n"
                             "at_exit do\n  require 'fileutils'\n  FileUtils.rm_rf(DIR)\nend\n"
                             "require 'fileutils'\n" + DIR_MAIN,
        "own_mkdir": DIR_HEAD + "DIR = File.join(ENV['HS_DIR'], \"own-#{Process.pid}\")\nDir.mkdir(DIR)\n"
                     "at_exit { FileUtils.rm_rf(DIR) }\n" + DIR_MAIN,
        "flock": LOCK_BODY + LOCK_MAIN,
        "flock_hidden": "LOCK_FILE = File.open(ENV['HS_CFG'], 'w')\n"
                        "begin\n  got = LOCK_FILE.flock(File::LOCK_EX | File::LOCK_NB)\n"
                        "rescue Exception\n  got = true\nend\n"
                        "unless got\n  puts 'BUSY'\n  exit 3\nend\n" + LOCK_MAIN,
        "read": "VALUE = File.read(ENV['HS_CFG'])\ndef main = puts(VALUE)\n",
        "read_if_there": "CFG = ENV['HS_CFG']\nVALUE = File.exist?(CFG) ? File.read(CFG) : 'none'\n"
                         "def main = puts(VALUE)\n",
        "size": "VALUE = File.size(ENV['HS_CFG'])\ndef main = puts(VALUE)\n",
        "list": "VALUE = Dir.children(ENV['HS_DIR']).size\ndef main = puts(VALUE)\n",
        "is_dir": "VALUE = File.directory?(ENV['HS_DIR']) ? 'yes' : 'no'\ndef main = puts(VALUE)\n",
        "relative_if_there": "VALUE = File.exist?('local.cfg') ? File.read('local.cfg') : 'none'\n"
                              "def main = puts(VALUE)\n",
        "relative": "VALUE = File.read('local.cfg')\ndef main = puts(VALUE)\n",
        "relative_exists": "VALUE = File.exist?('local.cfg') ? 'yes' : 'no'\ndef main = puts(VALUE)\n",
        "cwd": "VALUE = Dir.pwd\ndef main = puts(VALUE)\n",
        "lazy": "def main\n  require 'hsread_lazy'\n  puts HSREAD_LAZY\nend\n",
        "lazy_module": "HSREAD_LAZY = File.read(ENV['HS_CFG'])\n",
        "read_at_run": "def main = puts(File.read(ENV['HS_CFG']))\n",
        "write": "File.open(ENV['HS_CFG'], 'a') {}\ndef main = puts('ok')\n",
        "allocate": "def main\n  before = GC.count\n  Array.new(1000) { Object.new }\n  puts(GC.count - before)\nend\n",
    }

    def tool(self, kind):
        root, env = super().tool(kind)
        # On the load path the daemon starts with: what it may learn from.
        env["RUBYLIB"] = os.path.join(root, "bin")
        return root, env

    def script(self):
        # The one the suite runs with: the tests' PATH may only find the system's.
        return (f"#!{shutil.which('ruby')}\n"
                "require_relative 'hsread_lib'\n"
                "main\n")

    def test_load_dir_removed_by_rm_rf(self):
        self.check_load_dir("temp_rm_rf")

    def test_load_dir_removed_by_rm_r(self):
        self.check_load_dir("temp_rm_r")

    def test_load_dir_removed_by_remove_entry_secure(self):
        self.check_load_dir("temp_secure")

    def test_load_dir_removed_by_a_pathname(self):
        self.check_load_dir("temp_pathname")

    def test_load_dir_removed_by_hand(self):
        self.check_load_dir("temp_by_hand")

    def test_load_dir_removed_by_a_library_required_at_exit(self):
        self.check_load_dir("temp_late_require")

    def test_own_load_dir_made_by_mkdir(self):
        self.check_load_dir("own_mkdir")

    def test_flock_at_load_stays_per_run(self):
        self.check_lock_stays_per_run("flock")

    def test_lock_at_load_seen_though_the_error_is_swallowed(self):
        self.check_lock_stays_per_run("flock_hidden")

    def test_child_does_not_collect_for_its_parent(self):
        # A child that finds the parent's heap nearly full collects it itself,
        # at each call until the parent does: 3 to 8 ms for a small tool.
        _, env = self.tool("allocate")
        self.start(env)
        # The first spare came out before the parent's first collection.
        m.run("hs", ["hsread"], env=env)
        counts = [int(m.run("hs", ["hsread"], env=env).stdout) for _ in range(150)]
        self.assertEqual(sum(counts), 0, counts)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(m.Matrix):
    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "rbtest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_client_killed_mid_run(self):
        root, env = m.private_daemon("rbtest")
        try:
            m.check_client_killed(self, "rbtest", self.start_hold, root, env)
        finally:
            m.stop("rbtest", env)

    def test_failed_load_is_retried(self):
        # The job's, as rbtest's: BASE_ENV's PATH may find macOS's old system Ruby.
        ruby = shutil.which("ruby")

        def write_tool(root, flag):
            lib = os.path.join(root, "flaky_lib.rb")
            with open(lib, "w") as f:
                f.write(f"raise 'half installed' if File.exist?({json.dumps(flag)})\n")
            path = os.path.join(root, "flaky-tool")
            with open(path, "w") as f:
                f.write(f"#!{ruby}\nrequire {json.dumps(lib)}\nputs 'healthy'\n")
            os.chmod(path, 0o755)
            return path

        m.check_failed_load_is_retried(self, write_tool)

    def test_output_at_load_not_read_in_time(self):
        m.check_output_not_read_in_time(self, "rbnoisy", ["rbnoisy"])

    def test_read_to_mark(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = r"""
          require "io/console"
          require "pty"
          src = File.read(ARGV[0])
          eval(src[0...src.rindex("at_exit { Speedrunner.report_exit }")])
          S = Speedrunner
          master, slave = PTY.open
          slave.raw!
          master.nonblock = true if master.respond_to?(:nonblock=)
          old, mine = S.new_pty_mark, S.new_pty_mark
          raise "same" if old == mine
          slave.syswrite(old)
          writer = fork { sleep 0.1; slave.syswrite("late" + mine); exit!(0) }
          got = S.read_to_mark(master, "".b, mine)
          Process.wait(writer)
          raise got.inspect unless got == "late"
          slave.syswrite("part")
          S.send(:remove_const, :PTY_MARK_WAIT_S)
          S.const_set(:PTY_MARK_WAIT_S, 0.1)
          begin
            S.read_to_mark(master, "".b, S.new_pty_mark)
            raise "no OutputLost"
          rescue S::OutputLost
            puts "ok"
          end
        """
        with open(shutil.which("rbtest", path=m.BASE_ENV["PATH"])) as f:
            ruby = f.readline()[2:].split()  # the tool's, not the first on PATH
        p = subprocess.run([*ruby, "-e", code, os.path.join(repo, "server", "speedrunner_daemon.rb")],
                           capture_output=True, env=m.BASE_ENV, timeout=30)
        self.assertEqual((p.stdout, p.stderr), (b"ok\n", b""))

    def test_client_that_closed_its_side_is_not_taken_for_gone(self):
        m.check_client_closed_its_side_only(self, "rbtest", m.ROOT)
        self.assertIn(b"still there", m.open_log(m.tool_socket("rbtest")))

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


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Security(m.Matrix):
    """The daemon's own checks, behind the client's (see test_matrix.py)."""

    @unittest.skipUnless(os.getuid() == 0, "needs root to connect as another uid")
    def test_daemon_refuses_peer_of_other_uid(self):
        m.check_peer_uid(self, "rbtest")

    def test_daemon_refuses_bad_runtime_dir(self):
        m.check_daemon_refuses_bad_dir(self, "rbtest")


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class TooOld(m.TooOldChecks, m.Matrix):
    runtime, version, oldest, probe_mark = "ruby", "2.6.10", "3.0", "RUBY_VERSION"
    real = shutil.which("ruby", path=m.BASE_ENV["PATH"])
    code = 'puts ARGV.join(" ")\n'


if __name__ == "__main__":
    unittest.main()
