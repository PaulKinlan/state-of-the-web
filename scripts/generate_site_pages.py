#!/usr/bin/env python3
"""Generate a static HTML page per audited site with all data baked in.
No query strings, no client-side DB query — each site is a real, crawlable page."""
import sqlite3, os, html, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "state-of-the-web.db")
OUT = os.path.join(ROOT, "sites")
os.makedirs(OUT, exist_ok=True)

PRINCIPLES = json.load(open(os.path.join(ROOT, "principles.js").replace("principles.js", "principles.js")) if os.path.exists(os.path.join(ROOT, "principles.js")) else "/dev/null") if False else None
# Load PRINCIPLES from principles.js (it's JS, parse it)
import re
pjs = open(os.path.join(ROOT, "principles.js")).read()
# Extract titles/descriptions via simple parsing
PRINCIPLES = {}
for m in re.finditer(r'"([^"]+)":\{title:"([^"]*)"[^}]*desc:"([^"]*)"[^}]*how:"([^"]*)"', pjs):
    pid, title, desc, how = m.group(1), m.group(2), m.group(3), m.group(4)
    PRINCIPLES[pid] = {"title": title, "desc": desc, "how": how}

def pid_name(pid):
    return PRINCIPLES.get(pid, {}).get("title", pid.replace("-", " "))

def score_class(s):
    if s is None: return ""
    if s < 30: return "s0-29"
    if s < 60: return "s30-59"
    if s < 80: return "s60-79"
    return "s80-100"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="description" content="{desc}">
<title>{title} — State of the Web audit</title>
<style>
:root{{color-scheme:light dark;--color:#000;--background:#fdfcf8;--bg-secondary:#f0eee6;--border:#e0ddd4;--muted:#666;--accent:#4b3aff;--good:#1a8a3a;--bad:#c0392b;--warn:#e6a700}}
@media(prefers-color-scheme:dark){{:root{{--color:#e8e4dc;--background:#1c1a17;--bg-secondary:#2a2723;--border:#3a3530;--muted:#9a9088;--accent:#8ab4f8;--good:#57c97a;--bad:#e06c75;--warn:#d9a441}}}}
@media(prefers-reduced-motion:reduce){{*{{animation-duration:.01ms!important;transition-duration:.01ms!important}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;line-height:1.7;color:var(--color);background-color:var(--background);padding:1rem;max-width:800px;margin:0 auto}}
h1{{font-family:Georgia,serif;font-size:1.8rem;font-weight:normal;margin-bottom:.2rem}}
h2{{font-family:Georgia,serif;font-size:1.2rem;font-weight:normal;margin:1.5rem 0 .5rem;border-bottom:1px solid var(--border);padding-bottom:.3rem}}
a{{color:var(--accent)}}
.back{{font-size:.8rem;color:var(--muted);margin-bottom:1rem;display:inline-block}}
.score{{display:inline-block;width:36px;height:36px;border-radius:50%;text-align:center;line-height:36px;font-size:.8rem;font-weight:700;color:#fff;vertical-align:middle}}
.score.s0-29{{background:#c0392b}}.score.s30-59{{background:#e6a700}}.score.s60-79{{background:#2980b9}}.score.s80-100{{background:#1a8a3a}}
.meta-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.6rem;margin:1rem 0}}
.meta-card{{background:var(--bg-secondary);border:1px solid var(--border);border-radius:.5rem;padding:.7rem}}
.meta-card .label{{font-size:.65rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:.2rem}}
.meta-card .val{{font-size:1.1rem;font-weight:700;font-variant-numeric:tabular-nums}}
.verdict{{font-size:.82rem;color:var(--muted);line-height:1.5;padding:.7rem;background:var(--bg-secondary);border-radius:.4rem;margin:1rem 0}}
.principle-row{{display:flex;align-items:flex-start;gap:.6rem;padding:.6rem 0;border-bottom:1px solid var(--border)}}
.principle-row .pstatus{{flex-shrink:0;width:65px;font-weight:700;font-size:.7rem;text-transform:uppercase}}
.principle-row .pname{{flex-shrink:0;width:170px}}
.principle-row .psum{{flex:1;color:var(--muted);font-size:.8rem;line-height:1.45}}
.pstatus.pass{{color:var(--good)}}.pstatus.issues{{color:var(--bad)}}.pstatus.not-applicable{{color:var(--muted)}}
.findings{{margin-top:.3rem;padding-left:1rem}}
.finding{{font-size:.75rem;color:var(--muted);padding:.15rem 0}}
.finding .sev{{font-weight:700;text-transform:uppercase;font-size:.65rem;margin-right:.3rem}}
.finding .sev.high,.finding .sev.serious{{color:var(--bad)}}.finding .sev.moderate{{color:var(--warn)}}.finding .sev.low{{color:var(--muted)}}
.principle-link{{color:var(--accent);text-decoration:none;cursor:help}}
.principle-link:hover{{text-decoration:underline}}
.principle-link .q{{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;background:var(--accent);color:var(--background);font-size:.58rem;font-weight:700;line-height:1;margin-left:.15rem}}
details summary{{cursor:pointer;font-size:.8rem;color:var(--accent)}}
details[open] summary{{margin-bottom:.3rem}}
</style>
</head>
<body>
<a class="back" href="../index.html">← Back to all sites</a>
<h1>{name}</h1>
<div class="meta-grid">{meta}</div>
<div class="verdict">{verdict}</div>
<h2>Principle results (17)</h2>
{principles}
<p style="font-size:.75rem;color:var(--muted);margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border)">Data: <a href="https://github.com/PaulKinlan/state-of-the-web" target="_blank">GitHub</a> · Methodology: <a href="https://github.com/PaulKinlan/web-uplift" target="_blank">web-uplift</a></p>
</body>
</html>"""

def generate():
    con = sqlite3.connect(DB)
    sites = con.execute("SELECT * FROM sites WHERE source='gpt' ORDER BY rank").fetchall()
    cols = [d[0] for d in con.execute("SELECT * FROM sites LIMIT 1").description]
    n = 0
    for row in sites:
        s = dict(zip(cols, row))
        site = s["site"]
        # safe filename
        fname = site.replace("/", "-") + ".html"
        # meta cards
        score_html = f'<span class="score {score_class(s["overall_score"])}">{s["overall_score"]}</span>' if s["overall_score"] is not None else "—"
        meta_items = [
            ("Overall score", score_html),
            ("Rank", str(s["rank"] or "—")),
            ("CLS", f"{s['cls']:.3f}" if s["cls"] is not None else "—"),
            ("LH perf", str(round(s["lh_performance"]*100)) if s["lh_performance"] is not None else "—"),
            ("LH a11y", str(round(s["lh_accessibility"]*100)) if s["lh_accessibility"] is not None else "—"),
            ("JS shell", "Yes" if s["is_js_shell"] else "No"),
        ]
        if s.get("url"):
            meta_items.append(("URL", f'<a href="{html.escape(s["url"])}" target="_blank">Visit ↗</a>'))
        meta = "".join(f'<div class="meta-card"><div class="label">{l}</div><div class="val">{v}</div></div>' for l, v in meta_items)
        # principles
        prs = con.execute("SELECT principle_id,status,confidence,summary,finding_count FROM principles WHERE site=? ORDER BY CASE status WHEN 'issues' THEN 0 WHEN 'pass' THEN 1 ELSE 2 END, principle_id", (site,)).fetchall()
        finds = con.execute("SELECT principle_id,severity,summary FROM findings WHERE site=?", (site,)).fetchall()
        finds_by = {}
        for f in finds:
            finds_by.setdefault(f[0], []).append(f)
        pr_rows = []
        for p in prs:
            pid, status, conf, summary, fc = p
            st = "n/a" if status == "not-applicable" else status
            pinfo = PRINCIPLES.get(pid, {})
            title = pid_name(pid)
            desc = pinfo.get("desc", "")
            how = pinfo.get("how", "")
            # use <details> for the principle description (no JS needed)
            q_link = f'<details><summary class="principle-link">{title} <span class="q">?</span></summary><p style="font-size:.78rem;color:var(--muted);padding:.5rem 0 0 1.5rem;line-height:1.5">{html.escape(desc)}<br><br><strong style="color:var(--color)">How it\'s tested:</strong> {html.escape(how)}</p></details>' if desc else title
            fs_html = ""
            for f in finds_by.get(pid, []):
                sev = html.escape(f[1] or "")
                fs_html += f'<div class="finding"><span class="sev {sev}">{sev}</span>{html.escape(f[2] or "")}</div>'
            pr_rows.append(f'<div class="principle-row"><span class="pstatus {status}">{st}</span><div style="flex:1">{q_link}<div class="psum">{html.escape(summary or "")}{fs_html}</div></div></div>')
        principles = "".join(pr_rows)
        page = TEMPLATE.format(
            title=html.escape(site),
            name=html.escape(site) + (" <span style=\"color:var(--warn)\" title=\"JS shell\">⬚</span>" if s["is_js_shell"] else ""),
            desc=f"Web quality audit of {site} across 17 modern web principles. Score: {s['overall_score'] or '—'}.",
            meta=meta,
            verdict=html.escape(s.get("verdict") or ""),
            principles=principles,
        )
        with open(os.path.join(OUT, fname), "w") as f:
            f.write(page)
        n += 1
    print(f"Generated {n} static site pages in sites/")
    con.close()

if __name__ == "__main__":
    generate()
