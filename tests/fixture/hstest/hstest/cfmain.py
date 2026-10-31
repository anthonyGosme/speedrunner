"""An entry module that uses CoreFoundation as it loads (level 1): the
daemon must skip it, and still serve the tool, which then uses
CoreFoundation itself, in the child."""
from hstest import cf  # noqa: F401


def main():
    import urllib.request
    print("cf main", isinstance(urllib.request.getproxies(), dict))
    return 0
