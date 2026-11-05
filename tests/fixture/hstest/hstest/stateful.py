"""State loaded at import and saved at exit, as a tool with a history file."""
import atexit
import os
import sys

PATH = os.path.expanduser("~/hsstate")


def _load():
    try:
        with open(PATH) as f:
            return f.read()
    except OSError:
        return ""


STATE = _load()


def _save():
    with open(PATH, "w") as f:
        f.write(STATE)


atexit.register(_save)


def main():
    global STATE
    STATE = sys.argv[1]
