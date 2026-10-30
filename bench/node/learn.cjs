// node --require learn.cjs <tool> ...: writes to $J0_LEARN the modules the
// tool loaded, in the order their loading started, with their format.
const { registerHooks } = require("node:module");
const fs = require("node:fs");
const seen = [];
registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    seen.push([url, result.format]);
    return result;
  },
});
process.on("exit", () => fs.writeFileSync(process.env.J0_LEARN, JSON.stringify(seen)));
