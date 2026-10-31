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


if __name__ == "__main__":
    unittest.main()
