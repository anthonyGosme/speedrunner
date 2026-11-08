#!/bin/sh
# Is the order of `llm plugins` stable natively? Runs it 15 times natively and
# 15 times served, and compares the raw outputs and the outputs once sorted.
#   ./check-plugins.sh <speedrunner-x86_64-unknown-linux-musl.tar.gz>
set -e
[ $# -eq 1 ] || { echo "usage: $0 <speedrunner tar.gz>" >&2; exit 2; }
w=$(cd "$(dirname "$0")" && pwd)
tar -xzf "$1" -O --wildcards '*/speedrunner' > "$w/speedrunner" 2>/dev/null \
  || tar -xzf "$1" -O '*/speedrunner' > "$w/speedrunner"
chmod +x "$w/speedrunner"
trap 'rm -f "$w/speedrunner"' EXIT
docker run --rm --platform linux/amd64 -v "$w":/w python:3.12-slim bash -c '
set -e
cp /w/speedrunner /usr/local/bin/speedrunner && ln -sf speedrunner /usr/local/bin/srr
python3 -m venv /plugins
/plugins/bin/pip install -q --disable-pip-version-check llm llm-anthropic llm-gemini llm-mistral llm-ollama
export PATH=/plugins/bin:$PATH LLM_USER_PATH=/p/llm-data
mkdir -p /p "$LLM_USER_PATH" && cd /p
python3 -I /w/check_plugins.py 2>&1 | tee /w/check-plugins.txt'
