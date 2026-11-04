Plain against served, 5 measured calls per setup, after 4 warm calls.

| Runtime | Command | Served | Files | Frozen | Output, exit code | Null run | Left out as noise |
|---|---|---|---:|---:|---:|---:|---:|
| python | `aws --version` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `aws configure list` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `az --help` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `az vm --help` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `gcloud --version` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `gcloud config list` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `ansible --version` | yes | 3 | 0 | 0 | 0 | 0 |
| python | `ansible-doc ping` | yes | 3 | 0 | 0 | 0 | 0 |
| python | `black --check sample.py` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `poetry --version` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `http --offline (httpie)` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `pre-commit --version` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `mypy sample.py` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `pytest -q` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `flake8 sample.py` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `yamllint sample.yaml` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `pip list` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rubocop --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rubocop sample.rb` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rake --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `bundle --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `gem list` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rails --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rspec sample_spec.rb` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `pod --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `solargraph --version` | yes | 0 | 0 | 0 | 0 | 0 |
| ruby | `rdoc --version` | yes | 0 | 0 | 0 | 0 | 0 |
| python | `ansible -m copy` | yes | 3 | 0 | 2 | 0 | 0 |

### `ansible --version`

- `state-extra` ansible/ansible-local-29398037ro_9owo: {}
- `state-extra` ansible/ansible-local-2939805boj2girh: {}
- `state-extra` ansible/ansible-local-2939807by29ng33: {}

### `ansible-doc ping`

- `state-extra` ansible/ansible-local-29405365ln283zn: {}
- `state-extra` ansible/ansible-local-29405387w0x_j0s: {}
- `state-extra` ansible/ansible-local-2940540jdnn6ur1: {}

### `ansible -m copy`

- `state-extra` ansible/ansible-local-2955441w51al_dc: {}
- `state-extra` ansible/ansible-local-295544332dlvcc1: {}
- `state-extra` ansible/ansible-local-2955445c6nib1ux: {}
- `exit-code` : {"plain": [0], "served": [2, 2, 2, 2, 2]}
- `output` stdout: {"plain": "14 lines", "served": "[10] lines"}
