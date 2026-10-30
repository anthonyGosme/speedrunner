// Reads the environment at import: a client with another value runs natively.
"use strict";
exports.value = process.env.NODETEST_IMPORT ?? "<unset>";
