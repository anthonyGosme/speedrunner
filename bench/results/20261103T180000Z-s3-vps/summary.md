Wall time in ms over 20 interleaved runs each; speedup = ratio of the medians, with its 95% bootstrap confidence interval.

| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median | speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| python | `aws s3 ls (local S3)` | 315.6 | 315.9 ± 19.1 | 286.0–350.3 | 70.9 | 71.8 ± 5.7 | 65.6–91.1 | x4.45 | x4.21–x4.67 | yes | 56.7 |
| python | `aws s3 ls s3://speedrunner-bench/ (local S3)` | 323.4 | 323.6 ± 23.1 | 280.2–368.4 | 73.9 | 74.8 ± 5.7 | 66.4–87.4 | x4.37 | x4.17–x4.59 | yes | 93.7 |
| python | `aws s3 cp, a small file (local S3)` | 323.5 | 325.5 ± 17.0 | 294.7–351.3 | 86.6 | 87.0 ± 10.3 | 71.8–110.5 | x3.73 | x3.62–x3.95 | yes | 98.2 |
| python | `aws s3api head-object (local S3)` | 293.7 | 296.6 ± 12.0 | 277.3–322.5 | 76.9 | 77.5 ± 4.3 | 72.2–89.2 | x3.82 | x3.73–x3.95 | yes | 53.7 |
| python | `aws sts get-caller-identity (local)` | 276.7 | 277.7 ± 14.9 | 258.0–314.1 | 70.5 | 70.5 ± 2.6 | 66.9–76.3 | x3.93 | x3.81–x4.07 | yes | 94.9 |
