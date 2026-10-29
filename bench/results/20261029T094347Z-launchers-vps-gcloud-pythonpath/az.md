| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `az version` | 153.6 ± 30.2 | 129.0 | 233.5 | 1.79 ± 0.67 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- version` | 85.7 ± 27.3 | 56.6 | 144.8 | 1.00 |
| `az vm create --help` | 398.4 ± 14.7 | 368.1 | 420.3 | 4.65 ± 1.49 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- vm create --help` | 105.7 ± 24.2 | 89.8 | 171.0 | 1.23 ± 0.48 |
| `az group list` | 414.6 ± 22.4 | 374.4 | 449.3 | 4.84 ± 1.56 |
| `/root/speedrunner/repo/client/target/release/speedrunner run az-sr -- group list` | 90.2 ± 20.3 | 71.3 | 144.4 | 1.05 ± 0.41 |
