| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 241.8 ± 19.3 | 211.1 | 285.5 | 5.89 ± 0.87 |
| `/root/headstart/before/client/target/release/headstart run aws -- --version` | 41.0 ± 5.1 | 35.0 | 54.6 | 1.00 |
| `/root/headstart/venv-aws/bin/aws configure list` | 234.1 ± 14.0 | 215.9 | 263.4 | 5.70 ± 0.79 |
| `/root/headstart/before/client/target/release/headstart run aws -- configure list` | 44.3 ± 3.3 | 39.0 | 53.2 | 1.08 ± 0.16 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 343.8 ± 22.8 | 311.0 | 393.1 | 8.38 ± 1.18 |
| `/root/headstart/before/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 111.2 ± 10.3 | 101.1 | 142.0 | 2.71 ± 0.42 |
