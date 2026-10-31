// Wraps process.emit and process.reallyExit as signal-exit does (jest loads
// it): its own "exit" listener runs after the original emit returns.
// Transparent, as signal-exit is: it writes only when NODETEST_WRAPPER asks,
// since once learned it is preloaded for every command.
"use strict";
const emit = process.emit;
const reallyExit = process.reallyExit;
const note = (text) => process.env.NODETEST_WRAPPER && process.stdout.write(text);
process.emit = function (event, ...args) {
  const r = emit.call(this, event, ...args);
  if (event === "exit") note(`wrapper saw exit ${process.exitCode ?? args[0]}\n`);
  return r;
};
process.reallyExit = function (code) {
  note(`wrapper reallyExit ${code}\n`);
  return reallyExit.call(this, code);
};
