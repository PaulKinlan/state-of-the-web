#!/usr/bin/env python3
"""Speculative loading (Speculation Rules / prefetch / prerender) check & probe library.

Designed for the modern-web-guidance catalog (Option A next-generation expansion).
Covers:
1. Check definition: `be-fast-and-stable / speculative-loading`
2. Browser-driven in-page probe via `scripts/probes/speculative-loading.js`
3. Network/HAR analysis for speculative loading request/response headers
4. Synthesis of check outcome candidates (pass, issues, not-applicable, blocked)
5. CLI runner with immediate per-site evidence persistence and process leak guards
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROBE_JS = ROOT / "scripts" / "probes" / "speculative-loading.js"
EVIDENCE_CLI = Path(os.environ.get("WEB_UPLIFT_CLI", Path.home() / ".web-uplift" / "evidence" / "cli.mjs"))
DEFAULT_TIMEOUT = int(os.environ.get("SPECULATION_PROBE_TIMEOUT", "90"))
DEFAULT_WAIT_MS = int(os.environ.get("SPECULATION_PROBE_WAIT", "2000"))

# Canonical definition conforming strictly to catalog schema:
# Check schema keys: ['id', 'summary', 'detectableVia', 'guides']
# (Principles own 'applicability'; checks do not declare check-level applicability blocks)
CHECK_DEFINITION: dict[str, Any] = {
    "principleId": "be-fast-and-stable",
    "checkId": "speculative-loading",
    "summary": "Likely next navigations are speculatively loaded (Speculation Rules prefetch/prerender with an appropriate eagerness), rather than every navigation paying full cost.",
    "detectableVia": (
        "HINT: the evaluate primitive inspects <script type=\"speculationrules\"> for valid JSON, "
        "prefetch/prerender rules, document vs list sources, and eagerness levels; checks "
        "HTMLScriptElement.supports('speculationrules'); a HAR summary or network trace can corroborate "
        "Sec-Purpose: prefetch/prerender, Sec-Speculation-Tags, or deliveryType: navigational-prefetch requests. "
        "The model chooses."
    ),
    "guides": [
        "improve-next-page-load-performance",
    ],
}


def kill_profile(output: str) -> list[str]:
    """Kill any headless Chrome process and remove user-data-dir profiles on timeout (F5)."""
    profiles = sorted(set(re.findall(r"/tmp/web-uplift-cdp-[A-Za-z0-9_-]+", output or "")))
    for profile in profiles:
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={profile}"], capture_output=True)
        shutil.rmtree(profile, ignore_errors=True)
    return profiles


def run_speculative_probe(url: str, timeout: int = DEFAULT_TIMEOUT, wait_ms: int = DEFAULT_WAIT_MS) -> dict[str, Any]:
    """Execute the speculative-loading probe against a URL via CDP evaluate with leak guards."""
    if not EVIDENCE_CLI.exists():
        return {
            "probe": "speculative-loading",
            "ok": False,
            "url": url,
            "error": f"Evidence CLI not found at {EVIDENCE_CLI}",
        }
    if not PROBE_JS.exists():
        return {
            "probe": "speculative-loading",
            "ok": False,
            "url": url,
            "error": f"Speculative loading probe not found at {PROBE_JS}",
        }

    command = [
        "node", str(EVIDENCE_CLI), "evaluate", url,
        "--wait", str(wait_ms),
        "--expr-file", str(PROBE_JS),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return {
                "probe": "speculative-loading",
                "ok": False,
                "url": url,
                "error": f"CLI exited {proc.returncode}: {output[-200:].strip()}",
            }
        text = proc.stdout
        idx = text.rfind("\n{")
        raw = text[idx + 1:] if idx != -1 else text
        report = json.loads(raw)
        final_url = str(report.get("url") or "")
        if not final_url or final_url.startswith(("chrome-error://", "about:")):
            return {
                "probe": "speculative-loading",
                "ok": False,
                "url": url,
                "error": f"navigation failed: {final_url or 'no document url'}",
            }
        return report
    except subprocess.TimeoutExpired as exc:
        raw_out = (exc.stdout or "") + (exc.stderr or "")
        output_str = raw_out.decode(errors="replace") if isinstance(raw_out, bytes) else str(raw_out)
        kill_profile(output_str)
        return {
            "probe": "speculative-loading",
            "ok": False,
            "url": url,
            "error": f"timeout after {timeout}s",
        }
    except Exception as exc:
        return {
            "probe": "speculative-loading",
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


def parse_har_speculation_signals(har: dict[str, Any]) -> dict[str, Any]:
    """Extract speculative headers from a HAR 1.2 object."""
    entries = har.get("log", {}).get("entries", []) if isinstance(har, dict) else []
    speculative_requests = []
    header_rulesets = []
    prerender_eligible_responses = []

    for entry in entries:
        req = entry.get("request", {})
        resp = entry.get("response", {})
        url = req.get("url", "")
        req_headers = {h.get("name", "").lower(): h.get("value", "") for h in req.get("headers", [])}
        resp_headers = {h.get("name", "").lower(): h.get("value", "") for h in resp.get("headers", [])}

        sec_purpose = req_headers.get("sec-purpose", "").lower()
        purpose = req_headers.get("purpose", "").lower()
        tags = req_headers.get("sec-speculation-tags", "")

        is_prefetch = "prefetch" in sec_purpose or "prefetch" in purpose
        is_prerender = "prerender" in sec_purpose

        if is_prefetch or is_prerender:
            speculative_requests.append({
                "url": url,
                "type": "prerender" if is_prerender else "prefetch",
                "secPurpose": sec_purpose or None,
                "purpose": purpose or None,
                "tags": tags or None,
                "status": resp.get("status"),
            })

        if "speculation-rules" in resp_headers:
            header_rulesets.append({
                "url": url,
                "header": "Speculation-Rules",
                "value": resp_headers["speculation-rules"],
            })

        link = resp_headers.get("link", "")
        if "speculationrules" in link.lower():
            header_rulesets.append({
                "url": url,
                "header": "Link",
                "value": link,
            })

        loading_mode = resp_headers.get("supports-loading-mode", "").lower()
        if "credentialed-prerender" in loading_mode:
            prerender_eligible_responses.append({
                "url": url,
                "supportsLoadingMode": loading_mode,
            })

    return {
        "speculativeRequestsCount": len(speculative_requests),
        "speculativeRequests": speculative_requests[:10],
        "headerRulesetsCount": len(header_rulesets),
        "headerRulesets": header_rulesets,
        "prerenderEligibleResponsesCount": len(prerender_eligible_responses),
        "prerenderEligibleResponses": prerender_eligible_responses,
        "hasSpeculativeNetworkActivity": len(speculative_requests) > 0 or len(header_rulesets) > 0,
    }


def synthesize_check_outcome(
    probe_data: dict[str, Any],
    har_data: dict[str, Any] | None = None,
    is_spa_override: bool | None = None,
) -> dict[str, Any]:
    """Derive an atomic check outcome candidate from probe and HAR evidence (F3 & F4).

    Correctly enforces applicability:
    - SPAs with client-side routing -> not-applicable
    - Single-surface tools with 0 links -> not-applicable
    - Pages with external links only -> not-applicable
    - Multi-page sites with internal links -> issues (if missing rules) or pass (if present)
    """
    if not probe_data.get("ok"):
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "blocked",
            "confidence": "high",
            "method": "CDP evaluate speculation-rules probe",
            "evidence": probe_data.get("error", "probe execution failed"),
            "reason": f"Probe execution failed: {probe_data.get('error', 'unknown error')}",
        }

    signals = probe_data.get("signals", {})
    rules = probe_data.get("speculationRules", {})
    nav_ctx = probe_data.get("navigationContext", {})
    har_signals = parse_har_speculation_signals(har_data) if har_data else {}

    errors = rules.get("errors", [])
    if errors:
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "issues",
            "confidence": "high",
            "method": "CDP evaluate speculation-rules probe",
            "evidence": f"Malformed speculation rules: {errors[0].get('error')} (snippet: {errors[0].get('snippet', '')})",
            "reason": "Speculation rules script contains invalid syntax or malformed JSON.",
        }

    has_speculation = signals.get("hasSpeculationRules") or har_signals.get("hasSpeculativeNetworkActivity")

    if has_speculation:
        features = []
        if signals.get("hasPrerender"):
            features.append("prerender")
        if signals.get("hasPrefetch"):
            features.append("prefetch")
        if signals.get("hasDocumentRules"):
            features.append("document rules")
        if signals.get("hasListRules"):
            features.append("list rules")
        eagerness = ", ".join(signals.get("eagernessLevels", []))
        if eagerness:
            features.append(f"eagerness: {eagerness}")
        if har_signals.get("speculativeRequestsCount"):
            features.append(f"{har_signals['speculativeRequestsCount']} speculative HTTP request(s)")

        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "pass",
            "confidence": "high",
            "method": "CDP evaluate speculation-rules probe and HAR network inspection",
            "evidence": f"Speculative loading configured: {', '.join(features)}",
        }

    # Applicability evaluations when no Speculation Rules are found (F3):
    total_links = nav_ctx.get("anchorCount", 0)
    internal_links = nav_ctx.get("internalLinkCount", 0)
    external_links = nav_ctx.get("externalLinkCount", 0)
    is_client_routed = is_spa_override if is_spa_override is not None else nav_ctx.get("isClientSideRouted", False)
    framework = nav_ctx.get("frameworkRouter")

    # Case 1: Page has zero links (single-surface utility or isolated page)
    if total_links == 0:
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "not-applicable",
            "confidence": "high",
            "method": "DOM anchor inspection and speculation probe",
            "evidence": "Page contains zero navigation links; isolated single-surface view.",
            "reason": "Single-surface utility with no subsequent navigation paths to speculate.",
        }

    # Case 2: Page contains only external links
    if internal_links == 0 and external_links > 0:
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "not-applicable",
            "confidence": "high",
            "method": "DOM anchor inspection and speculation probe",
            "evidence": f"Page contains {external_links} external links and zero internal navigation links.",
            "reason": "Exposes external links only; cross-origin speculative loading is not applicable without target opt-in.",
        }

    # Case 3: Single-Page Application using client-side routing (F3 fix)
    if is_client_routed:
        router_desc = f" ({framework})" if framework else ""
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "not-applicable",
            "confidence": "medium",
            "method": "Client-side router detection and anchor inspection",
            "evidence": f"Single-page application using client-side router{router_desc} with {internal_links} client-routed links.",
            "reason": "Single-page application performs internal view transitions client-side without document navigations.",
        }

    # Case 4: Multi-page site with internal navigation links but no speculative loading
    legacy = probe_data.get("legacySpeculation", {})
    legacy_notes = []
    if legacy.get("linkPrefetch"):
        legacy_notes.append(f"{legacy['linkPrefetch']} <link rel=prefetch>")
    if legacy.get("linkPrerender"):
        legacy_notes.append(f"{legacy['linkPrerender']} legacy <link rel=prerender>")

    evidence_str = (
        f"Multi-page site has {internal_links} internal navigation links but no <script type=\"speculationrules\"> "
        f"or Speculation-Rules headers."
    )
    if legacy_notes:
        evidence_str += f" Uses legacy hints ({', '.join(legacy_notes)}) instead of modern Speculation Rules."

    return {
        "principleId": "be-fast-and-stable",
        "checkId": "speculative-loading",
        "status": "issues",
        "confidence": "high",
        "method": "CDP evaluate speculation-rules probe",
        "evidence": evidence_str,
        "reason": "Multi-page navigation flows do not leverage Speculation Rules to reduce navigation latency.",
    }


def probe_single_target(url: str, out_path: Path | None = None) -> dict[str, Any]:
    """Execute probe for one target, synthesize outcome, and persist immediately (F6)."""
    raw_probe = run_speculative_probe(url)
    outcome = synthesize_check_outcome(raw_probe)
    record = {
        "url": url,
        "auditedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probe": raw_probe,
        "outcome": outcome,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Speculative loading probe runner")
    parser.add_argument("target", help="URL or path to manifest / origin list (CSV/txt)")
    parser.add_argument("--out", type=Path, default=None, help="Output file path (single URL) or directory (manifest)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-target timeout in seconds")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT_MS, help="Settle wait in ms before evaluate")
    args = parser.parse_args(argv)

    target_str = args.target.strip()
    if target_str.startswith(("http://", "https://", "file://")):
        out_file = args.out if args.out else Path(f"evidence/speculative-loading/{re.sub(r'[^A-Za-z0-9.-]+', '-', target_str)}.json")
        res = probe_single_target(target_str, out_file)
        status = res["outcome"]["status"]
        print(f"[{status}] {target_str} -> {out_file}: {res['outcome']['evidence']}")
        return 0 if res["probe"].get("ok") else 1

    manifest_path = Path(target_str)
    if not manifest_path.exists():
        print(f"Target manifest file not found: {manifest_path}", file=sys.stderr)
        return 2

    out_dir = args.out if args.out else Path("evidence/speculative-loading")
    out_dir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if manifest_path.suffix == ".csv":
        reader = csv.reader(lines)
        for row in reader:
            if row and row[0].isdigit() and len(row) > 1 and row[1].startswith("http"):
                urls.append(row[1].strip())
            elif row and row[0].startswith("http"):
                urls.append(row[0].strip())
    else:
        for line in lines:
            line = line.strip()
            if line:
                urls.append(line if line.startswith("http") else f"https://{line}/")

    print(f"Probing speculative loading across {len(urls)} targets -> {out_dir}")
    success_count = 0
    for idx, u in enumerate(urls, 1):
        slug = re.sub(r"[^A-Za-z0-9.-]+", "-", u).strip("-")
        out_file = out_dir / f"{idx:04d}-{slug}.json"
        res = probe_single_target(u, out_file)
        if res["probe"].get("ok"):
            success_count += 1
        print(f"[{idx}/{len(urls)}] [{res['outcome']['status']}] {u}")

    print(f"Complete: {success_count}/{len(urls)} targets probed successfully")
    return 0 if success_count == len(urls) else 1


if __name__ == "__main__":
    sys.exit(main())
