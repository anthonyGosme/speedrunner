| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `az version` | 141.0 ± 7.5 | 131.3 | 152.8 | 1.82 ± 0.23 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- version` | 77.5 ± 8.9 | 67.4 | 91.4 | 1.00 |
| `az vm create --help` | 410.8 ± 25.0 | 375.9 | 441.1 | 5.30 ± 0.69 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- vm create --help` | 115.6 ± 11.4 | 100.0 | 139.2 | 1.49 ± 0.23 |
| `az group list` | 389.3 ± 36.0 | 362.0 | 483.5 | 5.02 ± 0.74 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- group list` | 91.7 ± 22.2 | 63.1 | 128.3 | 1.18 ± 0.32 |
