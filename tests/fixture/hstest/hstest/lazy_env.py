import os

# Frozen at import: a client with another value must run natively.
VALUE = os.environ.get("HSTEST_LAZY")
