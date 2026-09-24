#!/usr/bin/env python3
"""Guide-coverage analysis for the pinned principles.json catalog.

`principles.json` declares `appliesAnalysis: "docs/principles-analysis.md"`; this
script produces and verifies the numbers in that document, so the provenance
claims are checkable instead of asserted.

    python3 scripts/analyze_guide_coverage.py                 # print the analysis
    python3 scripts/analyze_guide_coverage.py --check docs/principles-analysis.md
    python3 scripts/analyze_guide_coverage.py --write docs/principles-analysis.md

Pack-side facts need an extracted npm pack (the guides are not vendored here):

    npm pack modern-web-guidance@0.0.172 && tar xzf modern-web-guidance-0.0.172.tgz -C pinned
    npm pack modern-web-guidance@0.0.190 && tar xzf modern-web-guidance-0.0.190.tgz -C latest
    python3 scripts/analyze_guide_coverage.py --guides-dir pinned --guides-dir latest \\
        --write docs/principles-analysis.md

Exit codes: 0 on success, 1 when --check finds the document out of date, 2 on bad
usage. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / 'principles.json'
BASELINE_MARKER = 'guide-coverage-baseline'
BEGIN = '<!-- BEGIN GENERATED guide-coverage -->'
END = '<!-- END GENERATED guide-coverage -->'
SLUG = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


def load_catalog(path: Path = CATALOG):
    raw = path.read_bytes()
    return json.loads(raw), f"sha256:{hashlib.sha256(raw).hexdigest()}"


def classify(guides):
    """Split a check's `guides` array into guide slugs and free-text search phrases."""
    slugs, phrases = [], []
    for entry in guides or []:
        (phrases if (' ' in entry or not SLUG.match(entry)) else slugs).append(entry)
    return slugs, phrases


def analyse(catalog):
    rows = []
    slug_refs = {}
    for principle in catalog['principles']:
        for check in principle['checks']:
            slugs, phrases = classify(check.get('guides'))
            for slug in slugs:
                slug_refs.setdefault(slug, []).append(f"{principle['id']}/{check['id']}")
            rows.append({
                'principle': principle['id'],
                'check': check['id'],
                'slugs': slugs,
                'phrases': phrases,
            })
    return rows, slug_refs


def guide_slugs(directory: Path):
    """Accept a guides/ directory or an extracted package root."""
    if not directory.exists():
        raise SystemExit(f'guides directory not found: {directory}')
    if directory.name != 'guides':
        nested = sorted(directory.rglob('guides'))
        if nested:
            directory = nested[0]
    return {path.stem: path for path in directory.rglob('*.md')}


def facts(catalog, catalog_checksum, rows, slug_refs, guide_dirs):
    referenced = set(slug_refs)
    data = {
        'catalogVersion': catalog['guidanceCatalogVersion'],
        'catalogChecksum': catalog_checksum,
        'appliesAnalysis': catalog['appliesAnalysis'],
        'principles': len(catalog['principles']),
        'checks': len(rows),
        'referencedSlugs': len(referenced),
        'searchPhrases': sum(len(row['phrases']) for row in rows),
        'checksWithoutReferencedSlug': sorted(f"{row['principle']}/{row['check']}" for row in rows if not row['slugs']),
    }
    if guide_dirs:
        pinned, latest = guide_dirs[0], guide_dirs[-1]
        data['pinnedGuides'] = len(pinned)
        data['latestGuides'] = len(latest)
        data['newGuides'] = sorted(set(latest) - set(pinned))
        data['referencedAbsentFromLatest'] = sorted(referenced - set(latest))
        data['latestUnreferenced'] = sorted(set(latest) - referenced)
        data['pinnedUnreferenced'] = sorted(set(pinned) - referenced)
    return data


def coverage_map(catalog, rows):
    lines = ['| Principle | Check | Guides referenced |', '| --- | --- | --- |']
    for principle in catalog['principles']:
        for row in [r for r in rows if r['principle'] == principle['id']]:
            slugs = ', '.join(f'`{slug}`' for slug in row['slugs']) or '— (search phrase only)'
            lines.append(f"| `{principle['id']}` | `{row['check']}` | {slugs} |")
    return lines


def render(catalog, data, rows):
    out = [BEGIN, '', '<!-- ' + BASELINE_MARKER, json.dumps(data, indent=2, sort_keys=True), '-->', '']
    out += ['## Catalog-side facts', '',
            f"- Catalog pin: `{data['catalogVersion']}`",
            f"- Catalog checksum (`sha256` of `principles.json` bytes): `{data['catalogChecksum']}`",
            f"- Principles: **{data['principles']}** · checks: **{data['checks']}**",
            f"- Distinct guide slugs referenced: **{data['referencedSlugs']}**",
            f"- Free-text Modern Web Guidance search phrases: **{data['searchPhrases']}** (one per check)",
            f"- Checks that reference no guide slug: **{len(data['checksWithoutReferencedSlug'])}**",
            '']
    if 'latestGuides' in data:
        out += ['## Package-side facts', '',
                f"- Guides shipped in the pinned pack: **{data['pinnedGuides']}**",
                f"- Guides shipped in the latest pack: **{data['latestGuides']}**",
                f"- New guides in the latest pack: **{len(data['newGuides'])}**",
                f"- New guides no check references: **{len(data['latestUnreferenced'])}**",
                f"- Referenced slugs absent from the latest pack: **{len(data['referencedAbsentFromLatest'])}**"
                + (f" ({', '.join(f'`{s}`' for s in data['referencedAbsentFromLatest'])})" if data['referencedAbsentFromLatest'] else ''),
                '']
        new_referenced = sorted(set(data['newGuides']) - set(data['latestUnreferenced']))
        if new_referenced:
            out += [f"- New in the latest pack **and already referenced** by the pinned catalog: "
                    + ', '.join(f'`{s}`' for s in new_referenced), '']
    out += ['## Coverage map', ''] + coverage_map(catalog, rows) + ['']
    if data['checksWithoutReferencedSlug']:
        out += ['Checks carrying only a search phrase (no resolvable guide slug):', '']
        out += [f"- `{check}`" for check in data['checksWithoutReferencedSlug']]
        out += ['']
    if 'latestUnreferenced' in data:
        out += ['Guides in the latest pack that no check references:', '']
        out += [f"- `{slug}`" for slug in data['latestUnreferenced']] or ['- none']
        out += ['']
    out += [END]
    return '\n'.join(out)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--catalog', default=str(CATALOG), metavar='PATH',
                        help='catalog to analyse (default: principles.json at the repo root)')
    parser.add_argument('--guides-dir', action='append', default=[], metavar='DIR',
                        help='extracted modern-web-guidance pack (repeat: pinned, then latest)')
    parser.add_argument('--write', metavar='PATH', help='splice the generated section into this document')
    parser.add_argument('--check', metavar='PATH', help='verify this document against the catalog')
    args = parser.parse_args(argv[1:])

    catalog, checksum = load_catalog(Path(args.catalog))
    rows, slug_refs = analyse(catalog)
    guide_dirs = [guide_slugs(Path(directory)) for directory in args.guides_dir]
    data = facts(catalog, checksum, rows, slug_refs, guide_dirs)
    block = render(catalog, data, rows)

    if args.write:
        document = Path(args.write)
        text = document.read_text() if document.exists() else "# Principles catalog analysis\n"
        if BEGIN in text and END in text:
            text = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
        else:
            text = text.rstrip() + '\n\n' + block + '\n'
        document.write_text(text)
        print(f'wrote generated section to {document}')

    if args.check:
        text = Path(args.check).read_text()
        if BEGIN not in text or END not in text:
            print(f'{args.check}: generated section markers not found', file=sys.stderr)
            return 1
        section = text[text.index(BEGIN):text.index(END)]
        match = re.search(r'<!-- ' + BASELINE_MARKER + r'\s*(\{.*?\})\s*-->', section, re.S)
        if not match:
            print(f'{args.check}: baseline block not found in the generated section', file=sys.stderr)
            return 1
        recorded = json.loads(match.group(1))
        stale = {key: (recorded.get(key), value) for key, value in data.items() if recorded.get(key) != value}
        if stale:
            print(f'{args.check}: OUT OF DATE', file=sys.stderr)
            for key, (was, now) in stale.items():
                print(f'  {key}: document says {was!r}; catalog says {now!r}', file=sys.stderr)
            return 1
        print(f'{args.check}: OK ({data["referencedSlugs"]} referenced slugs across {data["checks"]} checks)')
    elif not args.write:
        print(json.dumps({key: value for key, value in data.items() if key != 'checksWithoutReferencedSlug'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
