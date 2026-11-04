# Tracking what a tool reads at load: what it costs

Panel on the VPS, 8 interleaved runs per command, with the three changes together:
what a tool reads as it loads is tracked, a client killed takes its tool with it,
and the daemon follows a client out of a directory no longer in use. Compared with
`20261030T212139Z-panel-vps` (20 runs, earlier code).

- **38/38 outputs identical to the native run.**
- Speedup, geometric mean: x4.84, against x5.06. Median change per command: -4%.
- Largest drops, in ratio: solargraph x12.5 to x8.3, bundle x14.8 to x12.0, poetry
  x21.7 to x17.8, mypy x3.9 to x3.2, rdoc x17.7 to x15.0, black x9.4 to x8.1.
  These commands take 10 to 30 ms served: a millisecond or two moves the ratio a lot.
- 8 runs against 20, on a noisy VPS: small differences are not significant, and the
  cost was not split between its sources (the audit hook, the extra stats, the two
  messages to the parent).

From another directory (35 panel commands, started in one directory, then called in
another): no output differs apart from rspec's printed time. az and `bundle --version`
run natively there until the daemon follows. On a laptop, of 6 Node tools, only
prettier does.
