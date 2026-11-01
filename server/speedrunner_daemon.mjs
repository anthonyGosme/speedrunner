// speedrunner daemon for Node tools: serves each invocation of one tool from
// a forked child. It speaks the protocol of speedrunner_daemon.py, to the
// same client; see there for the wire format and the model (prefork spare,
// control socketpair, rule n°1). What differs for Node:
//
// - Node has no fork(), no fd passing and no dup2: speedrunner_node.node, a
//   small Node-API addon next to this file
//   (speedrunner_node-gnu.node or -musl.node), provides them. V8 marks its heap
//   MADV_DONTFORK; the addon undoes that before each fork.
// - Node keeps threads of its own (V8 workers, a delayed-task scheduler, the
//   inspector's signal thread), which a child does not have. The client
//   starts us with --single-threaded, so that V8 never hands them work, and
//   --no-lazy, so that what the parent loaded is compiled once, there. A
//   child leaves by _exit once every "exit" listener ran: Node's teardown
//   would wait for the threads it lacks.
// - Preloading is learned only (level 2): a child reports the modules it
//   loaded, in the order their loading started (module.registerHooks), and
//   a probe child vets them before the parent loads them. A module that
//   prints, changes the environment, starts a thread (other than libuv's
//   pool, which libuv rebuilds after fork) or leaves work pending is refused,
//   as is one that reads the command line beyond looking for given flags.
// - At import, reads of the environment, of process.argv flags and of
//   whether a fd is a terminal are recorded; a client that would see
//   another answer runs natively. Imports run with a pty on the fds that
//   were terminals for the client that started us.
//
// Started by the client, with the tool's own interpreter:
//
//   node --single-threaded --v8-pool-size=1 --no-lazy speedrunner_daemon.mjs
//       --tool NAME --script PATH --socket PATH [--lock-fd N] [--origin TEXT]
//       [--tty MASK[:ROWSxCOLS]]
//
// Linux and macOS: the addon hides most of the difference; on macOS files
// are compared by stat (no inotify), and CoreFoundation is watched for, as
// in speedrunner_daemon.py.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import tty from "node:tty";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const Module = require("node:module");
// A Node built without the code cache of its internals (Homebrew's node@22)
// compiles them from source, and --no-lazy, which the client gives us, then
// names some of their functions otherwise: a stack's "Module._extensions..js"
// becomes "Object..js". Run without it then (children compile more), before
// anything else, our env as the client gave it; or, without execve, serve nothing.
const LAZY_NAMES = process.config.variables.node_use_node_code_cache === false
  && process.execArgv.includes("--no-lazy");
if (LAZY_NAMES && typeof process.execve === "function") {
  process.execve(process.execPath, [process.execPath, ...process.execArgv.filter((a) => a !== "--no-lazy"),
                                    ...process.argv.slice(1)]);
}

// The addon built for our libc, glibc or musl, or for macOS: the client puts them here.
const MACOS = process.platform === "darwin";
const libc = MACOS ? "darwin" : fs.readFileSync("/proc/self/maps", "utf8").includes("ld-musl") ? "musl" : "gnu";
// Set by the client on macOS so that children may use Objective-C (Node has
// threads; see objc_fork_env in client/src/platform.rs): ours, not the tool's.
if (process.env.SPEEDRUNNER_OBJC_ADDED) {
  delete process.env.SPEEDRUNNER_OBJC_ADDED;
  delete process.env.OBJC_DISABLE_INITIALIZE_FORK_SAFETY;
}
const sys = require(path.join(path.dirname(fileURLToPath(import.meta.url)), `speedrunner_node-${libc}.node`));

// How a served child ends, hooked before anything is preloaded, so that
// these wrappers are the innermost ones: signal-exit and the like wrap them
// in turn, and run their own "exit" listeners before reaching them.
let serving = null; // in a served child: { dying }
const nodeEmit = process.emit;
process.emit = function (event, ...args) {
  const r = nodeEmit.call(this, event, ...args);
  if (serving && event === "exit") exiting(Number(process.exitCode ?? args[0] ?? 0) | 0);
  return r;
};
const nodeReallyExit = process.reallyExit;
process.reallyExit = function (code) {
  if (serving && !serving.dying) finish(Number(code ?? process.exitCode ?? 0) | 0);
  return nodeReallyExit.call(this, code);
};

const MAGIC = Buffer.from("HS1");
const HANDSHAKE_TIMEOUT_MS = 5000;
const LOCK_WAIT_MS = 10000;
const PROBE_STEP_MS = 3000; // a probe silent this long hangs on its current module
// By name: their numbers differ between Linux and macOS (SIGCHLD 17 or 20).
const { signals } = require("node:os").constants;
const SIG = { HUP: signals.SIGHUP, INT: signals.SIGINT, USR1: signals.SIGUSR1, TERM: signals.SIGTERM,
              CHLD: signals.SIGCHLD };
const CAUGHT = [SIG.CHLD, SIG.TERM, SIG.USR1, SIG.INT, SIG.HUP];
const EINTR = -4, EAGAIN = -11;
// What Node reads at its own start: a client with other values runs natively.
const STARTUP_ENV = (key) => key.startsWith("NODE_") || ["UV_THREADPOOL_SIZE", "LANG", "LC_ALL", "LC_MESSAGES"].includes(key);

const parentPid = process.pid;
let logFd = 2;
const state = {};

function log(msg) {
  sys.write(logFd, Buffer.from(`speedrunner[${process.pid}] ${msg}\n`));
}

// --- fds -------------------------------------------------------------------------

function waitReadable(fd, deadline) {
  for (;;) {
    const ms = deadline === null ? -1 : Math.max(0, Math.ceil(deadline - performance.now()));
    const r = sys.poll([fd], ms);
    if (Array.isArray(r)) return r[0] !== 0;
    if (r !== EINTR) return false;
  }
}

function readExact(fd, n, deadline = null) {
  const parts = [];
  let got = 0;
  while (got < n) {
    if (!waitReadable(fd, deadline)) throw new Error("handshake timed out");
    const b = sys.read(fd, n - got);
    if (typeof b === "number") {
      if (b === EAGAIN) continue;
      throw new Error(`read failed (${b})`);
    }
    if (b.length === 0) throw new Error("connection closed");
    parts.push(b);
    got += b.length;
  }
  return Buffer.concat(parts, n);
}

function writeAll(fd, data) {
  while (data.length) {
    const r = sys.write(fd, data);
    if (r < 0) throw new Error(`write failed (${r})`);
    data = data.subarray(r);
  }
}

function frame(kind, payload = Buffer.alloc(0)) {
  const head = Buffer.alloc(5);
  head.write(kind, 0, "latin1");
  head.writeUInt32LE(payload.length, 1);
  return Buffer.concat([head, payload]);
}

function sendMsg(fd, kind, payload = "") {
  writeAll(fd, frame(kind, typeof payload === "string" ? Buffer.from(payload) : payload));
}

function i32(n) {
  const b = Buffer.alloc(4);
  b.writeInt32LE(n);
  return b;
}

// --- wire format ------------------------------------------------------------------

function recvRequest(conn, deadline) {
  if (!waitReadable(conn, deadline)) throw new Error("handshake timed out");
  const r = sys.recvfds(conn, 8, 5);
  if (typeof r === "number") throw new Error(`recvmsg failed (${r})`);
  const [head, ...fds] = r;
  if (head.length === 0) return null; // a client that only checked we are up
  const all = head.length < 8 ? Buffer.concat([head, readExact(conn, 8 - head.length, deadline)]) : head;
  if (!all.subarray(0, 3).equals(MAGIC)) throw new Error("bad magic");
  return { kind: String.fromCharCode(all[3]), body: readExact(conn, all.readUInt32LE(4), deadline), fds };
}

function parseRequest(body) {
  let pos = 0;
  const take = (n) => {
    if (pos + n > body.length) throw new Error("truncated request");
    pos += n;
    return body.subarray(pos - n, pos);
  };
  const u32 = () => take(4).readUInt32LE(0);
  const blobs = () => Array.from({ length: u32() }, () => take(u32()).toString());
  const argv = blobs();
  const env = {};
  for (const entry of blobs()) {
    const eq = entry.indexOf("=");
    if (eq > 0) env[entry.slice(0, eq)] = entry.slice(eq + 1);
  }
  const umask = u32();
  take(8); // the signals the client ignores: Node resets them all at its start
  const groups = Array.from({ length: u32() }, u32);
  return { argv, env, umask, groups };
}

// --- what imports read ------------------------------------------------------------

// While modules load (in the parent and in probes), reads of process.env,
// process.argv and terminal checks are recorded in `reads`, and per load
// step in `step`, for the probe's verdict.
const reads = { env: new Set(), envKeys: false, flags: new Set(), tty: new Map() };
// What a shell changes from one command to the next: not compared when an
// import listed the environment's keys (the debug package looks for DEBUG_*).
const VOLATILE_ENV = new Set(["PWD", "OLDPWD", "SHLVL", "_"]);
let step = null;
let tracking = false;
const realEnv = process.env;
const realArgv = process.argv;
const realIsatty = tty.isatty;

function note(kind, key) {
  reads[kind].add(key);
  if (step) (step[kind] ||= new Set()).add(key);
}

const envProxy = new Proxy(realEnv, {
  get(t, k) {
    if (tracking && typeof k === "string") note("env", k);
    return Reflect.get(t, k);
  },
  has(t, k) {
    if (tracking && typeof k === "string") note("env", k);
    return Reflect.has(t, k);
  },
  ownKeys(t) {
    if (tracking) reads.envKeys = true;
    return Reflect.ownKeys(t);
  },
});

// Looking for a flag (supports-color's hasFlag uses indexOf) is fine: the
// flags looked for are recorded. Any other read of the arguments is not.
const argvProxy = new Proxy(realArgv, {
  get(t, k) {
    if (tracking && (k === "includes" || k === "indexOf" || k === "lastIndexOf")) {
      return (value, ...rest) => {
        if (typeof value === "string") note("flags", value);
        return t[k](value, ...rest);
      };
    }
    if (tracking && step && typeof k === "string" && k !== "0" && k !== "1") step.argv = true;
    return Reflect.get(t, k, t);
  },
});

function trackedIsatty(fd) {
  const r = realIsatty(fd);
  if (tracking && !reads.tty.has(fd)) reads.tty.set(fd, r);
  return r;
}

// process.stdin/stdout/stderr are made on first use, for what the fd is
// then. One made at import is a terminal stream or not, as a terminal
// check would answer; otherwise it is a file stream (the daemon's fds are
// its log and /dev/null), whose synchronous writes suit any fd.
["stdin", "stdout", "stderr"].forEach((name, fd) => {
  const desc = Object.getOwnPropertyDescriptor(process, name);
  if (!desc?.get || !desc.configurable) return;
  Object.defineProperty(process, name, {
    ...desc,
    get() {
      if (tracking && !reads.tty.has(fd)) reads.tty.set(fd, realIsatty(fd));
      return desc.get.call(this);
    },
  });
});

function startTracking() {
  tracking = true;
  process.env = envProxy;
  // Imports see the command line of a bare run of the tool.
  realArgv.splice(0, realArgv.length, process.execPath, state.script);
  process.argv = argvProxy;
  tty.isatty = trackedIsatty;
}

function stopTracking() {
  tracking = false;
  process.env = realEnv;
  process.argv = realArgv;
  tty.isatty = realIsatty;
}

// --- loading modules ------------------------------------------------------------

// Each module a child loads, in the order its loading started.
const loaded = [];
let learning = false;
const hooks = typeof Module.registerHooks !== "function" ? null : Module.registerHooks({
  load(url, context, nextLoad) {
    if (learning) loaded.push([url, context.format ?? null]);
    return nextLoad(url, context);
  },
});

async function load([url, format]) {
  if (format === "builtin" || url.startsWith("node:")) return require(url);
  if (!url.startsWith("file:")) throw new Error("not a file");
  if (format === "module" || format === "module-typescript") return import(url);
  if (format === "wasm") throw new Error("a WebAssembly module");
  return require(fileURLToPath(url));
}

function threadNames() {
  if (MACOS) return sys.threads();
  const names = [];
  for (const t of fs.readdirSync("/proc/self/task")) {
    try {
      names.push(fs.readFileSync(`/proc/self/task/${t}/comm`, "utf8").trim());
    } catch {}
  }
  return names;
}

// Threads a child would lack. Not libuv's pool: its pthread_atfork handler
// resets it, and a child makes its own on first use.
function foreignThreads() {
  return threadNames().filter((n) => n !== "libuv-worker").length;
}

const tick = () => new Promise((r) => setImmediate(r));

function pendingWork() {
  return process.getActiveResourcesInfo().filter((r) => r !== "TTYWrap" && r !== "PipeWrap").length;
}

// Loads one module and judges it: null if it may stay, else the reason.
async function loadJudged(entry, outs, threads) {
  const env = JSON.stringify(realEnv);
  const work = pendingWork();
  const s = (step = {});
  try {
    await load(entry);
    await tick();
  } catch (e) {
    return `fails: ${String(e?.message ?? e).split("\n")[0].slice(0, 120)}`;
  } finally {
    step = null;
  }
  if (outs && outs.some((fd) => sys.poll([fd], 0)[0] !== 0)) return "prints";
  if (JSON.stringify(realEnv) !== env) return "changes the environment";
  if (s.argv) return "reads the command line";
  if (foreignThreads() > threads) return "starts a thread";
  if (pendingWork() > work) return "leaves work pending";
  return null;
}

// --- state -------------------------------------------------------------------------

function fingerprint(paths) {
  return JSON.stringify(paths.map((p) => {
    try {
      const st = fs.statSync(p);
      return [st.mtimeMs, st.size, st.ino, st.dev];
    } catch {
      return null;
    }
  }));
}

// Every file loaded is watched: a pending inotify event means stale, for
// every process; nobody reads the queue. Files that cannot be watched are
// compared by stat.
function watchLoaded() {
  const files = new Set([state.scriptReal, process.execPath]);
  for (const k of Object.keys(require.cache)) if (k.startsWith("/")) files.add(k);
  for (const [url] of state.preloaded) if (url.startsWith("file:")) files.add(fileURLToPath(url));
  for (const f of files) {
    if (state.watched.has(f)) continue;
    if (state.inotify >= 0 && sys.inotifyWatch(state.inotify, f) >= 0) state.watched.add(f);
    else state.unwatched.push([f, fingerprint([f])]);
  }
}

function stale() {
  if (state.inotify >= 0 && sys.poll([state.inotify], 0)[0] !== 0) return true;
  if (fingerprint(state.fpPaths) !== state.fp) return true;
  return state.unwatched.some(([f, fp]) => fingerprint([f]) !== fp);
}

function statusText() {
  if (state.disabled) return `disabled: ${state.disabled}`;
  const learn = state.learning ? ` learning=${state.learning}` : "";
  return `ready pid=${parentPid} preload_ms=${Math.round(state.preloadMs)} modules=${state.preloaded.length} ` +
    `levels=2 learned=${state.preloaded.length} blacklisted=${state.blacklist.size} memo=0 gen=${state.gen}${learn}`;
}

function cachePath() {
  const base = realEnv.XDG_CACHE_HOME || path.join(realEnv.HOME || "/tmp", ".cache");
  let h = 0xcbf29ce484222325n;
  for (const c of Buffer.from(state.scriptReal + state.fp + process.version)) {
    h = ((h ^ BigInt(c)) * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return path.join(base, "speedrunner", `${state.tool.replace(/[^\w.-]/g, "_")}-node-${h.toString(16)}.json`);
}

function loadCache() {
  try {
    const c = JSON.parse(fs.readFileSync(cachePath(), "utf8"));
    return { learned: c.learned ?? [], blacklist: c.blacklist ?? [], mainFrame: c.mainFrame };
  } catch {
    return { learned: [], blacklist: [] };
  }
}

function saveCache() {
  const p = cachePath();
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(`${p}.${process.pid}.tmp`, JSON.stringify({
      learned: state.preloaded, blacklist: [...state.blacklist], mainFrame: state.mainFrameCache,
    }));
    fs.renameSync(`${p}.${process.pid}.tmp`, p);
  } catch (e) {
    log(`cannot save the learned set: ${e.message}`);
  }
}

// --- child: one invocation ------------------------------------------------------------

// The kernel's groups for us: on macOS process.getgroups() may give the
// user's, resolved anew; ours are read as a peer's are, over a socketpair.
let ownGroups = null;
function groupsOfOurs() {
  if (!MACOS) return process.getgroups();
  if (ownGroups === null) {
    const sv = sys.socketpair();
    ownGroups = sys.peercred(sv[0]).slice(3);
    sys.close(sv[0]);
    sys.close(sv[1]);
  }
  return ownGroups;
}

function whyNotFast(req, peerGid, fds, peerGroups = null) {
  if (state.disabled) return state.disabled;
  const typed = req.argv[0] ?? "";
  let real = null;
  try {
    real = fs.realpathSync(typed.startsWith("/") ? typed : `${sys.fdpath(fds[3])}/${typed}`);
  } catch {}
  if (!typed || real !== state.scriptReal) return "script path mismatch";
  if (stale()) return "stale: installed files changed";
  const mine = [...new Set(groupsOfOurs())].sort();
  const theirs = [...new Set(peerGroups ?? req.groups)].sort();
  if (peerGid !== process.getgid() || JSON.stringify(mine) !== JSON.stringify(theirs)) {
    return "credentials differ from the daemon's";
  }
  for (const key of new Set([...Object.keys(req.env), ...Object.keys(state.startEnv)])) {
    if (STARTUP_ENV(key) && req.env[key] !== state.startEnv[key]) return `startup env differs: ${key}`;
  }
  for (const key of reads.env) {
    if (withoutLinks(key, req.env[key]) !== withoutLinks(key, state.importEnv[key])) {
      return `import-time env differs: ${key}`;
    }
  }
  if (reads.envKeys) {
    const keys = (env) => Object.keys(env).filter((k) => !VOLATILE_ENV.has(k) && !k.startsWith("SPEEDRUNNER_")).sort().join("\0");
    if (keys(req.env) !== keys(state.importEnv)) return "import-time env keys differ";
  }
  for (const arg of req.argv.slice(1)) {
    if (reads.flags.has(arg) || reads.flags.has(arg.split("=")[0])) return `import-time flag check: ${arg}`;
  }
  for (const [fd, was] of reads.tty) {
    if (fd <= 2 && realIsatty(fds[fd]) !== was) return `import-time terminal state differs: fd ${fd}`;
  }
  return null;
}

function applyRequest(req, fds, script) {
  for (const i of [0, 1, 2]) sys.dup2(fds[i], i);
  state.standIn?.close();
  sys.fchdir(fds[3]);
  process.chdir("."); // Node caches its cwd: drop the daemon's
  for (const fd of fds) sys.close(fd); // the tty too: no bridge to /dev/tty yet
  process.umask(req.umask);
  // In place: modules hold references to both.
  for (const key of Object.keys(realEnv)) if (!(key in req.env)) delete realEnv[key];
  Object.assign(realEnv, req.env);
  realArgv.splice(0, realArgv.length, process.execPath, script, ...req.argv.slice(1));
  process.execArgv.splice(0, process.execArgv.length);
  reseedMathRandom();
  sys.unblock();
}

// V8 keeps Math.random's state in the heap, which every child inherits:
// each would draw the same numbers. A fresh generator per child instead
// (xoshiro128**, seeded by the kernel), with Math.random's 53-bit doubles.
function reseedMathRandom() {
  const s = new Uint32Array(crypto.randomBytes(16).buffer.slice(0));
  const next = () => {
    const r = Math.imul(((Math.imul(s[1], 5) << 7) | (Math.imul(s[1], 5) >>> 25)), 9) >>> 0;
    const t = s[1] << 9;
    s[2] ^= s[0];
    s[3] ^= s[1];
    s[1] ^= s[2];
    s[0] ^= s[3];
    s[2] ^= t;
    s[3] = (s[3] << 11) | (s[3] >>> 21);
    return r;
  };
  Math.random = { random() {
    return ((next() >>> 5) * 67108864 + (next() >>> 6)) / 9007199254740992;
  } }.random;
}

let conn = -1;
let ctl = -1;

// The end of a served run. "exit" was emitted: what was learned goes to the
// parent; then the exit code to the client, from process.reallyExit (after
// process.exit) or from Node's environment cleanup (at a natural end), and
// no teardown. After an uncaught exception, Node prints it only once "exit"
// was emitted: the addon passes that report on, as a native run would
// print it, then sends X and exits.
function exiting(code) {
  if (serving.dying) {
    sys.reportThenExit(conn, code, `Node.js ${process.version}`, state.mainFrame ?? "");
    return;
  }
  if (learning) {
    learning = false;
    const known = new Set(state.preloaded.map(([u]) => u));
    const fresh = loaded.filter(([u]) => !known.has(u) && u !== state.scriptUrl && !state.blacklist.has(u));
    if (fresh.length && ctl >= 0) {
      try {
        sendMsg(ctl, "L", JSON.stringify(fresh));
      } catch {}
    }
  }
  sys.exitOnCleanup(conn, code);
}

function finish(code) {
  try {
    sendMsg(conn, "X", i32(code));
  } catch {}
  sys.exit(code);
}

function serveChild(c) {
  conn = c;
  const deadline = performance.now() + HANDSHAKE_TIMEOUT_MS;
  let req, fds, script;
  try {
    const cred = sys.peercred(conn);
    if (!Array.isArray(cred) || cred[1] !== process.getuid()) sys.exit(0);
    const msg = recvRequest(conn, deadline);
    if (msg === null) sys.exit(0);
    if (msg.kind === "P" || msg.kind === "S") {
      for (const fd of msg.fds) sys.close(fd);
      if (msg.kind === "S") {
        sendMsg(conn, "P", "stopping");
        process.kill(parentPid, "SIGTERM");
      } else if (stale()) {
        markExiting();
        unlinkSocket();
        process.kill(parentPid, "SIGUSR1");
        sendMsg(conn, "P", "stale: installed files changed");
      } else {
        sendMsg(conn, "P", statusText());
      }
      sys.exit(0);
    }
    req = parseRequest(msg.body);
    fds = msg.fds;
    if (fds.length < 4 || fds.length > 5) {
      sendMsg(conn, "N", "missing file descriptors");
      sys.exit(0);
    }
    const reason = whyNotFast(req, cred[2], fds, MACOS ? cred.slice(3) : null);
    if (reason !== null) {
      if (reason.startsWith("stale")) {
        markExiting();
        unlinkSocket();
        process.kill(parentPid, "SIGUSR1");
      }
      sendMsg(conn, "N", reason);
      sys.exit(0);
    }
    // process.argv[1] as Node makes it: the script as typed, made absolute.
    script = path.resolve(MACOS ? sys.fdpath(fds[3]) : fs.readlinkSync(`/proc/self/fd/${fds[3]}`), req.argv[0]);
    sendMsg(conn, "R", i32(process.pid));
    if (readExact(conn, 1, deadline).toString() !== "G") sys.exit(0);
  } catch (e) {
    log(`child setup failed: ${e.message}`);
    sys.exit(0); // nothing has run yet: let the client fall back
  }
  applyRequest(req, fds, script);
  sys.close(logFd);
  serving = { dying: false };
  process.on("uncaughtExceptionMonitor", () => {
    serving.dying = process.listenerCount("uncaughtException") === 0 && !process.hasUncaughtExceptionCaptureCallback();
  });
  learning = hooks !== null;
  // As `node script` runs it: CommonJS or ES module, symlinks resolved.
  setImmediate(() => Module.runMain(script));
}

// --- parent ---------------------------------------------------------------------------

// See without_links in speedrunner_daemon.py.
function withoutLinks(key, value) {
  if (key !== "PATH" || value === undefined) return value;
  const links = path.dirname(state.sockPath);
  return value.split(":").filter((d) => {
    const dir = d.replace(/\/+$/, "");
    return !(path.dirname(dir) === links && path.basename(dir).startsWith("bin-"));
  }).join(":");
}

function markExiting() {
  try {
    fs.writeFileSync(`${path.basename(state.sockPath)}.lock`, `exiting ${parentPid}\n`);
  } catch {}
}

function unlinkSocket() {
  const name = path.basename(state.sockPath);
  try {
    if (fs.statSync(name).ino === state.boundIno) fs.unlinkSync(name);
  } catch {}
}

function openRuntimeDir(dir) {
  const st = fs.lstatSync(dir);
  if (!st.isDirectory() || st.uid !== process.getuid() || (st.mode & 0o7777) !== 0o700) {
    throw new Error(`${dir} must be a directory owned by uid ${process.getuid()} with mode 0700`);
  }
  process.chdir(dir);
  const here = fs.statSync(".");
  if (here.ino !== st.ino || here.dev !== st.dev) throw new Error(`${dir} changed under us`);
}

function acquireLock(lockFd, sockPath) {
  if (lockFd !== null) {
    sys.cloexec(lockFd, 1);
    sys.flock(lockFd, 1); // ours already: a no-op
    return lockFd;
  }
  const fd = fs.openSync(`${path.basename(sockPath)}.lock`, fs.constants.O_RDWR | fs.constants.O_CREAT, 0o600);
  sys.cloexec(fd, 1);
  const deadline = performance.now() + LOCK_WAIT_MS;
  while (sys.flock(fd, 1) < 0) {
    if (performance.now() > deadline) return null;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
  }
  return fd;
}

// In a forked process: Node keeps its pid and ppid from its start, and
// the parent's signal handlers write to the parent's pipe.
function forked() {
  for (const [key, value] of [["pid", sys.getpid()], ["ppid", sys.getppid()]]) {
    Object.defineProperty(process, key, { ...Object.getOwnPropertyDescriptor(process, key), value });
  }
  for (const sig of CAUGHT) sys.disposition(sig, 0);
}

// Runs in a forked spare: wait for one client, then serve it.
function runSpare(lsock, ctlFd, inherited) {
  forked();
  sys.setpgid(); // the client signals the child's group
  for (const fd of inherited) sys.close(fd);
  ctl = ctlFd;
  for (;;) {
    const r = sys.poll([lsock, ctlFd], -1);
    if (!Array.isArray(r)) continue;
    if (r[1] !== 0) sys.exit(0); // retired, or the daemon is gone
    const c = sys.accept(lsock);
    if (c >= 0) {
      sys.close(lsock);
      // The parent keeps a copy, to send the wait status and replace us.
      sys.sendfd(ctlFd, frame("A"), c);
      serveChild(c);
      return;
    }
  }
}

// One control message: [kind, payload, fds], or [null] at end of file.
function readCtl(fd) {
  const r = sys.recvfds(fd, 5, 1);
  if (typeof r === "number") return [null, null, []];
  if (r[0].length === 0) return [null, null, r.slice(1)];
  const head = r[0].length < 5 ? Buffer.concat([r[0], readExact(fd, 5 - r[0].length)]) : r[0];
  return [String.fromCharCode(head[0]), readExact(fd, head.readUInt32LE(1)), r.slice(1)];
}

// Vets candidates in a throwaway child, in order: the accepted ones, then
// the first refused one and why; what follows is for the next probe.
function probe(candidates) {
  const [rd, wr] = sys.socketpair();
  const [out1, in1] = sys.socketpair();
  const [out2, in2] = sys.socketpair();
  const pid = sys.fork();
  if (pid === 0) {
    forked();
    sys.close(rd);
    sys.dup2(in1, 1);
    sys.dup2(in2, 2);
    (async () => {
      startTracking();
      for (const entry of candidates) {
        const why = await loadJudged(entry, [out1, out2], 1);
        writeAll(wr, Buffer.from(JSON.stringify(why === null ? { ok: entry[0] } : { refused: entry[0], why }) + "\n"));
        if (why !== null) break;
      }
      sys.exit(0);
    })().catch(() => sys.exit(1));
    return null;
  }
  for (const fd of [wr, in1, in2, out1, out2]) sys.close(fd);
  const accepted = [];
  let refused = null;
  let text = "";
  while (waitReadable(rd, performance.now() + PROBE_STEP_MS)) {
    const b = sys.read(rd, 65536);
    if (typeof b === "number" || b.length === 0) break;
    text += b.toString();
  }
  sys.close(rd);
  try {
    process.kill(pid, "SIGKILL");
  } catch {}
  for (const line of text.split("\n").filter(Boolean)) {
    const v = JSON.parse(line);
    if (v.ok) accepted.push(v.ok);
    else refused = v;
  }
  if (!refused && accepted.length < candidates.length) {
    refused = { refused: candidates[accepted.length][0], why: "crashes or hangs the probe" };
  }
  return { accepted, refused };
}

// Loads what children learned, once a probe vetted it: "child" in a probe,
// "stop" if a module misbehaved here, else whether anything was added.
async function applyLearned(candidates) {
  const t0 = performance.now();
  const threads = foreignThreads();
  let todo = candidates.filter(([u]) => !state.blacklist.has(u) && u !== state.scriptUrl);
  let added = 0;
  while (todo.length) {
    state.learning = todo.length;
    const v = probe(todo);
    if (v === null) return "child";
    startTracking();
    for (const entry of todo.slice(0, v.accepted.length)) {
      const why = await loadJudged(entry, null, threads);
      if (why !== null) {
        // Fine in the probe, not here: leave, and let the next daemon skip it.
        stopTracking();
        state.blacklist.add(entry[0]);
        saveCache();
        log(`learned module ${entry[0]} ${why} in the daemon; exiting`);
        return "stop";
      }
      state.preloaded.push(entry);
      added++;
    }
    stopTracking();
    // macOS: CoreFoundation used (a canary, a fork: once per batch, not per
    // module in the probe, where its usual user, process.title, starts
    // threads anyway). Then none of the batch again.
    const batch = todo.slice(0, v.accepted.length);
    if (batch.length && sys.cfUnsafe()) {
      for (const [u] of batch) state.blacklist.add(u);
      saveCache();
      log(`${batch.length} learned modules used CoreFoundation in the daemon; exiting`);
      return "stop";
    }
    todo = todo.slice(v.accepted.length);
    if (v.refused) {
      state.blacklist.add(v.refused.refused);
      log(`refused ${v.refused.refused}: ${v.refused.why}`);
      todo = todo.filter(([u]) => u !== v.refused.refused);
    }
  }
  state.learning = 0;
  if (added) {
    state.gen++;
    state.importEnv = { ...realEnv };
    watchLoaded();
    saveCache();
    log(`learned ${added} modules in ${Math.round(performance.now() - t0)} ms`);
  }
  return added ? "changed" : "same";
}

function takeLearned(payload, pending) {
  try {
    for (const entry of JSON.parse(payload.toString())) {
      if (!pending.some(([u]) => u === entry[0])) pending.push(entry);
    }
  } catch {}
}

async function serve(opts) {
  openRuntimeDir(path.dirname(opts.socket));
  const lockFd = acquireLock(opts["lock-fd"] !== undefined ? Number(opts["lock-fd"]) : null, opts.socket);
  if (lockFd === null) {
    log("another daemon holds the lock; exiting");
    return 0;
  }
  const name = path.basename(opts.socket);
  try {
    fs.unlinkSync(name);
  } catch {}
  const lsock = sys.listen(name);
  if (lsock < 0) {
    log(`cannot listen on ${name} (${lsock})`);
    return 1;
  }
  fs.chmodSync(name, 0o600);
  state.boundIno = fs.statSync(name).ino;
  state.mainFrame = mainFrame();
  const sigfd = sys.selfpipe(CAUGHT); // after mainFrame: spawning sets libuv's SIGCHLD handler
  // Without it no child's end is seen, unless it sends X itself (macOS once
  // had the signals' Linux numbers here, and an execve'd child hung its client).
  if (sigfd < 0) state.disabled = `cannot catch signals (${sigfd})`;
  if (LAZY_NAMES) state.disabled = "this Node has no code cache, and no execve to run without --no-lazy";
  state.inotify = sys.inotifyInit();

  // What earlier daemons learned, loaded with a pty on the fds that were
  // terminals for the client that started us.
  const t0 = performance.now();
  const cache = loadCache();
  state.blacklist = new Set(cache.blacklist);
  // Kept for the daemon's life: what it learns later loads with it too.
  state.standIn = ttyStandIn(opts.tty ?? "0");
  // Node loads its bundled root certificates once, in a thread of its own,
  // under a lock that thread may hold when we fork: a child would then wait
  // on it forever (firebase). Making a TLS context waits for that load.
  require("node:tls").createSecureContext();
  if (sys.cfUnsafe()) state.disabled = "CoreFoundation used at startup: a forked child would be killed";
  if (cache.learned.length) {
    const r = await applyLearned(cache.learned);
    if (r === "child") return null;
    if (r === "stop") {
      markExiting();
      unlinkSocket();
      return 0;
    }
  }
  state.preloadMs = performance.now() - t0;
  state.importEnv = { ...realEnv };
  watchLoaded();
  log(`ready for ${state.tool}, started by ${opts.origin ?? "hand"}; ${statusText()}`);

  const children = new Map(); // pid -> {ctl, conn}
  let spare = null;
  let spareFailures = 0;
  let stop = null;
  const pending = [];
  const idle = Number(realEnv.SPEEDRUNNER_IDLE_TIMEOUT || 4 * 3600) * 1000;
  let lastActivity = performance.now();

  let leaving = false;
  for (;;) {
    // Stopping: out of the way first, so that new clients start a fresh
    // daemon; then the runs in progress, whose end only our wait status
    // reports if they die of a signal.
    if (stop !== null && !leaving) {
      leaving = true;
      log(`exiting: ${stop}`);
      markExiting();
      unlinkSocket();
      sys.close(lsock); // clients queued on it fall back at once
      // The lock only says who serves the socket: a new daemon may start
      // now, however long the runs we wait for last.
      sys.close(lockFd);
      state.disabled ??= "exiting";
      if (spare !== null) {
        try {
          sendMsg(children.get(spare).ctl, "Q");
        } catch {}
        spare = null;
      }
    }
    if (leaving && children.size === 0) break;
    if (spare === null && !state.disabled) {
      const [a, b] = sys.socketpair();
      const pid = sys.fork();
      if (pid === 0) {
        sys.close(a);
        const inherited = [lockFd, sigfd];
        for (const ch of children.values()) for (const fd of [ch.ctl, ch.conn]) if (fd !== null) inherited.push(fd);
        runSpare(lsock, b, inherited.filter((fd) => fd > 2));
        return null; // the child's event loop runs the tool
      }
      sys.close(b);
      children.set(pid, { ctl: a, conn: null });
      spare = pid;
    }
    // Learn while no child serves a client.
    const busy = [...children].some(([pid, ch]) => pid !== spare && ch.conn !== null);
    if (pending.length && !busy && !leaving) {
      const r = await applyLearned(pending.splice(0));
      if (r === "child") return null;
      if (r === "stop") {
        stop ??= "a learned module misbehaved";
        continue;
      }
      if (r === "changed" && spare !== null) {
        try {
          sendMsg(children.get(spare).ctl, "Q");
        } catch {}
        spare = null;
        continue;
      }
    }
    const ctls = [...children.values()].map((ch) => ch.ctl).filter((fd) => fd !== null);
    // Once stale, the inotify fd stays readable: not watched while leaving.
    const watch = [sigfd, leaving ? -1 : state.inotify, ...ctls];
    const timeout = leaving ? 50 : pending.length ? 50 : Math.max(1000, lastActivity + idle - performance.now());
    const r = sys.poll(watch, Math.ceil(timeout));
    if (Array.isArray(r)) {
      if (r[1] !== 0 && !leaving) {
        stop ??= "a preloaded file changed";
        continue;
      }
      for (const [pid, ch] of children) {
        const i = ch.ctl === null ? -1 : watch.indexOf(ch.ctl);
        if (i < 0 || r[i] === 0) continue;
        const [kind, payload, fds] = readCtl(ch.ctl);
        if (kind === "A" && fds.length) {
          ch.conn = fds.shift();
          lastActivity = performance.now();
          if (pid === spare) {
            spare = null;
            spareFailures = 0;
          }
        } else if (kind === "L") {
          takeLearned(payload, pending);
        }
        for (const fd of fds) sys.close(fd);
        if (kind === null) {
          sys.close(ch.ctl);
          ch.ctl = null;
        }
      }
      if (r[0] !== 0) {
        for (const sig of sys.signals(sigfd)) {
          if (sig === SIG.USR1) stop ??= "stale fingerprint";
          else if (sig !== SIG.CHLD) stop ??= `signal ${sig}`;
        }
      }
    }
    for (let w = sys.reap(); w.length; w = sys.reap()) {
      const [pid, kind, value] = w;
      const ch = children.get(pid);
      if (!ch) continue;
      children.delete(pid);
      if (ch.ctl !== null) {
        // What it sent before dying (its A, its L): without waiting.
        while (waitReadable(ch.ctl, performance.now())) {
          const [k, payload, fds] = readCtl(ch.ctl);
          if (k === "A" && fds.length) ch.conn = fds.shift();
          if (k === "L") takeLearned(payload, pending);
          for (const fd of fds) sys.close(fd);
          if (k === null) break;
        }
        sys.close(ch.ctl);
      }
      if (pid === spare) {
        spare = null;
        if (++spareFailures >= 3) {
          state.disabled = "spare children keep dying";
          log(state.disabled);
        }
      }
      if (ch.conn !== null) {
        const status = Buffer.alloc(5);
        status.writeUInt8(kind, 0);
        status.writeInt32LE(value, 1);
        try {
          sendMsg(ch.conn, "W", status);
        } catch {}
        sys.close(ch.conn);
      }
    }
    const active = [...children.values()].some((ch) => ch.conn !== null);
    if (!active && performance.now() >= lastActivity + idle) stop ??= "idle timeout";
  }
  return 0;
}

// The last frame of a stack that starts in a script's top level, as `node
// script` prints it ("at node:internal/main/run_main_module:33:47"), which
// a served child's stack ends otherwise; learned once per Node version.
function mainFrame() {
  const cache = loadCache();
  if (cache.mainFrame?.[0] === process.version) {
    state.mainFrameCache = cache.mainFrame;
    return cache.mainFrame[1];
  }
  const dir = fs.mkdtempSync(path.join(realEnv.TMPDIR || "/tmp", "speedrunner-"));
  let frame = "";
  try {
    fs.writeFileSync(path.join(dir, "frame.cjs"), "process.stdout.write(new Error().stack.split('\\n').pop())");
    const r = require("node:child_process").spawnSync(process.execPath, [path.join(dir, "frame.cjs")], {
      env: realEnv, encoding: "utf8", timeout: 10000,
    });
    if (/^ +at .*node:internal/.test(r.stdout ?? "")) frame = r.stdout + "\n";
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  state.mainFrameCache = [process.version, frame];
  return frame;
}

// --tty MASK[:ROWSxCOLS]: a pty on those fds, for the modules the daemon
// loads.
function ttyStandIn(spec) {
  const [m, size = ""] = spec.split(":");
  const mask = Number(m) & 7;
  if (!mask) return null;
  const [rows, cols] = size.split("x").map(Number);
  const pty = sys.openpty(rows || 0, cols || 0);
  if (!Array.isArray(pty)) {
    log(`no terminal stand-in (${pty}): imports see none`);
    return null;
  }
  const saved = [];
  for (const fd of [0, 1, 2]) {
    if (mask & (1 << fd)) {
      saved.push([fd, sys.dup(fd)]);
      sys.dup2(pty[1], fd);
    }
  }
  return {
    close() { // in a child, whose fds 0-2 are the client's
      for (const fd of [pty[0], pty[1], ...saved.map(([, copy]) => copy)]) sys.close(fd);
    },
  };
}

async function main() {
  const args = realArgv.slice(2);
  const opts = {};
  for (let i = 0; i + 1 < args.length; i += 2) {
    if (!["--tool", "--script", "--socket", "--lock-fd", "--origin", "--tty"].includes(args[i])) {
      fs.writeSync(2, `speedrunner_daemon: unknown argument ${args[i]}\n`);
      return 2;
    }
    opts[args[i].slice(2)] = args[i + 1];
  }
  for (const k of ["tool", "script", "socket"]) {
    if (opts[k] === undefined) {
      fs.writeSync(2, `speedrunner_daemon: missing ${k}\n`);
      return 2;
    }
  }
  logFd = sys.dup(2);
  Object.assign(state, {
    tool: opts.tool, script: opts.script, scriptReal: fs.realpathSync(opts.script), sockPath: opts.socket,
    disabled: null, startEnv: { ...realEnv }, preloaded: [], blacklist: new Set(), gen: 0, learning: 0,
    preloadMs: 0, watched: new Set(), unwatched: [], inotify: -1, importEnv: {},
  });
  state.scriptUrl = pathToFileURL(state.scriptReal).href;
  state.fpPaths = [state.script, state.scriptReal, process.execPath];
  state.fp = fingerprint(state.fpPaths);
  if (!hooks) log("warning: no module.registerHooks (Node < 22.15): nothing is learned");
  return serve(opts);
}

const code = await main();
// The parent and the probes end here; a served child returned to run the tool.
if (code !== null) sys.exit(code);
