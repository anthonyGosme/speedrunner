// The system calls speedrunner_daemon.mjs needs and Node does not expose:
// fork, fd passing, dup2, poll, signals, inotify, a pty. A Node-API addon,
// loaded by the daemon only.
//
// The Node-API declarations it uses are below, so that it builds with a C
// compiler alone, without Node's headers. Node-API's ABI is stable across
// Node versions: one build serves every Node of its libc and architecture.
//
// Errors come back as negative errno values, never as exceptions.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <pthread.h>
#include <sys/file.h>
#include <sys/inotify.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <termios.h>
#include <sys/ioctl.h>
#include <unistd.h>

// --- Node-API, as declared by node_api.h --------------------------------------
typedef struct napi_env__ *napi_env;
typedef struct napi_value__ *napi_value;
typedef struct napi_callback_info__ *napi_callback_info;
typedef int napi_status;
typedef napi_value (*napi_callback)(napi_env, napi_callback_info);
typedef struct {
  const char *utf8name;
  napi_value name;
  napi_callback method, getter, setter;
  napi_value value;
  int attributes;
  void *data;
} napi_property_descriptor;
struct uv_loop_s;
napi_status napi_get_cb_info(napi_env, napi_callback_info, size_t *, napi_value *, napi_value *, void **);
napi_status napi_get_value_int32(napi_env, napi_value, int32_t *);
napi_status napi_get_value_string_utf8(napi_env, napi_value, char *, size_t, size_t *);
napi_status napi_get_buffer_info(napi_env, napi_value, void **, size_t *);
napi_status napi_get_array_length(napi_env, napi_value, uint32_t *);
napi_status napi_get_element(napi_env, napi_value, uint32_t, napi_value *);
napi_status napi_create_int32(napi_env, int32_t, napi_value *);
napi_status napi_create_buffer_copy(napi_env, size_t, const void *, void **, napi_value *);
napi_status napi_create_array_with_length(napi_env, size_t, napi_value *);
napi_status napi_set_element(napi_env, napi_value, uint32_t, napi_value);
napi_status napi_define_properties(napi_env, napi_value, size_t, const napi_property_descriptor *);
napi_status napi_get_uv_event_loop(napi_env, struct uv_loop_s **);
napi_status napi_add_env_cleanup_hook(napi_env, void (*)(void *), void *);
int uv_loop_fork(struct uv_loop_s *);

// --- helpers ------------------------------------------------------------------
#define MAX_ARGS 4
typedef struct {
  napi_env env;
  size_t argc;
  napi_value argv[MAX_ARGS];
} args_t;

static args_t args(napi_env env, napi_callback_info info) {
  args_t a = {env, MAX_ARGS, {0}};
  napi_get_cb_info(env, info, &a.argc, a.argv, NULL, NULL);
  return a;
}

static int32_t int_arg(args_t *a, size_t i) {
  int32_t n = 0;
  if (i < a->argc) napi_get_value_int32(a->env, a->argv[i], &n);
  return n;
}

// A path argument, at most PATH_MAX bytes; "" if absent.
static void str_arg(args_t *a, size_t i, char *buf, size_t size) {
  size_t len = 0;
  buf[0] = 0;
  if (i < a->argc) napi_get_value_string_utf8(a->env, a->argv[i], buf, size, &len);
}

static napi_value num(napi_env env, int32_t n) {
  napi_value v;
  napi_create_int32(env, n, &v);
  return v;
}

static napi_value result(napi_env env, long r) { return num(env, r < 0 ? -errno : (int32_t)r); }

static napi_value array(napi_env env, const int32_t *items, size_t n) {
  napi_value arr;
  napi_create_array_with_length(env, n, &arr);
  for (size_t i = 0; i < n; i++) napi_set_element(env, arr, i, num(env, items[i]));
  return arr;
}

static napi_value buffer(napi_env env, const void *data, size_t n) {
  napi_value v;
  void *out;
  napi_create_buffer_copy(env, n, n ? data : "", &out, &v);
  return v;
}

// --- process --------------------------------------------------------------------

// V8 marks its heap MADV_DONTFORK ("dc" in smaps): a child would not have
// it, and would crash on its first touch of a JavaScript object. Undo it.
static void allow_fork(void) {
  FILE *f = fopen("/proc/self/smaps", "re");
  if (!f) return;
  char line[512];
  unsigned long start = 0, end = 0;
  while (fgets(line, sizeof line, f)) {
    unsigned long a, b;
    if (line[0] != 'V' && sscanf(line, "%lx-%lx ", &a, &b) == 2) {
      start = a, end = b;
    } else if (strncmp(line, "VmFlags:", 8) == 0 && strstr(line, " dc")) {
      madvise((void *)start, end - start, MADV_DOFORK);
    }
  }
  fclose(f);
}

// fork() -> pid. In the child, libuv's loop gets its own epoll and pipes.
static napi_value Fork(napi_env env, napi_callback_info info) {
  allow_fork();
  pid_t pid = fork();
  if (pid == 0) {
    struct uv_loop_s *loop;
    if (napi_get_uv_event_loop(env, &loop) == 0) uv_loop_fork(loop);
  }
  return result(env, pid);
}

// exit(code): _exit, past Node's teardown, which in a child would wait for
// worker threads that only the parent has.
static napi_value Exit(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  _exit(int_arg(&a, 0));
}

// exitOnCleanup(fd, code): when Node, ending normally, frees its
// environment (after every "exit" listener, whoever wrapped process.emit),
// send X(code) on fd and leave: past that, Node's teardown would wait for
// worker threads only the parent has. Called again, it updates the code.
static int cleanup_fd = -1, cleanup_code;
static void write_all(int fd, const char *p, size_t n);

// X(code) on fd, then _exit(code).
static void send_exit(int fd, int code) {
  unsigned char x[9] = {'X', 4, 0, 0, 0};
  memcpy(x + 5, &code, 4); // little-endian, as the protocol
  if (fd >= 0) write_all(fd, (const char *)x, sizeof x);
  _exit(code);
}

static void on_cleanup(void *unused) { send_exit(cleanup_fd, cleanup_code); }

static napi_value ExitOnCleanup(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int first = cleanup_fd < 0;
  cleanup_fd = int_arg(&a, 0);
  cleanup_code = int_arg(&a, 1);
  return num(env, first ? napi_add_env_cleanup_hook(env, on_cleanup, NULL) : 0);
}

// setpgid(): a group of our own, which the client signals.
static napi_value Setpgid(napi_env env, napi_callback_info info) { return result(env, setpgid(0, 0)); }

// Node caches its pid and ppid: a child needs the real ones.
static napi_value Getpid(napi_env env, napi_callback_info info) { return num(env, getpid()); }
static napi_value Getppid(napi_env env, napi_callback_info info) { return num(env, getppid()); }

// After an uncaught exception, Node prints it on stderr, ends with a
// "Node.js vX.Y.Z" line, then exits; in a child that exit aborts, on a
// failed join of worker threads only the parent has. reportThenExit(fd,
// code, footer, frame): stderr goes through a pipe that a thread copies to
// the real one, line by line, until the footer; there it sends X(code) on
// fd and exits, so that the client needs no wait status from the daemon
// (which may be gone: a kill -9, or a stop that did not wait). On
// the way, the frames of the daemon's own code (speedrunner_daemon.mjs,
// and the timers frame that called it) give way to frame, the one a
// native run ends with. If the main thread dies first (an abort, or a
// crash in the teardown), it closes the pipe and waits for that thread to
// pass on what is left.
static int report_fd = -1, real_err = -1, report_conn = -1, report_code;
static pthread_t report_thread;
static char report_footer[64], report_frame[256];

static void write_all(int fd, const char *p, size_t n) {
  while (n) {
    ssize_t w = write(fd, p, n);
    if (w < 0 && errno == EINTR) continue;
    if (w <= 0) return;
    p += w, n -= w;
  }
}

static void *copy_report(void *unused) {
  char buf[65536];
  size_t len = 0;
  int ours = 0;
  for (;;) {
    ssize_t r = read(report_fd, buf + len, sizeof buf - len - 1);
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) break;
    len += r;
    char *line = buf, *nl;
    while ((nl = memchr(line, '\n', buf + len - line))) {
      size_t n = nl - line + 1;
      int done = n - 1 == strlen(report_footer) && memcmp(line, report_footer, n - 1) == 0;
      if (memmem(line, n, "/speedrunner_daemon.mjs:", 24)) {
        ours = 1;
        if (report_frame[0]) write_all(real_err, report_frame, strlen(report_frame));
      } else if (!(ours && memmem(line, n, "(node:internal/timers:", 22))) {
        ours = 0;
        write_all(real_err, line, n);
      }
      line = nl + 1;
      if (done) send_exit(report_conn, report_code);
    }
    len -= line - buf;
    memmove(buf, line, len);
    if (len == sizeof buf - 1) {
      write_all(real_err, buf, len);
      len = 0;
    }
  }
  write_all(real_err, buf, len);
  send_exit(report_conn, report_code);
}

static void wait_for_report(int sig) {
  if (pthread_equal(pthread_self(), report_thread)) send_exit(report_conn, report_code);
  close(2);
  for (;;) pause();
}

static napi_value ReportThenExit(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  report_conn = int_arg(&a, 0);
  report_code = int_arg(&a, 1);
  str_arg(&a, 2, report_footer, sizeof report_footer);
  str_arg(&a, 3, report_frame, sizeof report_frame);
  int p[2];
  if (pipe2(p, O_CLOEXEC) < 0) return result(env, -1);
  real_err = fcntl(2, F_DUPFD_CLOEXEC, 3);
  dup2(p[1], 2);
  close(p[1]);
  report_fd = p[0];
  pthread_attr_t attr;
  pthread_attr_init(&attr);
  pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
  int r = pthread_create(&report_thread, &attr, copy_report, NULL);
  pthread_attr_destroy(&attr);
  int fatal[] = {SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP};
  for (size_t i = 0; i < sizeof fatal / sizeof *fatal; i++) signal(fatal[i], wait_for_report);
  return num(env, -r);
}

// reap() -> [pid, kind, value] of a child that ended (kind 0: exit code,
// 1: signal), [] if none.
static napi_value Reap(napi_env env, napi_callback_info info) {
  int st;
  pid_t pid;
  do pid = waitpid(-1, &st, WNOHANG);
  while (pid < 0 && errno == EINTR);
  if (pid <= 0) return array(env, NULL, 0);
  int32_t out[3] = {pid, WIFSIGNALED(st) ? 1 : 0, WIFSIGNALED(st) ? WTERMSIG(st) : WEXITSTATUS(st)};
  return array(env, out, 3);
}

// --- fds ----------------------------------------------------------------------

static napi_value Close(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, close(int_arg(&a, 0)));
}

static napi_value Dup(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, fcntl(int_arg(&a, 0), F_DUPFD_CLOEXEC, 3));
}

static napi_value Dup2(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, dup2(int_arg(&a, 0), int_arg(&a, 1)));
}

static napi_value Fchdir(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, fchdir(int_arg(&a, 0)));
}

// cloexec(fd, on)
static napi_value Cloexec(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int fd = int_arg(&a, 0), flags = fcntl(fd, F_GETFD);
  if (flags < 0) return result(env, -1);
  return result(env, fcntl(fd, F_SETFD, int_arg(&a, 1) ? flags | FD_CLOEXEC : flags & ~FD_CLOEXEC));
}

// blocking(fd): clears O_NONBLOCK.
static napi_value Blocking(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int fd = int_arg(&a, 0), flags = fcntl(fd, F_GETFL);
  if (flags < 0) return result(env, -1);
  return result(env, fcntl(fd, F_SETFL, flags & ~O_NONBLOCK));
}

static napi_value Flock(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int r;
  do r = flock(int_arg(&a, 0), int_arg(&a, 1) ? LOCK_EX | LOCK_NB : LOCK_UN);
  while (r < 0 && errno == EINTR);
  return result(env, r);
}

// read(fd, max) -> Buffer (empty at end of file), or -errno.
static napi_value Read(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int max = int_arg(&a, 1);
  char *buf = malloc(max > 0 ? max : 1);
  ssize_t r;
  do r = read(int_arg(&a, 0), buf, max);
  while (r < 0 && errno == EINTR);
  napi_value v = r < 0 ? result(env, -1) : buffer(env, buf, r);
  free(buf);
  return v;
}

// write(fd, buffer) -> bytes written, or -errno.
static napi_value Write(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  void *data;
  size_t len;
  napi_get_buffer_info(env, a.argv[1], &data, &len);
  ssize_t r;
  do r = write(int_arg(&a, 0), data, len);
  while (r < 0 && errno == EINTR);
  return result(env, r);
}

// poll(fds, timeoutMs) -> revents of each fd, or -errno (EINTR included).
static napi_value Poll(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  uint32_t n = 0;
  napi_get_array_length(env, a.argv[0], &n);
  struct pollfd *p = calloc(n ? n : 1, sizeof *p);
  int32_t *out = calloc(n ? n : 1, sizeof *out);
  for (uint32_t i = 0; i < n; i++) {
    napi_value v;
    napi_get_element(env, a.argv[0], i, &v);
    napi_get_value_int32(env, v, &p[i].fd);
    p[i].events = POLLIN;
  }
  int r = poll(p, n, int_arg(&a, 1));
  for (uint32_t i = 0; i < n; i++) out[i] = p[i].revents;
  napi_value v = r < 0 ? result(env, -1) : array(env, out, n);
  free(p);
  free(out);
  return v;
}

// --- sockets ------------------------------------------------------------------

// listen(name) -> fd: a Unix socket bound to name, relative to the cwd.
static napi_value Listen(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  struct sockaddr_un addr = {.sun_family = AF_UNIX};
  str_arg(&a, 0, addr.sun_path, sizeof addr.sun_path);
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
  if (fd < 0) return result(env, -1);
  mode_t old = umask(077);
  int r = bind(fd, (struct sockaddr *)&addr, sizeof addr);
  umask(old);
  if (r < 0 || listen(fd, 128) < 0) {
    int e = errno;
    close(fd);
    return num(env, -e);
  }
  return num(env, fd);
}

static napi_value Accept(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, accept4(int_arg(&a, 0), NULL, NULL, SOCK_CLOEXEC));
}

// socketpair() -> [a, b], both close-on-exec.
static napi_value Socketpair(napi_env env, napi_callback_info info) {
  int sv[2];
  if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sv) < 0) return result(env, -1);
  return array(env, sv, 2);
}

// peercred(fd) -> [pid, uid, gid]
static napi_value Peercred(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  struct ucred c;
  socklen_t len = sizeof c;
  if (getsockopt(int_arg(&a, 0), SOL_SOCKET, SO_PEERCRED, &c, &len) < 0) return result(env, -1);
  int32_t out[3] = {c.pid, (int32_t)c.uid, (int32_t)c.gid};
  return array(env, out, 3);
}

// recvfds(fd, max, maxFds) -> [Buffer, fd...], or -errno.
static napi_value Recvfds(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int max = int_arg(&a, 1), maxfds = int_arg(&a, 2);
  char *buf = malloc(max > 0 ? max : 1);
  size_t space = CMSG_SPACE(sizeof(int) * (maxfds > 0 ? maxfds : 1));
  char *ctl = calloc(1, space);
  struct iovec iov = {buf, (size_t)max};
  struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1, .msg_control = ctl, .msg_controllen = space};
  ssize_t r;
  do r = recvmsg(int_arg(&a, 0), &msg, MSG_CMSG_CLOEXEC);
  while (r < 0 && errno == EINTR);
  napi_value out;
  if (r < 0) {
    out = result(env, -1);
  } else {
    napi_create_array_with_length(env, 1, &out);
    napi_set_element(env, out, 0, buffer(env, buf, r));
    uint32_t n = 1;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
      if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS) continue;
      int count = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
      for (int i = 0; i < count; i++) {
        int fd;
        memcpy(&fd, CMSG_DATA(c) + i * sizeof(int), sizeof fd);
        napi_set_element(env, out, n++, num(env, fd));
      }
    }
  }
  free(buf);
  free(ctl);
  return out;
}

// sendfd(fd, buffer, passFd) -> bytes sent, or -errno.
static napi_value Sendfd(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  void *data;
  size_t len;
  napi_get_buffer_info(env, a.argv[1], &data, &len);
  int pass = int_arg(&a, 2);
  char ctl[CMSG_SPACE(sizeof(int))] = {0};
  struct iovec iov = {data, len};
  struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1, .msg_control = ctl, .msg_controllen = sizeof ctl};
  struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
  c->cmsg_level = SOL_SOCKET;
  c->cmsg_type = SCM_RIGHTS;
  c->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(c), &pass, sizeof pass);
  ssize_t r;
  do r = sendmsg(int_arg(&a, 0), &msg, MSG_NOSIGNAL);
  while (r < 0 && errno == EINTR);
  return result(env, r);
}

// --- signals ------------------------------------------------------------------

// selfpipe(signals) -> fd: each of these signals writes its number to a
// pipe, read with signals(fd). Not a signalfd: blocking a signal blocks it
// for the calling thread only, and Node's own threads would take it.
static int pipe_w = -1;

static void on_signal(int sig) {
  int e = errno;
  unsigned char b = (unsigned char)sig;
  ssize_t r = write(pipe_w, &b, 1);
  (void)r;
  errno = e;
}

static napi_value Selfpipe(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int p[2];
  if (pipe2(p, O_NONBLOCK | O_CLOEXEC) < 0) return result(env, -1);
  pipe_w = p[1];
  uint32_t n = 0;
  napi_get_array_length(env, a.argv[0], &n);
  struct sigaction sa;
  memset(&sa, 0, sizeof sa);
  sa.sa_handler = on_signal;
  sa.sa_flags = SA_RESTART;
  sigemptyset(&sa.sa_mask);
  for (uint32_t i = 0; i < n; i++) {
    napi_value v;
    int32_t sig;
    napi_get_element(env, a.argv[0], i, &v);
    napi_get_value_int32(env, v, &sig);
    if (sigaction(sig, &sa, NULL) < 0) return result(env, -1);
  }
  return num(env, p[0]);
}

// signals(fd) -> the signal numbers written to the pipe since last time.
static napi_value Signals(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  unsigned char buf[64];
  int32_t out[64];
  ssize_t r = read(int_arg(&a, 0), buf, sizeof buf);
  size_t n = r > 0 ? (size_t)r : 0;
  for (size_t i = 0; i < n; i++) out[i] = buf[i];
  return array(env, out, n);
}

// unblock(): an empty signal mask, as a native process starts with.
static napi_value Unblock(napi_env env, napi_callback_info info) {
  sigset_t none;
  sigemptyset(&none);
  return result(env, sigprocmask(SIG_SETMASK, &none, NULL));
}

// disposition(sig, ignore): SIG_IGN or SIG_DFL, below Node.
static napi_value Disposition(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  return result(env, signal(int_arg(&a, 0), int_arg(&a, 1) ? SIG_IGN : SIG_DFL) == SIG_ERR ? -1 : 0);
}

// --- files ----------------------------------------------------------------------

static napi_value InotifyInit(napi_env env, napi_callback_info info) {
  return result(env, inotify_init1(IN_NONBLOCK | IN_CLOEXEC));
}

// watch(fd, path) -> watch descriptor: any change to the file.
static napi_value InotifyWatch(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  char path[4096];
  str_arg(&a, 1, path, sizeof path);
  return result(env, inotify_add_watch(int_arg(&a, 0), path,
                                       IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVE_SELF | IN_DELETE_SELF));
}

// openpty(rows, cols) -> [master, slave], for imports that look for a terminal.
static napi_value Openpty(napi_env env, napi_callback_info info) {
  args_t a = args(env, info);
  int m = posix_openpt(O_RDWR | O_NOCTTY | O_CLOEXEC);
  if (m < 0 || grantpt(m) < 0 || unlockpt(m) < 0) return result(env, -1);
  char *name = ptsname(m);
  int s = name ? open(name, O_RDWR | O_NOCTTY | O_CLOEXEC) : -1;
  if (s < 0) {
    int e = errno;
    close(m);
    return num(env, -e);
  }
  struct winsize ws = {.ws_row = (unsigned short)int_arg(&a, 0), .ws_col = (unsigned short)int_arg(&a, 1)};
  if (ws.ws_row && ws.ws_col) ioctl(s, TIOCSWINSZ, &ws);
  int32_t out[2] = {m, s};
  return array(env, out, 2);
}

// --- module -----------------------------------------------------------------------

#define FN(name, f) {name, NULL, f, NULL, NULL, NULL, 0, NULL}

napi_value napi_register_module_v1(napi_env env, napi_value exports) {
  napi_property_descriptor props[] = {
      FN("fork", Fork),         FN("exit", Exit),         FN("reap", Reap),
      FN("getpid", Getpid),     FN("getppid", Getppid),   FN("reportThenExit", ReportThenExit),
      FN("setpgid", Setpgid),   FN("exitOnCleanup", ExitOnCleanup),
      FN("close", Close),       FN("dup", Dup),           FN("dup2", Dup2),
      FN("fchdir", Fchdir),     FN("cloexec", Cloexec),   FN("blocking", Blocking),
      FN("flock", Flock),       FN("read", Read),         FN("write", Write),
      FN("poll", Poll),         FN("listen", Listen),     FN("accept", Accept),
      FN("socketpair", Socketpair), FN("peercred", Peercred), FN("recvfds", Recvfds),
      FN("sendfd", Sendfd),     FN("selfpipe", Selfpipe), FN("signals", Signals),
      FN("unblock", Unblock),   FN("disposition", Disposition),
      FN("inotifyInit", InotifyInit), FN("inotifyWatch", InotifyWatch), FN("openpty", Openpty),
  };
  napi_define_properties(env, exports, sizeof props / sizeof props[0], props);
  return exports;
}
