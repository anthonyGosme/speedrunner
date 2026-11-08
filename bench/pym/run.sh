#!/bin/sh
# Python tools called as `python -m <module>`, natively and with speedrunner.
#   bench/pym/run.sh <static Linux speedrunner binary>
# pytest, black and mypy are installed inside a throwaway container (--rm).
# The result is written to result.txt next to this script.
set -e
[ $# -eq 1 ] || { echo "usage: $0 <speedrunner binary>" >&2; exit 2; }
w=$(cd "$(dirname "$0")" && pwd)
cp "$1" "$w/speedrunner"
trap 'rm -f "$w/speedrunner"' EXIT
docker run --rm --platform linux/amd64 -v "$w":/w python:3.12-slim bash /w/inside.sh
