# The panel's 4%, and what a Ruby child collects for its parent

The panel of `20261104T030000Z-import-reads-vps` lost 4% per command, against an
older panel. Measured directly here (`bench/daemon_ab.py`, VPS): versions of the
daemon in turn through one client, 40 to 60 rounds, a native run before each served
one. `main` is the code before the three changes, `p17` is with all three.

## How far to trust a difference

Two identical copies of a version (`aa-control-*.json`):

| Command | Identical copies differ by |
|---|---:|
| `bundle --version` | 0.1 ms |
| `pip list` | 0.2 ms |
| `poetry --version` | up to 2.2 ms |
| `solargraph --version` | up to 5 ms |

So the panel's largest drops, poetry and solargraph, are within what the method
produces: `p17` is level with or ahead of `main` on both here. The full panel was not
run again, and x4.84 against x5.06 are not re-evaluated.

## Python: little, and no one piece

`python-pieces.json`, `p17` against `main`: black +0.25 ms, pre-commit +0.6 ms, pip
list +0.3 to +1.4 ms, aws -0.1 ms. Without the audit hook, without the R and E
messages, or without the files followed, the differences stay about the same.

## Ruby: the child collects for the parent

`bundle --version` showed runs of 19 slow calls, at the same calls in two copies of a
version. Counting collections in the child: slow calls are exactly those where it
collected. `gc_by: newobj`, about 1,500 free slots at the fork; +3 ms for a minor
collection, +8 ms for a major one. The parent allocates a hundred objects per call and
reaches no threshold itself, so each child collects, until one parent allocation does.
`main` has this too, later and smaller: the three changes only moved it.

The parent now collects just after a fork, by what it allocated since its last
collection (`heap_free_slots` adds up all slot sizes, and the smallest fill first: a
first version that looked at it still left 16 collections in 150 calls, against 26).

`ruby-collection.json`, median ms, 60 rounds, `gcafter` is the change:

| Command | main | p17 | gcafter | gcafter - main, paired (95% CI) |
|---|---:|---:|---:|---:|
| `bundle --version` | 7.5 | 10.7 | 7.8 | +0.15 [-0.10, +0.85] |
| `rake --version` | 8.0 | 8.1 | 7.5 | -0.56 [-0.93, -0.28] |
| `rdoc --version` | 11.2 | 12.8 | 12.7 | +1.51 [+1.24, +1.96] |
| `gem list` | 39.9 | 37.0 | 37.3 | -2.33 [-3.73, -0.91] |
| `rubocop --version` | 99.3 | 104.0 | 100.2 | +1.05 [-1.40, +2.44] |
| `solargraph --version` | 65.6 | 60.6 | 57.8 | -7.39 [-8.80, -6.07] |

rdoc keeps +1.5 ms: not explained here. The `main` column of gem list and solargraph
stands above `p17` in every run, for no cause found, and may overstate those gains.

## The first call after a cold start

From `prewarm` to the first call served, 25 rounds, in a fixed order, median ms:

| Command | main | p17 | gcafter |
|---|---:|---:|---:|
| `bundle --version` | 117 | 122 | 123 |
| `rubocop --version` | 453 | 477 | 498 |
| `solargraph --version` | 673 | 724 | 643 |

Collecting before the fork would make the first spare wait, hence after it. These
numbers cannot tell more: solargraph's `main` was 568 in another series of 20. The
first spare still comes out before the first collection, and may collect as today.

## Not covered

Node's collector, PHP and Apple Silicon were not measured. Memory neither.

## From an eighth to a sixteenth

The release run failed on Fedora's Ruby 3.4.8: with the parent collecting once it had
allocated an eighth of what was free, 48 children in 150 still collected. The official
image's 3.4.12 passed. Slots of the smallest objects are not published, and they run out
before the total says so. A sixteenth passes on Fedora (4 runs of 4), Ruby 3.1 to 3.4,
Ubuntu 22.04 and Alpine.

`threshold-1-16.json`, 50 rounds, a neutral version first, median ms, a sixteenth against
an eighth, paired (95% CI): bundle +0.30 [-0.21, +0.85], rake +0.10 [-0.48, +0.73], rdoc
-0.19 [-0.86, +0.37], gem list -0.28 [-1.45, +1.87], rubocop -1.92 [-5.26, +0.89], rails
-0.01 [-1.18, +0.69], solargraph +0.28 [-1.32, +2.34]. Geometric mean of served time:
+0.6%. Nothing measurable. A sixteenth is what passed, not a value derived: another Ruby
build may need less.
