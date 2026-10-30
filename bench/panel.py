"""Extended panel: well-known command-line tools of each runtime, run with no
tool-specific setup (`speedrunner run TOOL -- ARGS`, as `srr TOOL ARGS`).

For each command:
  native, speedrunner  wall time over RUNS runs each, the two interleaved so
                       that a drift of the machine affects both alike: n,
                       mean, median, standard deviation, min, max and the
                       samples themselves; speedrunner with a warm daemon
                       (warmed by a few calls, then the fast path required: a
                       fallback would be reported, not measured)
  speedup              ratio of the medians, with its 95% confidence interval
                       (bootstrap: medians of resampled runs)
  identical            exit code, stdout and stderr equal to a native run,
                       printed durations aside ("in 0.01s")
  pss_mb               the daemon's memory: PSS of its parent and its spare
Each daemon is stopped after its command, to keep the machine's memory free.

Usage: python3 panel.py <speedrunner binary> <out_dir>
Env:   RUNS (default 20), PANEL_ONLY (comma-separated runtimes or tools).
"""
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = "/root/speedrunner"
LARAVEL = f"{ROOT}/php/laravel"
PHP_TOOLS = f"{ROOT}/php/tools"
PATH = ":".join([f"{ROOT}/venv-aws/bin", f"{ROOT}/venv-panel/bin", f"{ROOT}/venv-az/bin",
                 f"{ROOT}/gems-rb/bin", f"{PHP_TOOLS}/vendor/bin", "/usr/local/bin", "/usr/bin", "/bin"])

SAMPLES = {
    "sample.py": "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n",
    "test_sample.py": "from sample import greet\n\n\ndef test_greet():\n    assert greet(\"a\") == \"Hello, a!\"\n",
    "sample.yaml": "---\nkey: value\nlist:\n  - one\n  - two\n",
    "sample.rb": "# frozen_string_literal: true\n\n# A clean file.\nputs 1\n",
    "sample_spec.rb": "RSpec.describe 'arithmetic' do\n  it('adds') { expect(1 + 1).to eq(2) }\nend\n",
    "Greeter.php": ("<?php\n\nnamespace App;\n\nfinal class Greeter\n{\n    public function greet(string $name): string\n"
                    "    {\n        return \"Hello, {$name}!\";\n    }\n}\n"),
}

TIMES = re.compile(rb"\d+(\.\d+)?\s?(ms|s|seconds?)\b|(?<!\d)\d\d:\d\d:\d\d(?!\d)")  # durations, clock times

# Set by another panel (bench/candidates.py): env added to every run and
# extra working directories, "{scratch}" and "{work}" filled in.
EXTRA_ENV = {}
CWDS = {}

def stats(times):
    return {"n": len(times), "mean": round(statistics.mean(times), 1),
            "median": round(statistics.median(times), 1), "sd": round(statistics.stdev(times), 1),
            "min": round(min(times), 1), "max": round(max(times), 1),
            "samples": [round(t, 2) for t in times]}


def speedup_ci(native, fast, rounds=2000):
    """95% interval of median(native) / median(fast), by bootstrap."""
    rng = random.Random(0)
    ratios = sorted(statistics.median(rng.choices(native, k=len(native))) /
                    statistics.median(rng.choices(fast, k=len(fast))) for _ in range(rounds))
    return [round(ratios[int(rounds * 0.025)], 2), round(ratios[int(rounds * 0.975) - 1], 2)]


# runtime, label, argv, cwd ("work": the samples' directory), normalize durations
PANEL = [
    ("python", "aws --version", ["aws", "--version"], None, False),
    ("python", "aws configure list", ["aws", "configure", "list"], None, False),
    ("python", "az --help", ["az", "--help"], None, False),
    ("python", "az vm --help", ["az", "vm", "--help"], None, False),
    ("python", "gcloud --version", ["gcloud", "--version"], None, False),
    ("python", "gcloud config list", ["gcloud", "config", "list"], None, False),
    ("python", "ansible --version", ["ansible", "--version"], None, False),
    ("python", "ansible-doc ping", ["ansible-doc", "ping"], None, False),
    ("python", "black --check sample.py", ["black", "--check", "sample.py"], "work", False),
    ("python", "poetry --version", ["poetry", "--version"], None, False),
    ("python", "http --offline (httpie)", ["http", "--offline", "--ignore-stdin", "example.org", "a=b"], None, False),
    ("python", "pre-commit --version", ["pre-commit", "--version"], None, False),
    ("python", "mypy sample.py", ["mypy", "sample.py"], "work", False),
    ("python", "pytest -q", ["pytest", "-q", "-p", "no:cacheprovider", "test_sample.py"], "work", True),
    ("python", "flake8 sample.py", ["flake8", "sample.py"], "work", False),
    ("python", "yamllint sample.yaml", ["yamllint", "sample.yaml"], "work", False),
    ("python", "pip list", ["pip", "list"], None, False),
    ("ruby", "rubocop --version", ["rubocop", "--version"], None, False),
    ("ruby", "rubocop sample.rb", ["rubocop", "--cache", "false", "sample.rb"], "work", True),
    ("ruby", "rake --version", ["rake", "--version"], None, False),
    ("ruby", "bundle --version", ["bundle", "--version"], None, False),
    ("ruby", "gem list", ["gem", "list"], None, False),
    ("ruby", "rails --version", ["rails", "--version"], None, False),
    ("ruby", "rspec sample_spec.rb", ["rspec", "sample_spec.rb"], "work", True),
    ("ruby", "pod --version", ["pod", "--version", "--allow-root"], None, False),
    ("ruby", "solargraph --version", ["solargraph", "--version"], None, False),
    ("ruby", "rdoc --version", ["rdoc", "--version"], None, False),
    ("php", "composer --version", ["composer", "--version"], None, False),
    ("php", "composer show", ["composer", "show"], "laravel", False),
    ("php", "php artisan --version", ["php", "artisan", "--version"], "laravel", False),
    ("php", "php artisan list", ["php", "artisan", "list"], "laravel", False),
    ("php", "phpcs Greeter.php", ["phpcs", "--standard=PSR12", "Greeter.php"], "work", False),
    ("php", "phpunit --version", ["phpunit", "--version"], None, False),
    ("php", "phploc --version", ["phploc", "--version"], None, False),
    ("php", "phpstan analyse", ["phpstan", "analyse", "--no-progress", "-l", "5", "Greeter.php"], "work", False),
    ("php", "php-cs-fixer --version", ["php-cs-fixer", "--version"], None, False),
    ("php", "psalm --version", ["psalm", "--version"], None, False),
    ("php", "rector --version", ["rector", "--version"], None, False),
]


def main():
    sr, out = sys.argv[1], sys.argv[2]
    runs = int(os.environ.get("RUNS", "20"))
    only = set(filter(None, os.environ.get("PANEL_ONLY", "").split(",")))
    os.makedirs(out, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="sr-panel-")
    work = os.path.join(scratch, "work")
    os.makedirs(work)
    for name, text in SAMPLES.items():
        os.makedirs(os.path.dirname(os.path.join(work, name)), exist_ok=True)
        with open(os.path.join(work, name), "w") as f:
            f.write(text)
    env = {
        "PATH": PATH, "HOME": scratch, "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": os.path.join(scratch, "run"),
        "XDG_CACHE_HOME": os.path.join(scratch, "cache"),
        "XDG_CONFIG_HOME": os.path.join(scratch, "config"),
        "GEM_HOME": f"{ROOT}/gems-rb", "GEM_PATH": f"{ROOT}/gems-rb",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_CONFIG_FILE": os.path.join(scratch, "aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": os.path.join(scratch, "aws-credentials"),
        "AZURE_CORE_COLLECT_TELEMETRY": "no", "AZURE_CONFIG_DIR": os.path.join(scratch, "azure"),
        "CLOUDSDK_CONFIG": os.path.join(scratch, "gcloud"),
        "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true",
        "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK": "true",
        "COMPOSER_ALLOW_SUPERUSER": "1", "ANSIBLE_LOCAL_TEMP": os.path.join(scratch, "ansible"),
        **{k: v.format(scratch=scratch, work=work) for k, v in EXTRA_ENV.items()},
    }
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    cwds = {None: work, "work": work, "laravel": LARAVEL,
            **{k: v.format(scratch=scratch, work=work) for k, v in CWDS.items()}}

    def run(argv, cwd, extra=None, capture=True):
        # No timeout on timed runs: subprocess then polls waitpid in steps of
        # up to 50 ms, which the measurement would include.
        return subprocess.run(argv, cwd=cwd, env={**env, **(extra or {})}, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                              timeout=120 if capture else None)

    def timed_ms(argv, cwd, extra=None):
        t0 = time.perf_counter()
        run(argv, cwd, extra, capture=False)
        return (time.perf_counter() - t0) * 1000

    def interleaved_ms(*commands):
        """Each command's times over `runs` rounds, one run of each per round."""
        times = [[] for _ in commands]
        for _ in range(runs):
            for t, command in zip(times, commands):
                t.append(timed_ms(*command))
        return times

    def target(argv):
        return argv[:2] if argv[0] == "php" else argv[:1]

    def status(argv, cwd):
        return run([sr, "status", *target(argv)], cwd).stdout.decode().strip()

    def pss_mb(pid):
        total = 0
        for p in [pid] + [int(c) for c in open(f"/proc/{pid}/task/{pid}/children").read().split()]:
            try:
                for line in open(f"/proc/{p}/smaps_rollup"):
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
            except OSError:
                pass
        return round(total / 1024, 1)

    results = []
    for runtime, label, argv, cwd_key, times in PANEL:
        if only and runtime not in only and argv[0] not in only:
            continue
        cwd = cwds[cwd_key]
        fast_argv = [sr, "run", argv[0], "--", *argv[1:]]
        row = {"runtime": runtime, "command": label}
        if shutil.which(argv[0], path=PATH) is None:
            row["error"] = "not installed"
            results.append(row)
            print(f"{label:32} not installed", flush=True)
            continue
        run(argv, cwd)  # first-run notices (az) and caches, before the comparison
        native = run(argv, cwd)
        # Warm up: a few calls (learning), the status settled, the fast path taken.
        for _ in range(3):
            run(fast_argv, cwd)
        prev, deadline = None, time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(0.5)
            cur = status(argv, cwd)
            if cur == prev and cur.startswith("ready") and " learning=" not in cur:
                break
            prev = cur
        check = run(fast_argv, cwd, {"SPEEDRUNNER_REQUIRE_FAST": "1"})
        if check.returncode == 124:
            row["error"] = "not fast: " + check.stderr.decode(errors="replace").strip()[-200:]
            native_t, = interleaved_ms((argv, cwd))
            row.update(native_ms=round(statistics.median(native_t), 1), native=stats(native_t))
        else:
            norm = (lambda b: TIMES.sub(b"<t>", b)) if times else (lambda b: b)
            same = (native.returncode, norm(native.stdout), norm(native.stderr)) == \
                   (check.returncode, norm(check.stdout), norm(check.stderr))
            row["identical"] = same
            row["native_rc"] = native.returncode  # a failing command measures its error path
            if not same:
                row["diff"] = {"rc": [native.returncode, check.returncode],
                               "stdout_equal": norm(native.stdout) == norm(check.stdout),
                               "stderr_native": native.stderr.decode(errors="replace")[-300:],
                               "stderr_fast": check.stderr.decode(errors="replace")[-300:]}
            native_t, fast_t = interleaved_ms((argv, cwd), (fast_argv, cwd, {"SPEEDRUNNER_REQUIRE_FAST": "1"}))
            n, f = stats(native_t), stats(fast_t)
            row.update(native_ms=n["median"], native_sd=n["sd"], fast_ms=f["median"], fast_sd=f["sd"],
                       speedup=round(statistics.median(native_t) / statistics.median(fast_t), 2),
                       speedup_ci95=speedup_ci(native_t, fast_t), native=n, fast=f)
            m = re.search(r"pid=(\d+)", status(argv, cwd))
            if m:
                row["pss_mb"] = pss_mb(int(m.group(1)))
        run([sr, "stop", *target(argv)], cwd)
        results.append(row)
        print(f"{label:32} " + json.dumps({k: v for k, v in row.items()
                                           if k not in ('runtime', 'command', 'native', 'fast')}), flush=True)

    # The learned sets, to estimate one daemon per environment (bench/union.py).
    shutil.copytree(os.path.join(scratch, "cache", "speedrunner"), os.path.join(out, "cache"),
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.tmp"))
    with open(os.path.join(out, "panel.json"), "w") as f:
        json.dump({"runs": runs, "results": results}, f, indent=1)
    # Per command: median, mean ± standard deviation and min–max, in ms.
    def cell(st):
        return f"{st['median']} | {st['mean']} ± {st['sd']} | {st['min']}–{st['max']}"
    lines = [f"Wall time in ms over {runs} interleaved runs each; speedup = ratio of the medians, "
             "with its 95% bootstrap confidence interval.", "",
             "| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median "
             "| speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    for r in results:
        if "error" in r:
            native = cell(r["native"]) if "native" in r else " | | "
            lines.append(f"| {r['runtime']} | `{r['command']}` | {native} | — | — | — | — | — | {r['error']} | |")
        else:
            ci = r["speedup_ci95"]
            lines.append(f"| {r['runtime']} | `{r['command']}` | {cell(r['native'])} | {cell(r['fast'])} "
                         f"| x{r['speedup']} | x{ci[0]}–x{ci[1]} | {'yes' if r['identical'] else 'NO'} "
                         f"| {r.get('pss_mb', '')} |")
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
