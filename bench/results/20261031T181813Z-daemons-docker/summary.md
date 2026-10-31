Wall time in ms, median (min–max) over 20 interleaved rounds, the file changed before every call; speedup over native = ratio of the medians (95% bootstrap interval).

| Tool | Native | speedrunner | Its own daemon | speedrunner speedup | Own daemon speedup | speedrunner output identical | Own daemon stdout identical |
|---|---:|---:|---:|---:|---:|---|---|
| `eslint sample.js` vs `eslint_d sample.js` | 328.3 (319.2–342.2) | 43.3 (42.5–48.8) | 106.6 (100.6–129.5) | x7.59 (x7.43–x7.73) | x3.08 (x2.99–x3.15) | yes | yes |
| `prettier --stdin-filepath sample.js` vs `prettierd sample.js` | 210.6 (197.9–252.8) | 62.0 (58.9–68.8) | 104.8 (97.2–125.6) | x3.4 (x3.32–x3.5) | x2.01 (x1.89–x2.07) | yes | yes |
| `rubocop sample.rb` vs `rubocop --server sample.rb` | 297.4 (278.7–369.7) | 107.5 (102.0–121.9) | 302.7 (280.3–501.3) | x2.77 (x2.67–x2.9) | x0.98 (x0.94–x1.03) | yes | NO |
| `mypy sample.py` vs `dmypy run -- sample.py` | 212.5 (198.1–311.0) | 50.1 (46.4–88.8) | 147.2 (138.9–163.0) | x4.24 (x3.27–x4.61) | x1.44 (x1.39–x1.54) | yes | yes |
| `black - (unformatted.py on stdin)` vs `curl -s -XPOST http://127.0.0.1:45484 --data-binary @-` | 240.5 (234.0–260.7) | 25.7 (24.9–26.8) | 19.0 (17.1–24.3) | x9.36 (x9.2–x9.5) | x12.66 (x10.53–x13.3) | yes | yes |

Versions: eslint: v9.39.5; prettier: 3.9.10; rubocop: 1.92.2; mypy: mypy 2.4.1 (compiled: yes); black: black, 26.10.1 (compiled: yes)
