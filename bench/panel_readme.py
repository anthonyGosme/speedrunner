"""The README's results table, generated from a panel run (bench/panel.py),
so that it never drifts from what was measured.

Usage: python3 panel_readme.py <panel_dir> [README.md]
Prints the Markdown; with a README path, replaces what lies between the
<!-- panel:start --> and <!-- panel:end --> markers in it.
"""
import json
import re
import sys

RUNTIMES = {"python": "Python", "ruby": "Ruby", "php": "PHP"}

# Why a command gains nothing: shown under its table.
NOTES = {
    "phpstan analyse": "`phpstan analyse` spends its time in the analysis itself, not in the "
                       "interpreter start.",
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


def table(panel_dir):
    data = json.load(open(f"{panel_dir}/panel.json"))
    rows = data["results"]
    out = [f"Measured on a 2-vCPU Debian 12 VPS ({machine(panel_dir)}), median of {data['runs']} runs, "
           "warm daemon, no tool-specific setup: each command is simply run as `srr <command>`. "
           "*Identical* compares exit code, stdout and stderr with the native run.", ""]
    for key, name in RUNTIMES.items():
        group = [r for r in rows if r["runtime"] == key]
        if not group:
            continue
        group.sort(key=lambda r: -(r.get("native_ms", 0) - r.get("fast_ms", r.get("native_ms", 0))))
        out += [f"**{name}**", "",
                "| Command | Native | speedrunner | Speedup | Saved per call | Identical | Daemon memory |",
                "|---|---:|---:|---:|---:|:---:|---:|"]
        for r in group:
            if "error" in r:
                out.append(f"| `{r['command']}` | {r.get('native_ms', '—')} ms | — | — | — | {r['error'][:40]} | |")
                continue
            saved = r["native_ms"] - r["fast_ms"]
            if r.get("native_rc", 0) != 0:
                r["command"] += f" (exits {r['native_rc']} natively)"
            mem = f"{r['pss_mb']:.0f} MB" if "pss_mb" in r else ""
            out.append(f"| `{r['command']}` | {r['native_ms']:.0f} ms | {r['fast_ms']:.0f} ms | "
                       f"**x{r['speedup']:.1f}** | {saved:.0f} ms | {'✓' if r['identical'] else '✗'} | {mem} |")
        out.append("")
        notes = {text for r in group if r.get("speedup", 9) < 1.2
                 for key, text in NOTES.items() if r["command"].startswith(key)}
        out += [f"No gain: {text}" for text in sorted(notes)] + ([""] if notes else [])
    return "\n".join(out)


def main():
    text = table(sys.argv[1])
    if len(sys.argv) > 2:
        readme = open(sys.argv[2]).read()
        new = re.sub(r"(<!-- panel:start -->\n).*?(<!-- panel:end -->)", lambda m: m.group(1) + text + "\n" + m.group(2),
                     readme, flags=re.S)
        if new == readme and "<!-- panel:start -->" not in readme:
            sys.exit("no <!-- panel:start --> marker in the README")
        open(sys.argv[2], "w").write(new)
    print(text)


if __name__ == "__main__":
    main()
