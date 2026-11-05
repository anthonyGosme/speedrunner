// A handler set as it loads, as a tool that cleans up on SIGTERM.
process.on("SIGTERM", () => {
  process.stdout.write("clean up\n");
  process.exit(42);
});
