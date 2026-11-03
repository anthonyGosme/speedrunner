# aws against S3

`bench/s3_panel.py`: the panel's protocol (`bench/panel.py`: 20 interleaved runs, warm daemon,
outputs compared with native) on five aws commands that send requests, to a local S3 server
(moto 5.2.5) on the same 2-vCPU VPS. The network then takes next to no time, and the
difference is the start-up that speedrunner saves. speedrunner v0.4.3, aws-cli 1.46.2
(`machine.txt`).

| Command | Native | speedrunner | Saved per call | Speedup (95% CI) |
|---|---:|---:|---:|---:|
| `aws s3 ls` | 316 ms | 71 ms | 245 ms | x4.5 (4.2–4.7) |
| `aws s3 ls s3://bucket/` | 323 ms | 74 ms | 250 ms | x4.4 (4.2–4.6) |
| `aws s3 cp`, a small file | 324 ms | 87 ms | 237 ms | x3.7 (3.6–4.0) |
| `aws s3api head-object` | 294 ms | 77 ms | 217 ms | x3.8 (3.7–4.0) |
| `aws sts get-caller-identity` | 277 ms | 71 ms | 206 ms | x3.9 (3.8–4.1) |

All five outputs identical to native. Every run: `panel.json`, `summary.md`.

Against AWS itself, the round trips add the same time to both columns: the time saved per call
stays, about 0.2 s, and the ratio drops with the latency. With 100 ms of round trips, for
instance, `aws s3 ls` would go from about 0.42 to 0.17 s (x2.4); a large transfer gains next to
nothing in ratio.
