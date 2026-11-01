// macOS (M3) go/no-go: fork a preloaded Node, run JavaScript in each child
// (JSON, a hash, zlib, GC, then a timer and asynchronous I/O through the
// thread pool, after fork); count children that exit 0.
//
// Usage: node --single-threaded --v8-pool-size=1 --no-lazy --expose-gc \
//            nodefork.js <built speedrunner_node.c> [N]
const fs = require("fs"), crypto = require("crypto"), zlib = require("zlib"), path = require("path");
const sys = require(path.resolve(process.argv[2]));
for (let i = 0; i < 2e5; i++) JSON.parse(JSON.stringify({ i, s: "x".repeat(i % 50) }));
const N = Number(process.argv[3] || 200);
const ok = { exit0: 0 }, bad = {};
function child() {
  let s = 0;
  for (let i = 0; i < 1e5; i++) s += JSON.stringify({ i }).length;
  crypto.createHash("sha256").update("x" + s).digest("hex");
  zlib.gzipSync(Buffer.alloc(100000, 1));
  if (global.gc) gc();
  setTimeout(() => fs.readFile(__filename, (e, d) => // the thread pool, after fork
    zlib.gzip(d, (e2, z) => sys.exit(!e && !e2 && z.length ? 0 : 3))), 1);
}
const pids = [];
for (let i = 0; i < N; i++) {
  const pid = sys.fork();
  if (pid === 0) { child(); return; }
  pids.push(pid);
  const sab = new Int32Array(new SharedArrayBuffer(4));
  for (;;) { const r = sys.reap(); if (r.length) { const k = r[1] ? `signal ${r[2]}` : `exit ${r[2]}`; if (k === "exit 0") ok.exit0++; else bad[k] = (bad[k] || 0) + 1; break; } Atomics.wait(sab, 0, 0, 2); }
}
console.log(JSON.stringify({ node: process.version, threads: sys.threads().length, ok, bad }));
