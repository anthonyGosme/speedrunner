"""Build the demo page (race.html.in) from a record.sh output: the three
casts, the script, the time per tool; and check that the speedrunner runs
printed what the native run did.

Usage: python3 page.py <record_dir> <out.html>
"""
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = re.compile(r"\x1b\[2m\d+\.\ds\x1b\[0m \x1b\[1;32m❯\x1b\[0m ")
# Durations, rates and dates, which differ from run to run.
VARIES = re.compile(r"\d+(\.\d+)? ?(s|ms|seconds?|MiB/s|KiB/s)\b|\d{2}:\d{2}(\.\d{3})?"
                    r"|\d{4}-\d\d-\d\d \d\d:\d\d:\d\d|Memory: [\d.]+ MB|done in [\d.]+")


def load(path):
    lines = open(path).read().splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]


def outputs(events):
    """(command, output) per traced command, with what varies blanked."""
    text = "".join(e[2] for e in events if e[1] == "o")
    out = []
    for part in PROMPT.split(text)[1:]:
        cmd, _, body = part.partition("\r\n")
        body = VARIES.sub("<t>", body)
        if cmd.startswith("ansible"):  # hosts run in parallel: their order varies, natively too
            body = "".join(sorted(re.split(r"(?=\x1b\[0;32mweb)", body)))
        out.append((cmd, body))
    return out


def per_tool(events):
    marks = []
    for t, _, data in events:
        for m in re.finditer(r"❯\x1b\[0m (\S+)(?: (\S+))?", data):
            marks.append((t, m.group(1) if m.group(1) != "php" else f"php {m.group(2)}"))
    marks.append((events[-1][0], None))
    total, calls = defaultdict(float), defaultdict(int)
    for (t, tool), (t2, _) in zip(marks, marks[1:]):
        total[tool] += t2 - t
        calls[tool] += 1
    return total, calls


def main():
    src, dest = sys.argv[1], sys.argv[2]
    data, times = {}, {}
    for name in ("native", "cold", "warm"):
        head, events = load(os.path.join(src, f"{name}.cast"))
        data["width"], data["height"] = head["width"], head["height"]
        out = [[round(t, 4), d] for t, kind, d in events if kind == "o"]
        data[name] = {"duration": round(out[-1][0], 3), "events": out}
        times[name] = per_tool(events)
        if name != "native":
            native = outputs(load(os.path.join(src, "native.cast"))[1])
            diffs = [c for (c, a), (_, b) in zip(native, outputs(events)) if a != b]
            if diffs or len(native) != len(outputs(events)):
                sys.exit(f"{name}: output differs from native for {diffs[:5]}")
    shell = {"for", "cd", "set", "{"}
    data["tools"] = sorted(
        ({"tool": k, "calls": times["native"][1][k],
          **{n: round(times[n][0][k], 2) for n in ("native", "cold", "warm")}}
         for k in times["native"][0] if k not in shell),
        key=lambda r: r["warm"] - r["native"])
    data["script"] = open(os.path.join(HERE, "nightly.sh")).read()
    page = open(os.path.join(HERE, "race.html.in")).read()
    with open(dest, "w") as f:
        f.write(page.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")))
    print({n: data[n]["duration"] for n in ("native", "cold", "warm")})


if __name__ == "__main__":
    main()
