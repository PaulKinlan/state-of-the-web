#!/usr/bin/env python3
"""Generate one crawlable, sortable result page per principle."""

import html
import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "state-of-the-web.db")
OUT = os.path.join(ROOT, "principles")
os.makedirs(OUT, exist_ok=True)

REAUDIT_TESTS = {
    "provide-guided-navigation": (
        "results/gpt/reaudit-guided-nav.json",
        "Homepage wayfinding review",
        "Desktop/mobile screenshot review of navigation, search, hierarchy, obstruction, and paths to primary actions.",
    ),
    "be-trustworthy": (
        "results/gpt/reaudit-trustworthy.json",
        "Homepage dark-pattern and trust review",
        "Desktop/mobile screenshot review of consent obstruction, misleading defaults, disguised advertising, and visible recovery paths.",
    ),
}


def load_metadata():
    script = """
const fs=require('fs'),vm=require('vm');
const context={};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
process.stdout.write(JSON.stringify(context.PRINCIPLES));
"""
    result = subprocess.run(
        ["node", "-e", script, os.path.join(ROOT, "principles.js")],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def status_label(status):
    return "Not assessed" if status == "not-applicable" else status.title()


def score_class(score):
    if score is None:
        return ""
    if score < 30:
        return "s0-29"
    if score < 60:
        return "s30-59"
    if score < 80:
        return "s60-79"
    return "s80-100"


STYLE = """
:root{color-scheme:light dark;--color:#000;--background:#fdfcf8;--bg-secondary:#f0eee6;--border:#d9d5ca;--muted:#666;--accent:#4b3aff;--good:#147a32;--bad:#b52f23;--warn:#9b6800}
@media(prefers-color-scheme:dark){:root{--color:#e8e4dc;--background:#1c1a17;--bg-secondary:#2a2723;--border:#4a443d;--muted:#aaa097;--accent:#9abcf8;--good:#6bd889;--bad:#ff8b83;--warn:#e8b64e}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
*{box-sizing:border-box}body{max-width:1180px;margin:auto;padding:1rem;font:16px/1.55 system-ui,sans-serif;color:var(--color);background:var(--background)}
h1,h2{font-family:Georgia,serif;font-weight:400}h1{font-size:clamp(1.8rem,5vw,2.8rem);line-height:1.1;margin:.5rem 0}h2{margin:2rem 0 .6rem;border-bottom:1px solid var(--border);padding-bottom:.35rem}
a{color:var(--accent)}.back{font-size:.85rem}.lede{max-width:75ch;color:var(--muted)}.method{padding:1rem;border-left:4px solid var(--accent);background:var(--bg-secondary);max-width:90ch}.method strong{color:var(--color)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin:1.25rem 0}.card{padding:1rem;border:1px solid var(--border);border-radius:.5rem;background:var(--bg-secondary)}.card .label{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}.card .value{font-size:1.65rem;font-weight:700;font-variant-numeric:tabular-nums}
.controls{display:grid;grid-template-columns:minmax(180px,1fr) repeat(2,minmax(150px,auto));gap:.6rem;margin:1rem 0}input,select{width:100%;padding:.55rem;border:1px solid var(--border);border-radius:.35rem;background:var(--background);color:var(--color);font:inherit}
.table-wrap{overflow:auto;border:1px solid var(--border);border-radius:.45rem}table{width:100%;border-collapse:collapse;background:var(--background)}caption{text-align:left;padding:.7rem;color:var(--muted);font-size:.82rem}th,td{padding:.55rem .65rem;border-bottom:1px solid var(--border);text-align:left;vertical-align:top;font-size:.82rem}th{position:sticky;top:0;background:var(--bg-secondary);font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tbody tr:last-child td{border-bottom:0}.num{text-align:right;font-variant-numeric:tabular-nums}.status{font-weight:700;text-transform:uppercase;font-size:.7rem}.status.pass{color:var(--good)}.status.issues{color:var(--bad)}.status.not-applicable{color:var(--muted)}.summary{min-width:28ch;color:var(--muted)}
details{margin-top:.35rem}summary{cursor:pointer;color:var(--accent)}.finding{margin:.45rem 0;padding:.55rem;background:var(--bg-secondary);border-left:3px solid var(--bad)}.finding b{font-size:.68rem;text-transform:uppercase}.finding p{margin:.2rem 0;color:var(--muted)}.score{font-weight:700}.score.s0-29{color:var(--bad)}.score.s30-59{color:var(--warn)}.score.s60-79,.score.s80-100{color:var(--good)}.empty{padding:1.5rem;color:var(--muted)}.note{color:var(--muted);font-size:.82rem}.hidden{display:none}
@media(max-width:700px){.controls{grid-template-columns:1fr}.summary{min-width:20ch}th,td{padding:.45rem;font-size:.76rem}}
"""

SCRIPT = """
const rows=[...document.querySelectorAll('#results tbody tr')];
const search=document.querySelector('#search');
const status=document.querySelector('#status-filter');
const order=document.querySelector('#order');
const statusWeight={issues:0,pass:1,'not-applicable':2};
function render(){
 const term=search.value.trim().toLowerCase();
 const wanted=status.value;
 rows.forEach(row=>row.classList.toggle('hidden',!!term&&!row.dataset.search.includes(term)||!!wanted&&row.dataset.status!==wanted));
 const sorted=[...rows].sort((a,b)=>{
  if(order.value==='failures')return statusWeight[a.dataset.status]-statusWeight[b.dataset.status]||Number(a.dataset.rank)-Number(b.dataset.rank);
  if(order.value==='successes')return statusWeight[b.dataset.status]-statusWeight[a.dataset.status]||Number(a.dataset.rank)-Number(b.dataset.rank);
  if(order.value==='rank')return Number(a.dataset.rank)-Number(b.dataset.rank);
  if(order.value==='score')return Number(b.dataset.score)-Number(a.dataset.score);
  return a.dataset.site.localeCompare(b.dataset.site);
 });
 sorted.forEach(row=>row.parentNode.append(row));
 const visible=rows.filter(row=>!row.classList.contains('hidden')).length;
 document.querySelector('#shown').textContent=visible;
}
[search,status,order].forEach(control=>control.addEventListener(control===search?'input':'change',render));
render();
"""


def generate():
    metadata = load_metadata()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    principle_ids = [row[0] for row in con.execute("SELECT DISTINCT principle_id FROM principles ORDER BY principle_id")]

    for pid in principle_ids:
        info = metadata.get(pid, {})
        title = info.get("title", pid.replace("-", " ").title())
        results = con.execute(
            """SELECT p.*,s.rank,s.overall_score,s.verdict
               FROM principles p JOIN sites s ON s.site=p.site
               WHERE p.principle_id=?""",
            (pid,),
        ).fetchall()
        findings = con.execute(
            "SELECT site,severity,summary,evidence FROM findings WHERE principle_id=? ORDER BY site,severity",
            (pid,),
        ).fetchall()
        by_site = defaultdict(list)
        for finding in findings:
            by_site[finding["site"]].append(finding)

        counts = Counter(row["status"] for row in results)
        assessed = counts["pass"] + counts["issues"]
        fail_rate = round(counts["issues"] / assessed * 100) if assessed else 0

        rows_html = []
        for row in results:
            site = row["site"]
            site_findings = by_site.get(site, [])
            details = ""
            if site_findings:
                items = []
                for finding in site_findings:
                    items.append(
                        '<div class="finding"><b>{}</b> {}<p>{}</p></div>'.format(
                            html.escape(finding["severity"] or "finding"),
                            html.escape(finding["summary"] or ""),
                            html.escape(finding["evidence"] or ""),
                        )
                    )
                details = f'<details><summary>{len(site_findings)} recorded failure check(s)</summary>{"".join(items)}</details>'
            score = row["overall_score"]
            score_html = "—" if score is None else f'<span class="score {score_class(score)}">{score}</span>'
            rank = row["rank"] if row["rank"] is not None else 999999
            rows_html.append(
                f'<tr data-site="{html.escape(site)}" data-search="{html.escape(site.lower())}" data-status="{html.escape(row["status"])}" data-rank="{rank}" data-score="{score if score is not None else -1}">'
                f'<td><span class="status {html.escape(row["status"])}">{html.escape(status_label(row["status"]))}</span></td>'
                f'<td><a href="../sites/{html.escape(site)}.html">{html.escape(site)}</a></td>'
                f'<td class="num">{row["rank"] if row["rank"] is not None else "—"}</td>'
                f'<td class="num">{score_html}</td>'
                f'<td>{html.escape(row["confidence"] or "—")}</td>'
                f'<td class="summary">{html.escape(row["summary"] or "")}{details}</td></tr>'
            )

        finding_groups = defaultdict(lambda: {"count": 0, "sites": [], "severities": Counter()})
        for finding in findings:
            key = finding["summary"] or "Unlabelled finding"
            group = finding_groups[key]
            group["count"] += 1
            group["sites"].append(finding["site"])
            group["severities"][finding["severity"] or "unknown"] += 1
        finding_rows = []
        for summary, group in sorted(finding_groups.items(), key=lambda item: (-item[1]["count"], item[0])):
            links = ", ".join(
                f'<a href="../sites/{html.escape(site)}.html">{html.escape(site)}</a>'
                for site in sorted(set(group["sites"]))
            )
            severities = ", ".join(f"{key}: {value}" for key, value in group["severities"].most_common())
            finding_rows.append(
                f'<tr><td>{html.escape(summary)}</td><td class="num">{group["count"]}</td><td>{html.escape(severities)}</td><td>{links}</td></tr>'
            )

        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        atomic_rows = []
        if {"principle_tests", "test_results"}.issubset(tables):
            atomic = con.execute(
                """SELECT t.test_id,t.title,t.method,
                          SUM(CASE WHEN r.status='pass' THEN 1 ELSE 0 END) pass,
                          SUM(CASE WHEN r.status='issues' THEN 1 ELSE 0 END) issues,
                          SUM(CASE WHEN r.status='not-applicable' THEN 1 ELSE 0 END) na,
                          SUM(CASE WHEN r.status='blocked' THEN 1 ELSE 0 END) blocked,
                          SUM(CASE WHEN r.status='not-run' THEN 1 ELSE 0 END) not_run,
                          COUNT(r.site) total
                   FROM principle_tests t
                   LEFT JOIN test_results r ON r.principle_id=t.principle_id AND r.test_id=t.test_id
                   WHERE t.principle_id=? GROUP BY t.test_id,t.title,t.method ORDER BY t.test_id""",
                (pid,),
            ).fetchall()
            for test in atomic:
                atomic_rows.append(
                    f'<tr><td><strong>{html.escape(test["title"] or test["test_id"])}</strong><br><span class="note">{html.escape(test["method"] or "")}</span></td>'
                    f'<td class="num">{test["pass"]}</td><td class="num">{test["issues"]}</td><td class="num">{test["na"]}</td>'
                    f'<td class="num">{test["blocked"]}</td><td class="num">{test["not_run"]}</td><td class="num">{test["total"]}</td></tr>'
                )
        if not atomic_rows and pid in REAUDIT_TESTS:
            relative_path, test_title, test_method = REAUDIT_TESTS[pid]
            source_path = os.path.join(ROOT, relative_path)
            if os.path.exists(source_path):
                audited_sites = {row["site"] for row in results}
                source_results = json.load(open(source_path, encoding="utf-8"))
                test_counts = Counter()
                for result in source_results:
                    site = result.get("site") or result.get("domain")
                    if site not in audited_sites:
                        continue
                    result_status = result.get("status", "not-applicable")
                    summary = result.get("summary", "").lower()
                    if result_status == "not-applicable" and any(word in summary for word in ("prevent", "impossible", "obscur")):
                        result_status = "blocked"
                    test_counts[result_status] += 1
                measured_total = sum(test_counts.values())
                test_counts["not-run"] = max(0, len(results) - measured_total)
                total = sum(test_counts.values())
                atomic_rows.append(
                    f'<tr><td><strong>{html.escape(test_title)}</strong><br><span class="note">{html.escape(test_method)}</span></td>'
                    f'<td class="num">{test_counts["pass"]}</td><td class="num">{test_counts["issues"]}</td>'
                    f'<td class="num">{test_counts["not-applicable"]}</td><td class="num">{test_counts["blocked"]}</td>'
                    f'<td class="num">{test_counts["not-run"]}</td><td class="num">{total}</td></tr>'
                )
        atomic_section = (
            '<div class="table-wrap"><table><caption>Atomic test outcomes</caption><thead><tr><th>Test and method</th><th class="num">Pass</th><th class="num">Issues</th><th class="num">N/A</th><th class="num">Blocked</th><th class="num">Not run</th><th class="num">Total</th></tr></thead><tbody>'
            + ''.join(atomic_rows) + '</tbody></table></div>'
            if atomic_rows
            else '<p class="method"><strong>No atomic test-result rows were stored in this audit run.</strong> The site outcomes and failure findings below are real, but the UI does not infer that every sub-check passed when no finding exists. The updated audit schema now requires stable test IDs, methods, and explicit pass/issues/blocked/not-run results for future runs.</p>'
        )

        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><link rel="icon" href="../favicon.svg" type="image/svg+xml">
<meta name="description" content="Passes, failures, evidence and site-level results for {html.escape(title)} in the State of the Web audit.">
<title>{html.escape(title)} — State of the Web</title><style>{STYLE}</style></head><body>
<nav aria-label="Breadcrumb"><a class="back" href="../index.html">← All principles and sites</a></nav>
<main><header><p class="note">Principle result breakdown</p><h1>{html.escape(title)}</h1><p class="lede">{html.escape(info.get("desc", ""))}</p></header>
<section aria-labelledby="method-title"><h2 id="method-title">How it is measured</h2><p class="method"><strong>Current method:</strong> {html.escape(info.get("how", "No method description recorded."))}</p></section>
<div class="cards"><div class="card"><div class="label">Site results</div><div class="value">{len(results)}</div></div><div class="card"><div class="label">Pass</div><div class="value">{counts['pass']}</div></div><div class="card"><div class="label">Issues</div><div class="value">{counts['issues']}</div></div><div class="card"><div class="label">Not assessed / N/A</div><div class="value">{counts['not-applicable']}</div></div><div class="card"><div class="label">Failure rate</div><div class="value">{fail_rate}%</div></div></div>
<section aria-labelledby="atomic-tests"><h2 id="atomic-tests">Atomic test breakdown</h2>{atomic_section}</section>
<section aria-labelledby="site-results"><h2 id="site-results">Sites that passed and failed</h2><div class="controls"><label>Search sites<input id="search" type="search" placeholder="example.com"></label><label>Filter status<select id="status-filter"><option value="">All statuses</option><option value="issues">Issues</option><option value="pass">Pass</option><option value="not-applicable">Not assessed / N/A</option></select></label><label>Order<select id="order"><option value="failures">Failures first</option><option value="successes">Passes first</option><option value="rank">Tranco rank</option><option value="score">Overall score</option><option value="site">Site name</option></select></label></div>
<p class="note"><span id="shown">{len(results)}</span> of {len(results)} site results shown. “Not assessed / N/A” remains separate from a pass.</p><div class="table-wrap"><table id="results"><caption>{html.escape(title)} outcomes by site</caption><thead><tr><th>Status</th><th>Site</th><th class="num">Rank</th><th class="num">Score</th><th>Confidence</th><th>Assessment and evidence</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div></section>
<section aria-labelledby="finding-tests"><h2 id="finding-tests">Recorded failure checks</h2><p class="note">These are the individual failed checks retained as findings in the current reports. The current dataset does not infer a per-check pass merely because no finding was recorded.</p><div class="table-wrap"><table><caption>Failure finding types and affected sites</caption><thead><tr><th>Check / finding</th><th class="num">Occurrences</th><th>Severity</th><th>Sites</th></tr></thead><tbody>{''.join(finding_rows) if finding_rows else '<tr><td colspan="4" class="empty">No individual failure findings were retained for this principle.</td></tr>'}</tbody></table></div></section>
</main><footer><p class="note">Data and methodology: <a href="https://github.com/PaulKinlan/state-of-the-web">State of the Web on GitHub</a>.</p></footer><script>{SCRIPT}</script></body></html>"""
        with open(os.path.join(OUT, f"{pid}.html"), "w", encoding="utf-8") as output:
            output.write(page)
        print(f"{pid}: {len(results)} results, {len(findings)} findings")

    con.close()


if __name__ == "__main__":
    generate()
