# brew, B1: served

Machine: MacBook Pro (Intel i7-9750H), macOS 26.7.3. Homebrew 7.0.11 on its portable Ruby 4.0.9, 192 formulae installed, `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_ANALYTICS=1` and `HOMEBREW_NO_ENV_HINTS=1`. The client of this branch, daemon warm and done learning. 20 runs alternated with the native ones, after 2 warm-ups, every time kept ([served.txt](served.txt)). Served runs set `SPEEDRUNNER_REQUIRE_FAST=1`: a call not served would have failed with 124. Scripts: [served.py](../../brew/served.py), [compare.sh](../../brew/compare.sh).

## What changed

`bin/brew` was already followed as a launcher, on macOS too (ptrace with `PT_TRACE_ME`): the client let it run natively once it reached Ruby. Now the follower stops at a Ruby interpreter as at a Python one, and serves `ruby -W1 --disable=gems,rubyopt brew.rb` by a Ruby daemon started with the same options. Making that work found five problems:

1. **macOS: SIGCHLD taken for ignored**, in every launcher followed on macOS, Python ones included. The kernel reports the signals whose default is to be ignored (CHLD, URG, IO, WINCH, INFO) in `p_sigignore` even at their default. The served child set SIGCHLD to `SIG_IGN`, the kernel reaped its children, and the tool found none to wait for. brew's `SystemCommand` failed on a nil `$?`. A Python tool behind gcloud or az saw `subprocess.call()` return 0 for a child that exited with 5. Fixed in its own pull request, so it can ship first.
2. **fiddle without RubyGems.** Ruby 3.5 made fiddle a bundled gem. Under `--disable=gems` the daemon could not load it, and never started. It now loads it from its gem directory and takes that off the load path.
3. **`Homebrew/os.rb` reads `ARGV` as it loads**, to tell whether brew runs from nix-darwin (`--file /nix/store/…`). The daemon refuses a file that does, and without `global.rb` nothing else loaded. An adapter tolerates that read, and sends natively any call with such an argument.
4. **Parts of a refused file.** `openssl.rb` is refused (it writes `PORTABLE_RUBY_SSL_CERT_FILE`). `openssl/digest.rb` was still accepted: the probe found nothing wrong, because the daemon had loaded `digest` itself. In the child, `require "openssl"` then failed with `superclass mismatch for class OpenSSL::Digest`. The files of a refused file's own directory (`openssl/` for `openssl.rb`) are now refused with it.
5. **A script named through `..`.** The daemon's `load` turns `lib/../x.rb` into `x.rb`, where natively `__FILE__` and `$0` keep the path as named. brew names `brew.rb` by a plain absolute path. Any other path runs natively.

On macOS the client cannot read the stopped process's umask, and `brew.sh` has `umask go-w`. A launcher that mentioned `umask` used to run natively. Now the launcher, and every script a shell runs on its way, is read for `umask` commands. A call is served only if none of them can change the caller's umask: `go-w` changes nothing to the usual 022.

## Same output

19 commands compared byte for byte (stdout and stderr together, and the exit code) with the native run, twice: right after the daemon started, then once it had learned ([compare-1.txt](compare-1.txt), [compare-2.txt](compare-2.txt)). Errors included (`brew nonexistent-cmd`, `brew info no-such-formula`). **19 of 19 identical, both times.** The 20 timed runs of each command below were identical to their native run too.

## Results

| Command | Native | Served | Gain |
|---|---:|---:|---:|
| `brew config` | 1047 ms | 531 ms | **x2.0** |
| `brew desc gh` | 1047 ms | 551 ms | **x1.9** |
| `brew deps gh` | 1061 ms | 562 ms | **x1.9** |
| `brew info gh` | 1071 ms | 574 ms | **x1.9** |
| `brew commands` | 1778 ms | 1138 ms | x1.6 |
| `brew --cache gh` | 1345 ms | 924 ms | x1.5 |
| `brew leaves` | 1693 ms | 1220 ms | x1.4 |
| `brew outdated` | 1845 ms | 1339 ms | x1.4 |
| `brew uses --installed openssl@3` | 2281 ms | 1794 ms | x1.3 |

420 to 640 ms saved on every Ruby command, about 500. B0 projected x2.2 to x2.5 for the quick ones; served, they run 65 to 145 ms slower than that.

## What is not preloaded

The daemon refused 121 files. Nearly all of them come from one root: `openssl.rb`, refused because it changes the environment as it loads. Then come the files that load it (`formula_installer.rb`, `cask/installer.rb`, `livecheck.rb`…), the `extend/os` files that reopen those, and the parts of each. They load at every call, which is most likely where B0's projection was lost. Not measured.

Accepting a learned file that changes the environment, by replaying the change in each child, is the decision still open for Ruby (bundler, for rubocop, is the same case). It would bring openssl and what depends on it back into the preload.

## Not measured

Apple Silicon and Linuxbrew. The CI runs the new tests on both macOS runners and on Linux: a bash launcher that filters the environment and runs `bash -p`, then Ruby with options, as brew does.
