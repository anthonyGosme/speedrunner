"""macOS (M1): which uses of CoreFoundation in a parent make its forked
children die, whether the parent's native threads show it, and which call,
made in a throwaway child, detects it (the daemon's canary).

For each parent-side use, a fresh parent: its native threads (proc_pidinfo),
then 5 children per canary, killed or not.

Usage: python cfcanary.py
"""
import ctypes
import os
import socket
import struct

CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
libc = ctypes.CDLL(None)


def native_threads():
    info = ctypes.create_string_buffer(96)  # struct proc_taskinfo, PROC_PIDTASKINFO
    libc.proc_pidinfo(os.getpid(), 4, ctypes.c_uint64(0), info, 96)
    return struct.unpack_from("<i", info, 84)[0]


def cf(name, restype=ctypes.c_void_p):
    fn = getattr(ctypes.CDLL(CF_PATH), name)
    fn.restype = restype
    return fn


def getproxies():
    import urllib.request
    urllib.request.getproxies()


def proxy_settings():
    import _scproxy
    _scproxy._get_proxy_settings()


PARENT = {
    "nothing": lambda: None,
    "import urllib.request (loads _scproxy)": lambda: __import__("urllib.request"),
    "urllib getproxies()": getproxies,
    "_scproxy._get_proxy_settings()": proxy_settings,
    "platform.mac_ver()": lambda: __import__("platform").mac_ver(),
    "socket.getaddrinfo(localhost)": lambda: socket.getaddrinfo("localhost", 80),
    "ssl.create_default_context()": lambda: __import__("ssl").create_default_context(),
    "ctypes CFStringCreateWithCString": lambda: cf("CFStringCreateWithCString")(None, b"x", 0x08000100),
    "ctypes CFRunLoopGetCurrent": lambda: cf("CFRunLoopGetCurrent")(),
}
CANARY = {
    "_get_proxy_settings": proxy_settings,
    "CFRunLoopGetCurrent": lambda: cf("CFRunLoopGetCurrent")(),
    "CFStringCreate": lambda: cf("CFStringCreateWithCString")(None, b"x", 0x08000100),
    "CFBundleGetMainBundle": lambda: cf("CFBundleGetMainBundle")(),
}


def killed(fn, n=5):
    bad = 0
    for _ in range(n):
        pid = os.fork()
        if pid == 0:
            try:
                null = os.open(os.devnull, os.O_RDWR)
                os.dup2(null, 2)  # "The process has forked and you cannot use..."
                fn()
            finally:
                os._exit(0)
        bad += os.waitpid(pid, 0)[1] != 0
    return bad


for name, use in PARENT.items():
    pid = os.fork()
    if pid == 0:
        try:
            use()
            found = ", ".join(f"{c} {killed(f)}/5" for c, f in CANARY.items())
            print(f"{name:40} threads={native_threads()}  children killed: {found}", flush=True)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
