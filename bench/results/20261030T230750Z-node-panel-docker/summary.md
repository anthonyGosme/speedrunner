Wall time in ms over 20 interleaved runs each; speedup = ratio of the medians, with its 95% bootstrap confidence interval.

| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median | speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| node | `npm --version` | 142.5 | 143.8 ± 5.6 | 137.2–159.6 | 37.6 | 37.5 ± 0.7 | 36.5–38.9 | x3.78 | x3.75–x3.86 | yes | 69.4 |
| node | `eslint --version` | 244.2 | 245.6 ± 6.6 | 236.5–267.4 | 15.8 | 15.8 ± 0.3 | 15.1–16.5 | x15.45 | x15.29–x15.75 | yes | 65.5 |
| node | `eslint sample.js` | 325.3 | 325.7 ± 4.4 | 314.9–332.6 | 50.2 | 50.4 ± 1.3 | 48.9–55.0 | x6.48 | x6.41–x6.58 | yes | 66.5 |
| node | `prettier --check sample.js` | 208.5 | 211.7 ± 9.4 | 201.4–237.9 | 70.0 | 70.3 ± 1.6 | 67.7–75.4 | x2.98 | x2.95–x3.05 | yes | 72.3 |
| node | `jest --version` | 128.6 | 129.1 ± 4.6 | 122.2–137.4 | 35.9 | 36.0 ± 0.5 | 35.3–37.4 | x3.58 | x3.52–x3.65 | yes | 68.3 |
| node | `next --version` | 178.9 | 179.0 ± 4.9 | 172.6–192.2 | 20.5 | 20.8 ± 0.9 | 20.1–24.0 | x8.74 | x8.55–x8.87 | yes | 76.2 |
| node | `nest --version` | 958.5 | 972.4 ± 39.3 | 948.6–1096.6 | 810.7 | 832.6 ± 41.8 | 804.0–920.8 | x1.18 | x1.16–x1.19 | yes | 97.2 |
| node | `ng version` | 549.4 | 560.6 ± 40.5 | 541.8–729.7 | 188.7 | 188.9 ± 5.1 | 181.7–199.9 | x2.91 | x2.87–x2.97 | yes | 74.6 |
| node | `webpack --version` | 119.5 | 120.3 ± 3.5 | 113.6–130.4 | 37.0 | 36.8 ± 1.0 | 34.6–38.5 | x3.23 | x3.19–x3.29 | yes | 64.2 |
| node | `vite --version` | 204.9 | 205.7 ± 5.9 | 199.4–225.1 | — | — | — | — | — | not fast: speedrunner: fast path unavailable: connect: No such file or directory (os error 2) | |
| node | `firebase --version` | 915.5 | 916.9 ± 10.0 | 904.8–950.8 | 136.9 | 137.8 ± 3.4 | 134.4–149.4 | x6.69 | x6.64–x6.73 | yes | 144.2 |
| node | `vercel --version` | 197.3 | 199.1 ± 6.4 | 190.2–211.5 | 149.9 | 150.9 ± 5.8 | 143.7–165.3 | x1.32 | x1.28–x1.37 | yes | 57.2 |
| node | `tsc --version (TypeScript 7, in Go)` | 66.8 | 67.9 ± 4.1 | 63.0–76.3 | 34.3 | 33.7 ± 1.7 | 30.1–36.1 | x1.95 | x1.89–x2.02 | yes | 56.8 |
