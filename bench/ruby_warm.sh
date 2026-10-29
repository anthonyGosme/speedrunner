#!/usr/bin/env bash
# Ruby warm-path bench: native vs a warm Ruby daemon for the Ruby reference
# commands. Each command first runs twice through speedrunner, so that the
# daemon has learned what it preloads (level 2) before the measurement.
#
# Usage: bench/ruby_warm.sh <gem home> <speedrunner binary> [out_dir]
# Env:   RUNS (default 20); SPEEDRUNNER_PRELOAD and other SPEEDRUNNER_* pass through.
set -euo pipefail

gems=${1:?usage: ruby_warm.sh <gem home> <speedrunner> [out_dir]}
hs=${2:?usage: ruby_warm.sh <gem home> <speedrunner> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${3:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-ruby-warm}
runs=${RUNS:-20}

scratch=$(mktemp -d)
mkdir -m 0700 "$scratch/run"
export HOME=$scratch XDG_RUNTIME_DIR=$scratch/run XDG_CACHE_HOME=$scratch/cache
export GEM_HOME=$gems GEM_PATH=$gems PATH=$gems/bin:$PATH LANG=C.UTF-8
export SPEEDRUNNER_REQUIRE_FAST=1
printf '# frozen_string_literal: true\n\n# A clean file.\nputs 1\n' > "$scratch/sample.rb"
tools=(rubocop rake bundle gem)
trap 'for t in "${tools[@]}"; do "$hs" stop "$t" > /dev/null 2>&1 || true; done; rm -rf "$scratch"' EXIT

mkdir -p "$out"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-unknown}"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  ruby -v
} > "$out/machine.txt" 2>&1

for t in "${tools[@]}"; do
  "$hs" prewarm "$t"
  until "$hs" status "$t" | grep -q '^ready'; do sleep 0.05; done
done

commands=("rubocop --version" "rubocop --cache false $scratch/sample.rb"
          "rake --version" "bundle --version" "gem --version")
for c in "${commands[@]}"; do
  for _ in 1 2; do
    # shellcheck disable=SC2086  # $c holds several arguments
    "$hs" run ${c%% *} -- ${c#* } > /dev/null
  done
done
# Learning is applied in the background: wait until the statuses settle.
prev=
for _ in $(seq 100); do
  sleep 0.5
  cur=$(for t in "${tools[@]}"; do "$hs" status "$t"; done)
  [ "$cur" = "$prev" ] && break
  prev=$cur
done
for t in "${tools[@]}"; do "$hs" status "$t"; done > "$out/daemon-status.txt"
args=(--warmup 3 --runs "$runs" -N --export-json "$out/hyperfine.json")
for c in "${commands[@]}"; do
  args+=("$(command -v "${c%% *}") ${c#* }" "$hs run ${c%% *} -- ${c#* }")
done
hyperfine "${args[@]}" > "$out/hyperfine.txt"

python3 - "$out/hyperfine.json" <<'PY' | tee "$out/summary.md"
import json, sys
rows = json.load(open(sys.argv[1]))["results"]
print("| Command | Native (ms) | speedrunner (ms) | Speedup |")
print("|---|---|---|---|")
for native, fast in zip(rows[::2], rows[1::2]):
    name = native["command"].split("/")[-1].replace(sys.argv[1].rsplit("/", 1)[0], "")
    print(f"| `{name}` | {native['median'] * 1000:.1f} ± {native['stddev'] * 1000:.1f} "
          f"| {fast['median'] * 1000:.1f} ± {fast['stddev'] * 1000:.1f} "
          f"| x{native['median'] / fast['median']:.2f} |")
PY
