"""Prints more at import than a pty holds."""
print("x" * 199, flush=True)
for _ in range(1000):
    print("x" * 199)


def main():
    print("done")
