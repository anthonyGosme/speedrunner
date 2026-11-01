"""install.sh, end to end, from a release built here: the archive of this
machine's platform and its SHA-256 sum, served as SPEEDRUNNER_BASE_URL
(file://). Then the guide's uninstall, which must leave nothing.

Env: HS_BIN, the client to put in the archive (else the test is skipped).

Run: python3 tests/test_install.py -v
"""
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import unittest

HS_BIN = os.environ.get("HS_BIN")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MARK = "added by the speedrunner installer"


def asset_name():
    if platform.system() == "Darwin":
        return "speedrunner-universal-apple-darwin"
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    return f"speedrunner-{arch}-unknown-linux-musl"


FETCH = shutil.which("curl")  # wget has no file:// URLs
ROOT_USER = os.getuid() == 0
# Root installs in /usr/local/bin: only where nothing else lives there.
THROWAWAY = os.path.exists("/.dockerenv") or os.environ.get("CI") == "true"


@unittest.skipUnless(HS_BIN, "needs the speedrunner client")
@unittest.skipUnless(FETCH, "needs curl")
class Release(unittest.TestCase):
    """A release of the client under test, and a fresh home per test."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="hs-install-")
        cls.release = os.path.join(cls.root, "release")
        os.makedirs(cls.release)
        name = asset_name()
        tarball = os.path.join(cls.release, name + ".tar.gz")
        with tarfile.open(tarball, "w:gz") as t:
            t.add(HS_BIN, arcname=f"{name}/speedrunner")
            for doc in ("LICENSE-MIT", "LICENSE-APACHE", "README.md"):
                t.add(os.path.join(REPO, doc), arcname=f"{name}/{doc}")
        with open(tarball, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(tarball + ".sha256", "w") as f:
            f.write(f"{digest}  {name}.tar.gz\n")
        cls.tarball = tarball
        cls.version = subprocess.run([HS_BIN, "--version"], capture_output=True,
                                     text=True, check=True).stdout.strip()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.home = tempfile.mkdtemp(dir=self.root)
        path = "/usr/local/bin:/usr/bin:/bin"
        if os.path.dirname(FETCH) not in path.split(":"):
            path += ":" + os.path.dirname(FETCH)
        self.env = {"HOME": self.home, "PATH": path,
                    "SHELL": "/bin/bash", "SPEEDRUNNER_BASE_URL": "file://" + self.release}

    def install(self, **extra):
        return subprocess.run(["sh", os.path.join(REPO, "install.sh")], capture_output=True,
                              text=True, env={**self.env, **extra}, timeout=60)

    def rc(self, name):
        try:
            with open(os.path.join(self.home, name)) as f:
                return f.read()
        except FileNotFoundError:
            return ""


class Install(Release):
    """What holds for any user."""

    def test_bin_dir_and_no_modify_path(self):
        bin_dir = os.path.join(self.home, "tools")
        p = self.install(SPEEDRUNNER_BIN_DIR=bin_dir, SPEEDRUNNER_NO_MODIFY_PATH="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.access(os.path.join(bin_dir, "srr"), os.X_OK))
        self.assertIn("is not on your PATH", p.stdout)
        self.assertEqual((self.rc(".profile"), self.rc(".bashrc")), ("", ""))

    def test_checksum_mismatch_installs_nothing(self):
        bad = os.path.join(self.root, "bad")
        shutil.copytree(self.release, bad)
        with open(os.path.join(bad, os.path.basename(self.tarball) + ".sha256"), "w") as f:
            f.write("0" * 64 + "  x\n")
        p = self.install(SPEEDRUNNER_BASE_URL="file://" + bad)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("checksum mismatch", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))
        self.assertEqual(self.rc(".profile"), "")

    def test_missing_release_fails_cleanly(self):
        p = self.install(SPEEDRUNNER_BASE_URL="file://" + os.path.join(self.root, "nowhere"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("speedrunner:", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))



@unittest.skipIf(ROOT_USER, "as root it installs in /usr/local/bin: see RootInstall")
class UserInstall(Release):
    """A user's install: in ~/.local/bin, put on PATH in the shell's files."""

    def test_installs_for_a_user_and_puts_it_on_path(self):
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        bin_dir = os.path.join(self.home, ".local", "bin")
        self.assertEqual(sorted(os.listdir(bin_dir)), ["speedrunner", "srr"])
        self.assertEqual(os.readlink(os.path.join(bin_dir, "srr")), "speedrunner")
        out = subprocess.run([os.path.join(bin_dir, "speedrunner"), "--version"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, self.version)
        # .profile always, .bashrc for a bash user; no .zshrc for one who has none.
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', self.rc(".profile"))
        self.assertIn(MARK, self.rc(".bashrc"))
        self.assertEqual(self.rc(".zshrc"), "")
        # A new login shell finds both commands.
        p = subprocess.run(["sh", "-c", ". ./.profile && command -v speedrunner && command -v srr"],
                           cwd=self.home, capture_output=True, text=True, env=self.env)
        self.assertEqual(p.stdout.split(), [os.path.join(bin_dir, "speedrunner"),
                                            os.path.join(bin_dir, "srr")])

    def test_second_run_upgrades_in_place_and_edits_nothing_twice(self):
        self.assertEqual(self.install().returncode, 0)
        before = self.rc(".profile"), self.rc(".bashrc")
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.rc(".profile"), self.rc(".bashrc")), before)
        self.assertEqual(self.rc(".profile").count(MARK), 1)

    def test_dir_already_on_path_is_used_as_is(self):
        bin_dir = os.path.join(self.home, "bin")
        os.makedirs(bin_dir)
        p = self.install(PATH=bin_dir + ":" + self.env["PATH"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.access(os.path.join(bin_dir, "speedrunner"), os.X_OK))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")))
        self.assertEqual(self.rc(".profile"), "")

    def test_guide_uninstall_leaves_nothing(self):
        # docs/GUIDE.md, "Uninstall", install.sh: the files, then the PATH lines.
        self.assertEqual(self.install().returncode, 0)
        bin_dir = os.path.join(self.home, ".local", "bin")
        subprocess.run(["sh", "-c", "rm ~/.local/bin/speedrunner ~/.local/bin/srr"],
                       env=self.env, check=True)
        self.assertEqual(os.listdir(bin_dir), [])
        with open(os.path.join(REPO, "docs", "GUIDE.md")) as f:
            guide = f.read()
        self.assertIn("rm ~/.local/bin/speedrunner ~/.local/bin/srr", guide)
        self.assertIn(MARK, guide)  # the guide names the line to remove


@unittest.skipUnless(ROOT_USER and THROWAWAY, "needs root, in a container or on CI")
class RootInstall(Release):

    def test_root_installs_in_usr_local_bin(self):
        target = "/usr/local/bin/speedrunner"
        if os.path.lexists(target) or os.path.lexists("/usr/local/bin/srr"):
            self.skipTest(f"{target} exists")
        self.addCleanup(lambda: [os.unlink(p) for p in (target, "/usr/local/bin/srr")
                                 if os.path.lexists(p)])
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.readlink("/usr/local/bin/srr"), "speedrunner")
        out = subprocess.run([target, "--version"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, self.version)
        # On every PATH already: no shell file touched.
        self.assertEqual((self.rc(".profile"), self.rc(".bashrc")), ("", ""))


if __name__ == "__main__":
    unittest.main()
