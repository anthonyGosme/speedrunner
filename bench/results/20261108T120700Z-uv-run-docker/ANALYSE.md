# Tools called through `uv run` and `uvx`

Machine: MacBook (Intel i7-9750H), Docker Desktop 29.9.2, a `linux/amd64` `python:3.12-slim` container, uv 0.13.7, black 24.10.0. No network during the runs (`UV_OFFLINE=1`). Median of 20 runs alternated with the native ones. Scripts: [bench/uv/](../../uv/).

## Before: 0.4.9 serves nothing behind uv

[before-srr-in-front.txt](before-srr-in-front.txt):

| Call | Native | speedrunner | Served |
|---|---:|---:|---|
| `srr .venv/bin/black --version` | 244 ms | 15 ms | yes, x16 |
| `srr ~/.local/bin/black --version` (`uv tool install`) | 244 ms | 16 ms | yes, x15 |
| `srr uv run black --version` | 287 ms | 276 ms | no |
| `srr uvx black --version` | 251 ms | 251 ms | no |
| `speedrunner exec -t black uv run black --version` | 261 ms | 266 ms | no |
| `speedrunner exec -t black uvx black --version` | 256 ms | 260 ms | no |

- `srr uv`: uv is a native binary, so the client runs it as is (`not a script of a runtime speedrunner serves`) and black runs natively behind it.
- Under `exec -t black`, the file says "served": that check was on `speedrunner exec` itself, not on black. The times show black is not served: uv puts the project's `.venv/bin` first on `PATH`, ahead of the links `exec` adds.

## By hand: srr after uv

[before-srr-after-uv.txt](before-srr-after-uv.txt). uv prepares the environment, then runs `srr`, which finds black in it. `SPEEDRUNNER_REQUIRE_FAST` reaches `srr` through uv, so the check is meaningful here.

| Call | Native | speedrunner | Gain |
|---|---:|---:|---:|
| `.venv/bin/black --version` (reference, `srr` in front) | 235 ms | 15 ms | x15.5 |
| `uv run srr black --version` | 246 ms | 28 ms | x8.7 |
| `uv run --frozen srr black --version` | 247 ms | 28 ms | x8.9 |
| `uvx --from black==24.10.0 srr black --version` | 251 ms | 32 ms | x7.9 |

Same output as native in every case. uv's own preparation costs about 13 ms a call (28 ms against 15 ms), which speedrunner cannot remove. `--frozen` changes nothing on a project already in sync.

## After: the client steps into `uv run`

[client.txt](client.txt), with this branch's client (it still prints 0.4.9: no version bump):

| Call | Native | speedrunner | Gain |
|---|---:|---:|---:|
| `srr .venv/bin/black --version` (reference) | 288 ms | 18 ms | x16.4 |
| `srr uv run black --version` | 293 ms | 32 ms | **x9.2** |
| `srr uv -q run --frozen black --version` | 308 ms | 33 ms | x9.4 |
| `srr uv run black -q --check fmt.py` (a file checked) | 272 ms | 39 ms | x7.0 |
| `speedrunner exec -t uv uv run black --version` | 262 ms | 33 ms | x8.0 |
| `srr uv run python -c pass` (left to uv) | 32 ms | 32 ms | x0.98, native as intended |

Same output as native in all six. `SPEEDRUNNER_DEBUG` shows `uv run: the client takes the command's place` for the served ones, and `uv run: python is not a script of a runtime speedrunner serves` for the last. That one costs nothing measurable: the client decides from the arguments and one `stat` per candidate, then execs uv.

The native times of this run are higher than in the first two (288 against 235 to 244 ms for the same command): the machine was noisier. Gains are compared within a run, natively and served alternated.

## `uvx`

`uvx --from black srr black` works by hand (x7.9), but the client does not rewrite `uvx black` by itself. Which commands a package provides is only known once uv has installed it. For a package with no command of its own name, uv prints its own error natively, while the rewritten call would start the client, which would print `command not found`. A difference in output on an error path, so it stays out; the guide gives the manual form.
