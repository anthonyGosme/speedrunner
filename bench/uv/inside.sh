#!/bin/bash
# Runs inside a throwaway python:3.12-slim container (see run.sh):
# installs uv and black, then bench.py <round>.
set -e
round=$1
export PATH="$HOME/.local/bin:$PATH"

cp /w/speedrunner /usr/local/bin/speedrunner
ln -sf speedrunner /usr/local/bin/srr

pip install -q --root-user-action=ignore --disable-pip-version-check uv==0.13.7
mkdir -p /p && cd /p
uv init -q --no-workspace --name demo .
uv add -q black==24.10.0
uv tool install -q black==24.10.0
uvx -q black==24.10.0 --version >/dev/null   # fills uvx's cache
uv run black --version >/dev/null            # syncs the project once
printf 'x = 1\n' > fmt.py

# No network from here on: every call is answered from what is installed.
export UV_OFFLINE=1
python3 -I /w/bench.py "$round" 2>&1 | tee "/w/result-$round.txt"
