"""Native Ruby part, preloaded fork, and same output, for each brew command."""
import filecmp, json, os, statistics, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from split import capture, timed  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
for args in [a.split() for a in sys.argv[1:]]:
    pre, d = capture(args)
    subprocess.run(pre, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rb_argv = [a.decode() for a in open(os.path.join(d, "argv"), "rb").read().split(b"\0")[:-1]]
    env = {k.decode(): v.decode() for k, v in (kv.split(b"=", 1) for kv in open(os.path.join(d, "env"), "rb").read().split(b"\0") if b"=" in kv)}
    env.pop("SRR_DIR", None)
    native_ms, _ = timed(rb_argv, env=env)
    native_out = subprocess.run(rb_argv, env=env, capture_output=True).stdout
    out = tempfile.mkdtemp()
    ruby, flags = rb_argv[0], rb_argv[1:3]
    p = subprocess.run([ruby, *flags, os.path.join(HERE, "ceiling.rb"), "10", out, *args], env=env, capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        print(f"brew {' '.join(args)}: prototype failed rc={p.returncode}\n{p.stderr[-1500:]}")
        continue
    res = json.loads(p.stdout.strip().splitlines()[-1])
    same = all(open(os.path.join(out, f"fork-{i}.out"), "rb").read() == native_out for i in range(10))
    print(f"brew {' '.join(args):28s} ruby native {native_ms:7.1f} ms  forked {res['median_ms']:6.1f} ms (X at {res['to_x_ms']})  "
          f"x{native_ms / res['to_x_ms']:.1f}  same={same}  files={res['files']} preload={res['preload_ms']}ms "
          f"threads={res['threads']} rss={res['rss_mb']}MB failed={len(res['failed'])} rc={res['statuses']}")
    if res["failed"]:
        print("   failed:", res["failed"][:5])
