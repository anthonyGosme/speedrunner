# A handler set as it loads, as a tool that cleans up on SIGTERM.
trap("TERM") do
  puts "clean up"
  $stdout.flush
  exit!(42)
end
