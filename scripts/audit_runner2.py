#!/usr/bin/env python3
"""Batch web-uplift audit runner v2 — Mode 1 CDP evidence pass.

Collects per site: layout metrics (CLS, long tasks, overflow), viewport meta,
discoverability (JS-shell detection, crawler-vs-rendered coverage), and the
modern-web (Chrome 134+) feature probe in `scripts/probes/modern-web-features.js`
— view transitions, scroll-driven animations, anchored positioning,
scroll-state-aware chrome, and platform gestures.

It still does NOT collect Lighthouse, axe, heap, HAR, traces, or principle
judgements: those belong to Mode 2 (the agentic atomic-check audit).

The probe is folded in rather than left as a separate command (bead
state-of-the-web-if6): a Mode 1 pass now always measures the Chrome 134+
features, instead of a sweep completing while they silently stay unmeasured.

Usage: python3 scripts/audit_runner2.py <site-list> [start-index] [count]
"""
import csv, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_CLI = os.path.expanduser("~/.web-uplift/evidence/cli.mjs")
MODERN_WEB_PROBE = os.path.join(ROOT, "probes", "modern-web-features.js")
MODERN_WEB_COLLECTOR = os.path.join(ROOT, "collect_modern_web.mjs")
EVIDENCE_TIMEOUT = int(os.environ.get("AUDIT_EVIDENCE_TIMEOUT", "90"))
PROBE_TIMEOUT = int(os.environ.get("AUDIT_PROBE_TIMEOUT", str(EVIDENCE_TIMEOUT)))
# Real pages need to settle before CSSOM and running animation timelines are
# meaningful; the fixture suite runs without it.
PROBE_WAIT_MS = int(os.environ.get("AUDIT_PROBE_WAIT", "3000"))


def run_evidence(primitive, url, **kwargs):
    args = ["node", EVIDENCE_CLI, primitive, url]
    for k, v in kwargs.items():
        args.extend([f"--{k.replace('_','-')}", str(v)])
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=EVIDENCE_TIMEOUT)
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


def safe_name(domain):
    return re.sub(r'[^A-Za-z0-9.-]+', '-', domain).strip('-') or 'site'


def load_targets(site_list):
    """Read a plain domain list or the published manifest (position,origin,bucket).

    The documented invocation is `audit_runner2.py results/atomic/manifest.csv 0 50`,
    so the CSV header and full origin URLs must be handled: treating a manifest
    row as a bare domain builds `https://www.<row>/`, which is not a site at all.
    Returns (rank, url) pairs, preserving source order.
    """
    rows = [row for row in csv.reader(open(site_list, newline='')) if any(cell.strip() for cell in row)]
    targets = []
    if rows and any(cell.strip().lower() in ('position', 'rank', 'index') for cell in rows[0]):
        header = [cell.strip().lower() for cell in rows[0]]
        rank_at = next(i for i, cell in enumerate(header) if cell in ('position', 'rank', 'index'))
        url_at = next((i for i, cell in enumerate(header) if cell in ('origin', 'domain', 'url', 'site')), None)
        if url_at is None:
            raise SystemExit(f'{site_list}: header has no origin/domain/url column')
        for offset, row in enumerate(rows[1:], 1):
            if url_at >= len(row):
                continue
            value = row[url_at].strip()
            if not value:
                continue
            cell = row[rank_at].strip() if rank_at < len(row) else ''
            targets.append((int(cell) if cell.isdigit() else offset, value))
        return targets
    for offset, row in enumerate(rows, 1):
        value = row[0].strip()
        if value:
            targets.append((offset, value))
    return targets


def target_url(value):
    """A manifest row is already a URL; a bare list entry is a domain."""
    if value.startswith(('http://', 'https://')):
        return value
    return f'https://www.{value}/'


def kill_profile(output):
    """Kill and remove the Chrome profile a timed-out invocation launched.

    The CLI cannot clean up after itself here: cdp.mjs removes the profile
    directory on normal exit and handles SIGINT/SIGTERM/SIGHUP, but a timed-out
    call is SIGKILLed (no handler runs) and leaves headless Chrome plus its
    profile behind. Only profile directories that are direct children of a real
    temp root are touched, so an arbitrary path that merely looks like a profile
    name cannot be removed.

    The temp root is resolved rather than assumed: `mkdtemp` honours TMPDIR, and a
    sweep run with TMPDIR on disk-backed storage (which /tmp inode exhaustion
    forces, see the os3 pilot) puts its profiles somewhere other than /tmp.

    `pkill -f` parses a leading `--` as an option and exits 2 without signalling
    anything, so the pattern must follow an explicit end-of-options marker.
    """
    candidates = set(re.findall(r'(/[^\s\'"]*?/web-uplift-cdp-[A-Za-z0-9_-]+)', output or ''))
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve(), Path('/var/tmp').resolve()}
    profiles = []
    for candidate in sorted(candidates):
        path = Path(candidate)
        try:
            parent = path.parent.resolve()
        except OSError:
            continue
        if path.name.startswith('web-uplift-cdp-') and parent in temp_roots:
            profiles.append(str(path))
    for profile in profiles:
        subprocess.run(['pkill', '-f', '--', f'--user-data-dir={profile}'], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)
    return profiles


def collect_modern_web(domain, url):
    """Run the modern-web feature probe for one site.

    Returns (report, artifact_path). Fails closed: a probe that timed out, wrote
    nothing, reported an unsupported result, or evaluated on Chrome's error page
    comes back as ``ok: False`` with the reason, because absent evidence must
    never be mistaken for "the site does not use these features".

    The artifact on disk is the evidence a later reader trusts, so it is written
    for failures too, and any artifact from an earlier run is removed first so a
    stale success can never be read back as this run's result.
    """
    artifact = os.path.join("evidence", safe_name(domain), "modern-web-features.json")
    artifact_path = Path(artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    def failure(reason):
        payload = {"probe": "modern-web-features", "ok": False, "url": url,
                   "error": reason, "artifact": artifact}
        artifact_path.write_text(json.dumps(payload, indent=2) + "\n")
        return payload, artifact

    artifact_path.unlink(missing_ok=True)

    args = ["node", MODERN_WEB_COLLECTOR, url, "--harness", EVIDENCE_CLI,
            "--wait", str(PROBE_WAIT_MS),
            "--expr-file", MODERN_WEB_PROBE,
            "--out", artifact]

    def captured(value):
        return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        returncode = result.returncode
        output = captured(result.stdout) + captured(result.stderr)
    except subprocess.TimeoutExpired as exc:
        kill_profile(captured(exc.stdout) + captured(exc.stderr))
        return failure(f"probe timed out after {PROBE_TIMEOUT}s")
    except Exception as e:
        return failure(f"{type(e).__name__}: {e}")

    if returncode != 0:
        return failure(f"probe exited {returncode}: {output.strip()[-200:]}")
    if not artifact_path.exists():
        return failure("probe wrote no evidence")
    try:
        with artifact_path.open() as handle:
            report = json.load(handle)
    except Exception as e:
        return failure(f"unreadable evidence: {type(e).__name__}: {e}")
    if report.get("ok") is not True:
        return failure(f"probe reported ok={report.get('ok')!r}")
    final_url = str(report.get("url") or "")
    if not final_url or final_url.startswith(("chrome-error://", "about:")):
        return failure(f"navigation failed: {final_url or 'no document url'}")
    report["artifact"] = artifact
    return report, artifact


def audit_site(domain, rank, url=None):
    url = url or f"https://www.{domain}/"
    print(f"  [{rank}] {url}...", end=" ", flush=True)
    t0 = time.time()
    r = {"domain": domain, "rank": rank, "url": url, "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    layout = run_evidence("layout", url, viewport="1280x900")
    obs = layout.get("observed", {}) if isinstance(layout, dict) else {}
    disc = run_evidence("discoverability", url)
    modern_web, modern_web_artifact = collect_modern_web(domain, url)

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
        "modern_web": modern_web,
        "modern_web_artifact": modern_web_artifact,
    }
    r["elapsed_s"] = round(time.time() - t0, 1)
    families = modern_web.get("css", {}).get("families", {}) if modern_web.get("ok") else {}
    used = ",".join(sorted(name for name, value in families.items() if value.get("used")))
    if not modern_web.get("ok"):
        # Never let a failed probe read as "this site uses none of it".
        modern = "PROBE FAILED: " + str(modern_web.get("error", ""))
    elif used:
        modern = used
    else:
        # Cross-origin stylesheets are unreadable from the page and derived
        # animations still fire, so "no family used" must not be printed as a
        # bare "none" when part of the CSS was never measured.
        sheets = modern_web.get("css", {}).get("sheets", {})
        caveats = []
        if modern_web.get("viewTransitions", {}).get("apiReferencedInExternalScript"):
            caveats.append("external JS references startViewTransition; runtime usage not measured")
        if modern_web.get("scriptInspection", {}).get("partial"):
            caveats.append("partial external script inspection")
        if sheets.get("inaccessible"):
            caveats.append(f"{sheets['inaccessible']}/{sheets.get('total', '?')} CSS sheets unreadable")
        if modern_web.get("animations", {}).get("withTimeline"):
            caveats.append(f"{modern_web['animations']['withTimeline']} live animation timeline(s)")
        modern = "none measured" + (f" ({'; '.join(caveats)})" if caveats else "")
    print(f"{r['elapsed_s']}s CLS={r['evidence']['cls']} overflow={r['evidence']['horizontal_overflow_px']}px "
          f"shell={r['evidence']['is_js_shell']} modern={modern}", flush=True)
    return r


def main(argv):
    site_list = argv[1] if len(argv) > 1 else "/tmp/crux-rank-1000-origins.txt"
    start_idx = int(argv[2]) if len(argv) > 2 else 0
    count = int(argv[3]) if len(argv) > 3 else 50

    targets = load_targets(site_list)
    batch = targets[start_idx:start_idx + count]
    print(f"Auditing {len(batch)} sites ({start_idx}-{start_idx+count-1}) from {site_list}", flush=True)
    os.makedirs("evidence", exist_ok=True)
    results = []
    for i, (rank, value) in enumerate(batch):
        url = target_url(value)
        domain = safe_name(re.sub(r'^https?://', '', value).split('/')[0])
        try:
            results.append(audit_site(domain, rank, url=url))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"domain": domain, "rank": rank, "url": url, "error": str(e)})
        if (i+1) % 10 == 0:
            json.dump(results, open(f"results-batch-{start_idx}.json", "w"), indent=2, default=str)
            print(f"  Saved {len(results)}", flush=True)
    json.dump(results, open(f"results-batch-{start_idx}.json", "w"), indent=2, default=str)
    print(f"\nDone: {len(results)} sites", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
