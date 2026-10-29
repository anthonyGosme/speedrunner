```text
 ____                      _                                  
/ ___| _ __   ___  ___  __| |_ __ _   _ _ __  _ __   ___ _ __ 
\___ \| '_ \ / _ \/ _ \/ _` | '__| | | | '_ \| '_ \ / _ \ '__|
 ___) | |_) |  __/  __/ (_| | |  | |_| | | | | | | |  __/ |   
|____/| .__/ \___|\___|\__,_|_|   \__,_|_| |_|_| |_|\___|_|   
      |_|                                                     
```

Run Python, Ruby and PHP command-line tools from a preloaded, forked daemon from 3x up to 15x faster:
the interpreter start, the imports and the configuration are done once, and
each call starts with them already done. Output is identical to the native run.

![speedrunner demo: the same bash script, run natively and with speedrunner](bench/speedrunner.gif)


```bash
srr aws s3 ls                      # any Python, Ruby or PHP tool, nothing to set up
speedrunner exec ./script.sh       # a whole script: every tool it calls, preloaded
eval "$(speedrunner activate)"     # at the top of a script: no prefix needed after
eval "$(speedrunner init zsh)"     # in ~/.zshrc: daemons warm up while you type
speedrunner enroll aws             # then plain `aws s3 ls` in zsh
```

## Install

Linux, x86_64 or aarch64, any distribution: one static binary, no root.

```bash
curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh
```

It checks the download's SHA-256 sum and installs `speedrunner` and its `srr`
link where your shell finds them: `/usr/local/bin` as root, otherwise
`~/.local/bin` (added to your PATH in `~/.profile`, `~/.bashrc` and `~/.zshrc`
if it is not there yet; `SPEEDRUNNER_NO_MODIFY_PATH=1` to leave them alone). Rather not pipe to `sh`? Take the archive and its
`.sha256` from the [releases](https://github.com/anthonyGosme/speedrunner/releases).
Each tool's daemon runs on that tool's own interpreter: nothing else to install
(PHP tools need PHP's FFI extension; without it they simply run natively).

Then see what it gains on your machine, with the tools you have:

```bash
speedrunner demo
```

## Results

<!-- panel:start -->
Measured on a 2-vCPU Debian 12 VPS (Python 3.11.2, Ruby 3.1.2, PHP 8.2.35), median of 10 runs, warm daemon, no tool-specific setup: each command is simply run as `srr <command>`. *Identical* compares exit code, stdout and stderr with the native run.

**Python**

| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `gcloud config list` | 387 ms | 96 ms | **x4.0** | 291 ms | ✓ | 94 MB |
| `ansible-doc ping` | 354 ms | 66 ms | **x5.3** | 287 ms | ✓ | 76 MB |
| `gcloud --version` | 368 ms | 82 ms | **x4.5** | 286 ms | ✓ | 90 MB |
| `az vm --help` | 322 ms | 65 ms | **x5.0** | 257 ms | ✓ | 83 MB |
| `poetry --version` | 267 ms | 20 ms | **x13.1** | 247 ms | ✓ | 76 MB |
| `ansible --version` | 222 ms | 15 ms | **x14.4** | 206 ms | ✓ | 60 MB |
| `aws configure list` | 218 ms | 40 ms | **x5.5** | 178 ms | ✓ | 66 MB |
| `aws --version` | 215 ms | 38 ms | **x5.7** | 177 ms | ✓ | 62 MB |
| `http --offline (httpie)` | 202 ms | 60 ms | **x3.4** | 142 ms | ✓ | 48 MB |
| `pip list` | 136 ms | 18 ms | **x7.6** | 118 ms | ✓ | 50 MB |
| `pytest -q` | 172 ms | 60 ms | **x2.9** | 112 ms | ✓ | 58 MB |
| `black --check sample.py` | 94 ms | 10 ms | **x9.3** | 84 ms | ✓ | 43 MB |
| `pre-commit --version` | 87 ms | 11 ms | **x8.1** | 76 ms | ✓ | 28 MB |
| `mypy sample.py` | 87 ms | 21 ms | **x4.1** | 66 ms | ✓ | 43 MB |
| `az --help` | 74 ms | 18 ms | **x4.2** | 56 ms | ✓ | 39 MB |
| `flake8 sample.py` | 69 ms | 16 ms | **x4.2** | 53 ms | ✓ | 39 MB |
| `yamllint sample.yaml` | 53 ms | 10 ms | **x5.3** | 43 ms | ✓ | 32 MB |

**Ruby**

| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `solargraph --version` | 743 ms | 62 ms | **x12.0** | 682 ms | ✓ | 56 MB |
| `rubocop sample.rb` | 724 ms | 183 ms | **x4.0** | 540 ms | ✓ | 82 MB |
| `rubocop --version` | 442 ms | 114 ms | **x3.9** | 328 ms | ✓ | 34 MB |
| `pod --version` | 358 ms | 43 ms | **x8.4** | 316 ms | ✓ | 38 MB |
| `rdoc --version` | 226 ms | 14 ms | **x16.0** | 212 ms | ✓ | 29 MB |
| `gem list` | 200 ms | 48 ms | **x4.1** | 152 ms | ✓ | 26 MB |
| `rails --version` | 174 ms | 30 ms | **x5.7** | 144 ms | ✓ | 19 MB |
| `rspec sample_spec.rb` | 122 ms | 14 ms | **x8.6** | 108 ms | ✓ | 21 MB |
| `bundle --version` | 92 ms | 7 ms | **x12.6** | 85 ms | ✓ | 21 MB |
| `rake --version` | 77 ms | 7 ms | **x11.7** | 70 ms | ✓ | 18 MB |

**PHP**

| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `rector --version` | 230 ms | 114 ms | **x2.0** | 116 ms | ✓ | 25 MB |
| `php artisan list` | 112 ms | 29 ms | **x3.8** | 82 ms | ✓ | 27 MB |
| `php artisan --version` | 101 ms | 26 ms | **x3.9** | 75 ms | ✓ | 29 MB |
| `php-cs-fixer --version` | 105 ms | 34 ms | **x3.1** | 71 ms | ✓ | 30 MB |
| `composer show` | 96 ms | 46 ms | **x2.1** | 50 ms | ✓ | 26 MB |
| `phpcs Greeter.php` | 45 ms | 15 ms | **x3.1** | 31 ms | ✓ | 26 MB |
| `composer --version` | 33 ms | 13 ms | **x2.5** | 20 ms | ✓ | 26 MB |
| `psalm --version` | 26 ms | 7 ms | **x3.7** | 19 ms | ✓ | 28 MB |
| `phpunit --version` | 24 ms | 6 ms | **x3.8** | 18 ms | ✓ | 28 MB |
| `phploc --version` | 23 ms | 7 ms | **x3.2** | 16 ms | ✓ | 28 MB |
| `phpstan analyse` | 643 ms | 651 ms | **x1.0** | -8 ms | ✓ | 30 MB |

No gain: `phpstan analyse` spends its time in the analysis itself, not in the interpreter start.

<!-- panel:end -->

How it works, and the pitfalls met on the way: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT), at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in speedrunner by you, as defined in the Apache-2.0 license, shall
be dual licensed as above, without any additional terms or conditions.
