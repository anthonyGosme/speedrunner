"""Uses CoreFoundation at import on macOS (a run loop, through ctypes), as
an extension module might: no thread to show for it, yet a forked child of
the process that imported it is killed when it uses CoreFoundation in turn
(urllib's proxy lookup). The daemon must find out, and not keep it.
Nothing on other platforms."""
import ctypes
import sys

if sys.platform == "darwin":
    _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
    _cf.CFRunLoopGetCurrent()
