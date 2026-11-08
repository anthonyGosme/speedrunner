#!/bin/sh
# Simon Willison's `llm`, natively and with speedrunner, in a throwaway container.
#   ./run.sh <speedrunner-x86_64-unknown-linux-musl.tar.gz | static Linux speedrunner binary>
# llm is installed twice inside the container (--rm): alone, and with four
# model plugins. No API key and no network call to a model: only commands that
# answer locally. The result is written to result.txt next to this script.
set -e
[ $# -eq 1 ] || { echo "usage: $0 <speedrunner tar.gz or binary>" >&2; exit 2; }
w=$(cd "$(dirname "$0")" && pwd)
case "$1" in
  *.tar.gz) tar -xzf "$1" -O --wildcards '*/speedrunner' > "$w/speedrunner" 2>/dev/null \
              || tar -xzf "$1" -O '*/speedrunner' > "$w/speedrunner"
            chmod +x "$w/speedrunner" ;;
  *) cp "$1" "$w/speedrunner" ;;
esac
trap 'rm -f "$w/speedrunner"' EXIT
docker run --rm --platform linux/amd64 -v "$w":/w python:3.12-slim bash /w/inside.sh
