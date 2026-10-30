// A minimal Node fork server, for the go/no-go: preloads what a tool loaded
// (learn.cjs), then forks one child per request read on stdin.
//   node [flags] daemon.mjs <fork.node> <bin> <learned.json>
// Request (one JSON line): {"args": [...], "cwd": ..., "out": path, "err": path}
// Response (one line):     "<exit code> <threads in the parent before fork>",
//                           or "refused <threads>" if preloading started threads
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const [addonPath, bin, learnedPath] = process.argv.slice(2);
const require = createRequire(import.meta.url);
const sys = require(addonPath);
const binUrl = new URL(`file://${bin}`).href;

const threads = () => fs.readdirSync("/proc/self/task").length;
const baseThreads = threads();  // Node's own: idle in the parent, absent in a child
let loaded = 0, failed = [];
for (const [url, format] of JSON.parse(fs.readFileSync(learnedPath, "utf8"))) {
  if (url === binUrl) continue;  // the entry point runs the tool
  try {
    if (format === "builtin") require(url);
    // require() reports no format: a file it loaded is CommonJS (or JSON)
    else if (!format || format === "commonjs" || format === "json") require(fileURLToPath(url));
    else if (format === "module") await import(url);
    else continue;
    loaded++;
  } catch (e) {
    failed.push(`${url}: ${e.message.split("\n")[0]}`);
  }
}
fs.writeSync(1, `ready ${loaded} ${failed.length} ${threads()}\n`);
for (const f of failed) fs.writeSync(2, `preload failed: ${f}\n`);

sys.blocking(0);  // a pipe may come non-blocking
const buf = Buffer.alloc(1 << 16);
let pending = "";
function nextRequest() {
  for (;;) {
    const nl = pending.indexOf("\n");
    if (nl >= 0) {
      const line = pending.slice(0, nl);
      pending = pending.slice(nl + 1);
      return JSON.parse(line);
    }
    const n = fs.readSync(0, buf);
    if (n === 0) return null;
    pending += buf.toString("utf8", 0, n);
  }
}

for (let req; (req = nextRequest()) !== null;) {
  const before = threads();
  if (before > baseThreads) {  // a preloaded module started threads: a child would lack them
    fs.writeSync(1, `refused ${before}\n`);
    continue;
  }
  const pid = sys.fork();
  if (pid === 0) {
    sys.redirect(0, "/dev/null", false);
    sys.redirect(1, req.out, true);
    sys.redirect(2, req.err, true);
    process.chdir(req.cwd);
    // In place: a module may have kept a reference to the array (yargs in jest)
    process.argv.splice(0, process.argv.length, process.execPath, bin, ...req.args);
    // Once every "exit" listener ran (the tool's too), leave without Node's
    // teardown: it would wait for worker threads the child does not have.
    const emit = process.emit;
    process.emit = function (event, ...args) {
      const r = emit.call(this, event, ...args);
      if (event === "exit") sys.exit(process.exitCode ?? args[0] ?? 0);
      return r;
    };
    setImmediate(() => require("node:module").runMain(bin));
    break;  // the child leaves the loop; its event loop runs the tool
  }
  fs.writeSync(1, `${sys.wait(pid)} ${before}\n`);
}
