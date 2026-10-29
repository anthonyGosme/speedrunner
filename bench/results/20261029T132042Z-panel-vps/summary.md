| Runtime | Command | Native (ms) | speedrunner (ms) | Speedup | Identical | Daemon PSS (MB) |
|---|---|---|---|---|---|---|
| python | `aws --version` | 215.2 | 37.9 | x5.67 | yes | 62.1 |
| python | `aws configure list` | 217.7 | 39.8 | x5.48 | yes | 66.3 |
| python | `az --help` | 73.8 | 17.7 | x4.17 | yes | 39.4 |
| python | `az vm --help` | 321.9 | 64.8 | x4.97 | yes | 83.2 |
| python | `gcloud --version` | 368.4 | 81.9 | x4.5 | yes | 90.0 |
| python | `gcloud config list` | 386.6 | 95.6 | x4.05 | yes | 94.2 |
| python | `ansible --version` | 221.7 | 15.4 | x14.43 | yes | 59.5 |
| python | `ansible-doc ping` | 353.6 | 66.3 | x5.33 | yes | 75.8 |
| python | `black --check sample.py` | 94.0 | 10.1 | x9.34 | yes | 43.2 |
| python | `poetry --version` | 267.3 | 20.5 | x13.05 | yes | 75.9 |
| python | `http --offline (httpie)` | 201.7 | 59.7 | x3.38 | yes | 48.3 |
| python | `pre-commit --version` | 87.3 | 10.8 | x8.11 | yes | 27.6 |
| python | `mypy sample.py` | 86.6 | 21.1 | x4.11 | yes | 42.6 |
| python | `pytest -q` | 171.6 | 59.8 | x2.87 | yes | 57.6 |
| python | `flake8 sample.py` | 69.4 | 16.3 | x4.25 | yes | 38.6 |
| python | `yamllint sample.yaml` | 52.6 | 9.9 | x5.31 | yes | 32.4 |
| python | `pip list` | 135.7 | 17.8 | x7.62 | yes | 49.6 |
| ruby | `rubocop --version` | 441.7 | 114.1 | x3.87 | yes | 33.5 |
| ruby | `rubocop sample.rb` | 723.6 | 183.1 | x3.95 | yes | 82.1 |
| ruby | `rake --version` | 76.6 | 6.6 | x11.66 | yes | 17.9 |
| ruby | `bundle --version` | 92.0 | 7.3 | x12.55 | yes | 20.6 |
| ruby | `gem list` | 200.3 | 48.5 | x4.13 | yes | 25.5 |
| ruby | `rails --version` | 174.0 | 30.4 | x5.72 | yes | 19.1 |
| ruby | `rspec sample_spec.rb` | 122.2 | 14.2 | x8.58 | yes | 20.9 |
| ruby | `pod --version` | 358.4 | 42.7 | x8.39 | yes | 37.7 |
| ruby | `solargraph --version` | 743.3 | 61.7 | x12.04 | yes | 56.5 |
| ruby | `rdoc --version` | 225.6 | 14.1 | x15.99 | yes | 28.8 |
| php | `composer --version` | 33.1 | 13.4 | x2.48 | yes | 26.1 |
| php | `composer show` | 96.0 | 45.7 | x2.1 | yes | 25.5 |
| php | `php artisan --version` | 100.7 | 25.8 | x3.91 | yes | 29.0 |
| php | `php artisan list` | 111.5 | 29.1 | x3.83 | yes | 27.0 |
| php | `phpcs Greeter.php` | 45.3 | 14.7 | x3.09 | yes | 25.7 |
| php | `phpunit --version` | 24.4 | 6.4 | x3.81 | yes | 27.8 |
| php | `phploc --version` | 23.3 | 7.2 | x3.23 | yes | 28.5 |
| php | `phpstan analyse` | 642.7 | 650.9 | x0.99 | yes | 29.7 |
| php | `php-cs-fixer --version` | 105.4 | 34.4 | x3.06 | yes | 29.5 |
| php | `psalm --version` | 25.8 | 7.1 | x3.66 | yes | 28.4 |
| php | `rector --version` | 229.7 | 113.9 | x2.02 | yes | 25.2 |
