"""Summarize a baseline run directory into summary.md (stage 0 go/no-go).

Usage: python summarize.py <results_dir>
"""
import json
import statistics
import sys
from pathlib import Path

GO_THRESHOLD = 0.5


def ms(seconds):
    return f'{seconds * 1000:.1f}'


def main():
    out = Path(sys.argv[1])
    hf = json.loads((out / 'hyperfine.json').read_text())['results']
    by_cmd = {r['command']: r for r in hf}
    floor = next(r for r in hf if r['command'].endswith(' -c pass'))
    imp = next(r for r in hf if r['command'].endswith("'import awscli.clidriver'"))
    imp_exit = next(r for r in hf if 'os._exit' in r['command'])
    phases = [json.loads(l) for l in (out / 'phases.jsonl').read_text().splitlines()]

    lines = [
        '# Stage 0 baseline',
        '',
        f'Interpreter floor (`python -c pass`): median {ms(floor["median"])} ms '
        f'(stddev {ms(floor["stddev"])} ms).',
        '',
        f'Interpreter teardown with awscli imported: ~{ms(imp["median"] - imp_exit["median"])} ms '
        '(median of `import awscli.clidriver` minus the same followed by `os._exit(0)`).',
        '',
        '## Native wall time (hyperfine)',
        '',
        '| Command | Median (ms) | Mean ± σ (ms) | Min (ms) | Runs |',
        '|---|---|---|---|---|',
    ]
    for r in hf:
        lines.append(
            f'| `{r["command"]}` | {ms(r["median"])} | {ms(r["mean"])} ± {ms(r["stddev"])} '
            f'| {ms(r["min"])} | {len(r["times"])} |'
        )

    lines += [
        '',
        '## Phase breakdown (medians)',
        '',
        'Startup + exit = native median − in-process phases. It covers interpreter '
        'startup, site import, and interpreter teardown.',
        '',
        '| Command | Total | Startup + exit | Imports | create_clidriver | Run | '
        'Removable by import preload | + driver preload |',
        '|---|---|---|---|---|---|---|---|',
    ]
    verdicts = []
    for args in sorted({tuple(p['args']) for p in phases}, key=len):
        rows = [p for p in phases if tuple(p['args']) == args]
        imp = statistics.median(p['import_s'] for p in rows)
        drv = statistics.median(p['create_driver_s'] for p in rows)
        run = statistics.median(p['run_s'] for p in rows)
        cmd = next(c for c in by_cmd if c.endswith('/aws ' + ' '.join(args)))
        total = by_cmd[cmd]['median']
        startup = max(total - imp - drv - run, 0.0)
        removable = (startup + imp) / total
        with_driver = (startup + imp + drv) / total
        verdicts.append((' '.join(args), removable, with_driver))
        lines.append(
            f'| `aws {" ".join(args)}` | {ms(total)} | {ms(startup)} | {ms(imp)} | {ms(drv)} '
            f'| {ms(run)} | {removable:.0%} | {with_driver:.0%} |'
        )

    lines += ['', '## Go/no-go (threshold: imports + init ≥ 50 % of total)', '']
    for name, removable, with_driver in verdicts:
        lines.append(
            f'- `aws {name}`: startup + imports = {removable:.0%} '
            f'({"GO" if removable >= GO_THRESHOLD else "NO-GO"}); '
            f'including create_clidriver = {with_driver:.0%} '
            f'({"GO" if with_driver >= GO_THRESHOLD else "NO-GO"}); '
            f'max speedup if removed: x{1 / (1 - removable):.1f} / x{1 / (1 - with_driver):.1f}'
        )
    (out / 'summary.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
