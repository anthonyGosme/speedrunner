| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `gcloud --version` | 330.8 ± 25.4 | 306.8 | 391.5 | 1.71 ± 0.19 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- --version` | 193.5 ± 15.2 | 173.1 | 224.3 | 1.00 |
| `gcloud config list` | 368.7 ± 13.6 | 340.9 | 389.2 | 1.91 ± 0.17 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- config list` | 220.0 ± 29.7 | 192.7 | 292.9 | 1.14 ± 0.18 |
| `gcloud compute instances create --help` | 1496.0 ± 32.5 | 1442.1 | 1541.3 | 7.73 ± 0.63 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- compute instances create --help` | 1049.1 ± 94.3 | 945.1 | 1235.8 | 5.42 ± 0.65 |
