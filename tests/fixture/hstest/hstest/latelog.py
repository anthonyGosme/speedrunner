"""Opens a file at import and writes to it during the run, never closing it:
native teardown flushes it. Nothing here counts as a finalizer."""
import os
import sys

LOG = open(os.environ["HSLOG_FILE"], "a")


def main():
    LOG.write(sys.argv[1] + "\n")
    print("ok")
    return 0
