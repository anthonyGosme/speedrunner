| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 216.5 ± 10.4 | 201.6 | 239.4 | 1.66 ± 0.13 |
| `/root/headstart/before/client/target/release/headstart run aws -- --version` | 134.6 ± 13.0 | 118.8 | 170.0 | 1.03 ± 0.12 |
| `/root/headstart/venv-aws/bin/aws configure list` | 214.7 ± 10.2 | 197.1 | 236.9 | 1.65 ± 0.13 |
| `/root/headstart/before/client/target/release/headstart run aws -- configure list` | 130.1 ± 8.1 | 119.1 | 152.3 | 1.00 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 301.5 ± 11.9 | 282.1 | 332.1 | 2.32 ± 0.17 |
| `/root/headstart/before/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 226.7 ± 31.2 | 202.6 | 373.2 | 1.74 ± 0.26 |
