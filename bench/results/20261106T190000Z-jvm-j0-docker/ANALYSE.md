# Go/no-go JVM (J0): what a stock JDK and a snapshot give on short CLIs

Machine: MacBook (Intel i7-9750H), Docker Desktop, a `linux/amd64` container, Temurin 25.0.5 and, for the ceiling only, Zulu 25.0.4.1 with CRaC (`machine.txt`). No network during the runs. Image, panel and scripts: [bench/jvm/](../../jvm/); versions: [versions.tsv](../../jvm/versions.tsv). No client and no daemon: these are ceilings for what speedrunner could do.

## Verdict: NO-GO for a JVM mode

The bar set beforehand: x3 on at least half of the panel with the cache alone for a go, about x2 for a limited go. On 49 commands from 33 tools:

| Technique | Valid commands | Median | Best | At x3 or more | At x2 or more |
|---|---:|---:|---:|---:|---:|
| Dynamic CDS archive (JDK 19 and later) | 31 | x1.23 | x2.07 | 0 | 1 |
| **AOT cache (JDK 24 and later)** | 44 | **x1.58** | x2.78 | **0** | 13 |
| AOT cache, plus `-XX:+UseSerialGC -XX:TieredStopAtLevel=1` | 41 | x1.88 | x3.28 | 3 | 19 |
| CRaC, classes initialised before the checkpoint (non-stock JDK) | 14 | x1.69 | x3.31 | 3 | 4 |

There is time to win: the median command takes 650 ms natively, and some take seconds (`pmd check` 4.2 s, `kotlinc -version` 3.1 s). But nothing short of a fork gets to it, and the JVM does not fork.

A limited go is not justified either: x1.58 is short of the x2 asked for, and a tool that promises x3 and more would have to present it as something else. The short-run flags reach x1.88 at a price: C1 only makes long runs slower.

**Worth redoing if** a stock JDK ships compiled code in its AOT cache: what stays slow here is code running in the interpreter (below).

## What the numbers say

- **The cache is reliable, and modest.** 44 of 49 commands keep the native output. The JDK's own tools gain most (median x2.31 for `javac`, `jar`, `jshell`, `javadoc`), build tools least (x1.24).
- **A snapshot does little better, and costs more.** Restoring takes 89 to 128 ms for a program that does nothing, against 45 ms to start a JVM with it. With only the classes loaded, the gain is x1.01 to x1.55.
- **Even warm, `javac` stays under x3.** Compiling before the checkpoint, the kindest case available: x0.94 with no compile, x2.74 with one, x1.86 with ten, x1.89 with fifty (image from 24 to 93 MB). Why more warm-up is slower here was not looked into; the size of the image is a guess. The x100 reported for Spring Boot is a long-lived server's initialisation, not a short CLI.
- **The fidelity would be hard too.** Here the arguments are baked into the checkpoint and the working directory never changes. The real thing would need new arguments, environment and working directory on restore, and from memory the JDK fixes `user.dir` at startup; this was not tested. It also needs a CRaC JDK, on Linux only.

## False gains to watch for

A failure is fast. These looked like the best results of the panel, and all of them are the JVM refusing to start, or a tool breaking:

- `jruby -e` x171 and `jruby --version` x13, with the AOT cache: JRuby's launcher passes options that exclude it (`Option AOTCache cannot be used…`).
- `jmeter --version` x34 and Kafka x4.7 to x5.5, with the short-run flags: they choose their own collector (`Multiple garbage collectors selected`).
- `gradle --version` x49 with CDS: an error during the dump.
- `clojure -e` x6.4 and `jython -c` x9.3, with initialised classes: an initialiser fails after the restore.

So no gain is counted here unless the exit code, stdout and stderr match the native run in all 11 runs, digits aside (clocks differ natively too). `gradle --version` and `jshell --version` also differ between native runs: their gains are shown, and their output could not be compared.

Injecting options through `JAVA_TOOL_OPTIONS` was only for this measure: it prints `Picked up…` on stderr, which is filtered here and would be a difference in a product. Five commands stay invalid with the AOT cache (JRuby, `zkServer`, SpotBugs, Spring), and CDS prints errors on many.

## Limits of this measure

- **The best case for the cache:** it is trained on the very command it then serves.
- **JDK 25 only.** On 17 or 21, still common, there is no AOT cache: CDS is all there is, x1.23 in this panel.
- **Left out for lack of disk:** Hadoop, Spark, Flink, Cassandra, Solr, sbt, Flyway. The ops tools measured gain no more than the others, but these were not measured.
- **11 runs per mode**, interleaved, and every time kept in `results.json`. No confidence intervals: the margins do not call for them.
- **This says nothing of market share:** only of the gain on this panel.
- `pmd` (x0.96) gains nothing, and why was not looked into.

## All commands

| Family | Command | Native | Dynamic CDS | AOT cache | AOT + short-run flags |
|---|---|---:|---:|---:|---:|
| build | `mvn --version` | 400 ms | n/a | x1.07 | x1.20 |
| build | `gradle --version` | 974 ms | n/a | x1.43 | x1.49 |
| build | `ant -version` | 154 ms | x1.08 | x1.27 | x1.44 |
| build | `ant -f Build.xml` | 625 ms | x1.08 | x1.21 | x1.43 |
| jdk | `javac --version` | 244 ms | n/a | x2.78 | **x3.28** |
| jdk | `javac Hello.java` | 874 ms | n/a | x2.24 | x2.51 |
| jdk | `java Hello.java` | 850 ms | n/a | x2.35 | x2.56 |
| jdk | `jar --version` | 132 ms | n/a | x1.67 | x2.01 |
| jdk | `keytool -help` | 210 ms | n/a | x1.46 | x1.66 |
| jdk | `jshell --version` | 214 ms | n/a | x2.52 | x2.95 |
| jdk | `javadoc --version` | 332 ms | n/a | x2.31 | x2.64 |
| lang | `kotlinc -version` | 3059 ms | n/a | x1.21 | x1.25 |
| lang | `groovy --version` | 1087 ms | x1.00 | x1.23 | x1.38 |
| lang | `groovy -e` | 2405 ms | x1.02 | x1.31 | x1.48 |
| lang | `scalac -version` | 1120 ms | x2.07 | x2.12 | x2.54 |
| lang | `jruby --version` | 386 ms | x1.02 | n/a | n/a |
| lang | `jruby -e` | 4512 ms | x1.05 | n/a | n/a |
| lang | `jython --version` | 166 ms | x1.08 | x1.25 | x1.33 |
| lang | `jython -c` | 2169 ms | x1.19 | x1.43 | x1.58 |
| lang | `clojure -e` | 1031 ms | x1.68 | x2.03 | x2.38 |
| lang | `jbang --version` | 611 ms | x1.90 | x2.52 | **x3.12** |
| ops | `kafka-topics --version` | 2538 ms | n/a | x1.54 | n/a |
| ops | `kafka-configs --help` | 2707 ms | n/a | x1.61 | n/a |
| ops | `zkServer version` | 109 ms | n/a | n/a | n/a |
| ops | `liquibase --version` | 1212 ms | x0.94 | x1.15 | x1.25 |
| ops | `trino --version` | 547 ms | x1.20 | x1.26 | x1.41 |
| ops | `sonar-scanner --version` | 650 ms | x1.71 | x2.15 | x2.61 |
| ops | `jmeter --version` | 1581 ms | n/a | x1.33 | n/a |
| quality | `checkstyle --version` | 429 ms | x1.06 | x1.22 | x1.35 |
| quality | `checkstyle Hello.java` | 1243 ms | x1.23 | x1.53 | x1.60 |
| quality | `pmd --version` | 986 ms | x1.00 | x0.96 | x1.13 |
| quality | `pmd check Hello.java` | 4219 ms | x1.01 | x0.96 | x0.90 |
| quality | `ktlint --version` | 654 ms | n/a | x1.91 | x2.13 |
| quality | `ktlint Sample.kt` | 1660 ms | n/a | x2.75 | **x3.07** |
| quality | `google-java-format --version` | 143 ms | x1.41 | x1.90 | x2.21 |
| quality | `detekt --version` | 335 ms | x1.30 | x1.49 | x1.57 |
| quality | `spotbugs -version` | 801 ms | n/a | n/a | n/a |
| quality | `jacoco version` | 212 ms | x1.10 | x1.29 | x1.21 |
| gen | `openapi-generator version` | 346 ms | x1.34 | x1.44 | x1.62 |
| gen | `openapi-generator validate` | 1108 ms | x1.83 | x2.27 | x2.70 |
| gen | `swagger-codegen version` | 1073 ms | x1.79 | x2.20 | x2.56 |
| gen | `plantuml -version` | 288 ms | x1.30 | x1.65 | x1.88 |
| gen | `plantuml sample.puml` | 847 ms | x1.72 | x2.03 | x2.38 |
| gen | `apktool --version` | 98 ms | x1.10 | x1.29 | x1.46 |
| gen | `antlr, usage` | 189 ms | x1.59 | x1.89 | x2.08 |
| gen | `closure-compiler --version` | 435 ms | x1.54 | x1.70 | x1.97 |
| gen | `bfg --version` | 473 ms | x1.90 | x1.97 | x2.25 |
| gen | `spring --version` | 483 ms | n/a | n/a | n/a |
| gen | `quarkus --version` | 1741 ms | x1.48 | x1.71 | x2.09 |

n/a: the output differs from the native run, usually because the JVM refused to start. Gains of x3 or more are in bold.

## The ceiling, with CRaC

| Command | Native | Classes loaded | Classes initialised | Same output (of 11) |
|---|---:|---:|---:|---:|
| `(restore floor) Noop` | 45 ms | 128 ms, x0.35 | 89 ms, x0.50 | 11 |
| `checkstyle --version` | 498 ms | 421 ms, x1.18 | 308 ms, x1.62 | 11 |
| `checkstyle Hello.java` | 1474 ms | 1460 ms, x1.01 | 1125 ms, x1.31 | 11 |
| `openapi-generator version` | 377 ms | 328 ms, x1.15 | 203 ms, x1.86 | 11 |
| `openapi-generator validate` | 1246 ms | 961 ms, x1.30 | 376 ms, x3.31 | 11 |
| `swagger-codegen version` | 1167 ms | 867 ms, x1.35 | 374 ms, x3.12 | 11 |
| `plantuml -version` | 309 ms | 275 ms, x1.13 | 150 ms, x2.06 | 11 |
| `plantuml sample.puml` | 950 ms | 762 ms, x1.25 | 498 ms, x1.91 | 11 |
| `closure-compiler --version` | 465 ms | 363 ms, x1.28 | 266 ms, x1.75 | 11 |
| `bfg --version` | 504 ms | 325 ms, x1.55 | 152 ms, x3.31 | 11 |
| `detekt --version` | 316 ms | 255 ms, x1.24 | 237 ms, x1.33 | 11 |
| `quarkus --version` | 1819 ms | 1539 ms, x1.18 | 1119 ms, x1.63 | 11 |
| `trino --version` | 607 ms | 466 ms, x1.30 | 385 ms, x1.58 | 11 |
| `clojure -e` | 1042 ms | 846 ms, x1.23 | 162 ms, n/a | 0 |
| `jython -c` | 2144 ms | 1922 ms, x1.12 | 230 ms, n/a | 0 |
| `javac Hello.java` | 795 ms | 787 ms, x1.01 | 677 ms, x1.18 | 11 |
| `pmd check Hello.java` | 4514 ms | 3954 ms, x1.14 | 3682 ms, x1.23 | 11 |

Restoring with Azul's `warp` engine, no privileges needed. The first line is the restore's own cost. Also in `ceiling.log`; `javac` warmed up in `warm-javac.log`.

## To redo it

```bash
docker build -t speedrunner-jvm-j0 bench/jvm
docker volume create speedrunner-jvm-tools
docker run --rm -v $PWD/bench/jvm:/j0:ro -v speedrunner-jvm-tools:/tools speedrunner-jvm-j0 python3 -I /j0/install.py
docker run --rm --network none -v $PWD/bench/jvm:/j0:ro -v speedrunner-jvm-tools:/tools -v $PWD/out:/out \
  --tmpfs /cache:size=4g speedrunner-jvm-j0 sh -c 'echo "127.0.0.1 $(hostname)" >> /etc/hosts; python3 -I /j0/measure.py measure /out 11'
```

The tools take 1.8 GB. `J0_CHECK=1` checks that the versions still resolve: 32 of 33 did; Kafka's archive answered `Network is unreachable` from the container, which it also did once during the first install, before it worked.
