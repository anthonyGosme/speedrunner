#!/bin/sh
# black through `uv run` and `uvx`, natively and with speedrunner.
#   bench/uv/run.sh <static Linux speedrunner binary> before|client
# uv and black are installed inside a throwaway container (--rm). The result
# is written to result-<round>.txt next to this script.
set -e
[ $# -eq 2 ] || { echo "usage: $0 <speedrunner binary> before|client" >&2; exit 2; }
w=$(cd "$(dirname "$0")" && pwd)
cp "$1" "$w/speedrunner"
trap 'rm -f "$w/speedrunner"' EXIT
docker run --rm --platform linux/amd64 -v "$w":/w python:3.12-slim bash /w/inside.sh "$2"
