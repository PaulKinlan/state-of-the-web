#!/usr/bin/env python3
"""Reconcile a finished bounded atomic run into the publishable top-1,000 dataset.

The canonical reports are byte-for-byte copies of the retained latest report for
all 1,000 manifest origins. Raw browser artifacts stay under the local run tree;
the inventory records their exact roots and hashes the published reports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SITES = 1000
VALID_CHECK_STATUSES = {
    "pass", "issues", "not-applicable", "opted-out", "blocked", "not-run"
}
DISPOSITION_MAP = {
    "complete": "complete",
    "blocked-after-retries": "exhaustedBlocked",
    "partial-after-retries": "exhaustedPartial",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_origin(value: str) -> str:
    """Compare root origins without treating a trailing slash as meaningful."""
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, parsed.fragment))


def expected_catalog(catalog: dict) -> tuple[list[str], set[tuple[str, str]], dict[str, dict]]:
    principle_ids = [principle["id"] for principle in catalog["principles"]]
    principle_map = {principle["id"]: principle for principle in catalog["principles"]}
    pairs = {
        (principle["id"], check["id"])
        for principle in catalog["principles"]
        for check in principle.get("checks", [])
    }
    return principle_ids, pairs, principle_map


def derive_principle_status(rows: list[dict]) -> str:
    statuses = [row["status"] for row in rows]
    if "issues" in statuses:
        return "issues"
    if any(status in {"blocked", "not-run"} for status in statuses):
        return "incomplete"
    if statuses and all(status == "not-applicable" for status in statuses):
        return "not-applicable"
    if statuses and all(status == "opted-out" for status in statuses):
        return "opted-out"
    if statuses and all(status in {"pass", "not-applicable", "opted-out"} for status in statuses) and "pass" in statuses:
        return "pass"
    return "incomplete"


def validate_artifact_files(report: dict, evidence_root: Path) -> list[str]:
    """Fail closed when a declared artifact path does not exist beneath evidence_root."""
    errors: list[str] = []
    resolved_root = evidence_root.resolve()
    if not resolved_root.is_dir():
        return [f"evidence root is not a directory: {evidence_root}"]
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), list) else []
    for index, artifact in enumerate(artifacts):
        declared = artifact.get("path") if isinstance(artifact, dict) else None
        if not isinstance(declared, str) or not declared.strip():
            errors.append(f"artifacts[{index}] has no valid path")
            continue
        relative = Path(declared)
        if relative.is_absolute():
            errors.append(f"artifacts[{index}] path is absolute: {declared}")
            continue
        resolved = (resolved_root / relative).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            errors.append(f"artifacts[{index}] path escapes evidence root: {declared}")
            continue
        if not resolved.exists():
            errors.append(f"artifacts[{index}] declared path is missing: {declared}")
    return errors


def validate_report(
    report: dict,
    catalog: dict,
    catalog_checksum: str,
    evidence_root: Path | None = None,
) -> dict:
    """Validate exact rows, outcomes, references, coverage, scoring, and local artifacts."""
    errors: list[str] = []
    principle_ids, pairs, principle_map = expected_catalog(catalog)
    rows = report.get("checkOutcomes")
    if not isinstance(rows, list):
        return {"errors": ["checkOutcomes must be an array"]}

    actual_pairs = [(row.get("principleId"), row.get("checkId")) for row in rows]
    occurrences = Counter(actual_pairs)
    missing = pairs - set(actual_pairs)
    unknown = set(actual_pairs) - pairs
    duplicates = sum(max(0, count - 1) for count in occurrences.values())
    if missing:
        errors.append(f"missing {len(missing)} catalog checks")
    if unknown:
        errors.append(f"contains {len(unknown)} unknown checks")
    if duplicates:
        errors.append(f"contains {duplicates} duplicate checks")

    paths = report.get("paths") if isinstance(report.get("paths"), list) else []
    path_ids = {path.get("id") for path in paths}
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), list) else []
    artifact_paths = {artifact.get("path") for artifact in artifacts}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    finding_ids = {finding.get("id") for finding in findings}
    if not report.get("evidenceUsed"):
        errors.append("evidenceUsed is empty")
    if not paths:
        errors.append("paths is empty")

    by_principle: dict[str, list[dict]] = defaultdict(list)
    status_counts = Counter()
    for index, row in enumerate(rows):
        pair = (row.get("principleId"), row.get("checkId"))
        status = row.get("status")
        if pair in pairs:
            status_counts[status] += 1
            by_principle[pair[0]].append(row)
        if status not in VALID_CHECK_STATUSES:
            errors.append(f"checkOutcomes[{index}] invalid status {status!r}")
        if row.get("confidence") not in {"low", "medium", "high"}:
            errors.append(f"checkOutcomes[{index}] invalid confidence")
        if not str(row.get("method") or "").strip():
            errors.append(f"checkOutcomes[{index}] has no method")
        if status in {"pass", "issues"}:
            if not str(row.get("evidence") or "").strip():
                errors.append(f"checkOutcomes[{index}] {status} has no evidence")
            if not (row.get("pathIds") or row.get("artifacts")):
                errors.append(f"checkOutcomes[{index}] {status} has no evidence reference")
        if status in {"not-applicable", "opted-out", "blocked", "not-run"} and not str(row.get("reason") or "").strip():
            errors.append(f"checkOutcomes[{index}] {status} has no reason")
        for path_id in row.get("pathIds") or []:
            if path_id not in path_ids:
                errors.append(f"checkOutcomes[{index}] unknown pathId {path_id}")
        for artifact_path in row.get("artifacts") or []:
            if artifact_path not in artifact_paths:
                errors.append(f"checkOutcomes[{index}] unknown artifact {artifact_path}")
        if status == "issues" and not row.get("findingIds"):
            errors.append(f"checkOutcomes[{index}] issues has no findingIds")
        for finding_id in row.get("findingIds") or []:
            if finding_id not in finding_ids:
                errors.append(f"checkOutcomes[{index}] unknown findingId {finding_id}")

    outcomes = report.get("principleOutcomes")
    if not isinstance(outcomes, list):
        errors.append("principleOutcomes must be an array")
        outcomes = []
    outcome_occurrences = Counter(outcome.get("principleId") for outcome in outcomes)
    if set(outcome_occurrences) != set(principle_ids):
        errors.append("principleOutcomes do not exactly match catalog principles")
    if any(count != 1 for count in outcome_occurrences.values()):
        errors.append("principleOutcomes contain duplicates")
    for outcome in outcomes:
        principle_id = outcome.get("principleId")
        if principle_id not in principle_map:
            continue
        derived = derive_principle_status(by_principle[principle_id])
        if outcome.get("status") != derived:
            errors.append(f"{principle_id} outcome {outcome.get('status')!r}, derived {derived!r}")
        expectation = principle_map[principle_id].get("applicability", {}).get("expectation")
        if outcome.get("expectation") != expectation:
            errors.append(f"{principle_id} expectation mismatch")
        if derived in {"incomplete", "not-applicable", "opted-out"} and not str(outcome.get("reason") or "").strip():
            errors.append(f"{principle_id} {derived} has no reason")

    judged = sum(status_counts[status] for status in ("pass", "issues", "not-applicable", "opted-out"))
    blocked = status_counts["blocked"]
    not_run = status_counts["not-run"]
    complete = not missing and not unknown and not duplicates and blocked == 0 and not_run == 0 and len(rows) == len(pairs)
    expected_coverage = {
        "catalogVersion": catalog.get("guidanceCatalogVersion") or catalog.get("version") or "unversioned",
        "catalogChecksum": f"sha256:{catalog_checksum}",
        "expected": len(pairs),
        "recorded": len(rows),
        "judged": judged,
        "blocked": blocked,
        "notRun": not_run,
        "missing": len(missing),
        "unknown": len(unknown),
        "duplicates": duplicates,
        "complete": complete,
    }
    if report.get("coverage") != expected_coverage:
        errors.append("coverage object does not match derived coverage")
    if complete and report.get("status") != "completed":
        errors.append("complete report status is not completed")
    if not complete and report.get("status") == "completed":
        errors.append("incomplete report status is completed")
    if not complete and ("overallScore" in report or "score" in report):
        errors.append("incomplete report publishes a score")
    if evidence_root is not None:
        errors.extend(validate_artifact_files(report, evidence_root))
    return {"errors": errors, "coverage": expected_coverage, "statusCounts": dict(status_counts)}


def disposition_for(coverage: dict) -> str:
    if coverage.get("complete") is True:
        return "complete"
    if coverage.get("judged") == 0 and coverage.get("blocked") == coverage.get("expected"):
        return "exhaustedBlocked"
    return "exhaustedPartial"


def status_label(value: str) -> str:
    return {
        "complete": "Coverage complete",
        "exhaustedBlocked": "Blocked after retries",
        "exhaustedPartial": "Partial after retries",
    }[value]


def status_class(value: str) -> str:
    return {"complete": "complete", "exhaustedBlocked": "blocked", "exhaustedPartial": "partial"}[value]


STYLE = """
:root{color-scheme:light dark;--text:#171612;--bg:#fdfcf8;--surface:#f0eee6;--border:#d8d4ca;--muted:#625e56;--accent:#4b3aff;--good:#147a32;--bad:#b42318;--warn:#9a6500}
@media(prefers-color-scheme:dark){:root{--text:#eeeae2;--bg:#1c1a17;--surface:#2a2723;--border:#484139;--muted:#b7aea4;--accent:#9dc1ff;--good:#75d995;--bad:#ff8a80;--warn:#f2c14e}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
*{box-sizing:border-box}body{margin:auto;padding:1rem;max-width:76rem;background:var(--bg);color:var(--text);font:1rem/1.55 system-ui,sans-serif}h1,h2{font-family:Georgia,serif;font-weight:400;line-height:1.2}h1{font-size:clamp(2rem,6vw,3.4rem);margin:.6rem 0}h2{margin:2rem 0 .6rem}a{color:var(--accent)}code{overflow-wrap:anywhere}.lede,.note{color:var(--muted);max-width:78ch}.notice{border-inline-start:.3rem solid var(--warn);background:var(--surface);padding:1rem;margin:1rem 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.7rem;margin:1rem 0}.card{background:var(--surface);border:1px solid var(--border);border-radius:.5rem;padding:1rem}.label{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}.value{font-size:1.7rem;font-weight:750;font-variant-numeric:tabular-nums}.controls{display:flex;gap:.6rem;flex-wrap:wrap;margin:.8rem 0}input,select{font:inherit;padding:.45rem .6rem;border:1px solid var(--border);border-radius:.35rem;background:var(--bg);color:var(--text)}input{flex:1;min-width:14rem}.table-wrap{overflow:auto;border:1px solid var(--border);border-radius:.45rem}table{width:100%;border-collapse:collapse;background:var(--bg)}th,td{padding:.55rem .65rem;border-bottom:1px solid var(--border);text-align:left;vertical-align:top;font-size:.82rem}th{background:var(--surface);font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}.num{text-align:right;font-variant-numeric:tabular-nums}.status{font-weight:750}.status.complete,.check.pass{color:var(--good)}.status.blocked,.check.blocked,.check.issues{color:var(--bad)}.status.partial,.check.not-run{color:var(--warn)}.check.not-applicable,.check.opted-out{color:var(--muted)}.hidden{display:none}.summary{max-width:50ch;color:var(--muted)}.check-detail{min-width:25rem}.back{font-size:.85rem}.provenance{font-size:.8rem;background:var(--surface);padding:1rem;border:1px solid var(--border);border-radius:.4rem}.finding{border-inline-start:3px solid var(--bad);padding:.4rem .6rem;margin:.35rem 0;background:var(--surface)}footer{padding:2rem 0;color:var(--muted);font-size:.8rem}
"""


def render_index(inventory: dict) -> str:
    counts = inventory["counts"]
    totals = inventory["checkOutcomes"]
    rows = []
    for target in inventory["targets"]:
        disposition = target["disposition"]
        coverage = target["coverage"]
        rows.append(
            f'<tr data-search="{html.escape((target["origin"] + " " + disposition).lower())}" data-status="{disposition}">'
            f'<td class="num">{target["position"]}</td>'
            f'<td><a href="sites/{html.escape(target["page"])}">{html.escape(target["origin"])}</a></td>'
            f'<td><span class="status {status_class(disposition)}">{status_label(disposition)}</span></td>'
            f'<td class="num">{target["attempts"]}</td><td class="num">{coverage["judged"]}</td>'
            f'<td class="num">{coverage["blocked"]}</td><td class="num">{coverage["notRun"]}</td></tr>'
        )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><link rel="icon" href="favicon.svg" type="image/svg+xml"><meta name="description" content="Final bounded-run inventory for 1,000 CrUX origins: 705 complete, 257 blocked after retries, and 38 partial after retries."><title>State of the Web — final top-1,000 atomic inventory</title><style>{STYLE}</style></head><body>
<header><p class="note">Final bounded-run publication · {html.escape(inventory["runId"])}</p><h1>State of the Web</h1><p class="lede">An exact inventory of 1,000 origins from the CrUX global rank=1,000 bucket, audited against 17 principles and 58 atomic checks. CrUX does not order origins within this bucket; “position” preserves source-file order and is not an exact popularity rank.</p></header>
<div class="notice"><strong>The run is finished; coverage is not universal.</strong> Blocked and partial outcomes remain explicit and unscored. Outcome percentages below describe only the 705 coverage-complete reports and must not be generalized to blocked or partial targets.</div>
<section><h2>Final disposition</h2><div class="cards"><div class="card"><div class="label">Manifest origins</div><div class="value">1,000</div><div class="note">Exactly once</div></div><div class="card"><div class="label">Coverage complete</div><div class="value">{counts['complete']}</div><div class="note">{counts['complete'] * 58:,} fully judged checks</div></div><div class="card"><div class="label">Blocked after retries</div><div class="value">{counts['exhaustedBlocked']}</div><div class="note">No inferred passes</div></div><div class="card"><div class="label">Partial after retries</div><div class="value">{counts['exhaustedPartial']}</div><div class="note">Measured rows retained</div></div></div>
<p class="note">All 58,000 manifest check rows are recorded: {totals['judged']:,} judged, {totals['blocked']:,} blocked, and {totals['notRun']:,} not run. Retry eligible: 0; queued: 0; invalid: 0.</p></section>
<section><h2>Inventory</h2><div class="controls"><input id="search" type="search" placeholder="Search origin or disposition"><select id="filter"><option value="">All 1,000 targets</option><option value="complete">Coverage complete</option><option value="exhaustedBlocked">Blocked after retries</option><option value="exhaustedPartial">Partial after retries</option></select></div><p class="note"><span id="shown">1,000</span> targets shown. Each detail page links the byte-identical canonical report and records its SHA-256 and local evidence provenance.</p><div class="table-wrap"><table><thead><tr><th class="num">Position</th><th>Origin</th><th>Disposition</th><th class="num">Attempts</th><th class="num">Judged</th><th class="num">Blocked</th><th class="num">Not run</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section><h2>Reproducibility</h2><p class="provenance">Manifest SHA-256: <code>{inventory['manifest']['sha256']}</code><br>Catalog: <code>{html.escape(inventory['catalog']['version'])}</code>, SHA-256 <code>{inventory['catalog']['sha256']}</code><br>Machine inventory: <a href="results/atomic/inventory.json">results/atomic/inventory.json</a><br>Final summary: <a href="atomic-checkpoint.json">atomic-checkpoint.json</a><br>Method and validation commands: <a href="README.md">README.md</a></p></section>
<footer>State of the Web · <a href="https://github.com/PaulKinlan/state-of-the-web">source</a></footer><script>const rows=[...document.querySelectorAll('tbody tr')],search=document.querySelector('#search'),filter=document.querySelector('#filter');function render(){{const q=search.value.trim().toLowerCase(),f=filter.value;let shown=0;for(const row of rows){{const hide=(q&&!row.dataset.search.includes(q))||(f&&row.dataset.status!==f);row.classList.toggle('hidden',hide);if(!hide)shown++}}document.querySelector('#shown').textContent=shown.toLocaleString()}}search.addEventListener('input',render);filter.addEventListener('change',render);</script></body></html>'''


def render_site(target: dict, report: dict, catalog: dict) -> str:
    coverage = target["coverage"]
    finding_map = {finding.get("id"): finding for finding in report.get("findings", [])}
    check_rows = []
    for row in report["checkOutcomes"]:
        supporting = []
        for finding_id in row.get("findingIds") or []:
            finding = finding_map.get(finding_id, {})
            supporting.append(f'<div class="finding"><strong>{html.escape(finding_id)}</strong> {html.escape(str(finding.get("severity") or ""))}: {html.escape(str(finding.get("summary") or ""))}</div>')
        detail = row.get("evidence") or row.get("reason") or ""
        check_rows.append(
            f'<tr><td><a href="../principles/{html.escape(row["principleId"])}.html">{html.escape(row["principleId"])}</a><br><code>{html.escape(row["checkId"])}</code></td>'
            f'<td><span class="check {html.escape(row["status"])}">{html.escape(row["status"])}</span></td>'
            f'<td>{html.escape(str(row.get("confidence") or "—"))}</td>'
            f'<td class="check-detail">{html.escape(str(detail))}{"".join(supporting)}</td></tr>'
        )
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><meta name="description" content="Atomic audit disposition and 58-check record for {html.escape(target['origin'])}."><title>{html.escape(target['origin'])} — State of the Web</title><style>{STYLE}</style></head><body><nav><a class="back" href="../index.html">← All 1,000 targets</a></nav><main><header><p class="note">Manifest position {target['position']} · CrUX rank bucket {target['cruxRankBucket']}</p><h1>{html.escape(target['origin'])}</h1><p><span class="status {status_class(target['disposition'])}">{status_label(target['disposition'])}</span></p><p class="lede">{html.escape(str(target.get('statusDetail') or 'No status detail recorded.'))}</p></header>
<div class="cards"><div class="card"><div class="label">Attempts</div><div class="value">{target['attempts']} / {target['maxAttempts']}</div></div><div class="card"><div class="label">Judged checks</div><div class="value">{coverage['judged']} / 58</div></div><div class="card"><div class="label">Blocked</div><div class="value">{coverage['blocked']}</div></div><div class="card"><div class="label">Not run</div><div class="value">{coverage['notRun']}</div></div></div>
<div class="notice">This report has no published overall score. Blocked and not-run checks are not passes. A coverage-complete report means every check has a judged outcome; it does not mean every check passed.</div>
<section><h2>All 58 atomic check outcomes</h2><div class="table-wrap"><table><thead><tr><th>Principle / check</th><th>Status</th><th>Confidence</th><th>Evidence or reason</th></tr></thead><tbody>{''.join(check_rows)}</tbody></table></div></section>
<section><h2>Provenance</h2><p class="provenance">Canonical report: <a href="../{html.escape(target['report'])}">{html.escape(target['report'])}</a><br>Report SHA-256: <code>{target['reportSha256']}</code><br>Local retained report: <code>{html.escape(target['provenance']['sourceReport'])}</code><br>Local evidence root: <code>{html.escape(target['provenance']['evidenceRoot'])}</code><br>Catalog SHA-256: <code>{html.escape(coverage['catalogChecksum'])}</code></p><p class="note">Raw screenshots, HARs, traces, heaps, and other browser artifacts are retained at the local evidence root and intentionally are not committed. Artifact paths in the canonical report are relative to that root.</p></section></main><footer>Generated from the final bounded run without changing report outcomes.</footer></body></html>'''


def render_principle(principle: dict, inventory: dict, reports: dict[int, dict]) -> str:
    pid = principle["id"]
    complete_outcomes = Counter()
    check_counts = {check["id"]: Counter() for check in principle.get("checks", [])}
    target_rows = []
    for target in inventory["targets"]:
        report = reports[target["position"]]
        checks = [row for row in report["checkOutcomes"] if row["principleId"] == pid]
        for row in checks:
            check_counts[row["checkId"]][row["status"]] += 1
        outcome = next(row for row in report["principleOutcomes"] if row["principleId"] == pid)
        if target["disposition"] == "complete":
            complete_outcomes[outcome["status"]] += 1
        target_rows.append(
            f'<tr><td class="num">{target["position"]}</td><td><a href="../sites/{html.escape(target["page"])}">{html.escape(target["origin"])}</a></td>'
            f'<td><span class="status {status_class(target["disposition"])}">{status_label(target["disposition"])}</span></td>'
            f'<td>{html.escape(outcome["status"])}</td><td class="num">{sum(row["status"] in {"pass","issues","not-applicable","opted-out"} for row in checks)} / {len(checks)}</td></tr>'
        )
    check_rows = []
    for check in principle.get("checks", []):
        counts = check_counts[check["id"]]
        check_rows.append(f'<tr><td><code>{html.escape(check["id"])}</code><br>{html.escape(check["summary"])}</td><td class="num">{counts["pass"]}</td><td class="num">{counts["issues"]}</td><td class="num">{counts["not-applicable"] + counts["opted-out"]}</td><td class="num">{counts["blocked"]}</td><td class="num">{counts["not-run"]}</td><td class="num">{sum(counts.values())}</td></tr>')
    cards = ''.join(f'<div class="card"><div class="label">Complete-subset {label}</div><div class="value">{complete_outcomes[key]}</div></div>' for key, label in (("pass","pass"),("issues","issues"),("not-applicable","not applicable"),("opted-out","opted out")))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><meta name="description" content="Exact top-1,000 atomic outcomes for {html.escape(principle['title'])}."><title>{html.escape(principle['title'])} — State of the Web</title><style>{STYLE}</style></head><body><nav><a class="back" href="../index.html">← All targets</a></nav><main><h1>{html.escape(principle['title'])}</h1><p class="lede">{html.escape(principle.get('description',''))}</p><div class="notice">Principle outcome cards use only the 705 coverage-complete reports. The check table retains all 1,000 targets' measured, blocked, and not-run rows without inference.</div><div class="cards">{cards}</div><section><h2>Atomic check totals, all 1,000 targets</h2><div class="table-wrap"><table><thead><tr><th>Check</th><th class="num">Pass</th><th class="num">Issues</th><th class="num">N/A / opted out</th><th class="num">Blocked</th><th class="num">Not run</th><th class="num">Total</th></tr></thead><tbody>{''.join(check_rows)}</tbody></table></div></section><section><h2>Target coverage and recorded outcome</h2><div class="table-wrap"><table><thead><tr><th class="num">Position</th><th>Origin</th><th>Run disposition</th><th>Derived outcome</th><th class="num">Judged checks</th></tr></thead><tbody>{''.join(target_rows)}</tbody></table></div></section></main><footer>Blocked and partial reports are never scored or counted as complete-subset outcomes.</footer></body></html>'''


def reconcile(run_dir: Path, catalog_path: Path, root: Path = ROOT) -> dict:
    run_dir = run_dir.resolve()
    root = root.resolve()
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    catalog_checksum = sha256_bytes(catalog_bytes)
    principle_ids, catalog_pairs, _ = expected_catalog(catalog)
    if len(principle_ids) != 17 or len(catalog_pairs) != 58:
        raise SystemExit(f"catalog denominator mismatch: {len(principle_ids)} principles / {len(catalog_pairs)} checks")

    manifest_path = run_dir / "manifest.csv"
    status_path = run_dir / "atomic-retry-status.json"
    run_path = run_dir / "run.json"
    manifest_bytes = manifest_path.read_bytes()
    with manifest_path.open(newline="", encoding="utf-8") as source:
        manifest = list(csv.DictReader(source))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    run = json.loads(run_path.read_text(encoding="utf-8"))
    records = status.get("records", [])
    if len(manifest) != EXPECTED_SITES or len(records) != EXPECTED_SITES:
        raise SystemExit(f"denominator mismatch: manifest={len(manifest)}, retry records={len(records)}")
    positions = [int(row["position"]) for row in manifest]
    origins = [row["origin"] for row in manifest]
    if positions != list(range(1, EXPECTED_SITES + 1)) or len(set(origins)) != EXPECTED_SITES:
        raise SystemExit("manifest positions/origins are not an exact unique 1..1000 inventory")
    if len({record["url"] for record in records}) != EXPECTED_SITES or len({record["slug"] for record in records}) != EXPECTED_SITES:
        raise SystemExit("retry status URLs/slugs are not unique")

    out = root / "results" / "atomic"
    reports_out = out / "reports"
    if reports_out.exists():
        shutil.rmtree(reports_out)
    reports_out.mkdir(parents=True)
    sites_out = root / "sites"
    sites_out.mkdir(exist_ok=True)
    for stale in sites_out.glob("*.html"):
        stale.unlink()

    targets = []
    canonical_reports: dict[int, dict] = {}
    derived_counts = Counter()
    check_totals = Counter()
    for manifest_row, record in zip(manifest, records, strict=True):
        position = int(manifest_row["position"])
        origin = manifest_row["origin"]
        if canonical_origin(origin) != canonical_origin(record["url"]):
            raise SystemExit(f"position {position}: manifest/status URL mismatch: {origin!r} != {record['url']!r}")
        source_report = Path(record["latestReport"])
        try:
            source_relative = source_report.resolve().relative_to(root)
        except ValueError as error:
            raise SystemExit(f"position {position}: report is outside repository: {source_report}") from error
        report_bytes = source_report.read_bytes()
        report = json.loads(report_bytes)
        if canonical_origin(report.get("url", "")) != canonical_origin(origin):
            raise SystemExit(f"position {position}: report URL mismatch: {report.get('url')!r} != {origin!r}")
        validation = validate_report(report, catalog, catalog_checksum, source_report.parent)
        if validation["errors"]:
            raise SystemExit(f"position {position} ({origin}) invalid report: {'; '.join(validation['errors'][:8])}")
        coverage = validation["coverage"]
        derived_disposition = disposition_for(coverage)
        stated_disposition = DISPOSITION_MAP.get(record.get("disposition"))
        if stated_disposition != derived_disposition:
            raise SystemExit(f"position {position}: disposition mismatch {stated_disposition} != {derived_disposition}")
        if record.get("coverage") != coverage:
            raise SystemExit(f"position {position}: retry status coverage differs from report")
        if record.get("attempts", 0) < 1 or record.get("attempts", 0) > record.get("maxAttempts", 0):
            raise SystemExit(f"position {position}: invalid attempt count")
        report_name = f"{position:04d}-{record['slug']}.json"
        report_path = reports_out / report_name
        report_path.write_bytes(report_bytes)
        report_relative = report_path.relative_to(root).as_posix()
        page_name = f"{position:04d}-{record['slug']}.html"
        target = {
            "position": position,
            "origin": origin,
            "cruxRankBucket": int(manifest_row["crux_rank_bucket"]),
            "slug": record["slug"],
            "disposition": derived_disposition,
            "attempts": record["attempts"],
            "maxAttempts": record["maxAttempts"],
            "auditedAt": report.get("auditedAt"),
            "status": report.get("status"),
            "statusDetail": report.get("statusDetail"),
            "coverage": coverage,
            "report": report_relative,
            "reportSha256": sha256_bytes(report_bytes),
            "page": page_name,
            "provenance": {
                "sourceReport": source_relative.as_posix(),
                "evidenceRoot": source_relative.parent.as_posix(),
                "artifactsCommitted": False,
            },
        }
        targets.append(target)
        canonical_reports[position] = report
        derived_counts[derived_disposition] += 1
        check_totals.update(validation["statusCounts"])

    expected_counts = {
        "complete": status["counts"]["complete"],
        "exhaustedBlocked": status["counts"]["exhaustedBlocked"],
        "exhaustedPartial": status["counts"]["exhaustedPartial"],
        "retryEligible": status["counts"]["retryEligible"],
        "queued": status["queueCount"],
        "invalid": status["counts"]["exhaustedInvalid"],
    }
    actual_counts = {
        "complete": derived_counts["complete"],
        "exhaustedBlocked": derived_counts["exhaustedBlocked"],
        "exhaustedPartial": derived_counts["exhaustedPartial"],
        "retryEligible": 0,
        "queued": 0,
        "invalid": 0,
    }
    if actual_counts != expected_counts or sum(actual_counts[key] for key in ("complete", "exhaustedBlocked", "exhaustedPartial")) != EXPECTED_SITES:
        raise SystemExit(f"final count mismatch: derived={actual_counts}, stated={expected_counts}")
    recorded = sum(check_totals.values())
    judged = sum(check_totals[status] for status in ("pass", "issues", "not-applicable", "opted-out"))
    if recorded != EXPECTED_SITES * len(catalog_pairs):
        raise SystemExit(f"check denominator mismatch: {recorded}")

    inventory = {
        "schemaVersion": 1,
        "kind": "final-bounded-run-inventory",
        "generatedAt": run.get("finishedAt") or status.get("generatedAt"),
        "runId": run["runId"],
        "runStatus": run["status"],
        "method": run["method"],
        "source": run["source"],
        "manifest": {
            "path": "results/atomic/manifest.csv",
            "sha256": sha256_bytes(manifest_bytes),
            "origins": EXPECTED_SITES,
            "unit": "origin",
        },
        "catalog": {
            "path": "principles.json",
            "version": catalog.get("guidanceCatalogVersion"),
            "sha256": catalog_checksum,
            "principles": len(principle_ids),
            "checksPerTarget": len(catalog_pairs),
        },
        "retryBudget": {"maxAttempts": status["maxAttempts"], "queueCount": status["queueCount"]},
        "counts": actual_counts,
        "checkOutcomes": {
            "recorded": recorded,
            "judged": judged,
            "pass": check_totals["pass"],
            "issues": check_totals["issues"],
            "notApplicable": check_totals["not-applicable"],
            "optedOut": check_totals["opted-out"],
            "blocked": check_totals["blocked"],
            "notRun": check_totals["not-run"],
        },
        "publication": {
            "scored": False,
            "wholeInventoryInferenceAllowed": False,
            "completeSubsetSelectionBiased": True,
            "rawBrowserArtifactsCommitted": False,
            "note": "Blocked and partial reports are published as explicit dispositions, never passes or scores. Raw browser artifacts remain at each target's local evidenceRoot.",
        },
        "targets": targets,
    }

    (out / "inventory.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "manifest.csv").write_bytes(manifest_bytes)
    (out / "manifest.sha256").write_text(f"{inventory['manifest']['sha256']}  manifest.csv\n", encoding="utf-8")
    (out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "retry-status.json").write_text(json.dumps({key: value for key, value in status.items() if key != "records"}, indent=2) + "\n", encoding="utf-8")
    (root / "principles.json").write_bytes(catalog_bytes)
    (root / "index.html").write_text(render_index(inventory), encoding="utf-8")

    for target in targets:
        (sites_out / target["page"]).write_text(render_site(target, canonical_reports[target["position"]], catalog), encoding="utf-8")
    principles_out = root / "principles"
    principles_out.mkdir(exist_ok=True)
    for stale in principles_out.glob("*.html"):
        stale.unlink()
    for principle in catalog["principles"]:
        (principles_out / f"{principle['id']}.html").write_text(render_principle(principle, inventory, canonical_reports), encoding="utf-8")

    summary = {key: value for key, value in inventory.items() if key != "targets"}
    summary["inventory"] = "results/atomic/inventory.json"
    summary["reports"] = "results/atomic/reports/"
    (root / "atomic-checkpoint.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    checkpoint = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><meta name="description" content="Final bounded-run disposition for the State of the Web top-1,000 atomic audit."><title>Final atomic run — State of the Web</title><style>{STYLE}</style></head><body><nav><a class="back" href="index.html">← Exact 1,000-target inventory</a></nav><main><p class="note">Final bounded-run publication · {html.escape(inventory['runId'])}</p><h1>Atomic run final disposition</h1><p class="lede">The bounded retry run is finished: 705 reports are coverage-complete, 257 are blocked after retries, and 38 are partial after retries. No targets are queued, retry-eligible, or invalid.</p><div class="notice"><strong>This is a final run inventory, not a 1,000-site score.</strong> Blocked and partial checks remain literal. They are never converted to passes, not-applicable outcomes, or scores.</div><div class="cards"><div class="card"><div class="label">Fixed denominator</div><div class="value">1,000</div></div><div class="card"><div class="label">Complete</div><div class="value">705</div></div><div class="card"><div class="label">Blocked</div><div class="value">257</div></div><div class="card"><div class="label">Partial</div><div class="value">38</div></div></div><p><a href="index.html">Browse all 1,000 targets</a> · <a href="results/atomic/inventory.json">Download the machine inventory</a> · <a href="atomic-checkpoint.json">Download this summary</a></p></main><footer>State of the Web</footer></body></html>'''
    (root / "checkpoint.html").write_text(checkpoint, encoding="utf-8")
    print(json.dumps({"origins": EXPECTED_SITES, "counts": actual_counts, "checkOutcomes": inventory["checkOutcomes"], "catalogSha256": catalog_checksum, "manifestSha256": inventory["manifest"]["sha256"]}, indent=2))
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--catalog", type=Path, required=True, help="Exact catalog bytes used by the run")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    reconcile(args.run_dir, args.catalog, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
