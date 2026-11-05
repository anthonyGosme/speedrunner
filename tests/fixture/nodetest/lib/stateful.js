// State loaded as it loads and saved at exit, as a tool with a history file.
const fs = require("fs");
const os = require("os");
const path = require("path");

const file = path.join(os.homedir(), "nodestate");
const state = { value: fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "" };
process.on("exit", () => fs.writeFileSync(file, state.value));
module.exports = state;
