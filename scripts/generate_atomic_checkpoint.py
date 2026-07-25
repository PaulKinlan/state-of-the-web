#!/usr/bin/env python3
"""Generate a labelled, non-scored checkpoint from retained atomic audit reports.

The checkpoint includes only sites with one retained report whose exact 58-check
coverage matrix is complete. Blocked and partial sites remain in the fixed
1,000-site denominator but never contribute inferred outcomes or scores.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_CHECKS = 58
EXPECTED_SITES = 1000
SEVERITIES = ("critical", "high", "medium", "low")


def load_report(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_complete(report: dict | None) -> bool:
    if not report:
        return False
    coverage = report.get("coverage", {})
    return (
        coverage.get("complete") is True
        and coverage.get("expected") == EXPECTED_CHECKS
        and coverage.get("recorded") == EXPECTED_CHECKS
        and coverage.get("judged") == EXPECTED_CHECKS
        and all(
            coverage.get(key, 0) == 0
            for key in ("blocked", "notRun", "missing", "unknown", "duplicates")
        )
        and len(report.get("checkOutcomes", [])) == EXPECTED_CHECKS
    )


def report_paths(site_dir: Path):
    return sorted(
        site_dir.glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def collect(run_dir: Path):
    reports_root = run_dir / "atomic-reports"
    complete = []
    remaining = Counter()

    # Top-level compatibility aliases are symlinks; excluding them prevents a
    # single site from appearing twice in the fixed denominator.
    site_dirs = sorted(
        path for path in reports_root.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if len(site_dirs) != EXPECTED_SITES:
        raise SystemExit(
            f"Expected {EXPECTED_SITES} real site directories, found {len(site_dirs)}"
        )

    for site_dir in site_dirs:
        candidates = [(path, load_report(path)) for path in report_paths(site_dir)]
        retained_complete = next(
            ((path, report) for path, report in candidates if is_complete(report)), None
        )
        if retained_complete:
            complete.append((site_dir.name, *retained_complete))
            continue

        newest = candidates[0][1] if candidates else None
        if newest is None:
            remaining["noReport"] += 1
            continue
        coverage = newest.get("coverage", {})
        judged = coverage.get("judged", 0)
        blocked = coverage.get("blocked", 0)
        not_run = coverage.get("notRun", 0)
        if judged == 0 and blocked == EXPECTED_CHECKS:
            remaining["fullyBlocked"] += 1
        elif blocked or not_run:
            remaining["partiallyJudged"] += 1
        else:
            remaining["otherIncomplete"] += 1

    severities = Counter()
    sites_with_severity = Counter()
    findings_per_site = []
    principles = defaultdict(Counter)
    checks = defaultdict(Counter)
    catalog_versions = Counter()
    catalog_checksums = Counter()

    for _, _, report in complete:
        coverage = report["coverage"]
        catalog_versions[coverage.get("catalogVersion", "unknown")] += 1
        catalog_checksums[coverage.get("catalogChecksum", "unknown")] += 1

        findings = report.get("findings", [])
        findings_per_site.append(len(findings))
        seen_severities = set()
        for finding in findings:
            severity = finding.get("severity", "unknown")
            severities[severity] += 1
            seen_severities.add(severity)
        for severity in seen_severities:
            sites_with_severity[severity] += 1

        for outcome in report.get("principleOutcomes", []):
            principles[outcome["principleId"]][outcome["status"]] += 1
        for outcome in report.get("checkOutcomes", []):
            checks[(outcome["principleId"], outcome["checkId"])][
                outcome["status"]
            ] += 1

    complete_count = len(complete)
    top_checks = []
    for (principle_id, check_id), statuses in sorted(
        checks.items(), key=lambda item: (-item[1]["issues"], item[0])
    )[:12]:
        top_checks.append(
            {
                "principleId": principle_id,
                "checkId": check_id,
                "issues": statuses["issues"],
                "pass": statuses["pass"],
                "notApplicable": statuses["not-applicable"],
                "issuePctOfCompleteSubset": round(
                    statuses["issues"] / complete_count * 100, 1
                ),
            }
        )

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 1,
        "kind": "labelled-intermediate-checkpoint",
        "generatedAt": generated,
        "runId": run_dir.name,
        "method": "web-uplift atomic 58-check audit",
        "catalog": {
            "versions": dict(catalog_versions),
            "checksums": dict(catalog_checksums),
            "expectedChecksPerSite": EXPECTED_CHECKS,
        },
        "coverage": {
            "fixedSiteDenominator": EXPECTED_SITES,
            "completeSites": complete_count,
            "completeCheckOutcomes": complete_count * EXPECTED_CHECKS,
            "fullyBlockedSites": remaining["fullyBlocked"],
            "partiallyJudgedSites": remaining["partiallyJudged"],
            "noReportSites": remaining["noReport"],
            "otherIncompleteSites": remaining["otherIncomplete"],
            "complete": complete_count == EXPECTED_SITES,
        },
        "completeSubset": {
            "findings": {severity: severities[severity] for severity in SEVERITIES},
            "totalFindings": sum(severities.values()),
            "medianFindingsPerSite": statistics.median(findings_per_site)
            if findings_per_site
            else 0,
            "sitesWithSeverity": {
                severity: sites_with_severity[severity] for severity in SEVERITIES
            },
            "principleOutcomes": {
                principle_id: dict(statuses)
                for principle_id, statuses in sorted(principles.items())
            },
            "topIssueChecks": top_checks,
        },
        "publication": {
            "scored": False,
            "wholeInventoryInferenceAllowed": False,
            "reason": "The fixed 1,000-site audit is incomplete. The complete subset is completion-biased because blocked and partial sites are excluded from outcome percentages.",
            "catalogNote": "The retained reports use the web-uplift catalogue checksum recorded above. The State of the Web vendored catalogue currently has a different checksum, although all 58 (principleId, checkId) pairs match; reconcile before final import.",
        },
    }


def pretty_id(value: str) -> str:
    return value.replace("-", " ").capitalize()


def render(checkpoint: dict) -> str:
    coverage = checkpoint["coverage"]
    subset = checkpoint["completeSubset"]
    generated = datetime.fromisoformat(
        checkpoint["generatedAt"].replace("Z", "+00:00")
    ).strftime("%d %B %Y, %H:%M UTC")

    cards = [
        ("Coverage-complete sites", f'{coverage["completeSites"]:,} / 1,000', "Exact 58-check matrix"),
        ("Judged check outcomes", f'{coverage["completeCheckOutcomes"]:,}', "No inferred or missing rows"),
        ("Fully blocked", f'{coverage["fullyBlockedSites"]:,}', "Usually bot, auth, or rate limits"),
        ("Partially judged", f'{coverage["partiallyJudgedSites"]:,}', "Retained, but excluded below"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div><div class="detail">{html.escape(detail)}</div></div>'
        for label, value, detail in cards
    )

    severities = subset["findings"]
    severity_rows = "".join(
        f'<tr><th scope="row">{severity.capitalize()}</th><td class="num">{severities[severity]:,}</td>'
        f'<td class="num">{subset["sitesWithSeverity"][severity]:,}</td></tr>'
        for severity in SEVERITIES
    )

    check_rows = "".join(
        "<tr>"
        f'<td><code>{html.escape(row["checkId"])}</code><span class="principle">{html.escape(pretty_id(row["principleId"]))}</span></td>'
        f'<td class="num">{row["issues"]:,}</td>'
        f'<td class="num">{row["issuePctOfCompleteSubset"]:.1f}%</td>'
        "</tr>"
        for row in subset["topIssueChecks"]
    )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<meta name="description" content="Labelled intermediate checkpoint for the State of the Web fixed top-1,000 atomic audit.">
<title>Atomic audit checkpoint — State of the Web</title>
<style>
:root{{color-scheme:light dark;--text:#171612;--bg:#fdfcf8;--surface:#f0eee6;--border:#d8d4ca;--muted:#625e56;--accent:#4b3aff;--warn:#9a6500;--critical:#b42318;--high:#c2410c;--medium:#9a6700;--low:#4b5563}}
@media(prefers-color-scheme:dark){{:root{{--text:#eeeae2;--bg:#1c1a17;--surface:#2a2723;--border:#484139;--muted:#b7aea4;--accent:#9dc1ff;--warn:#f2c14e;--critical:#ff8a80;--high:#ffab70;--medium:#f2c14e;--low:#c4c9d1}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:1rem/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main,header,footer{{width:min(72rem,calc(100% - 2rem));margin-inline:auto}}
header{{padding-block:1.25rem .5rem}}
a{{color:var(--accent)}}
h1,h2{{font-family:Georgia,"Times New Roman",serif;font-weight:400;line-height:1.2}}
h1{{font-size:clamp(2rem,7vw,3.5rem);margin:.75rem 0 .4rem}}
h2{{font-size:1.45rem;margin:2rem 0 .65rem}}
p{{max-width:76ch}}
.eyebrow{{font-size:.78rem;font-weight:750;letter-spacing:.08em;text-transform:uppercase;color:var(--warn)}}
.lede{{font-size:1.12rem;color:var(--muted);margin-block:.5rem 1.25rem}}
.notice{{border-inline-start:.3rem solid var(--warn);background:var(--surface);padding:1rem 1.1rem;margin-block:1rem 1.5rem}}
.notice strong{{display:block;margin-bottom:.25rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:.75rem}}
.card{{border:1px solid var(--border);background:var(--surface);border-radius:.55rem;padding:1rem}}
.label{{font-size:.75rem;text-transform:uppercase;letter-spacing:.045em;color:var(--muted)}}
.value{{font-size:1.8rem;font-weight:750;font-variant-numeric:tabular-nums;line-height:1.2;margin-block:.25rem}}
.detail,.caption,.principle{{font-size:.82rem;color:var(--muted)}}
.table-wrap{{overflow-x:auto;border:1px solid var(--border);border-radius:.55rem}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}}
caption{{text-align:start;padding:.8rem 1rem;color:var(--muted)}}
th,td{{padding:.65rem .85rem;text-align:start;border-block-start:1px solid var(--border);vertical-align:top}}
thead th{{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
.num{{text-align:end;font-variant-numeric:tabular-nums;white-space:nowrap}}
code{{font-size:.86em;overflow-wrap:anywhere}}
.principle{{display:block}}
ul{{padding-inline-start:1.3rem;max-width:76ch}}
li+li{{margin-top:.45rem}}
footer{{padding-block:2.5rem;color:var(--muted);font-size:.85rem}}
:where(a):focus-visible{{outline:.2rem solid var(--accent);outline-offset:.2rem}}
@media(max-width:34rem){{th,td{{padding:.55rem .6rem}}}}
</style>
</head>
<body>
<header>
<a href="./">← State of the Web</a>
</header>
<main id="content">
<p class="eyebrow">Labelled intermediate checkpoint · not a completed audit</p>
<h1>What the atomic audit shows so far</h1>
<p class="lede">Snapshot generated {html.escape(generated)} from run <code>{html.escape(checkpoint["runId"])}</code>. This page reports the completed subset without turning incomplete coverage into a score.</p>
<div class="notice" role="note">
<strong>Do not generalise these percentages to the whole top 1,000.</strong>
The completed subset is selection-biased: blocked and partially judged sites are excluded from its outcome percentages. The fixed denominator remains 1,000 sites.
</div>
<section aria-labelledby="coverage-heading">
<h2 id="coverage-heading">Coverage</h2>
<div class="cards">{card_html}</div>
<p class="caption">One additional site has no report. Every completed site has exactly 58 recorded and judged check outcomes, with zero blocked, not-run, missing, unknown, or duplicate rows.</p>
</section>
<section aria-labelledby="assessment-heading">
<h2 id="assessment-heading">Intermediate assessment</h2>
<p>Across the {coverage["completeSites"]:,} coverage-complete sites, the audit records <strong>{subset["totalFindings"]:,} findings</strong>, with a median of {subset["medianFindingsPerSite"]:g} findings per site. Security-policy hygiene, resource delivery, accessibility structure and focus, Core Web Vitals, and user color-scheme preferences are the most widespread issue families.</p>
<p>Memory is comparatively healthier: {subset["principleOutcomes"]["be-memory-efficient"]["pass"]:,} of {coverage["completeSites"]:,} completed sites ({subset["principleOutcomes"]["be-memory-efficient"]["pass"] / coverage["completeSites"] * 100:.1f}%) pass the memory-efficiency principle. This is still a completed-subset observation, not a whole-inventory result.</p>
</section>
<section aria-labelledby="severity-heading">
<h2 id="severity-heading">Finding severity in the completed subset</h2>
<div class="table-wrap"><table>
<caption>Finding count and affected completed sites. A site may appear in several severity rows.</caption>
<thead><tr><th scope="col">Severity</th><th scope="col" class="num">Findings</th><th scope="col" class="num">Sites affected</th></tr></thead>
<tbody>{severity_rows}</tbody>
</table></div>
</section>
<section aria-labelledby="checks-heading">
<h2 id="checks-heading">Most widespread issue checks</h2>
<div class="table-wrap"><table>
<caption>Ranked by sites with an issues outcome among the {coverage["completeSites"]:,} completed sites.</caption>
<thead><tr><th scope="col">Atomic check</th><th scope="col" class="num">Sites</th><th scope="col" class="num">Completed subset</th></tr></thead>
<tbody>{check_rows}</tbody>
</table></div>
</section>
<section aria-labelledby="limits-heading">
<h2 id="limits-heading">What this checkpoint does not claim</h2>
<ul>
<li>It is not a score or pass rate for the top 1,000.</li>
<li>Blocked and partially judged checks do not become passes or not-applicable outcomes.</li>
<li>Completion is correlated with whether a site permits meaningful headless inspection, so the subset is not random.</li>
<li>The run uses catalogue checksum <code>{html.escape(next(iter(checkpoint["catalog"]["checksums"])))}</code>. The repository's vendored catalogue has a different checksum even though all 58 atomic IDs match; that must be reconciled before final import.</li>
<li>Percentages describe evidence observed at one point in time and under the audited routes and conditions.</li>
</ul>
</section>
<section aria-labelledby="next-heading">
<h2 id="next-heading">Next</h2>
<p>The resume worker continues to retry incomplete sites without weakening the publication gate. The final site and aggregate pages will be rebuilt only after catalogue reconciliation and a fresh exact-denominator validation.</p>
<p><a href="atomic-checkpoint.json">Download this checkpoint as JSON</a></p>
</section>
</main>
<footer>State of the Web · <a href="https://github.com/PaulKinlan/state-of-the-web">source and methodology</a></footer>
</body>
</html>
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("."))
    args = parser.parse_args()

    checkpoint = collect(args.run_dir.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "atomic-checkpoint.json").write_text(
        json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "checkpoint.html").write_text(render(checkpoint), encoding="utf-8")
    print(
        f'checkpoint: {checkpoint["coverage"]["completeSites"]}/{EXPECTED_SITES} sites, '
        f'{checkpoint["coverage"]["completeCheckOutcomes"]} complete check outcomes'
    )


if __name__ == "__main__":
    main()
