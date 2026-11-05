"""A handler set at import, as a tool that cleans up on SIGTERM."""
import signal
import sys
import time


def _terminated(*_):
    print("clean up", flush=True)
    sys.exit(42)


signal.signal(signal.SIGTERM, _terminated)


def main():
    print("ready", flush=True)
    time.sleep(30)
