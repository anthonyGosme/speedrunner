class Finalized:
    def __del__(self):
        pass


# Leaves an object with a finalizer at import: must never be preloaded in the daemon.
KEEP = Finalized()
