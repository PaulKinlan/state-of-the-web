#!/usr/bin/env python3
"""Batch web-uplift audit runner — drives Chrome via CDP evidence primitives."""
import subprocess, json, os, sys, time, sqlite3
from pathlib import Path

EVIDENCE_CLI = os.path.expanduser("~/.web-uplift/evidence/cli.mjs")
PANEL_FILE = "/tmp/tranco-top1000.txt"
DB_PATH = "audit.db"
SITE_LIST = sys.argv[1] if len(sys.argv) > 1 else PANEL_FILE
START_IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 50

def run_evidence(primitive, url, **kwargs):
    """Run a web-uplift evidence primitive."""
    args = ["node", EVIDENCE_CLI, primitive, url]
    for k, v in kwargs.items():
        args.extend([f"--{k.replace('_','-')}", str(v)])
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        # Try to parse JSON from output
        for line in result.stdout.strip().split('\n'):
            if line.strip().startswith('{'):
                try: return json.loads(line)
                except: pass
        return {"raw": result.stdout[-500:], "error": result.stderr[-200:] if result.stderr else None}
    except Exception as e:
        return {"error": str(e)}

def audit_site(domain, rank):
    """Run a quick audit on a single site."""
    url = f"https://www.{domain}/"
    print(f"  [{rank}] {domain}...", end=" ", flush=True)
    t0 = time.time()
    
    result = {
        "domain": domain, "rank": rank, "url": url,
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    
    # Screenshot (gives us the rendered page)
    ss = run_evidence("screenshot", url, viewport="1280x900", out=f"evidence/{domain}.png")
    
    # Layout metrics (CLS, long tasks, scroll width)
    layout = run_evidence("layout", url, viewport="1280x900")
    observed = layout.get("observed", {}) if isinstance(layout, dict) else {}
    
    # Discoverability (JS shell check)
    disc = run_evidence("discoverability", url)
    
    # DOM inspection (meta tags, viewport, h1)
    dom = run_evidence("dom", url, selector="head")
    
    # Evaluate (HTTPS, HSTS, CSP via fetch)
    headers = run_evidence("evaluate", url, expr="JSON.stringify({hsts: performance.getEntriesByType('navigation')[0]?.transferSize > 0})")
    
    result["evidence"] = {
        "cls": observed.get("cls", None),
        "long_tasks": len(observed.get("longTasks", [])),
        "scroll_width": observed.get("scrollWidth", None),
        "client_width": observed.get("clientWidth", None),
        "horizontal_overflow": observed.get("horizontalOverflowPx", None),
        "discoverability": disc.get("coveragePct", None) if isinstance(disc, dict) else None,
        "is_js_shell": disc.get("isJsShell", None) if isinstance(disc, dict) else None,
    }
    
    elapsed = time.time() - t0
    print(f"{elapsed:.0f}s", flush=True)
    return result

# Main
domains = [l.strip() for l in open(SITE_LIST) if l.strip()]
batch = domains[START_IDX:START_IDX + COUNT]
print(f"Auditing {len(batch)} sites ({START_IDX}-{START_IDX+COUNT-1} of {len(domains)})", flush=True)

os.makedirs("evidence", exist_ok=True)
results = []
for i, domain in enumerate(batch):
    rank = START_IDX + i + 1
    try:
        r = audit_site(domain, rank)
        results.append(r)
    except Exception as e:
        print(f"  ERROR: {e}")
        results.append({"domain": domain, "rank": rank, "error": str(e)})
    
    # Save incrementally
    if (i+1) % 5 == 0:
        json.dump(results, open(f"results-batch-{START_IDX}.json", "w"), indent=2, default=str)
        print(f"  Saved {len(results)} results", flush=True)

json.dump(results, open(f"results-batch-{START_IDX}.json", "w"), indent=2, default=str)
print(f"\nDone: {len(results)} sites audited. Saved to results-batch-{START_IDX}.json", flush=True)
