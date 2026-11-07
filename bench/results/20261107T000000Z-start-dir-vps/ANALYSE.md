# A tool installed under the directory its daemon starts in

`bench/demo/record.sh` with aws v1, `NO_REC=1 REPEAT=3 RUNS="cold warm"`, on the 2-vCPU VPS, the client before and after this fix, from three directories. The times are the demo script's, medians of 3.

| Started from | What is under it | Cold, before | Cold, after | Warm, before | Warm, after |
|---|---|---:|---:|---:|---:|
| `/root` | the tools' venvs | 13.7 s | **7.3 s** | 7.5 s | **5.2 s** |
| `bench/demo` | nothing the tools read | 7.6 s | 8.4 s | 5.2 s | 5.1 s |
| `/tmp` | the project of the demo | 8.5 s | 8.5 s | 6.5 s | 6.8 s |

From `/root`, warm before the fix went 12.2 → 7.5 → 5.5 s as daemons moved one by one. The 8.4 s from `bench/demo` is a slow first run (9.5, 8.4, 7.8 s): three runs on a shared VPS do not show a difference there.

## The cause

Module-level code that asks for the cwd (`tempfile`, which keeps nothing of it, is enough) made `keep_file_reads` take every file read under the start directory for one read through the cwd. So a tool in a venv under that directory recorded its own files as relative. From another directory they are not there, `why_cwd_differs` answered `import-time file differs: venv-aws/lib/...`, and the call ran natively, right but with no gain, until the daemon followed the client after 30 s of its old directory unused.

The tool's own files are now left out: those under its `sys.path`, as `learn_roots` gives them, so not the start directory itself. They stay watched like any other, by absolute path.

## What this does not change

- A project's file under the start directory stays relative, as it must: `test_project_file_under_the_start_directory_stays_relative`. That is the likely reason `/tmp` stays slower here, its daemons starting there with the project under it; it was not looked into further.
- Files read under the home directory when a daemon starts there, such as a config in `~/.something`, are not in a `sys.path`: they stay relative, with the same effect if a tool reads one at load. Not measured.
- Only the Python daemon had this. Ruby and Node count only paths that are relative as given, and PHP has no such tracking.
