| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `/root/headstart/venv-aws/bin/python -c pass` | 7.7 ± 0.5 | 7.0 | 8.9 | 1.00 |
| `/root/headstart/venv-aws/bin/python -c 'import awscli.clidriver'` | 139.4 ± 8.1 | 125.0 | 162.3 | 18.16 ± 1.62 |
| `/root/headstart/venv-aws/bin/python -c 'import awscli.clidriver, os; os._exit(0)'` | 109.7 ± 4.0 | 102.7 | 117.7 | 14.28 ± 1.10 |
| `/root/headstart/venv-aws/bin/aws --version` | 243.1 ± 14.6 | 219.0 | 278.3 | 31.65 ± 2.86 |
| `/root/headstart/venv-aws/bin/aws configure list` | 241.5 ± 10.9 | 213.1 | 259.8 | 31.45 ± 2.56 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 319.6 ± 16.0 | 289.6 | 353.6 | 41.61 ± 3.50 |
