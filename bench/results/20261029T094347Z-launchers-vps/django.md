| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `./manage.py --version` | 192.4 ± 12.7 | 177.4 | 210.8 | 7.68 ± 0.98 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- --version` | 25.0 ± 2.7 | 22.6 | 32.3 | 1.00 |
| `./manage.py check` | 194.0 ± 6.7 | 185.9 | 207.1 | 7.74 ± 0.88 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- check` | 36.2 ± 2.4 | 32.9 | 40.8 | 1.45 ± 0.18 |
| `./manage.py help` | 172.8 ± 7.6 | 162.0 | 188.9 | 6.90 ± 0.81 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- help` | 28.6 ± 2.8 | 24.4 | 33.0 | 1.14 ± 0.17 |
| `./manage.py showmigrations` | 208.4 ± 22.0 | 182.5 | 261.5 | 8.32 ± 1.26 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- showmigrations` | 43.6 ± 1.7 | 41.0 | 46.0 | 1.74 ± 0.20 |
