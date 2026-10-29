| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 224.4 ± 11.9 | 207.0 | 247.9 | 2.65 ± 0.32 |
| `/root/headstart/repo/client/target/release/headstart run aws -- --version` | 84.8 ± 9.3 | 77.2 | 122.1 | 1.00 |
| `/root/headstart/venv-aws/bin/aws configure list` | 223.1 ± 12.7 | 206.9 | 273.3 | 2.63 ± 0.33 |
| `/root/headstart/repo/client/target/release/headstart run aws -- configure list` | 93.3 ± 9.4 | 82.1 | 128.9 | 1.10 ± 0.16 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 337.3 ± 23.0 | 305.6 | 405.0 | 3.98 ± 0.52 |
| `/root/headstart/repo/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 155.3 ± 12.9 | 138.3 | 190.2 | 1.83 ± 0.25 |
