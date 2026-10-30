"""Node tools, measured as panel.py measures them (20 interleaved runs,
outputs compared with the native run), in the image of bench/node/Dockerfile,
where they are installed globally.

Usage: python3 node_panel.py <speedrunner binary> <out_dir>   (env as panel.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel  # noqa: E402

panel.PATH = "/usr/local/bin:/usr/bin:/bin"
panel.SAMPLES.update({
    "sample.js": "const a = 1\nconsole.log(a)\n",
    "eslint.config.mjs": "export default [{ rules: { semi: 'error', 'no-unused-vars': 'error' } }];\n",
})
panel.EXTRA_ENV = {
    "NG_CLI_ANALYTICS": "false", "NEXT_TELEMETRY_DISABLED": "1", "VERCEL_TELEMETRY_DISABLED": "1",
    "NO_UPDATE_NOTIFIER": "1", "npm_config_update_notifier": "false",
}

V = "--version"
panel.PANEL = [
    ("node", "npm --version", ["npm", V], None, False),
    ("node", "eslint --version", ["eslint", V], None, False),
    ("node", "eslint sample.js", ["eslint", "sample.js"], "work", False),
    ("node", "prettier --check sample.js", ["prettier", "--check", "sample.js"], "work", True),
    ("node", "jest --version", ["jest", V], None, False),
    ("node", "next --version", ["next", V], None, False),
    ("node", "nest --version", ["nest", V], None, False),
    ("node", "ng version", ["ng", "version"], None, False),
    ("node", "webpack --version", ["webpack", V], None, False),
    ("node", "vite --version", ["vite", V], None, False),
    ("node", "firebase --version", ["firebase", V], None, False),
    ("node", "vercel --version", ["vercel", V], None, False),
    ("node", "tsc --version (TypeScript 7, in Go)", ["tsc", V], None, False),
]

if __name__ == "__main__":
    panel.main()
