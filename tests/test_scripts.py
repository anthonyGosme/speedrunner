"""Scripts (brief §3): `eval "$(speedrunner activate tool...)"` at the top of a
script, or `speedrunner exec -t tool cmd`, puts links named after the tools
first on PATH, so every later call, in subprocesses too (xargs, make), goes
through its daemon without a prefix on each line.

Env: as test_matrix.py.

Run: python3 tests/test_scripts.py -v
"""
import glob
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest

import test_matrix as m

HS_BIN = m.HS_BIN

# The same body runs natively and activated: everything after the first line.
BODY = '''
for i in 1 2 3; do hstest argv "$i"; done
hstest exit int:4; echo "rc=$?"
printf 'a\\nb\\n' | xargs -n1 hstest argv
'''


def tearDownModule():
    shutil.rmtree(m.ROOT, ignore_errors=True)


class ScriptCase(m.Matrix):
    def setUp(self):
        root = tempfile.mkdtemp(dir=m.ROOT)
        self.env = {"XDG_RUNTIME_DIR": os.path.join(root, "run"),
                    "XDG_CACHE_HOME": os.path.join(root, "cache"),
                    "XDG_CONFIG_HOME": os.path.join(root, "config")}
        os.makedirs(self.env["XDG_RUNTIME_DIR"], mode=0o700)
        self.root = root
        self.addCleanup(m.stop, "hstest", self.env)

    def bash(self, script, **extra):
        return subprocess.run(["bash", "-c", script], capture_output=True, timeout=60,
                              env={**m.BASE_ENV, **self.env, **extra})

    def native(self):
        return m.outcome(subprocess.run(["bash", "-c", BODY], capture_output=True, timeout=60,
                                        env={**m.BASE_ENV, **self.env}))

    def activated(self, **extra):
        return m.outcome(self.bash(f'eval "$({shlex.quote(HS_BIN)} activate hstest)"' + BODY,
                                   **extra))



@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class Scripts(ScriptCase):
    """`activate` with tools named: links for those, daemons started at once."""

    def test_warm_daemon_serves_every_call(self):
        m.wait_ready("hstest", self.env)
        # Every call fast, xargs' too: a native fallback would exit with 124.
        self.assertEqual(self.native(), self.activated(SPEEDRUNNER_REQUIRE_FAST="1"))

    def test_activate_starts_the_daemon(self):
        self.assertEqual(m.status("hstest", self.env), "down")
        self.assertEqual(self.native(), self.activated())
        m.wait_ready("hstest", self.env)  # started by activate, or by the first call

    @unittest.skipUnless(subprocess.run(["env", "-S", "true"], capture_output=True).returncode == 0,
                         "this env has no -S (busybox)")
    def test_exec_as_shebang(self):
        m.wait_ready("hstest", self.env)
        script = os.path.join(self.root, "script")
        with open(script, "w") as f:
            f.write(f"#!/usr/bin/env -S {HS_BIN} exec -t hstest bash\n" + BODY)
        os.chmod(script, 0o755)
        p = subprocess.run([script], capture_output=True, timeout=60,
                           env={**m.BASE_ENV, **self.env, "SPEEDRUNNER_REQUIRE_FAST": "1"})
        self.assertEqual(self.native(), m.outcome(p))

    def test_no_loop_without_a_daemon(self):
        # The link must exec the real tool, never itself.
        self.assertEqual(self.native(), self.activated(SPEEDRUNNER_NO_AUTOSTART="1"))
        self.assertEqual(m.status("hstest", self.env), "down")

    def test_enrolled_tools_are_included(self):
        subprocess.run([HS_BIN, "enroll", "hstest"], env={**m.BASE_ENV, **self.env}, check=True,
                       capture_output=True)
        m.wait_ready("hstest", self.env)
        p = self.bash(f'eval "$({shlex.quote(HS_BIN)} activate)"' + BODY,
                      SPEEDRUNNER_REQUIRE_FAST="1")
        self.assertEqual(self.native(), m.outcome(p))

    def test_tool_reading_path_at_import_served_under_exec(self):
        # hstest.lazy_env reads PATH as it loads, as pre-commit does. Its
        # daemon, started and taught outside any script, must still serve a
        # script's calls, whose PATH begins with speedrunner's own links.
        m.wait_ready("hstest", self.env)

        def preloaded():
            for path in glob.glob(os.path.join(self.env["XDG_CACHE_HOME"], "speedrunner", "hstest-*.json")):
                with open(path) as f:
                    if "hstest.lazy_env" in json.load(f).get("modules", []):
                        return True
            return False

        deadline = time.monotonic() + 30
        while not preloaded() or " learning=" in m.status("hstest", self.env):
            self.assertLess(time.monotonic(), deadline, m.status("hstest", self.env))
            m.run("hs", ["hstest", "lazy"], env=self.env, require_fast=False)
            time.sleep(0.3)
        m.wait_ready("hstest", self.env)
        native = m.outcome(m.run("native", ["hstest", "lazy"], env=self.env))
        p = subprocess.run([HS_BIN, "exec", "-t", "hstest", "hstest", "lazy"], capture_output=True, timeout=60,
                           env={**m.BASE_ENV, **self.env, "SPEEDRUNNER_REQUIRE_FAST": "1"})
        self.assertEqual(native, m.outcome(p))

    def test_native_tool_refused(self):
        p = self.bash(f'eval "$({shlex.quote(HS_BIN)} activate ls)"; command -v ls')
        self.assertEqual(p.returncode, 0)
        self.assertIn(b"ls: cannot be preloaded", p.stderr)
        self.assertNotIn(b"speedrunner", p.stdout)

    def test_activate_twice_adds_path_once(self):
        act = f'eval "$({shlex.quote(HS_BIN)} activate hstest)"'
        p = self.bash(f'{act}; {act}; echo "$PATH"')
        first = p.stdout.decode().strip().split(":")[0]
        self.assertEqual(p.stdout.decode().count(first), 1, p.stdout)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
class AutoScripts(ScriptCase):
    """`activate` with no tool named finds the Python and Ruby tools on PATH
    itself; a daemon starts at a tool's second call only."""

    def auto(self, body, **extra):
        return self.bash(f'eval "$({shlex.quote(HS_BIN)} activate)"' + body, **extra)

    def test_finds_python_tools_only(self):
        p = self.auto("\ncommand -v hstest; command -v ls; command -v bash")
        hstest, ls, bash = p.stdout.decode().split()
        self.assertIn("/bin-auto-", hstest)
        path = {**m.BASE_ENV, **self.env}["PATH"]  # Fedora has /usr/sbin first on its own
        self.assertEqual((ls, bash), (shutil.which("ls", path=path), shutil.which("bash", path=path)))

    def test_matches_native_and_starts_the_daemon(self):
        self.assertEqual(self.native(), m.outcome(self.auto(BODY)))
        m.wait_ready("hstest", self.env)

    def test_single_call_starts_nothing(self):
        p = self.auto('\nhstest argv once')
        self.assertEqual(p.returncode, 0)
        time.sleep(1)
        self.assertEqual(m.status("hstest", self.env), "down")

    def test_daemons_start_in_first_use_order_one_at_a_time(self):
        # One start at a time: the daemon of the tool the script runs first
        # (aws, though written last) is ready before the next one starts.
        self.addCleanup(m.stop, "aws", self.env)
        script = os.path.join(self.root, "order.sh")
        with open(script, "w") as f:
            # Waits for hstest's daemon rather than for a fixed time: aws's
            # may take a while to preload on a busy machine.
            f.write("later() { for i in 1 2; do hstest argv $i; done; }\n"
                    "sleep 3\nfor i in 1 2; do aws --version; done\n"
                    f"for i in $(seq 300); do {shlex.quote(HS_BIN)} status hstest | grep -q ^ready && break; "
                    "sleep 0.1; done\nlater\n")
        p = subprocess.run([HS_BIN, "exec", "bash", script], capture_output=True, timeout=60,
                           env={**m.BASE_ENV, **self.env, "SPEEDRUNNER_START_JOBS": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        aws = m.wait_ready("aws", self.env)
        m.wait_ready("hstest", self.env)
        run = os.path.join(self.env["XDG_RUNTIME_DIR"], "speedrunner")
        [aws_sock] = glob.glob(os.path.join(run, "aws-*.sock"))
        [hstest_sock] = glob.glob(os.path.join(run, "hstest-*.sock"))
        # A daemon opens its socket, then preloads.
        aws_ready = os.stat(aws_sock).st_ctime + int(m.status_field(aws, "preload_ms")) / 1000
        self.assertGreater(os.stat(hstest_sock).st_ctime, aws_ready - 0.05)
        self.assertEqual(self.started_by(), ["exec"])

    def started_by(self):
        logs = glob.glob(os.path.join(self.env["XDG_RUNTIME_DIR"], "speedrunner", "hstest-*.log"))
        if not logs:
            return []
        with open(logs[0]) as f:
            return re.findall(r"started by (\w+)", f.read())

    def check_warmed_at_activation(self, body, files=()):
        for name, text in files:
            with open(os.path.join(self.root, name), "w") as f:
                f.write(text)
        p = subprocess.run(["bash", "-c", f'eval "$({shlex.quote(HS_BIN)} activate)"\n' + body],
                           capture_output=True, timeout=60, cwd=self.root,
                           env={**m.BASE_ENV, **self.env})
        self.assertEqual(p.returncode, 0, p.stderr)
        m.wait_ready("hstest", self.env)
        self.assertEqual(self.started_by(), ["activate"])

    def test_loop_warms_up_at_activation(self):
        # One iteration: the second-call counter alone would never start it.
        self.check_warmed_at_activation("for i in 1; do hstest argv x; done")

    def test_function_called_twice_warms_up(self):
        self.check_warmed_at_activation("f() { hstest argv \"$1\"; }\nf a\nif [ -n \"$NEVER\" ]; then f b; fi\n")

    def test_sourced_file_warms_up(self):
        self.check_warmed_at_activation(". ./lib.sh", [("lib.sh", "while false; do :; done\n"
                                                                  "for i in 1; do hstest argv; done\n")])

    def test_xargs_warms_up(self):
        self.check_warmed_at_activation("echo a | xargs hstest argv")

    def test_eager_warms_single_calls_too(self):
        p = self.bash(f'eval "$({shlex.quote(HS_BIN)} activate --eager)"\nhstest argv once')
        self.assertEqual(p.returncode, 0, p.stderr)
        m.wait_ready("hstest", self.env)
        self.assertEqual(self.started_by(), ["activate"])

    def test_function_called_once_waits(self):
        p = self.auto('\nf() { hstest argv once; }\nf')
        self.assertEqual(p.returncode, 0)
        time.sleep(1)
        self.assertEqual(m.status("hstest", self.env), "down")

    def test_later_runs_are_fast(self):
        self.auto(BODY)  # learns that hstest is called repeatedly
        m.wait_ready("hstest", self.env)
        self.assertEqual(self.native(), m.outcome(self.auto(BODY, SPEEDRUNNER_REQUIRE_FAST="1")))


# pre-commit's git hook: pre-commit 4's resources/hook-tmpl as `pre-commit
# install` fills it in, plus a line that logs each run of its preamble. The
# fixture's hook runs `python -mhstest ARGS` where the real one runs
# `python -mpre_commit hook-impl ...`.
HOOK = '''#!/usr/bin/env bash
# File generated by pre-commit: https://pre-commit.com
# ID: 138fd403232d2ddd5efb44317e38bf03

# start templated
INSTALL_PYTHON={python}
ARGS=({args})
# end templated
echo hook >> "$HOOK_LOG"

HERE="$(cd "$(dirname "$0")" && pwd)"
ARGS+=(--hook-dir "$HERE" -- "$@")

if [ -x "$INSTALL_PYTHON" ]; then
    exec "$INSTALL_PYTHON" -m{module} "${{ARGS[@]}}"
elif command -v pre-commit > /dev/null; then
    exec pre-commit "${{ARGS[@]}}"
else
    echo '`pre-commit` not found.  Did you forget to activate your virtualenv?' 1>&2
    exit 1
fi
'''

PRE_COMMIT_CONFIG = '''repos:
-   repo: local
    hooks:
    -   id: check
        name: check
        entry: sh -c 'echo ran >> "$RUN_LOG"; grep -q ok "$@"' --
        language: system
        files: '\\.txt$'
'''


def stop_hook_daemons(env):
    """The hook's daemon is named pre-commit, but `speedrunner stop
    pre-commit` reaches the daemon of the pre-commit script."""
    run = os.path.join(env["XDG_RUNTIME_DIR"], "speedrunner")
    socks = glob.glob(os.path.join(run, "pre-commit-*.sock"))
    for sock in socks:
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(5)
                s.connect(sock)
                s.sendall(b"HS1S" + struct.pack("<I", 0))
                s.recv(1)
        except OSError:
            pass
    deadline = time.monotonic() + 10
    while any(os.path.exists(s) for s in socks) and time.monotonic() < deadline:
        time.sleep(0.05)


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
@unittest.skipUnless(shutil.which("git"), "needs git")
class PreCommitHook(ScriptCase):
    """git runs pre-commit's hook by its path, and the hook starts Python by
    its absolute path; under `speedrunner exec`, a `bash` link next to
    pre-commit's catches the hook's `#!/usr/bin/env bash`, and the hook is
    followed as a launcher: its preamble runs natively, once, and what it
    execs is served."""

    def setUp(self):
        super().setUp()
        self.addCleanup(stop_hook_daemons, self.env)
        python = shutil.which("python3", path=os.environ["HS_PATH"])
        self.python = python
        # A pre-commit on PATH, as `speedrunner exec` wants to link bash.
        bindir = os.path.join(self.root, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "pre-commit"), "w") as f:
            f.write(f"#!{python}\nimport sys\nsys.exit('not this one')\n")
        os.chmod(os.path.join(bindir, "pre-commit"), 0o755)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.env.update({"PATH": bindir + ":" + m.BASE_ENV["PATH"],
                         "HOOK_LOG": os.path.join(self.root, "hook.log"),
                         "HSTEST_RUN_LOG": os.path.join(self.root, "run.log"),
                         "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                         "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "init")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("ok\n")
        self.git("add", "a.txt")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                       env={**m.BASE_ENV, **self.env})

    def install(self, args, module="hstest", python=None):
        path = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        with open(path, "w") as f:
            f.write(HOOK.format(python=python or self.python, args=args, module=module))
        os.chmod(path, 0o755)
        return path

    def call(self, argv, served, **extra):
        prefix = [HS_BIN, "exec", "-t", "pre-commit"] if served else []
        fast = {"SPEEDRUNNER_REQUIRE_FAST": "1"} if served else {}
        return subprocess.run(prefix + argv, cwd=self.repo, capture_output=True, timeout=60,
                              env={**m.BASE_ENV, **self.env, **fast, **extra})

    def commit(self, served, **extra):
        """A commit, undone if it was made: each one starts from the same state."""
        p = self.call(["git", "commit", "-qm", "edit"], served, **extra)
        if p.returncode == 0:
            self.git("reset", "-q", "--soft", "HEAD~1")
        return p

    def warm(self, argv):
        # The first calls start the daemon and run natively: 124 here.
        deadline = time.monotonic() + 60
        while True:
            p = self.call(argv, True)
            if p.returncode != m.EXIT_NOT_FAST and b"fast path unavailable" not in p.stderr:
                return
            self.assertLess(time.monotonic(), deadline, p.stderr)
            time.sleep(0.2)

    def lines(self, name):
        try:
            with open(self.env[name]) as f:
                return f.read().split()
        except FileNotFoundError:
            return []

    @m.needs_ptrace
    def test_hook_served_as_git_runs_it(self):
        # git execs the hook by path, with the arguments of its hook type.
        for args, hook_args in (("argv", ["x y"]), ("exit int:3", []), ("exit raise", [])):
            with self.subTest(args=args):
                hook = self.install(args)
                self.warm([hook, *hook_args])
                native = m.outcome(self.call([hook, *hook_args], False))
                self.assertEqual(native, m.outcome(self.call([hook, *hook_args], True)))

    @m.needs_ptrace
    def test_commit_same_as_native_and_runs_once(self):
        self.install("argv")
        deadline = time.monotonic() + 60
        while self.commit(True).returncode != 0:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        hooks, runs = len(self.lines("HOOK_LOG")), len(self.lines("HSTEST_RUN_LOG"))
        native = m.outcome(self.commit(False))
        self.assertEqual(native[0], 0, native)
        for _ in range(3):
            self.assertEqual(native, m.outcome(self.commit(True)))
        self.assertEqual(len(self.lines("HOOK_LOG")) - hooks, 4)
        self.assertEqual(len(self.lines("HSTEST_RUN_LOG")) - runs, 4)

    @m.needs_ptrace
    def test_failing_hook_fails_the_commit(self):
        self.install("exit int:1")
        head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=self.repo,
                              capture_output=True, env={**m.BASE_ENV, **self.env}).stdout
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        self.warm([hook])
        native = m.outcome(self.commit(False))
        self.assertEqual(native[0], 1, native)
        runs = len(self.lines("HSTEST_RUN_LOG"))
        self.assertEqual(native, m.outcome(self.commit(True)))
        self.assertEqual(len(self.lines("HSTEST_RUN_LOG")) - runs, 1)
        self.assertEqual(head, subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=self.repo,
                                              capture_output=True, env={**m.BASE_ENV, **self.env}).stdout)

    def test_falls_back_silently(self):
        # No daemon yet (or no ptrace): the hook and its tool run natively, once.
        self.install("argv")
        native = m.outcome(self.commit(False))
        self.assertEqual(native[0], 0, native)
        hooks, runs = len(self.lines("HOOK_LOG")), len(self.lines("HSTEST_RUN_LOG"))
        p = self.call(["git", "commit", "-qm", "edit"], True, SPEEDRUNNER_REQUIRE_FAST="0",
                      SPEEDRUNNER_NO_AUTOSTART="1")
        self.git("reset", "-q", "--soft", "HEAD~1")
        self.assertEqual(native, m.outcome(p))
        self.assertEqual((len(self.lines("HOOK_LOG")) - hooks, len(self.lines("HSTEST_RUN_LOG")) - runs), (1, 1))

    def test_other_bash_scripts_run_natively(self):
        script = os.path.join(self.root, "other")
        with open(script, "w") as f:
            f.write("#!/usr/bin/env bash\necho \"$0 $# $1\"; exec true\n")
        os.chmod(script, 0o755)
        for argv in ([script, "a b"], ["bash", "-c", "echo $0 $1", "x", "y"], ["bash", script, "z"]):
            with self.subTest(argv=argv):
                self.assertEqual(m.outcome(self.call(argv, False)), m.outcome(self.call(argv, True)))

    def test_bash_linked_only_next_to_pre_commit(self):
        which = ["sh", "-c", "command -v bash"]
        self.assertIn(b"/bin-", self.call(which, True).stdout)
        # Automatic mode: pre-commit is found on PATH.
        p = subprocess.run([HS_BIN, "exec", *which], capture_output=True, timeout=60,
                           env={**m.BASE_ENV, **self.env, "SPEEDRUNNER_NO_AUTOSTART": "1"})
        self.assertIn(b"/bin-auto-", p.stdout)
        p = subprocess.run([HS_BIN, "exec", "-t", "hstest", *which], capture_output=True, timeout=60,
                           env={**m.BASE_ENV, **self.env})
        self.assertEqual(p.stdout.decode().strip(), shutil.which("bash", path=self.env["PATH"]))

    @m.needs_ptrace
    def test_real_pre_commit(self):
        found = shutil.which("pre-commit", path=os.environ["HS_PATH"] + ":/root/speedrunner/venv-panel/bin")
        if not found:
            self.skipTest("needs pre-commit")
        self.env.update({"PATH": self.env["PATH"].split(":", 1)[1] + ":" + os.path.dirname(found),
                         "RUN_LOG": os.path.join(self.root, "pre-commit.log")})
        with open(os.path.join(self.repo, ".pre-commit-config.yaml"), "w") as f:
            f.write(PRE_COMMIT_CONFIG)
        self.git("add", ".pre-commit-config.yaml")
        subprocess.run([found, "install"], cwd=self.repo, check=True, capture_output=True,
                       env={**m.BASE_ENV, **self.env})
        deadline = time.monotonic() + 60
        while self.commit(True).returncode != 0:  # 124 from the hook until its daemon is up
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        for content, rc in (("ok\n", 0), ("bad\n", 1)):
            with self.subTest(content=content):
                with open(os.path.join(self.repo, "a.txt"), "w") as f:
                    f.write(content)
                self.git("add", "a.txt")
                runs = len(self.lines("RUN_LOG"))
                native = m.outcome(self.commit(False))
                self.assertEqual(native[0], rc, native)
                self.assertEqual(native, m.outcome(self.commit(True)))
                self.assertEqual(len(self.lines("RUN_LOG")) - runs, 2)


if __name__ == "__main__":
    unittest.main()
