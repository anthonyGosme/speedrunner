"""aws v2, the official PyInstaller bundle, served: every output compared with its native
run. Needs the real thing: HS_PATH with the directory of its `aws`. Requests go to a local
server that answers anything, so no network and no credentials.

Run: HS_BIN=... HS_PATH=<aws v2's bin> python3 tests/test_aws_v2.py -v
"""
import concurrent.futures
import http.server
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest

import test_matrix as m

HS_BIN = m.HS_BIN
if os.environ.get("SPEEDRUNNER_DAEMON"):
    m.BASE_ENV["SPEEDRUNNER_DAEMON"] = os.environ["SPEEDRUNNER_DAEMON"]
HOME = tempfile.mkdtemp(dir=m.ROOT)
BUCKETS = (b'<?xml version="1.0"?><ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
           b"<Owner><ID>x</ID></Owner><Buckets><Bucket><Name>b</Name>"
           b"<CreationDate>2020-01-01T00:00:00.000Z</CreationDate></Bucket></Buckets></ListAllMyBucketsResult>")
BINARY = bytes(range(256)) * 40
ENDPOINT = ["--endpoint-url", "http://127.0.0.1:9000"]


class Server(http.server.BaseHTTPRequestHandler):
    bodies = []
    slow = threading.Event()   # a request came to /slow: it hangs until released
    release = threading.Event()

    def answer(self):
        size = int(self.headers.get("content-length") or 0)
        if size:
            self.bodies.append(self.rfile.read(size))
        if "slow" in self.path:
            self.slow.set()
            self.release.wait(30)
        body = BINARY if self.path.endswith("/binary") else b"" if self.command == "PUT" else BUCKETS
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream" if self.path.endswith("/binary") else "application/xml")
        self.send_header("ETag", '"x"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_PUT = do_POST = do_HEAD = answer

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # a client that was interrupted


ENV = {"HOME": HOME, "AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret",
       "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true", "AWS_PAGER": "",
       "XDG_RUNTIME_DIR": os.path.join(HOME, "run"), "XDG_CACHE_HOME": os.path.join(HOME, "cache")}
COMMANDS = [["--version"], ["configure", "list"], ["ec2", "run-instances", "--generate-cli-skeleton"],
            ["s3api", "list-buckets", *ENDPOINT], ["s3", "ls", *ENDPOINT],
            ["nosuchcommand"], ["s3", "ls", "--nosuchoption"], ["configure", "get", "region"],
            ["sts", "get-caller-identity", *ENDPOINT], ["argdump", "a b", "c"], ["ec2", "describe-regions", "--bad"]]


def is_bundle():
    path = shutil.which("aws", path=m.BASE_ENV["PATH"])
    if not path:
        return False
    with open(os.path.realpath(path), "rb") as f:
        return f.read(4) == b"\x7fELF"


def aws(mode, *args, env=None, **kwargs):
    return m.run(mode, ["aws", *args], env={**ENV, **(env or {})}, **kwargs)


def outcome(p):
    """As natively, but for what differs between any two runs, natively too: a transfer's
    progress, and so the spaces that blank it out, as many as its speed took characters."""
    last = p.stdout.split(b"\r")[-1]
    return p.returncode, b"\n".join(line.rstrip(b" ") for line in last.split(b"\n")), p.stderr


def env_of(p):
    return dict(line.split(b"=", 1) for line in p.stdout.split(b"\n") if b"=" in line)


def setUpModule():
    if not (HS_BIN and is_bundle()):
        raise unittest.SkipTest("needs the client, and aws v2 on HS_PATH")
    os.makedirs(os.path.join(HOME, ".aws", "cli"), mode=0o700)
    os.makedirs(ENV["XDG_RUNTIME_DIR"], mode=0o700)
    with open(os.path.join(HOME, ".aws", "cli", "alias"), "w") as f:
        f.write('[toplevel]\nenvdump = !env | sort\nargdump = !f() { echo "$@"; }; f\n')
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 9000), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settle()


def settle():
    """A daemon that has learned what the commands need. A fresh one reloads it from its cache."""
    aws("hs", "--version", require_fast=False)
    m.wait_ready("aws", ENV)
    for args in COMMANDS:
        aws("hs", *args, require_fast=False)
        time.sleep(0.3)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        text = m.wait_ready("aws", ENV)
        if int(m.status_field(text, "memo") or 0) > 0 and int(m.status_field(text, "learned") or 0) > 50:
            break
        aws("hs", *COMMANDS[3], require_fast=False)
        time.sleep(0.5)


def tearDownModule():
    if HS_BIN:
        m.stop("aws", ENV)


class Outputs(unittest.TestCase):
    maxDiff = None

    def test_commands(self):
        for args in COMMANDS:
            with self.subTest(args=args):
                self.assertEqual(outcome(aws("native", *args)), outcome(aws("hs", *args)))

    def test_env_as_natively(self):
        native, served = env_of(aws("native", "envdump")), env_of(aws("hs", "envdump"))
        served.pop(b"SPEEDRUNNER_REQUIRE_FAST")
        self.assertEqual(native, served)
        self.assertIn(b"_PYI_PARENT_PROCESS_LEVEL", native)

    def test_library_path_of_the_client(self):
        env = {"LD_LIBRARY_PATH": "/some/where"}
        self.addCleanup(m.stop, "aws", {**ENV, **env})
        native = env_of(aws("native", "envdump", env=env))
        aws("hs", "--version", env=env, require_fast=False)
        m.wait_ready("aws", {**ENV, **env})
        served = env_of(aws("hs", "envdump", env=env))
        served.pop(b"SPEEDRUNNER_REQUIRE_FAST")
        self.assertEqual(native, served)
        self.assertEqual(native[b"LD_LIBRARY_PATH_ORIG"], b"/some/where")

    def test_upload_from_a_file(self):
        with tempfile.NamedTemporaryFile(dir=HOME) as f:
            f.write(b"x" * 200000)
            f.flush()
            args = ["s3", "cp", f.name, "s3://b/up", *ENDPOINT]
            self.assertEqual(outcome(aws("native", *args)), outcome(aws("hs", *args)))

    def test_upload_through_the_crt(self):
        config = os.path.join(HOME, "crt-config")
        with open(config, "w") as f:
            f.write("[default]\ns3 =\n  preferred_transfer_client = crt\n")
        env = {"AWS_CONFIG_FILE": config}  # the same daemon: it reads this at each run
        with tempfile.NamedTemporaryFile(dir=HOME) as f:
            f.write(b"x" * 200000)
            f.flush()
            args = ["s3", "cp", f.name, "s3://b/up", *ENDPOINT]
            self.assertEqual(outcome(aws("native", *args, env=env)), outcome(aws("hs", *args, env=env)))
            debug = aws("hs", *args, "--debug", env=env)
            self.assertIn(b"Creating CRT S3 client", debug.stderr)  # it was the CRT, and it did not hang

    def test_stdin_goes_to_the_tool(self):
        for mode in ("native", "hs"):
            with self.subTest(mode=mode):
                Server.bodies.clear()
                p = aws(mode, "s3", "cp", "-", "s3://b/piped", *ENDPOINT, input=b"from stdin\x00\n")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertTrue(any(b"from stdin\x00\n" in body for body in Server.bodies))

    def test_binary_on_stdout(self):
        args = ["s3", "cp", "s3://b/binary", "-", *ENDPOINT]
        native, served = aws("native", *args), aws("hs", *args)
        self.assertEqual(native.stdout, BINARY)
        self.assertEqual((native.returncode, native.stdout, native.stderr), (served.returncode, served.stdout, served.stderr))

    def test_debug(self):
        def scrub(p):
            return p.returncode, p.stdout, b"CLI version" in p.stderr
        self.assertEqual(scrub(aws("native", "--debug", "configure", "list")), scrub(aws("hs", "--debug", "configure", "list")))

    def test_on_a_terminal(self):
        for args in (["configure", "list"], ["--version"]):
            with self.subTest(args=args):
                native, served = aws("native", *args, tty=True), aws("hs", *args, tty=True, require_fast=False)
                self.assertEqual((native.returncode, native.stdout), (served.returncode, served.stdout))

    def test_ten_at_once(self):
        native = outcome(aws("native", "s3", "ls", *ENDPOINT))
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            for p in pool.map(lambda _: aws("hs", "s3", "ls", *ENDPOINT), range(10)):
                self.assertEqual(native, outcome(p))


class Daemon(unittest.TestCase):
    def test_no_second_thread_at_the_fork(self):
        pid = m.status_field(m.wait_ready("aws", ENV), "pid")
        if os.path.isdir(f"/proc/{pid}/task"):
            self.assertEqual(len(os.listdir(f"/proc/{pid}/task")), 1)

    def test_learns_and_refuses_nothing(self):
        text = m.wait_ready("aws", ENV)
        self.assertGreater(int(m.status_field(text, "learned")), 50, text)
        self.assertEqual(m.status_field(text, "blacklisted"), "0", text)
        self.assertGreater(int(m.status_field(text, "memo")), 0, text)

    def test_runs_on_the_python_inside(self):
        self.assertEqual(aws("native", "--version").stdout, aws("hs", "--version").stdout)
        self.assertIn(b"Python/", aws("hs", "--version").stdout)


class Signals(unittest.TestCase):
    def interrupted(self, mode):
        Server.slow.clear()
        Server.release.clear()
        cmd, extra = m.command(mode, ["aws", "s3", "ls", "s3://slow", *ENDPOINT])
        p = subprocess.Popen(cmd, env={**m.BASE_ENV, **ENV, **extra}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        try:
            if not Server.slow.wait(30):
                p.kill()
                self.fail(f"{mode}: no request: {p.communicate()}")
            p.send_signal(signal.SIGINT)
            out, err = p.communicate(timeout=30)
        finally:
            Server.release.set()
            if p.poll() is None:
                p.kill()
        return p.returncode, out, err

    def test_ctrl_c(self):
        self.assertEqual(self.interrupted("native"), self.interrupted("hs"))


class Fallbacks(unittest.TestCase):
    def test_forced_native(self):
        p = subprocess.run([HS_BIN, "run", "--native", "aws", "--", "--version"], env={**m.BASE_ENV, **ENV}, capture_output=True)
        self.assertEqual(p.stdout, aws("native", "--version").stdout)

    def test_an_upgrade_is_noticed(self):
        # Another private daemon, on a copy of the bundle's link: only its stamp changes.
        exe = os.path.realpath(shutil.which("aws", path=m.BASE_ENV["PATH"]))
        before = os.stat(exe)
        m.wait_ready("aws", ENV)

        def settled():  # the stamp put back is one more upgrade
            os.utime(exe, ns=(before.st_atime_ns, before.st_mtime_ns))
            settle()

        self.addCleanup(settled)
        os.utime(exe)
        p = aws("hs", "--version", require_fast=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, aws("native", "--version").stdout)
        m.wait_ready("aws", ENV)
        served = aws("hs", "--version", require_fast=False)
        self.assertEqual(served.stdout, p.stdout)

    def test_an_executable_called_aws_without_an_archive(self):
        d = tempfile.mkdtemp(dir=HOME)
        shutil.copy("/bin/echo", os.path.join(d, "aws"))
        env = {"PATH": f"{d}:{m.BASE_ENV['PATH']}"}
        p = m.run("hs", ["aws", "hello"], env={**ENV, **env}, require_fast=False)
        self.assertEqual((p.returncode, p.stdout), (0, b"hello\n"))
        p = m.run("hs", ["aws", "hello"], env={**ENV, **env})
        self.assertEqual(p.returncode, m.EXIT_NOT_FAST, p.stderr)


if __name__ == "__main__":
    unittest.main()
