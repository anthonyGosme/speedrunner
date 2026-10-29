| Runtime | Command | Native (ms) | speedrunner (ms) | Speedup | Identical | Daemon PSS (MB) |
|---|---|---|---|---|---|---|
| python | `aws --version` | 223.5 | 39.0 | x5.74 | yes | 61.2 |
| python | `aws configure list` | 239.3 | 44.1 | x5.42 | yes | 71.0 |
| python | `az --help` | 73.3 | 76.1 | x0.96 | yes | 11.9 |
| python | `az vm --help` | 369.0 | 331.8 | x1.11 | yes | 14.8 |
| python | `ansible --version` | 221.5 | 15.6 | x14.21 | yes | 66.9 |
| python | `ansible-doc ping` | 393.0 | 69.9 | x5.62 | yes | 75.5 |
| python | `black --check sample.py` | 93.6 | 10.3 | x9.12 | yes | 25.2 |
| python | `poetry --version` | 282.9 | 19.4 | x14.6 | yes | 54.4 |
| python | `http --offline (httpie)` | 201.0 | 55.2 | x3.65 | yes | 48.9 |
| python | `pre-commit --version` | 82.3 | 11.4 | x7.22 | yes | 25.0 |
| python | `mypy sample.py` | 93.0 | 23.0 | x4.05 | yes | 38.0 |
| python | `pytest -q` | 203.2 | 73.1 | x2.78 | yes | 56.2 |
| python | `flake8 sample.py` | 73.1 | 16.5 | x4.44 | yes | 36.9 |
| python | `yamllint sample.yaml` | 62.4 | 9.8 | x6.34 | yes | 20.8 |
| python | `pip list` | 131.0 | 18.0 | x7.29 | yes | 45.3 |
| ruby | `rubocop --version` | 447.1 | 127.5 | x3.51 | yes | 64.4 |
| ruby | `rubocop sample.rb` | 721.7 | 196.0 | x3.68 | yes | 26.2 |
| ruby | `rake --version` | 75.3 | 6.2 | x12.09 | yes | 17.7 |
| ruby | `bundle --version` | 99.4 | 6.7 | x14.79 | yes | 27.8 |
| ruby | `gem list` | 208.2 | 47.4 | x4.39 | yes | 25.6 |
| ruby | `rails --version` | 181.0 | 32.5 | x5.57 | yes | 21.4 |
| ruby | `rspec sample_spec.rb` | 118.6 | 13.2 | x8.99 | yes | 20.6 |
| ruby | `pod --version` | 378.1 | 46.9 | x8.06 | yes | 37.6 |
| ruby | `solargraph --version` | 769.2 | 63.2 | x12.16 | yes | 78.4 |
| ruby | `rdoc --version` | 216.0 | 18.5 | x11.68 | yes | 33.9 |
| php | `composer --version` | 38.9 | 15.1 | x2.58 | yes | 26.4 |
| php | `composer show` | 109.2 | 47.7 | x2.29 | yes | 28.9 |
| php | `php artisan --version` | 106.7 | 25.2 | x4.23 | yes | 28.3 |
| php | `php artisan list` | 120.8 | 32.5 | x3.72 | yes | 27.0 |
| php | `phpcs Greeter.php` | 45.7 | 15.0 | x3.05 | yes | 26.9 |
| php | `phpunit --version` | 26.1 | 7.5 | x3.5 | yes | 28.4 |
| php | `phploc --version` | 24.7 | 7.4 | x3.35 | yes | 28.8 |
| php | `phpstan analyse` | 661.0 | 619.8 | x1.07 | yes | 26.2 |
| php | `php-cs-fixer --version` | 100.4 | 38.2 | x2.62 | yes | 27.5 |
| php | `psalm --version` | 26.9 | 7.5 | x3.59 | yes | 28.6 |
| php | `rector --version` | 233.0 | 123.0 | x1.89 | yes | 27.2 |
