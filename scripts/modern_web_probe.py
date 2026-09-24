#!/usr/bin/env python3
"""Crawler entry point for the modern-web (Chrome 134+) feature probe.

Collects objective, judgement-free evidence for the catalog checks that need
declarative-platform signals: `view-transitions`, `scroll-driven-animations`,
`anchored-positioning`, `scroll-state-aware-chrome`, and `physical-gestures`.

The probe expression lives in `scripts/probes/modern-web-features.js` so the same
test can be run by the model on any representative route during an atomic audit:

    node ~/.web-uplift/evidence/cli.mjs evaluate <url> \\
      --expr-file scripts/probes/modern-web-features.js \\
      --out evidence/<site>/modern-web-features.json

That direct expression is a CSS/inline-script snapshot. This runner also uses
collect_modern_web.mjs to inspect bounded, already-loaded external script bodies
via CDP in the same navigation, without re-requesting URLs. Text references are
not runtime usage, and unreadable/over-budget sources remain explicit.
It accepts a URL or site list, resumably:

    python3 scripts/modern_web_probe.py https://example.com/
    python3 scripts/modern_web_probe.py results/atomic/manifest.csv --out /tmp/modernweb

Exit code is non-zero when any target failed to produce evidence, so it can be
used as a collection gate. Absent evidence is never recorded as a pass: a failed
target is recorded with `ok: false` and a reason for the auditor to judge as
`blocked`/`not-run`.

Environment: `WEB_UPLIFT_CLI` (evidence CLI path), `PROBE_TIMEOUT` (per-target
seconds, default 180), `PROBE_WAIT` (page settle ms, default 3000).
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / 'scripts' / 'probes' / 'modern-web-features.js'
COLLECTOR = ROOT / 'scripts' / 'collect_modern_web.mjs'
CLI = Path(os.environ.get('WEB_UPLIFT_CLI', Path.home() / '.web-uplift' / 'evidence' / 'cli.mjs'))
TIMEOUT = int(os.environ.get('PROBE_TIMEOUT', '180'))
# Real sites need to settle before CSSOM/animations are meaningful.
WAIT = int(os.environ.get('PROBE_WAIT', '3000'))


def target(name: str) -> tuple[int | None, str]:
    """Accept `rank<TAB>domain`, `rank,domain`, `rank domain`, a domain, or a URL."""
    cells = name.replace('\t', ' ').replace(',', ' ').split()
    if not cells:
        return None, ''
    if len(cells) > 1 and cells[0].isdigit():
        return int(cells[0]), ' '.join(cells[1:])
    return None, ' '.join(cells)


def url_for(value: str) -> str:
    if value.startswith(('http://', 'https://', 'file://')):
        return value
    return f'https://{value}/'


RANK_HEADERS = ('position', 'rank', 'index')
TARGET_HEADERS = ('origin', 'domain', 'url', 'site')


def rows_to_targets(rows: list[list[str]]) -> list[tuple[int | None, str]]:
    """Map manifest rows to (rank, target), honouring a named header when present.

    `results/atomic/manifest.csv` is `position,origin,crux_rank_bucket`: a parser
    that ignores the header turns every row into an invalid URL.
    """
    rows = [row for row in rows if row and any(str(cell).strip() for cell in row)]
    if not rows:
        return []
    header = [str(cell).strip().lower() for cell in rows[0]]
    rank_index = next((index for index, cell in enumerate(header) if cell in RANK_HEADERS), None)
    target_index = next((index for index, cell in enumerate(header) if cell in TARGET_HEADERS), None)
    if target_index is None:
        return [target(' '.join(str(cell) for cell in row)) for row in rows]
    entries: list[tuple[int | None, str]] = []
    for row in rows[1:]:
        if target_index >= len(row):
            continue
        value = str(row[target_index]).strip()
        if not value:
            continue
        rank = None
        if rank_index is not None and rank_index < len(row):
            cell = str(row[rank_index]).strip()
            rank = int(cell) if cell.isdigit() else None
        entries.append((rank, value))
    return entries


def load_targets(source: str) -> list[tuple[int | None, str]]:
    path = Path(source)
    if not path.exists() or source.startswith(('http://', 'https://', 'file://')):
        return [target(source)]
    text = path.read_text()
    if path.suffix == '.json':
        payload = json.loads(text)
        entries = payload.get('targets', []) if isinstance(payload, dict) else payload
        return [target(str(entry.get('url') or entry.get('domain')) if isinstance(entry, dict) else str(entry)) for entry in entries]
    rows = list(csv.reader(text.splitlines())) if path.suffix == '.csv' else [line.split() for line in text.splitlines()]
    return rows_to_targets(rows)


def validate_evidence(path: Path) -> tuple[bool, str]:
    """Judge the evidence, not the exit code.

    The evidence CLI exits zero even when navigation lands on Chrome's error
    page, so a written file proves nothing. Only a probe report from a real
    document counts; anything else is missing evidence (the auditor then records
    `blocked`/`not-run`, never a pass).
    """
    try:
        report = json.loads(path.read_text())
    except Exception as exc:
        return False, f'unreadable evidence: {type(exc).__name__}: {exc}'
    if report.get('ok') is not True:
        return False, f"probe reported ok={report.get('ok')!r}"
    url = str(report.get('url') or '')
    if not url:
        return False, 'probe reported no document url'
    if url.startswith('chrome-error://') or url.startswith('about:'):
        return False, f'navigation failed: {url}'
    return True, url


def slug(value: str) -> str:
    return ''.join(character if character.isalnum() or character in '.-' else '-' for character in value.lower()).strip('-')


def kill_profile(profile: str) -> bool:
    """Kill every Chrome process holding `profile`, and remove the profile dir.

    `pkill -f "--user-data-dir=..."` does NOT work: the pattern starts with `--`,
    so pkill parses it as an option and exits 2 without killing anything, which
    left a headless Chrome (plus zygote/GPU children) orphaned for every timed-out
    target. `--` terminates option parsing and makes the pattern a pattern.
    """
    killed = subprocess.run(['pkill', '-f', '--', f'--user-data-dir={profile}'],
                            capture_output=True).returncode == 0
    shutil.rmtree(profile, ignore_errors=True)
    return killed


def evaluate_target(url: str, out: Path) -> tuple[int, str]:
    """Run the probe once, bounded and leak-free.

    A single unresponsive page must not kill the crawl: the timeout is caught,
    recorded as a failure, and the uniquely named Chrome profile from this
    invocation is killed so it cannot hold the port open (same discipline as the
    recon crawler).
    """
    command = ['node', str(COLLECTOR), url, '--harness', str(CLI),
               '--wait', str(WAIT), '--expr-file', str(PROBE), '--out', str(out)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
        return result.returncode, (result.stderr or '')[-2000:]
    except subprocess.TimeoutExpired as exc:
        def text(value):
            if isinstance(value, bytes):
                return value.decode(errors='replace')
            return value or ''
        output = text(exc.stdout) + text(exc.stderr)
        for profile in set(re.findall(r'/tmp/web-uplift-cdp-[A-Za-z0-9_-]+', output)):
            kill_profile(profile)
        return 124, f'timeout after {TIMEOUT}s'


def probe(rank: int | None, value: str, out_dir: Path) -> dict:
    url = url_for(value)
    out = out_dir / slug(value) / 'modern-web-features.json'
    if out.exists():
        valid, detail = validate_evidence(out)
        # Old CSS-only artifacts cannot stand in for the newly requested script
        # inspection. Keep published reports untouched; refresh only this output.
        if valid and json.loads(out.read_text()).get('scriptInspection', {}).get('version') == 1:
            return {'rank': rank, 'target': value, 'url': url, 'finalUrl': detail, 'out': str(out), 'ok': True, 'cached': True}
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    exit_code, stderr = evaluate_target(url, out)
    if exit_code != 0 or not out.exists():
        ok, detail = False, f'exit {exit_code}' if exit_code != 0 else 'no evidence written'
    else:
        ok, detail = validate_evidence(out)
    if not ok:
        out.write_text(json.dumps({'probe': 'modern-web-features', 'ok': False, 'url': url,
                                   'error': detail, 'exitCode': exit_code,
                                   'stderr': stderr}, indent=2) + '\n')
    return {'rank': rank, 'target': value, 'url': url, 'finalUrl': detail if ok else None, 'out': str(out),
            'ok': ok, 'failure': None if ok else detail, 'durationSeconds': round(time.time() - started, 2)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source = argv[1]
    out_dir = Path(argv[argv.index('--out') + 1]) if '--out' in argv else Path('evidence/modern-web')
    workers = int(argv[argv.index('--workers') + 1]) if '--workers' in argv else 2
    if not CLI.exists():
        print(f'web-uplift evidence CLI not found at {CLI}; set WEB_UPLIFT_CLI', file=sys.stderr)
        return 2
    if not PROBE.exists():
        print(f'probe expression not found at {PROBE}', file=sys.stderr)
        return 2

    targets = [entry for entry in load_targets(source) if entry[1]]
    if not targets:
        print(f'no targets parsed from {source}', file=sys.stderr)
        return 2
    print(f'probing {len(targets)} target(s) with {workers} worker(s) -> {out_dir}', flush=True)
    results = []
    if len(targets) == 1:
        results.append(probe(*targets[0], out_dir))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(probe, rank, value, out_dir): value for rank, value in targets}
            for future in as_completed(futures):
                item = future.result()
                results.append(item)
                detail = '' if item['ok'] else f" -- {item.get('failure')}"
                print(f"  {'ok' if item['ok'] else 'FAILED'} {item['target']} {item.get('durationSeconds', '')}s{detail}", flush=True)

    summary = {'targets': len(results), 'ok': sum(1 for item in results if item['ok']),
               'failed': sum(1 for item in results if not item['ok']), 'results': results,
               'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'note': 'Judgement-free modern-web feature signals. Absent evidence is not a pass.'}
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({key: summary[key] for key in ('targets', 'ok', 'failed')}, indent=2), flush=True)
    print(f'summary: {summary_path}', flush=True)
    return 1 if summary['failed'] else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
