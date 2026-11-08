# llm, alone and with four model plugins

Machine: MacBook (Intel i7-9750H), Docker Desktop, a `linux/amd64` `python:3.12-slim` container (Python 3.12.16), speedrunner 0.4.10 (the release's static Linux binary). Two venvs: `/bare`, llm 0.37.1 alone; `/plugins`, llm 0.37.1 with llm-anthropic 0.30, llm-gemini 0.35, llm-mistral 0.16 and llm-ollama 0.19.1. `LLM_USER_PATH` points to an empty directory: no key, no template, no log. Only commands that answer locally: no request to a model. Median of 20 runs alternated with the native ones. Scripts: [bench/llm/](../../llm/); every run: [result.txt](result.txt).

| Call | Native | speedrunner | Gain | Same output |
|---|---:|---:|---:|---|
| `llm --version` | 1171 ms | 21 ms | **x54.6** | yes |
| `llm models` | 1223 ms | 45 ms | **x27.3** | yes |
| `llm plugins` | 1175 ms | 19 ms | **x60.8** | yes |
| `llm --version`, 4 plugins | 3472 ms | 33 ms | **x104.7** | yes |
| `llm models`, 4 plugins | 3569 ms | 139 ms | **x25.6** | yes |
| `llm plugins`, 4 plugins | 3401 ms | 38 ms | x88.8 | same content, order varies natively |

All six calls are served (`SPEEDRUNNER_REQUIRE_FAST=1` exits 0), with no setup: `srr llm …`.

- llm loads its plugins at every call: `llm --version` takes 1.2 s alone and 3.5 s with four plugins, for the same one line. Served, the plugins are already loaded in the daemon, and the call costs 21 to 33 ms.
- `llm models` keeps more work per call, 45 ms alone and 139 ms with the plugins: it builds the list of models, 31 lines alone, 101 with the plugins. What part of that is the plugins' own work at each call was not measured.
- `llm plugins` with the four plugins printed them in another order served than in the native run it was compared with. [check-plugins.sh](../../llm/check-plugins.sh) ran it 15 times each way ([check-plugins.txt](check-plugins.txt)): natively, 4 different orders in 15 runs; served, always the same one; once sorted, the same content (names, versions, hooks) in all 30 runs. The order changes from one process to the next (where it comes from in llm or pluggy was not traced). A forked child shares the daemon's state, so it keeps the order the daemon drew: an output that varies from one native run to the next comes out the same at each served run, as with the hash seed ([known limits](../../../docs/GUIDE.md#known-limits)).
- What a prompt spends waiting for a model is not touched; the gain is the start-up before it.
