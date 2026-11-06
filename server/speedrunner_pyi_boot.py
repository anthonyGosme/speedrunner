"""First code to run under speedrunner_pyi: what a PyInstaller bootloader does once libpython is
up, on the bundle's own modules, up to the tool's script. Then either the daemon or that script."""
import marshal
import os
import sys
COOKIE = b"MEI\014\013\012\013\016"


def read_archive(exe):
    """(start of the package, its table: offset, size, full size, compressed, kind, name)."""
    with open(exe, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 8192))
        tail = f.read()
        at = tail.rfind(COOKIE)
        if at < 0:
            raise ValueError("no archive")
        cookie = tail[at:at + 88]
        pkg_len, toc_off, toc_len = (int.from_bytes(cookie[8 + 4 * i:12 + 4 * i], "big") for i in range(3))
        start = size - len(tail) + at + 88 - pkg_len
        f.seek(start + toc_off)
        raw = f.read(toc_len)
    table, p = [], 0
    while p < toc_len:
        ints = [int.from_bytes(raw[p + 4 * i:p + 4 + 4 * i], "big") for i in range(4)]
        kind = raw[p + 17:p + 18]
        name = raw[p + 18:p + ints[0]].rstrip(b"\0").decode()
        table.append((ints[1], ints[2], ints[3], raw[p + 16], kind, name))
        p += ints[0]
    return start, table


def read_entry(exe, start, entry):
    with open(exe, "rb") as f:
        f.seek(start + entry[0])
        data = f.read(entry[1])
    if entry[3]:
        import zlib
        data = zlib.decompress(data)
    return data


def boot(exe):
    """Returns the tool's script, a code object, with all before it run."""
    home = os.path.dirname(exe)
    sys.frozen = True
    sys._MEIPASS = home
    sys.executable = exe
    sys.prefix = sys.exec_prefix = sys.base_prefix = sys.base_exec_prefix = home
    version = "python%d.%d" % sys.version_info[:2]
    sys.path[:] = [os.path.join(home, "base_library.zip"), os.path.join(home, version, "lib-dynload"), home]
    start, table = read_archive(exe)
    pyz = next(e for e in table if e[4] == b"z")
    sys._pyinstaller_pyz = "%s?%d" % (exe, start + pyz[0])
    for entry in table:  # as the bootloader: importers and the like, as modules
        if entry[4] == b"m":
            code = marshal.loads(read_entry(exe, start, entry))
            module = type(sys)(entry[5])
            module.__file__ = os.path.join(home, entry[5] + ".pyc")
            sys.modules[entry[5]] = module
            exec(code, module.__dict__)
    scripts = [e for e in table if e[4] == b"s"]
    main = sys.modules["__main__"]
    main.__dict__.pop("__file__", None)
    for entry in scripts[:-1]:  # the first of them installs the importer
        exec(marshal.loads(read_entry(exe, start, entry)), main.__dict__)
    last = scripts[-1]
    return last[5], marshal.loads(read_entry(exe, start, last))


def scrub_env():
    """What only this start needs: the tool's env has what the bootloader leaves, and no more."""
    for key in ("_SR_BUNDLE_STARTED", "_SR_BUNDLE_EXE", "PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(key, None)


if __name__ == "__main__":
    bundle = os.environ["_SR_BUNDLE_EXE"]
    scrub_env()
    sys._speedrunner_pyi = boot(bundle)
    daemon = sys.argv[1]
    sys.argv = sys.argv[1:]
    main_globals = sys.modules["__main__"].__dict__
    main_globals["__file__"] = daemon
    with open(daemon, "rb") as f:
        source = f.read()
    exec(compile(source, daemon, "exec"), main_globals)
