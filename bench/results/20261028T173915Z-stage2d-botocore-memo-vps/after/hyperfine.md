| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 228.5 ± 11.3 | 207.1 | 263.8 | 4.92 ± 0.38 |
| `/root/headstart/repo/client/target/release/headstart run aws -- --version` | 48.6 ± 7.3 | 37.2 | 69.0 | 1.05 ± 0.17 |
| `/root/headstart/venv-aws/bin/aws configure list` | 248.3 ± 19.0 | 213.2 | 306.0 | 5.34 ± 0.51 |
| `/root/headstart/repo/client/target/release/headstart run aws -- configure list` | 46.5 ± 2.7 | 41.6 | 53.4 | 1.00 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 323.8 ± 18.2 | 297.6 | 362.9 | 6.97 ± 0.56 |
| `/root/headstart/repo/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 78.0 ± 5.0 | 70.6 | 93.4 | 1.68 ± 0.15 |
