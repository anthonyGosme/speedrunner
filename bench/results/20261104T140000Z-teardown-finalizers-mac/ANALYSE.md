# Teardown with finalizers

A `__del__` on a run's object held in a module global ran natively, never when served. The same
went for a suspended generator's `finally`, and for the last buffer of a file opened at import
and written during the run, which was lost. Seen on macOS and Linux alike.

## Why

At exit, CPython drops each module from `sys.modules`. The module object goes with its last
reference, not its dictionary, which is in a cycle with its functions' `__globals__` and only
falls to the collection at exit. `gc.freeze()` hides that dictionary from the collection, so what
it holds is never freed. The child leaves normally: `strace` shows `exit_group(0)` with no call
to the `__del__`'s `open`.

## What was measured

`ab.py`: old daemon against new, alternating, warm, 40 runs each, then loops of 50 calls
(`machine.txt`; a busy machine, hence the alternation).

| Command | Old | New |
|---|---:|---:|
| `aws --version` | 140.8 ms | 140.2 ms |
| `aws ec2 run-instances --generate-cli-skeleton` | 259.1 ms | 258.5 ms |
| `black --version` | 23.5 ms | 23.8 ms |
| Loop of 50 `aws --version` (3 passes) | 7.07 / 7.20 / 7.10 s | 7.17 / 7.12 / 7.29 s |

## Options measured and dropped

- **`gc.unfreeze()` on every call.** Exact, but each child then copies and frees the whole heap:
  a loop of 50 `aws --version` on the 2-vCPU VPS went from about 4 s to about 7.5 s (another bench
  was running there: an order of magnitude only). Hence a check first, and a full teardown only
  when it finds something.
- **Running a finalizer made at import in each child.** A `NamedTemporaryFile` made at import
  was gone from the second call on. Such a module is refused, as one that starts a thread is.
- **Refusing every type with a `__del__`.** pyparsing's `Forward` only warns, if asked to. Refused,
  it took nearly all of pip with it (`pip list` x2.3, against x8.3 once exempted).
- **Treating garbage as live.** `regex` leaves a parser node with a `__del__` in a cycle. Waiting
  for a full teardown took cfn-lint from 443 to 752 ms. A collection over the run's heap, only
  when a finalizer turns up, leaves 514 ms.

## What it costs

cfn-lint is the one command found to pay: 438 to 514 ms, x3.7 to x3.15 (Docker, 12 runs, twice).
A command with a live finalizer pays its native teardown, in exchange for the native behaviour.

Across the two Python panels (Docker, 5 runs each): 42 of 42 outputs identical to native and no
module refused for a finalizer. az and gcloud are not in the images. In a survey of 23 commands
of 11 tools, only `ansible localhost -m ping` had a finalizer at exit, 5
`multiprocessing.connection.Connection` (before the collection was added).
