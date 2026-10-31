import os

# Frozen at import: a client with another value must run natively.
VALUE = os.environ.get("HSTEST_LAZY")
# Read at import too, as pre-commit does: speedrunner's own link directories
# (`speedrunner exec`, `activate`) at the head of PATH must not count.
SEARCH_PATH = os.environ.get("PATH")
