// The few system calls a Node fork server needs, which Node does not expose.
#include <fcntl.h>
#include <node_api.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <uv.h>

static int32_t arg_int(napi_env env, napi_value v) {
  int32_t n = 0;
  napi_get_value_int32(env, v, &n);
  return n;
}

// V8 marks its heap pages MADV_DONTFORK ("dc" in smaps): a child would not
// have them. Undo it before forking; returns the number of areas changed.
static int allow_fork(void) {
  FILE *f = fopen("/proc/self/smaps", "r");
  if (!f) return -1;
  char line[512];
  unsigned long start = 0, end = 0;
  int changed = 0;
  while (fgets(line, sizeof line, f)) {
    unsigned long a, b;
    if (sscanf(line, "%lx-%lx ", &a, &b) == 2 && line[0] != 'V') {
      start = a, end = b;
    } else if (strncmp(line, "VmFlags:", 8) == 0 && strstr(line, " dc")) {
      if (madvise((void *)start, end - start, MADV_DOFORK) == 0) changed++;
    }
  }
  fclose(f);
  return changed;
}

// fork(); in the child, libuv's loop gets its own epoll and signal pipes.
static napi_value Fork(napi_env env, napi_callback_info info) {
  allow_fork();
  pid_t pid = fork();
  if (pid == 0) {
    uv_loop_t *loop;
    napi_get_uv_event_loop(env, &loop);
    uv_loop_fork(loop);
  }
  napi_value r;
  napi_create_int32(env, pid, &r);
  return r;
}

// waitpid(pid) -> exit code, or 128 + signal.
static napi_value Wait(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value argv[1];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  int st = 0;
  waitpid(arg_int(env, argv[0]), &st, 0);
  napi_value r;
  napi_create_int32(env, WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st), &r);
  return r;
}

// redirect(fd, path, write): fd now points to path.
static napi_value Redirect(napi_env env, napi_callback_info info) {
  size_t argc = 3;
  napi_value argv[3];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  char path[4096];
  size_t len;
  napi_get_value_string_utf8(env, argv[1], path, sizeof path, &len);
  bool write;
  napi_get_value_bool(env, argv[2], &write);
  int fd = open(path, write ? O_WRONLY | O_CREAT | O_TRUNC : O_RDONLY, 0644);
  dup2(fd, arg_int(env, argv[0]));
  close(fd);
  return NULL;
}

// blocking(fd): clears O_NONBLOCK, for the daemon's synchronous reads.
static napi_value Blocking(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value argv[1];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  int fd = arg_int(env, argv[0]);
  fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK);
  return NULL;
}

// _exit(code): a forked child must not run Node's teardown, which joins
// worker threads that only exist in the parent.
static napi_value Exit(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value argv[1];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  _exit(arg_int(env, argv[0]));
  return NULL;
}

static napi_value Init(napi_env env, napi_value exports) {
  napi_property_descriptor props[] = {
      {"exit", NULL, Exit, NULL, NULL, NULL, napi_default, NULL},
      {"blocking", NULL, Blocking, NULL, NULL, NULL, napi_default, NULL},
      {"fork", NULL, Fork, NULL, NULL, NULL, napi_default, NULL},
      {"wait", NULL, Wait, NULL, NULL, NULL, napi_default, NULL},
      {"redirect", NULL, Redirect, NULL, NULL, NULL, napi_default, NULL},
  };
  napi_define_properties(env, exports, 5, props);
  return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, Init)
