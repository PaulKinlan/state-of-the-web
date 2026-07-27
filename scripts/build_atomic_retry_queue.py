#!/usr/bin/env python3
"""Build a bounded retry queue for the fixed top-1,000 atomic audit.

A site is retried only while it has fewer than --max-attempts report-bearing runs.
Incomplete sites that exhaust the budget remain explicit blocked/partial outcomes;
they are never converted into passes.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def host_slug(url: str) -> str:
    host = urlparse(url).netloc
    return host.replace(":", "_").replace(".", "_") or "page"


def coverage_state(report: dict | None) -> tuple[str, dict]:
    if report is None:
        return "missing", {}
    coverage = report.get("coverage")
    if not isinstance(coverage, dict):
        return "invalid", {}
    if coverage.get("complete") is True:
        return "complete", coverage
    expected = coverage.get("expected")
    blocked = coverage.get("blocked")
    judged = coverage.get("judged")
    if isinstance(expected, int) and expected > 0 and blocked == expected and judged == 0:
        return "blocked", coverage
    return "partial", coverage


def load_report(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    if args.max_attempts < 1:
        parser.error("--max-attempts must be >= 1")

    run_dir = args.run_dir.resolve()
    urls_path = run_dir / "atomic-urls.txt"
    reports_root = run_dir / "atomic-reports"
    queue_path = args.queue or run_dir / "atomic-retry-urls.txt"
    status_path = args.status or run_dir / "atomic-retry-status.json"

    urls = [
        line.strip()
        for line in urls_path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(urls) != 1000 or len(set(urls)) != 1000:
        raise SystemExit(
            f"fixed denominator violation: expected 1000 unique URLs, got {len(urls)} lines / {len(set(urls))} unique"
        )

    queue: list[str] = []
    records: list[dict] = []
    counts = {
        "complete": 0,
        "retryEligible": 0,
        "exhaustedBlocked": 0,
        "exhaustedPartial": 0,
        "exhaustedInvalid": 0,
    }

    for url in urls:
        slug = host_slug(url)
        site_root = reports_root / slug
        run_dirs = sorted(
            path for path in site_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ) if site_root.is_dir() else []
        report_paths = [path / "report.json" for path in run_dirs if (path / "report.json").is_file()]
        attempts = len(report_paths)
        latest_path = report_paths[-1] if report_paths else None
        latest = load_report(latest_path) if latest_path else None
        state, coverage = coverage_state(latest)

        if state == "complete":
            disposition = "complete"
            counts["complete"] += 1
        elif attempts < args.max_attempts:
            disposition = "retry-eligible"
            counts["retryEligible"] += 1
            queue.append(url)
        elif state == "blocked":
            disposition = "blocked-after-retries"
            counts["exhaustedBlocked"] += 1
        elif state == "invalid" or state == "missing":
            disposition = "invalid-after-retries"
            counts["exhaustedInvalid"] += 1
        else:
            disposition = "partial-after-retries"
            counts["exhaustedPartial"] += 1

        records.append({
            "url": url,
            "slug": slug,
            "attempts": attempts,
            "maxAttempts": args.max_attempts,
            "disposition": disposition,
            "latestReport": str(latest_path) if latest_path else None,
            "coverage": coverage,
            "status": latest.get("status") if latest else None,
            "statusDetail": latest.get("statusDetail") if latest else None,
        })

    if sum(counts.values()) != 1000:
        raise SystemExit(f"status denominator mismatch: {counts}")

    queue_path.write_text("".join(f"{url}\n" for url in queue))
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "denominator": 1000,
        "maxAttempts": args.max_attempts,
        "counts": counts,
        "queueCount": len(queue),
        "records": records,
    }
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"queue": len(queue), **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
