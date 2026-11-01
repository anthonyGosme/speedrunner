# speedrunner user guide

Everything you can do with speedrunner, what it does behind your back, and how to
undo it. For what it gains, see the [README](../README.md); for how it is built,
[ARCHITECTURE.md](ARCHITECTURE.md).

- [The idea in one minute](#the-idea-in-one-minute)
- [Calling a tool](#calling-a-tool)
- [Your shell: wrappers and warm-up while you type](#your-shell-wrappers-and-warm-up-while-you-type)
- [Scripts: `exec` and `activate`](#scripts-exec-and-activate)
- [pre-commit and git hooks](#pre-commit-and-git-hooks)
- [Other commands](#other-commands)
- [Why did a call run natively?](#why-did-a-call-run-natively)
- [Environment variables](#environment-variables)
- [Where things live](#where-things-live)
- [Platforms and runtimes](#platforms-and-runtimes)
- [Launchers: az, gcloud](#launchers-az-gcloud)
- [Known limits](#known-limits)
- [Stopping every daemon](#stopping-every-daemon)
- [Uninstall](#uninstall)

## The idea in one minute

A Python, Ruby, PHP or Node tool spends most of a short call starting its
interpreter and loading its code. speedrunner keeps one daemon per tool, with that
code already loaded, and forks it for each call. The forked child gets your
arguments, current directory, environment, umask, terminal and standard streams,
runs the tool, and hands back its exit code.

Two rules hold whatever happens:

- **Before the tool starts**, any problem (no daemon yet, the tool was upgraded, an
  environment the daemon cannot honour) sends the call to the real tool, natively.
  You see the same output, only slower.
- **Once the tool has started**, speedrunner never runs it again, whatever dies. If
  the client loses track of the child, it says so and exits with 125:
  `speedrunner: lost track of aws (pid 4242) after it started; not re-running it`.

## Calling a tool

`srr` is a prefix, like `time` or `nice`. It works for any tool, with nothing to
set up, in a terminal or in a script:

```bash
srr aws s3 ls
srr black --check src/
srr rubocop app/models/user.rb
srr ./manage.py migrate        # a script by path: one daemon per project
srr php artisan route:list     # `php script`: the script gets a daemon of its own
```

`srr` is a link to `speedrunner`. The long form is the same thing:

```bash
speedrunner run aws -- s3 ls   # the `--` is required
```

The first call runs natively while the tool's daemon starts beside it: it is a
little slower than native (15 to 40 ms for aws). The gain starts with the second
call. What `srr` gets that is not a script of a runtime speedrunner serves (`ls`,
a Go or Rust binary) is simply executed: there is nothing to preload.

## Your shell: wrappers and warm-up while you type

Add one line to your shell's startup file:

```bash
eval "$(speedrunner init zsh)"     # in ~/.zshrc  (zsh 5.3 or later)
eval "$(speedrunner init bash)"    # in ~/.bashrc (bash 4.2 or later)
```

Then enroll the tools you want served without a prefix:

```bash
speedrunner enroll aws black rubocop
aws s3 ls                          # now served
speedrunner enroll                 # lists the enrolled tools
```

- `enroll` writes the names to `~/.config/speedrunner/tools`
  (`$XDG_CONFIG_HOME/speedrunner/tools`), one per line. To unenroll a tool, delete
  its line; the change applies to new shells.
- The hook defines a function per enrolled tool:
  `aws() { command speedrunner run aws -- "$@" }`. An alias or function of your own
  with the same name is left alone.
- After `speedrunner enroll`, the current shell gets the new wrapper at once.
- `enroll` refuses what it cannot serve:
  `speedrunner: ls: cannot be preloaded (no shebang); left native`.

**Warm-up while you type.** As soon as you have typed an enrolled tool's name and a
space, the hook runs `speedrunner prewarm <tool>` in the background. That starts
the daemon if needed, so that even the first call of the day is fast, provided
the rest of the line takes you a few hundred milliseconds to type. It also works after the prefix:
`srr ansible ` warms up ansible, enrolled or not. Leading `VAR=value` words are
skipped. It costs a couple of microseconds per keystroke and one background
process per command line.

In bash the warm-up is bound to the space key, in the emacs and vi-insert keymaps.
If your space key already does something else (`magic-space`), the hook leaves it
alone and you lose only the warm-up. macOS ships bash 3.2: the bash hook then says
`speedrunner: the bash hook needs bash 4.2 or later` and sets nothing up; `srr`
works as is, and so does zsh, the default shell.

## Scripts: `exec` and `activate`

Scripts do not see your shell's functions. Two ways to serve every tool a script
calls, without editing each line: `exec`, which also works as a shebang, and
`activate`.

```bash
speedrunner exec ./nightly.sh          # run the script under speedrunner
```

```bash
#!/usr/bin/env -S speedrunner exec bash
# the same, as the script's own shebang (env -S: coreutils 8.30+, macOS, BSD)
```

```bash
#!/bin/bash
eval "$(speedrunner activate)"         # first line of the script: no prefix after it
aws s3 ls
black --check .
```

Both put first on `PATH` a private directory of links named after tools, all
pointing to the client. `aws` in the script then finds the link, and the client
serves the real `aws` further down the `PATH`. `PATH` is inherited, so
subprocesses are covered too: `xargs`, `make`, a script the script runs.

**Auto mode** (no tool named): a link for every Python, Ruby, PHP and Node script
on your `PATH`, plus `php` itself, for `php script` calls. The directory is
rebuilt only when a `PATH` directory changes: a few milliseconds the first time,
next to nothing after. Daemons start:

- at once for enrolled tools;
- at once for the tools the script will call more than once, in the order it
  first calls them. speedrunner reads the script to guess this: a tool in a loop
  (its condition included), in a function called more than once, from a loop or
  recursively, run by `xargs` or `find -exec`, or written twice. Files read by
  `source` or `.` with a literal path are followed;
- for any other tool, at its second call. This catches what the reading cannot
  see (`eval`, a command name in a variable). A tool called once costs nothing.

At most one daemon preloads per CPU at a time; the next starts as soon as one is
ready (`SPEEDRUNNER_START_JOBS` changes the limit). A tool the script reaches
before its daemon is ready runs natively. A wrong guess never changes the output,
only when a daemon starts.

**Naming tools**: `speedrunner exec -t aws -t black ./deploy.sh` or
`eval "$(speedrunner activate aws black)"` links only those tools (plus the
enrolled ones) and starts their daemons at once.

**`--eager`** (`exec --eager …`, `activate --eager`), in auto mode: start the daemon
of every tool the script mentions, even once. Worth it when that single call comes late in the
script, once the daemon is ready; useless if it comes first. Each daemon costs
memory (below) and the CPU of its preload.

A daemon outlives the script: the next run finds it warm, for 4 hours by default.

## pre-commit and git hooks

```bash
speedrunner exec git commit
```

git runs pre-commit's hook by its path, and the hook starts Python by its absolute
path: neither goes through `PATH`. Only the hook's `#!/usr/bin/env bash` does. So
when pre-commit is on `PATH` (auto mode) or named (`-t pre-commit`), `exec` and
`activate` also put a `bash` link next to pre-commit's. Called as `bash` on a hook
written by `pre-commit install`, the client follows it as a
[launcher](#launchers-az-gcloud) and serves pre-commit itself. Any other `bash`
call goes straight to the real bash, at the cost of one more client start (about
4 ms). Nothing in your repository changes.

pre-commit's default hooks run from environments of its own, by absolute path:
they stay native. Hooks declared `language: system` run the tools from your `PATH`
and are served too: black, isort, flake8 and mypy on a changed file, 1.11 s →
0.31 s per commit
([measurements](../bench/results/20261031T214045Z-precommit-hook-docker/ANALYSE.md),
in French).

In auto mode the hook's daemon starts at pre-commit's second call;
`speedrunner exec -t pre-commit git commit`, or enrolling pre-commit, starts it at
the first. That daemon (pre-commit run as `python -m pre_commit`) is not the one
`speedrunner status pre-commit` reports, which serves the `pre-commit` command; it
stops after 4 hours unused, or with the [stop-everything loop](#stopping-every-daemon).

## Other commands

| Command | What it does | Exit code |
|---|---|---|
| `speedrunner demo [--runs N]` | Measures the gain on this machine, with the tools it has (see below). | 0 |
| `speedrunner status <tool>` | Prints the daemon's state: `ready …`, `disabled: <reason>` or `down`. | 0 if ready, else 1 |
| `speedrunner stop <tool>` | Stops the daemon (`stopping`), or prints `down`. | 0 if one stopped, else 1 |
| `speedrunner prewarm <tool>` | Starts the daemon if none is up, or replaces one the tool's upgrade made stale. Returns at once. | 0, 1 if it cannot start one |
| `speedrunner enroll [<tool>…]` | Enrolls tools; with no argument, lists them. | 0, 1 or 127 if refused |
| `speedrunner init zsh\|bash` | Prints the shell hook, for `eval`. | 0 |
| `speedrunner --version` | Prints `speedrunner 0.4.0`. | 0 |

All of them exit with 127 for a tool not found on `PATH`, and 2 on a usage error.
`status`, `stop` and `prewarm` also take `php <script>`
(`speedrunner status php artisan`), and wait up to 3 s for a daemon that is still
starting rather than answer `down`.

**`demo`** runs only a fixed list of well-known tools (aws, pip, ansible, black,
poetry, mypy, pytest, rubocop, rspec, rake, bundle, gem, rails, composer,
phpunit, phpstan, npm, eslint, prettier, jest and some more), and only with
`--version`: never whatever else is on your `PATH`. For each one installed, it
times the native call and the served call (median of `--runs`, 3 by default),
compares their outputs, and stops the daemons it started. On an Intel Mac:

```text
speedrunner demo: 8 tools found here, 3 runs each, median time

  command                     native  speedrunner    gain  same output
  pip3 --version              404 ms        27 ms   15.2x  yes
  rake --version              258 ms        25 ms   10.2x  yes
  gem --version               153 ms        16 ms    9.9x  yes
  rails --version             170 ms runs natively here
  npm --version               228 ms        79 ms    2.9x  yes
  …
```

(`rails` there is macOS's own, on Ruby 2.6, which speedrunner does not serve.)

**`status`** of a ready daemon:

```text
ready pid=53935 preload_ms=369 modules=345 levels=1,2,3 learned=1 blacklisted=0 memo=0 gen=1 tty_read=-
```

`preload_ms` is how long the daemon took to load the tool; `modules`, how many
modules it holds; `levels`, the [preload levels](#environment-variables) in use;
`learned`, the modules it added after watching calls; `blacklisted`, those it
refused (they print, start a thread, change the environment, or fail as they
load); `memo`, data files kept parsed (botocore); `gen`, how many times the
preload grew; `tty_read`, which of fds 0 to 2 the tool asked "is this a terminal?"
as it loaded. `learning=N`, when present, means modules are still being vetted.

## Why did a call run natively?

Ask:

```console
$ SPEEDRUNNER_DEBUG=1 srr pip3 --version
speedrunner: running pip3 natively: connect: No such file or directory (os error 2)
pip 24.3.1 from /usr/local/lib/python3.13/site-packages/pip (python 3.13)
```

Or make a slow call an error, in a benchmark or a test:

```console
$ SPEEDRUNNER_REQUIRE_FAST=1 LANG=C srr pip3 --version
speedrunner: fast path unavailable: startup env differs: LANG
$ echo $?
124
```

With `SPEEDRUNNER_REQUIRE_FAST=1`, the client exits with 124 instead of running
the tool natively, so nothing runs at all.

Each daemon writes a log next to its socket, `<tool>-<hash>.log` in the
[runtime directory](#where-things-live): what started it, how long its preload
took, what it learned or refused, and why it exited.

```console
$ cat /run/user/1000/speedrunner/pip3-*.log
speedrunner[53935] preloaded pip3 in 369 ms, started by run pid=53933; ready pid=53935 …
speedrunner[53935] learned in 65 ms; ready pid=53935 … learned=1 … gen=1 tty_read=-
```

The reasons you can meet, and what to do:

| Reason | Meaning | What to do |
|---|---|---|
| `connect: No such file or directory` | No daemon yet; this call started one. | Nothing: the next call is served. |
| `connect: Connection refused` | The daemon died; this call started another. | Nothing. |
| `runtime dir: …` | The [runtime directory](#where-things-live) is missing (the very first call) or is not yours with mode 0700. | If it persists, check `ls -ld` on it. |
| `connect: path must be shorter than SUN_LEN` | The socket path is too long: `$XDG_RUNTIME_DIR` is too deep. | Use a shorter `XDG_RUNTIME_DIR`, or unset it. |
| `not a script of a runtime speedrunner serves` | A native binary, or a script for another interpreter. | Nothing to gain. |
| `stale: installed files changed` | The tool, a module it loaded, or its interpreter changed. The daemon exits and a new one starts. | Nothing. |
| `startup env differs: LANG` | A variable the interpreter reads as it starts differs from the daemon's: `PYTHON*`, `LANG`, `LC_ALL`, `LC_CTYPE` (Python); `RUBY*`, `GEM_*` and the same locale variables (Ruby); `PHPRC`, `PHP_INI_SCAN_DIR` (PHP); `NODE_*`, `UV_THREADPOOL_SIZE`, `LANG`, `LC_ALL`, `LC_MESSAGES` (Node). | `speedrunner stop <tool>`: the next call starts a daemon with your current environment. |
| `import-time env differs: KEY` | The tool read `KEY` as it loaded, and kept its value. Yours differs from the daemon's. | Same. |
| `import-time env keys differ`, `import-time flag check: --x` (Node) | A module listed the environment's keys, or looked for a flag on the command line, as it loaded. | Same, or live with it. |
| `import-time terminal state differs: fd 1` | The tool asked whether stdout was a terminal as it loaded. The daemon was started from a terminal and this call is piped (`tool \| less`), or the reverse. | Natural; the daemon keeps the state it started with. |
| `gem version chosen on the command line` (Ruby) | A RubyGems binstub was called with `_1.2.3_` to pick a gem version. | Nothing. |
| `credentials differ from the daemon's` | Your group or supplementary groups differ (`newgrp`, `sg`). | Nothing: it would run with the wrong groups. |
| `fd 0 is closed` | One of your standard streams is closed. | Nothing. |
| `the cwd is deleted`, `the cwd has its own json` | A tool run as `python -m module` (az) would import from the current directory. | Nothing: it would see other code natively. |
| `preload failed: …`, `threads running after preload`, `threads appeared in the daemon`, `spare children keep dying` | The daemon cannot fork this tool safely, and `status` shows `disabled: …`. Every call runs natively. | Read the log. `speedrunner stop <tool>` frees its memory. |
| `cannot follow the launcher: …`, `the launcher execs no script or module` | See [launchers](#launchers-az-gcloud). | |
| `handshake: …` | The daemon did not answer within 3 s: still preloading a slow tool, or a machine under heavy load. | |

The daemon takes its environment from the client that started it. The variables
above are the only ones it compares; every other variable is passed to each call
as you have it.

`SPEEDRUNNER_DEBUG` also tells you when a daemon cannot even start:
`speedrunner: cannot start eslint daemon: this build has no Node addon for this libc`.

## Environment variables

Read by the client, on each call:

| Variable | Default | Effect |
|---|---|---|
| `SPEEDRUNNER_DEBUG` | unset | Any value: say on stderr why a call runs natively, or why a daemon cannot start. |
| `SPEEDRUNNER_REQUIRE_FAST` | unset | `1`: exit with 124 instead of running the tool natively. |
| `SPEEDRUNNER_NO_AUTOSTART` | unset | Any value: `run`, `srr`, `exec` and `activate` never start a daemon. `prewarm` and `demo` still do. |
| `SPEEDRUNNER_START_JOBS` | CPUs available | How many daemons `exec` and `activate` let preload at once. |
| `SPEEDRUNNER_DAEMON` | unset | For development: the path of a daemon file. The daemons are looked for in its directory, instead of next to the binary, in `../share/speedrunner`, or the copy built into the binary. |

Read by a daemon when it starts, from the environment of the call that started it.
To change them, stop the daemon and start it again with the new value:

| Variable | Default | Effect |
|---|---|---|
| `SPEEDRUNNER_IDLE_TIMEOUT` | `14400` (4 h) | Seconds without a call before the daemon exits. All runtimes. |
| `SPEEDRUNNER_PRELOAD` | `1,2,3` | Preload levels. 1: the modules the tool's script imports. 2: the modules earlier calls loaded later on, learned. 3: botocore's data files, kept parsed. Python reads all three, Ruby 1 and 2. PHP and Node have no levels. |
| `SPEEDRUNNER_MEMO_MB` | `16` | Python, level 3: megabytes of source JSON kept parsed. |

```bash
SPEEDRUNNER_IDLE_TIMEOUT=600 speedrunner prewarm aws    # this aws daemon exits after 10 idle minutes
```

Read by `install.sh`: `SPEEDRUNNER_VERSION` (a tag such as `v0.4.0`; the latest
release by default), `SPEEDRUNNER_BIN_DIR` (where to install),
`SPEEDRUNNER_NO_MODIFY_PATH=1` (leave your shell's startup files alone) and
`SPEEDRUNNER_BASE_URL` (where to download from, for tests). When building the
client from source, `SPEEDRUNNER_NODE_ADDONS` names a directory of prebuilt Node
addons.

The client sets `SPEEDRUNNER_LAUNCH_ENV` and `SPEEDRUNNER_OBJC_ADDED` for the
daemons it starts on macOS, which remove them at once. Do not set them yourself.

The standard `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and
`XDG_DATA_HOME` move the directories below.

## Where things live

| What | Where |
|---|---|
| Sockets, locks, logs, link directories | `$XDG_RUNTIME_DIR/speedrunner/`, else `/tmp/speedrunner-<uid>/` (the usual case on macOS, under `sudo` and in containers) |
| Enrolled tools | `$XDG_CONFIG_HOME/speedrunner/tools`, else `~/.config/speedrunner/tools` |
| What daemons learned | `$XDG_CACHE_HOME/speedrunner/`, else `~/.cache/speedrunner/`: one `<tool>-<hash>.json` per tool and installed version |
| The daemons themselves | `$XDG_DATA_HOME/speedrunner/daemons/<hash>/`, else `~/.local/share/speedrunner/daemons/<hash>/` |

**The runtime directory** holds, per tool, `<tool>-<hash>.sock` (the daemon's Unix
socket, mode 0600), `.sock.lock` (so that only one daemon starts however many calls
race), `.log`, and for a launcher a `.target` file. The `bin-<hash>/` and
`bin-auto-<hash>/` directories are the links of `exec` and `activate`. The
`<hash>` tells two tools of the same name apart, such as `pip` in two virtual
environments: each has its own daemon.

Before using that directory, the client and the daemons check that it is a real
directory (not a symbolic link), owned by you, with mode exactly 0700. Otherwise a
daemon refuses to start and every call runs natively. Once connected, the client
also checks that the daemon runs as your user, and the daemon checks the same of
the client. speedrunner never listens on a TCP port.

**The daemons** are built into the binary. The client writes them out the first
time it needs them, into a directory named after their content, so an upgraded
speedrunner never runs an older daemon file. After an upgrade, the old directory
can be deleted.

**Memory.** One daemon per tool, while you use it: 28 to 40 MB for a typical
Python, Ruby or PHP tool (median PSS, shared pages split fairly), about 60 MB for a
Node tool, up to 200 MB for the largest. A daemon exits after 4 hours without a
call (`SPEEDRUNNER_IDLE_TIMEOUT`), or with `speedrunner stop <tool>`.

**Upgrading a tool.** `pip install -U awscli`, `gem update`, `npm update`: the
daemon notices. Its fingerprint covers the interpreter, the tool's script, the
installed packages' directories and records, and every module file it loaded,
watched with inotify on Linux and kqueue on macOS. On a change, the call runs
natively, the old daemon exits, and a new one starts in the background. With the
shell hook, typing `aws ` is enough to restart it. PHP tools also rely on OPcache,
which recompiles a changed file.

**Upgrading speedrunner.** A daemon already running keeps serving until it exits.
To switch every tool to the new daemons at once, [stop them all](#stopping-every-daemon).

## Platforms and runtimes

**Linux**, x86_64 and aarch64, any distribution: the binary is static (musl).
Tested with the whole regression suite on Debian 12, Ubuntu 22.04 and 24.04,
Alpine 3.20 and Fedora 42, as root and as an unprivileged user.

**macOS** 11 or later, Intel and Apple Silicon: one universal binary. Python, Ruby,
PHP and Node tools are served, and launchers are followed. What differs:

- a tool module that uses CoreFoundation as it loads (proxy detection, a run
  loop) is not preloaded: forked children could not use it. It loads again on each
  call, so that tool gains less;
- module files are watched with kqueue, which misses a change of attributes alone
  (`touch`, `chmod`); a change of content is seen;
- macOS's own Ruby 2.6 is not served; Homebrew's or another Ruby 3 is;
- a Node built without its internal code cache (Homebrew's `node@22`) is served,
  with a smaller gain;
- the bash hook needs bash 4.2 or later (Homebrew's bash); macOS's bash 3.2 gets
  nothing from it. zsh has no such limit.

**Windows** is not planned.

**Runtimes.** Each daemon runs on the tool's own interpreter, the one its shebang
names: nothing else to install.

| Runtime | Versions | Notes |
|---|---|---|
| Python | 3.9 or later (3.9 to 3.14 tested) | Preload levels 1, 2 and 3. |
| Ruby | 3.0 or later (3.0 to 3.4 tested) | Levels 1 and 2. A binstub's `_x.y.z_` version argument runs natively. |
| PHP | 8.1 or later (8.1 to 8.5 tested) | Needs the FFI, pcntl and posix extensions; uses OPcache. Without them, tools run natively. |
| Node | 22 and 24 tested | Experimental. Learning what a tool loads needs Node 22.15 or later; before that, only Node's own start is saved. |

Node tools run in a daemon started with `--single-threaded`, so that V8 can be
forked: a tool that computes a lot can be slower than natively, and a tool whose
modules start threads as they load (vite) is slower served.

## Launchers: az, gcloud

Some tools are a script that prepares an environment, then `exec`s Python on the
real tool: gcloud is a shell script ending in `exec "$CLOUDSDK_PYTHON" … gcloud.py`,
az (from pip) a Python script ending in `os.execl(sys.executable, …, "-m",
"azure.cli", …)`. Preloading the launcher would gain nothing.

So the client runs the launcher natively, under ptrace, and stops it the instant
it `exec`s the Python interpreter, before a single instruction of the tool runs.
It then serves what the launcher was about to run, with the environment, umask
and ignored signals the launcher set up, by a daemon of its own. The launcher's
preamble runs once per call, as natively (6.6 ms for gcloud, 7.5 ms for az).

```bash
srr gcloud config list
speedrunner enroll gcloud az     # and the shell wrappers serve them too
```

Limits:

- Only a launcher whose last command is an `exec` of Python is followed. wp-cli
  and drush go through two shell scripts and then PHP: they run natively.
- ptrace must be allowed. In a container without it, or with Yama
  `ptrace_scope` set to 3, the client says `cannot follow the launcher: ptrace
  unavailable` and runs the launcher natively, once.
- In scripts, prefix launchers with `srr` (`srr gcloud …`). Auto mode links only
  Python, Ruby, PHP and Node scripts, so az is linked and gcloud, a shell script,
  is not; and `exec -t` and `activate` refuse to link a shell launcher
  (`gcloud: cannot be preloaded …; left native`).
- `prewarm` of a launcher does nothing until one call has been followed. After
  that, it runs the launcher's preamble without arguments to find its target.
- On macOS, a launcher written in Python on a framework build of Python (az
  installed by pip on python.org's or Apple's Python) runs natively; shell
  launchers (gcloud, Homebrew's az) are followed. A launcher whose text mentions
  `umask` runs natively, since macOS does not let one process read another's
  umask.

## Known limits

What a fork from a daemon cannot make identical to a native run. Each is known,
and none runs a command twice.

- **The first call** of a tool runs natively, a little slower than without
  speedrunner, while its daemon starts. Use the shell hook or `exec` to start
  daemons early.
- **No controlling terminal.** The served tool has your terminal on its standard
  streams, but not as its controlling terminal. Python and Ruby tools that open
  `/dev/tty` themselves (a password prompt through `getpass`) are given yours.
  A subprocess the tool starts that opens `/dev/tty` (ssh, sudo, git asking for a
  password) fails, where natively it would find the terminal. Node tools get no
  `/dev/tty` at all.
- **Ctrl-Z** suspends the client, not the tool, which runs on. Ctrl-C, Ctrl-\,
  `kill`, terminal resizes and SIGHUP reach the tool as they would natively.
- **The tool's parent** is the daemon: `getppid()` differs, and the tool leads its
  own process group.
- **`PYTHONHASHSEED`**: every call of a Python tool shares the daemon's hash seed,
  where native runs each draw one. The order of a `set` of strings is then the same
  from call to call. `random` and `uuid` stay random.
- **Values frozen at load.** The daemon checks the environment variables a tool
  reads as it loads, and whether it asks if its streams are terminals; a call that
  would see something else runs natively. It cannot see reads through
  `os.environ.copy()` or C's `getenv`, nor the terminal's size read at load. A
  clock read at load is the daemon's (rspec's load time is corrected).
- **Learned modules are shared.** A module one command loaded is preloaded for all
  commands of that tool. Only code that inspects the list of loaded modules can
  tell. For Node, a module that patches Node's globals as it loads (`graceful-fs`,
  `signal-exit`) has that effect on every command.
- **Teardown happens after you get your prompt back.** The client returns as soon
  as the tool's output is flushed and its exit code known; the interpreter's
  teardown goes on unseen. What it would still print (`Exception ignored in …`
  from a `__del__`) is lost; a file a `__del__` writes appears after the client
  has returned; a `flock` the tool holds is released at the very end.
- **Client killed with `kill -9`**: the tool runs to the end, where natively it
  would die with the client. Closing the terminal sends SIGHUP, which is relayed:
  same as natively.
- **The daemon's own state**: if a daemon is started from a terminal and the tool
  checks for a terminal as it loads, later piped calls run natively, and the
  reverse.
- **Root and other users**: each user has their own daemons; one never serves
  another.

## Stopping every daemon

One tool: `speedrunner stop aws`.

There is no single command for all of them. This loop asks each running daemon to
stop; a call in progress finishes first:

```sh
dir=${XDG_RUNTIME_DIR:+$XDG_RUNTIME_DIR/speedrunner}
for lock in "${dir:-/tmp/speedrunner-$(id -u)}"/*.sock.lock; do
  read -r state pid < "$lock" || continue
  [ "$state" = running ] || continue
  ps -p "$pid" -o command= | grep -q speedrunner_daemon && kill "$pid"
done
```

Each lock file holds `running <pid>` while its daemon runs; the `ps` check skips a
pid left behind by a daemon that crashed. (BusyBox's `ps` has no `-p`; on Alpine,
check `/proc/$pid/cmdline` instead.)

## Uninstall

First [stop every daemon](#stopping-every-daemon), and remove the
`eval "$(speedrunner init …)"` lines from `~/.zshrc` and `~/.bashrc`. Then, by how
you installed it:

**install.sh**

```bash
rm ~/.local/bin/speedrunner ~/.local/bin/srr
```

That is where it installs them for a user, unless `~/bin` was already on your
`PATH` (then there), or `SPEEDRUNNER_BIN_DIR` said otherwise; as root, in
`/usr/local/bin`. `command -v srr` shows which. If it added that directory to your
`PATH`, it left a line ending in `# added by the speedrunner installer` in
`~/.profile`, `~/.bashrc` and `~/.zshrc`: delete it if nothing else needs it.

**Homebrew**

```bash
brew uninstall speedrunner
brew untap anthonyGosme/speedrunner
```

**Debian, Ubuntu**

```bash
sudo apt remove speedrunner
sudo rm /etc/apt/sources.list.d/speedrunner.sources /etc/apt/keyrings/speedrunner.asc   # the APT repository, if you added it
```

**Arch Linux** (built with `makepkg -si` in [packaging/aur](../packaging/aur))

```bash
sudo pacman -R speedrunner-bin
```

**What speedrunner wrote in your home**, the same for every method:

```bash
rm -rf ~/.config/speedrunner ~/.cache/speedrunner ~/.local/share/speedrunner
rm -rf "/tmp/speedrunner-$(id -u)"
[ -n "$XDG_RUNTIME_DIR" ] && rm -rf "$XDG_RUNTIME_DIR/speedrunner"
```

(with your `XDG_*` directories instead, if you set them). No package manager
removes these: they belong to your user, not to the package.
