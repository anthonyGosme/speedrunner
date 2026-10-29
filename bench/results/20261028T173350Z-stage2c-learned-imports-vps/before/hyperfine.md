| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/aws --version` | 222.5 ± 10.2 | 207.9 | 243.9 | 2.45 ± 0.30 |
| `/root/headstart/before/client/target/release/headstart run aws -- --version` | 95.9 ± 10.9 | 78.2 | 120.5 | 1.06 ± 0.17 |
| `/root/headstart/venv-aws/bin/aws configure list` | 230.3 ± 16.9 | 207.1 | 275.0 | 2.54 ± 0.34 |
| `/root/headstart/before/client/target/release/headstart run aws -- configure list` | 90.8 ± 10.2 | 78.8 | 129.7 | 1.00 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 314.9 ± 17.1 | 285.0 | 362.6 | 3.47 ± 0.43 |
| `/root/headstart/before/client/target/release/headstart run aws -- ec2 run-instances --generate-cli-skeleton` | 150.9 ± 11.2 | 132.4 | 184.8 | 1.66 ± 0.22 |
