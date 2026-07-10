#!/usr/bin/env python3
"""Rebuild the State of the Web SQLite DB from all evidence sources.

Sources:
  - results/cdp/all-results.json         (870 sites: CLS, overflow, discoverability, JS-shell)
  - results/gpt/<domain>.json            (per-site vision audit: 17 principles, verdict, score)
  - results/gpt/batch-00{1,2}.json       (batched vision audits)
  - results/gpt/supplemental-ltv-*.json  (Lighthouse + trace LCP/TBT/CLS)

The schema has 23 columns in `sites`. We populate what we have and leave the rest NULL.
"""
import json, glob, os, sqlite3, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "state-of-the-web.db")

# --- principle id mapping: normalise the 17 principle ids ---
def norm_pid(pid):
    pid = pid.lower().strip()
    return pid

def load_cdp():
    p = os.path.join(ROOT, "results", "cdp", "all-results.json")
    data = json.load(open(p))
    out = {}
    for row in data:
        dom = row.get("domain") or row.get("site")
        if not dom: continue
        ev = row.get("evidence", {})
        out[dom] = {
            "rank": row.get("rank"),
            "url": row.get("url"),
            "audited_at": row.get("audited_at"),
            "cls": ev.get("cls"),
            "has_viewport": 1 if ev.get("has_viewport_meta") else (0 if ev.get("has_viewport_meta") is False else None),
            "is_js_shell": 1 if ev.get("is_js_shell") else 0,
            "discoverability_pct": ev.get("discoverability_pct"),
        }
    return out

def load_gpt():
    """Merge per-site + batch JSONs into {domain: result}."""
    out = {}
    # per-site files
    for f in glob.glob(os.path.join(ROOT, "results", "gpt", "*.json")):
        base = os.path.basename(f)
        if base.startswith("batch") or base.startswith("supplemental") or base.startswith("memory"):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if isinstance(d, list):
            for r in d: _add_gpt(out, r)
        elif isinstance(d, dict):
            _add_gpt(out, d)
    # batch files
    for f in glob.glob(os.path.join(ROOT, "results", "gpt", "batch-*.json")):
        if ".partial" in f: continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if isinstance(d, list):
            for r in d: _add_gpt(out, r)
    return out

def _add_gpt(out, r):
    dom = r.get("site") or r.get("domain")
    if not dom: return
    # keep the richest version (most principles)
    prev = out.get(dom)
    if prev and len(prev.get("principles", [])) >= len(r.get("principles", [])):
        return
    out[dom] = r

def load_ltv():
    """Merge supplemental Lighthouse/trace summaries."""
    out = {}
    for f in glob.glob(os.path.join(ROOT, "results", "gpt", "supplemental-ltv-*.json")):
        if ".partial" in f: continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for r in items:
            dom = r.get("site")
            if not dom: continue
            lh = r.get("lighthouse", {})
            # also try to pull real scores from evidence lighthouse.json if summary has nulls
            out[dom] = {
                "lh_perf": lh.get("performance"),
                "lh_a11y": lh.get("accessibility"),
                "lh_bp": lh.get("bestPractices") or lh.get("best_practices"),
                "lh_seo": lh.get("seo"),
                "lcp": r.get("lcp"),
                "tbt": r.get("tbt"),
                "cls_ltv": r.get("cls"),
                "reduced_motion": r.get("reducedMotionAnimations"),
            }
    # backfill lighthouse scores from raw evidence files where summary was null
    for f in glob.glob(os.path.join(ROOT, "results", "gpt", "supplemental-ltv-*-evidence", "*", "lighthouse.json")):
        dom = f.split(os.sep)[-2]
        if dom not in out: continue
        if out[dom]["lh_perf"] is not None: continue  # already have it
        try:
            d = json.load(open(f))
            cats = d.get("categories", {})
            if not cats: continue
            def sc(cid): return cats.get(cid, {}).get("score")
            out[dom]["lh_perf"] = sc("performance")
            out[dom]["lh_a11y"] = sc("accessibility")
            out[dom]["lh_bp"] = sc("best-practices")
            out[dom]["lh_seo"] = sc("seo")
        except Exception:
            pass
    return out

def derive_resilience(con, cdp):
    """Derive be-resilient from CDP evidence: content visibility without JS.
    A site that shows 0% content without JS, or is a JS shell, fails resilience
    (core content does not work under adverse conditions)."""
    n_pass = n_issues = 0
    for dom, c in cdp.items():
        exists = con.execute("SELECT 1 FROM principles WHERE site=? AND principle_id='be-resilient'", (dom,)).fetchone()
        if not exists:
            continue  # only derive for vision-audited sites
        disc = c.get("discoverability_pct")
        is_shell = c.get("is_js_shell")
        if is_shell or (disc is not None and disc == 0):
            status, conf = "issues", "high"
            summ = f"Site shows {disc or 0}% of content without JavaScript" + (" and is a JS shell" if is_shell else "") + ". Core content does not work under no-JS conditions."
            finding_id = dom.replace('.', '-') + "-resilience-01"
            con.execute("DELETE FROM findings WHERE site=? AND principle_id='be-resilient'", (dom,))
            con.execute("INSERT INTO findings (site, principle_id, finding_id, severity, summary, evidence) VALUES (?,?,?,?,?,?)",
                        (dom, "be-resilient", finding_id, "serious", "Core content invisible without JavaScript",
                         f"CDP discoverability probe: {disc or 0}% of page content visible with JS disabled"))
            n_issues += 1
        elif disc is not None and disc < 10:
            status, conf = "issues", "medium"
            summ = f"Only {disc}% of content visible without JavaScript — most of the page requires JS to render."
            n_issues += 1
        else:
            status, conf = "pass", "medium"
            summ = f"{disc}% of content visible without JavaScript — core content survives no-JS conditions."
            con.execute("DELETE FROM findings WHERE site=? AND principle_id='be-resilient'", (dom,))
            n_pass += 1
        con.execute("INSERT OR REPLACE INTO principles (site, principle_id, status, confidence, summary, finding_count) VALUES (?,?,?,?,?,?)",
                    (dom, "be-resilient", status, conf, summ, 1 if status=="issues" else 0))
    print(f"  be-resilient derived from CDP: {n_pass} pass, {n_issues} issues", file=sys.stderr)

def fix_false_passes(con):
    """Reclassify low-confidence 'not exercised' passes as not-applicable (pending).
    support-core-task-success was marked pass/low with 'task completion flow not exercised'
    — that is not a real audit, so reclassify it honestly."""
    n = con.execute("""UPDATE principles SET status='not-applicable', confidence='low',
                   summary='Task completion flows were not exercised in this audit — pending active interaction testing.'
                   WHERE principle_id='support-core-task-success' AND status='pass'
                   AND confidence='low'""").rowcount
    if n:
        con.execute("DELETE FROM findings WHERE principle_id='support-core-task-success'")
        print(f"  support-core-task-success: {n} false passes reclassified as pending", file=sys.stderr)

def main():
    cdp = load_cdp()
    gpt = load_gpt()
    ltv = load_ltv()
    print(f"CDP sites: {len(cdp)} | GPT vision sites: {len(gpt)} | LTV sites: {len(ltv)}", file=sys.stderr)

    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    schema = open(os.path.join(ROOT, "schemas", "schema.sql")).read()
    schema = "\n".join(l for l in schema.splitlines() if not l.strip().startswith("//"))  # strip // comment lines
    con.executescript(schema)

    # union of all domains
    all_doms = sorted(set(cdp) | set(gpt))
    n_sites = 0
    for dom in all_doms:
        c = cdp.get(dom, {})
        g = gpt.get(dom, {})
        l = ltv.get(dom, {})
        source = 'gpt' if dom in gpt else 'cdp'
        # rank: prefer CDP, then GPT
        rank = c.get("rank") or g.get("rank")
        url = c.get("url") or g.get("url")
        final_url = g.get("finalUrl")
        http_status = g.get("httpStatus")
        overall = g.get("overallScore")
        verdict = g.get("verdict")
        audited_at = c.get("audited_at") or g.get("auditedAt")

        cls = c.get("cls") if c.get("cls") is not None else l.get("cls_ltv")
        lcp = l.get("lcp")
        # INP not directly available; leave None
        inp = None
        text_chars = None
        has_viewport = c.get("has_viewport")
        has_meta_description = None
        https_only = None
        hsts = None

        con.execute("""INSERT INTO sites
            (site, source, rank, audited_at, url, final_url, http_status, overall_score, verdict,
             lh_performance, lh_accessibility, lh_best_practices, lh_seo, axe_violations,
             heap_size, cls, lcp, inp, is_js_shell, text_chars, has_viewport,
             has_meta_description, https_only, hsts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (dom, source, rank, audited_at, url, final_url, http_status, overall, verdict,
             l.get("lh_perf"), l.get("lh_a11y"), l.get("lh_bp"), l.get("lh_seo"), None,
             None, cls, lcp, inp, c.get("is_js_shell"), text_chars, has_viewport,
             has_meta_description, https_only, hsts))
        n_sites += 1

    # principles + findings from GPT
    n_prin = 0; n_find = 0
    for dom, g in gpt.items():
        for pr in g.get("principles", []):
            pid = norm_pid(pr.get("id", ""))
            status = pr.get("status")
            conf = pr.get("confidence")
            summ = pr.get("summary")
            finds = pr.get("findings", [])
            con.execute("""INSERT OR REPLACE INTO principles
                (site, principle_id, status, confidence, summary, finding_count)
                VALUES (?,?,?,?,?,?)""",
                (dom, pid, status, conf, summ, len(finds)))
            n_prin += 1
            for fnd in finds:
                con.execute("""INSERT INTO findings
                    (site, principle_id, finding_id, severity, summary, evidence)
                    VALUES (?,?,?,?,?,?)""",
                    (dom, pid, fnd.get("id"), fnd.get("severity"),
                     fnd.get("title") or fnd.get("summary"),
                     fnd.get("evidence")))
                n_find += 1
    con.commit()

    # --- Post-processing: derive principles from real evidence ---
    derive_resilience(con, cdp)
    fix_false_passes(con)
    con.commit()
    print(f"Inserted {n_sites} sites, {n_prin} principle results, {n_find} findings", file=sys.stderr)

    # quick stats
    for q in ["SELECT COUNT(*) FROM sites",
              "SELECT COUNT(*) FROM sites WHERE overall_score IS NOT NULL",
              "SELECT COUNT(*) FROM sites WHERE lh_performance IS NOT NULL",
              "SELECT COUNT(*) FROM principles",
              "SELECT COUNT(*) FROM findings"]:
        r = con.execute(q).fetchone()[0]
        print(f"  {q.split('FROM')[-1].strip()}: {r}", file=sys.stderr)

    # principle pass-rate summary
    print("\nPrinciple pass rates (GPT vision, 106 sites):", file=sys.stderr)
    for pid, status, cnt in con.execute("""SELECT principle_id, status, COUNT(*) c
            FROM principles GROUP BY principle_id, status
            ORDER BY principle_id, status"""):
        print(f"  {pid:38s} {status:16s} {cnt}", file=sys.stderr)
    con.close()

if __name__ == "__main__":
    main()
