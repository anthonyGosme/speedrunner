"""State made at load and released at exit, one tool per pattern: does each
served call still find it, as each plain call does?
Usage: python3 load_state_probe.py <speedrunner binary> <python|ruby|node>..."""
import os, shutil, subprocess, sys, tempfile, time

PY = {
    "mkdtemp+atexit": "import atexit, shutil, tempfile\nP = tempfile.mkdtemp()\natexit.register(shutil.rmtree, P, True)\n",
    "TemporaryDirectory": "import tempfile\nT = tempfile.TemporaryDirectory()\nP = T.name\n",
    "mkstemp+atexit": "import atexit, os, tempfile\nfd, P = tempfile.mkstemp()\nos.close(fd)\natexit.register(os.unlink, P)\n",
    "NamedTemporaryFile": "import tempfile\nF = tempfile.NamedTemporaryFile()\nP = F.name\n",
    "own dir+atexit": "import atexit, os, shutil\nP = os.path.expanduser('~/own-%d' % os.getpid())\nos.makedirs(P)\natexit.register(shutil.rmtree, P, True)\n",
}
RB = {
    "mktmpdir+at_exit": "require 'tmpdir'\nrequire 'fileutils'\nP = Dir.mktmpdir\nat_exit { FileUtils.remove_entry(P, true) }\n",
    "Tempfile": "require 'tempfile'\nF = Tempfile.new('x')\nP = F.path\n",
    "own dir+at_exit": "require 'fileutils'\nP = File.expand_path(\"~/own-#{Process.pid}\")\nFileUtils.mkdir_p(P)\nat_exit { FileUtils.remove_entry(P, true) }\n",
}
JS = {
    "mkdtemp+on exit": "const fs = require('fs'), os = require('os'), path = require('path');\nexports.P = fs.mkdtempSync(path.join(os.tmpdir(), 'x-'));\nprocess.on('exit', () => fs.rmSync(exports.P, {recursive: true, force: true}));\n",
    "own dir+on exit": "const fs = require('fs'), os = require('os'), path = require('path');\nexports.P = path.join(os.homedir(), 'own-' + process.pid);\nfs.mkdirSync(exports.P);\nprocess.on('exit', () => fs.rmSync(exports.P, {recursive: true, force: true}));\n",
}
sr = sys.argv[1]
for runtime in sys.argv[2:]:
    sources = {"python": PY, "ruby": RB, "node": JS}[runtime]
    for i, (label, source) in enumerate(sources.items()):
        home = tempfile.mkdtemp(prefix="lsp-")
        bin_dir = os.path.join(home, "bin")
        tmp = os.path.join(home, "tmp")
        os.makedirs(bin_dir); os.makedirs(tmp); os.makedirs(os.path.join(home, "run"), mode=0o700)
        tool = f"lsp{i}"
        if runtime == "python":
            open(f"{bin_dir}/lsplib{i}.py", "w").write(source)
            script = f"#!{shutil.which('python3')}\nimport os, lsplib{i} as L\nprint('exists' if os.path.exists(L.P) else 'GONE')\n"
        elif runtime == "ruby":
            open(f"{bin_dir}/lsplib{i}.rb", "w").write(source)
            script = f"#!{shutil.which('ruby')}\nrequire_relative 'lsplib{i}'\nputs(File.exist?(P) ? 'exists' : 'GONE')\n"
        else:
            open(f"{bin_dir}/lsplib{i}.js", "w").write(source)
            script = f"#!{shutil.which('node')}\nconst L = require('./lsplib{i}.js');\nconsole.log(require('fs').existsSync(L.P) ? 'exists' : 'GONE');\n"
        open(f"{bin_dir}/{tool}", "w").write(script)
        os.chmod(f"{bin_dir}/{tool}", 0o755)
        env = {**os.environ, "PATH": bin_dir + ":" + os.environ["PATH"], "HOME": home, "TMPDIR": tmp, "LANG": "C.UTF-8",
               "XDG_RUNTIME_DIR": os.path.join(home, "run"), "XDG_CACHE_HOME": os.path.join(home, "cache"),
               "XDG_DATA_HOME": os.path.join(home, "data"), "RUBYLIB": bin_dir}
        def call(argv, extra=None):
            r = subprocess.run(argv, env={**env, **(extra or {})}, cwd=home, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
            return r.returncode, r.stdout.decode().strip(), r.stderr.decode().strip()
        def state():
            return sorted(n for n in os.listdir(tmp)) + sorted(n for n in os.listdir(home) if n.startswith("own-"))
        plain = [call([tool]) for _ in range(3)]
        left_plain = state()
        for _ in range(6):
            call([sr, "run", tool, "--"])
            time.sleep(0.4)
        status = call([sr, "status", tool])[1][:80]
        served = []
        for _ in range(4):
            served.append(call([sr, "run", tool, "--"], {"SPEEDRUNNER_REQUIRE_FAST": "1"}))
            time.sleep(0.3)
        during = state()
        call([sr, "stop", tool])
        time.sleep(1.5)
        after = state()
        print(f"{runtime:6} {label:20} plain {[p[1] for p in plain]} left {len(left_plain)}")
        print(f"       served rc {[s[0] for s in served]} {[s[1] for s in served]} left while running {len(during)}, after stop {len(after)}")
        errors = {s[2][-160:] for s in served if s[2]}
        if errors or plain[0][2]:
            print("       stderr served:", errors, "| plain:", plain[0][2][-100:])
        print("      ", status, flush=True)
        shutil.rmtree(home, ignore_errors=True)
