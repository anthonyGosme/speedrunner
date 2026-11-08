#!/bin/bash
# Runs inside a throwaway python:3.12-slim container (see run.sh).
#   /bare    llm alone
#   /plugins llm with four model plugins (Anthropic, Gemini, Mistral, Ollama)
set -e
cp /w/speedrunner /usr/local/bin/speedrunner
ln -sf speedrunner /usr/local/bin/srr

python3 -m venv /bare
/bare/bin/pip install -q --disable-pip-version-check llm
python3 -m venv /plugins
/plugins/bin/pip install -q --disable-pip-version-check llm llm-anthropic llm-gemini llm-mistral llm-ollama

echo "== versions" | tee /w/result.txt
/plugins/bin/pip freeze 2>/dev/null | grep -i -E '^(llm|click|pluggy|pydantic|openai|httpx)' | tee -a /w/result.txt

# llm keeps its keys, logs and templates here; nothing is set in it.
export LLM_USER_PATH=/p/llm-data
mkdir -p /p "$LLM_USER_PATH" && cd /p
python3 -I /w/bench.py 2>&1 | tee -a /w/result.txt
