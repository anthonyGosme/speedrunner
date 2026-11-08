#!/bin/bash
# Runs inside a throwaway python:3.12-slim container (see run.sh): installs
# pytest, black and mypy in a venv, then bench.py.
set -e
cp /w/speedrunner /usr/local/bin/speedrunner
ln -sf speedrunner /usr/local/bin/srr

python3 -m venv /v
/v/bin/pip install -q --disable-pip-version-check pytest==8.3.3 black==24.10.0 mypy==1.13.0
export PATH=/v/bin:$PATH
mkdir -p /p && cd /p
printf 'x = 1\n' > fmt.py
printf 'def test_one():\n    assert 1 + 1 == 2\n' > test_one.py
printf '{"a": [1, 2]}\n' > in.json
python3 -I /w/bench.py 2>&1 | tee /w/result.txt
