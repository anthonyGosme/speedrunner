"""Runs the program when imported, as rails/cli does: never to be preloaded."""
import sys

print("autorun:", sys.argv[1:])


def main():
    return 0
