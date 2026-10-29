# Starts a thread at load: must never be preloaded in the daemon.
Thread.new { sleep 0.05 }
