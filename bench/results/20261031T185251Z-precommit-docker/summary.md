`pre-commit run --files shop/cart.py`, or a commit of it, the file changed before every call; wall time in ms, median (min–max) over 20 interleaved rounds; speedup over native = ratio of the medians (95% bootstrap interval).

| Way | Command | Time | Speedup | Same verdicts |
|---|---|---:|---:|---|
| native | `pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 1155.7 (1133.5–1259.8) | x1.0 (x0.98–x1.02) | yes |
| srr | `srr pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 980.6 (947.0–1018.0) | x1.18 (x1.17–x1.2) | yes |
| exec | `speedrunner exec pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 982.8 (942.6–1151.7) | x1.18 (x1.17–x1.19) | yes |
| native-system | `pre-commit run --files shop/cart.py -c .pre-commit-config.system.yaml` | 1106.9 (1061.9–1242.3) | x1.04 (x1.03–x1.06) | yes |
| exec-system | `speedrunner exec pre-commit run --files shop/cart.py -c .pre-commit-config.system.yaml` | 276.0 (264.2–324.0) | x4.19 (x4.14–x4.26) | yes |
| git | `git commit -qam edit` | 1124.1 (1086.3–1328.5) | x1.03 (x1.02–x1.05) | yes |
| git-exec | `speedrunner exec git commit -qam edit` | 492.9 (478.0–526.6) | x2.34 (x2.31–x2.38) | yes |

Verdicts (native): black................................................................... Passed, isort................................................................... Passed, flake8.................................................................. Passed, mypy.................................................................... Passed
Versions: pre-commit: pre-commit 4.7.0; black: black, 26.10.1 (compiled: yes); isort: _                 _; flake8: 7.5.0 (mccabe: 0.7.0, pycodestyle: 2.15.0, pyflakes: 4.1.0) CPython 3.11.18 on; mypy: mypy 2.4.1 (compiled: yes)
