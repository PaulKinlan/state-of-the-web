#!/usr/bin/env python3
"""Build the local SQLite database from the canonical atomic publication."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text_value(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def build(root: Path, output: Path) -> None:
    inventory = json.loads((root / "results/atomic/inventory.json").read_text(encoding="utf-8"))
    catalog = json.loads((root / "principles.json").read_text(encoding="utf-8"))
    if output.exists():
        output.unlink()
    connection = sqlite3.connect(output)
    schema = (root / "schemas/schema.sql").read_text(encoding="utf-8")
    schema = "\n".join(line for line in schema.splitlines() if not line.strip().startswith("//"))
    connection.executescript(schema)

    for principle in catalog["principles"]:
        for check in principle.get("checks", []):
            connection.execute(
                "INSERT INTO principle_tests (principle_id,test_id,title,method) VALUES (?,?,?,?)",
                (principle["id"], check["id"], check["summary"], check.get("detectableVia")),
            )

    for target in inventory["targets"]:
        report = json.loads((root / target["report"]).read_text(encoding="utf-8"))
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
            rows = checks_by_principle[outcome["principleId"]]
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
    connection.close()
    expected = {"sites": 1000, "unique_positions": 1000, "test_results": 58000, "principles": 17000, "scored_sites": 0}
    if checks != expected or dispositions != {"complete": 705, "exhaustedBlocked": 257, "exhaustedPartial": 38}:
        raise SystemExit(f"database validation failed: {checks}, {dispositions}")
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
