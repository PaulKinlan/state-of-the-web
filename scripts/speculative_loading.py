#!/usr/bin/env python3
"""Speculative loading (Speculation Rules / prefetch / prerender) check & probe library.

Designed for the modern-web-guidance catalog (Option A next-generation expansion).
Covers:
1. Check definition: `be-fast-and-stable / speculative-loading`
2. Browser-driven in-page probe via `scripts/probes/speculative-loading.js`
3. Network/HAR analysis for speculative loading request/response headers
4. Synthesis of check outcome candidates (pass, issues, not-applicable)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROBE_JS = ROOT / "scripts" / "probes" / "speculative-loading.js"
EVIDENCE_CLI = Path(os.environ.get("WEB_UPLIFT_CLI", Path.home() / ".web-uplift" / "evidence" / "cli.mjs"))

# Canonical definition for modern-web-guidance catalog expansion
CHECK_DEFINITION = {
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
    "applicability": {
        "expectation": "contextual",
        "description": (
            "Applies to multi-page sites and applications with internal navigation links. "
            "Single-page applications that execute purely client-side transitions without navigation, "
            "or standalone single-surface tools without internal navigation journeys, are legitimately "
            "not-applicable with an explicit rationale."
        ),
    },
    "guides": [
        "improve-next-page-load-performance",
        "speculative-loading-speculation-rules",
        "prerender-pages-chrome",
    ],
}


def run_speculative_probe(url: str, timeout: int = 60, wait_ms: int = 1500) -> dict[str, Any]:
    """Execute the speculative-loading probe against a URL via CDP evaluate."""
    if not EVIDENCE_CLI.exists():
        raise FileNotFoundError(f"Evidence CLI not found at {EVIDENCE_CLI}")
    if not PROBE_JS.exists():
        raise FileNotFoundError(f"Speculative loading probe not found at {PROBE_JS}")

    command = [
        "node", str(EVIDENCE_CLI), "evaluate", url,
        "--wait", str(wait_ms),
        "--expr-file", str(PROBE_JS),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            return {
                "probe": "speculative-loading",
                "ok": False,
                "url": url,
                "error": f"CLI exited {proc.returncode}: {(proc.stderr or '')[-200:].strip()}",
            }
        # The CLI outputs JSON at the end
        text = proc.stdout
        idx = text.rfind("\n{")
        raw = text[idx + 1:] if idx != -1 else text
        report = json.loads(raw)
        final_url = str(report.get("url") or "")
        if final_url.startswith(("chrome-error://", "about:")):
            return {
                "probe": "speculative-loading",
                "ok": False,
                "url": url,
                "error": f"navigation failed: {final_url}",
            }
        return report
    except subprocess.TimeoutExpired:
        return {"probe": "speculative-loading", "ok": False, "url": url, "error": f"timeout after {timeout}s"}
    except Exception as exc:
        return {"probe": "speculative-loading", "ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


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

        # Request signals
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

        # Response signals
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


def synthesize_check_outcome(probe_data: dict[str, Any], har_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive an atomic check outcome candidate from probe and HAR evidence."""
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

    # If no speculation rules found, check applicability
    internal_links = nav_ctx.get("internalLinkCount", 0)
    total_links = nav_ctx.get("anchorCount", 0)

    if total_links == 0 or (internal_links == 0 and nav_ctx.get("externalLinkCount", 0) == 0):
        return {
            "principleId": "be-fast-and-stable",
            "checkId": "speculative-loading",
            "status": "not-applicable",
            "confidence": "medium",
            "method": "DOM anchor inspection and speculation probe",
            "evidence": f"Page has {total_links} navigation links; single-surface view with no internal navigation flows.",
            "reason": "Single-surface utility with no subsequent internal navigation paths to speculate.",
        }

    # Multi-page site with internal navigation links but no speculative loading
    legacy = probe_data.get("legacySpeculation", {})
    legacy_notes = []
    if legacy.get("linkPrefetch"):
        legacy_notes.append(f"{legacy['linkPrefetch']} <link rel=prefetch>")
    if legacy.get("linkPrerender"):
        legacy_notes.append(f"{legacy['linkPrerender']} legacy <link rel=prerender>")

    evidence_str = (
        f"Page has {internal_links} internal navigation links but no <script type=\"speculationrules\"> "
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
