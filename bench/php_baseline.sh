#!/usr/bin/env bash
# Stage P0 bench: native times of the PHP reference commands, the bare
# interpreter start, and the fork ceiling (bench/php_ceiling.php) per
# preload level.
#
# Usage: bench/php_baseline.sh <laravel project dir> [out_dir]
# Env:   RUNS (default 20).
set -euo pipefail

laravel=${1:?usage: php_baseline.sh <laravel project dir> [out_dir]}
bench_dir=$(cd "$(dirname "$0")" && pwd)
out=${2:-$bench_dir/results/$(date -u +%Y%m%dT%H%M%SZ)-php-p0}
mkdir -p "$out"
out=$(cd "$out" && pwd)  # absolute: we cd into the project below
runs=${RUNS:-20}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export HOME=$scratch COMPOSER_ALLOW_SUPERUSER=1 LANG=C.UTF-8
printf '<?php\n\nnamespace App;\n\nfinal class Greeter\n{\n    public function greet(string $name): string\n    {\n        return "Hello, {$name}!";\n    }\n}\n' > "$scratch/Greeter.php"
cd "$laravel"

mkdir -p "$out"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "loadavg: $(cat /proc/loadavg)"
  uname -a
  php -v | head -1
  composer --version 2>/dev/null
  php artisan --version
  phpcs --version
  phpunit --version | head -1
} > "$out/machine.txt" 2>&1

# name|script|args (run from the Laravel project)
commands=(
  "composer --version|$(command -v composer)|--version"
  "composer show|$(command -v composer)|show"
  "artisan --version|$laravel/artisan|--version"
  "artisan list|$laravel/artisan|list"
  "phpcs --version|$(command -v phpcs)|--version"
  "phpcs Greeter.php|$(command -v phpcs)|--standard=PSR12 $scratch/Greeter.php"
  "phpunit --version|$(command -v phpunit)|--version"
)

hyperfine_args=(--warmup 3 --runs "$runs" -N --export-json "$out/hyperfine.json"
  "php -n -r ;" "php -r ;")
for c in "${commands[@]}"; do
  IFS='|' read -r name script args <<< "$c"
  hyperfine_args+=("$script $args")
done
hyperfine "${hyperfine_args[@]}" > "$out/hyperfine.txt" 2>&1

: > "$out/ceiling.txt"
for c in "${commands[@]}"; do
  IFS='|' read -r name script args <<< "$c"
  slug=$(echo "$name" | tr ' ./' '___')
  for level in none opcache; do
    # Children print to a file: each must have printed what a native run does.
    # shellcheck disable=SC2086  # $args holds several arguments
    # "none" without OPcache, as natively; "opcache" with it in the parent.
    enable=0
    [ "$level" = opcache ] && enable=1
    php -d opcache.enable_cli=$enable -d opcache.revalidate_freq=0 "$bench_dir/php_ceiling.php" \
      "$level" "$runs" "$out/ceiling-$slug-$level.json" "$script" -- $args \
      2>> "$out/ceiling.txt" > "$scratch/children.out" < /dev/null || true
    # shellcheck disable=SC2086
    native_lines=$("$script" $args 2>/dev/null < /dev/null | wc -l)
    learning=0
    [ "$level" = opcache ] && learning=1
    children=$(( runs + 3 + learning ))
    got=$(wc -l < "$scratch/children.out")
    echo "         check: $got lines from $children children, $native_lines per native run" >> "$out/ceiling.txt"
  done
done
cat "$out/ceiling.txt"

python3 - "$out" <<'PY' | tee "$out/summary.md"
import json, sys
rows = json.load(open(f"{sys.argv[1]}/hyperfine.json"))["results"]
ms = lambda r: r["median"] * 1000
print(f"Interpreter start: {ms(rows[0]):.1f} ms without php.ini, {ms(rows[1]):.1f} ms with it\n")
print("| Command | Native |")
print("|---|---|")
for r in rows[2:]:
    print(f"| `{r['command'].split('/')[-1]}` | {ms(r):.0f} ± {r['stddev'] * 1000:.0f} ms |")
PY
