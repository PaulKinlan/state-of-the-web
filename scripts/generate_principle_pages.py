#!/usr/bin/env python3
"""Generate one crawlable, sortable result page per principle."""

import html
import json
import os
import sqlite3
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "state-of-the-web.db")
OUT = os.path.join(ROOT, "principles")
os.makedirs(OUT, exist_ok=True)

def load_metadata():
    with open(os.path.join(ROOT, "principles.json"), encoding="utf-8") as source:
        data = json.load(source)
    return {principle["id"]: principle for principle in data["principles"]}


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
.table-wrap{overflow:auto;border:1px solid var(--border);border-radius:.45rem}table{width:100%;border-collapse:collapse;background:var(--background)}caption{text-align:left;padding:.7rem;color:var(--muted);font-size:.82rem}th,td{padding:.55rem .65rem;border-bottom:1px solid var(--border);text-align:left;vertical-align:top;font-size:.82rem}th{position:sticky;top:0;background:var(--bg-secondary);font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tbody tr:last-child td{border-bottom:0}.sort-button{display:inline-flex;align-items:center;gap:.3rem;padding:0;border:0;background:none;color:inherit;font:inherit;text-transform:inherit;letter-spacing:inherit;cursor:pointer}.sort-button:hover,.sort-button:focus-visible{color:var(--accent);text-decoration:underline;text-underline-offset:3px}.sort-arrow{display:inline-block;min-width:1ch}.num{text-align:right;font-variant-numeric:tabular-nums}.num .sort-button{justify-content:flex-end;width:100%}.status{font-weight:700;text-transform:uppercase;font-size:.7rem}.status.pass{color:var(--good)}.status.issues{color:var(--bad)}.status.not-applicable{color:var(--muted)}.summary{min-width:28ch;color:var(--muted)}
details{margin-top:.35rem}summary{cursor:pointer;color:var(--accent)}.finding{margin:.45rem 0;padding:.55rem;background:var(--bg-secondary);border-left:3px solid var(--bad)}.finding b{font-size:.68rem;text-transform:uppercase}.finding p{margin:.2rem 0;color:var(--muted)}.score{font-weight:700}.score.s0-29{color:var(--bad)}.score.s30-59{color:var(--warn)}.score.s60-79,.score.s80-100{color:var(--good)}.empty{padding:1.5rem;color:var(--muted)}.note{color:var(--muted);font-size:.82rem}.hidden{display:none}
@media(max-width:700px){.controls{grid-template-columns:1fr}.summary{min-width:20ch}th,td{padding:.45rem;font-size:.76rem}}
"""

SCRIPT = """
const rows=[...document.querySelectorAll('#results tbody tr')];
const search=document.querySelector('#search');
const status=document.querySelector('#status-filter');
const order=document.querySelector('#order');
const statusWeight={issues:0,pass:1,'not-applicable':2};
const defaultDirection={failures:'asc',successes:'asc',rank:'asc',score:'desc',confidence:'desc',site:'asc'};
let sortKey=order.value,sortDirection=defaultDirection[sortKey];
function compareNumeric(a,b,key,missing){
 const av=Number(a.dataset[key]),bv=Number(b.dataset[key]);
 if(av===missing&&bv!==missing)return 1;
 if(bv===missing&&av!==missing)return -1;
 const result=av-bv;
 return sortDirection==='desc'?-result:result;
}
function compareRows(a,b){
 let result=0;
 if(sortKey==='failures')result=statusWeight[a.dataset.status]-statusWeight[b.dataset.status]||Number(a.dataset.rank)-Number(b.dataset.rank);
 else if(sortKey==='successes')result=statusWeight[b.dataset.status]-statusWeight[a.dataset.status]||Number(a.dataset.rank)-Number(b.dataset.rank);
 else if(sortKey==='rank')return compareNumeric(a,b,'rank',999999);
 else if(sortKey==='score')return compareNumeric(a,b,'score',-1);
 else if(sortKey==='confidence')return compareNumeric(a,b,'confidence',0);
 else result=a.dataset.site.localeCompare(b.dataset.site);
 return sortDirection==='desc'?-result:result;
}
function updateSortHeaders(){
 document.querySelectorAll('th[data-sort]').forEach(th=>{
  const active=th.dataset.sort===sortKey;
  th.setAttribute('aria-sort',active?(sortDirection==='asc'?'ascending':'descending'):'none');
  th.querySelector('.sort-arrow').textContent=active?(sortDirection==='asc'?'▲':'▼'):'';
 });
}
function render(){
 const term=search.value.trim().toLowerCase();
 const wanted=status.value;
 rows.forEach(row=>row.classList.toggle('hidden',!!term&&!row.dataset.search.includes(term)||!!wanted&&row.dataset.status!==wanted));
 [...rows].sort(compareRows).forEach(row=>row.parentNode.append(row));
 const visible=rows.filter(row=>!row.classList.contains('hidden')).length;
 document.querySelector('#shown').textContent=visible;
 updateSortHeaders();
}
search.addEventListener('input',render);status.addEventListener('change',render);
order.addEventListener('change',()=>{sortKey=order.value;sortDirection=defaultDirection[sortKey];render();});
document.querySelectorAll('.sort-button[data-sort]').forEach(button=>button.addEventListener('click',()=>{
 const next=button.dataset.sort;
 if(sortKey===next)sortDirection=sortDirection==='asc'?'desc':'asc';
 else{sortKey=next;sortDirection=defaultDirection[next];order.value=next;}
 render();
}));
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
            confidence_weight = {"low": 1, "medium": 2, "high": 3}.get(row["confidence"], 0)
            rows_html.append(
                f'<tr data-site="{html.escape(site)}" data-search="{html.escape(site.lower())}" data-status="{html.escape(row["status"])}" data-rank="{rank}" data-score="{score if score is not None else -1}" data-confidence="{confidence_weight}">'
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
        test_stats = {}
        if "test_results" in tables:
            for test in con.execute(
                """SELECT test_id,
                          SUM(CASE WHEN status='pass' THEN 1 ELSE 0 END) pass,
                          SUM(CASE WHEN status='issues' THEN 1 ELSE 0 END) issues,
                          SUM(CASE WHEN status='not-applicable' THEN 1 ELSE 0 END) na,
                          SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) blocked,
                          SUM(CASE WHEN status='not-run' THEN 1 ELSE 0 END) not_run,
                          COUNT(*) measured
                   FROM test_results WHERE principle_id=? GROUP BY test_id""",
                (pid,),
            ):
                test_stats[test["test_id"]] = test

        atomic_rows = []
        for check in info.get("checks", []):
            test = test_stats.get(check["id"])
            measured = test["measured"] if test else 0
            not_run = (test["not_run"] if test else 0) + max(0, len(results) - measured)
            atomic_rows.append(
                f'<tr><td><strong>{html.escape(check["id"])}</strong><br>{html.escape(check["summary"])}'
                f'<br><span class="note"><strong>Detection:</strong> {html.escape(check.get("detectableVia", "No detection guidance recorded."))}</span></td>'
                f'<td class="num">{test["pass"] if test else 0}</td><td class="num">{test["issues"] if test else 0}</td>'
                f'<td class="num">{test["na"] if test else 0}</td><td class="num">{test["blocked"] if test else 0}</td>'
                f'<td class="num">{not_run}</td><td class="num">{len(results)}</td></tr>'
            )
        atomic_section = (
            '<p class="method"><strong>Important:</strong> The older 499-site dataset stored broad principle judgements, not outcomes for every authoritative check below. Those judgements remain visible for inspection, but they do not prove that an unrecorded check passed. Check-level coverage is shown literally.</p>'
            '<div class="table-wrap"><table><caption>Authoritative check outcomes from principles.json</caption><thead><tr><th>Defined check and detection guidance</th><th class="num">Pass</th><th class="num">Issues</th><th class="num">N/A</th><th class="num">Blocked</th><th class="num">Not run</th><th class="num">Sites</th></tr></thead><tbody>'
            + ''.join(atomic_rows) + '</tbody></table></div>'
        )

        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><link rel="icon" href="../favicon.svg" type="image/svg+xml">
<meta name="description" content="Passes, failures, evidence and site-level results for {html.escape(title)} in the State of the Web audit.">
<title>{html.escape(title)} — State of the Web</title><style>{STYLE}</style></head><body>
<nav aria-label="Breadcrumb"><a class="back" href="../index.html">← All principles and sites</a></nav>
<main><header><p class="note">Principle result breakdown</p><h1>{html.escape(title)}</h1><p class="lede">{html.escape(info.get("description", ""))}</p></header>
<section aria-labelledby="method-title"><h2 id="method-title">Applicability and measurement</h2><p class="method"><strong>Applicability:</strong> {html.escape(info.get("applicability", {}).get("criteria", "No applicability criteria recorded."))}<br><br><strong>Required coverage:</strong> all {len(info.get("checks", []))} checks defined in the vendored <code>principles.json</code>, with explicit pass, issues, N/A, blocked, or not-run evidence per site.</p></section>
<div class="cards"><div class="card"><div class="label">Principle-level outcomes</div><div class="value">{len(results)}</div></div><div class="card"><div class="label">Recorded pass (legacy)</div><div class="value">{counts['pass']}</div></div><div class="card"><div class="label">Recorded issues</div><div class="value">{counts['issues']}</div></div><div class="card"><div class="label">Unassessed / N/A</div><div class="value">{counts['not-applicable']}</div></div><div class="card"><div class="label">Recorded failure rate</div><div class="value">{fail_rate}%</div></div></div>
<section aria-labelledby="atomic-tests"><h2 id="atomic-tests">Atomic test breakdown</h2>{atomic_section}</section>
<section aria-labelledby="site-results"><h2 id="site-results">Sites that passed and failed</h2><div class="controls"><label>Search sites<input id="search" type="search" placeholder="example.com"></label><label>Filter status<select id="status-filter"><option value="">All statuses</option><option value="issues">Issues</option><option value="pass">Pass</option><option value="not-applicable">Not assessed / N/A</option></select></label><label>Order<select id="order"><option value="failures">Failures first</option><option value="successes">Passes first</option><option value="rank">Tranco rank</option><option value="score">Overall score</option><option value="confidence">Confidence</option><option value="site">Site name</option></select></label></div>
<p class="note"><span id="shown">{len(results)}</span> of {len(results)} site results shown. “Not assessed / N/A” remains separate from a pass. Select an order above or use a sortable column heading.</p><div class="table-wrap"><table id="results"><caption>{html.escape(title)} outcomes by site</caption><thead><tr><th>Status</th><th data-sort="site" aria-sort="none"><button class="sort-button" type="button" data-sort="site">Site <span class="sort-arrow" aria-hidden="true"></span></button></th><th class="num" data-sort="rank" aria-sort="none"><button class="sort-button" type="button" data-sort="rank">Rank <span class="sort-arrow" aria-hidden="true"></span></button></th><th class="num" data-sort="score" aria-sort="none"><button class="sort-button" type="button" data-sort="score">Score <span class="sort-arrow" aria-hidden="true"></span></button></th><th data-sort="confidence" aria-sort="none"><button class="sort-button" type="button" data-sort="confidence">Confidence <span class="sort-arrow" aria-hidden="true"></span></button></th><th>Assessment and evidence</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div></section>
<section aria-labelledby="finding-tests"><h2 id="finding-tests">Recorded failure checks</h2><p class="note">These are the individual failed checks retained as findings in the current reports. The current dataset does not infer a per-check pass merely because no finding was recorded.</p><div class="table-wrap"><table><caption>Failure finding types and affected sites</caption><thead><tr><th>Check / finding</th><th class="num">Occurrences</th><th>Severity</th><th>Sites</th></tr></thead><tbody>{''.join(finding_rows) if finding_rows else '<tr><td colspan="4" class="empty">No individual failure findings were retained for this principle.</td></tr>'}</tbody></table></div></section>
</main><footer><p class="note">Data and methodology: <a href="https://github.com/PaulKinlan/state-of-the-web">State of the Web on GitHub</a>.</p></footer><script>{SCRIPT}</script></body></html>"""
        with open(os.path.join(OUT, f"{pid}.html"), "w", encoding="utf-8") as output:
            output.write(page)
        print(f"{pid}: {len(results)} results, {len(findings)} findings")

    con.close()


if __name__ == "__main__":
    generate()
