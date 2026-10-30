| Runtime | Command | Native (ms) | speedrunner (ms) | Speedup | Identical | Daemon PSS (MB) |
|---|---|---|---|---|---|---|
| python | `dbt --help` | 3091.0 | 38.8 | x79.61 | yes | 167.8 |
| python | `dbt ls` | 4567.9 | 1342.2 | x3.4 | yes | 179.0 |
| python | `airflow version` | 2685.1 | 117.3 | x22.9 | yes | 141.5 |
| python | `checkov --version` | 3630.2 | 293.0 | x12.39 | yes | 159.3 |
| python | `cfn-lint template.yaml` | 1538.6 | 420.5 | x3.66 | yes | 141.5 |
| python | `sam --version` | 160.2 | 18.2 | x8.8 | yes | 38.4 |
| python | `openstack --version` | 1232.4 | 55.7 | x22.14 | yes | 65.3 |
| python | `oci --version` | 800.2 | 273.1 | x2.93 | yes | 88.2 |
| python | `pylint --version` | 202.3 | 15.4 | x13.14 | yes | 41.9 |
| python | `pylint sample.py` | 697.8 | 350.2 | x1.99 | yes | 64.8 |
| python | `sqlfluff lint sample.sql` | 735.2 | 156.8 | x4.69 | yes | 66.7 |
| python | `pgcli --version` | 504.9 | 22.0 | x22.91 | yes | 96.7 |
| python | `mycli --version` | 627.1 | 23.0 | x27.32 | yes | 122.8 |
| python | `litecli --version` | 359.8 | 20.2 | x17.79 | yes | 54.7 |
| python | `yt-dlp --version` | 306.0 | 24.7 | x12.39 | yes | 37.1 |
| python | `meson --version` | 428.7 | 48.9 | x8.77 | yes | 53.7 |
| python | `conan --version` | 441.8 | 41.5 | x10.65 | yes | 63.6 |
| python | `pio --version` | 303.6 | 24.0 | x12.65 | yes | 53.3 |
| python | `mkdocs --version` | 156.7 | 17.9 | x8.76 | yes | 39.0 |
| python | `pipenv --version` | 598.8 | 36.4 | x16.45 | yes | 61.6 |
| python | `pdm --version` | 496.9 | 81.0 | x6.14 | yes | 58.4 |
| python | `hatch --version` | 159.7 | 16.9 | x9.44 | yes | 38.3 |
| python | `twine --version` | 278.9 | 34.7 | x8.04 | yes | 49.9 |
| python | `bandit --version` | 239.4 | 20.6 | x11.62 | yes | 43.7 |
| python | `codespell --version` | 62.1 | 17.9 | x3.46 | yes | 20.4 |
| python | `cookiecutter --version` | 354.3 | 21.5 | x16.49 | yes | 52.5 |
| python | `copier --version` | 646.9 | 20.1 | x32.11 | yes | 95.6 |
| python | `nox --version` | 220.2 | 23.5 | x9.36 | yes | 41.7 |
| python | `command-not-found sl` | 86.9 | 20.8 | x4.18 | yes | 26.6 |
| ruby | `fastlane --version` | 2604.2 | 721.3 | x3.61 | yes | 112.8 |
| ruby | `kamal version` | 717.2 | 147.4 | x4.87 | yes | 33.1 |
| ruby | `cap --version` | 557.4 | 72.2 | x7.72 | yes | 31.5 |
| ruby | `brakeman --version` | 172.4 | 21.3 | x8.1 | yes | 16.8 |
| ruby | `standardrb sample.rb` | 2633.1 | 1039.5 | x2.53 | yes | 66.8 |
| ruby | `asciidoctor sample.adoc` | 265.3 | 35.3 | x7.51 | yes | 20.2 |
| ruby | `puppet --version` | 1553.8 | 530.2 | x2.93 | yes | 51.3 |
| ruby | `jekyll --version` | 748.2 | 106.4 | x7.03 | yes | 31.8 |
| php | `wp --version` | 202.4 | — | — | not fast: speedrunner: fast path unavailable: not a script of a runtime speedrunner serves | |
| php | `php bin/console --version` | 130.3 | 36.6 | x3.56 | yes | 40.1 |
| php | `php bin/console list` | 157.8 | 40.2 | x3.93 | yes | 40.2 |
| php | `drush --version` | 210.9 | — | — | not fast: speedrunner: fast path unavailable: not a script of a runtime speedrunner serves | |
| php | `pint --version` | 330.4 | 176.0 | x1.88 | yes | 39.8 |
| php | `infection --version` | 103.3 | 30.7 | x3.37 | yes | 40.1 |
| php | `phpmd --version` | 85.2 | 30.2 | x2.82 | yes | 40.0 |
| php | `dep --version` | 96.8 | 37.2 | x2.6 | yes | 40.1 |
| php | `behat --version` | 236.8 | 113.5 | x2.09 | yes | 40.5 |
| php | `pest --version` | 103.9 | 29.3 | x3.55 | yes | 40.0 |
