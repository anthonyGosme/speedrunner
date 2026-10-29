| Runtime | Command | Native (ms) | speedrunner (ms) | Speedup | Identical | Daemon PSS (MB) |
|---|---|---|---|---|---|---|
| python | `aws --version` | 221.3 | 37.9 | x5.83 | yes | 60.0 |
| python | `aws configure list` | 268.1 | 45.2 | x5.93 | yes | 48.1 |
| python | `az --help` | 73.8 | 75.4 | x0.98 | yes | 12.1 |
| python | `az vm --help` | 314.2 | 310.7 | x1.01 | yes | 14.9 |
| python | `ansible --version` | 246.7 | 15.2 | x16.2 | yes | 58.9 |
| python | `ansible-doc ping` | 394.5 | 66.9 | x5.9 | yes | 80.3 |
| python | `black --check sample.py` | 94.0 | 11.5 | x8.19 | yes | 24.3 |
| python | `poetry --version` | 303.3 | 21.6 | x14.05 | yes | 55.0 |
| python | `http --offline (httpie)` | 188.8 | 19.0 | x9.94 | yes | 62.9 |
| python | `pre-commit --version` | 84.3 | 10.6 | x7.93 | yes | 40.0 |
| python | `mypy sample.py` | 91.2 | 22.6 | x4.03 | yes | 41.3 |
| python | `pytest -q` | 186.1 | 60.5 | x3.08 | yes | 51.8 |
| python | `flake8 sample.py` | 75.7 | 17.9 | x4.23 | yes | 35.8 |
| python | `yamllint sample.yaml` | 51.4 | 10.6 | x4.84 | yes | 20.5 |
| python | `pip list` | 153.7 | 20.9 | x7.35 | yes | 42.9 |
| ruby | `rubocop --version` | 442.6 | 130.2 | x3.4 | yes | 39.5 |
| ruby | `rubocop sample.rb` | 756.4 | 209.5 | x3.61 | yes | 80.5 |
| ruby | `rake --version` | 82.2 | 6.5 | x12.62 | yes | 18.4 |
| ruby | `bundle --version` | 97.3 | 8.4 | x11.64 | yes | 20.3 |
| ruby | `gem list` | 196.5 | 47.8 | x4.11 | yes | 25.3 |
| ruby | `rails --version` | 202.7 | 35.5 | x5.71 | yes | 21.4 |
| ruby | `rspec sample_spec.rb` | 131.1 | 10.6 | x12.39 | yes | 20.4 |
| ruby | `pod --version` | 391.5 | 47.4 | x8.25 | yes | 52.5 |
| ruby | `solargraph --version` | 775.6 | 59.7 | x13.0 | yes | 49.8 |
| ruby | `rdoc --version` | 217.6 | 12.5 | x17.43 | yes | 28.6 |
| php | `composer --version` | 34.4 | 14.0 | x2.45 | yes | 30.8 |
| php | `composer show` | 103.9 | 50.3 | x2.07 | yes | 23.6 |
| php | `php artisan --version` | 103.5 | 23.7 | x4.37 | yes | 28.7 |
| php | `php artisan list` | 116.1 | 29.2 | x3.98 | yes | 28.9 |
| php | `phpcs Greeter.php` | 44.6 | 15.9 | x2.8 | yes | 25.7 |
| php | `phpunit --version` | 24.9 | 8.9 | x2.79 | yes | 24.7 |
| php | `phploc --version` | 22.1 | 5.8 | x3.83 | yes | 26.1 |
| php | `phpstan analyse` | 644.7 | 650.9 | x0.99 | yes | 28.9 |
| php | `php-cs-fixer --version` | 100.4 | 33.7 | x2.98 | yes | 29.4 |
| php | `psalm --version` | 26.3 | 8.4 | x3.14 | yes | 29.1 |
| php | `rector --version` | 229.6 | 121.4 | x1.89 | yes | 28.5 |
