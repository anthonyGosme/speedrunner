module Rbtest
  module LazyEnv
    # Frozen at load: a client with another value must run natively.
    VALUE = ENV["RBTEST_LAZY"]
  end
end
