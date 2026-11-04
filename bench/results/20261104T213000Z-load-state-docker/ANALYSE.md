# State made at load and released at exit, after the fix

`load_state_probe.py`: one tool per pattern, plain against served.
`load_state_probe.txt`: its output in the Debian image (Python 3.11, Ruby 3.1) and
the Node 24 image, with the client of this change.
`load_state_probe_before.txt`: its output on the VPS, at commit 5047c3f.

| Pattern | Before | After |
|---|---|---|
| Ruby `Dir.mktmpdir` + `at_exit` | GONE | found, removed at stop |
| Ruby own directory + `at_exit` | native every other call (stale) | found, removed at stop |
| Node `mkdtempSync` + `on('exit')` | GONE, 1 left after stop | found, removed at stop |
| Node own directory + `on('exit')` | GONE, 1 left after stop | found, removed at stop |
| Python `mkstemp` + `atexit` | GONE, an error on stderr | found, removed at stop |
| Python own directory + `atexit` | GONE | found, removed at stop |

The Ruby alternation was no matter of test layout: `FileUtils.mkdir_p` probes the
directory, which became an input of the daemon; the first child removed it, so the
next call found the daemon stale. A real mechanism, for any Ruby tool of this shape.

Real tool, Debian image: `ansible localhost -c local -m copy`, 6 calls in a row,
rc 0 each, nothing left after stop. With a client from before PR 22: rc 2, 3
directories left. `--version` unchanged within noise, as for aws, rubocop, black
(one run of 5, not a benchmark).

Suites, `docker/test-all.sh`: Debian (matrix 143, shell, scripts, Ruby 80, PHP 40,
install, docs), Ruby 3.1 and 3.4, Node 22 and 24-alpine (65), green on the final
code; Python 3.9 and 3.14, Node 24 and stress green before a last comment edit.
macOS (Intel, Python 3.11, Ruby 3.4, Node 23): the load-directory tests alone, green.
