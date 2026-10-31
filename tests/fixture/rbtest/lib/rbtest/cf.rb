# Uses CoreFoundation at load on macOS (a run loop, through Fiddle), as a
# native extension might: no thread to show for it, yet a forked child of
# the process that loaded it is killed when it uses CoreFoundation in turn.
# The daemon must find out, and not keep it. Nothing on other platforms.
if RUBY_PLATFORM.include?("darwin")
  require "fiddle"
  cf = Fiddle.dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
  Fiddle::Function.new(cf["CFRunLoopGetCurrent"], [], Fiddle::TYPE_VOIDP).call
end
