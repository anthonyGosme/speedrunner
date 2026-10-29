```text
 ____                      _                                  
/ ___| _ __   ___  ___  __| |_ __ _   _ _ __  _ __   ___ _ __ 
\___ \| '_ \ / _ \/ _ \/ _` | '__| | | | '_ \| '_ \ / _ \ '__|
 ___) | |_) |  __/  __/ (_| | |  | |_| | | | | | | |  __/ |   
|____/| .__/ \___|\___|\__,_|_|   \__,_|_| |_|_| |_|\___|_|   
      |_|                                                     
```

# speedrunner

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
| `ansible-doc ping` | 393 ms | 70 ms | **x5.6** | 323 ms | ✓ | 76 MB |
| `poetry --version` | 283 ms | 19 ms | **x14.6** | 264 ms | ✓ | 54 MB |
| `ansible --version` | 222 ms | 16 ms | **x14.2** | 206 ms | ✓ | 67 MB |
| `aws configure list` | 239 ms | 44 ms | **x5.4** | 195 ms | ✓ | 71 MB |
| `aws --version` | 224 ms | 39 ms | **x5.7** | 184 ms | ✓ | 61 MB |
| `http --offline (httpie)` | 201 ms | 55 ms | **x3.6** | 146 ms | ✓ | 49 MB |
| `pytest -q` | 203 ms | 73 ms | **x2.8** | 130 ms | ✓ | 56 MB |
| `pip list` | 131 ms | 18 ms | **x7.3** | 113 ms | ✓ | 45 MB |
| `black --check sample.py` | 94 ms | 10 ms | **x9.1** | 83 ms | ✓ | 25 MB |
| `pre-commit --version` | 82 ms | 11 ms | **x7.2** | 71 ms | ✓ | 25 MB |
| `mypy sample.py` | 93 ms | 23 ms | **x4.0** | 70 ms | ✓ | 38 MB |
| `flake8 sample.py` | 73 ms | 16 ms | **x4.4** | 57 ms | ✓ | 37 MB |
| `yamllint sample.yaml` | 62 ms | 10 ms | **x6.3** | 53 ms | ✓ | 21 MB |
| `az vm --help` | 369 ms | 332 ms | **x1.1** | 37 ms | ✓ | 15 MB |
| `az --help` | 73 ms | 76 ms | **x1.0** | -3 ms | ✓ | 12 MB |

No gain: `az` is a shell script that execs `python -m azure.cli`: the daemon only saves the shell's start. Serving the module form (`python -m`) is future work.

**Ruby**

| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `solargraph --version` | 769 ms | 63 ms | **x12.2** | 706 ms | ✓ | 78 MB |
| `rubocop sample.rb` | 722 ms | 196 ms | **x3.7** | 526 ms | ✓ | 26 MB |
| `pod --version` | 378 ms | 47 ms | **x8.1** | 331 ms | ✓ | 38 MB |
| `rubocop --version` | 447 ms | 128 ms | **x3.5** | 320 ms | ✓ | 64 MB |
| `rdoc --version` | 216 ms | 18 ms | **x11.7** | 198 ms | ✓ | 34 MB |
| `gem list` | 208 ms | 47 ms | **x4.4** | 161 ms | ✓ | 26 MB |
| `rails --version` | 181 ms | 32 ms | **x5.6** | 148 ms | ✓ | 21 MB |
| `rspec sample_spec.rb` | 119 ms | 13 ms | **x9.0** | 105 ms | ✓ | 21 MB |
| `bundle --version` | 99 ms | 7 ms | **x14.8** | 93 ms | ✓ | 28 MB |
| `rake --version` | 75 ms | 6 ms | **x12.1** | 69 ms | ✓ | 18 MB |

**PHP**

| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `rector --version` | 233 ms | 123 ms | **x1.9** | 110 ms | ✓ | 27 MB |
| `php artisan list` | 121 ms | 32 ms | **x3.7** | 88 ms | ✓ | 27 MB |
| `php artisan --version` | 107 ms | 25 ms | **x4.2** | 82 ms | ✓ | 28 MB |
| `php-cs-fixer --version` | 100 ms | 38 ms | **x2.6** | 62 ms | ✓ | 28 MB |
| `composer show` | 109 ms | 48 ms | **x2.3** | 62 ms | ✓ | 29 MB |
| `phpstan analyse` | 661 ms | 620 ms | **x1.1** | 41 ms | ✓ | 26 MB |
| `phpcs Greeter.php` | 46 ms | 15 ms | **x3.0** | 31 ms | ✓ | 27 MB |
| `composer --version` | 39 ms | 15 ms | **x2.6** | 24 ms | ✓ | 26 MB |
| `psalm --version` | 27 ms | 8 ms | **x3.6** | 19 ms | ✓ | 29 MB |
| `phpunit --version` | 26 ms | 8 ms | **x3.5** | 19 ms | ✓ | 28 MB |
| `phploc --version` | 25 ms | 7 ms | **x3.4** | 17 ms | ✓ | 29 MB |

No gain: `phpstan analyse` spends its time in the analysis itself, not in the interpreter start.

<!-- panel:end -->

How it works, and the pitfalls met on the way: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT), at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in speedrunner by you, as defined in the Apache-2.0 license, shall
be dual licensed as above, without any additional terms or conditions.
