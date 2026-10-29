| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `gcloud --version` | 355.8 ± 21.3 | 319.3 | 386.0 | 3.72 ± 1.13 |
| `/root/speedrunner/repo/client/target/release/speedrunner run gcloud -- --version` | 95.8 ± 28.5 | 76.6 | 173.1 | 1.00 |
| `gcloud config list` | 367.8 ± 21.4 | 344.0 | 413.5 | 3.84 ± 1.16 |
| `/root/speedrunner/repo/client/target/release/speedrunner run gcloud -- config list` | 104.0 ± 11.8 | 85.6 | 122.2 | 1.09 ± 0.35 |
| `gcloud compute instances create --help` | 1407.6 ± 55.4 | 1287.3 | 1483.7 | 14.70 ± 4.41 |
| `/root/speedrunner/repo/client/target/release/speedrunner run gcloud -- compute instances create --help` | 395.9 ± 25.8 | 368.3 | 448.9 | 4.13 ± 1.26 |
