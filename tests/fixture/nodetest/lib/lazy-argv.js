// Reads the arguments at import: refused, run on each call instead.
"use strict";
exports.args = JSON.stringify(process.argv.slice(3));
