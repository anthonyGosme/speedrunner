| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `gcloud --version` | 337.9 ± 28.9 | 305.9 | 400.2 | 4.22 ± 0.47 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- --version` | 89.7 ± 26.6 | 68.4 | 156.8 | 1.12 ± 0.34 |
| `gcloud config list` | 350.0 ± 25.3 | 324.1 | 417.7 | 4.37 ± 0.45 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- config list` | 80.1 ± 5.8 | 72.6 | 90.7 | 1.00 |
| `gcloud compute instances create --help` | 1388.7 ± 103.7 | 1236.7 | 1548.5 | 17.33 ± 1.81 |
| `/root/speedrunner/repo/client/target/release/speedrunner run .gcloud-sr -- compute instances create --help` | 371.2 ± 23.6 | 344.2 | 427.5 | 4.63 ± 0.45 |
