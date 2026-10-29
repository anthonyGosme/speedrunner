"""Notes at import whether its streams are terminals, as httpie's Environment
and ansible's display do: the daemon's state must not stand for the client's."""
import os
import sys

FROZEN = (sys.stdin.isatty(), sys.stdout.isatty(), os.isatty(2))
# As ansible checks: streams must be blocking, as a terminal is.
BLOCKING = all(os.get_blocking(fd) for fd in (0, 1, 2))


def reconfigure(stream):
    # As ansible does at import: on a terminal or a pipe, this works only if
    # the stream was made for what its fd is.
    try:
        stream.reconfigure(errors="surrogateescape")
        return "ok"
    except OSError as exc:
        return f"failed: {exc}"


RECONFIGURED = reconfigure(sys.stdout)


def main():
    live = (sys.stdin.isatty(), sys.stdout.isatty(), os.isatty(2))
    print("frozen", FROZEN, "blocking", BLOCKING, "reconfigure", RECONFIGURED, reconfigure(sys.stderr),
          "live", live)
    return 0
