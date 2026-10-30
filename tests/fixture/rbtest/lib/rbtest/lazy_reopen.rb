# Natively loaded after lazy_alias.rb: reopens Rbtest::Real through its
# alias. Preloaded alone, it would make a Rbtest::Alias of its own.
module Rbtest
  module Alias
    REOPENED = true
  end
end
