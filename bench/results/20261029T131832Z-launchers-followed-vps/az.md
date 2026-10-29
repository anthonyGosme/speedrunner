| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `az version` | 144.8 ± 24.3 | 126.7 | 209.3 | 1.82 ± 0.41 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az -- version` | 79.4 ± 11.7 | 67.2 | 101.1 | 1.00 |
| `az vm create --help` | 386.9 ± 20.0 | 365.5 | 423.2 | 4.88 ± 0.76 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az -- vm create --help` | 101.6 ± 27.0 | 84.6 | 175.7 | 1.28 ± 0.39 |
| `az group list` | 370.5 ± 20.5 | 346.2 | 419.9 | 4.67 ± 0.74 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az -- group list` | 85.7 ± 5.6 | 77.0 | 93.9 | 1.08 ± 0.17 |
