Wall time in ms over 20 interleaved runs each; speedup = ratio of the medians, with its 95% bootstrap confidence interval.

| Runtime | Command | Native median | Native mean ± sd | Native min–max | speedrunner median | speedrunner mean ± sd | speedrunner min–max | Speedup | 95% CI | Identical | Daemon PSS (MB) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| python | `aws --version` | 386.3 | 396.4 ± 38.8 | 356.6–522.7 | 39.8 | 40.6 ± 4.3 | 33.7–51.3 | x9.7 | x9.24–x10.12 | yes | 89.9 |
| python | `aws configure list` | 400.7 | 398.8 ± 21.4 | 357.1–435.2 | 48.4 | 51.8 ± 10.0 | 44.4–79.3 | x8.27 | x7.76–x8.74 | yes | 100.6 |
| python | `aws ec2 run-instances --generate-cli-skeleton` | 535.4 | 537.9 ± 20.3 | 510.2–574.6 | 112.3 | 114.6 ± 9.9 | 105.5–149.4 | x4.77 | x4.55–x4.97 | yes | 123.8 |
| python | `aws s3 ls (local S3)` | 547.8 | 556.3 ± 44.6 | 504.3–668.6 | 99.8 | 101.9 ± 7.5 | 93.2–123.9 | x5.49 | x5.17–x5.73 | yes | 144.6 |
| python | `aws s3 ls s3://speedrunner-bench/ (local S3)` | 533.6 | 534.9 ± 22.4 | 500.8–588.5 | 97.0 | 98.5 ± 7.4 | 88.1–120.1 | x5.5 | x5.27–x5.74 | yes | 141.6 |
| python | `aws s3 cp, a small file (local S3)` | 556.2 | 564.1 ± 40.2 | 518.2–684.6 | 120.2 | 122.1 ± 9.8 | 109.9–146.6 | x4.63 | x4.38–x4.89 | yes | 143.7 |
| python | `aws s3api head-object (local S3)` | 522.4 | 532.6 ± 55.0 | 455.2–687.9 | 108.5 | 113.8 ± 17.5 | 98.1–179.3 | x4.81 | x4.49–x5.01 | yes | 125.7 |
| python | `aws sts get-caller-identity (local)` | 485.5 | 498.9 ± 33.0 | 446.9–576.1 | 97.7 | 100.4 ± 9.3 | 90.9–128.7 | x4.97 | x4.7–x5.21 | yes | 142.7 |
