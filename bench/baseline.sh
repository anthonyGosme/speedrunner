#!/usr/bin/env bash
# Stage 0 baseline: native timings, phase breakdown and import profile
# for the reference commands.
#
# Usage: bench/baseline.sh <venv> [out_dir]
# Env:   RUNS (default 30) runs per command, for hyperfine and phases.py.
set -euo pipefail

venv=${1:?usage: baseline.sh <venv> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${2:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-baseline}
runs=${RUNS:-30}
py=$venv/bin/python
aws=$venv/bin/aws

commands=(
  "--version"
  "configure list"
  "ec2 run-instances --generate-cli-skeleton"
)

# Keep the reference commands offline and independent of the user's ~/.aws:
# no IMDS lookup, no config or credentials files.
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export AWS_EC2_METADATA_DISABLED=true
export AWS_CONFIG_FILE=$scratch/config
export AWS_SHARED_CREDENTIALS_FILE=$scratch/credentials
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

mkdir -p "$out"

{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-$(git -C "$bench_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
  echo "runs: $runs"
  echo "--- uname"; uname -a
  echo "--- os-release"; cat /etc/os-release
  echo "--- lscpu"; lscpu
  echo "--- memory"; free -h
  echo "--- loadavg"; cat /proc/loadavg
  echo "--- python"; "$py" -VV
  echo "--- aws"; "$aws" --version
  echo "--- hyperfine"; hyperfine --version
  echo "--- pip freeze"; "$py" -m pip freeze
} > "$out/machine.txt" 2>&1

hyperfine_args=(--warmup 3 --runs "$runs" -N
  --export-json "$out/hyperfine.json" --export-markdown "$out/hyperfine.md"
  "$py -c pass"
  "$py -c 'import awscli.clidriver'"
  # Same, skipping interpreter teardown: isolates the cost of exiting.
  "$py -c 'import awscli.clidriver, os; os._exit(0)'")
for c in "${commands[@]}"; do
  hyperfine_args+=("$aws $c")
done
hyperfine "${hyperfine_args[@]}"

export SPEEDRUNNER_PHASES_OUT=$out/phases.jsonl
: > "$SPEEDRUNNER_PHASES_OUT"
for c in "${commands[@]}"; do
  # Warm the page cache like hyperfine --warmup does, without recording.
  # shellcheck disable=SC2086
  for _ in 1 2 3; do SPEEDRUNNER_PHASES_OUT=/dev/null "$py" "$bench_dir/phases.py" $c > /dev/null; done
  # shellcheck disable=SC2086
  for _ in $(seq "$runs"); do "$py" "$bench_dir/phases.py" $c > /dev/null; done
done

for i in "${!commands[@]}"; do
  # shellcheck disable=SC2086
  "$py" -X importtime "$aws" ${commands[$i]} 2> "$out/importtime-$i.txt" > /dev/null
done

"$py" "$bench_dir/summarize.py" "$out"
