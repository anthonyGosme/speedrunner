# Fixture library with load-time side effects that speedrunner must preserve.

# An env var set at load time: the daemon must re-apply it in each child.
ENV["RBTEST_IMPORTED"] ||= "1"

# Output at load time: natively printed on every run, so it must be replayed.
warn "rbtest load-time warning"

module Rbtest
  # Read once at load time: a client with another value must run natively.
  FROZEN = ENV["RBTEST_FROZEN"]
end
