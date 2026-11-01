# Extending speedrunner to other runtimes: proposal

Written on 2026-10-28, after stage 1 (Python). Nothing had been measured yet for these runtimes. The gains below are orders of magnitude taken from prior art, **to be confirmed by a stage 0 per runtime** (go if initialisation weighs at least 50 % of the time, as for aws).

**Status (2026-11-01).** Ruby (stages R0 to R2) and PHP (P0, P1) were built as proposed: fork servers that speak the same protocol. Node is served by fork too, against what this proposal expected (update below). The JVM and CRIU have not been started. Current state: [ARCHITECTURE.md](ARCHITECTURE.md) §2 bis and §13.

## What is reused as is

- **The Rust client and the protocol** (takeover, "go" byte, fd passing, signal relay, exit status) know nothing about Python. They only need to be routed to the right daemon on the shebang or on the resolved binary: at the time, `read_shebang` only accepted `python*` (it now routes `ruby*`, `php*` and `node` as well).
- **The test matrix**: same harness. A test tool per runtime is added, the counterpart of `hstest`.
- **Anticipation in the shell** (stage 3) works for every runtime.

## What is not reused: the fork

The core of speedrunner is `fork()` of an already initialised process. That only works if the runtime is single-threaded at fork time. Python is, Ruby nearly is (and knows how to repair itself after a fork), Node and the JVM never are: they start internal threads (GC, compilation, I/O) at launch, and after a `fork()` only the calling thread survives. So those two need another technique.

**Update (2026-10-31): for Node, the fork works after all.** With `--single-threaded`, V8 hands nothing to its threads, and an addon removes the other obstacles (`MADV_DONTFORK` on the heap, exit through `_exit`, cached PID and cwd). That is stage N1: see [ARCHITECTURE.md](ARCHITECTURE.md) §2 bis and `bench/results/…-node-panel-docker/`. What follows still holds for the JVM.

## Runtime by runtime

| | Ruby | Node | JVM | Native binaries (wget, curl…) |
|---|---|---|---|---|
| Technique | Fork server, as for Python | V8 compile cache, then V8 snapshot or CRIU | AOT/CDS cache, then CRaC or CRIU | None |
| Precedent | Spring and Zeus (Rails) already do preload + fork | `NODE_COMPILE_CACHE` (Node ≥ 22.1), `--build-snapshot` (Node ≥ 18.8) | AppCDS (`-XX:+AutoCreateSharedArchive`, JDK ≥ 19), AOT cache (JEP 483, JDK ≥ 24), CRaC (Azul Zulu, BellSoft Liberica builds) | — |
| Expected gain | Same order as Python, x3 and more if initialisation dominates | Cache: modest, ~x1.2 to x1.5. Snapshot: high, but not for every tool | Cache: modest, ~x1.3 to x2. CRaC: very high (Spring Boot 4 s → 38 ms) | None: 2.6 ms measured for `wget --version` |
| Zero configuration? | Yes | Cache: yes. Snapshot: no, the tool must be bundled | Cache: hard, see below. CRaC: no, specific JDK | — |
| Cost | Medium: a Ruby daemon of about 500 lines, no dependency, speaking the same protocol | Cache: low. Snapshot: high and fragile | Cache: medium. CRaC: high | — |

### Ruby: the natural extension

Same architecture as Python: a Ruby daemon (standard library) preloads the tool's binstub, forks a child per call, receives the fds through `UNIXSocket#recv_io` and updates `ENV` in place (`ENV.replace`).

Ruby-specific pitfalls, to add to the matrix:
- RubyGems binstubs (`Gem.activate_bin_path`) and `bundle exec`: preloading must activate the same gems as natively;
- gems that start threads at load time: same rule as in Python, disabling and native fallback;
- `at_exit`, `exit!`, `abort`, `SignalException`, `$0` and `$PROGRAM_NAME`;
- `Process.setproctitle` and gems that query their PID at load time.

Possible reference targets: `rubocop --version`, `bundle --version`, `pod --version` (CocoaPods).

### Node: a safe but modest gain, then a risky leap

1. **V8 compile cache**: the client starts the real `node` with `NODE_COMPILE_CACHE` pointing to a directory of ours. V8 validates the cache (source hash, version), so the output does not change. No daemon needed, safe, but the gain is partial: it avoids compilation, not the execution of the modules. Requires Node ≥ 22.1, whereas Debian 12 ships Node 18.
2. **V8 snapshot of the initialised heap** (`node --snapshot-blob … --build-snapshot`): each call starts a fresh process from the snapshot, without fork. The tool must be bundled into a single file, and it does not work with native modules (`.node`) nor with some APIs at capture time. Zero configuration is lost, and some tools will not make it.
3. **CRIU**: see below.

### JVM: the cache is simple on paper, injecting it is not

1. **AOT or CDS cache**: options must be added to the `java` command line. The `JAVA_TOOL_OPTIONS` and `JDK_JAVA_OPTIONS` variables allow it, but the JVM then prints `Picked up …` on stderr, which violates "zero output difference". The call to `java` would have to be rewritten instead, while JVM tools (gradle, mvn, sbt…) often go through their own shell scripts. Doable tool by tool, not with zero configuration.
2. **CRaC**: restoring a snapshot taken after initialisation. It is the most spectacular gain, but it needs a CRaC JDK (not Debian's JDK) and a tool that supports restoring: files and sockets open at snapshot time.

### CRIU: the only real "any command" path

Any process is captured after its initialisation, then restored on each call. It is language-independent. But:
- it needs root or `CAP_CHECKPOINT_RESTORE` (Linux ≥ 5.9): unacceptable for a general-purpose tool, acceptable on our VPS for a trial;
- restore time grows with the process's memory (tens of ms);
- PIDs, fds, terminal and environment variables must be rewired on each restore;
- the image holds the process's memory, so potentially secrets: it must be protected like a sensitive file.

To be kept for an exploratory trial, not for the product.

**First measurement (VPS, 2026-10-28, Debian 12's CRIU 3.17.1, root).** A Python process that imported `awscli.clidriver`, 36 MB of RSS, idle:

- `criu check`: the VPS's 6.1 kernel has everything needed;
- capture: 42 ms, 25 MB image;
- restore (`criu restore -d`): **~21 ms** median over 10 tries, from 19 to 40 ms;
- two simultaneous restores of the same image: the second fails (`Can't fork for <pid>: File exists`), because CRIU restores the original PID. Serving concurrent calls would need a PID namespace per restore.

For comparison, the current daemon's fork takeover costs 2.2 ms: CRIU is about 10 times slower to resume the state, but the rest of the run is identical. So it brings nothing to Python, nor to native binaries (wget starts in 2.6 ms, less than a restore). It becomes interesting where forking is not possible and initialisation costs far more than 20 ms: Node, JVM. Not measured yet: an end-to-end restore, injecting the client's argv, env and fds (`--inherit-fd`).

## Recommendation

1. **Finish Python first** (stage 2, level 2 as the priority). That is what makes the gain general: without it, pip only gains x1.2.
2. **Ruby next**: same architecture, gain of the same order, medium cost. Start with a stage 0 (baseline of `rubocop` and `bundle`).
3. **Node and JVM "cache only"** to start with: a modest gain, but safe and almost free for Node, trickier for the JVM. It must be said clearly that **x3 is probably out of reach there without a snapshot**: V8 snapshot, CRaC or CRIU.
4. **CRIU as an exploratory trial**, if we want to know what "any command" really costs.
5. **Native binaries excluded**: nothing to gain, and the client adds 0.7 ms. `speedrunner enroll` must refuse to enroll them.

This choice changes §2 of the brief (Node, JVM/CRaC and CRIU are out of scope for stage 1 there): to be decided before carrying it over.
