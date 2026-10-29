| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 231.5 ± 11.6 | 214.4 | 256.4 | 1.73 ± 0.14 |
| `/root/headstart/repo/client/target/release/headstart run aws -- --version` | 136.1 ± 13.4 | 121.9 | 191.8 | 1.02 ± 0.12 |
| `/root/headstart/venv-aws/bin/aws configure list` | 231.0 ± 14.7 | 203.7 | 262.7 | 1.73 ± 0.16 |
| `/root/headstart/repo/client/target/release/headstart run aws -- configure list` | 133.5 ± 8.9 | 119.9 | 154.6 | 1.00 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 305.8 ± 18.6 | 276.4 | 351.0 | 2.29 ± 0.21 |
| `/root/headstart/repo/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 234.1 ± 17.2 | 202.1 | 266.9 | 1.75 ± 0.17 |
