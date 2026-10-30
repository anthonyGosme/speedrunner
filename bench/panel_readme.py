"""The README's results table, generated from a panel run (bench/panel.py),
so that it never drifts from what was measured.

Usage: python3 panel_readme.py <panel_dir> [README.md] [<marker> <where>]
Prints the Markdown; with a README path, replaces what lies between the
<!-- panel:start --> and <!-- panel:end --> markers in it (or those of
<marker>, for a panel measured elsewhere than the VPS, described by <where>).
"""
import json
import math
import re
import statistics
import sys

RUNTIMES = {"python": "Python", "ruby": "Ruby", "php": "PHP"}

# Why a command gains nothing: shown under its table.
NOTES = {
    "phpstan analyse": "`phpstan analyse` spends its time in the analysis itself, not in the "
                       "interpreter start.",
}

# Why a command is not served at all.
NOT_SERVED = {
    "wp ": "`wp` and `drush` are not served: Composer installs them behind a shell script "
           "that runs PHP, and speedrunner follows such launchers only up to Python.",
    "drush ": "`wp` and `drush` are not served: Composer installs them behind a shell script "
              "that runs PHP, and speedrunner follows such launchers only up to Python.",
}


def machine(panel_dir):
    try:
        lines = open(f"{panel_dir}/machine.txt").read().splitlines()
    except OSError:
        return ""
    # "ruby 3.1.2p20 (2022-04-12 …)" -> "Ruby 3.1.2"
    keep = [re.sub(r"^(\S+) (\d+\.\d+\.\d+).*", r"\1 \2", l).capitalize().replace("Php", "PHP")
            for l in lines if l.startswith(("Python", "ruby", "PHP"))]
    return ", ".join(keep)


def table(panel_dir, where="a 2-vCPU Debian 12 VPS"):
    data = json.load(open(f"{panel_dir}/panel.json"))
    rows = data["results"]
    versions = machine(panel_dir)
    served = [r for r in rows if "speedup" in r]
    gains = [r["speedup"] for r in served]
    geo = math.exp(statistics.mean(math.log(g) for g in gains))
    out = [f"Measured on {where}{f' ({versions})' if versions else ''}, warm daemon, no tool-specific setup: "
           "each command is simply run as `srr <command>`. Each time is the median of "
           f"{data['runs']} runs, native and speedrunner interleaved; the speedup is the ratio of the medians, "
           "with its 95% confidence interval (bootstrap). *Identical* compares exit code, stdout and stderr "
           f"with the native run. Mean, standard deviation, min and max: [summary]({panel_dir}/summary.md); "
           f"every run: [panel.json]({panel_dir}/panel.json).",
           "",
           f"**{len(served)} commands served, {sum(r['identical'] for r in served)} identical outputs. "
           f"Speedup: geometric mean x{geo:.1f}, median x{statistics.median(gains):.1f}, "
           f"from x{min(gains):.1f} to x{max(gains):.1f}.**", ""]
    for key, name in RUNTIMES.items():
        group = [r for r in rows if r["runtime"] == key]
        if not group:
            continue
        group.sort(key=lambda r: -(r.get("native_ms", 0) - r.get("fast_ms", r.get("native_ms", 0))))
        out += [f"**{name}**", "",
                "| Command | Native | speedrunner | Speedup (95% CI) | Saved per call | Identical | Daemon memory |",
                "|---|---:|---:|---:|---:|:---:|---:|"]
        for r in group:
            if "error" in r:
                out.append(f"| `{r['command']}` | {r.get('native_ms', 0):.0f} ms | — | not served | — | — | |")
                continue
            saved = r["native_ms"] - r["fast_ms"]
            if r.get("native_rc", 0) != 0:
                r["command"] += f" (exits {r['native_rc']} natively)"
            mem = f"{r['pss_mb']:.0f} MB" if "pss_mb" in r else ""
            ci = " ({:.1f}–{:.1f})".format(*r["speedup_ci95"]) if "speedup_ci95" in r else ""
            out.append(f"| `{r['command']}` | {r['native_ms']:.0f} ms | {r['fast_ms']:.0f} ms | "
                       f"**x{r['speedup']:.1f}**{ci} | {saved:.0f} ms | {'✓' if r['identical'] else '✗'} | {mem} |")
        out.append("")
        notes = {text for r in group if r.get("speedup", 9) < 1.2
                 for key, text in NOTES.items() if r["command"].startswith(key)}
        out += [f"No gain: {text}" for text in sorted(notes)] + ([""] if notes else [])
        skipped = {text for r in group if "error" in r
                   for key, text in NOT_SERVED.items() if r["command"].startswith(key)}
        out += sorted(skipped) + ([""] if skipped else [])
    return "\n".join(out)


def main():
    marker, where = sys.argv[3:5] if len(sys.argv) > 4 else ("panel", None)
    text = table(sys.argv[1], *([where] if where else []))
    if len(sys.argv) > 2:
        readme = open(sys.argv[2]).read()
        new = re.sub(rf"(<!-- {marker}:start -->\n).*?(<!-- {marker}:end -->)",
                     lambda m: m.group(1) + text + "\n" + m.group(2), readme, flags=re.S)
        if new == readme and f"<!-- {marker}:start -->" not in readme:
            sys.exit(f"no <!-- {marker}:start --> marker in the README")
        open(sys.argv[2], "w").write(new)
    print(text)


if __name__ == "__main__":
    main()
