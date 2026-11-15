"""For each brew command: time of the bash preamble up to the exec of Ruby,
and time of that Ruby command run directly with the env and argv it gets."""
import os, shlex, statistics, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "HOMEBREW_NO_ENV_HINTS": "1"}

def outer_exec(args):
    """bin/brew's final `env -i … /bin/bash -p brew.sh args`, as argv."""
    p = subprocess.run(["bash", "-x", "/usr/local/bin/brew", *args], env=BASE, capture_output=True, text=True)
    line = [l for l in p.stderr.splitlines() if l.startswith("+ exec /usr/bin/env -i")][-1]
    return shlex.split(line[len("+ exec "):])

HARNESS = r'''
exec() {
  # The first exec brew.sh makes: record it, run nothing.
  { printf '%s\0' "$@"; } > "$SRR_DIR/argv"; env -0 > "$SRR_DIR/env"; builtin exit 0
}
source "$HOMEBREW_LIBRARY/Homebrew/brew.sh"
'''

def capture(args):
    argv = outer_exec(args)
    i = argv.index("/bin/bash")
    assert argv[i + 1] == "-p" and argv[i + 2].endswith("/brew.sh"), argv[i:i + 3]
    d = tempfile.mkdtemp()
    pre = argv[:i] + ["SRR_DIR=" + d, "/bin/bash", "-p", "-c", HARNESS, argv[i + 2]] + argv[i + 3:]
    return pre, d

def timed(cmd, env=None, n=10):
    ts = []
    for _ in range(n + 2):
        t = time.perf_counter()
        r = subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts[2:]), r.returncode

def main():
  for args in [a.split() for a in sys.argv[1:]]:
      pre, d = capture(args)
      t_pre, rc_pre = timed(pre)
      rb_argv = open(os.path.join(d, "argv"), "rb").read().split(b"\0")[:-1]
      env = dict(kv.split(b"=", 1) for kv in open(os.path.join(d, "env"), "rb").read().split(b"\0") if b"=" in kv)
      env.pop(b"SRR_DIR", None)
      t_rb, rc_rb = timed(rb_argv, env=env)
      t_all, rc_all = timed(["brew", *args], env=BASE)
      print(f"brew {' '.join(args):28s} total {t_all:7.1f}  bash preamble {t_pre:6.1f}  ruby {t_rb:7.1f} ms  rc {rc_all}/{rc_rb}")
      if args == sys.argv[1].split():
          print("  ruby argv:", [a.decode() for a in rb_argv][:6], "...", len(env), "env vars")

if __name__ == "__main__":
    main()
