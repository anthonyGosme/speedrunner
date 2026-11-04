"""Leaves an object with a __del__ at import, and a temporary file whose
__del__ removes it. Preloaded, every child would finalize the same file."""
import os
import sys
import tempfile


class Stamp:
    path = None

    def __del__(self, open=os.open, close=os.close, flags=os.O_CREAT | os.O_WRONLY):
        if self.path:
            close(open(self.path, flags))


STAMP = Stamp()
TMP = tempfile.NamedTemporaryFile()


def main():
    STAMP.path = sys.argv[1]
    print("temporary file there" if os.path.exists(TMP.name) else "temporary file gone")
    return 0
