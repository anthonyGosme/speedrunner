# Starts a thread when required, as sshkit's connection pool does: the
# daemon must not keep it, and still serve the tool.
Thread.new { sleep 0.05 }
puts "threaded"
