# The system's proxy settings, as _scproxy reads them for Python: on macOS
# through SystemConfiguration and CoreFoundation, from Objective-C classes
# a forked child of a Ruby process (which has threads) may not use unless
# its daemon allowed it. True when found, as natively.
module Rbtest
  module Proxies
    def self.found?
      return true unless RUBY_PLATFORM.include?("darwin")

      require "fiddle"
      sc = Fiddle.dlopen("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration")
      !Fiddle::Function.new(sc["SCDynamicStoreCopyProxies"], [Fiddle::TYPE_VOIDP], Fiddle::TYPE_VOIDP)
                       .call(nil).null?
    end
  end
end
