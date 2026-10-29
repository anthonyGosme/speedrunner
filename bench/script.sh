#!/usr/bin/env bash
# Scripted use: a loop of aws calls, native vs speedrunner three ways:
#   srr       `srr aws ...` on each line
#   explicit  `eval "$(speedrunner activate aws)"`, then plain `aws ...`
#   auto      `eval "$(speedrunner activate)"`: aws found on PATH by itself,
#             its daemon started at its second call (call counters reset)
# The daemon is stopped before each speedrunner loop (its learned cache
# kept, as for a user). No typing, so no anticipation.
#
# Usage: bench/script.sh <aws venv> <speedrunner binary> [out_dir]
# Env:   ROUNDS (default 3).
set -euo pipefail

venv=${1:?usage: script.sh <venv> <speedrunner> [out_dir]}
hs=${2:?usage: script.sh <venv> <speedrunner> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${3:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-script}
rounds=${ROUNDS:-3}

scratch=$(mktemp -d)
mkdir -m 0700 "$scratch/run"
ln -s "$hs" "$scratch/srr"
export HOME=$scratch XDG_RUNTIME_DIR=$scratch/run XDG_CACHE_HOME=$scratch/cache
export PATH=$venv/bin:$PATH LANG=C.UTF-8 AWS_EC2_METADATA_DISABLED=true
export AWS_CONFIG_FILE=$scratch/config AWS_SHARED_CREDENTIALS_FILE=$scratch/credentials
sr=$scratch/srr
trap '"$hs" stop aws > /dev/null 2>&1 || true; rm -rf "$scratch"' EXIT

mkdir -p "$out"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-unknown}"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  "$venv/bin/python" -VV
} > "$out/machine.txt" 2>&1

stop_daemon() {
  "$hs" stop aws > /dev/null || true
  until [ "$("$hs" status aws)" = down ]; do :; done
}
ms() {  # run "$@", print its wall time in ms
  local start=$(date +%s%N)
  "$@"
  echo $(( ($(date +%s%N) - start) / 1000000 ))
}
seq_version() { for _ in $(seq 50); do "$@" --version > /dev/null; done; }
par_version() { seq 50 | xargs -P 4 -I{} "$@" --version > /dev/null; }
seq_skeleton() { for _ in $(seq 20); do "$@" ec2 run-instances --generate-cli-skeleton > /dev/null; done; }
# The same loops as a script would write them, after one activation line.
activated() {  # activation args, scenario
  local act=$1 scenario=$2
  bash -c "eval \"\$('$hs' activate $act)\"; $(declare -f "$scenario"); $scenario aws"
}

# Learn once, as earlier use would have.
"$hs" prewarm aws
until "$hs" status aws | grep -q '^ready'; do sleep 0.05; done
"$sr" aws --version > /dev/null
"$sr" aws ec2 run-instances --generate-cli-skeleton > /dev/null
sleep 2

echo "scenario,round,native_ms,srr_ms,explicit_ms,auto_ms" > "$out/script.csv"
for r in $(seq "$rounds"); do
  for scenario in seq_version par_version seq_skeleton; do
    native=$(ms $scenario aws)
    stop_daemon
    fast=$(ms $scenario "$sr" aws)
    stop_daemon
    explicit=$(ms activated aws $scenario)
    stop_daemon
    rm -rf "$XDG_RUNTIME_DIR"/speedrunner/bin-auto-*/.calls
    auto=$(ms activated "" $scenario)
    echo "$scenario,$r,$native,$fast,$explicit,$auto" | tee -a "$out/script.csv"
  done
done
"$hs" status aws > "$out/daemon-status.txt"
