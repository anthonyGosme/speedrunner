# Tools called as `python -m <module>`

Machine: MacBook (Intel i7-9750H), Docker Desktop, a `linux/amd64` `python:3.12-slim` container, a venv with pytest 8.3.3, black 24.10.0 and mypy 1.13.0 first on `PATH`. Median of 20 runs alternated with the native ones. Scripts: [bench/pym/](../../pym/); every run: [result.txt](result.txt).

Before this branch, `srr python3 -m …` ran natively: `python3` is a native binary (`not a script of a runtime speedrunner serves`). Now the module gets a daemon of its own.

| Call | Native | speedrunner | Gain |
|---|---:|---:|---:|
| `pytest --version` (the script, for reference) | 296 ms | 76 ms | x3.9 |
| `srr python3 -m pytest --version` | 283 ms | 74 ms | **x3.8** |
| `srr python3 -m pytest -q test_one.py` | 339 ms | 96 ms | **x3.5** |
| `srr python3 -m black --version` | 243 ms | 16 ms | **x15.3** |
| `srr python3 -m black -q --check fmt.py` | 224 ms | 20 ms | **x11.1** |
| `srr python3 -m mypy --version` | 212 ms | 20 ms | **x10.6** |
| `srr python3 -m pip --version` | 304 ms | 18 ms | **x17.3** |
| `srr python3 -m json.tool in.json` (stdlib) | 53 ms | 30 ms | x1.8 |
| `speedrunner exec -t python3 python3 -m pytest --version` | 289 ms | 75 ms | x3.9 |
| `srr python3 -c pass` (left native) | 17 ms | 18 ms | x0.96 |

Same output as native in all ten.

- `python -m pytest` gains what `pytest` called directly gains (74 against 76 ms): the form costs nothing. What is left in pytest's 74 ms is pytest's own work at each run, the same in both forms.
- A module of the standard library that loads little (`json.tool`) gains little: there was little to preload.
- Under `exec -t python3`, `SPEEDRUNNER_REQUIRE_FAST` checks `speedrunner exec` itself; the times show pytest is served.
- A call left native costs one client start, about 1 ms here (17 → 18 ms).
