#!/usr/bin/env bash
# Stage P1 bench: native vs a warm PHP daemon (fork + shared OPcache) for
# the PHP reference commands, `php artisan ...` form included.
#
# Usage: bench/php_warm.sh <laravel project dir> <speedrunner binary> [out_dir]
# Env:   RUNS (default 20).
set -euo pipefail

laravel=${1:?usage: php_warm.sh <laravel dir> <speedrunner> [out_dir]}
sr=${2:?usage: php_warm.sh <laravel dir> <speedrunner> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${3:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-php-warm}
runs=${RUNS:-20}
mkdir -p "$out"
out=$(cd "$out" && pwd)
scratch=$(mktemp -d)
mkdir -m 0700 "$scratch/run"
export HOME=$scratch XDG_RUNTIME_DIR=$scratch/run COMPOSER_ALLOW_SUPERUSER=1 LANG=C.UTF-8
printf '<?php\n\nnamespace App;\n\nfinal class Greeter\n{\n    public function greet(string $name): string\n    {\n        return "Hello, {$name}!";\n    }\n}\n' > "$scratch/Greeter.php"
cd "$laravel"
# name|native command|speedrunner command|stop target
commands=(
  "composer --version|composer --version|$sr run composer -- --version|composer"
  "composer show|composer show|$sr run composer -- show|composer"
  "php artisan --version|php artisan --version|$sr run php -- artisan --version|php artisan"
  "php artisan list|php artisan list|$sr run php -- artisan list|php artisan"
  "phpcs --version|phpcs --version|$sr run phpcs -- --version|phpcs"
  "phpcs Greeter.php|phpcs --standard=PSR12 $scratch/Greeter.php|$sr run phpcs -- --standard=PSR12 $scratch/Greeter.php|phpcs"
  "phpunit --version|phpunit --version|$sr run phpunit -- --version|phpunit"
)
cleanup() {
  for c in "${commands[@]}"; do
    IFS='|' read -r _ _ _ target <<< "$c"
    # shellcheck disable=SC2086
    "$sr" stop $target > /dev/null 2>&1 || true
  done
  rm -rf "$scratch"
}
trap cleanup EXIT

{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git: ${GIT_REV:-unknown}"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  php -v | head -1
} > "$out/machine.txt" 2>&1

args=(--warmup 3 --runs "$runs" -N --export-json "$out/hyperfine.json")
for c in "${commands[@]}"; do
  IFS='|' read -r _ native fast target <<< "$c"
  # Warm up: start the daemon, then until the fast path takes the command.
  # shellcheck disable=SC2086
  $fast > /dev/null 2>&1 || true
  for _ in $(seq 100); do
    # shellcheck disable=SC2086
    SPEEDRUNNER_REQUIRE_FAST=1 $fast > /dev/null 2>&1 && break
    sleep 0.1
  done
  args+=("$native" "env SPEEDRUNNER_REQUIRE_FAST=1 $fast")
done
hyperfine "${args[@]}" > "$out/hyperfine.txt" 2>&1

python3 - "$out/hyperfine.json" <<'PY' | tee "$out/summary.md"
import json, sys
rows = json.load(open(sys.argv[1]))["results"]
names = ["composer --version", "composer show", "php artisan --version", "php artisan list",
         "phpcs --version", "phpcs Greeter.php", "phpunit --version"]
print("| Command | Native (ms) | speedrunner (ms) | Speedup |")
print("|---|---|---|---|")
for name, native, fast in zip(names, rows[::2], rows[1::2]):
    print(f"| `{name}` | {native['median'] * 1000:.1f} ± {native['stddev'] * 1000:.1f} "
          f"| {fast['median'] * 1000:.1f} ± {fast['stddev'] * 1000:.1f} "
          f"| x{native['median'] / fast['median']:.2f} |")
PY
