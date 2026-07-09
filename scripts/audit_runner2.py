#!/usr/bin/env python3
"""Batch web-uplift audit runner v2 — properly parses evidence CLI JSON output."""
import subprocess, json, os, sys, time, re

EVIDENCE_CLI = os.path.expanduser("~/.web-uplift/evidence/cli.mjs")
SITE_LIST = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tranco-top1000.txt"
START_IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 50

def run_evidence(primitive, url, **kwargs):
    args = ["node", EVIDENCE_CLI, primitive, url]
    for k, v in kwargs.items():
        args.extend([f"--{k.replace('_','-')}", str(v)])
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        # The CLI outputs JSON as the last block (multi-line). Find the opening brace.
        text = result.stdout
        idx = text.rfind('\n{')
        if idx == -1: idx = text.find('{')
        if idx >= 0:
            json_text = text[idx:].strip()
            return json.loads(json_text)
        # Try last line only
        lines = [l for l in text.strip().split('\n') if l.strip()]
        for line in reversed(lines):
            try: return json.loads(line)
            except: pass
        return {"error": "no JSON in output", "raw": text[-300:]}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}

def audit_site(domain, rank):
    url = f"https://www.{domain}/"
    print(f"  [{rank}] {domain}...", end=" ", flush=True)
    t0 = time.time()
    r = {"domain": domain, "rank": rank, "url": url, "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    
    layout = run_evidence("layout", url, viewport="1280x900")
    obs = layout.get("observed", {}) if isinstance(layout, dict) else {}
    disc = run_evidence("discoverability", url)
    
    r["evidence"] = {
        "cls": obs.get("cls"),
        "long_tasks": len(obs.get("longTasks", [])),
        "scroll_width": obs.get("scrollWidth"),
        "client_width": obs.get("clientWidth"),
        "horizontal_overflow_px": obs.get("horizontalOverflowPx"),
        "has_viewport_meta": obs.get("hasViewportMeta"),
        "discoverability_pct": disc.get("coveragePct") if isinstance(disc, dict) else None,
        "is_js_shell": disc.get("isJsShell") if isinstance(disc, dict) else None,
        "content_visible_without_js": disc.get("contentVisibleWithoutJs") if isinstance(disc, dict) else None,
    }
    r["elapsed_s"] = round(time.time() - t0, 1)
    print(f"{r['elapsed_s']}s CLS={r['evidence']['cls']} overflow={r['evidence']['horizontal_overflow_px']}px shell={r['evidence']['is_js_shell']}", flush=True)
    return r

domains = [l.strip() for l in open(SITE_LIST) if l.strip()]
batch = domains[START_IDX:START_IDX + COUNT]
print(f"Auditing {len(batch)} sites ({START_IDX}-{START_IDX+COUNT-1})", flush=True)
os.makedirs("evidence", exist_ok=True)
results = []
for i, domain in enumerate(batch):
    rank = START_IDX + i + 1
    try:
        results.append(audit_site(domain, rank))
    except Exception as e:
        print(f"  ERROR: {e}")
        results.append({"domain": domain, "rank": rank, "error": str(e)})
    if (i+1) % 10 == 0:
        json.dump(results, open(f"results-batch-{START_IDX}.json", "w"), indent=2, default=str)
        print(f"  Saved {len(results)}", flush=True)
json.dump(results, open(f"results-batch-{START_IDX}.json", "w"), indent=2, default=str)
print(f"\nDone: {len(results)} sites", flush=True)
