| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 226.2 ± 20.3 | 199.8 | 297.5 | 2.32 ± 0.27 |
| `/root/headstart/before/client/target/release/headstart run aws -- --version` | 97.4 ± 6.9 | 88.6 | 120.5 | 1.00 |
| `/root/headstart/venv-aws/bin/aws configure list` | 222.9 ± 15.2 | 201.5 | 254.0 | 2.29 ± 0.23 |
| `/root/headstart/before/client/target/release/headstart run aws -- configure list` | 100.6 ± 9.3 | 90.1 | 129.0 | 1.03 ± 0.12 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 306.2 ± 23.5 | 280.3 | 380.8 | 3.15 ± 0.33 |
| `/root/headstart/before/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 180.8 ± 20.4 | 153.8 | 265.2 | 1.86 ± 0.25 |
