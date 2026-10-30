# Refused (prints at load). Defines an alias that lazy_reopen.rb, loaded
# later, reopens, as fastlane.rb and dotenv_helper.rb do.
warn "rbtest alias at load"
module Rbtest
  module Real; end
  Alias = Real
end
