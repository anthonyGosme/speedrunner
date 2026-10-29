#!/usr/bin/env bash
# The first cold run of the demo script (bench/demo/nightly.sh), as a nightly
# cron meets it once the daemons have exited: every sample in a fresh
# environment (record.sh: a learning run, a native run, then one cold run).
# The clients alternate from sample to sample, so that drift hits them alike.
#
# Usage: bench/first_cold.sh <out_dir> <samples> <label>=<speedrunner binary>...
# Writes <out_dir>/<label>-<n>/cold.cast and <out_dir>/summary.md.
set -euo pipefail

out=${1:?usage: first_cold.sh <out_dir> <samples> <label>=<binary>...}
samples=${2:?usage: first_cold.sh <out_dir> <samples> <label>=<binary>...}
shift 2
here=$(cd "$(dirname "$0")" && pwd)
# Wherever a client was built, it runs the daemons of this working copy.
export SPEEDRUNNER_DAEMON=$here/../server/speedrunner_daemon.py
mkdir -p "$out"
{
    echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "cpus: $(nproc) of $(getconf _NPROCESSORS_ONLN)"
    uname -a
} > "$out/machine.txt"

for ((i = 1; i <= samples; i++)); do
    clients=("$@")
    if ((i % 2 == 0)); then
        clients=($(printf '%s\n' "$@" | tac))
    fi
    for spec in "${clients[@]}"; do
        label=${spec%%=*} bin=${spec#*=}
        RUNS=cold "$here/demo/record.sh" "$bin" "$out/$label-$i" > /dev/null 2>&1
        echo "$label $i $(grep -o 'done in [0-9.]* s' "$out/$label-$i/cold.cast" | tail -1)"
    done
done | tee "$out/runs.txt"

python3 - "$out" <<'EOF'
import re, statistics, sys
out = sys.argv[1]
runs = {}
for line in open(f"{out}/runs.txt"):
    m = re.match(r"(\S+) \d+ done in ([\d.]+) s", line)
    if m:
        runs.setdefault(m.group(1), []).append(float(m.group(2)))
with open(f"{out}/summary.md", "w") as f:
    f.write("| Client | Runs (s) | Median | Mean |\n|---|---|---:|---:|\n")
    for label, v in runs.items():
        f.write(f"| {label} | {', '.join(f'{x:.1f}' for x in v)} | {statistics.median(v):.2f} s "
                f"| {statistics.mean(v):.2f} s |\n")
print(open(f"{out}/summary.md").read())
EOF
