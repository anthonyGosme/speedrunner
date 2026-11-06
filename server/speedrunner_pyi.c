/* Runs Python from a PyInstaller bundle in one directory, as its bootloader does, but
 * our script instead of the tool's. The bundle has no python executable, only a shared
 * libpython, and this is all that needs compiling.
 *
 *   speedrunner_pyi <the bundle's executable> <script> [args...]
 *
 * The bootloader sets LD_LIBRARY_PATH and execs itself, so that libpython finds the
 * bundle's libraries: the same here. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define COOKIE "MEI\014\013\012\013\016"
#define COOKIE_LEN 8
#define TAIL 8192 /* the cookie is near the end: the bundle's own bootloader looks there too */

static int fail(const char *what, const char *detail) {
    fprintf(stderr, "speedrunner_pyi: %s: %s\n", what, detail);
    return 127;
}

/* The Python version (314) and the library's name, from the archive's cookie. */
static int read_cookie(const char *exe, int *pyver, char *pylib, size_t room) {
    int fd = open(exe, O_RDONLY);
    if (fd < 0) return -1;
    struct stat st;
    unsigned char buf[TAIL];
    if (fstat(fd, &st) != 0) { close(fd); return -1; }
    off_t from = st.st_size > TAIL ? st.st_size - TAIL : 0;
    ssize_t got = pread(fd, buf, sizeof buf, from);
    close(fd);
    for (ssize_t i = got - 88; i >= 0; i--) {
        if (memcmp(buf + i, COOKIE, COOKIE_LEN) == 0) {
            const unsigned char *v = buf + i + 20;
            *pyver = (int)((uint32_t)v[0] << 24 | (uint32_t)v[1] << 16 | (uint32_t)v[2] << 8 | v[3]);
            snprintf(pylib, room, "%.64s", (const char *)buf + i + 24);
            return 0;
        }
    }
    return -1;
}

int main(int argc, char **argv) {
    if (argc < 3) return fail("usage", "speedrunner_pyi <executable> <script> [args...]");
    char exe[PATH_MAX], home[PATH_MAX], pylib[65], lib[PATH_MAX + 70], path[3 * PATH_MAX + 64];
    int pyver;
    if (!realpath(argv[1], exe)) return fail("not found", argv[1]);
    if (read_cookie(exe, &pyver, pylib, sizeof pylib) != 0) return fail("not a PyInstaller bundle", exe);
    snprintf(home, sizeof home, "%s", exe);
    dirname(home);
    if (!getenv("_SR_BUNDLE_STARTED")) {
        const char *orig = getenv("LD_LIBRARY_PATH");
        if (orig) setenv("LD_LIBRARY_PATH_ORIG", orig, 1);
        snprintf(path, sizeof path, "%s%s%s", home, orig ? ":" : "", orig ? orig : "");
        setenv("LD_LIBRARY_PATH", path, 1);
        setenv("_SR_BUNDLE_STARTED", "1", 1);
        execv("/proc/self/exe", argv);
        return fail("exec", "/proc/self/exe");
    }
    snprintf(lib, sizeof lib, "%s/%s", home, pylib);
    void *py = dlopen(lib, RTLD_NOW | RTLD_GLOBAL);
    if (!py) return fail("cannot load", dlerror());
    int (*bytes_main)(int, char **) = (int (*)(int, char **))dlsym(py, "Py_BytesMain");
    if (!bytes_main) return fail("no Py_BytesMain in", lib);
    setenv("PYTHONHOME", home, 1);
    snprintf(path, sizeof path, "%s/base_library.zip:%s/python%d.%d/lib-dynload:%s", home, home, pyver / 100, pyver % 100, home);
    setenv("PYTHONPATH", path, 1);
    setenv("_SR_BUNDLE_EXE", exe, 1);
    char **args = calloc((size_t)argc + 3, sizeof *args);
    if (!args) return 127;
    int n = 0;
    args[n++] = argv[1];
    args[n++] = "-S"; /* no site: the bundle has none */
    args[n++] = "-B";
    for (int i = 2; i < argc; i++) args[n++] = argv[i];
    return bytes_main(n, args);
}
