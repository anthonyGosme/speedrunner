"""Looks up the system's proxies at import: on macOS, through CoreFoundation
(_scproxy), which also starts threads."""
import urllib.request

PROXIES = urllib.request.getproxies()
