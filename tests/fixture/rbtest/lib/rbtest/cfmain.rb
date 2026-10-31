# An executable's file that uses CoreFoundation as it loads (level 1): the
# daemon must skip it, and still serve the tool, which then uses it itself,
# in the child.
require "rbtest/cf"
require "rbtest/proxies"
puts "cf main #{Rbtest::Proxies.found?}"
