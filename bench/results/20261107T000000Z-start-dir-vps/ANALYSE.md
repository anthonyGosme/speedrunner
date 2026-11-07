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

## Then narrower: who asked

Asking for the cwd now only counts if the asker may pass it on. `tempfile`, `multiprocessing.process` and `rich` keep it to themselves (read in their source; `CwdKeptToItself` fails if that changes), so a file read elsewhere under the cwd did not owe its path to them, and a config in the home directory stays absolute. ansible keeps the wider treatment: its `unfrackpath` returns paths built on the cwd, which other modules read. With the rule applied to it, the test standing for that case gives `here` where native gives `none`: a wrong output.

On the demo script this changes nothing measurable, medians of 3 on the VPS, with the first fix against this one:

| Started from | Cold, first | Cold, narrower | Warm, first | Warm, narrower |
|---|---:|---:|---:|---:|
| `/root` | 7.9 s | 8.2 s | 5.9 s | 5.6 s |
| `bench/demo` | 7.7 s | 8.0 s | 6.0 s | 5.6 s |
| `/tmp` | 8.8 s | 9.4 s | 7.4 s | 8.0 s |

So its gain is for the case it was made for, a config under the home directory, tested and not measured on a real tool.

## What this does not change

- **`/tmp` stays slower, and not for this reason:** neither fix moves it. Its daemons start there with the project under it; why that costs more was not found.
- A file named by a relative path stays relative, and a tool that keeps the cwd stays native elsewhere: `test_module_that_keeps_the_cwd_stays_native_elsewhere`.
- With ansible, a file read under the start directory still counts as read through the cwd, unless it is ansible's own: from elsewhere it runs natively, right and slower.
- Only the Python daemon had this. Ruby and Node count only paths that are relative as given, and PHP has no such tracking.
