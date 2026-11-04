# Side effects: plain runs against served ones

Measured on 2026-11-04 on the VPS. The main results come from **d68b0c2**, before the ansible fix. [After the fix](#after-the-fix-5047c3f) gives the rerun on 5047c3f, in `after-5047c3f/`.

The run covers, with `bench/side_effects.py`: 27 Python and Ruby commands of `bench/panel.py`, plus one added command, `ansible -m copy`. Each ran in 4 new setups (plain A, plain B, a third plain run as a null check, served), with 4 warm calls and then 5 measured calls, 1.1 s apart. PHP, Node and `bench/candidates.py` were not run.

Files: `side_effects.json` (every command), `fixture.json`, `summary.md` (generated), `ansible_temp_dir.sh` and `ansible_temp_dir.txt` (the reproduction and its output).

## Result on d68b0c2

| | |
|---|---|
| Fixture | 16 of 16 as expected, in Python and Ruby |
| Commands served | 28 of 28, no call refused once warm |
| Null run (plain against plain) | 0 differences on all 28 |
| Left out as noise | nothing |
| Commands with a difference | 3, all ansible |
| Values frozen across served calls (PID, clock) | 0 |

The other 25 commands show no difference, within the limits below. They are not proven free of side effects: see [What the method cannot see](#what-the-method-cannot-see).

## The differences

### 1. ansible: a command that succeeds natively fails when served (fixed since)

| `ansible localhost -c local -m …`, 3 calls each | Plain | Served |
|---|---|---|
| `ping` | 0 0 0 | 0 0 0 |
| `copy -a content=hello dest=c.txt` | 0 0 0 | **2 2 2** |
| `template -a src=t.j2 dest=t.txt` | 0 0 0 | **2 2 2** |

```
"msg": "could not write content temp file: [Errno 2] No such file or directory: '<home>/.ansible/tmp/ansible-local-29575042ejjx37y/.5rjzgboc'"
```

Reproduced with ansible's default paths and no settings, by `ansible_temp_dir.sh`. In the measured run, the script reported `exit-code` (plain 0, served 2 on all 5 calls) and `output`.

Cause:

- `ansible/config/manager.py:184-185` makes a temp directory while loading, named after the process, and registers its removal with `atexit`.
- In the daemon this happens once, so every child shares that directory and inherits the `atexit` handler.
- The first child that exits removes it. Every later child still has its path in `C.DEFAULT_LOCAL_TMP`, and finds it gone. A small test tool showed this directly, in Docker on the commit before PR 18: the directory `exists` in the first served call that has it, and is `GONE` in all the following ones, always with the same path.

So one call of anything, even `ansible --version`, is enough to break what follows, for the daemon's whole life. In the reproduction the daemon's directory was already gone after 4 `--version` calls.

Other uses of `C.DEFAULT_LOCAL_TMP` found by `grep`, not tested: `assemble`, `plugins/action/__init__.py`, vault editing, galaxy, the ssh connection plugin, the AnsiballZ cache (`executor/module_common.py`). `ping` still passes; why was not looked into.

`bench/panel.py` could not see this: its ansible commands, `ansible --version` and `ansible-doc ping`, never use the directory, and they exit 0 both ways. No command runs twice and nothing is corrupted: the task fails with an error and the exit code says so.

### 2. ansible: temp directories left behind by each daemon start (fixed since)

`state-extra` on all 3 ansible commands: 3 `ansible-local-<pid>…` directories in the served setup that plain runs never leave. Plain runs remove theirs. In the reproduction these 3 directories stayed after `speedrunner stop`.

They carry the pids of other processes than the daemon (2957530, 2957532, 2957535 for daemon 2957504). They are probably the disposable probes that vet learned modules, which load ansible and do not run its `atexit`; this was not traced. They are empty, and they add up with each daemon start, in `~/.ansible/tmp`.

## Why the fixture can be trusted, and how far

The fixture runs 8 tools in each language. These must be reported:

- a line appended at load;
- the same content rewritten at load, seen only through the mtime;
- a PID kept at load;
- a clock kept at load (a float, and `%H:%M:%S`).

These must not be reported:

- a tool with no effect;
- a line appended at run;
- a random value, PID, clock and random temp file name at run;
- a file written only if absent.

On real tools, the method also saw writes and agreed on them: `az vm --help` and both gcloud commands write one log per call, under a name with the time or the PID, and `mypy` rewrites `cache.3.db`. These came out the same in plain and served.

The first pass taught two things, now in the script:

- `speedrunner status` answers from a spare, which may predate what the last call taught. The script therefore makes served calls until the state stays the same, so that a module learned during the measured calls does not count as their effect.
- The XDG directory that only speedrunner's own files need is not recorded.

## What the method cannot see

**The commands are the main limit.** 14 of the 28 only print a version or help, and 24 change no file under the root from call to call. Effects that show only when a tool does real work are out of reach. Difference 1 is one: it was found by following difference 2 by hand, and `ansible -m copy` was then added. A clean row means "nothing seen on this command", not "safe".

Also out of reach:

- **Writes outside the roots.** Only `HOME`, the working directory, `TMPDIR` and the XDG directories are recorded. A coarse look by mtime covers `/tmp`, `/var/tmp`, `/dev/shm` and the real home directory, which includes the venvs (the speedrunner checkouts and result folders are skipped). It found only Docker's `runc-process*`, unrelated. `/etc`, `/var` and files with a restored mtime are not looked at.
- **Files created and removed within one call.** Recording happens after each call. ansible's temp directory is such a case: plain runs make and remove one per call, and the per-call comparison shows nothing. Only its leftovers showed.
- **Effects that are not files:** network, databases, shared memory, semaphores, processes left running, locks (file locks taken at load are handled separately since PR 19).
- **Values never printed.** A PID or a clock kept at load is only caught if it reaches stdout or stderr. One used internally, or printed only by another command, passes. None of these commands prints a PID.
- **Slow clocks.** The 5 calls span about 5.5 s, so a value that moves by the minute or by the day looks stable in plain too, and is not checked.
- **What varies by itself.** When plain runs disagree on a file's size or hash (the contents of gcloud's logs), only the count of changes is compared. For a growing file, only the size is compared, not the bytes added.
- **Rare events.** 5 calls will not show something that happens every 10 calls, such as a log rotation.
- **One daemon per command, in a new setup.** In real use a daemon serves different commands one after another, and learns from each. Effects of that order are not exercised, nor is a setup that has already been used for a long time.
- **No terminal.** stdin is `/dev/null` and the output is captured.
- **Once-only effects** are only compared through the end state, after the warm calls.

## After the fix (5047c3f)

PR 22 makes a directory created by `tempfile.mkdtemp` at load the daemon's. The rerun was built from its head, a9531a0, which has the same tree as main at 5047c3f. Fixture: 16 of 16.

| `ansible localhost -c local -m …`, 3 calls each | Plain | Served, d68b0c2 | Served, 5047c3f |
|---|---|---|---|
| `ping` | 0 0 0 | 0 0 0 | 0 0 0 |
| `copy` | 0 0 0 | 2 2 2 | **0 0 0** |
| `template` | 0 0 0 | 2 2 2 | **0 0 0** |

Temp directories, in the same reproduction:

| | d68b0c2 | 5047c3f |
|---|---|---|
| While the daemon lives | 3, not the daemon's | 1, the daemon's |
| After `speedrunner stop` | 3 | 0 |

The 3 ansible rows of the measurement no longer report an exit code or output difference. What remains is expected: `state-extra` on the daemon's directory while it lives. Under `-m copy` it also holds `ansiballz_cache`, which served calls now share, where each plain call has its own and removes it. Concurrent served calls on that shared cache were not measured.

## The same pattern elsewhere: not fixed

PR 22 covers Python's `tempfile.mkdtemp`, nothing else. `load_state_probe.py` tries the pattern on small test tools: state made at load, released at exit, then checked by each call. These ran in Docker on 5047c3f (`after-5047c3f/load_state_probe.txt`), on Python 3.11, Ruby 3.1 and Node 24. A plain call always finds its state, and leaves nothing behind.

| Runtime | State made at load, released at exit | Served, 4 calls | Left after stop |
|---|---|---|---|
| Python | `mkdtemp` + `atexit` | exists | 0 |
| Python | `TemporaryDirectory` | exists | 0 |
| Python | `NamedTemporaryFile` | exists; no shared file, and the status shows `blacklisted=1`, so its module is apparently refused and each call makes its own | 0 |
| Python | `mkstemp` + `atexit` | **GONE**, and `Exception ignored in atexit callback` on stderr | 0 |
| Python | a directory of its own + `atexit` | **GONE** | 0 |
| Ruby | `Dir.mktmpdir` + `at_exit` | **GONE** | 0 |
| Ruby | `Tempfile` | exists | 0 |
| Ruby | a directory of its own + `at_exit` | runs natively every other call (`stale: installed files changed`), so right but slow; may come from the test layout, the tool's script being under `HOME` | 0 |
| Node | `mkdtemp` + `on('exit')` | **GONE** | **1** |
| Node | a directory of its own + `on('exit')` | **GONE** | **1** |

These are test tools, not real ones: no real Ruby or Node tool was found failing this way. The panel's Ruby commands show no difference, and Node was not run. Sites worth checking, from a `grep` of what is installed on the VPS, none of them tested, and it is not known whether they run at load:

- `ansible/module_utils/basic.py:505` and `ansible/module_utils/_internal/_dataclass_validation.py:183`, which remove a directory at exit;
- `filelock/_soft_rw/_sync.py:786`, `_pytest/_py/path.py:1329`;
- `bundler.rb:687`, `rackup/server.rb:436`, in Ruby.

## Still to decide

Nothing in the daemons was changed by this measurement. Open points:

- whether to extend the treatment to the failing cases of the table, Ruby and Node first since they have the same bug as ansible had;
- longer command lists (real work rather than `--version`), which would make this measurement more useful;
- PHP, Node and the candidates, still to run.
