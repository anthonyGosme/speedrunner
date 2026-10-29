"""Fixture package with import-time side effects that speedrunner must preserve."""
import os
import warnings

# An env var set at import time: the daemon must re-apply it in each child.
os.environ.setdefault("HSTEST_IMPORTED", "1")

# Output at import time: natively printed on every run, so it must be replayed.
warnings.warn("hstest import-time warning", UserWarning)
