"""zsh integration (brief §3, stage 3): enroll, wrapper functions, and the
daemon warmed up while the command line is typed.

Env: HS_BIN and HS_PATH, as for test_matrix.py. Needs zsh >= 5.3.

Run: python3 tests/test_shell.py -v
"""
import os
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

HS_BIN = os.environ.get("HS_BIN")
ZSH = shutil.which("zsh")
SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shell", "speedrunner.zsh"))
PROMPT = "READY> "


@unittest.skipUnless(HS_BIN and ZSH, "needs the speedrunner client and zsh")
class Shell(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hs-shell-")
        self.addCleanup(shutil.rmtree, self.root, True)
        d = lambda *p: os.path.join(self.root, *p)
        for sub in ("bin", "home", "config", "cache", "zdot"):
            os.makedirs(d(sub))
        os.makedirs(d("run"), mode=0o700)
        # `speedrunner` on PATH logs each call, so that prewarms can be counted.
        self.calls = d("calls")
        with open(d("bin", "speedrunner"), "w") as f:
            f.write(f'#!/bin/sh\necho "$*" >> {self.calls}\nexec {HS_BIN} "$@"\n')
        os.chmod(d("bin", "speedrunner"), 0o755)
        with open(d("zdot", ".zshrc"), "w") as f:
            f.write(f"source {SCRIPT}\nPS1='{PROMPT}'\n")
        self.env = {
            "PATH": f"{d('bin')}:{os.environ['HS_PATH']}:/usr/local/bin:/usr/bin:/bin",
            "HOME": d("home"),
            "LANG": "C.UTF-8",
            "TERM": "xterm",
            "ZDOTDIR": d("zdot"),
            "XDG_RUNTIME_DIR": d("run"),
            "XDG_CONFIG_HOME": d("config"),
            "XDG_CACHE_HOME": d("cache"),
        }
        self.addCleanup(subprocess.run, [HS_BIN, "stop", "hstest"], env=self.env,
                        capture_output=True)

    def hs(self, *args):
        return subprocess.run([HS_BIN, *args], env=self.env, capture_output=True)

    def zsh(self, code):
        return subprocess.run([ZSH, "-f", "-c", f"source {SCRIPT}; {code}"], env=self.env,
                              capture_output=True, timeout=60)

    def logged(self, prefix):
        try:
            with open(self.calls) as f:
                return [line for line in f.read().splitlines() if line.startswith(prefix)]
        except FileNotFoundError:
            return []

    def test_enroll_refuses_native_binary(self):
        p = self.hs("enroll", "ls")
        self.assertEqual(p.returncode, 1)
        self.assertIn(b"cannot be preloaded", p.stderr)
        self.assertEqual(self.hs("enroll").stdout, b"")

    def test_enroll_defines_the_wrapper_at_once(self):
        p = self.zsh("speedrunner enroll hstest && whence -w hstest && hstest argv 'a b'")
        self.assertEqual(p.returncode, 0, p.stderr)
        native = subprocess.run(["hstest", "argv", "a b"], env=self.env, capture_output=True)
        self.assertEqual(p.stdout, b"speedrunner: enrolled hstest\nhstest: function\n" + native.stdout)
        self.assertEqual(self.logged("run"), ["run hstest -- argv a b"])

    def test_wrapper_keeps_exit_code_and_streams(self):
        self.hs("enroll", "hstest")
        p = self.zsh("hstest exit str:boom; echo rc=$?")
        native = subprocess.run(["hstest", "exit", "str:boom"], env=self.env, capture_output=True)
        self.assertEqual((p.stdout, p.stderr), (native.stdout + b"rc=1\n", native.stderr))
        self.assertEqual(self.logged("run"), ["run hstest -- exit str:boom"])

    def test_user_function_is_left_alone(self):
        self.hs("enroll", "hstest")
        p = self.zsh("hstest() { echo mine }; _speedrunner_load; hstest")
        self.assertEqual(p.stdout, b"mine\n")

    def test_typing_warms_up_once_per_line(self):
        self.hs("enroll", "hstest")
        self.assertEqual(self.hs("status", "hstest").stdout, b"down\n")
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(ZSH, [ZSH, "-d", "-i"], self.env)  # -d: no /etc/zsh rc files
        self.addCleanup(lambda: (os.kill(pid, signal.SIGKILL), os.waitpid(pid, 0)))
        self.addCleanup(os.close, fd)
        self.read_until(fd, PROMPT)

        self.type(fd, "hstest ar")
        self.wait_for(lambda: self.hs("status", "hstest").stdout.startswith(b"ready"))
        self.type(fd, "gv more words")
        self.assertEqual(len(self.logged("prewarm")), 1)
        # Same line, edited: no second prewarm.
        self.type(fd, "\x15hstest argv x")
        self.assertEqual(len(self.logged("prewarm")), 1)
        os.write(fd, b"\r")
        self.read_until(fd, PROMPT)
        self.assertEqual(self.logged("run"), ["run hstest -- argv x"])
        # On the new line: another command and an unfinished word do not warm
        # up; the tool after an assignment prefix does.
        for text, expected in [("ls -l", 1), ("\x15hstes", 1), ("\x15HSTEST_X=1 hstest ", 2)]:
            self.type(fd, text)
            self.wait_for(lambda: len(self.logged("prewarm")) >= expected, timeout=2, must=False)
            self.assertEqual(len(self.logged("prewarm")), expected, text)

    def test_prefix_warms_up_without_enrolling(self):
        self.assertEqual(self.hs("enroll").stdout, b"")
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(ZSH, [ZSH, "-d", "-i"], self.env)
        self.addCleanup(lambda: (os.kill(pid, signal.SIGKILL), os.waitpid(pid, 0)))
        self.addCleanup(os.close, fd)
        self.read_until(fd, PROMPT)
        self.type(fd, "A=1 srr hstest ar")
        self.wait_for(lambda: self.hs("status", "hstest").stdout.startswith(b"ready"))
        self.type(fd, "gv")
        self.assertEqual(self.logged("prewarm"), ["prewarm hstest"])

    def type(self, fd, text):
        for ch in text:
            os.write(fd, ch.encode())
            time.sleep(0.02)
        self.drain(fd, 0.2)

    def drain(self, fd, seconds):
        out = b""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
        return out

    def read_until(self, fd, marker, timeout=15):
        out = b""
        deadline = time.monotonic() + timeout
        while marker.encode() not in out:
            if time.monotonic() > deadline:
                self.fail(f"no {marker!r} in {out!r}")
            if select.select([fd], [], [], 0.1)[0]:
                out += os.read(fd, 65536)
        return out

    def wait_for(self, cond, timeout=15, must=True):
        deadline = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > deadline:
                if must:
                    self.fail("timed out")
                return
            time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
