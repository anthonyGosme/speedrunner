# aws v1 and v2, on the same VPS

`bench/aws_v2/vps.py`: the panel's protocol (`bench/panel.py`, `bench/s3_panel.py`: 20 interleaved runs, warm daemon, outputs compared with native), on three commands of the panel and five that send requests to a local S3 server (moto 5.2.5). The same 2-vCPU VPS, one after the other (`machine.txt`). aws-cli 1.46.2 and 2.39.6, the client of this branch. Every run: `v1/panel.json`, `v2/panel.json`, and `summary.md` beside them.

| Command | v1 native | v1 speedrunner | v1 speedup | v2 native | v2 speedrunner | v2 speedup |
|---|---:|---:|---:|---:|---:|---:|
| `aws --version` | 240 ms | 46 ms | x5.2 | 386 ms | 40 ms | **x9.7** |
| `aws configure list` | 229 ms | 50 ms | x4.6 | 401 ms | 48 ms | **x8.3** |
| `aws ec2 run-instances --generate-cli-skeleton` | 316 ms | 82 ms | x3.9 | 535 ms | 112 ms | **x4.8** |
| `aws s3 ls` | 320 ms | 84 ms | x3.8 | 548 ms | 100 ms | **x5.5** |
| `aws s3 ls s3://bucket/` | 312 ms | 81 ms | x3.9 | 534 ms | 97 ms | **x5.5** |
| `aws s3 cp, a small file` | 311 ms | 85 ms | x3.6 | 556 ms | 120 ms | **x4.6** |
| `aws s3api head-object` | 335 ms | 94 ms | x3.6 | 522 ms | 108 ms | **x4.8** |
| `aws sts get-caller-identity` | 319 ms | 86 ms | x3.7 | 486 ms | 98 ms | **x5.0** |

**Why v2 gains more.** Served, the two take about the same time. Natively v2 takes 150 to 240 ms more, and the fork removes that. So the ratio rises: the time saved is 0.35 to 0.45 s per call, against about 0.2 s for v1. Where that extra time goes natively was not measured step by step.

**Outputs.** All identical to native but v1's `ec2 run-instances --generate-cli-skeleton`: its examples come from sets whose order follows Python's hash seed, so two native runs differ too (`VolumeType`, `InstanceType`). v2's is stable.

**Memory.** The panel's PSS of the parent and its spare: 90 to 145 MB for v2, 43 to 119 MB for v1.

**In the README.** It gives v2 only. The two aws rows of its main table come from here, and its statistics were recomputed with them: geometric mean x5.6, where v1 gave x5.5; the median and the counts do not change. `bench/panel_readme.py` would put v1 back.

**Against earlier numbers.** The README used to give v1 as 316 → 71 ms on `aws s3 ls`, x4.5, with speedrunner 0.4.3 (`20261103T180000Z-s3-vps`). Here it is 320 → 84 ms, x3.8: the native is the same, the served run is about 13 ms slower. Whether that comes from what was added since or from the VPS that day was not looked into; the services running on it vary.

**Limits.** A real network adds the same time to both columns, so the ratio drops. A VPS shared with other services, so noisy. aarch64 and macOS not measured.
