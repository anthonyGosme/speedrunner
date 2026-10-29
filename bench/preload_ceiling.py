"""Estimate the warm-path ceiling of fork-based preloading for awscli v1.

For each reference command, a preloaded parent forks children and times
fork -> child reaped, under one preload level:

  imports  the bin/aws imports only (awscli.clidriver)
  learned  + modules that learning runs imported lazily (e.g. awscli.handlers)
  memo     + JSON data files those runs loaded, pre-parsed in the parent

Children skip interpreter teardown (os._exit after flushing), as the daemon
would by reporting the exit code first. There is no client, socket or fd
passing, so this is an upper bound on the gain, not the product.

Usage: python preload_ceiling.py <level> <runs> <out.json>
"""
import gc
import importlib
import json
import os
import statistics
import sys
import time

COMMANDS = [
    ['--version'],
    ['configure', 'list'],
    ['ec2', 'run-instances', '--generate-cli-skeleton'],
]
WARMUP = 3


def rss_mb():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) / 1024


def run_cli(args):
    import awscli.clidriver

    driver = awscli.clidriver.create_clidriver()
    try:
        return driver.main(args)
    except SystemExit as e:
        # argparse exits from inside main() for --version.
        return e.code


def fork_child(body):
    """Run body() in a forked child with stdio on /dev/null; return (pid, rfd)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        result = body()
        sys.stdout.flush()
        sys.stderr.flush()
        os.write(w, json.dumps(result).encode())
        os._exit(0)
    os.close(w)
    return pid, r


def read_all(fd):
    chunks = []
    while chunk := os.read(fd, 65536):
        chunks.append(chunk)
    os.close(fd)
    return json.loads(b''.join(chunks))


def learn(args):
    """In a child, run the command and report lazily imported modules and loaded files."""
    from botocore import loaders

    def body():
        before = set(sys.modules)
        loaded = []
        original = loaders.JSONFileLoader._load_file

        def recording(self, full_path, open_method):
            data = original(self, full_path, open_method)
            if data is not None:
                loaded.append(full_path)
            return data

        loaders.JSONFileLoader._load_file = recording
        rc = run_cli(args)
        new_modules = [m for m in sys.modules if m not in before]
        return {'rc': rc, 'modules': new_modules, 'files': loaded}

    pid, r = fork_child(body)
    result = read_all(r)
    os.waitpid(pid, 0)
    return result


def open_method_for(path):
    from botocore import loaders

    for ext, method in sorted(loaders._JSON_OPEN_METHODS.items(), key=lambda kv: -len(kv[0])):
        if path.endswith(ext):
            return method
    raise ValueError(path)


def install_memo(files):
    """Pre-parse files in the parent; each child consumes an entry at most once."""
    from botocore import loaders

    original = loaders.JSONFileLoader._load_file
    memo = {}
    for path in files:
        st = os.stat(path)
        data = original(loaders.JSONFileLoader(), path, open_method_for(path))
        memo[path] = ((st.st_mtime_ns, st.st_size, st.st_ino), data)

    def memoized(self, full_path, open_method):
        entry = memo.pop(full_path, None)
        if entry is not None:
            try:
                st = os.stat(full_path)
            except OSError:
                st = None
            if st is not None and entry[0] == (st.st_mtime_ns, st.st_size, st.st_ino):
                return entry[1]
        return original(self, full_path, open_method)

    loaders.JSONFileLoader._load_file = memoized
    return len(memo)


def main():
    level, runs, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    # awscli aliases its vendored botocore as top-level `botocore`; the CLI
    # uses that name, and awscli.botocore.* would be separate module objects.
    import awscli.clidriver  # noqa: F401  (the bin/aws imports)

    info = {'level': level, 'runs': runs, 'rss_imports_mb': rss_mb()}
    if level in ('learned', 'memo'):
        modules, files = [], []
        for args in COMMANDS:
            learned = learn(args)
            modules += [m for m in learned['modules'] if m not in modules]
            files += [f for f in learned['files'] if f not in files]
        failed = []
        for name in modules:
            try:
                importlib.import_module(name)
            except Exception:
                failed.append(name)
        info.update(learned_modules=len(modules), failed_imports=failed)
        if level == 'memo':
            info['memo_files'] = install_memo(files)
    gc.freeze()
    info['rss_preloaded_mb'] = rss_mb()

    results = {}
    for args in COMMANDS:
        times, rcs = [], set()
        for i in range(WARMUP + runs):
            t0 = time.perf_counter()
            pid, r = fork_child(lambda: run_cli(args))
            rcs.add(read_all(r))
            os.waitpid(pid, 0)
            if i >= WARMUP:
                times.append(time.perf_counter() - t0)
        results[' '.join(args)] = {
            'median_s': statistics.median(times),
            'stdev_s': statistics.stdev(times),
            'rcs': sorted(rcs, key=str),
            'times': times,
        }
    info['results'] = results
    with open(out_path, 'w') as f:
        json.dump(info, f, indent=1)
    for cmd, r in results.items():
        print(f'{level:8} aws {cmd:45} median {r["median_s"] * 1000:6.1f} ms '
              f'± {r["stdev_s"] * 1000:4.1f}  rc={r["rcs"]}')
    print(f'{level:8} RSS parent: {info["rss_imports_mb"]:.0f} MB after imports, '
          f'{info["rss_preloaded_mb"]:.0f} MB preloaded')


if __name__ == '__main__':
    main()
