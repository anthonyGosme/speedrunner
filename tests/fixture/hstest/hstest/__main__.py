"""`python -m hstest`: the hstest command, as a launcher execs it (pre-commit's
git hook execs `python -mpre_commit`). With HSTEST_RUN_LOG, one line there
per run, to count them."""
import os
import sys

from hstest.cli import main

if os.environ.get("HSTEST_RUN_LOG"):
    with open(os.environ["HSTEST_RUN_LOG"], "a") as f:
        f.write("tool\n")
sys.exit(main())
