"""The README's results, generated from panel runs (bench/panel.py and the
panels built on it), so that they never drift from what was measured: one
table per language, every command of every panel in it.

Usage: python3 panel_readme.py <main_dir> [<other_dir>...] [--readme README.md]
<main_dir> is the panel measured on the VPS; the others, measured on a
laptop, have their commands marked ¹. Prints the Markdown; with --readme,
replaces what lies between <!-- panel:start --> and <!-- panel:end --> in it.
"""
import json
import math
import re
import statistics
import sys

RUNTIMES = {"python": "Python", "ruby": "Ruby", "php": "PHP", "node": "Node"}
MAIN = "a 2-vCPU Debian 12 VPS"
OTHER = "a laptop (Intel i7-9750H), in a Debian 12 container"

# Why a command gains nothing: shown under its table.
NOTES = {
    "phpstan analyse": "`phpstan analyse` spends its time in the analysis itself, not in the "
                       "interpreter start.",
    "nest --version": "`nest --version`: most of what it loads fails the daemon's checks (modules that "
                      "start threads or read the command line as they load), and loads again on every run.",
}

# Why a command is not served at all.
NOT_SERVED = {
    "wp ": "`wp` and `drush` are not served: Composer installs them behind a shell script "
           "that runs PHP, and speedrunner follows such launchers only up to Python.",
    "drush ": "`wp` and `drush` are not served: Composer installs them behind a shell script "
              "that runs PHP, and speedrunner follows such launchers only up to Python.",
    "vite ": "`vite` was not served when measured: its bundler starts threads as it loads, the daemon "
             "refuses those modules, and it was restarting, after one of them. Served, it would gain "
             "nothing: what is refused loads again on every run.",
}


def versions(panel_dirs):
    """"Python 3.11.2, Ruby 3.1.2, PHP 8.2.35, Node 24.22.2", from machine.txt."""
    seen = {}
    for d in panel_dirs:
        try:
            lines = open(f"{d}/machine.txt").read().splitlines()
        except OSError:
            continue
        for l in lines:
            m = re.match(r"^(Python|ruby|PHP) (\d+\.\d+\.\d+)", l) or re.match(r"^(v)(\d+\.\d+\.\d+)$", l)
            if m:
                name = {"ruby": "Ruby", "v": "Node"}.get(m.group(1), m.group(1))
                seen.setdefault(name, m.group(2))
    return ", ".join(f"{k} {v}" for k, v in seen.items())


def row(r, mark):
    name = f"`{r['command']}`{mark}"
    if "error" in r:
        return f"| {name} | {r.get('native_ms', 0):.0f} ms | — | not served | — | — | |"
    if r.get("native_rc", 0) != 0:
        name = f"`{r['command']}` (exits {r['native_rc']} natively){mark}"
    mem = f"{r['pss_mb']:.0f} MB" if "pss_mb" in r else ""
    ci = " ({:.1f}–{:.1f})".format(*r["speedup_ci95"]) if "speedup_ci95" in r else ""
    return (f"| {name} | {r['native_ms']:.0f} ms | {r['fast_ms']:.0f} ms | **x{r['speedup']:.1f}**{ci} | "
            f"{r['native_ms'] - r['fast_ms']:.0f} ms | {'✓' if r['identical'] else '✗'} | {mem} |")


def tables(main_dir, other_dirs):
    rows, runs = [], set()
    for d in [main_dir, *other_dirs]:
        data = json.load(open(f"{d}/panel.json"))
        runs.add(data["runs"])
        rows += [(r, "" if d == main_dir else " ¹") for r in data["results"]]
    served = [r for r, _ in rows if "speedup" in r]
    gains = [r["speedup"] for r in served]
    geo = math.exp(statistics.mean(math.log(g) for g in gains))
    per = ", ".join(f"{sum(r['runtime'] == k for r in served)} {name}" for k, name in RUNTIMES.items()
                    if any(r["runtime"] == k for r in served))
    links = "; ".join(f"[{d.rstrip('/').split('/')[-1]}]({d}/summary.md)" for d in [main_dir, *other_dirs])
    out = [f"**{len(served)} commands served ({per}), {sum(r['identical'] for r in served)} with an output "
           f"identical to the native run. Speedup: geometric mean x{geo:.1f}, median "
           f"x{statistics.median(gains):.1f}; {sum(g >= 3 for g in gains)} at x3 or more, "
           f"{sum(g >= 10 for g in gains)} at x10 or more.**", "",
           f"Measured on {MAIN}, or, for the commands marked ¹, on {OTHER} ({versions([main_dir, *other_dirs])}). "
           "Warm daemon, no tool-specific setup: each command is simply run as `srr <command>`. Each time is the "
           f"median of {'/'.join(map(str, sorted(runs)))} runs, native and speedrunner interleaved; the speedup is "
           "the ratio of the medians, with its 95% confidence interval (bootstrap). *Identical* compares exit "
           "code, stdout and stderr with the native run. Mean, standard deviation, min, max and every run: "
           f"{links}.", ""]
    for key, name in RUNTIMES.items():
        group = [(r, mark) for r, mark in rows if r["runtime"] == key]
        if not group:
            continue
        group.sort(key=lambda rm: -rm[0].get("speedup", 0))
        out += [f"**{name}**", "",
                "| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |",
                "|---|---:|---:|---:|---:|:---:|---:|"]
        out += [row(r, mark) for r, mark in group] + [""]
        notes = {text for r, _ in group if r.get("speedup", 9) < 1.2
                 for k, text in NOTES.items() if r["command"].startswith(k)}
        skipped = {text for r, _ in group if "error" in r
                   for k, text in NOT_SERVED.items() if r["command"].startswith(k)}
        out += [f"No gain: {t}" for t in sorted(notes)] + sorted(skipped)
        if notes or skipped:
            out.append("")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    readme = None
    if "--readme" in args:
        i = args.index("--readme")
        readme = args[i + 1]
        del args[i:i + 2]
    text = tables(args[0], args[1:])
    if readme:
        content = open(readme).read()
        new = re.sub(r"(<!-- panel:start -->\n).*?(<!-- panel:end -->)",
                     lambda m: m.group(1) + text + "\n" + m.group(2), content, flags=re.S)
        if "<!-- panel:start -->" not in content:
            sys.exit("no <!-- panel:start --> marker in the README")
        open(readme, "w").write(new)
    print(text)


if __name__ == "__main__":
    main()
