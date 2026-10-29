# Stage 0 baseline

Interpreter floor (`python -c pass`): median 7.6 ms (stddev 0.5 ms).

Interpreter teardown with awscli imported: ~29.5 ms (median of `import awscli.clidriver` minus the same followed by `os._exit(0)`).

## Native wall time (hyperfine)

| Command | Median (ms) | Mean ± σ (ms) | Min (ms) | Runs |
|---|---|---|---|---|
| `/root/headstart/venv-aws/bin/python -c pass` | 7.6 | 7.7 ± 0.5 | 7.0 | 30 |
| `/root/headstart/venv-aws/bin/python -c 'import awscli.clidriver'` | 138.5 | 139.4 ± 8.1 | 125.0 | 30 |
| `/root/headstart/venv-aws/bin/python -c 'import awscli.clidriver, os; os._exit(0)'` | 109.0 | 109.7 ± 4.0 | 102.7 | 30 |
| `/root/headstart/venv-aws/bin/aws --version` | 243.1 | 243.1 ± 14.6 | 219.0 | 30 |
| `/root/headstart/venv-aws/bin/aws configure list` | 242.0 | 241.5 ± 10.9 | 213.1 | 30 |
| `/root/headstart/venv-aws/bin/aws ec2 run-instances --generate-cli-skeleton` | 315.9 | 319.6 ± 16.0 | 289.6 | 30 |

## Phase breakdown (medians)

Startup + exit = native median − in-process phases. It covers interpreter startup, site import, and interpreter teardown.

| Command | Total | Startup + exit | Imports | create_clidriver | Run | Removable by import preload | + driver preload |
|---|---|---|---|---|---|---|---|
| `aws --version` | 243.1 | 77.4 | 100.4 | 50.0 | 15.5 | 73% | 94% |
| `aws configure list` | 242.0 | 56.5 | 110.5 | 54.9 | 20.1 | 69% | 92% |
| `aws ec2 run-instances --generate-cli-skeleton` | 315.9 | 84.9 | 97.9 | 47.7 | 85.4 | 58% | 73% |

## Go/no-go (threshold: imports + init ≥ 50 % of total)

- `aws --version`: startup + imports = 73% (GO); including create_clidriver = 94% (GO); max speedup if removed: x3.7 / x15.7
- `aws configure list`: startup + imports = 69% (GO); including create_clidriver = 92% (GO); max speedup if removed: x3.2 / x12.0
- `aws ec2 run-instances --generate-cli-skeleton`: startup + imports = 58% (GO); including create_clidriver = 73% (GO); max speedup if removed: x2.4 / x3.7
