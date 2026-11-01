# speedrunner — architecture and handover

This document describes what is built, how the pieces fit together, where the project stands, and what to know to carry on without falling into the pitfalls already met. State as of 2026-11-01, version 0.4.0: Python, Ruby, PHP and Node (experimental), on Linux (x86_64 and aarch64) and macOS (Intel and Apple Silicon, §2 quinquies).

## 1. In one sentence

A daemon keeps an already initialised Python CLI in memory and serves each call with `fork()`. A tiny Rust client hands it the call, and the shell wakes the daemon up while the user types. Measured result: `aws --version` goes from ~230 ms to ~37 ms (x6), `pip --version` from 121 to 14 ms (x8.7), with output identical to the native run.

## 2. What is where

```
docs/
  ARCHITECTURE.md         this document
  prior-art.md            study of JumpTheGun, quicken, python-preloaded
  multi-runtime.md        Ruby / Node / JVM / CRIU: proposal and measurements
server/speedrunner_daemon.py  the Python daemon, standard library only
server/speedrunner_daemon.rb  the Ruby daemon, same protocol, standard library only
server/speedrunner_daemon.php the PHP daemon, same protocol (pcntl, posix, FFI)
server/speedrunner_daemon.mjs the Node daemon, same protocol
server/speedrunner_node.c   the Node daemon's addon: fork, fd passing, signals, pty
client/src/main.rs        the client, Rust, one dependency: libc (=0.2.175)
client/build.rs           embeds the Node addon, built for glibc and musl (Linux) or for the target (macOS)
client/src/launcher.rs    following launchers (az, gcloud) under ptrace
client/src/platform.rs    what differs between Linux and macOS on the client side
client/src/script.rs      reads a script to know which tools it calls more than once
shell/speedrunner.zsh       wrappers and anticipation for zsh ≥ 5.3
shell/speedrunner.bash      the same for bash ≥ 4.2
tests/
  test_matrix.py          regression matrix, native against speedrunner
  test_shell.py           zsh and bash integration, driven in a real pty
  test_ruby.py            Ruby matrix (rbtest), on the test_matrix.py harness
  test_php.py             PHP matrix (phptest, composer, artisan)
  test_node.py            Node matrix (nodetest, nodetty)
  test_stress.py          races: parallel calls while daemons stop, die, go stale
  test_scripts.py         activate, exec, automatic mode, pre-commit hook
  fixture/hstest/         Python test tool: one subcommand per behaviour
  fixture/rbtest/         Ruby test tool: a gem with a real binstub
  fixture/phptest/        PHP test tool, a plain script, no installation
  fixture/nodetest/       Node test tools, no installation
bench/
  baseline.sh, phases.py, summarize.py   stage 0
  preload_ceiling.py      prototype of the preload levels, without the client
  warm.sh                 native against warm daemon (hyperfine)
  phase_client.py         splits a call: handshake / run / teardown
  anticipation.py         daemon stopped, then prewarm, then D ms of typing, then the run
  keystroke.zsh           cost of the zsh hook per keystroke
  script.sh               scripted loops with `srr`
  launchers.sh            launcher CLIs (az, gcloud) and `./manage.py`
  node/                   Node go/no-go: image, fork prototype, ceilings
  node_panel.py           panel of Node CLIs, in the image from node/
  macos/                  macOS: ceilings and CoreFoundation cases (M0), canary and warm bench (M1)
  results/<date>-<stage>-<machine>/   every measurement, with an ANALYSE.md (in French)
docker/                   test images and docker/test-all.sh (§10 bis)
packaging/                .deb, APT repository, AUR, Homebrew (§10 bis)
```

The code lives in the repository https://github.com/anthonyGosme/speedrunner.

## 2 bis. Several runtimes

The client reads the tool's shebang and picks the daemon of its runtime: `server/speedrunner_daemon.py` for a `python*` interpreter, `server/speedrunner_daemon.rb` for `ruby*`, `server/speedrunner_daemon.php` for `php*`, `server/speedrunner_daemon.mjs` for `node`, `nodeN` or `nodejs`. The daemon is started by the tool's own interpreter. The protocol, the process model (parent, spare, child), the lock, security, the inotify fingerprint and rule no. 1 are the same everywhere: the Ruby daemon is a port of the Python daemon, with levels 1 and 2. What is specific to Ruby:

- **Level 2.** The child learns the files loaded, through `TracePoint(:script_compiled)`, in the order in which loading *starts*, along with the activated gems. After X, it sends them to the parent. The parent has them checked by a probe (same rules as in Python), activates the gems, requires the accepted files by their absolute path, then caches the lot. In addition, a throwaway child first replays every file in native order, refused ones included, and records the namespaces each file opens while it loads (`TracePoint(:class)`). The probe refuses a file that opens other namespaces without the refused files: it depends on what they define (see §11, fastlane).
- **Level 1.** It preloads the script's top-level `require`s. For a RubyGems binstub, it also activates the gem (`Gem.activate_bin_path`), then preloads the top-level `require`s of the gem's executable. A `_x.y.z_` argument (version choice by the binstub) runs natively.
- **Run.** `load script`, and exceptions such as `exit` propagate up to Ruby, as in a native run. The last `at_exit` (registered first) cleans up the backtrace, prints an uncaught exception itself (Ruby would do it after the `at_exit` handlers), flushes the files opened during the call (tracked by a `prepend` on `IO#initialize`), then sends X. Interrupt and SignalException send no X: Ruby dies of the signal, and W is what counts.
- **Context.** `IO#reopen` for fds 0/1/2, restoring the encodings. Ruby 3.1 has no `fchdir`: `chdir` goes through `/proc/self/fd/N`. `ENV.replace` in place. Signal dispositions reset to Ruby's defaults, plus the inherited ignored ones. `/dev/tty` bridge on `IO.console` and `File.open("/dev/tty")`.
- **Environment variables.** At startup: `RUBY*`, `GEM_*`, `LANG`, `LC_ALL`, `LC_CTYPE`. While loading, reads of `ENV` are recorded by a `prepend` on `ENV`.
- **inotify** goes through Fiddle, Ruby's counterpart of ctypes.

**PHP** (`server/speedrunner_daemon.php`): the same protocol, with another preload strategy.
- **Nothing of the tool runs in the parent.** Precompiling (`opcache_compile_file`) or predeclaring classes there would bind early classes that Composer then redeclares through an ordinary `require`, which is fatal. The gain comes from the fork (the startup of PHP and its extensions) and from **shared OPcache**. The client starts the daemon with `-d opcache.enable_cli=1 -d opcache.validate_timestamps=1 -d opcache.revalidate_freq=0`; the shared memory is inherited by the children, and what one child compiles serves the next ones, as with PHP-FPM. A modified file is recompiled, and the fingerprint covers the interpreter and its ini files.
- **Descriptors.** Sockets, fd passing, `SO_PEERCRED`, `dup2` and `fchdir` go through FFI to libc: PHP exposes neither fd numbers nor `dup2`.
- **Run.** `Speedrunner\Daemon::main()` returns in the child, which includes the tool at the file's global scope: the tool's top-level variables are therefore globals. `$argv`, `$argc`, `$_SERVER` (environment, then `PHP_SELF` … `argc`, in native order) and `$_ENV` (according to `variables_order`) are rebuilt. An exception handler reproduces the native message (log and display, according to `log_errors` and `display_errors`), without the daemon's frame, then exits with 255.
- **End.** PHP gives no exit code before it shuts down: no X, the client waits for the W wait status.
- **The `php script args` form.** The client recognises it when the tool is the PHP interpreter and its first argument is a file. It then serves the script, with a daemon of its own. `$argv[0]` is the path as typed, and the daemon resolves it against the client's cwd. `speedrunner status|stop php <script>` targets that daemon. In automatic mode, `activate` also creates a `php` link.

**Node** (`server/speedrunner_daemon.mjs`): the same protocol, on a `fork()` that Node does not have.
- **Addon.** `speedrunner_node.c`, a Node-API addon of about 500 lines, provides `fork` (with `uv_loop_fork` in the child), fd passing, `dup2`, `poll`, signals, inotify and a pty. It declares the Node-API functions it calls itself: it builds with a bare C compiler, and one build serves every Node version. `client/build.rs` embeds it in two variants on Linux, glibc (built on Ubuntu 22.04) and musl (Alpine); the client picks one from the ELF interpreter of the `node` binary. On macOS it embeds one, `speedrunner_node-darwin.node`, for the target architecture. Without the variant, the tool runs natively.
- **Threads.** Node keeps at least four threads: a delayed-task scheduler, a V8 worker and the inspector thread, which do not exist in the child. The client starts the daemon with `--single-threaded --v8-pool-size=1`: V8 no longer hands them work, and both GC and JIT run on the main thread. `--no-lazy` compiles in the parent every function it loads; without it, each child recompiles what it runs (eslint: 89 ms instead of 41). A Node built without the code cache of its internal modules is served without `--no-lazy` (§12).
- **Learned preloading only** (level 2). A `module.registerHooks` hook (Node ≥ 22.15) records the modules the child loads, in the order in which their loading starts, CommonJS and ES alike. After X, the child sends them to the parent, which has them judged by a probe. The probe refuses a module that prints something, changes the environment, starts a thread, leaves pending work (`process.getActiveResourcesInfo()`), reads the arguments, or fails. Threads of libuv's pool are tolerated: libuv reinitialises it after a fork (`pthread_atfork`). The parent then loads the accepted modules, with the same checks.
- **What imports read.** While loading, `process.env` and `process.argv` are replaced by proxies. The environment keys read are recorded, and so is the fact of having enumerated the keys (the `debug` package looks for `DEBUG_*`). For `process.argv`, the flags looked up with `includes` or `indexOf` are recorded, as supports-color does; any other read of the arguments gets the module refused. The answers of `tty.isatty` and the creation of `process.stdout`, `stderr` and `stdin` are recorded too. A client that would get a different answer runs natively. Imports run with a pty on the fds that were terminals for the client that started the daemon, for the daemon's whole life.
- **Run.** `Module.runMain(script)`, from a `setImmediate`: CommonJS or ES module, like `node script`. `process.argv` and `process.env` are changed in place, `process.execArgv` is emptied.
- **End.** Once every `exit` listener has run, the child sends X, then what it learned, and leaves through `_exit`: Node's normal teardown would wait for threads that do not exist in the child. After an uncaught exception, Node emits `exit` *before* printing the error. The addon then relays stderr through a pipe up to the `Node.js vX` line, then sends X and exits with the code. On the way, it replaces the daemon's two frames with the one of a native launch (`node:internal/main/run_main_module`), learned once per Node version. So the client does not need the daemon's W, and the daemon may be gone (`kill -9`).

For a new runtime: a daemon `server/speedrunner_daemon.<ext>` that speaks this protocol, an entry in `Runtime` (client), a test tool, and a matrix `tests/test_<runtime>.py` that reuses the `test_matrix.py` harness.

## 2 ter. Launchers

A launcher prepares an environment, then runs the interpreter on the real tool. gcloud is a shell script that ends with `exec "$CLOUDSDK_PYTHON" … lib/gcloud.py "$@"`; az, installed by pip, is a Python script that ends with `os.execl(sys.executable, …, "-m", "azure.cli", …)`. Preloading the launcher is useless: what it runs is what must be served ([client/src/launcher.rs](../client/src/launcher.rs)).

- **Recognition.** A shell script (`sh`, `bash`, `dash`, `ksh`, `zsh`, directly or through `env`) whose last command starts with `exec `, or that ends with an `if … fi` block whose every branch ends with `exec …` or `exit` (at least one with `exec`, no nested block); or a Python script whose last top-level line starts with `os.exec`. Everything before only prepares that exec.
- **Following.** The client starts the launcher with its arguments, under ptrace (`PTRACE_TRACEME`, then `PTRACE_O_TRACEEXEC | PTRACE_O_EXITKILL`). It only stops at execs, and passes on to the launcher the signals it receives. It lets through the first exec (the launcher itself) and those of a shell or of `env`. At the exec of a `python*`, the kernel stops the process before the interpreter's first instruction. The client then reads `/proc/<pid>/cmdline`, `environ`, `status` (umask, `SigIgn`) and checks that the cwd and fds 0 to 2 are its own, then kills the process. Nothing of the tool has run, and the preamble has run once, as natively: 6.6 ms for gcloud, one Python startup (7.5 ms) for az.
- **Interpreter.** It is `argv[0]` as the launcher gave it, looked up in *its* `PATH` if bare, and it must resolve to `/proc/<pid>/exe`. The resolved file will not do: a venv's python is recognised by the path it is called through.
- **Target.** Options without a per-call effect (`-B -E -I -O -P -R -S -b -q -s -u`, `-X …`, `-W …`), then a script or `-m module`. Otherwise (`-c`, `-`, another option), the client runs the command it read as is. The target has its own daemon, named after the launcher and hashed on interpreter, options and script or module. That daemon is started with the env the launcher built. The request carries the env, umask and ignored signals the launcher left, not the client's.
- **Fallback.** The client runs (`execve`) exactly the command it read: the preamble does not run a second time.
- **A launcher that does not run Python** (gcloud with `CLOUDSDK_USE_GOCLOUD`, or any other exec). The client detaches from it and waits for it as its parent: it ignores SIGINT and SIGQUIT, which the terminal sends to the launcher in the same group, relays SIGTERM and SIGHUP, then exits as the launcher did.
- **`status`, `stop`, `prewarm`.** Each successful follow writes the name of the target's socket to `<launcher socket>.target`, in the runtime directory. `status` and `stop` target that daemon. `prewarm` pings it. If it is absent, `prewarm` reruns the preamble without arguments, with fds 0 to 2 on `/dev/null`, kills it at the exec, then starts the daemon, only if the target is the same. `prewarm` does nothing for a launcher none of whose calls has been followed yet.
- **`activate`'s automatic mode.** It only creates links for scripts of a served runtime (Python, Ruby, PHP, Node): az is among them, gcloud is not. Many system shell scripts end with an `exec` that has nothing to do with Python, and each of them would then run under the client. For gcloud, name it (`activate gcloud`, `exec -t gcloud`), enroll it, or go through `srr`.
- **pre-commit's git hook.** `pre-commit install` writes `.git/hooks/pre-commit`, a bash script that ends with `if [ -x "$INSTALL_PYTHON" ]; then exec "$INSTALL_PYTHON" -mpre_commit …; elif command -v pre-commit …; then exec pre-commit …; else … exit 1; fi`. git runs it by its path, and the hook runs Python by its absolute path: neither goes through the links of `speedrunner exec`. The only lookup on `PATH` is the shebang `#!/usr/bin/env bash`: `env` looks `bash` up on `PATH`.
  - **A `bash` link**, next to `pre-commit`'s, in the links directory of `exec` and `activate` (automatic mode: pre-commit found on `PATH`; named tools: `-t pre-commit`). Without pre-commit, bash is left alone.
  - **Called as `bash`**, the client only looks at `argv[1]`. If it is a file carrying pre-commit's mark (`# File generated by pre-commit: https://pre-commit.com`, second line) and ending like a launcher, it follows it as a launcher: the real bash runs it under ptrace, with the same command line, and the client serves the `python -mpre_commit hook-impl …` it runs, through a daemon named `pre-commit` (the joined form `-mMODULE` is recognised). In every other case (`bash -c`, another script, an option), it `exec`s the real bash with the same arguments: one more client startup, i.e. 4.1 → 8.2 ms for an empty bash script in Docker Desktop, where `/usr/bin/true` alone takes 1.9 ms.
  - **Fallback** as for any launcher: without ptrace, or if the daemon refuses, the client runs the real bash, or the Python command it read, once; neither the preamble nor pre-commit runs twice. The daemon starts according to the automatic-mode rule (at pre-commit's second call, counting its direct calls), or from the first call if pre-commit is named or enrolled.
  - **Ruled out**: `core.hooksPath` (pre-commit then refuses to install, and it replaces the hooks of husky or others); a command that rewrites the hook (`pre-commit install` overwrites it, or renames it to `pre-commit.legacy` and runs it on top of its own: risk of double execution); a `python3` link (the hook uses an absolute path); a `git` link that would pass `core.hooksPath` through the env (inherited by the gits the hooks start, and the hook would no longer be in its directory). The `bash` link asks nothing of the user, changes no file in the repository and changes nothing outside `speedrunner exec` and `activate`.
- **`-m module` in the daemon** (`--module`). The file to run is found as runpy does: by importing the parent packages, and the package itself if it has a `__main__`. This import comes first in level 1, under the same safeguards. Natively, `sys.path[0]` is the cwd: the daemon removes it, and each child puts in the client's cwd. A file in the cwd that would shadow a preloaded module (`json.py`, or a package with `__init__`) sends the call native. The child runs through `runpy._run_module_as_main` in a fresh `__main__`: a traceback shows the same frames as natively.
- **`sys.path` entries added by the tool** (gcloud adds `lib/third_party`). The child sends them with its learned imports, and the cache keeps them (`"paths"`). The probe and the parent import with these entries at the head of `sys.path`, then restore it. A module found in another file than in the child is refused.

## 2 quinquies. macOS (M1: Python and the client; M2: Ruby and PHP; M3: Node)

Same product, same protocol; only the system's mechanisms change. On the client side, they are grouped in [client/src/platform.rs](../client/src/platform.rs) (`cfg(target_os)`); on the daemon side, in a "platform" section (`MACOS`) of the Python and Ruby daemons, and in `libc()` of the PHP daemon. Linux keeps exactly its behaviour.

| Piece | Linux | macOS |
|---|---|---|
| Peer's uid (and gid) | `SO_PEERCRED` | client: `getpeereid`; daemon: `LOCAL_PEERCRED` (`struct xucred`) |
| Daemon's native threads | `/proc/self/task` | `proc_pidinfo(PROC_PIDTASKINFO)`, `pti_threadnum`; a failure counts as threads |
| Watched module files | inotify, one `select()` in the child | kqueue in the parent only; the child receives its verdict (below) |
| Env received at launch | `/proc/self/environ` | `sysctl KERN_PROCARGS2`, and the fingerprints of the client's env (below) |
| cwd passed to the child | `O_PATH` | `O_SEARCH` (no read permission needed either); path through `F_GETPATH` |
| Import output captured | `memfd_create` | file created then removed at once in the runtime directory |
| Fds received | `MSG_CMSG_CLOEXEC` | `fcntl(F_SETFD)` after receipt |
| Terminal passed to the child | `/dev/tty` | the device itself (`/dev/ttys003`), found through `proc_pidinfo(PROC_PIDTBSDINFO)` and `devname` |
| SIGTERM relayed | through a pidfd | through the pid |
| Calling script (`activate`) | `/proc/<ppid>/cmdline`, `cwd`, `fd/255` | `KERN_PROCARGS2`, `PROC_PIDVNODEPATHINFO`, `proc_pidfdinfo` on fd 255 |
| Launchers (az, gcloud) | followed under ptrace (`PTRACE_O_TRACEEXEC`, `/proc`) | followed under ptrace (`PT_TRACE_ME`, sysctl and libproc; below) |
| Ruby | `O_TMPFILE`, inotify, `/proc/self/fd` | file removed in `$TMPDIR`, `stat`, `fchdir` through Fiddle |
| PHP (FFI to libc) | `msghdr`, 64-bit `cmsghdr`, `accept4`, `SOCK_CLOEXEC` | libSystem's: `cmsghdr` with a 32-bit length aligned on 4, `accept` + `fcntl` (the accepted socket would inherit `O_NONBLOCK`), `fcntl` declared variadic |
| Node (C addon) | `MADV_DOFORK` on V8's heap, inotify, `/proc/self/task`, `SO_PEERCRED` | nothing to undo (the heap is inherited), `stat`, Mach's thread list, `LOCAL_PEERCRED`; addon `speedrunner_node-darwin.node`, built for the target architecture |

**The CoreFoundation rule.** Once CoreFoundation has been used in a process, a forked child that uses it in turn is killed ("The process has forked and you cannot use this CoreFoundation functionality safely"). Measurements in `bench/results/…-macos-m1-mac/ANALYSE.md`:

- loading `_scproxy` (which `import urllib.request` does) is harmless; calling it (urllib's proxy detection) is what is fatal, on Apple Silicon. So the daemon does not refuse a loaded `_scproxy`, which would have disabled pip, aws, httpie…;
- proxy detection leaves 3 native threads: the thread rule already blocks it. But a CoreFoundation run loop (`CFRunLoopGetCurrent`, through a C extension or ctypes) leaves none, and kills every child, on Intel too;
- **the canary** (`cf_unsafe`): a throwaway child calls `_scproxy._get_proxy_settings()`, the only call found that triggers CoreFoundation's check; if it dies, this process must no longer fork. Cost: one fork and a round trip to configd, ~2 ms on an M6, 10 to 17 ms on an i7. It only runs when the preloaded state changes: after each level-1 import (which is then skipped, "uses CoreFoundation at import", like an import that starts a thread), after the `-m` module, after each batch of learned imports applied in the parent (on failure, the batch is blacklisted and the daemon restarts), and in the probe;
- in the probe, one canary per module would cost too much: just one, once the batch is imported. If it dies, or if a refused module left threads (the canary is then no longer reliable: it passes on the Intel after `getproxies`), the whole batch is judged again with a canary after each module, until the end of that learning round;
- a tool that uses CoreFoundation while it runs (the common case: an HTTP request) does so in the child, from a parent that never used it: harmless.

**The kqueue fingerprint.** A kqueue is not inherited across fork, and `EVFILT_VNODE` needs one fd per file (`O_EVTONLY`). So only the parent watches the module files. It raises its open-file limit (up to 10,240; a Terminal's is 256), and each spare closes those fds and restores the limit before accepting: the tool sees the same limit as natively (test `test_open_file_limit`). On a child's `A` message, the parent reads the queue and answers `F` (nothing) or `S` (stale) on the control socket; the kernel queues the event during the write itself, as with inotify. Without an answer within 20 ms (parent busy learning, or gone), the child `stat`s each file. As on Linux, the parent stops as soon as an event arrives. No `NOTE_ATTRIB`: a `.so` extension gets one each time another process loads it, with no change; writes, extensions, deletions and renames (pip replacing a file) are still seen. Gain: black's connection → ready phase goes from 8.9 to 3.4 ms on an i7.

**The canary leaves no crash report.** A crashing child makes macOS write a report and open the "Python quit unexpectedly" window on the user's screen. So the canary child turns SIGSEGV, SIGBUS, SIGILL, SIGTRAP and SIGABRT into `_exit(signal)`: a non-zero status, no report (checked on the Mac mini).

**Ruby and Objective-C.** A Ruby process always has a second thread, its timer. After a fork in a process that has threads, the Objective-C runtime refuses the child the first use of any class ("+initialize may have been in progress in another thread when fork() was called", SIGABRT), even in a parent that never touched CoreFoundation: a served Ruby tool that touched Foundation would crash, where it works natively. Ruby's thread never does any Objective-C: the client starts the Ruby daemon with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` (and `SPEEDRUNNER_OBJC_ADDED=1`), which the daemon removes from its env at once; nothing if the user had set it. The Ruby canary calls `SCDynamicStoreCopyProxies` through Fiddle, as `_scproxy` does, at the same moments as in Python. Python and PHP have only one thread: not affected.

**Node.** Same rules as Ruby: the client starts the daemon with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` (Node has threads). `process.title = …` goes through CoreFoundation (libuv) and starts 2 threads: the probe refuses a module that does it while loading; a canary runs at startup and after each learned batch (not per module: one fork each time, 6.5 s of learning for eslint on an i7).

**PHP.** Nothing of the tool runs in the parent: a single canary, at startup (an extension that would have used CoreFoundation). The whole C layer goes through FFI, with structures and constants specific to each system. On Apple's arm64, variadic arguments are passed on the stack: `fcntl` declared with a fixed prototype received garbage, and every spare died.

**Launchers on macOS** (`client/src/launcher.rs`). `PT_TRACE_ME` stops the traced process with a `SIGTRAP` at each exec; `KERN_PROCARGS2` then gives the executable, argv and env, `proc_pidinfo` the cwd, `proc_pidfdinfo` what fds 0 to 2 are (a file by device and inode, a pipe or socket by its kernel handle), `KERN_PROC_PID` the ignored signals (`p_sigignore`). Pitfalls, seen on the Mac mini:
- the kernel hides the env of Apple's binaries (`/bin/sh`, `/usr/bin/python3`): a stop on a `python*` without env is followed to the next exec; a framework's `python3` runs its `Python.app`, which is passed through like a shell;
- `PT_DETACH` on an exec stop gives the process back its `SIGTRAP`, which kills it (and the client, which reproduces its child's death, with a crash report): to let it run natively, the client resumes it, sends it `SIGCONT` and detaches at the stop for that signal, swallowing it;
- a traced, stopped process ignores `SIGKILL`: `PT_KILL`;
- `WIFSTOPPED` excludes `SIGCONT`, whose stop status is the one of `WIFCONTINUED`: the client decodes the stop itself;
- another process's umask cannot be read: the client assumes its own, and a launcher whose text mentions `umask` runs natively;
- after the exec of a framework Python's `Python.app` (the one of Apple's command line tools, and the one on GitHub runners), the kernel no longer reports the following execs: a launcher written in Python on such a Python (az installed by pip) runs natively, once. A shell launcher (gcloud, Homebrew's az) stops at its exec of Python, before that: it is served (CI, Mac mini).
The client's death kills the process it follows (checked), like `PTRACE_O_EXITKILL` on Linux.

**The env the tool sees.** On macOS, `python3.x` is often a launcher that does an `exec` of its own: python.org's adds `__CF_USER_TEXT_ENCODING` (absent from an ssh session), `/usr/bin/python3` adds `SDKROOT`, `CPATH`, `LIBRARY_PATH` and `MANPATH`. `KERN_PROCARGS2` gives the interpreter's env after that launcher, without saying what came from the client. So the client passes the daemon, in `SPEEDRUNNER_LAUNCH_ENV`, a 64-bit FNV-1a fingerprint of each entry of its env, followed by its name. From it, the daemon works out what the launcher and CPython added or changed, and adds it to the env of each request, as natively. A startup key (`PYTHON*`, `LC_*`, `LANG`) changed by the launcher sends the call native.

## 3. Process overview

```
 zsh shell ── function aws() ──► speedrunner run aws -- args      (or: srr aws args)
                                      │ Rust client
                                      │ Unix socket $XDG_RUNTIME_DIR/speedrunner/aws-<hash>.sock
                                      ▼
 ┌──────────── daemon (one per tool) ────────────────────────────────────────┐
 │ parent: preloads, never calls accept() itself, reaps its children         │
 │   ├── spare: already forked, waits on select() + accept()                 │
 │   ├── active child: one call in progress (former spare), runs the tool    │
 │   └── probe: throwaway child that checks learned imports (level 2)        │
 │ control socketpair parent ↔ each child                                    │
 └───────────────────────────────────────────────────────────────────────────┘
```

- **The parent** stays single-threaded, and without asyncio (not fork-safe). It loops on `select()`: the signals' self-pipe, the children's control sockets, the inotify queue. It never reads that queue: it stops at the first event.
- **The spare** is forked ahead of time. When it accepts a connection, it passes the parent a copy of the client fd (message `A` with `SCM_RIGHTS`), then the parent forks the next spare at once. That way the parent keeps what it needs to send the `W` wait status.
- **The child** checks, receives the request, waits for the "go", applies the client's context, runs the script through `runpy`, returns the exit code, then tears itself down without blocking anyone.

## 4. Protocol

Little-endian, on the Unix socket. Every frame: `kind (1 byte) + u32 length + payload`.

| Direction | Message | Role |
|---|---|---|
| client → child | `HS1` + `R`/`P`/`S` + u32 + body, fds as `SCM_RIGHTS` | R = run, P = ping/status, S = stop the daemon |
| child → client | `N(reason)` | run natively (before anything has started) |
| child → client | `R(pid i32)` | ready, waiting for the go |
| child → client | `P(text)` | answer to the ping (`ready …`, `stale: …`, `disabled: …`) |
| client → child | `G` | go: **from here on, the client never falls back to native** |
| child → client | `X(code i32)` | exit code, sent from the last `atexit`; the client exits with it |
| parent → client | `W(kind u8, value i32)` | wait status (0 = exited, 1 = killed by a signal), when X did not come |

Body of an `R`: argv list (u32 count, then u32+bytes blobs; argv[0] = script path), env list (`KEY=value`), u32 umask, u64 mask of inherited ignored signals, groups (u32 count + one u32 each). Fds: 0, 1, 2, an `O_PATH` of the cwd, and `/dev/tty` if the client has a controlling terminal.

Control socket parent ↔ child: `A` (+ client fd: "I accepted"), `L(json)` ("here is what I imported and loaded", sent after X), `Q` (parent → spare: "step down"), and in PHP `K` (parent → child: "I have your copy"). A PHP child only reports its end through the daemon's W: it waits for `K` before serving. If it sees end-of-file instead (daemon killed by `kill -9`, which the kernel tears down over a few milliseconds during which the spare still takes calls), it closes the connection before the `R` answer, and the client runs natively. Found by the stress suite on arm64.

**Rule no. 1: never run twice.** Before `G`, any anomaly (no socket, N refusal, failed handshake, stale fingerprint) leads to an `exec` of the real CLI. After `G`, nothing is ever rerun: without X or W, the client prints "lost track … not re-running it" and exits with 125. `SPEEDRUNNER_REQUIRE_FAST=1` replaces the native fallback with an exit with 124, for tests and benches.

## 5. Daemon life cycle

1. **Started by the client** (`run` that finds no daemon, or `prewarm`). The client first reads the state of the lock `<socket>.lock`:
   - lock free: it starts one;
   - `running <pid>`: nothing to do;
   - `exiting <pid>`: it starts a candidate that will wait only for *that* pid to exit.

   Double fork. The grandchild drops the client's fds (otherwise a pipeline would wait for it to end), takes the lock (waiting 3 s at most), writes `running <pid>` into it, then `exec`s the shebang's interpreter with `speedrunner_daemon.py --tool --script (or --module) --socket --lock-fd 3 --origin --tty`. Result: a single daemon, however many clients race.
2. **Socket opened before preloading.** A call that arrives during startup waits for the first spare, 3 s at most, instead of running natively. The runtime directory is checked with `fstat` on an fd (owner = us, mode exactly 0700), and the daemon then works relative to that fd. Socket in 0600, original umask restored right after.
3. **Preloading** (§6), check of Python and native threads (`/proc/self/task`, `proc_pidinfo` on macOS, plus the CoreFoundation canary, §2 quinquies), computation of the fingerprint and of the inotify watches (§7).
4. **Serving.** The parent's loop: spare, `on_ctl` (A/L), `reap` (W), applying what was learned when no child is active.
5. **Stopping**: `S` (stop), stale fingerprint (SIGUSR1 sent by a child), watched file modified, 4 h idle (`SPEEDRUNNER_IDLE_TIMEOUT`), or a learned import that misbehaves (the next daemon blacklists it). It steps down first: it writes `exiting <pid>` into the lock, removes its socket (only if it is still its own, by inode), closes its listening socket and dismisses its spare. It also releases its lock, which only says who serves the socket: the next daemon can start at once, even while a long call is in progress. Then it waits for its running children and sends them their W: in PHP, and for a child killed by a signal, that is the only way for the client to learn that it ended. (Keeping the lock during that wait made the next daemon's starter give up after 3 s: the tool stayed without a daemon until the call after. Found by `platforms`, Python 3.14; test `test_stopped_mid_run_lets_a_new_daemon_start`.)
6. **Crash** (`kill -9`): the active children finish and send their X, the spare exits. The next client finds a socket nobody listens on, restarts the daemon in the background and runs natively.

## 6. Preloading

`SPEEDRUNNER_PRELOAD` (default `1,2,3`) picks the levels, each tested alone and combined.

| Level | Mechanism | Key points |
|---|---|---|
| 1 | the script's top-level imports (AST) | `sys.path[0]` = the script's directory (except with `-I`/`-P`). Output produced at import is captured at the fd level (memfd) and replayed on each call. The env diff of the import and the env keys read at import are recorded. |
| 2 | learned lazy imports | The child records the order in which imports **start** (the `import` audit event), not the order of `sys.modules`, which is the order in which they finish. A throwaway probe imports the modules one by one and refuses those that print, change the env, start a thread or fail, as well as those that import an already refused module (without running it again). It starts afresh after each refusal. Accepted modules are imported in the parent, then the spare is replaced. All this happens in steps of about 0.25 s: in between, the parent's loop replaces the spare that was used, and `status` shows `learning=N`. |
| 3 | botocore files parsed ahead | Wraps `botocore.loaders.JSONFileLoader._load_file`. A child takes each entry at most once, and only if the `stat` matches. Same debug message as natively. LRU budget of 16 MB of source JSON (`SPEEDRUNNER_MEMO_MB`). Active only if botocore is already imported. |
| 4 | driver snapshot (stage 4 bis) | not started |

Persistence: `$XDG_CACHE_HOME/speedrunner/<tool>-<sha256 of the fingerprint>.json` = `{"modules", "paths", "blacklist", "files"}`, reloaded at startup. A modified installation changes the fingerprint, hence the cache file.

Before each fork, `gc.freeze()`: the child's GC does not walk the inherited heap, and so does not copy its pages.

## 7. What the child checks and applies

Before answering `R` (`why_not_fast`), otherwise `N` and a native run:

- the peer's UID (`SO_PEERCRED`, `LOCAL_PEERCRED` on macOS), the client's gid and groups identical to the daemon's;
- the script path, as the client started it: absolute, or relative to its cwd (`./manage.py`) if it names the same file; for a module, `-m` followed by its name, and no file in the cwd that shadows a preloaded module;
- the fingerprint: `stat` of the script, of the interpreter, of the `sys.path` directories and of the `RECORD` files, plus one `select()` on the inotify queue of the module files (on macOS, the parent's verdict, §2 quinquies);
- the startup env: `PYTHON*`, `LC_ALL`, `LC_CTYPE`, `LANG` identical to the daemon's;
- the env frozen at import: keys changed *or read* by top-level code must have the same value as when the daemon started. Real examples: botocore freezes `AWS_NEW_RETRIES_2026` and a path derived from `HOME`, and gettext freezes the language.

After `G` (`apply_request`, then `run_tool`):

- `dup2` of fds 0/1/2, `fchdir` to the client's cwd, the client's umask;
- `os.environ` updated **in place** (`clear()` + `update()`), then the import's diff applied again;
- native signal dispositions: SIGINT → KeyboardInterrupt, inherited SIG_IGN, empty mask;
- buffering of stdout/stdin according to `isatty()`, without recreating the objects;
- `/dev/tty` bridge: `os.open`, `open` and `io.open` of `/dev/tty` return a copy of the client's fd;
- `sys.orig_argv`: the daemon's interpreter and options (the tool's), then the script or `-m module`, then the arguments;
- output of the import replayed, then `runpy.run_path(script, run_name="__main__")`, or for a module `runpy._run_module_as_main`;
- exit in the CPython way (`SystemExit` None/int/other, exception printed with the traceback limited to the script's frames, KeyboardInterrupt that ends with SIGINT);
- last `atexit` (registered first): flush of the buffered writers and of stdout/stderr, then `X`, then fds 0/1/2 to `/dev/null`, then `L`. If flushing stdout fails, no X: teardown exits with 120, as natively.

## 8. Shell and scripts

- `shell/speedrunner.zsh` reads `${XDG_CONFIG_HOME:-~/.config}/speedrunner/tools` and defines `aws() { command speedrunner run aws -- "$@" }` for each enrolled tool, unless the user has an alias or function of that name. A `speedrunner` function reloads the wrappers right after `speedrunner enroll`.
- Anticipation: a widget on `zle-line-pre-redraw`. Once per line, when the first word (after any `VAR=value`) is complete and enrolled, it starts `speedrunner prewarm <tool> &!`. The cost is about 2 µs per keystroke in the common cases, and the single fork (~0.5 ms) goes to the background.
- **bash** (`shell/speedrunner.bash`, `eval "$(speedrunner init bash)"`): same wrappers, same reload after `enroll`. bash has no per-keystroke hook; anticipation goes through `bind -x` on the space key (emacs and vi-insert keymaps), since a command name is complete when a space follows it. The function inserts the space itself at `READLINE_POINT` (in characters: tested with multibyte text), then applies the same rule as zsh. It is rearmed on each line by `PROMPT_COMMAND`, placed first and returning the `$?` it received, for prompts that show it. If the space key already does something else for the user (`magic-space`…), it is left alone: only anticipation is lost.
- **Scripts, without a prefix**: `eval "$(speedrunner activate [tools…])"` at the top of the script, or `speedrunner exec [-t tool]… command` (also as a shebang: `#!/usr/bin/env -S speedrunner exec bash`).
  - **Principle.** A directory `$XDG_RUNTIME_DIR/speedrunner/bin-<hash>/` holds links named after the tools, all pointing to the client; it is put at the head of `PATH`. Called under a tool's name, the client serves it: `aws args` is the same as `speedrunner run aws -- args`. To find the real tool, `resolve_in_path` skips any candidate that resolves to our own binary, which rules out any loop. Since `PATH` is inherited, `xargs`, `make` and sub-scripts are covered.
  - **Automatic mode**, with no tool named: directory `bin-auto-<hash of PATH>`, one link per script of a served runtime (Python, Ruby, PHP, Node) found on `PATH`, plus the PHP interpreter (for `php script`), plus `bash` if pre-commit is among them, for its git hook (§2 ter), following `PATH` lookup rules (the first entry of a name wins). The directory is only scanned again if a `PATH` directory changes: its stamp is in `.auto`. Enrolled tools are prewarmed at once, and so are those the script will call more than once. To know which, `script::command_counts` ([client/src/script.rs](../client/src/script.rs)) reads the script: the parent shell's (`/proc/<ppid>/cmdline`, `-c TEXT` or the file; failing that, bash's fd 255) for `activate`, or the command's for `exec`.
    - **Lexer.** A lexer knows quotes, comments, `$(…)` and `` `…` `` (parsed in their context), `$((…))`, `${…}`, here-documents (ignored) and redirections.
    - **Parsing.** A parser tracks command positions and nesting: loops (condition included), functions with the number of their calls (a recursive function counts as "several"), `case`, subshells, wrappers (`exec`, `env`, `command`, `nohup`, `srr`), `xargs`, `find -exec`. It also follows `source` and `.` with a literal path, 8 levels deep at most, each file once.
    - **Safety net.** An automatic-mode link counts its calls in `.calls/<tool>` and starts the daemon at the second call, which catches what the analysis does not see.
    - **`--eager`** starts every tool mentioned.
    - **Order and pace.** `command_counts` returns the tools in the order of their first use: a call inside a function counts at that function's first call. `start_daemons` starts at most one per CPU (`SPEEDRUNNER_START_JOBS`). Beyond that, a detached starter, with fds of its own, chains them: one process per daemon runs `spawn_daemon`, then waits until its ping answers, i.e. until its first spare exists. The terminal state (`--tty`) is computed before the fork, on the script's fds. A tool reached before its daemon has started runs natively, and the lock prevents any duplicate. On 2 vCPUs, the first tool gets its daemon three times sooner, but the total stays the same: the preloading CPU does not change.
- Prefix, without enrolling: `srr aws s3 ls`. `srr` is a link to the client; called under that name, it is the same as `speedrunner run aws -- s3 ls`. It works for any tool of a served runtime, scripts included, and launchers, and the zsh and bash hooks also prewarm the tool that follows `srr`.
- `speedrunner enroll` refuses what is neither a script of a served runtime nor a launcher (§2 ter). `status`, `stop` and `prewarm` wait for a daemon that is starting.

## 9. Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `SPEEDRUNNER_REQUIRE_FAST=1` | client | exit code 124 instead of the native fallback |
| `SPEEDRUNNER_DEBUG` | client | prints the reason for a fallback, and when the lock is held |
| `SPEEDRUNNER_NO_AUTOSTART` | client | never starts a daemon |
| `SPEEDRUNNER_START_JOBS` | client | how many daemons `activate` and `exec` start at once (default: one per CPU) |
| `SPEEDRUNNER_DAEMON` | client | path to a daemon script (of any runtime): each runtime's daemon is taken from that directory (otherwise: next to the binary, then `../share/speedrunner/`, then `../../../server/` for development; failing that, the copy embedded in the binary, extracted to `$XDG_DATA_HOME/speedrunner/daemons/<fingerprint>/`) |
| `SPEEDRUNNER_PRELOAD` | daemon | levels, `1,2,3` by default |
| `SPEEDRUNNER_MEMO_MB` | daemon | level 3 budget, 16 by default |
| `SPEEDRUNNER_IDLE_TIMEOUT` | daemon | seconds idle before stopping, 4 h by default |
| `XDG_RUNTIME_DIR`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME` | all | socket (fallback: `/tmp/speedrunner-$UID`), learned cache, list of enrolled tools |

The daemon reads the env of the client that started it. Log: `<socket>.log`, which records for each startup the command that triggered it. `speedrunner status <tool>` prints `ready pid=… preload_ms=… modules=… levels=… learned=… blacklisted=… memo=… gen=…`. `gen` increases with each enrichment of the preload; the tests wait for it.

## 10. Developing and testing

Everything runs on the dev machine, a 2-vCPU VPS (Debian 12, 1.8 GB of RAM, Python 3.11, rustc 1.63). Typical loop, `<vps>` being its address:

```bash
rsync -a --delete --exclude .git --exclude target ./ root@<vps>:/root/speedrunner/repo/
ssh root@<vps> 'cd /root/speedrunner/repo/client && cargo build --release'
ssh root@<vps> 'cd /root/speedrunner/repo && HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_matrix.py'
ssh root@<vps> 'cd /root/speedrunner/repo && HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_shell.py'
```

Ruby (the VPS's ruby 3.1): the test gem is installed in `/root/speedrunner/gems-rbtest`, and rubocop, rake and bundler in `/root/speedrunner/gems-rb`. To reinstall it after a change, then run the Ruby matrix:

```bash
cd /tmp && rm -rf rbtest-build && cp -r /root/speedrunner/repo/tests/fixture/rbtest rbtest-build && cd rbtest-build && gem build rbtest.gemspec && GEM_HOME=/root/speedrunner/gems-rbtest gem install --local --no-document rbtest-0.1.0.gem
cd /root/speedrunner/repo && RB_GEM_HOME=/root/speedrunner/gems-rbtest HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=/root/speedrunner/gems-rbtest/bin:/root/speedrunner/venv-test/bin:/root/speedrunner/venv-aws/bin python3 tests/test_ruby.py -v
```

- Without `HS_BIN`, the matrix compares native with itself, which validates the harness.
- After a change to `tests/fixture/hstest`, reinstall it in `venv-test` from a copy: the venv has no `wheel`, so build isolation is needed (`cp -r tests/fixture/hstest /tmp/x && venv-test/bin/pip install --no-deps --force-reinstall /tmp/x`).
- Matrix classes:
  - `OutputsAndExitCodes`, `Terminal`, `Signals`, `Context`, `Randomness`, `Teardown`, `NoLocale`: fidelity;
  - `FailOpen`: native fallback;
  - `Preload`: each level, learning, blacklist, modified botocore file;
  - `Concurrency`: 10 calls in parallel, warm and cold;
  - `ScriptPrefix`: `srr`;
  - `RelativeScript`: `./manage.py`;
  - `Launchers`: shell and Python launchers, `-m module`, `sys.path` entries added by the tool, without ptrace;
  - `CoreFoundation`: the macOS canary (macOS only);
  - `Distribution`: the lone binary and its embedded daemons, `demo`, `init`;
  - `tests/test_scripts.py`: `activate`, `exec`, automatic mode, pre-commit's git hook;
  - `Failures`: daemon, child or client killed, module modified in place;
  - `Security`: runtime directory too open or owned by another UID, socket served by another UID;
  - `Interactive`: `input`, getpass with redirected stdin, `aws configure`.
- **Security**: only the machine's other users are attackers. Client side (`Security` class of `test_matrix.py`, valid for every runtime): runtime directory too open or owned by another uid, socket served by another uid, a symlink in place of the `/tmp/speedrunner-<uid>` fallback. Daemon side (`Security` class of each suite, Python, Ruby, PHP and Node): a peer of another uid gets no answer (the test connects with effective uid 65534, which `SO_PEERCRED` reports, but filesystem uid 0, to get through the 0700 directory); started by hand with a real daemon's command line and env, but a socket in a wrong directory, the daemon refuses to start. Tests that change uid need root; the `non-root` target skips them.
- **Races**: `tests/test_stress.py`. For `STRESS_SECONDS` (90 s by default), `STRESS_WORKERS` threads (8) call in parallel the test tools of each installed runtime, on a terminal or not, with commands that learn, fail or print. Meanwhile, a disrupter stops the daemons, kills them (`kill -9`) or touches their scripts to make them stale. Every call must finish, exit as natively (a native fallback is allowed), never exit with 125 (except on `kill -9` of a PHP daemon, where only its W reports the end) and run only once (one marker per call). `docker/test-all.sh stress stress-node`, with a terminal attached; the CI's `stress` job, 120 s per target. `STRESS_SEED` replays a draw.
- After each run, check that no daemon is left: `ps -eo args | grep -c '[s]peedrunner_daemon'` must print 0.
- **macOS**: every suite runs as is, with a client built on the Mac (`cargo build --release`, and `--target aarch64-apple-darwin` for the other architecture) and two venvs, as on the VPS. The workflow `.github/workflows/macos.yml` builds the universal client once, then runs the matrix, `test_shell.py`, `test_scripts.py`, the Ruby, Node and stress suites on `macos-15-intel` and `macos-26` (Apple Silicon), and the PHP suite (8.4 and 8.5) in a job of its own: `macos-13` no longer exists, `macos-14` retires on 2 November 2026. Tests skipped because they make no sense on macOS: peer of another uid (no `setfsuid`); the `Bash` class of `test_shell.py` needs bash ≥ 4.2 (Homebrew), the system's bash 3.2 only gets the `OldBash` test.

```bash
cd client && cargo build --release && cd ..
python3 -m venv ~/speedrunner-dev/venv-test && python3 -m venv ~/speedrunner-dev/venv-aws
cp -r tests/fixture/hstest /tmp/hstest-build && ~/speedrunner-dev/venv-test/bin/pip install --no-deps /tmp/hstest-build
~/speedrunner-dev/venv-aws/bin/pip install awscli
HS_BIN=$PWD/client/target/release/speedrunner HS_PATH=$HOME/speedrunner-dev/venv-test/bin:$HOME/speedrunner-dev/venv-aws/bin python3 tests/test_matrix.py
```
- Working rules: tests first, a committed before/after bench for every optimisation, small commits, sign-off by the human at the end of each stage.

## 10 bis. Distribution

A release is a single file: the client embeds the four daemons, the Node addon and the zsh and bash hooks (`include_bytes!`, `include_str!`).

- **Packages** ([packaging/](../packaging/README.md)): one `.deb` per architecture, built, installed, removed and signed by the release workflow; a `PKGBUILD` for the AUR (`speedrunner-bin`) and a formula for a Homebrew tap. All of them ship the release's binary unchanged: static on Linux, universal on macOS. `packaging/update.sh <version>` moves the AUR files to the published version and rewrites the formula whole: with the macOS archive if the release has one (releases after 0.3.2), otherwise for Linux only.
  - **At each release**, `release.yml` then calls two workflows, which can also be run by hand. `packages.yml` pushes the formula to the tap (token `HOMEBREW_TAP_TOKEN`, limited to the tap), checks `brew install` in Linuxbrew and on macOS (Intel and Apple Silicon runners), and proposes `packaging/` at the new version through a pull request: the workflow never commits to main. `apt.yml` rebuilds a signed APT repository from scratch (`packaging/apt.sh`, `apt-ftparchive`, a dedicated Ed25519 key in `APT_SIGNING_KEY`) from the `.deb` files of every release, deploys it to the repository's Pages (`https://anthonygosme.github.io/speedrunner/apt`, suite `stable` for every Debian and Ubuntu, the binary being static), then installs it in Debian 12 and Ubuntu 24.04.
  - Why the repository's Pages, deployed by Actions, rather than a `gh-pages` branch or a separate repository: no `.deb` in the git history (a clone of speedrunner would download them all), no extra token, and an APT repository that never depends on the previous deployment. The price: the `github-pages` environment must accept `v*` tags, and a future project site will have to be built by the same workflow.
  - Only the AUR stays manual (the maintainer's SSH key). Setup: [packaging/README.md](../packaging/README.md), "Setup, once".

- **Daemons.** When no daemon is found next to the binary, the client writes its embedded copy to `$XDG_DATA_HOME/speedrunner/daemons/<fingerprint of the embedded files>/`, under a temporary name, then renames it. A copy already there is only used if its bytes match: an update never starts another version's daemon. Directories next to the binary take precedence, for development: on the VPS, you change `server/` without rebuilding.
- **Hook.** `eval "$(speedrunner init zsh)"` in `~/.zshrc` replaces `source shell/speedrunner.zsh`; likewise `init bash` in `~/.bashrc`. The hook has no top-level `return`, so it can be evaluated.
- **Version.** `speedrunner --version`.
- **Releases.** A `v*` tag starts `.github/workflows/release.yml`. The workflow checks that the tag matches the version in `Cargo.toml`, then builds static musl binaries on an x86_64 runner and on a native arm64 runner. It then checks that each binary is static and starts. For macOS, `client/macos-universal.sh` builds both architectures on an Apple Silicon runner and joins them with `lipo` into a universal binary, for macOS 11 and later (`MACOSX_DEPLOYMENT_TARGET`, also read by the Node addon's `cc`). The script checks both slices, their minimum version and the ad hoc signature of the arm64 slice, without which Apple Silicon refuses to run the binary; `curl` sets no quarantine attribute, so Gatekeeper stays out of it. The macOS suites (`macos.yml`, called by the release like `platforms.yml`) run with that same universal binary, on an Intel runner and on an Apple Silicon runner. The release publishes `speedrunner-<arch>-unknown-linux-musl.tar.gz`, `speedrunner-universal-apple-darwin.tar.gz`, the `.deb` files, their `.sha256` sums and `install.sh` with `gh release create`. Archive names carry no version, so that `releases/latest/download/` works.
- **`install.sh`**, in POSIX sh (tested under dash): one line, `curl -fsSL https://raw.githubusercontent.com/anthonyGosme/speedrunner/main/install.sh | sh`. It detects the system and architecture (on macOS, the universal binary), downloads the archive and its sum, refuses if they do not match, and installs through a temporary name then `mv`, with the `srr` link, where the shell finds it without an extra step: `/usr/local/bin` as root; otherwise `~/.local/bin` or `~/bin` if it is already on `PATH`; otherwise `~/.local/bin`, added to `PATH` in `~/.profile`, `~/.bashrc` and `~/.zshrc`, once and marked. The shell that ran the installation only sees this last addition once restarted: a child process cannot change its parent's `PATH`. Variables: `SPEEDRUNNER_VERSION`, `SPEEDRUNNER_BIN_DIR`, `SPEEDRUNNER_NO_MODIFY_PATH=1`, and `SPEEDRUNNER_BASE_URL` to test it against a local server. Tested in Debian: as root, as a user with and without `~/.local/bin` on `PATH`, and for a reinstall; on macOS, against a local archive, with a right sum and a wrong one.
- **CI.** On every push, `.github/workflows/ci.yml` runs the client's unit tests, its static musl build, the five suites in the image from `docker/` (`docker/run.sh tests` returns non-zero as soon as a suite fails), the Node suite (glibc and musl), the stress suite and ShellCheck.
- **Platforms: `docker/test-all.sh`.** The suites on 28 targets, in parallel (half the CPUs, `JOBS=`), with a table per target and per suite, and the logs in `docker/logs/`. `docker/test-all.sh alpine py3.9` runs just those two, `SUITES="ruby php"` just those suites, `PLATFORM=linux/arm64` on another architecture (emulated by Docker Desktop, 5 to 10 times slower; logs in `docker/logs/<platform>/`). A failing suite shows its `FAIL`/`ERROR` in detail, and CI prints the log of the failing targets. The client is built once, as static musl like the releases (`rust:1-alpine`), then mounted in each container (`SPEEDRUNNER_BIN`).
  - **Distributions** (the five suites): Debian 12 (`docker/Dockerfile`), Ubuntu 24.04 and 22.04 (`Dockerfile.ubuntu`, `UBUNTU=`), Alpine 3.20 (`Dockerfile.alpine`, musl, busybox) and Fedora 42 (`Dockerfile.fedora`).
  - **Language versions**, with the official images (`docker/lang/`): Python 3.9 to 3.14 (matrix, scripts), Ruby 3.1 to 3.4, PHP 8.1 to 8.5 with pcntl, FFI and OPcache, without `php.ini`, Node 22 and 24 (Debian), 24 on Alpine (the musl addon), and Fedora's Node, built without code cache (`node-fedora`). Ruby 3.0 goes through Ubuntu 22.04: its official image is based on Debian 11, whose archive no longer serves it.
  - **Variants**: `non-root` (uid 1000, on the Ubuntu image, open to any uid), `no-ptrace` (`--security-opt seccomp=docker/no-ptrace.json`, where a launcher must run natively, once), and `stress` and `stress-node` (the stress suite, with a terminal).
  - **CI**: `.github/workflows/platforms.yml` runs each target in a job, plus Ubuntu 24.04, Alpine, `node24`, `node24-alpine`, `stress` and `stress-node` on arm64, every night, on demand and before each release (`release.yml` publishes nothing if a target fails). `ci.yml` checks the musl build on every push.
  - `docker/run.sh`: `tests` (the five suites) or `suites matrix ruby…`. It only installs the fixtures the image can run, and skips the Laravel tests without a Laravel project.
- **What the first pass found** (2026-10-29), besides the three bugs in §11 (musl, Ruby 3.2, Ruby 3.3): Ruby 3.4 quotes frames as `'<main>'` and no longer `` `<main>' `` (§11); the rest came from the harness, which assumed Debian. `sys.orig_argv` only exists from Python 3.10; `SIG_IGN` prints differently before 3.11; under 3.9, botocore is a separate package, with compressed models (`.json.gz`); without `php.ini`, `$_ENV` is filled and `display_errors` is on; Fedora puts `/usr/sbin` before `/usr/bin`; and PHP 8.1 closes its streams before the end of its shutdown: the pty harness must wait for the process before closing the pty.
- **`speedrunner demo`** measures the gain on the machine itself, with the tools it has. It only runs those of a fixed list (aws, pip, ansible, black, poetry, httpie, mypy, pytest, rubocop, rspec, rake, bundle, gem, rails, composer, phpunit, phpstan, npm, eslint, prettier, jest, webpack, next, firebase, yarn…), and only `--version`: never just any script on `PATH`, which might ignore its arguments. For each tool, it takes the median of 3 native runs (`--runs N` to change that), then starts the daemon on `/dev/null` (like the runs), lets it learn over 2 calls, compares the outputs once (code, stdout, stderr) and takes the median of 3 runs with the fast path required. A native fallback is reported, not counted. It stops the daemons it started, not the others. On the VPS: 27 tools, median gain x7.5, in 107 s.
- **To do.** `cargo install` (the embedded files would then have to be inside the crate); publishing the AUR package. A daemon of a previous version that is still running is not stopped on update: it serves until it stops for inactivity.

## 11. Pitfalls already met

- **awscli v1 and botocore.** awscli bundles botocore and exposes it under the name `botocore` through an alias finder: `awscli.botocore.*` and `botocore.*` are then distinct module objects. The CLI uses `botocore.*`, so that is the one to patch.
- **Order of `sys.modules`.** CPython reinserts each module at the end of its import, and `sys.modules` reflects the order in which imports finish. Replaying that order breaks circular imports (`awscli.paramfile` ↔ `awscli.shorthand`). The order in which they start is needed, given by the `import` audit event.
- **A polluted probe lies.** After a refused module, the ones that follow look healthy because the side effect has already happened. Hence a fresh probe after each refusal.
- **Python 3.11 and `sys.excepthook`.** Printing uses `exc.__traceback__`, not the tb argument: `exc.with_traceback(tb)` is needed.
- **`os.environ`.** Reassigning it breaks the references botocore keeps and leaves C-side `getenv` on the daemon's env. It must be changed in place (that is quicken's mistake).
- **`.pyc` files** only record the source's mtime to the second: a test that modifies a module must force the mtime.
- **`/dev/tty`.** Reopening through `/proc/self/fd/N` the `/dev/tty` fd received from the client refers to the caller's controlling terminal, so it fails with ENXIO in the child. `dup()` is needed.
- **getpass** without `/dev/tty` falls back to stdin, and then reads the password from a pipe.
- **The launch grandchild.** If it keeps the client's fds, a pipeline waits for it to end (3 s measured). If it waits for a lock without knowing who holds it, it can resurrect a daemon that was just stopped: hence the `running`/`exiting <pid>` state written into the lock.
- **zsh.** `#` patterns need `extended_glob` (`emulate -L zsh -o extended_glob`). While `.zshrc` is read, `zsh/zle` is not loaded yet, and `add-zle-hook-widget` gives up silently unless `zmodload zsh/zle` is done first.
- **rustc 1.63 (Debian 12).** libc must stay pinned to `=0.2.175`; newer versions need rustc ≥ 1.65.
- **venv-test has no `wheel`.** Reinstall the fixture with build isolation, from a copy.
- **Debugging over ssh.** `pkill -f pattern` also kills the remote shell whose command line contains the pattern. Better to kill by PID.
- **The command line read at import.** Three forms found by the panel.
  - A reference taken at import, like httpie's `def main(args=sys.argv)`: so the child changes `sys.argv` in place (`sys.argv[:] = …`), without reassigning it.
  - A module that starts the program as soon as it is loaded, like `rails/cli`.
  - A module that reads the arguments at global scope.

  The daemon replaces `sys.argv` with an `ArgvProbe` (in Ruby, a module prepended to `ARGV`'s singleton class), which records reads during imports. Only `argv[0]` is allowed: it is the script, as natively. A level-1 import that reads the arguments is written to `<tool>.skip-<hash>.json`, keyed on the script alone, then the daemon restarts without it. At levels 2 and 3, the probe blacklists it.
- **Import hooks installed at run time.** pytest's assertion-rewriting hook loads the plugins itself. Preloading those modules through the standard import deprives them of the rewriting, and causes a `PytestAssertRewriteWarning`. So the child only learns modules loaded by an importlib loader, or by a finder already present in the parent's `sys.meta_path`.
- **`getopt()` in PHP** reads the internal copy of `$_SERVER` made when the interpreter started, not the variable the child rebuilt. So the daemon is started with `-d register_argc_argv=0`, and `getopt()` falls back to the global `$argv`. The daemon reads its own options from `/proc/self/cmdline`. (PHP 8.5 changed this: see below.)
- **Terminal state read at import.** httpie (`Environment.stdin_isatty`), ansible (its colour output) and rubocop (the Rainbow gem) ask at import whether their streams are terminals, and freeze the answer. The captured outputs of the panel and the matrix did not show it: with no terminal on either side, the frozen state was right. The demo recorded in a terminal revealed it: httpie failed, ansible and rubocop lost their colours.
  - The client passes the daemon `--tty MASK[:ROWSxCOLUMNS]`, the fds 0 to 2 that are terminals on its side. For `prewarm`, started by the zsh hook with its fds on `/dev/null`, it is all of them if `/dev/tty` opens.
  - During imports, a raw pty, one per fd, replaces each of those fds. It stays blocking, like a terminal: ansible refuses to start on non-blocking streams. So a module that would write at import more than a pty holds (about 64 KiB) would block the daemon.
  - `isatty` reads are recorded, like env reads. In Python, `sys.stdin`, `stdout` and `stderr` are replaced from startup by a subclass of `TextIOWrapper`, and `os.isatty` is wrapped. In Ruby, the module is prepended to `IO`: `reopen` changes an object's class, which loses a module prepended to its singleton class.
  - The child refuses the fast path if an fd that was read does not have the same state on the client's side.
- **The message of an uncaught Ruby exception.** Ruby 3.1's native printer escapes the message: it doubles backslashes, names control characters (`\e`, `\r`…, otherwise `\x1F`), writes DEL as `\c?`, and leaves tab, newline and non-ASCII bytes as they are. `full_message`, which the daemon uses, does not escape it. So the daemon escapes the message inside the text of `full_message`, line by line, because the message may come from a redefined `message` method (CocoaPods' `CLAide::Help`), and because on a terminal each line is wrapped in formatting codes. Found by `speedrunner demo` on `pod --version`. Ruby 3.2 and later print the message as is (through `detailed_message`): so the daemon only escapes under Ruby < 3.2. Found under Ubuntu 24.04.
- **Ruby 3.3 starts with a timer thread**: two native threads from launch. The Ruby daemon, which required a single thread after preloading, disabled itself for every tool. It now counts the threads beyond those it had at startup; `fork` handles this Ruby thread. Found under Alpine. But each `fork` also replaces that thread in the parent, and on a loaded machine the old one survives for up to ~2 ms (1 to 2 forks in 3,000): the daemon saw a third thread and disabled itself. When no extra Ruby thread exists, it counts again for up to 100 ms before concluding. Found by `platforms` (Ruby 3.4).
- **Ruby 3.4 quotes frames with apostrophes** (`'<main>'`, formerly `` `<main>' ``): the daemon no longer renamed the script's `<top (required)>` frames. It accepts both forms. Found by the version matrix.
- **No locale (PEP 538).** In a fresh container, in cron or in CI, without `LANG` or `LC_*`, CPython adds `LC_CTYPE=C.UTF-8` to its own env at startup. The daemon compared this modified env with the client's, which does not have it: every call ran natively ("startup env differs: LC_CTYPE"). It now compares the client with the env it received (`/proc/self/environ`, which `setenv` does not touch), and gives the tool what CPython added to it, as natively. The matrix sets `LANG`: the `install.sh` test in a fresh Ubuntu found it, after v0.2.0; the `NoLocale` test covers it. Alpine escapes it: its C locale is already UTF-8.
- **An image of another architecture keeps its tag.** After a `PLATFORM=linux/arm64` pass, `rust:1-alpine` was the arm64 image: the next "native" pass built an arm64 client, run under QEMU, which has no `ptrace` (launchers native, everything 3 times slower). So `test-all.sh` always passes `--platform`.
- **Architecture-specific constants.** The Ruby daemon hard-coded `O_DIRECTORY = 0o200000`, the x86 value; on arm64 (asm-generic), that is `O_DIRECT`, and opening the runtime directory failed with EINVAL: no Ruby daemon started on arm. Ruby does not expose `File::DIRECTORY`: the daemon opens with `O_NOFOLLOW | O_NONBLOCK` (a FIFO placed there does not block it), then checks with `fstat` that it is a directory. Only `IN_CLOEXEC` (`0o2000000`, the same on x86 and arm64) stays hard-coded. The PHP daemon's constants are the same on both. Found by the arm64 job of `platforms.yml`, reproduced under emulation.
- **ptrace and musl.** With musl, the libc crate types the `PTRACE_*` constants as `c_int`, as `c_uint` with glibc: the client no longer built for the release target. Found under Alpine.
- **busybox.** Its `env` has no `-S`: the shebang `#!/usr/bin/env -S speedrunner exec bash` needs `coreutils-env` under Alpine. Its composer is a shell script that starts PHP without `exec`, so it is not followed and runs natively.
- **A script named relative to the cwd** (`./manage.py`). The client passed it as is to the daemon, which does not run in the same directory: the daemon stopped at startup, and two Django projects would have had the same daemon name. So the client passes the daemon the absolute path (without resolving links), hence one daemon per project, but keeps the typed path for `argv[0]`. It must also be undone from `runpy.run_path`, which puts the script's path back into `sys.argv[0]`: the child replaces `runpy._ModifiedArgv0` for that one call, then restores it. In Ruby and PHP, such a call runs natively.
- **A Python launcher leaves SIGPIPE and SIGXFSZ ignored**: CPython ignores them at startup, and the exec passes that on. Requiring the same ignored signals as the client therefore sent az native. The request now carries the launcher's ignored signals and umask.
- **Finding the `-m` file without importing anything** fails on namespace packages. `PathFinder.find_spec("azure.cli", …)` raises `KeyError: 'azure'`, because `_NamespacePath` looks for its parent in `sys.modules`. So the daemon imports the parent packages, as runpy does natively.
- **A `bytes` key in `os.environ`.** `os.environ[b"X"]` raises `TypeError` natively (some meson modules try it inside a `try`), but the env-read interceptor recorded the key before the call. Sorting the frozen keys then mixed `str` and `bytes`, and every child failed before the go: meson, pio and copier ran natively. A key of the wrong type is no longer recorded. Found by the candidates panel.
- **Learning that blocks the daemon.** oci_cli.cli reads the command line at import, and 470 modules import it. Each refusal started a fresh probe, which ran oci_cli.cli again: 93 s of learning (puppet 28 s). Meanwhile, the parent did not replace the spare, and each call waited 3 s before running natively. Two fixes:
  - a module that imports a refused module is refused at once, by a finder placed at the head of `sys.meta_path` in the probe, which raises a `BaseException` (an `except ImportError` cannot hide it). In Ruby, `TracePoint(:script_compiled)` raises before the refused file runs. Result: oci 6.6 s, puppet 7 s;
  - learning happens in steps (one probe, bounded to 0.25 s), between which the parent's loop runs normally.
- **A thread started at load time, at level 1.** sshkit starts its connection pool's cleanup thread at load time: kamal and cap disabled their daemon. A level-1 import that starts a thread is now skipped, like one that reads the command line, and the daemon restarts without it. Level 2 then learns what does not depend on it: kamal x5, cap x7.7.
- **In Ruby, a file can depend on a refused file loaded before it**, through the global namespace, without the `require` itself. fastlane.rb (refused: it changes the env) sets `Fastlane::Helper = FastlaneCore::Helper`; dotenv_helper.rb, loaded later by a method, reopens `module Fastlane; module Helper`. Preloaded alone, it created a `Fastlane::Helper` of its own, and the tool failed (`uninitialized constant FastlaneCore::Helper::DotenvHelper`). So the parent replays the complete native order in a throwaway child, and compares the namespaces each file opens while loading. A simpler rule (refuse every file of the same gem loaded after a refused one) was safe, but would have refused almost all of rubocop. Three pitfalls:
  - only count the `class`/`module` of the file's body: a `class << self` inside a method (puppet's metaid.rb) runs on every call, from other files;
  - strip addresses from the names of anonymous classes (`#<Class:0x…>::Config` in psych);
  - call `Module#name` through `instance_method`: puppet redefines it on some classes, which return a Symbol. The tracer must never raise: an exception raised in a TracePoint propagates into the load it is watching.
- **A SIGCHLD that arrives outside `poll` wakes nobody.** The PHP daemon sees end-of-file on a child's control socket, its `waitpid` runs before the child can be reaped, then the signal arrives while PHP runs the loop: the next `poll` slept for up to 4 h, and the client waited for its W (seen once on psalm, in the panel). As long as a child has closed its control socket without having been reaped, the parent looks again after 5 ms. Python (`signal.set_wakeup_fd`) and Ruby (self-pipe written by `trap`) are not exposed.
- **Node, `MADV_DONTFORK`.** V8 marks its heap pages so that they are not copied on fork (the `dc` flag in smaps, not to be confused with `dd`). The child crashed at the first JavaScript object it touched (SIGSEGV in `napi_get_uv_event_loop`). The addon sets `MADV_DOFORK` back before each fork.
- **Node, the parent's threads.** Without `--single-threaded`, the child's first GC waits for workers that do not exist. On normal exit, `pthread_cond_destroy` waits for threads that were waiting on that condition variable in the parent. After an uncaught exception, `uv_thread_join` fails and Node aborts (-6). Hence the exit through `_exit`, and the stderr relay after an exception. Depending on the machine, that exit also crashes with SIGSEGV (CI runners, never on the laptop): so the relay waits on any fatal signal, not just SIGABRT, and closes the pipe so that the copying thread passes on the end of the report. That thread sends X itself: the end of a call that finished with an exception no longer depends on the daemon. Before, a daemon killed by `kill -9` just before the call (the kernel frees its memory before its descriptors, and its spare still takes the call for a few milliseconds) left the client with 125. Found by the CI's `stress` job.
- **Node, signals.** A signal mask only applies to the thread that sets it: blocking SIGCHLD to read it through `signalfd` left it to Node's threads, which ignored it. A finished child was never reaped, and its client waited. The daemon goes through a self-pipe filled by a C handler.
- **Node, exit and `signal-exit`.** jest loads `signal-exit`, which wraps `process.emit` and `process.reallyExit`: a hook on the `exit` event set in the child did not see `process.exit()`, and the child hung in teardown. The daemon wraps both before any preloading (its versions are the innermost); a natural end exits through a Node-API cleanup hook.
- **Node, root certificates.** Node loads them once, under a C++ initialisation lock; a fork during that load left the child (firebase) waiting for it forever. The daemon loads them at startup, before any fork.
- **Node, what it caches.** In a forked process, `process.pid`, `process.ppid` and `process.cwd()` keep the parent's values: a client relayed Ctrl-C to the daemon, and prettier looked for its files in the runtime directory. Same for `Math.random`, whose state is in the heap: every child drew the same numbers. The child redefines both PIDs, does `process.chdir(".")` after `fchdir`, and replaces `Math.random` with a xoshiro128** seeded by the kernel. A spare also takes its own process group, which the client signals.
- **A daemon that stops waits for its children.** It used to exit as soon as the stop was decided (`stop`, stale fingerprint…): a PHP call in progress, or a Node call that ended with an exception, never got its W, and the client exited with 125 ("lost track") although the command had run. Found by the stress suite (2 calls in 8,600, then 1 in 6,800 in Node).
- **A pty delivers late.** What is written on the slave reaches the master a little later (the kernel's "flip" buffer). The Python and Ruby daemons read the master as soon as the import was done: on a slow machine, an import's output arrived during the next one, which was wrongly accused of printing something, and the daemon restarted. They now read until 10 ms of silence. Only with a daemon started from a terminal: CI runs its containers with `-t`.
- **`subprocess` with `timeout`** waits in growing steps, capped at 50 ms: a time measurement must wait without a timeout.
- **Fake servers in tests.** A process forked from the test runner must end with `os._exit` whatever happens, otherwise it runs the rest of the suite in parallel. A fake server that writes to a client that is gone dies of `BrokenPipe`. Either way, you get a ghost daemon.
- **macOS, `/dev/tty`.** On BSD, an fd opened on `/dev/tty` refers to the controlling terminal of *whoever uses it*: in the child, which has none, getpass failed with EIO. The client passes the device itself (`/dev/ttys003`). Found by the `Interactive` class.
- **macOS, `os.set_inheritable`** goes through `ioctl(FIOCLEX)`, which fails with EBADF on an fd opened with `O_SEARCH` (the cwd received from the client): every child failed before answering. The daemon uses `fcntl(F_SETFD)`.
- **macOS, socket paths**: 104 bytes at most (108 on Linux), and macOS's temporary directory (`/var/folders/…/T/`) is deep. The default runtime directory, `/tmp/speedrunner-<uid>`, is short; the tests create their directories under `/tmp`. A path that is too long sends the call native.
- **macOS, the canary and threads.** After proxy detection (3 threads), the CoreFoundation canary passes on the Intel: in a process that has threads, it proves nothing. So the probe checks threads first.
- **macOS, `fcntl` through FFI.** PHP declared it `int fcntl(int, int, int)`; it is variadic, and on Apple's arm64 its variadic arguments are passed on the stack: every spare died ("spare children keep dying"), on both runners. Declared `int fcntl(int, int, ...)` on macOS.
- **Signal numbers differ** between Linux and macOS (SIGCHLD is 17 or 20; 17 is SIGSTOP on macOS). The Node daemon hard-coded them: on macOS its self-pipe was not installed and it saw no child end, which went unnoticed as long as the child sends its X; a child that does `execve` (TypeScript 7's tsc) left its client waiting. Signals by name; a self-pipe that cannot be set up disables the daemon. Test `test_execve`.
- **Node, an error with properties** ends its stack with "{`": the relay that replaces the daemon's frames lost it. On Linux too. Case `exit throw-props`.
- **`pipe2`** is declared by the macOS 27 SDK, not by the macOS 26 one: the addon has its own version, `sr_pipe2`, so as not to depend on the system it runs on.
- **PHP 8.5 registers `argv` in the CLI whatever `register_argc_argv` says** ("Deprecations for PHP 8.5" RFC). The PHP daemon relied on `-d register_argc_argv=0` so that `getopt()` would read the `$argv` rebuilt by the child: in 8.5, `getopt()` reads the internal `$_SERVER['argv']`, the daemon's. Found by `test_getopt` on the Intel runner (PHP 8.5.9). Moreover, the CLI creates `$_SERVER` as soon as it starts (`php_cli.c`): there is no way to defer it until the child. So the daemon removes `argv` and `argc` from that internal copy, through FFI on `core_globals` (`PG(http_globals)[TRACK_VARS_SERVER]`, then `zend_hash_str_del`): `getopt()` falls back to each child's `$argv`, as before 8.5. The declared structure is 8.5's, up to the fields that follow `http_globals`; it is only used if its neighbouring fields (`variables_order`, `memory_limit`, `max_input_time`, `expose_php`, `register_argc_argv`, `auto_globals_jit`) hold what `ini_get()` says, and since the internal copy and `$_SERVER` share one array, `$_SERVER` tells whether the removal took. Otherwise, the daemon stays disabled (native run). PHP suite green on 8.5 (Docker `php8.5`); macOS CI tests 8.4 and 8.5. In 8.5, OPcache is part of the interpreter: the Docker image no longer installs it.
- **macOS, BSD `script(1)`.** On long output, it sometimes turns a `\r\n` into `\r\r\n`; and it echoes back the ^D it sends to the pty when its own input is done, sooner or later. On the native side as on the served side: the harness neutralises both on both sides.
- **`PATH` read at import, and `speedrunner exec`.** pre-commit reads `PATH` while loading. Its daemon, started by `srr`, had a `PATH` without `exec`'s links directory; under `exec`, every call ran natively ("import-time env differs: PATH"). The Python, Ruby and Node daemons now compare this `PATH` without their own `bin-*` directories of the runtime directory, which lead to the same tools: at worst, a sub-tool started by the tool is not served. Found by the pre-commit bench; test `test_tool_reading_path_at_import_served_under_exec`.
- **pre-commit measures the environment while loading.** `pre_commit/xargs.py` computes at import the maximum length of a command line (`ARG_MAX`, minus the size of the whole env, capped at 128 KiB) and freezes it as an argument's default value. It thereby reads every key of the env. Now git passes the hook `GIT_AUTHOR_DATE`, which changes with each commit: every call of the hook ran natively ("import-time env differs: GIT_AUTHOR_DATE"). An adapter, as for rspec: reads made from `_environ_size` of `pre_commit.xargs` are not recorded, and the child recomputes that value with the client's env, before applying the import's env changes again, since `pre_commit.main` removes its keys after importing xargs. Found by the test of the real pre-commit under git.

## 12. Known limits

They are not implementation bugs, but what forking from a daemon changes by nature:

- hash seed shared between children;
- no controlling terminal in the child: a subprocess that opens `/dev/tty` fails;
- what interpreter teardown writes after the X is lost;
- env reads through `os.environ.copy()` or in C are not seen;
- data files read at import are not covered by the fingerprint;
- the child runs to the end if the client is killed with `kill -9`;
- a tool that reads the terminal state at import runs natively when called in another state than that of the client that started its daemon (for example `tool | less` after a call on the terminal); the terminal size read at import is not tracked;
- a clock read at load time is the daemon's; for rspec alone, an adapter resets it to the start of the run;
- in Ruby, comparing namespaces does not see a dependency on a refused file through `defined?` or through a constant's value; a missing constant makes loading fail in the probe, so the file is refused;
- a launcher is only followed up to a Python interpreter, and only through its final `exec` (wp-cli and drush go through two shell scripts, then PHP: not followed); without ptrace (restrictive container, Yama `ptrace_scope` 3), it runs natively;
- Node, `--single-threaded`: sequential GC and compilation in the child. Compute-heavy work slows down, by up to 55 % (a script that stresses the GC); vite, some of whose modules start Rust threads at load time and are therefore reloaded on every call, is slower served than native (x0.56);
- Node, a learned module is preloaded for every command, as in Python: if it changes Node's global state while loading (`signal-exit` wraps `process.emit`, `graceful-fs` replaces `process.chdir`), the change also applies to commands that do not load it. Refusing them cost too much: `graceful-fs` is loaded by every npm or firebase call. Such a module should only be preloaded if every call loads it;
- Node, no `/dev/tty` bridge in the child; learning needs Node ≥ 22.15 (`module.registerHooks`). Without it, only Node's startup is saved;
- Node, an import that enumerates the environment's keys sends native any client whose set of keys differs (apart from `PWD`, `OLDPWD`, `SHLVL`, `_` and `SPEEDRUNNER_*`); a stack captured while the entry point is evaluated, and printed by the tool itself, shows the daemon's frames;
- for a launcher, `prewarm` runs the preamble without arguments: harmless for az and gcloud, but a launcher that did more than prepare its exec would also do it on every prewarm;
- pre-commit's git hook: served only under `speedrunner exec` or `activate` (git started outside them does not see the `bash` link), and if pre-commit is on `PATH` or named. Under them, every `bash` called by name goes through the client (one more startup, +4 ms measured in Docker Desktop), and `$BASH` there names the link, which leads to the same bash. The hook's daemon (`python -mpre_commit`) is distinct from the one of the `pre-commit` script: `speedrunner status pre-commit` and `stop pre-commit` target the latter, the former stops after 4 h without a call;
- macOS: a launcher written in Python on a framework Python (az installed by pip) runs natively: after the exec of its `Python.app`, the kernel no longer reports the following execs; a launcher that mentions `umask` runs natively (another process's umask cannot be read); the kqueue fingerprint does not see a mere attribute change (`touch`, `chmod`) of a module file; a tool that uses CoreFoundation while loading (proxy detection, run loop) gets that module reloaded on every call; the bash hook needs bash ≥ 4.2, the system's is 3.2 (zsh, the default shell, has no such limit); the system's Ruby 2.6 is not served (the daemon needs Ruby ≥ 3.0);
- macOS, Node: learning is slower (an i7 takes ~4 s for eslint's 221 modules);
- a Node built without the code cache of its internal modules (Homebrew's `node@22`, Fedora's) is served without `--no-lazy`: with it, it would name a loader frame differently in an error's stack (`Object..js` for `Module._extensions..js`). Its first daemon records the binary (real path, size, mtime) in `$XDG_CACHE_HOME/speedrunner/node-no-code-cache` and stops before opening its socket; the client starts the next one without `--no-lazy`. Each child then compiles what it runs: a smaller gain. (Setting `--no-lazy` from the daemon, through `v8.setFlagsFromString`, keeps the right names but costs ~10 % on a Node with a cache: eslint 47.7 → 52.5 ms on the i7.) Tested under Fedora 43 (Docker target `node-fedora`) and with the Node of the macOS runners.

## 13. Status and next steps

| Stage | Status | Key result |
|---|---|---|
| 0 Baseline, go/no-go | done | GO, initialisation dominates |
| 1 Daemon + client, level 1 | done | x1.7, matrix green |
| 2 Spare, early exit, levels 2 and 3 | done | x5.4 / x5.4 / x4.2; pip x8.7 |
| 3 zsh hook, anticipation, `srr` | done | 300 ms of typing ⇒ 37 ms on the first call; scripts x5.3 |
| Scripts: `activate`, `exec`, automatic mode | done | one line at the top of a script, x4.6 to x5 on an aws loop; 11 tests |
| 4 Robustness and security | done | 52 + 5 tests green, none deferred any more |
| R0 Ruby baseline | done | GO: loading 65–85 %; ceilings x5 (rubocop) to x25 |
| R1 Ruby daemon, level 1 | done | rake x12, bundle x4.5, gem x3.2; 29 Ruby tests green |
| R2 Ruby learned preloading | done | rubocop x3–x3.8, bundle x15, gem x8, rake x13; 32 Ruby tests green |
| P0 PHP baseline | done | GO: 60–90 % startup; ceilings x2.5 (composer show) to x10 (phpunit), artisan x5 |
| P1 PHP daemon | done | artisan x3.7–x3.9, phpunit x3.6, composer x2.1–x3, phpcs x2.8; 33 PHP tests green |
| Perl | to assess | if as simple as PHP |
| Extended panel (Python, Ruby, PHP) | done | 36 CLIs, 36 identical outputs; ansible x16, poetry x14, solargraph x13, aws x5.8; 145 tests green |
| L0 Launcher CLIs: az, gcloud, `./manage.py` | done | GO: Django x4.8–x7.7 (relative-path bug fixed) |
| L1 Following launchers, `-m module`, learned `sys.path` | done | az x1.9–x4.3, gcloud x3.5–x4.2, no tuning; 67-test matrix green |
| Candidates panel (47 more commands, Docker) | done | 45 served, 45 identical outputs, median x7.7; dbt x80, airflow x23, openstack x22, checkov x12; 6 bugs fixed; 70 + 37 tests green |
| N0 Node go/no-go | done | GO with conditions: safe fork (1,000 calls out of 1,000), ceilings x3 to x18 |
| N1 Node daemon | done | 13/13 identical outputs; eslint x14 and x6, next x8.6; 31-test Node matrix green |
| Stress suite, daemon-side security tests | done | ~38,000 calls under `kill -9`, stops and staleness, no failure, once the race bugs it found were fixed |
| pre-commit under `git commit` (hook followed through a `bash` link) | done | `speedrunner exec git commit` 1.11 s → 0.31 s (x3.6), pre-commit and its `language: system` hooks served, same output |
| Against dedicated daemons | done | eslint x7.6 (eslint_d x3.1), mypy x4.2 (dmypy x1.4); blackd + curl wins |
| 4 bis Driver snapshot (level 4) | postponed | specific to aws |
| 5 Comparative bench and video | done | video of the demo script: 24.0 s → 5.3 s, x4.5, warm daemons (median of three recordings: 22.8 s → 5.6 s warm, 7.3 s cold) |
| Release 0.3.1 | done | static x86_64 and aarch64 binaries, signed provenance attestations |
| Release 0.4.0 | done | macOS (universal binary, all four runtimes), PHP 8.5, Node without code cache; `.deb` packages, signed APT repository, Homebrew tap |
| M0 macOS, go/no-go | done | GO for Python: ceilings x5 to x50; CoreFoundation kills the children of a parent that used it |
| M1 macOS: Python daemon and client | done | Intel x4.4 (aws) to x20.6 (pip), M6 x4.2 to x16.6; 16/16 identical outputs; matrix green on both; CoreFoundation rule (canary); kqueue fingerprint |
| M2 macOS: Ruby and PHP daemons | done | rubocop x4.9, rake x8.5, gem x8 (i7); phptest x5.7 to x8.9, composer x2.3 (CI); Ruby and PHP suites green on Intel and Apple Silicon; Ruby and Objective-C; PHP 8.5 served since 0.4.0 (§11) |
| M3 macOS: Node daemon | done | safe fork (200/200); eslint x5.7 (i7) to x8.8 (M6), prettier x4.9, npm x2.5, tsc x2.3; 33 Node tests green on both Macs; signals by name, errors with properties |

Open decision: when the client dies (`kill -9`), the child currently runs to the end; sending it SIGHUP would be closer to native, but less safe for a command like `aws s3 rm`.

Decided: the name **speedrunner**, with `srr` as the short command; dual licence MIT OR Apache-2.0; one daemon per tool, not per environment (fidelity and speed first, memory measured in the panel).

Next:

- publish the AUR package (ready in `packaging/`; the Homebrew tap is published);
- macOS: the Python daemon and the client are ported (M1, §2 quinquies, `bench/results/…-macos-m1-mac/ANALYSE.md`). Ruby and PHP too (M2, `bench/results/…-macos-m2-mac/ANALYSE.md`). Node too (M3, `bench/results/…-macos-m3-mac/ANALYSE.md`). The universal binary is published by the release, installed by `install.sh` and by the Homebrew formula (§10 bis). PHP 8.5, Nodes without code cache and launchers are served there too (§2 quinquies);
- the JVM, through caches or CRIU: see [multi-runtime.md](multi-runtime.md).

A new OS was ruled out by measurement (VPS, hyperfine, 30 runs). Kernel time is only 44 ms out of 218 for `aws --version` and 70 ms out of 304 for the ec2 skeleton. A perfect kernel would therefore cap the gain at x1.25, and that kernel time itself mostly comes from the initialisation speedrunner removes (file opens, allocations). For `wget --version`, the total is only 2.2 ms.
