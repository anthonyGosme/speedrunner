// As supports-color does at import: a terminal, and no --no-color flag.
// Loaded by bin/nodetty's top level, so preloaded with its daemon.
"use strict";
const tty = require("tty");
exports.enabled = tty.isatty(1) && process.argv.indexOf("--no-color") === -1;
