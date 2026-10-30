// Starts a thread at import: refused, the child would not have it.
"use strict";
const { Worker } = require("worker_threads");
const w = new Worker("require('worker_threads').parentPort.postMessage(1)", { eval: true });
w.unref();
exports.started = true;
