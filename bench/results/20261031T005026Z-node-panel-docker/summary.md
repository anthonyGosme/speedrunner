Wall time in ms over 20 interleaved runs each; speedup = ratio of the medians, with its 95% bootstrap confidence interval.

| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median | speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| node | `npm --version` | 154.7 | 156.4 ± 4.7 | 150.6–168.1 | 41.1 | 41.1 ± 1.2 | 38.6–42.9 | x3.76 | x3.68–x3.88 | yes | 58.3 |
| node | `eslint --version` | 266.5 | 268.0 ± 7.5 | 256.7–291.5 | 18.9 | 19.1 ± 1.3 | 17.1–21.9 | x14.11 | x13.52–x14.73 | yes | 66.9 |
| node | `eslint sample.js` | 351.0 | 350.8 ± 6.4 | 337.9–365.6 | 58.1 | 59.6 ± 4.5 | 54.9–73.7 | x6.04 | x5.88–x6.11 | yes | 80.5 |
| node | `prettier --check sample.js` | 235.5 | 237.9 ± 13.0 | 224.4–272.2 | 79.0 | 80.7 ± 6.5 | 74.9–96.5 | x2.98 | x2.88–x3.11 | yes | 58.1 |
| node | `jest --version` | 139.0 | 139.8 ± 4.7 | 133.8–149.1 | 41.1 | 41.6 ± 2.4 | 39.1–48.8 | x3.38 | x3.29–x3.47 | yes | 58.2 |
| node | `next --version` | 194.5 | 195.6 ± 6.1 | 188.1–215.6 | 22.6 | 22.9 ± 1.9 | 20.1–27.4 | x8.62 | x8.33–x8.81 | yes | 60.6 |
| node | `nest --version` | 1030.8 | 1037.0 ± 22.1 | 1013.4–1106.3 | 867.5 | 872.2 ± 18.1 | 850.2–935.7 | x1.19 | x1.18–x1.2 | yes | 97.2 |
| node | `ng version` | 610.1 | 765.7 ± 392.9 | 578.3–1952.9 | 212.6 | 260.4 ± 149.8 | 196.9–862.9 | x2.87 | x2.73–x2.96 | yes | 70.3 |
| node | `webpack --version` | 121.3 | 121.3 ± 4.4 | 115.4–130.6 | 38.5 | 38.2 ± 1.6 | 34.7–42.5 | x3.15 | x3.07–x3.24 | yes | 53.6 |
| node | `vite --version` | 205.2 | 207.5 ± 6.4 | 197.4–222.3 | 368.3 | 370.7 ± 6.3 | 362.2–387.2 | x0.56 | x0.55–x0.57 | yes | 68.1 |
| node | `firebase --version` | 925.3 | 929.8 ± 18.4 | 910.9–971.9 | 137.8 | 138.6 ± 3.3 | 135.0–150.8 | x6.71 | x6.64–x6.78 | yes | 144.6 |
| node | `vercel --version` | 198.1 | 198.9 ± 6.0 | 191.1–211.7 | 148.6 | 150.8 ± 6.0 | 144.7–169.9 | x1.33 | x1.29–x1.35 | yes | 59.8 |
| node | `tsc --version (TypeScript 7, in Go)` | 69.2 | 70.1 ± 3.7 | 65.4–79.9 | 35.6 | 34.4 ± 2.1 | 31.4–37.2 | x1.94 | x1.9–x2.16 | yes | 60.0 |
