# brew, B0: go/no-go

**Corrected after B1** ([B1](../20261116T001200Z-brew-b1-mac/ANALYSE.md)): this analysis said that macOS has no ptrace. It has: the client already followed launchers there with `PT_TRACE_ME`, and only stopped at Python interpreters. B1 needed no new technique; the struck-out claims are marked below.

Machine: MacBook Pro (Intel i7-9750H), macOS 26.7.3. Homebrew 7.0.11 on its portable Ruby 4.0.9, 192 formulae installed, `HOMEBREW_NO_AUTO_UPDATE=1` and `HOMEBREW_NO_ANALYTICS=1` so that no call reaches the network. Medians of 10 runs after 2 warm-ups. No speedrunner client: these are ceilings. Scripts: [bench/brew/](../../brew/).

## Why brew

Nearly everyone on a Mac runs it, and its Ruby commands take a second or more: `brew info gh` 1.05 s, `brew outdated` 1.8 s ([native.txt](native.txt)). speedrunner does not serve it today. `brew` is a bash launcher: `bin/brew` filters the environment and runs `env -i … /bin/bash -p brew.sh`, which computes the call's environment and ends with `exec` of Ruby on `brew.rb`. The client follows a launcher only to a Python interpreter, under ptrace (~~which macOS lacks~~: it does not lack it). A few commands (`--version`, `--prefix`, `list`) never leave bash: 58 to 175 ms, out of scope.

## Method

- **Bash preamble and Ruby part** ([split.py](../../brew/split.py)): `bin/brew`'s final command is read from `bash -x`. `brew.sh` is then sourced in that same environment, with a shell function in place of `exec`: bash outside POSIX mode calls a function before the `exec` builtin. The function records the argv and the environment Ruby would get, and stops. The Ruby part is that command run directly.
- **Ceiling** ([ceiling.rb](../../brew/ceiling.rb), [ceiling.py](../../brew/ceiling.py)): a child runs the command once and reports the files it loaded, in the order their loading started (as the Ruby daemon learns, level 2). The parent requires them, then forks for each run, which loads `brew.rb` with the command's `ARGV`. "X" is when the child's last `at_exit` handler has run, where the Ruby daemon sends the exit code to the client; teardown after it costs about 15 ms.
- **Same output**: every forked run's stdout is compared byte for byte with the native Ruby part's.

## Results

| Command | Native total | Ruby part | Forked, to X | Projected served | Gain |
|---|---:|---:|---:|---:|---:|
| `brew desc gh` | 1021 ms | 812 ms | 194 ms | ~410 ms | **x2.5** |
| `brew deps gh` | 1086 ms | 844 ms | 202 ms | ~450 ms | **x2.4** |
| `brew info gh` | 1075 ms | 836 ms | 236 ms | ~480 ms | **x2.2** |
| `brew config` | 1024 ms | 789 ms | 226 ms | ~465 ms | **x2.2** |
| `brew commands` | 1674 ms | 1469 ms | 667 ms | ~875 ms | x1.9 |
| `brew --cache gh` | 1306 ms | 1081 ms | 474 ms | ~705 ms | x1.9 |
| `brew leaves` | 1707 ms | 1460 ms | 877 ms | ~1130 ms | x1.5 |
| `brew outdated` | 1817 ms | 1564 ms | 973 ms | ~1230 ms | x1.5 |
| `brew uses --installed openssl@3` | 2212 ms | 2061 ms | 1441 ms | ~1600 ms | x1.4 |

"Projected served" is the native total minus the Ruby part, plus the forked time to X, plus 5 ms for the client. The bash preamble stays native, as for any followed launcher. Totals and Ruby parts come from [split.txt](split.txt) where measured there, else from [native.txt](native.txt) and [ceiling.txt](ceiling.txt).

- **9 outputs out of 9 identical** to the native run, every forked run.
- **About 600 ms saved on every Ruby command** (560 to 800), whatever it does: the load of 600 to 1,160 files (`global`, the CLI parser, the command).
- **One thread** after the preload; fork safe on all 90 forked runs. A preloaded process holds 42 MB.

## Where the rest goes

- **The bash preamble, about 240 ms with `bin/brew`, at every call.** It runs some twenty external commands: `git` several times (a `git diff --quiet` on the Homebrew repository), `xcrun --find clang` (24 ms), `xcode-select` three times (9 ms each), `stat`, `sysctl`, `id`. Their results go into the environment Ruby gets, so speedrunner must run it as natively. This is what keeps the quick commands under x3.
- **The command's own work.** Brew's own phase timings (`HOMEBREW_PHASE_TIMINGS`) in a forked call put 123 ms of `brew info gh` in `formula_inflation`, which builds the formula from the API's JSON. `leaves`, `outdated` and `uses` do the same for every installed formula. A Ruby equivalent of Python's level 3 (data files kept parsed) might cut part of it. Not measured.

## Verdict

**GO, with a lower ceiling than the brief's x3.** On the commands people type most (`info`, `deps`, `desc`, `config`), the load is 55 to 61 % of the native time and a call would go from about 1.05 s to about 0.45 s, x2.2 to x2.5. The heavy commands keep their own work and gain x1.4 to x1.9. In absolute terms, every Ruby command saves about 0.6 s.

What B1 needs:

1. **Follow a bash launcher to Ruby.** ~~On macOS, without ptrace, with the `exec` function used here.~~ The ptrace follower, on Linux and macOS, must accept Ruby as a target; the `exec` function was only a measuring tool.
2. **Serve a script with interpreter flags.** `brew.rb` runs with `-W1 --disable=gems,rubyopt`: the flags belong in the daemon's identity.
3. **Staleness.** `brew update` rewrites the Homebrew repository. The files the daemon loaded are watched, so the next call runs natively and the daemon restarts.

Not measured: Apple Silicon, Linux (Linuxbrew), and the end-to-end path through a real client: the projected column adds an estimate, not a measurement.
