# The demo script, cold and warm, on 0.4.2 and with the learning fix

`bench/demo/record.sh` with `NO_REC=1 REPEAT=5 RUNS="native cold warm"`, each run timed by bash's
`time` (wall clock, seconds). Native: the script as is. Cold: `speedrunner exec`, every daemon
stopped before the run, their learned caches kept (a user who ran the tools before, e.g. the
first run of the day). Warm: the same, daemons left ready by the previous run. 2-vCPU VPS,
Debian 12 (`raw.txt`).

| Run | Client and daemons | Native | Cold | Warm | Cold gain | Warm gain |
|---|---|---:|---:|---:|---:|---:|
| `out` | v0.4.2 release | 22.2 s | 7.0 s | 5.0 s | x3.2 | x4.4 |
| `out2` | v0.4.2 client, daemons with the learning fix (`SPEEDRUNNER_DAEMON`) | 22.4 s | 7.0 s | 4.8 s | x3.2 | x4.7 |

Medians of 5; every run in `raw.txt`.

**Outputs.** On v0.4.2, every served run differed from native: pytest failed 11 tests, because
the pytest daemon had learned the first service's `app` module and preloaded it for every later
call (docs/ARCHITECTURE.md §11, a project's modules). With the fix, served and native outputs
differ only where two native runs also differ: timestamps, phpunit's timing, archive sizes and
upload speeds.

The video (24.0 s → 5.3 s, recorded on 29 October) stays the public figure; these runs give the
cold ratio next to it: about x3 on a first run.
