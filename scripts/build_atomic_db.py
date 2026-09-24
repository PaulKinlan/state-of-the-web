#!/usr/bin/env python3
"""Build the local SQLite database from the canonical atomic publication."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

try:  # invoked as a script, with scripts/ on sys.path
    from reconcile_atomic_run import expected_catalog, pinned_catalog
except ModuleNotFoundError:  # imported as scripts.build_atomic_db, e.g. by the test suite
    from scripts.reconcile_atomic_run import expected_catalog, pinned_catalog

ROOT = Path(__file__).resolve().parents[1]


def text_value(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def build(root: Path, output: Path) -> None:
    inventory = json.loads((root / "results/atomic/inventory.json").read_text(encoding="utf-8"))
    # Read the catalog the inventory PINNED, never whatever the working tree
    # holds. Reading `principles.json` unconditionally mixes generations: check
    # definitions from a newer catalog land beside results judged against the
    # older one, and every published total still matches.
    _, catalog, _ = pinned_catalog(root, inventory)
    principle_ids, catalog_pairs, _ = expected_catalog(catalog)
    targets = inventory["targets"]
    errors: list[str] = []

    # Build into a staging file and swap only after validation, so a rejected
    # build cannot leave the published database missing or half-written.
    staging = output.with_name(f"{output.name}.staging")
    if staging.exists():
        staging.unlink()
    connection = sqlite3.connect(staging)
    schema = (root / "schemas/schema.sql").read_text(encoding="utf-8")
    schema = "\n".join(line for line in schema.splitlines() if not line.strip().startswith("//"))
    connection.executescript(schema)

    for principle in catalog["principles"]:
        for check in principle.get("checks", []):
            connection.execute(
                "INSERT INTO principle_tests (principle_id,test_id,title,method) VALUES (?,?,?,?)",
                (principle["id"], check["id"], check["summary"], check.get("detectableVia")),
            )

    for target in targets:
        report = json.loads((root / target["report"]).read_text(encoding="utf-8"))
        # Identity, not arithmetic: this site must carry exactly the catalog's
        # pairs, once each. Counting rows alone cannot tell a complete site from
        # one missing a check while carrying an unknown extra.
        recorded_pairs = [(check["principleId"], check["checkId"]) for check in report["checkOutcomes"]]
        duplicates = sorted({pair for pair, count in Counter(recorded_pairs).items() if count > 1})
        missing = sorted(catalog_pairs - set(recorded_pairs))
        unknown = sorted(set(recorded_pairs) - catalog_pairs)
        if duplicates or missing or unknown:
            errors.append(
                f"{target['origin']}: duplicates={duplicates[:3]} missing={missing[:3]} unknown={unknown[:3]}"
            )
        recorded_principles = [outcome["principleId"] for outcome in report["principleOutcomes"]]
        if sorted(recorded_principles) != sorted(principle_ids):
            errors.append(f"{target['origin']}: principle outcomes do not match the pinned catalog")
        connection.execute(
            """INSERT INTO sites
               (site,source,manifest_position,crux_rank_bucket,disposition,attempts,
                audited_at,url,verdict,report_path,report_sha256,coverage_expected,
                coverage_recorded,coverage_judged,coverage_blocked,coverage_not_run,
                coverage_complete,status_detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                target["origin"], "atomic", target["position"], target["cruxRankBucket"],
                target["disposition"], target["attempts"], target.get("auditedAt"),
                target["origin"], None, target["report"], target["reportSha256"],
                target["coverage"]["expected"], target["coverage"]["recorded"],
                target["coverage"]["judged"], target["coverage"]["blocked"],
                target["coverage"]["notRun"], int(target["coverage"]["complete"]),
                text_value(target.get("statusDetail")),
            ),
        )
        findings = {finding.get("id"): finding for finding in report.get("findings", [])}
        checks_by_principle: dict[str, list[dict]] = {}
        for check in report["checkOutcomes"]:
            checks_by_principle.setdefault(check["principleId"], []).append(check)
            connection.execute(
                """INSERT INTO test_results
                   (site,principle_id,test_id,status,confidence,summary,evidence)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    target["origin"], check["principleId"], check["checkId"],
                    check["status"], check.get("confidence"), text_value(check.get("reason") or check.get("evidence")),
                    text_value(check.get("evidence")),
                ),
            )
        for outcome in report["principleOutcomes"]:
            # A malformed report must produce the collected diagnostics, not a
            # KeyError: if a principle recorded no checks the build is already
            # being rejected, and crashing here would hide why.
            rows = checks_by_principle.get(outcome["principleId"], [])
            confidence = "high" if all(row.get("confidence") == "high" for row in rows) else (
                "low" if any(row.get("confidence") == "low" for row in rows) else "medium"
            )
            connection.execute(
                """INSERT INTO principles
                   (site,principle_id,status,confidence,summary,finding_count)
                   VALUES (?,?,?,?,?,?)""",
                (
                    target["origin"], outcome["principleId"], outcome["status"], confidence,
                    text_value(outcome.get("reason")), len(outcome.get("findingIds") or []),
                ),
            )
        for finding_id, finding in findings.items():
            connection.execute(
                """INSERT INTO findings
                   (site,principle_id,finding_id,severity,summary,evidence)
                   VALUES (?,?,?,?,?,?)""",
                (
                    target["origin"], finding.get("principleId"), finding_id,
                    finding.get("severity"), text_value(finding.get("summary") or finding.get("title")),
                    text_value(finding.get("evidence")),
                ),
            )

    connection.commit()
    checks = {
        "sites": connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0],
        "unique_positions": connection.execute("SELECT COUNT(DISTINCT manifest_position) FROM sites").fetchone()[0],
        "test_results": connection.execute("SELECT COUNT(*) FROM test_results").fetchone()[0],
        "principles": connection.execute("SELECT COUNT(*) FROM principles").fetchone()[0],
        "scored_sites": connection.execute("SELECT COUNT(*) FROM sites WHERE overall_score IS NOT NULL").fetchone()[0],
    }
    dispositions = dict(connection.execute("SELECT disposition,COUNT(*) FROM sites GROUP BY disposition"))
    # Every definition must have results, and every result a definition.
    orphan_definitions = list(
        connection.execute(
            "SELECT principle_id,test_id FROM principle_tests EXCEPT SELECT principle_id,test_id FROM test_results"
        )
    )
    orphan_results = list(
        connection.execute(
            "SELECT DISTINCT principle_id,test_id FROM test_results EXCEPT SELECT principle_id,test_id FROM principle_tests"
        )
    )
    connection.close()

    # Totals are DERIVED from the selected generation, not hardcoded. For the
    # July publication these derive to exactly 1000 / 58,000 / 17,000 and
    # 705 / 257 / 38, so the historical contract is preserved rather than
    # restated -- and a future generation with a different catalog is checked
    # against its own shape instead of silently failing this gate.
    expected = {
        "sites": len(targets),
        "unique_positions": len(targets),
        "test_results": len(targets) * len(catalog_pairs),
        "principles": len(targets) * len(principle_ids),
        "scored_sites": 0,
    }
    expected_dispositions = dict(Counter(target["disposition"] for target in targets))
    recorded_counts = inventory.get("counts") or {}
    for disposition, count in expected_dispositions.items():
        if disposition in recorded_counts and recorded_counts[disposition] != count:
            errors.append(
                f"inventory counts {disposition}={recorded_counts[disposition]} "
                f"but {count} targets carry it"
            )
    if checks != expected:
        errors.append(f"totals differ: {checks} != {expected}")
    if dispositions != expected_dispositions:
        errors.append(f"dispositions differ: {dispositions} != {expected_dispositions}")
    if orphan_definitions:
        errors.append(f"catalog checks with no results: {orphan_definitions[:5]}")
    if orphan_results:
        errors.append(f"results with no catalog check: {orphan_results[:5]}")

    if errors:
        staging.unlink(missing_ok=True)
        raise SystemExit("database validation failed:\n  " + "\n  ".join(errors[:20]))

    os.replace(staging, output)
    print(json.dumps({"database": str(output), **checks, "dispositions": dispositions}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve() if args.output else root / "state-of-the-web.db"
    build(root, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
