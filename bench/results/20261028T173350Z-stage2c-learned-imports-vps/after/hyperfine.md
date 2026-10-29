| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 221.4 ± 16.4 | 198.1 | 265.3 | 5.36 ± 0.70 |
| `/root/headstart/repo/client/target/release/headstart run aws -- --version` | 41.3 ± 4.4 | 35.6 | 53.6 | 1.00 |
| `/root/headstart/venv-aws/bin/aws configure list` | 243.8 ± 22.9 | 210.5 | 292.4 | 5.90 ± 0.84 |
| `/root/headstart/repo/client/target/release/headstart run aws -- configure list` | 43.8 ± 3.1 | 38.7 | 50.4 | 1.06 ± 0.14 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 312.7 ± 22.6 | 273.6 | 344.1 | 7.57 ± 0.98 |
| `/root/headstart/repo/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 117.1 ± 11.8 | 99.4 | 153.7 | 2.83 ± 0.42 |
