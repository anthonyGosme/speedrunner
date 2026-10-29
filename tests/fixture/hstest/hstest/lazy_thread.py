import threading
import time

# Starts a thread at import: must never be preloaded in the daemon.
threading.Thread(target=time.sleep, args=(0.05,), daemon=True).start()
