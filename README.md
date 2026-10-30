```text
 ____                      _                                  
/ ___| _ __   ___  ___  __| |_ __ _   _ _ __  _ __   ___ _ __ 
\___ \| '_ \ / _ \/ _ \/ _` | '__| | | | '_ \| '_ \ / _ \ '__|
 ___) | |_) |  __/  __/ (_| | |  | |_| | | | | | | |  __/ |   
|____/| .__/ \___|\___|\__,_|_|   \__,_|_| |_|_| |_|\___|_|   
      |_|                                                     
```

Run Python, Ruby and PHP command-line tools from a preloaded, forked daemon, typically 3x to 15x faster
(up to 90x for `dbt --help`):
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

Tested, with the whole regression suite, on Debian 12, Ubuntu 22.04 and 24.04,
Alpine 3.20 and Fedora 42, with Python 3.9 to 3.14, Ruby 3.0 to 3.4 and PHP 8.1
to 8.4, as root and as an unprivileged user (`docker/test-all.sh`).

Then see what it gains on your machine, with the tools you have:

```bash
speedrunner demo
```

## Results

<!-- panel:start -->
Measured on a 2-vCPU Debian 12 VPS (Python 3.11.2, Ruby 3.1.2, PHP 8.2.35), warm daemon, no tool-specific setup: each command is simply run as `srr <command>`. Each time is the median of 20 runs, native and speedrunner interleaved; the speedup is the ratio of the medians, with its 95% confidence interval (bootstrap). *Identical* compares exit code, stdout and stderr with the native run. Mean, standard deviation, min and max: [summary](bench/results/20261030T212139Z-panel-vps/summary.md); every run: [panel.json](bench/results/20261030T212139Z-panel-vps/panel.json).

**38 commands served, 38 identical outputs. Speedup: geometric mean x5.1, median x4.6, from x1.0 to x21.7.**

**Python**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `gcloud config list` | 378 ms | 82 ms | **x4.6** (4.4–4.7) | 295 ms | ✓ | 108 MB |
| `gcloud --version` | 363 ms | 74 ms | **x4.9** (4.8–5.2) | 289 ms | ✓ | 104 MB |
| `ansible-doc ping` | 340 ms | 60 ms | **x5.6** (5.4–6.1) | 279 ms | ✓ | 60 MB |
| `poetry --version` | 287 ms | 13 ms | **x21.7** (20.9–22.8) | 274 ms | ✓ | 87 MB |
| `az vm --help` | 322 ms | 61 ms | **x5.3** (5.0–5.7) | 261 ms | ✓ | 96 MB |
| `aws --version` | 242 ms | 38 ms | **x6.3** (5.8–6.6) | 204 ms | ✓ | 75 MB |
| `ansible --version` | 213 ms | 13 ms | **x15.9** (15.4–16.3) | 200 ms | ✓ | 42 MB |
| `aws configure list` | 238 ms | 43 ms | **x5.5** (5.2–5.7) | 195 ms | ✓ | 73 MB |
| `http --offline (httpie)` | 210 ms | 49 ms | **x4.3** (4.0–4.7) | 160 ms | ✓ | 50 MB |
| `pytest -q` | 187 ms | 62 ms | **x3.0** (3.0–3.2) | 125 ms | ✓ | 33 MB |
| `pip list` | 125 ms | 17 ms | **x7.4** (6.6–7.8) | 108 ms | ✓ | 29 MB |
| `black --check sample.py` | 90 ms | 10 ms | **x9.3** (9.1–9.7) | 80 ms | ✓ | 28 MB |
| `pre-commit --version` | 82 ms | 10 ms | **x8.2** (8.0–8.5) | 72 ms | ✓ | 26 MB |
| `mypy sample.py` | 90 ms | 23 ms | **x3.9** (3.6–4.2) | 67 ms | ✓ | 42 MB |
| `az --help` | 88 ms | 22 ms | **x3.9** (3.8–4.4) | 65 ms | ✓ | 24 MB |
| `flake8 sample.py` | 76 ms | 16 ms | **x4.7** (4.3–4.8) | 60 ms | ✓ | 23 MB |
| `yamllint sample.yaml` | 53 ms | 10 ms | **x5.2** (4.8–5.5) | 43 ms | ✓ | 21 MB |

**Ruby**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `solargraph --version` | 769 ms | 62 ms | **x12.5** (11.6–13.2) | 708 ms | ✓ | 53 MB |
| `rubocop sample.rb` | 714 ms | 196 ms | **x3.6** (3.5–3.9) | 518 ms | ✓ | 53 MB |
| `rubocop --version` | 424 ms | 97 ms | **x4.4** (4.2–4.5) | 328 ms | ✓ | 38 MB |
| `pod --version` | 370 ms | 44 ms | **x8.4** (8.1–8.7) | 326 ms | ✓ | 37 MB |
| `rdoc --version` | 215 ms | 12 ms | **x17.7** (16.9–19.0) | 203 ms | ✓ | 28 MB |
| `gem list` | 194 ms | 48 ms | **x4.0** (3.8–4.1) | 146 ms | ✓ | 25 MB |
| `rails --version` | 172 ms | 34 ms | **x5.1** (4.9–5.5) | 138 ms | ✓ | 22 MB |
| `rspec sample_spec.rb` | 121 ms | 13 ms | **x9.2** (8.6–9.8) | 108 ms | ✓ | 21 MB |
| `bundle --version` | 94 ms | 6 ms | **x14.8** (13.5–15.4) | 88 ms | ✓ | 21 MB |
| `rake --version` | 81 ms | 10 ms | **x8.0** (7.3–9.5) | 71 ms | ✓ | 18 MB |

**PHP**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `rector --version` | 239 ms | 123 ms | **x1.9** (1.9–2.0) | 116 ms | ✓ | 27 MB |
| `php artisan list` | 120 ms | 32 ms | **x3.8** (3.6–3.9) | 88 ms | ✓ | 28 MB |
| `php artisan --version` | 101 ms | 25 ms | **x4.0** (3.9–4.1) | 76 ms | ✓ | 30 MB |
| `php-cs-fixer --version` | 101 ms | 37 ms | **x2.7** (2.6–2.9) | 64 ms | ✓ | 26 MB |
| `composer show` | 99 ms | 49 ms | **x2.0** (1.9–2.1) | 49 ms | ✓ | 26 MB |
| `phpcs Greeter.php` | 43 ms | 14 ms | **x3.0** (2.9–3.1) | 29 ms | ✓ | 26 MB |
| `phpstan analyse` | 663 ms | 636 ms | **x1.0** (1.0–1.1) | 28 ms | ✓ | 24 MB |
| `composer --version` | 36 ms | 15 ms | **x2.4** (2.3–2.6) | 21 ms | ✓ | 28 MB |
| `psalm --version` | 27 ms | 8 ms | **x3.4** (3.3–3.5) | 19 ms | ✓ | 28 MB |
| `phpunit --version` | 26 ms | 8 ms | **x3.3** (3.1–3.6) | 18 ms | ✓ | 28 MB |
| `phploc --version` | 23 ms | 8 ms | **x2.9** (2.5–3.1) | 15 ms | ✓ | 24 MB |

No gain: `phpstan analyse` spends its time in the analysis itself, not in the interpreter start.

<!-- panel:end -->

<details>
<summary><b>47 more commands</b>, measured in Docker on a laptop: dbt x90, copier x35, mycli x28, openstack x25, airflow x23, fastlane, kamal, Symfony…</summary>

<!-- candidates:start -->
Measured on a laptop in Docker: Debian 12, linux/amd64, Intel i7-9750H (Python 3.11.2, Ruby 3.1.2, PHP 8.2.35), warm daemon, no tool-specific setup: each command is simply run as `srr <command>`. Each time is the median of 20 runs, native and speedrunner interleaved; the speedup is the ratio of the medians, with its 95% confidence interval (bootstrap). *Identical* compares exit code, stdout and stderr with the native run. Mean, standard deviation, min and max: [summary](bench/results/20261030T210248Z-candidates-docker/summary.md); every run: [panel.json](bench/results/20261030T210248Z-candidates-docker/panel.json).

**45 commands served, 45 identical outputs. Speedup: geometric mean x7.3, median x7.5, from x1.8 to x90.5.**

**Python**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `checkov --version` | 3212 ms | 256 ms | **x12.6** (12.3–12.9) | 2957 ms | ✓ | 173 MB |
| `dbt ls` | 3858 ms | 1166 ms | **x3.3** (3.2–3.4) | 2692 ms | ✓ | 198 MB |
| `dbt --help` | 2721 ms | 30 ms | **x90.5** (83.8–93.2) | 2691 ms | ✓ | 148 MB |
| `airflow version` | 2254 ms | 98 ms | **x23.1** (22.8–23.4) | 2156 ms | ✓ | 162 MB |
| `openstack --version` | 1055 ms | 42 ms | **x24.9** (23.8–26.6) | 1013 ms | ✓ | 114 MB |
| `cfn-lint template.yaml` | 1293 ms | 361 ms | **x3.6** (3.5–3.6) | 932 ms | ✓ | 143 MB |
| `copier --version` | 592 ms | 17 ms | **x34.7** (33.6–35.7) | 575 ms | ✓ | 52 MB |
| `sqlfluff lint sample.sql (exits 1 natively)` | 669 ms | 141 ms | **x4.8** (4.6–5.0) | 528 ms | ✓ | 42 MB |
| `mycli --version` | 538 ms | 19 ms | **x28.4** (27.9–28.8) | 519 ms | ✓ | 77 MB |
| `pipenv --version` | 537 ms | 33 ms | **x16.4** (16.1–16.8) | 505 ms | ✓ | 45 MB |
| `oci --version` | 702 ms | 233 ms | **x3.0** (2.9–3.1) | 468 ms | ✓ | 97 MB |
| `pgcli --version` | 428 ms | 19 ms | **x22.2** (21.8–23.3) | 409 ms | ✓ | 48 MB |
| `pdm --version` | 456 ms | 73 ms | **x6.3** (6.2–6.3) | 383 ms | ✓ | 40 MB |
| `cookiecutter --version` | 386 ms | 20 ms | **x18.9** (16.4–22.6) | 366 ms | ✓ | 38 MB |
| `conan --version` | 367 ms | 34 ms | **x10.7** (10.6–11.0) | 332 ms | ✓ | 45 MB |
| `meson --version` | 367 ms | 43 ms | **x8.6** (8.4–8.8) | 324 ms | ✓ | 38 MB |
| `pylint sample.py (exits 16 natively)` | 616 ms | 305 ms | **x2.0** (2.0–2.1) | 311 ms | ✓ | 65 MB |
| `bandit --version` | 317 ms | 22 ms | **x14.2** (12.1–15.2) | 294 ms | ✓ | 30 MB |
| `litecli --version` | 307 ms | 18 ms | **x17.4** (17.3–17.7) | 290 ms | ✓ | 40 MB |
| `twine --version` | 300 ms | 35 ms | **x8.6** (7.3–11.7) | 265 ms | ✓ | 34 MB |
| `pio --version` | 263 ms | 22 ms | **x12.2** (11.9–12.3) | 242 ms | ✓ | 37 MB |
| `yt-dlp --version` | 235 ms | 22 ms | **x10.8** (10.4–11.0) | 213 ms | ✓ | 34 MB |
| `nox --version` | 213 ms | 22 ms | **x9.8** (9.5–10.1) | 191 ms | ✓ | 28 MB |
| `pylint --version` | 170 ms | 14 ms | **x11.9** (11.7–12.0) | 156 ms | ✓ | 28 MB |
| `mkdocs --version` | 141 ms | 16 ms | **x9.1** (8.9–9.2) | 125 ms | ✓ | 26 MB |
| `sam --version` | 136 ms | 16 ms | **x8.3** (8.1–8.6) | 120 ms | ✓ | 25 MB |
| `hatch --version` | 135 ms | 16 ms | **x8.4** (8.3–8.6) | 119 ms | ✓ | 25 MB |
| `codespell --version` | 118 ms | 28 ms | **x4.3** (3.8–4.6) | 90 ms | ✓ | 20 MB |
| `command-not-found sl (exits 127 natively)` | 82 ms | 18 ms | **x4.4** (4.3–4.5) | 63 ms | ✓ | 26 MB |

**Ruby**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `fastlane --version` | 2521 ms | 711 ms | **x3.5** (3.4–3.7) | 1810 ms | ✓ | 66 MB |
| `standardrb sample.rb` | 2402 ms | 951 ms | **x2.5** (2.5–2.7) | 1451 ms | ✓ | 70 MB |
| `puppet --version` | 1466 ms | 491 ms | **x3.0** (2.9–3.1) | 975 ms | ✓ | 56 MB |
| `jekyll --version` | 768 ms | 113 ms | **x6.8** (6.4–7.4) | 656 ms | ✓ | 32 MB |
| `kamal version` | 628 ms | 131 ms | **x4.8** (4.3–4.9) | 497 ms | ✓ | 33 MB |
| `cap --version` | 470 ms | 63 ms | **x7.5** (7.4–7.6) | 407 ms | ✓ | 32 MB |
| `asciidoctor sample.adoc` | 255 ms | 43 ms | **x5.9** (5.8–7.3) | 212 ms | ✓ | 20 MB |
| `brakeman --version` | 148 ms | 19 ms | **x7.7** (7.6–7.8) | 128 ms | ✓ | 17 MB |

**PHP**

| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |
|---|---:|---:|---:|---:|:---:|---:|
| `pint --version` | 355 ms | 193 ms | **x1.8** (1.5–2.4) | 162 ms | ✓ | 39 MB |
| `behat --version` | 226 ms | 108 ms | **x2.1** (2.1–2.1) | 118 ms | ✓ | 39 MB |
| `php bin/console list` | 135 ms | 34 ms | **x4.0** (3.9–4.1) | 102 ms | ✓ | 40 MB |
| `php bin/console --version` | 116 ms | 29 ms | **x4.0** (3.8–4.0) | 86 ms | ✓ | 39 MB |
| `pest --version` | 100 ms | 26 ms | **x3.8** (3.7–3.8) | 74 ms | ✓ | 39 MB |
| `infection --version` | 102 ms | 30 ms | **x3.4** (3.3–3.5) | 72 ms | ✓ | 39 MB |
| `dep --version` | 96 ms | 36 ms | **x2.7** (2.6–2.7) | 60 ms | ✓ | 39 MB |
| `phpmd --version` | 93 ms | 34 ms | **x2.7** (2.4–3.1) | 59 ms | ✓ | 39 MB |
| `wp --version` | 183 ms | — | not served | — | — | |
| `drush --version` | 203 ms | — | not served | — | — | |

`wp` and `drush` are not served: Composer installs them behind a shell script that runs PHP, and speedrunner follows such launchers only up to Python.

<!-- candidates:end -->
</details>

How it works, and the pitfalls met on the way: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT), at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in speedrunner by you, as defined in the Apache-2.0 license, shall
be dual licensed as above, without any additional terms or conditions.
