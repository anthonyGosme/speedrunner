"""Starts a thread when imported, as sshkit's connection pool does: the
daemon must not keep it, and still serve the tool."""
import threading
import time

threading.Thread(target=time.sleep, args=(0.05,), daemon=True).start()


def main():
    print("threaded")
    return 0
