# aws v2 served: from a go/no-go to the real client

Machine: MacBook (Intel i7-9750H), Docker Desktop, a throwaway `linux/amd64` Debian 12 container (`machine.txt`). AWS CLI 2.39.5, installed from AWS's own zip, on its own Python 3.14.8. No network: requests go to a local server that answers anything. Protocol: [bench/aws_v2/](../../aws_v2/).

## Result: served

The numbers to quote are those of the [VPS](../20261106T220000Z-aws-v1-v2-vps/ANALYSE.md), x4.6 to x9.7, beside v1. Here, in a container on a laptop, everything is about twice as slow, and the ratios higher.

The real client, the built-in daemon, 21 interleaved runs, medians (`served.log`):

| Command | Native | speedrunner | Saved per call | Speedup |
|---|---:|---:|---:|---:|
| `aws --version` | 895 ms | 74 ms | 821 ms | **x12.0** |
| `aws nosuchcommand` | 875 ms | 75 ms | 800 ms | **x11.7** |
| `aws configure list` | 970 ms | 102 ms | 868 ms | **x9.5** |
| `aws s3 ls` | 1157 ms | 199 ms | 958 ms | **x5.8** |
| `aws sts get-caller-identity` | 1140 ms | 213 ms | 927 ms | **x5.3** |
| `aws s3api list-buckets` | 1130 ms | 223 ms | 907 ms | **x5.1** |
| `aws ec2 run-instances --generate-cli-skeleton` | 1156 ms | 271 ms | 885 ms | **x4.3** |
| `aws s3 cp`, a small file | 1275 ms | 302 ms | 973 ms | **x4.2** |

Stdout, stderr and the exit code match the native run on all of them, but for `aws s3 cp`, whose bytes vary between any two runs, natively too. It prints its speed, then blanks that line with spaces, as many as the speed took characters: under 10 MiB/s, one less. Over 60 native runs, the 3 under 10 MiB/s took 33 spaces and the 57 others 34. Served, it takes the same two forms: 4 of 30 runs the shorter one, against 3 of 30 natively. So that command is compared without the speed and those spaces. A real link adds the same time to both columns, so the ratio drops and the time saved stays.

**Memory and the first call.** The daemon takes 51 MB of PSS (89 MB of RSS) with 12 data files cached, its parent alone. With its spare, as the panel counts it, 90 to 145 MB on the VPS, against 43 to 119 MB for v1. The first call runs natively (981 ms), and the daemon is ready about 100 ms after it returns.

**Not measured:** a real network, aarch64, a system with a glibc older than the container's. Nothing else built with PyInstaller is served.

## What it took, beyond the prototype

- **A launcher** ([speedrunner_pyi.c](../../../server/speedrunner_pyi.c)): the bundle has no `python`, only `libpython`. Built on Ubuntu 20.04, it asks for no glibc symbol newer than 2.4.
- **PyInstaller's own start**, run by [speedrunner_pyi_boot.py](../../../server/speedrunner_pyi_boot.py): its importer and runtime hooks, in its order, up to the tool's script.
- **The env the bootloader leaves** (`LD_LIBRARY_PATH`, `_PYI_*`), compared whole through an aws alias that runs `env`. It first differed from what I assumed.
- **`AwsLogger`, the CRT's logging thread:** held back in the parent, started in each child.
- **`multiprocessing.resource_tracker`:** its `__del__` does nothing until it starts, yet refused it and, in a chain, 27 modules with all of `s3`. `aws s3 ls` went from x2.2 to x3.2.
- **Level 3 for aws v2's botocore**, whose method is `load_file`. It also killed the daemon at first: an adapter that does not recognise what it wraps now goes without. `s3api` went from x2.8 to x5.1.
- **Two walks of the heap at each exit**, in Python: 118 ms on 35,000 objects. In C now. aws v1 gains nothing from this on a Mac (159 ms either way).

## The go/no-go before it: a ceiling

A prototype with no client, forking from one parent, on a Python borrowed from the image (`ceiling.log`). It said go, under four conditions; the first three are met above, the fourth stands.

## How it works

aws v2 is a PyInstaller bundle in one directory: a bootloader with its modules appended (`dist/aws`, 1,272 modules), `libpython3.14.so.1.0` beside it, and data files. The prototype reads the appended archive, imports `awscli.clidriver` and what the commands learned (77 more), then forks. It has to look modules up on path entries as PyInstaller does: `botocore` is an alias of `awscli.botocore`.

## The conditions

1. **The native thread at the fork: solved here, by an adapter.** It is `AwsLogger`, the CRT's logging thread, started by `init_logging` as `awscli.logger` loads. The prototype holds that call back in the parent and makes it in each child, so that the parent has no second thread at the fork, as our daemons require. The gains did not change. `--debug` works (x7.8), its output not compared: it holds clocks. This depends on how awscli calls the CRT, so a daemon must keep checking the thread count and refuse if it is not 1.
2. **Running on aws's own Python.** The prototype borrows a Python 3.14 from the image, and the bundle has only a shared library: a user may have no matching `python`. It would take a small launcher that loads `libpython` from the bundle, as the Node daemon has an addon, and the daemon must get by with the standard library the bundle holds. Not built.
3. **The rest of the matrix.** Three kinds of command is not fidelity: terminal, signals, prompts, pagers, `sys.executable`, and the runtime hooks the bootloader runs, which the prototype skips.
4. **Only Linux x86_64 was measured.** macOS's package and aarch64 are not.

## To redo it

The ceiling:

```bash
docker run --rm --entrypoint bash -v $PWD/bench/aws_v2:/c:ro speedrunner-test-py3.14-linux-amd64 /c/run.sh
```

That image comes from `docker/`; any image with Python 3.14 will do. The real client: [served.py](../../aws_v2/served.py), with a client at `/aws/target/release/speedrunner` and aws v2 at `/aws/bin/aws`. The zip is AWS's latest, so a new version may change the numbers or the bundle's layout. The regression suite is `tests/test_aws_v2.py`.
