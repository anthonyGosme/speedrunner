| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 249.5 ± 29.2 | 209.7 | 319.9 | 2.62 ± 0.36 |
| `/root/headstart/repo/client/target/release/headstart run aws -- --version` | 95.3 ± 6.7 | 84.8 | 115.7 | 1.00 |
| `/root/headstart/venv-aws/bin/aws configure list` | 217.0 ± 11.4 | 202.5 | 257.6 | 2.28 ± 0.20 |
| `/root/headstart/repo/client/target/release/headstart run aws -- configure list` | 96.8 ± 7.8 | 89.8 | 122.4 | 1.02 ± 0.11 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 320.9 ± 22.6 | 289.7 | 373.0 | 3.37 ± 0.33 |
| `/root/headstart/repo/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 172.3 ± 14.3 | 155.2 | 225.3 | 1.81 ± 0.20 |
