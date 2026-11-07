Wall time in ms over 20 interleaved runs each; speedup = ratio of the medians, with its 95% bootstrap confidence interval.

| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median | speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| python | `aws --version` | 240.1 | 246.1 ± 24.0 | 211.5–312.8 | 45.8 | 49.7 ± 11.9 | 41.3–86.2 | x5.24 | x4.97–x5.54 | yes | 44.5 |
| python | `aws configure list` | 229.3 | 240.0 ± 38.4 | 212.4–391.5 | 49.8 | 51.2 ± 5.4 | 45.9–65.2 | x4.61 | x4.43–x4.9 | yes | 42.5 |
| python | `aws ec2 run-instances --generate-cli-skeleton` | 315.6 | 319.6 ± 21.9 | 296.4–390.4 | 81.5 | 82.5 ± 6.0 | 74.5–98.9 | x3.87 | x3.62–x4.08 | NO | 90.3 |
| python | `aws s3 ls (local S3)` | 320.3 | 321.1 ± 25.5 | 278.7–363.9 | 83.5 | 85.2 ± 10.1 | 74.4–109.3 | x3.83 | x3.61–x4.19 | yes | 108.3 |
| python | `aws s3 ls s3://speedrunner-bench/ (local S3)` | 312.4 | 315.4 ± 20.3 | 290.7–384.5 | 81.2 | 84.2 ± 8.4 | 75.2–111.1 | x3.85 | x3.65–x3.99 | yes | 116.4 |
| python | `aws s3 cp, a small file (local S3)` | 310.8 | 309.9 ± 25.2 | 273.7–357.8 | 85.2 | 89.2 ± 11.5 | 78.4–127.7 | x3.65 | x3.29–x3.87 | yes | 113.0 |
| python | `aws s3api head-object (local S3)` | 334.8 | 345.2 ± 32.2 | 298.7–414.8 | 93.9 | 95.4 ± 6.5 | 85.9–106.3 | x3.57 | x3.31–x3.85 | yes | 119.1 |
| python | `aws sts get-caller-identity (local)` | 319.1 | 318.2 ± 11.3 | 299.4–339.5 | 85.9 | 87.7 ± 6.1 | 79.0–102.4 | x3.71 | x3.55–x3.8 | yes | 115.6 |
