| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `./manage.py --version` | 172.5 ± 12.2 | 156.6 | 201.1 | 7.11 ± 0.53 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- --version` | 32.8 ± 9.7 | 22.9 | 45.5 | 1.35 ± 0.40 |
| `./manage.py check` | 185.8 ± 5.1 | 177.2 | 196.5 | 7.66 ± 0.28 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- check` | 35.4 ± 2.6 | 31.5 | 39.8 | 1.46 ± 0.11 |
| `./manage.py help` | 170.6 ± 8.0 | 161.1 | 185.1 | 7.04 ± 0.37 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- help` | 24.2 ± 0.6 | 23.5 | 25.1 | 1.00 |
| `./manage.py showmigrations` | 212.1 ± 18.7 | 184.2 | 234.5 | 8.75 ± 0.80 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- showmigrations` | 51.9 ± 12.6 | 35.1 | 70.5 | 2.14 ± 0.52 |
