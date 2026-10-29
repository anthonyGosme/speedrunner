#!/usr/bin/env bash
# Stage R0 bench: native times of the Ruby reference commands, the share of
# interpreter boot and library loading in them, and the fork ceiling
# (bench/ruby_ceiling.rb) per preload level.
#
# Usage: bench/ruby_baseline.sh <gem home> [out_dir]
# Env:   RUNS (default 20).
set -euo pipefail

gems=${1:?usage: ruby_baseline.sh <gem home> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${2:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-ruby-r0}
runs=${RUNS:-20}
export GEM_HOME=$gems GEM_PATH=$gems PATH=$gems/bin:$PATH LANG=C.UTF-8
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export HOME=$scratch  # no ~/.rubocop.yml or caches of the user
cat > "$scratch/sample.rb" <<'RUBY'
# frozen_string_literal: true

# A small, clean file for rubocop to inspect.
class Greeter
  def initialize(name)
    @name = name
  end

  def greet
    "Hello, #{@name}!"
  end
end
RUBY

mkdir -p "$out"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  ruby -v
  gem -v
  rubocop --version
  rake --version
  bundle --version
} > "$out/machine.txt" 2>&1

# name|exe|library the binstub ends up requiring|args
commands=(
  "rubocop --version|$(ls -d "$gems"/gems/rubocop-[0-9]*/exe/rubocop)|rubocop|--version"
  "rubocop sample.rb|$(ls -d "$gems"/gems/rubocop-[0-9]*/exe/rubocop)|rubocop|--cache false $scratch/sample.rb"
  "rake --version|$(ls -d "$gems"/gems/rake-[0-9]*/exe/rake)|rake|--version"
  "bundle --version|$(ruby -e 'print Gem.bin_path("bundler", "bundle")')|bundler|--version"
  "gem --version|$(command -v gem)|rubygems|--version"
)

hyperfine_args=(--warmup 3 --runs "$runs" -N --export-json "$out/hyperfine.json"
  "ruby --disable-gems -e 0" "ruby -e 0")
for c in "${commands[@]}"; do
  IFS='|' read -r name exe lib args <<< "$c"
  hyperfine_args+=("$(command -v "${name%% *}") $args" "ruby -e 'require \"$lib\"'")
done
hyperfine "${hyperfine_args[@]}" > "$out/hyperfine.txt"

: > "$out/ceiling.txt"
for c in "${commands[@]}"; do
  IFS='|' read -r name exe lib args <<< "$c"
  slug=$(echo "$name" | tr ' ./' '___')
  for level in none lib learned; do
    # shellcheck disable=SC2086  # $args holds several arguments
    ruby "$bench_dir/ruby_ceiling.rb" "$level" "$runs" "$out/ceiling-$slug-$level.json" \
      "$exe" "$lib" -- $args | tee -a "$out/ceiling.txt"
  done
done

python3 - "$out" <<'PY' | tee "$out/summary.md"
import json, sys
out = sys.argv[1]
# In run order: two boot rows, then (native, boot + require) per command.
rows = json.load(open(f"{out}/hyperfine.json"))["results"]
ms = lambda r: r["median"] * 1000
print(f"Interpreter boot: {ms(rows[0]):.1f} ms bare, {ms(rows[1]):.1f} ms with RubyGems\n")
print("| Command | Native | Boot + require of the library | Share |")
print("|---|---|---|---|")
for native, lib in zip(rows[2::2], rows[3::2]):
    name = native["command"].split("/")[-1]
    print(f"| `{name}` | {ms(native):.0f} ms | {ms(lib):.0f} ms | {ms(lib) / ms(native):.0%} |")
PY
