| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `./manage.py --version` | 173.7 ± 10.9 | 161.0 | 198.8 | 7.17 ± 1.18 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- --version` | 24.2 ± 3.7 | 21.0 | 33.7 | 1.00 |
| `./manage.py check` | 191.2 ± 15.4 | 169.8 | 218.2 | 7.90 ± 1.36 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- check` | 41.0 ± 6.3 | 32.5 | 50.5 | 1.69 ± 0.37 |
| `./manage.py help` | 173.5 ± 7.0 | 160.4 | 182.9 | 7.17 ± 1.13 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- help` | 30.7 ± 11.6 | 22.9 | 58.1 | 1.27 ± 0.52 |
| `./manage.py showmigrations` | 193.5 ± 13.2 | 177.2 | 217.0 | 8.00 ± 1.33 |
| `/root/speedrunner/repo/client/target/release/speedrunner run ./manage.py -- showmigrations` | 36.6 ± 1.4 | 34.4 | 38.4 | 1.51 ± 0.24 |
