// Looks for a flag at import, as supports-color does: a client passing it
// runs natively.
"use strict";
exports.shout = process.argv.includes("--shout");
