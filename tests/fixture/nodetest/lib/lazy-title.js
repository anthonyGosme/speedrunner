// Sets the process title as it loads, as a CLI might: on macOS libuv does
// it through CoreFoundation, after which a forked child that sets its own
// title is killed. The daemon must not keep it.
process.title = "nodetest";
exports.title = process.title;
