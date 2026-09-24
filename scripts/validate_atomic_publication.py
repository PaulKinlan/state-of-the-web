#!/usr/bin/env python3
"""Fail-closed validation for the published 1,000-target atomic inventory."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

try:  # invoked as a script, with scripts/ on sys.path
    from reconcile_atomic_run import (
        canonical_origin,
        disposition_for,
        expected_catalog,
        pinned_catalog,
        sha256_file,
        validate_report,
    )
except ModuleNotFoundError:  # imported as scripts.validate_atomic_publication
    from scripts.reconcile_atomic_run import (
        canonical_origin,
        disposition_for,
        expected_catalog,
        pinned_catalog,
        sha256_file,
        validate_report,
    )

ROOT = Path(__file__).resolve().parents[1]


def path_beneath_root(root: Path, value: object) -> Path | None:
    """Resolve a recorded repository-relative path without allowing escape."""
    if not isinstance(value, str) or not value.strip():
        return None
    relative = Path(value)
    if relative.is_absolute():
        return None
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def validate(root: Path, check_db: bool = True, check_local_evidence: bool = False) -> dict:
    errors: list[str] = []
    inventory_path = root / "results/atomic/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    # Load the catalog the inventory pinned, verifying its digest and shape
    # before anything is judged against it.
    catalog_path, catalog, catalog_sha = pinned_catalog(root, inventory)
    principle_ids, pairs, _ = expected_catalog(catalog)
    if len(pairs) != 58:
        errors.append(f"catalog has {len(pairs)} checks")

    manifest_path = root / inventory["manifest"]["path"]
    if sha256_file(manifest_path) != inventory["manifest"]["sha256"]:
        errors.append("manifest SHA-256 mismatch")
    with manifest_path.open(newline="", encoding="utf-8") as source:
        manifest = list(csv.DictReader(source))
    targets = inventory.get("targets", [])
    if len(manifest) != 1000 or len(targets) != 1000:
        errors.append(f"denominator mismatch: manifest={len(manifest)} targets={len(targets)}")
    if [target.get("position") for target in targets] != list(range(1, 1001)):
        errors.append("target positions are not exactly 1..1000")
    if len({target.get("origin") for target in targets}) != 1000:
        errors.append("target origins are not unique")
    if len({target.get("report") for target in targets}) != 1000:
        errors.append("canonical report paths are not unique")
    if len({target.get("page") for target in targets}) != 1000:
        errors.append("static page paths are not unique")

    dispositions = Counter()
    statuses = Counter()
    for index, (manifest_row, target) in enumerate(zip(manifest, targets, strict=False), 1):
        prefix = f"position {index}"
        if int(manifest_row["position"]) != target.get("position") or canonical_origin(manifest_row["origin"]) != canonical_origin(target.get("origin", "")):
            errors.append(f"{prefix}: manifest target mismatch")
            continue
        report_path = root / target["report"]
        if not report_path.is_file():
            errors.append(f"{prefix}: missing canonical report")
            continue
        if sha256_file(report_path) != target.get("reportSha256"):
            errors.append(f"{prefix}: report SHA-256 mismatch")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        evidence_root = None
        if check_local_evidence:
            provenance = target.get("provenance") if isinstance(target.get("provenance"), dict) else {}
            source_report = path_beneath_root(root, provenance.get("sourceReport"))
            evidence_root = path_beneath_root(root, provenance.get("evidenceRoot"))
            if source_report is None:
                errors.append(f"{prefix}: invalid local source report path")
            elif not source_report.is_file():
                errors.append(f"{prefix}: missing local source report")
            elif sha256_file(source_report) != target.get("reportSha256"):
                errors.append(f"{prefix}: local source/canonical report mismatch")
            if evidence_root is None:
                errors.append(f"{prefix}: invalid local evidence root path")
            elif source_report is not None and evidence_root != source_report.parent:
                errors.append(f"{prefix}: evidence root is not the source report directory")
        result = validate_report(report, catalog, catalog_sha, evidence_root)
        if result["errors"]:
            errors.append(f"{prefix}: {'; '.join(result['errors'][:3])}")
            continue
        if canonical_origin(report.get("url", "")) != canonical_origin(target["origin"]):
            errors.append(f"{prefix}: report URL mismatch")
        if result["coverage"] != target.get("coverage"):
            errors.append(f"{prefix}: target/report coverage mismatch")
        derived = disposition_for(result["coverage"])
        if derived != target.get("disposition"):
            errors.append(f"{prefix}: disposition mismatch")
        if target["disposition"] != "complete" and ("overallScore" in report or "score" in report):
            errors.append(f"{prefix}: incomplete report has score")
        dispositions[target["disposition"]] += 1
        statuses.update(result["statusCounts"])
        if not (root / "sites" / target["page"]).is_file():
            errors.append(f"{prefix}: missing static page")

    expected_counts = {
        "complete": 705,
        "exhaustedBlocked": 257,
        "exhaustedPartial": 38,
        "retryEligible": 0,
        "queued": 0,
        "invalid": 0,
    }
    if inventory.get("counts") != expected_counts:
        errors.append(f"inventory counts differ: {inventory.get('counts')}")
    if dict(dispositions) != {key: expected_counts[key] for key in ("complete", "exhaustedBlocked", "exhaustedPartial")}:
        errors.append(f"derived disposition counts differ: {dict(dispositions)}")
    recorded = sum(statuses.values())
    judged = sum(statuses[key] for key in ("pass", "issues", "not-applicable", "opted-out"))
    expected_outcomes = {
        "recorded": recorded,
        "judged": judged,
        "pass": statuses["pass"],
        "issues": statuses["issues"],
        "notApplicable": statuses["not-applicable"],
        "optedOut": statuses["opted-out"],
        "blocked": statuses["blocked"],
        "notRun": statuses["not-run"],
    }
    if recorded != 58000 or inventory.get("checkOutcomes") != expected_outcomes:
        errors.append(f"check outcome totals differ: {expected_outcomes}")
    if inventory.get("publication", {}).get("scored") is not False:
        errors.append("publication must be explicitly unscored")
    if inventory.get("publication", {}).get("wholeInventoryInferenceAllowed") is not False:
        errors.append("whole-inventory inference must be false")

    principle_pages = list((root / "principles").glob("*.html"))
    if len(principle_pages) != 17:
        errors.append(f"expected 17 principle pages, found {len(principle_pages)}")
    site_pages = list((root / "sites").glob("*.html"))
    if len(site_pages) != 1000:
        errors.append(f"expected 1000 site pages, found {len(site_pages)}")
    for required in ("index.html", "checkpoint.html", "atomic-checkpoint.json", "README.md"):
        if not (root / required).is_file():
            errors.append(f"missing publication file {required}")

    db_summary = None
    db_path = root / "state-of-the-web.db"
    if check_db:
        if not db_path.is_file():
            errors.append("state-of-the-web.db has not been built")
        else:
            connection = sqlite3.connect(db_path)
            db_summary = {
                "sites": connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0],
                "positions": connection.execute("SELECT COUNT(DISTINCT manifest_position) FROM sites").fetchone()[0],
                "checks": connection.execute("SELECT COUNT(*) FROM test_results").fetchone()[0],
                "principles": connection.execute("SELECT COUNT(*) FROM principles").fetchone()[0],
                "scores": connection.execute("SELECT COUNT(*) FROM sites WHERE overall_score IS NOT NULL").fetchone()[0],
                "dispositions": dict(connection.execute("SELECT disposition,COUNT(*) FROM sites GROUP BY disposition")),
            }
            # Totals alone cannot tell a consistent database from a mixed one: a
            # build that took definitions from a drifted catalog and results
            # from the pinned generation still reports 58,000 rows. Compare the
            # actual (principle, check) IDENTITIES against the pinned catalog.
            definitions = set(connection.execute("SELECT principle_id,test_id FROM principle_tests"))
            result_pairs = set(connection.execute("SELECT DISTINCT principle_id,test_id FROM test_results"))
            # The schema's primary key already forbids duplicate (site, check)
            # rows, so an exact per-site row count plus a catalog-subset check
            # proves every site carries exactly the catalog's pairs.
            odd_sites = [
                row for row in connection.execute(
                    "SELECT site,COUNT(*) FROM test_results GROUP BY site HAVING COUNT(*) != ?",
                    (len(pairs),),
                )
            ]
            odd_principle_sites = [
                row for row in connection.execute(
                    "SELECT site,COUNT(*) FROM principles GROUP BY site HAVING COUNT(*) != ?",
                    (len(principle_ids),),
                )
            ]
            connection.close()
            if definitions != pairs:
                missing = sorted(pairs - definitions)[:5]
                extra = sorted(definitions - pairs)[:5]
                errors.append(f"database check definitions differ from the pinned catalog: missing={missing} extra={extra}")
            if result_pairs != pairs:
                missing = sorted(pairs - result_pairs)[:5]
                extra = sorted(result_pairs - pairs)[:5]
                errors.append(f"database results differ from the pinned catalog: missing={missing} extra={extra}")
            if odd_sites:
                errors.append(f"sites without exactly {len(pairs)} check rows: {odd_sites[:5]}")
            if odd_principle_sites:
                errors.append(f"sites without exactly {len(principle_ids)} principle rows: {odd_principle_sites[:5]}")
            if db_summary != {"sites": 1000, "positions": 1000, "checks": 58000, "principles": 17000, "scores": 0, "dispositions": {"complete": 705, "exhaustedBlocked": 257, "exhaustedPartial": 38}}:
                errors.append(f"database totals differ: {db_summary}")

    summary = {
        "valid": not errors,
        "errors": errors,
        "manifestOrigins": len(manifest),
        "publishedTargets": len(targets),
        "dispositions": dict(dispositions),
        "checkOutcomes": expected_outcomes,
        "sitePages": len(site_pages),
        "principlePages": len(principle_pages),
        "localEvidenceChecked": check_local_evidence,
        "database": db_summary,
    }
    print(json.dumps(summary, indent=2))
    if errors:
        raise SystemExit(1)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument(
        "--check-local-evidence",
        action="store_true",
        help="require retained source reports and every declared artifact beneath each local evidenceRoot",
    )
    args = parser.parse_args()
    validate(args.root.resolve(), not args.no_db, args.check_local_evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
