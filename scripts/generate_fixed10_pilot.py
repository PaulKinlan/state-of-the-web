#!/usr/bin/env python3
"""Generate the public fixed-10 operational pilot derivative from private evidence.

Only explicitly allowlisted fields are read. The source root must be supplied on the
command line; no source path is serialized into public output.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE_COMMIT = "352d52152883198ad6a10942deed7e4cee681e37"
DATASET_ID = "web-uplift-fixed10-operational-pilot-2026-08-01"
EXPECTED = 58
JUDGED = {"pass", "issues", "not-applicable", "opted-out"}
KNOWN_STATUSES = JUDGED | {"blocked", "not-run"}
DISPOSITION_MAP = {
    "audit-completed": "runner-completed",
    "audit-partial": "partial",
    "audit-error": "error",
}
REASON_SUMMARIES = {
    1: "The authoritative ledger records missing-report. Permit expiry is not claimed: the retained timestamps place the start before expiry.",
    2: "The authoritative ledger records runner-error. The runner invalidated completed journey states that crossed away from the reviewed origin.",
    3: "The ledger classified this row completed, but its console pass used an unsupported absence-of-error method.",
    4: "Atomic coverage was incomplete after a humanity challenge and cross-origin navigation block.",
    5: "Atomic coverage was incomplete after mutation-like POST traffic was blocked.",
    6: "The ledger classified this row completed, but its console pass used an unsupported absence-of-error method.",
    7: "Atomic coverage was incomplete after mutation-like POST traffic was blocked; six checks were blocked and two were not run.",
    8: "Atomic coverage was incomplete after mutation-like OPTIONS traffic was blocked.",
    9: "Atomic coverage was incomplete under mutation and scope boundaries and unavailable diagnostic or account-flow evidence.",
    10: "Atomic coverage was incomplete because exact-origin scope left primary, error, account, and diagnostic flows unavailable.",
}
ACTION_REASON_CODES = {
    "mutation-like-url": "mutation-like-url",
    "mutation-method-POST": "mutation-method-post",
    "mutation-method-OPTIONS": "mutation-method-options",
    "cross-origin-document": "cross-origin-document",
    "journey aborted after attempted mutation/network/navigation": "journey-aborted-after-blocker",
    "same-origin GET navigation completed": "same-origin-get-navigation-completed",
    "scrolled 246 CSS px": "bounded-scroll-completed",
}
SCREENSHOT_ALTS = {
    1: "Signed-out GitHub landing surface with an empty email field.",
    2: "Blank dark Facebook journey baseline frame.",
    3: "Signed-out Google consent surface.",
    4: "Reddit humanity challenge page.",
    5: "Blank white Amazon journey baseline frame.",
    6: "Signed-out English Wikipedia landing surface.",
    7: "New York Times privacy preferences surface with no filled controls.",
    8: "Signed-out Gemini consent surface.",
    9: "Signed-out Netflix landing surface with an empty email field.",
    10: "Apple region selector above a public iPhone landing surface.",
}
VIDEO_DESCRIPTIONS = {
    4: "A silent 0.1-second retained frame of the Reddit humanity challenge under reduced-motion capture.",
    7: "A silent 2.3-second reduced-motion scroll from a generic privacy panel to public news content.",
    10: "A silent 2.5-second reduced-motion scroll from a region selector through public product content.",
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("expected a JSON object in allowlisted source")
    return value


def load_jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise SystemExit("expected JSON objects in allowlisted JSONL source")
    return rows


def source_roots(root: Path) -> tuple[Path, Path]:
    root = root.resolve()
    private = root / "private" if (root / "private").is_dir() else root
    artifact = private.parent
    for filename in ("cohort.jsonl", "cohort-events.jsonl", "cohort-summary.json"):
        if not (private / filename).is_file():
            raise SystemExit(f"source root is missing {filename}")
    for filename in ("mcp-start-evidence.json", "cleanup-result.json"):
        if not (artifact / filename).is_file():
            raise SystemExit(f"source root is missing private receipt {filename}")
    return artifact, private


def unique_paths(paths: list[Path]) -> list[Path]:
    return sorted({path.resolve() for path in paths})


def unique_glob(private: Path, pattern: str, required: bool = True) -> Path | None:
    matches = unique_paths(list(private.glob(pattern)))
    if not matches and not required:
        return None
    if len(matches) != 1:
        raise SystemExit(f"allowlisted selector expected one source, found {len(matches)}")
    matches[0].relative_to(private.resolve())
    return matches[0]


def normalize_origin(value: object) -> str:
    if not isinstance(value, str):
        raise SystemExit("origin is not a string")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise SystemExit("invalid source origin")
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)
    port = "" if parts.port is None or default_port else f":{parts.port}"
    return f"{parts.scheme}://{host}{port}"


def verify_ledger(events: list[dict]) -> None:
    previous = None
    for event in events:
        recorded_previous = event.get("previousEventSha256")
        if recorded_previous != previous:
            raise SystemExit("cohort event chain predecessor mismatch")
        expected = event.get("eventSha256")
        unsigned = {key: value for key, value in event.items() if key != "eventSha256"}
        actual = "sha256:" + sha256_bytes(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())
        if expected != actual:
            raise SystemExit("cohort event hash mismatch")
        previous = expected


def validate_number(value: object, nullable: bool = False) -> int | float | None:
    if value is None and nullable:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise SystemExit("invalid numeric aggregate")
    return value


def coverage_from_report(report: dict | None) -> dict:
    if report is None:
        return {"blocked": 0, "duplicates": 0, "expected": EXPECTED, "judged": 0, "missing": EXPECTED, "notRun": 0, "recorded": 0, "unknown": 0}
    outcomes = report.get("checkOutcomes")
    if not isinstance(outcomes, list):
        raise SystemExit("report checkOutcomes is missing")
    pairs: list[tuple[str, str]] = []
    statuses: list[str] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise SystemExit("invalid check outcome")
        principle = outcome.get("principleId")
        check = outcome.get("checkId")
        status = outcome.get("status")
        if not all(isinstance(value, str) and value for value in (principle, check, status)):
            raise SystemExit("invalid check outcome fields")
        pairs.append((principle, check))
        statuses.append(status)
    counts = Counter(statuses)
    recorded = len(outcomes)
    duplicates = recorded - len(set(pairs))
    unknown = sum(count for status, count in counts.items() if status not in KNOWN_STATUSES)
    return {
        "blocked": counts["blocked"],
        "duplicates": duplicates,
        "expected": EXPECTED,
        "judged": sum(counts[status] for status in JUDGED),
        "missing": max(0, EXPECTED - len(set(pairs))),
        "notRun": counts["not-run"],
        "recorded": recorded,
        "unknown": unknown,
    }


def journey_from_flow(private: Path, ordinal: int, artifact: Path) -> dict:
    flow_path = unique_glob(private, f"{ordinal:04d}-*/*/evidence/flow/flow-result.json", required=False)
    if flow_path is None:
        if ordinal != 2:
            raise SystemExit(f"row {ordinal}: flow result unexpectedly missing")
        pilot_log = (artifact / "pilot.log").read_text(encoding="utf-8", errors="strict")
        marker = "invalid journey result: completed cross-origin state"
        if "https://web.facebook.com" not in pilot_log or pilot_log.count(marker) < 1:
            raise SystemExit("Facebook cross-origin invalidation is not supported by the private runner log")
        return {
            "actions": [
                {"ordinal": 1, "outcome": "invalidated-cross-origin-state", "reasonCode": "completed-cross-origin-state", "type": "baseline-load"},
                {"ordinal": 2, "outcome": "invalidated-cross-origin-state", "reasonCode": "completed-cross-origin-state", "type": "bounded-scroll"},
            ],
            "status": "invalid",
        }
    flow = load_json(flow_path)
    result = flow.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("actions"), list):
        raise SystemExit("invalid flow result")
    actions = []
    for action in result["actions"]:
        detail = action.get("detail")
        if detail not in ACTION_REASON_CODES:
            raise SystemExit("unrecognized journey detail cannot be published")
        actions.append({
            "ordinal": action.get("ordinal"),
            "outcome": action.get("outcome"),
            "reasonCode": ACTION_REASON_CODES[detail],
            "type": action.get("type"),
        })
    if [action["ordinal"] for action in actions] != [1, 2]:
        raise SystemExit("journey action ordinals drifted")
    return {"actions": actions, "status": result.get("status")}


def find_network_summary(private: Path, ordinal: int) -> Path:
    matches = []
    for name in ("network-summary.json", "load-summary.json"):
        matches.extend(private.glob(f"{ordinal:04d}-*/*/evidence/**/{name}"))
    unique = unique_paths(matches)
    if len(unique) != 1:
        raise SystemExit(f"row {ordinal}: expected one network summary")
    return unique[0]


def find_performance_summary(private: Path, ordinal: int) -> Path:
    matches = []
    for name in ("perf-summary.json", "performance-summary.json", "trace-summary.json", "load-trace-summary.json"):
        matches.extend(private.glob(f"{ordinal:04d}-*/*/evidence/**/{name}"))
    unique = unique_paths(matches)
    if len(unique) != 1:
        raise SystemExit(f"row {ordinal}: expected one performance summary")
    return unique[0]


def find_har(private: Path, ordinal: int) -> dict:
    files = unique_paths(list(private.glob(f"{ordinal:04d}-*/*/evidence/**/*")))
    candidates = []
    for path in files:
        if not path.is_file() or path.stat().st_size > 10_000_000:
            continue
        if path.name not in {"network", "network.har", "load.har"}:
            continue
        try:
            value = load_json(path)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value.get("log"), dict) and isinstance(value["log"].get("entries"), list):
            candidates.append(value)
    if len(candidates) != 1:
        raise SystemExit(f"row {ordinal}: expected one HAR source for aggregation")
    return candidates[0]


def aggregate_facts(private: Path, ordinal: int, origin: str) -> dict | None:
    if ordinal < 3:
        return None
    network = load_json(find_network_summary(private, ordinal))
    totals = network.get("totals")
    third_party = network.get("thirdParty")
    if not isinstance(totals, dict) or not isinstance(third_party, dict):
        raise SystemExit("network summary shape drifted")
    resources = {}
    for name, facts in sorted(totals.get("byResourceType", {}).items()):
        if not isinstance(name, str) or not isinstance(facts, dict):
            raise SystemExit("invalid resource aggregate")
        resources[name] = {
            "count": validate_number(facts.get("count")),
            "transferBytes": validate_number(facts.get("transferredBytes")),
        }
    har = find_har(private, ordinal)
    statuses: Counter[str] = Counter()
    origins: set[str] = set()
    for entry in har["log"]["entries"]:
        if not isinstance(entry, dict):
            raise SystemExit("invalid HAR entry")
        status = entry.get("response", {}).get("status")
        if isinstance(status, int) and 100 <= status <= 599:
            statuses[f"{status // 100}xx"] += 1
        else:
            statuses["no-response"] += 1
        url = entry.get("request", {}).get("url")
        try:
            origins.add(normalize_origin(url))
        except (SystemExit, ValueError):
            continue
    headers_path = unique_glob(private, f"{ordinal:04d}-*/*/evidence/**/headers.json")
    headers = load_json(headers_path).get("securityHeaders")
    allowed_headers = ["content-security-policy", "permissions-policy", "referrer-policy", "strict-transport-security", "x-content-type-options", "x-frame-options"]
    if not isinstance(headers, dict) or set(headers) != set(allowed_headers):
        raise SystemExit("security header source shape drifted")
    header_presence = {name: headers[name].get("present") is True for name in allowed_headers}

    cookies_path = unique_glob(private, f"{ordinal:04d}-*/*/evidence/**/cookies.json")
    cookies_doc = load_json(cookies_path)
    cookies = cookies_doc.get("cookies")
    if not isinstance(cookies, list):
        raise SystemExit("cookie source shape drifted")
    same_site = Counter(str(cookie.get("sameSite", "")).lower() or "unspecified" for cookie in cookies if isinstance(cookie, dict))
    cookie_counts = {
        "httpOnly": sum(cookie.get("httpOnly") is True for cookie in cookies if isinstance(cookie, dict)),
        "sameSiteLax": same_site["lax"],
        "sameSiteNone": same_site["none"],
        "sameSiteStrict": same_site["strict"],
        "sameSiteUnspecified": same_site["unspecified"],
        "secure": sum(cookie.get("secure") is True for cookie in cookies if isinstance(cookie, dict)),
        "thirdParty": sum(cookie.get("isThirdParty") is True for cookie in cookies if isinstance(cookie, dict)),
        "total": len(cookies),
    }

    performance = load_json(find_performance_summary(private, ordinal))
    main = performance.get("mainThread")
    timings = performance.get("timings")
    if not isinstance(main, dict) or not isinstance(timings, dict):
        raise SystemExit("performance summary shape drifted")
    performance_facts = {
        "firstContentfulPaintMs": validate_number(timings.get("firstContentfulPaintMs"), nullable=True),
        "largestContentfulPaintMs": validate_number(timings.get("largestContentfulPaintMs"), nullable=True),
        "loadEventEndMs": validate_number(timings.get("loadEventEndMs"), nullable=True),
        "longTaskCount": validate_number(main.get("longTaskCount")),
        "longestTaskMs": validate_number(main.get("longestTaskMs")),
        "totalBlockingTimeMs": validate_number(main.get("totalBlockingTimeMs")),
        "traceDurationMs": validate_number(timings.get("traceDurationMs")),
    }
    first_party = 1 if origin in origins else 0
    return {
        "cookieAttributeCounts": cookie_counts,
        "firstPartyOriginCount": first_party,
        "httpStatusClassCounts": dict(sorted(statuses.items())),
        "performance": performance_facts,
        "requestCount": validate_number(totals.get("requestCount")),
        "resourceTypes": resources,
        "securityHeaderPresence": header_presence,
        "thirdPartyOriginCount": len(origins) - first_party,
        "transferBytes": validate_number(totals.get("totalTransferredBytes")),
    }


def report_for_row(private: Path, ordinal: int) -> dict | None:
    path = unique_glob(private, f"{ordinal:04d}-*/*/report.json", required=False)
    return load_json(path) if path else None


def fmt_number(value: object, unit: str = "") -> str:
    if value is None:
        return "not observed"
    return f"{value:,}{unit}"


def render_actions(actions: list[dict]) -> str:
    items = "".join(
        f"<li><strong>{html.escape(action['type'])}:</strong> {html.escape(action['outcome'])} "
        f"(<code>{html.escape(action['reasonCode'])}</code>)</li>" for action in actions
    )
    return f"<ol class=actions>{items}</ol>"


def render_aggregate(aggregate: dict | None) -> str:
    if aggregate is None:
        return "<p>No network or performance summary was derivable because no report was produced.</p>"
    perf = aggregate["performance"]
    present = sum(aggregate["securityHeaderPresence"].values())
    total_headers = len(aggregate["securityHeaderPresence"])
    return (
        "<dl class=facts>"
        f"<div><dt>Requests</dt><dd>{fmt_number(aggregate['requestCount'])}</dd></div>"
        f"<div><dt>Transfer</dt><dd>{fmt_number(aggregate['transferBytes'], ' bytes')}</dd></div>"
        f"<div><dt>Observed origins</dt><dd>{aggregate['firstPartyOriginCount']} first-party; {aggregate['thirdPartyOriginCount']} third-party</dd></div>"
        f"<div><dt>Security headers present</dt><dd>{present} of {total_headers}</dd></div>"
        f"<div><dt>Cookies observed</dt><dd>{aggregate['cookieAttributeCounts']['total']} total; {aggregate['cookieAttributeCounts']['secure']} Secure; {aggregate['cookieAttributeCounts']['httpOnly']} HttpOnly</dd></div>"
        f"<div><dt>Trace timings</dt><dd>FCP {fmt_number(perf['firstContentfulPaintMs'], ' ms')}; LCP {fmt_number(perf['largestContentfulPaintMs'], ' ms')}; {fmt_number(perf['longTaskCount'])} long tasks</dd></div>"
        "</dl>"
    )


def render_media(ordinal: int, receipts: list[dict]) -> str:
    screenshot = next(receipt for receipt in receipts if receipt["kind"] == "screenshot")
    video = next((receipt for receipt in receipts if receipt["kind"] == "video"), None)
    image = (
        f"<figure><img src=\"{html.escape(screenshot['outputFile'])}\" "
        f"alt=\"{html.escape(SCREENSHOT_ALTS[ordinal])}\" width=\"{screenshot['width']}\" height=\"{screenshot['height']}\" "
        "loading=\"lazy\" decoding=\"async\"><figcaption>Sanitized baseline screenshot. Metadata stripped; OCR and visual privacy review passed.</figcaption></figure>"
    )
    if not video:
        return image
    video_markup = (
        f"<figure><video controls playsinline preload=\"metadata\" width=\"{video['width']}\" height=\"{video['height']}\" "
        f"poster=\"{html.escape(screenshot['outputFile'])}\"><source src=\"{html.escape(video['outputFile'])}\" type=\"video/mp4\"></video>"
        f"<figcaption>{html.escape(VIDEO_DESCRIPTIONS[ordinal])} It has no audio; this caption describes the full visual content. First, middle, and last frame OCR and visual privacy review passed.</figcaption></figure>"
    )
    return image + video_markup


def render_html(dataset: dict, media_manifest: dict) -> str:
    media_by_row: dict[int, list[dict]] = {ordinal: [] for ordinal in range(1, 11)}
    for receipt in media_manifest["receipts"]:
        media_by_row[receipt["ordinal"]].append(receipt)
    table_rows = []
    cards = []
    for row in dataset["rows"]:
        c = row["coverage"]
        label = row["disposition"]
        table_rows.append(
            f"<tr><th scope=row>{row['ordinal']}. <code>{html.escape(row['origin'])}</code></th>"
            f"<td>{html.escape(row['archetype'])}</td><td><strong class=\"status {label}\">{html.escape(label)}</strong><br><code>{html.escape(str(row['reasonCode']))}</code></td>"
            f"<td>{c['judged']} judged; {c['blocked']} blocked; {c['notRun']} not run; {c['missing']} missing</td>"
            f"<td>{html.escape(row['journey']['status'])}</td></tr>"
        )
        cards.append(
            f"<article class=\"row-card deferred status-{label}\"><h3>{row['ordinal']}. <code>{html.escape(row['origin'])}</code></h3>"
            f"<p><strong class=\"status {label}\">Ledger: {html.escape(label)}</strong> · {html.escape(row['archetype'])}</p>"
            f"<p><strong>Reason:</strong> <code>{html.escape(str(row['reasonCode']))}</code>. {html.escape(row['reasonSummary'])}</p>"
            f"<p><strong>Recomputed coverage:</strong> expected {c['expected']}; recorded {c['recorded']}; judged {c['judged']}; blocked {c['blocked']}; not run {c['notRun']}; missing {c['missing']}; unknown {c['unknown']}; duplicates {c['duplicates']}.</p>"
            f"<h4>Journey actions</h4>{render_actions(row['journey']['actions'])}<h4>Safe aggregates</h4>{render_aggregate(row['aggregates'])}"
            f"<div class=media>{render_media(row['ordinal'], media_by_row[row['ordinal']])}</div></article>"
        )
    source = dataset["source"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta name="color-scheme" content="light dark"><meta name="description" content="Sanitized operational fixed-10 Web Uplift journey pilot for State of the Web."><title>Fixed-10 journey pilot | State of the Web</title><link rel="stylesheet" href="styles.css"></head>
<body><a class="skip-link" href="#content">Skip to report</a><header><nav aria-label="Breadcrumb"><a href="../index.html">State of the Web inventory</a></nav><p class="eyebrow">Operational convenience pilot · fixed denominator 10</p><h1>Fixed-10 Web Uplift journey pilot</h1><p class="lede">A sanitized, point-in-time operational sub-report about runner behavior across ten fixed, reviewed origins. This is not a site ranking, not a quality league table, and not a replacement for the main State of the Web report.</p></header>
<main id="content" tabindex="-1"><section class="warning"><h2>Read this before interpreting the ledger</h2><p><strong>The authoritative hash-chained ledger classified 2 rows runner-completed, 6 partial, and 2 errors.</strong> Both runner-completed rows contain method-invalid console passes: the collector was unsupported and absence of surfaced errors was treated as evidence. Therefore this report publishes <strong>no scores, pass rates, rankings, or completed-quality claims</strong>. “Runner-completed” means only that the ledger classified the row completed.</p></section>
<section><h2>Ledger disposition</h2><div class="summary-cards"><article><strong>10</strong><span>fixed origins</span></article><article><strong>2</strong><span>ledger runner-completed</span></article><article><strong>6</strong><span>partial</span></article><article><strong>2</strong><span>errors</span></article></div><p>No target was replaced or retried. GitHub is correctly reported as <code>missing-report</code>; permit expiry is not claimed because the retained timestamps disprove it. Facebook is an error because the runner invalidated completed cross-origin journey states.</p></section>
<section class="deferred"><h2>Ten-row inventory</h2><div class="table-wrap"><table><caption>Authoritative ledger disposition, recomputed atomic coverage, and journey status for the fixed ten origins</caption><thead><tr><th scope=col>Origin</th><th scope=col>Archetype</th><th scope=col>Disposition / reason</th><th scope=col>Coverage</th><th scope=col>Journey</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div><div class="mobile-cards">{''.join(cards)}</div></section>
<section class="desktop-details deferred"><h2>Row evidence details</h2>{''.join(cards)}</section>
<section class="deferred"><h2>Methodology</h2><ol><li>Keep the reviewed convenience cohort fixed at ten origins, with no substitutions or automatic retries.</li><li>Verify the hash-chained event ledger and use it as the sole disposition authority. Stale <code>site-run</code> pending states and conflicting report states are ignored.</li><li>Recompute coverage from the 58 atomic check outcomes. A missing report produces 58 missing rows.</li><li>Allowlist only origins, categorical outcomes, counts, bytes, timing aggregates, header presence, and cookie-attribute counts. URLs are reduced to origins before counting.</li><li>Deterministically re-encode selected media, strip metadata, verify hashes and dimensions, and admit it only after OCR and visual privacy review.</li></ol><p>Network and trace facts describe one retained collection under its recorded conditions. They are not lab scores and are not comparable performance rankings.</p></section>
<section class="deferred"><h2>Limitations</h2><ul><li>This is a fixed, reviewed convenience pilot, not a representative sample. No population inference is supported.</li><li>Observations are limited to public landing surfaces, reachable states, strict mutation containment, and the captured window.</li><li>Authentication walls, humanity challenges, exact-origin scope, and unavailable Stage 2 collectors limited journey and check coverage.</li><li>The ledger is authoritative for disposition, but its two completed classifications do not establish valid completion quality because both console methods were unsupported.</li><li>Cleanup evidence is an operational assertion rather than authenticated per-session teardown receipts; additional audit profiles observed in private logs were outside the ten-profile cleanup list.</li></ul></section>
<section class="deferred"><h2>Public evidence and provenance</h2><ul><li><a href="data/pilot.json">Sanitized aggregate JSON</a></li><li><a href="data/public-manifest.json">Public evidence manifest</a></li><li><a href="data/media-manifest.json">Media transform and review receipts</a></li><li><a href="data/provenance.json">Source and provenance hashes</a></li><li><a href="data/cleanup-summary.json">Sanitized cleanup summary</a></li><li><a href="data/schema.json">Strict aggregate schema</a></li></ul><p>Runner commit: <code>{source['runnerCommit']}</code><br>Pilot manifest SHA-256: <code>{source['pilotManifestSha256']}</code><br>Source manifest SHA-256: <code>{source['sourceManifestSha256']}</code><br>Policy SHA-256: <code>{source['policySha256']}</code><br>Ledger SHA-256: <code>{source['ledgerSha256']}</code></p><p><strong>Publication authorization:</strong> explicit authorization for this sanitized derivative was granted after the run. The private start receipt recorded the status at collection start; it does not negate later authorization. No relay identifiers are published.</p></section></main><footer><p>State of the Web · sanitized operational pilot derivative</p></footer></body></html>
"""


def public_manifest(output: Path) -> dict:
    files = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "public-manifest.json":
            continue
        relative = path.relative_to(output).as_posix()
        suffix = path.suffix.lower()
        media_type = {".css": "text/css", ".html": "text/html", ".json": "application/json", ".mp4": "video/mp4", ".png": "image/png"}.get(suffix)
        if media_type is None:
            raise SystemExit("unknown public evidence media type")
        files.append({"bytes": path.stat().st_size, "mediaType": media_type, "path": relative, "sha256": sha256_file(path)})
    return {"files": files, "schemaVersion": 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "journey-pilot")
    args = parser.parse_args()
    artifact, private = source_roots(args.source_root)
    output = args.output_root.resolve()
    data_dir = output / "data"
    media_manifest_path = data_dir / "media-manifest.json"
    if not media_manifest_path.is_file():
        raise SystemExit("run process_fixed10_media.py before generation")
    media_manifest = load_json(media_manifest_path)

    cohort = load_jsonl(private / "cohort.jsonl")
    events = load_jsonl(private / "cohort-events.jsonl")
    summary = load_json(private / "cohort-summary.json")
    start = load_json(artifact / "mcp-start-evidence.json")
    cleanup_raw = load_json(artifact / "cleanup-result.json")
    verify_ledger(events)
    if len(cohort) != 10 or len(events) != 10:
        raise SystemExit("fixed denominator drift")
    if [row.get("ordinal") for row in cohort] != list(range(1, 11)) or [event.get("ordinal") for event in events] != list(range(1, 11)):
        raise SystemExit("ordinal drift")
    counts = Counter(event.get("disposition") for event in events)
    if counts != Counter({"audit-partial": 6, "audit-completed": 2, "audit-error": 2}):
        raise SystemExit("ledger disposition drift")
    if summary.get("dispositions", {}).get("audit-completed") != 2 or summary.get("dispositions", {}).get("audit-partial") != 6 or summary.get("dispositions", {}).get("audit-error") != 2:
        raise SystemExit("cohort summary disposition drift")
    if start.get("commit") != "e644b7c7ce7c1e82cf797bc8e7affb9fea03ff37" or start.get("denominator") != 10 or start.get("noAutomaticRetry") is not True:
        raise SystemExit("start receipt provenance drift")

    policy_values = set()
    catalog_values = set()
    rows = []
    for cohort_row, event in zip(cohort, events, strict=True):
        ordinal = cohort_row["ordinal"]
        origin = normalize_origin(cohort_row.get("normalizedInputOrigin"))
        if len(urlsplit(cohort_row.get("normalizedInputOrigin")).path.strip("/")) or urlsplit(cohort_row.get("normalizedInputOrigin")).query or urlsplit(cohort_row.get("normalizedInputOrigin")).fragment:
            raise SystemExit("cohort origin is not origin-only")
        report = report_for_row(private, ordinal)
        if ordinal in {1, 2} and report is not None or ordinal >= 3 and report is None:
            raise SystemExit("report inventory drift")
        if report:
            catalog_values.add(report.get("coverage", {}).get("catalogChecksum"))
        permit_path = unique_glob(private, f"{ordinal:04d}-*/*/execution-permit.json")
        permit = load_json(permit_path)
        policy_values.add(permit.get("policyChecksum"))
        reason_code = event.get("reasonCode")
        rows.append({
            "aggregates": aggregate_facts(private, ordinal, origin),
            "archetype": cohort_row.get("archetype"),
            "coverage": coverage_from_report(report),
            "disposition": DISPOSITION_MAP.get(event.get("disposition")),
            "journey": journey_from_flow(private, ordinal, artifact),
            "methodInvalid": ordinal in {3, 6},
            "ordinal": ordinal,
            "origin": origin,
            "reasonCode": reason_code,
            "reasonSummary": REASON_SUMMARIES[ordinal],
        })
    if len(policy_values) != 1 or None in policy_values or len(catalog_values) != 1 or None in catalog_values:
        raise SystemExit("policy or catalog checksum drift")
    if len({row["origin"] for row in rows}) != 10:
        raise SystemExit("origin uniqueness drift")

    timestamps = [path.name for path in private.glob("00*/*") if path.is_dir() and path.name != "latest"]
    collection_window = {"endedAt": max(timestamps), "startedAt": min(timestamps)}
    source = {
        "catalogSha256": next(iter(catalog_values)).removeprefix("sha256:"),
        "cleanupAssertionSha256": sha256_file(artifact / "cleanup-result.json"),
        "cohortManifestSha256": sha256_file(private / "cohort.jsonl"),
        "cohortSummarySha256": sha256_file(private / "cohort-summary.json"),
        "generationBaseCommit": PUBLIC_BASE_COMMIT,
        "ledgerSha256": sha256_file(private / "cohort-events.jsonl"),
        "pilotManifestSha256": start["pilotManifestSha256"].removeprefix("sha256:"),
        "policySha256": next(iter(policy_values)).removeprefix("sha256:"),
        "runnerCommit": start["commit"],
        "sourceManifestSha256": start["sourceManifestSha256"].removeprefix("sha256:"),
        "startReceiptSha256": sha256_file(artifact / "mcp-start-evidence.json"),
    }
    dataset = {
        "cohort": {
            "denominator": 10,
            "dispositions": {"error": 2, "partial": 6, "runnerCompleted": 2},
            "noAutomaticRetry": True,
            "selectionType": "fixed-reviewed-convenience-pilot",
        },
        "collectionWindow": collection_window,
        "datasetId": DATASET_ID,
        "publication": {
            "authorization": "Explicitly authorized after the run for this sanitized derivative.",
            "claimsExcluded": ["scores", "pass rates", "rankings", "completed-quality claims"],
            "scope": "operational pilot; not a site ranking or replacement main report",
        },
        "rows": rows,
        "schemaVersion": 1,
        "source": source,
    }
    cleanup = {
        "additionalAuditProfilesCoveredByPerSessionReceipts": False,
        "batchExited": cleanup_raw.get("batchExited") is True,
        "dashboardExited": cleanup_raw.get("dashboardExited") is True,
        "mcpPageClosed": cleanup_raw.get("mcpPageClosed") is True,
        "ownedProfileResidueCount": len(cleanup_raw.get("ownedProfileResidue", [])),
        "ownedProfilesInCleanupList": len(cleanup_raw.get("ownedProfilesObserved", [])),
        "receiptType": "operational cleanup assertion; not authenticated per-session teardown receipts",
        "schemaVersion": 1,
        "temporaryDependencySymlinkRemoved": cleanup_raw.get("temporaryDependencySymlinkRemoved") is True,
        "wrapperRemoved": cleanup_raw.get("wrapperRemoved") is True,
    }
    provenance = {
        "authorizationNote": "Publication authorization for this sanitized derivative was granted after collection; the private start receipt does not negate later authorization.",
        "collectionWindow": collection_window,
        "publicPrivateBoundary": "Only allowlisted aggregates and deterministically sanitized, privacy-reviewed media are public. Raw reports, HAR, DOM, flow, logs, permits, profiles, bodies, and headers remain private.",
        "schemaVersion": 1,
        "source": source,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "pilot.json").write_bytes(canonical(dataset))
    (data_dir / "cleanup-summary.json").write_bytes(canonical(cleanup))
    (data_dir / "provenance.json").write_bytes(canonical(provenance))
    (output / "index.html").write_text(render_html(dataset, media_manifest), encoding="utf-8", newline="\n")
    (data_dir / "public-manifest.json").write_bytes(canonical(public_manifest(output)))
    print(f"generated {len(rows)} sanitized rows")
    print(f"pilot sha256 {sha256_file(data_dir / 'pilot.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
