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
                               ("throw", 1), ("throw-props", 1), ("reject", 1), ("late", 1)]:
            with self.subTest(kind=kind):
                rc, out, err = self.assertSame(["nodetest", "exit", kind])
                self.assertEqual(rc, expected)
                self.assertNotIn(b"speedrunner_daemon", out + err)

    def test_version(self):
        self.assertSame(["nodetest", "version"])


class AsNatively(m.Matrix):
    def test_time_zone(self):
        m.served_soon(["nodetest", "tz"])
        self.assertSame(["nodetest", "tz"], env={"TZ": "Asia/Tokyo"})

    @unittest.skipUnless(HS_BIN, "needs the speedrunner client")
    def test_daemon_does_not_save_what_it_loaded(self):
        root = tempfile.mkdtemp(dir=m.ROOT)
        env = {**m.in_private_daemon_env(root), "HOME": root}
        self.addCleanup(m.stop, "nodestate", env)
        for _ in range(4):  # until the daemon loaded lib/stateful.js
            run("hs", ["nodestate", "from-a-run"], env=env, require_fast=False)
            time.sleep(0.4)
        deadline = time.monotonic() + 20
        while not m.status("nodestate", env).startswith("ready") or " learned=0" in m.status("nodestate", env):
            self.assertLess(time.monotonic(), deadline, m.status("nodestate", env))
            time.sleep(0.1)
        self.assertEqual(run("hs", ["nodestate", "the-last-run"], env=env).returncode, 0)
        time.sleep(0.2)
        m.stop("nodestate", env)
        with open(os.path.join(root, "nodestate")) as f:
            self.assertEqual(f.read(), "the-last-run")

    def test_listener_set_as_it_loads(self):
        results = []
        for mode in ("native", "hs"):
            if mode == "hs" and HS_BIN:
                self.addCleanup(m.stop, "nodesig")
                # Learned: lib/sigload.js is loaded by the daemon from then on.
                for _ in range(3):
                    q = subprocess.Popen(command("hs", ["nodesig"], require_fast=False)[0],
                                         stdout=subprocess.PIPE, env=m.BASE_ENV)
                    q.stdout.readline()
                    q.send_signal(signal.SIGTERM)
                    q.communicate(timeout=20)
                    time.sleep(0.5)
                deadline = time.monotonic() + 20
                while "learned=0" in m.status("nodesig") or " learning=" in m.status("nodesig"):
                    if time.monotonic() > deadline:
                        self.fail(m.status("nodesig"))
                    time.sleep(0.1)
            cmd, extra = command(mode, ["nodesig"])
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
            m.stop("nodesig")
            self.assertEqual(m.status("nodesig"), "down")

    def test_a_projects_file_is_not_learned(self):
        # As eslint, which requires a project's eslint.config.js: not the tool's
        # to preload, nor to run with the next project's environment.
        root = tempfile.mkdtemp(dir=m.ROOT)
        bin_dir = os.path.join(root, "bin")
        os.makedirs(bin_dir)
        tool = os.path.join(bin_dir, "cfgtool")
        with open(tool, "w") as f:
            f.write("#!/usr/bin/env node\n"
                    "const path = require('path');\n"
                    "try { require(path.resolve('cfg.js')); } catch {}\n"
                    "console.log('tool ran in', path.basename(process.cwd()));\n")
        os.chmod(tool, 0o755)
        marker = os.path.join(root, "cfg-ran")
        one, two = os.path.join(root, "one"), os.path.join(root, "two")
        os.makedirs(one)
        os.makedirs(two)
        with open(os.path.join(one, "cfg.js"), "w") as f:
            f.write(f"require('fs').appendFileSync({json.dumps(marker)}, process.env.SECRET + '\\n');\n")
        env = {"PATH": f"{bin_dir}:{m.BASE_ENV['PATH']}", "XDG_CACHE_HOME": os.path.join(root, "cache")}
        full = {**m.BASE_ENV, **env}
        self.addCleanup(lambda: subprocess.run([HS_BIN, "stop", "cfgtool"], env=full, capture_output=True))
        for _ in range(6):
            run("hs", ["cfgtool"], cwd=one, env=env, require_fast=False)
            time.sleep(0.3)
        open(marker, "w").close()
        for _ in range(6):
            run("hs", ["cfgtool"], cwd=two, env={**env, "SECRET": "from-two"}, require_fast=False)
            time.sleep(0.2)
        with open(marker) as f:
            self.assertEqual(f.read(), "")
        for name in os.listdir(os.path.join(root, "cache", "speedrunner")):
            with open(os.path.join(root, "cache", "speedrunner", name), errors="replace") as f:
                self.assertNotIn("cfg.js", f.read())


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

    def test_execve(self):
        rc, out, _ = self.assertSame(["nodetest", "execve"])
        self.assertEqual((rc, out), (3, b"execd\n"))

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


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
@unittest.skipUnless(m.MACOS, "CoreFoundation is macOS's")
class CoreFoundation(m.Matrix):
    """macOS, as test_matrix.CoreFoundation: a module that sets the process
    title at load (CoreFoundation, through libuv) is not kept, and each run
    that sets its own is served as natively."""

    def test_module_setting_the_title_is_refused(self):
        root, env = m.private_daemon("nodetest")
        self.addCleanup(m.stop, "nodetest", env)
        learn(["nodetest", "title"], env)
        for _ in range(3):
            _, out, _ = self.assertSame(["nodetest", "title"], env=env)
            self.assertEqual(out, b"title: nodetest-run\n")
        [cache] = [os.path.join(dp, f) for dp, _, fs in os.walk(env["XDG_CACHE_HOME"])
                   for f in fs if f.startswith("nodetest-node-") and f.endswith(".json")]
        with open(cache) as f:
            refused = [u for u in json.load(f)["blacklist"] if u.endswith("/lib/lazy-title.js")]
        self.assertEqual(len(refused), 1)


def node_has_code_cache():
    node = shutil.which("node", path=m.BASE_ENV["PATH"])
    p = subprocess.run([node, "-p", "process.config.variables.node_use_node_code_cache"],
                       capture_output=True, text=True)
    return p.stdout.strip() != "false"


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class CodeCache(m.Matrix):
    """A Node built without the code cache of its internals (Fedora's,
    Homebrew's node@22): --no-lazy would rename frames of its loader in a
    stack. Its first daemon notes it and leaves; the next starts without
    --no-lazy, and serves the same stacks as natively."""

    def test_noted_and_served_without_no_lazy(self):
        root, env = m.private_daemon("nodetest")
        self.addCleanup(m.stop, "nodetest", env)
        note = os.path.join(env["XDG_CACHE_HOME"], "speedrunner", "node-no-code-cache")
        if node_has_code_cache():
            self.assertFalse(os.path.exists(note))
            return
        with open(note) as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        argv = ["nodetest", "exit", "throw"]
        rc, _, err = self.assertSame(argv, env=env)
        self.assertIn(b"Module._extensions..js", err)


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
    @unittest.skipUnless(m.HS_BIN, "needs the speedrunner client")
    def test_child_missing_its_go_says_so(self):
        m.check_child_says_it_missed_its_go(self, "nodetest")

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
class ImportTimeReads(m.ImportTimeReadChecks, m.LoadDirChecks, m.Matrix):
    LIB = "hsread_lib.js"
    LAZY = "hsread_lazy.js"
    TAUGHT_LAZILY = True
    FS = "const fs = require('node:fs');\n"
    MAIN = "exports.main = () => console.log(String(VALUE));\n"
    DIR_HEAD = "const fs = require('node:fs'), path = require('node:path');\n"
    DIR_TEMP = "const DIR = fs.mkdtempSync(path.join(process.env.HS_DIR, 'hs-'));\n"
    DIR_OWN = ("const DIR = path.join(process.env.HS_DIR, 'own-' + process.pid);\n"
               "fs.mkdirSync(DIR, { recursive: true });\n")
    DIR_RM = "process.on('exit', () => fs.rmSync(DIR, { recursive: true, force: true }));\n"
    DIR_HOLD = "  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(process.env.HS_HOLD ?? 0) * 1000);\n"
    DIR_MAIN = ("exports.main = () => {\n" + DIR_HOLD +
                "  if (process.env.HS_CLEAR) {\n    fs.rmSync(DIR, { recursive: true });\n"
                "    console.log('cleared');\n    return;\n  }\n"
                "  const file = path.join(DIR, 'f' + process.pid);\n"
                "  fs.writeFileSync(file, '');\n  fs.unlinkSync(file);\n  console.log('ok');\n};\n")
    LAZY_DIR = DIR_HEAD + DIR_TEMP + DIR_RM + "exports.VALUE = 'ok';\n"
    SOURCES = {
        "dir_temp": DIR_HEAD + DIR_TEMP + DIR_RM + DIR_MAIN,
        "dir_own": DIR_HEAD + DIR_OWN + DIR_RM + DIR_MAIN,
        "dir_data": DIR_HEAD + DIR_TEMP + "fs.writeFileSync(path.join(DIR, 'data'), '');\n" + DIR_RM +
                    "exports.main = () => {\n" + DIR_HOLD +
                    "  console.log(fs.existsSync(path.join(DIR, 'data')) ? 'ok' : 'GONE');\n};\n",
        "dir_gone": DIR_HEAD + DIR_TEMP + "fs.rmSync(DIR, { recursive: true });\n"
                    "exports.main = () => console.log('ok');\n",
        "dir_at_run": DIR_HEAD + "exports.main = () => {\n"
                      "  const dir = fs.mkdtempSync(path.join(process.env.HS_DIR, 'hs-'));\n"
                      "  process.on('exit', () => fs.rmSync(dir, { recursive: true }));\n"
                      "  console.log('ok');\n};\n",
        "temp_destructured": "const { mkdtempSync, rmSync, ...fs } = require('node:fs'), path = require('node:path');\n"
                              "const DIR = mkdtempSync(path.join(process.env.HS_DIR, 'hs-'));\n"
                              "process.on('exit', () => rmSync(DIR, { recursive: true }));\n" + DIR_MAIN,
        "temp_by_hand": DIR_HEAD + DIR_TEMP +
                        "process.on('exit', () => {\n"
                        "  for (const e of fs.readdirSync(DIR)) fs.unlinkSync(path.join(DIR, e));\n"
                        "  fs.rmdirSync(DIR);\n});\n" + DIR_MAIN,
        "temp_process_exit": DIR_HEAD + DIR_TEMP + DIR_RM +
                             DIR_MAIN.replace("console.log('ok');", "console.log('ok');\n  process.exit(0);"),
        "own_chain": DIR_HEAD + "const TOP = path.join(process.env.HS_DIR, 'own-' + process.pid), DIR = path.join(TOP, 'a', 'b');\n"
                     "fs.mkdirSync(DIR, { recursive: true });\n"
                     "process.on('exit', () => fs.rmSync(TOP, { recursive: true, force: true }));\n" + DIR_MAIN,
        "read": FS + "const VALUE = fs.readFileSync(process.env.HS_CFG, 'utf8');\n" + MAIN,
        "read_if_there": FS + "const CFG = process.env.HS_CFG;\n"
                              "const VALUE = fs.existsSync(CFG) ? fs.readFileSync(CFG, 'utf8') : 'none';\n" + MAIN,
        "size": FS + "const VALUE = fs.statSync(process.env.HS_CFG).size;\n" + MAIN,
        "list": FS + "const VALUE = fs.readdirSync(process.env.HS_DIR).length;\n" + MAIN,
        "is_dir": FS + "const VALUE = fs.existsSync(process.env.HS_DIR) && fs.statSync(process.env.HS_DIR).isDirectory() ? 'yes' : 'no';\n" + MAIN,
        "relative_if_there": FS + "const VALUE = fs.existsSync('local.cfg') ? fs.readFileSync('local.cfg', 'utf8') : 'none';\n" + MAIN,
        "relative": FS + "const VALUE = fs.readFileSync('local.cfg', 'utf8');\n" + MAIN,
        "relative_exists": FS + "const VALUE = fs.existsSync('local.cfg') ? 'yes' : 'no';\n" + MAIN,
        "cwd": "const VALUE = process.cwd();\n" + MAIN,
        "lazy": "exports.main = () => console.log(require('./hsread_lazy.js').VALUE);\n",
        "lazy_module": FS + "exports.VALUE = fs.readFileSync(process.env.HS_CFG, 'utf8');\n",
        "read_at_run": FS + "exports.main = () => console.log(fs.readFileSync(process.env.HS_CFG, 'utf8'));\n",
        "write": FS + "fs.closeSync(fs.openSync(process.env.HS_CFG, 'a'));\nconst VALUE = 'ok';\n" + MAIN,
    }

    def script(self):
        return "#!/usr/bin/env node\nrequire('./hsread_lib.js').main();\n"

    def test_load_dir_removed_by_a_destructured_function(self):
        self.check_load_dir("temp_destructured")

    def test_load_dir_removed_by_hand(self):
        self.check_load_dir("temp_by_hand")

    def test_load_dir_removed_after_process_exit(self):
        self.check_load_dir("temp_process_exit")

    def test_own_load_dir_with_parents(self):
        self.check_load_dir("own_chain")

    def teach(self, env, cwd):
        # Node preloads what it learned only: the module once a run has loaded it.
        first = m.status("hsread", env)
        gen = int(m.status_field(first, "gen") or 0)
        if gen:
            return
        deadline = time.monotonic() + 30
        while int(m.status_field(m.status("hsread", env), "gen") or 0) <= gen:
            run("hs", ["hsread"], env=env, cwd=cwd, require_fast=False)
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        m.wait_ready("hsread", env, cwd=cwd)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Failures(m.Matrix):
    def start_hold(self, env, marker, seconds="1"):
        cmd = [HS_BIN, "run", "nodetest", "--", "hold", seconds, marker]
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **env, "SPEEDRUNNER_REQUIRE_FAST": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        line = p.stdout.readline().split()
        self.assertEqual(line[0], b"ready", line)
        return p, int(line[1])

    def test_client_killed_mid_run(self):
        root, env = m.private_daemon("nodetest")
        try:
            m.check_client_killed(self, "nodetest", self.start_hold, root, env)
        finally:
            m.stop("nodetest", env)

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
