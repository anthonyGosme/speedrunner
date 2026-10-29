#!/usr/bin/env bash
# Warm-path bench: native vs a warm daemon, for the reference commands.
# Each command first runs twice through speedrunner, so that the daemon has
# learned what it preloads (levels 2 and 3) before the measurement.
#
# Usage: bench/warm.sh <aws venv> <speedrunner binary> [out_dir]
# Env:   RUNS (default 30) runs per command; SPEEDRUNNER_* are passed through.
set -euo pipefail

venv=${1:?usage: warm.sh <venv> <speedrunner> [out_dir]}
hs=${2:?usage: warm.sh <venv> <speedrunner> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${3:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-warm}
runs=${RUNS:-30}
aws=$venv/bin/aws

commands=(
  "--version"
  "configure list"
  "ec2 run-instances --generate-cli-skeleton"
)

scratch=$(mktemp -d)
export XDG_RUNTIME_DIR=$scratch/run
mkdir -m 0700 "$XDG_RUNTIME_DIR"
export PATH=$venv/bin:$PATH
export AWS_EC2_METADATA_DISABLED=true
export AWS_CONFIG_FILE=$scratch/config
export AWS_SHARED_CREDENTIALS_FILE=$scratch/credentials
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
# Fail loudly instead of silently benchmarking the native fallback.
export SPEEDRUNNER_REQUIRE_FAST=1
trap '"$hs" stop aws > /dev/null 2>&1 || true; rm -rf "$scratch"' EXIT

mkdir -p "$out"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-unknown}"
  echo "runs: $runs"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  "$venv/bin/python" -VV
  "$aws" --version
  hyperfine --version
} > "$out/machine.txt" 2>&1

"$hs" prewarm aws
for _ in $(seq 100); do
  "$hs" status aws | grep -q '^ready' && break
  sleep 0.1
done
for c in "${commands[@]}"; do
  for _ in 1 2; do
    # shellcheck disable=SC2086  # $c holds several arguments
    "$hs" run aws -- $c > /dev/null
  done
done
# Learning is applied in the background: wait until the status settles.
prev=
for _ in $(seq 100); do
  sleep 0.5
  cur=$("$hs" status aws)
  [ "$cur" = "$prev" ] && break
  prev=$cur
done
"$hs" status aws | tee "$out/daemon-status.txt"

hyperfine_args=(--warmup 3 --runs "$runs" -N
  --export-json "$out/hyperfine.json" --export-markdown "$out/hyperfine.md")
for c in "${commands[@]}"; do
  hyperfine_args+=("$aws $c" "$hs run aws -- $c")
done
hyperfine "${hyperfine_args[@]}"

python3 - "$out/hyperfine.json" <<'EOF' | tee "$out/summary.md"
import json, sys
rows = json.load(open(sys.argv[1]))["results"]
print("| Command | Native (ms) | speedrunner (ms) | Speedup |")
print("|---|---|---|---|")
for native, fast in zip(rows[::2], rows[1::2]):
    cmd = native["command"].split("/bin/aws ", 1)[1]
    print(f"| `aws {cmd}` | {native['median'] * 1000:.1f} ± {native['stddev'] * 1000:.1f} "
          f"| {fast['median'] * 1000:.1f} ± {fast['stddev'] * 1000:.1f} "
          f"| x{native['median'] / fast['median']:.2f} |")
EOF
