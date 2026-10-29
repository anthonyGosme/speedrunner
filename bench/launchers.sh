#!/usr/bin/env bash
# Bench for CLIs that start Python through a launcher: az (its pip
# launcher execs `python -m azure.cli`), gcloud (a shell script that execs
# python on lib/gcloud.py) and Django's `./manage.py`.
#
# Both sides run the real command: natively, and through speedrunner, which
# follows the launcher to what it execs. Each command runs twice through
# speedrunner first, so that the daemon has learned its lazy imports.
#
# Usage: bench/launchers.sh <speedrunner binary> [out_dir]
# Env:   RUNS (default 10), AZ_VENV, DJANGO_VENV, DJANGO_PROJECT, GCLOUD_ROOT
set -euo pipefail

hs=${1:?usage: launchers.sh <speedrunner> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${2:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-launchers}
runs=${RUNS:-10}
az_venv=${AZ_VENV:-/root/speedrunner/venv-az}
dj_venv=${DJANGO_VENV:-/root/speedrunner/venv-django}
dj_project=${DJANGO_PROJECT:-/root/speedrunner/django/shop}
groot=${GCLOUD_ROOT:-/usr/lib/google-cloud-sdk}

scratch=$(mktemp -d)
export XDG_RUNTIME_DIR=$scratch/run XDG_CACHE_HOME=$scratch/cache
mkdir -m 0700 "$XDG_RUNTIME_DIR"
unset SPEEDRUNNER_REQUIRE_FAST
cleanup() {
  for t in az gcloud; do PATH=$az_venv/bin:$groot/bin:$PATH "$hs" stop "$t" > /dev/null 2>&1 || true; done
  (cd "$dj_project" && PATH=$dj_venv/bin:$PATH "$hs" stop ./manage.py > /dev/null 2>&1) || true
  rm -rf "$scratch"
}
trap cleanup EXIT
base_path=$PATH
mkdir -p "$out"

{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-unknown}"
  echo "runs: $runs"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  free -m
  "$az_venv/bin/python" -VV
  "$az_venv/bin/az" version
  "$groot/bin/gcloud" --version
  "$dj_venv/bin/python" -m django --version
  hyperfine --version
} > "$out/machine.txt" 2>&1

# Milliseconds spent in top-level imports, from -X importtime on stderr.
import_ms() {
  python3 -c '
import sys
total = 0
for line in open(sys.argv[1], errors="replace"):
    parts = line.split("|")
    if not line.startswith("import time:") or len(parts) != 3 or "cumulative" in line:
        continue
    name = parts[2]
    if name.startswith(" ") and not name.startswith("  "):
        total += int(parts[1])
print(round(total / 1000))
' "$1"
}

# One tool: name, the fast-path tool, the native command, then its commands.
bench_tool() {
  local name=$1 fast=$2 native=$3
  shift 3
  local args=() c n_rc f_rc
  echo "== $name" >&2
  for c in "$@"; do
    # Learn, unmeasured.
    "$hs" run "$fast" -- $c > /dev/null 2>&1 || true
    "$hs" run "$fast" -- $c > /dev/null 2>&1 || true
    # Same stdout, stderr and exit code as natively.
    n_rc=0 f_rc=0
    $native $c > "$scratch/n.out" 2> "$scratch/n.err" || n_rc=$?
    SPEEDRUNNER_REQUIRE_FAST=1 "$hs" run "$fast" -- $c > "$scratch/f.out" 2> "$scratch/f.err" || f_rc=$?
    if cmp -s "$scratch/n.out" "$scratch/f.out" && cmp -s "$scratch/n.err" "$scratch/f.err" && [ $n_rc = $f_rc ]; then
      echo "$name $c: same output, rc=$n_rc" >> "$out/identity.txt"
    else
      echo "$name $c: DIFFERENT (rc $n_rc vs $f_rc)" >> "$out/identity.txt"
      diff "$scratch/n.err" "$scratch/f.err" | head -20 >> "$out/identity.txt" || true
    fi
    PYTHONPROFILEIMPORTTIME=1 $native $c > /dev/null 2> "$scratch/imp.err" || true
    echo "$name $c: imports $(import_ms "$scratch/imp.err") ms" >> "$out/imports.txt"
    args+=("$native $c" "$hs run $fast -- $c")
  done
  SPEEDRUNNER_REQUIRE_FAST=1 hyperfine -N --warmup 2 --runs "$runs" -i \
    --export-json "$out/$name.json" --export-markdown "$out/$name.md" "${args[@]}" > /dev/null
  "$hs" stop "$fast" > /dev/null 2>&1 || true
}

(
  export PATH=$az_venv/bin:$base_path
  bench_tool az az az "version" "vm create --help" "group list"
)

(
  export PATH=$groot/bin:$base_path
  bench_tool gcloud gcloud gcloud "--version" "config list" "compute instances create --help"
)

# Django: the project's manage.py, its venv first on PATH.
(
  cd "$dj_project"
  export PATH=$dj_venv/bin:$base_path
  bench_tool django ./manage.py ./manage.py "--version" "check" "help" "showmigrations"
)

python3 - "$out" << 'PYEOF'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
imports = {}
for line in (out / "imports.txt").read_text().splitlines():
    key, ms = line.rsplit(": imports ", 1)
    imports[key] = int(ms.split()[0])
rows = ["| Command | Native | Imports | speedrunner | Gain |", "|---|---|---|---|---|"]
for name in ("az", "gcloud", "django"):
    f = out / f"{name}.json"
    if not f.exists():
        continue
    res = json.loads(f.read_text())["results"]
    for nat, fast in zip(res[::2], res[1::2]):
        cmd = nat["command"]
        label = name + " " + cmd.split(" ", 1)[1] if " " in cmd else cmd
        key = name + " " + cmd.split(" ", 1)[1]
        n, s = nat["median"] * 1000, fast["median"] * 1000
        share = imports.get(key, 0) / n * 100
        rows.append(f"| `{label}` | {n:.0f} ms | {share:.0f} % | {s:.0f} ms | x{n / s:.1f} |")
(out / "summary.md").write_text("\n".join(rows) + "\n")
print("\n".join(rows))
PYEOF
cat "$out/identity.txt"
