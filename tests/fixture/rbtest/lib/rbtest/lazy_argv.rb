module Rbtest
  module LazyArgv
    # Reads the command line at load: a preload would freeze the daemon's.
    VALUE = ARGV.dup
  end
end
