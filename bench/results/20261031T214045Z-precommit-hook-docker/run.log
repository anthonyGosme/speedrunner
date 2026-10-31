`pre-commit run --files shop/cart.py`, or a commit of it, the file changed before every call; wall time in ms, median (min–max) over 20 interleaved rounds; speedup over native = ratio of the medians (95% bootstrap interval).

| Way | Command | Time | Speedup | Same verdicts | Same output |
|---|---|---:|---:|---|---|
| native | `pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 1152.6 (1091.9–1752.2) | x1.0 (x0.97–x1.03) | yes | yes |
| srr | `srr pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 990.6 (916.1–1407.4) | x1.16 (x1.12–x1.2) | yes | yes |
| exec | `speedrunner exec pre-commit run --files shop/cart.py -c .pre-commit-config.python.yaml` | 997.5 (938.8–1580.6) | x1.16 (x1.1–x1.2) | yes | yes |
| native-system | `pre-commit run --files shop/cart.py -c .pre-commit-config.system.yaml` | 1106.0 (1058.0–1406.4) | x1.04 (x1.0–x1.07) | yes | yes |
| exec-system | `speedrunner exec pre-commit run --files shop/cart.py -c .pre-commit-config.system.yaml` | 276.0 (260.1–296.4) | x4.18 (x4.04–x4.32) | yes | yes |
| git | `git commit -qam edit` | 1108.5 (1051.2–1282.8) | x1.04 (x1.01–x1.07) | yes | yes |
| git-exec | `speedrunner exec git commit -qam edit` | 306.2 (294.6–391.8) | x3.76 (x3.65–x3.86) | yes | yes |

Verdicts (native): black................................................................... Passed, isort................................................................... Passed, flake8.................................................................. Passed, mypy.................................................................... Passed
Versions: pre-commit: pre-commit 4.7.0; black: black, 26.10.1 (compiled: yes); isort: 9.0.2 (compiled yes); flake8: 7.5.0 (mccabe: 0.7.0, pycodestyle: 2.15.0, pyflakes: 4.1.0) CPython 3.11.18 on; mypy: mypy 2.4.1 (compiled: yes)
