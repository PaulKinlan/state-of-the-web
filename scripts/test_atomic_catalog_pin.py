#!/usr/bin/env python3
"""Regression suite: the database must be built from the catalog the inventory pinned.

`build_atomic_db.py` used to read the working `principles.json` regardless of
what `inventory.catalog.path` recorded. When the working catalog drifted, the
builder emitted check DEFINITIONS from the new catalog beside RESULTS judged
against the old one, exited 0, and the publication gate agreed -- because both
only counted rows, and every total still matched.

These tests use a miniature publication rather than the 1,000-target one so they
stay fast and so the assertions are about the LOGIC, not about the July numbers.
The July contract is asserted separately by `validate_atomic_publication.py`.

  python3 -m unittest scripts/test_atomic_catalog_pin.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_atomic_db import build
from scripts.reconcile_atomic_run import pinned_catalog

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "schemas/schema.sql").read_text(encoding="utf-8")

CATALOG = {
    "guidanceCatalogVersion": "test-catalog@1",
    "principles": [
        {
            "id": "first-principle",
            "title": "First principle",
            "applicability": {"expectation": "default", "criteria": "Always."},
            "checks": [
                {"id": "check-one", "summary": "One", "detectableVia": "HINT"},
                {"id": "check-two", "summary": "Two", "detectableVia": "HINT"},
            ],
        },
        {
            "id": "second-principle",
            "title": "Second principle",
            "applicability": {"expectation": "default", "criteria": "Always."},
            "checks": [{"id": "check-three", "summary": "Three", "detectableVia": "HINT"}],
        },
    ],
}


def catalog_bytes(catalog: dict) -> bytes:
    return (json.dumps(catalog, indent=2) + "\n").encode("utf-8")


def build_publication(root: Path, catalog: dict, *, catalog_path: str, targets: int = 2) -> dict:
    """Write a miniature but structurally faithful publication."""
    (root / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "schemas/schema.sql").write_text(SCHEMA, encoding="utf-8")
    (root / "results/atomic/reports").mkdir(parents=True, exist_ok=True)

    payload = catalog_bytes(catalog)
    pinned = root / catalog_path
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    pairs = [
        (principle["id"], check["id"])
        for principle in catalog["principles"]
        for check in principle["checks"]
    ]
    entries = []
    for position in range(1, targets + 1):
        origin = f"https://example{position}.test"
        report = {
            "site": origin,
            "url": origin,
            "status": "completed",
            "checkOutcomes": [
                {
                    "principleId": principle_id,
                    "checkId": check_id,
                    "status": "pass",
                    "confidence": "high",
                    "method": "Synthetic fixture.",
                    "evidence": "Synthetic fixture.",
                }
                for principle_id, check_id in pairs
            ],
            "principleOutcomes": [
                {"principleId": principle["id"], "expectation": "default", "status": "pass", "findingIds": []}
                for principle in catalog["principles"]
            ],
            "findings": [],
        }
        report_bytes = (json.dumps(report, indent=2) + "\n").encode("utf-8")
        relative = f"results/atomic/reports/{position:04d}-example.json"
        (root / relative).write_bytes(report_bytes)
        entries.append(
            {
                "position": position,
                "origin": origin,
                "cruxRankBucket": 1000,
                "disposition": "complete",
                "attempts": 1,
                "auditedAt": "2026-01-01T00:00:00Z",
                "report": relative,
                "reportSha256": hashlib.sha256(report_bytes).hexdigest(),
                "coverage": {
                    "expected": len(pairs),
                    "recorded": len(pairs),
                    "judged": len(pairs),
                    "blocked": 0,
                    "notRun": 0,
                    "complete": True,
                },
                "statusDetail": None,
            }
        )

    inventory = {
        "catalog": {
            "path": catalog_path,
            "version": catalog.get("guidanceCatalogVersion"),
            "sha256": digest,
            "principles": len(catalog["principles"]),
            "checksPerTarget": len(pairs),
        },
        "counts": {"complete": targets},
        "targets": entries,
    }
    (root / "results/atomic/inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return inventory


def drifted(catalog: dict) -> dict:
    """The same catalog plus one extra check -- a newer generation."""
    evolved = copy.deepcopy(catalog)
    evolved["principles"][0]["checks"].append(
        {"id": "added-in-a-later-generation", "summary": "Added", "detectableVia": "HINT"}
    )
    return evolved


class PinnedCatalogTest(unittest.TestCase):
    def test_pin_is_read_from_the_recorded_path_not_the_working_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            # The working catalog drifts; the pin must be unaffected.
            (root / "principles.json").write_bytes(catalog_bytes(drifted(CATALOG)))
            path, catalog, digest = pinned_catalog(root, inventory)
            self.assertEqual(path, root / "results/atomic/catalog.json")
            self.assertEqual(digest, inventory["catalog"]["sha256"])
            self.assertEqual(len(catalog["principles"][0]["checks"]), 2, "read the drifted working catalog")

    def test_tampered_pinned_catalog_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            (root / "results/atomic/catalog.json").write_bytes(catalog_bytes(drifted(CATALOG)))
            with self.assertRaises(SystemExit) as raised:
                pinned_catalog(root, inventory)
            self.assertIn("SHA-256", str(raised.exception))

    def test_recorded_shape_must_match_the_pinned_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            # Digest still matches; the recorded shape does not.
            inventory["catalog"]["checksPerTarget"] = 99
            with self.assertRaises(SystemExit) as raised:
                pinned_catalog(root, inventory)
            self.assertIn("99", str(raised.exception))

    def test_catalog_path_cannot_escape_the_publication_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            for hostile in ("../outside.json", "/etc/passwd", "", None):
                with self.subTest(path=hostile):
                    escaped = copy.deepcopy(inventory)
                    escaped["catalog"]["path"] = hostile
                    with self.assertRaises(SystemExit):
                        pinned_catalog(root, escaped)


class DatabaseBuildTest(unittest.TestCase):
    def test_database_is_built_from_the_pin_when_the_working_catalog_has_drifted(self):
        """The core regression: definitions and results must be one generation."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            (root / "principles.json").write_bytes(catalog_bytes(drifted(CATALOG)))
            output = root / "state-of-the-web.db"
            build(root, output)

            connection = sqlite3.connect(output)
            definitions = set(connection.execute("SELECT principle_id,test_id FROM principle_tests"))
            results = set(connection.execute("SELECT DISTINCT principle_id,test_id FROM test_results"))
            connection.close()
            self.assertEqual(definitions, results, "definitions and results came from different generations")
            self.assertEqual(len(definitions), 3, "the drifted working catalog was used")
            self.assertNotIn(("first-principle", "added-in-a-later-generation"), definitions)

    def test_totals_are_derived_from_the_selected_generation(self):
        """A publication with a different shape is judged against its own shape."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json", targets=3)
            output = root / "state-of-the-web.db"
            build(root, output)
            connection = sqlite3.connect(output)
            rows = connection.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
            principles = connection.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
            connection.close()
            self.assertEqual(rows, 3 * 3, "3 targets x 3 checks")
            self.assertEqual(principles, 3 * 2, "3 targets x 2 principles")

    def test_report_missing_a_catalog_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            target = inventory["targets"][0]
            report_path = root / target["report"]
            report = json.loads(report_path.read_text())
            report["checkOutcomes"] = report["checkOutcomes"][:-1]
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                build(root, root / "state-of-the-web.db")
            self.assertIn("missing", str(raised.exception))

    def test_report_carrying_an_unknown_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            target = inventory["targets"][0]
            report_path = root / target["report"]
            report = json.loads(report_path.read_text())
            report["checkOutcomes"].append(
                {
                    "principleId": "first-principle",
                    "checkId": "not-in-the-pinned-catalog",
                    "status": "pass",
                    "confidence": "high",
                    "method": "Synthetic.",
                    "evidence": "Synthetic.",
                }
            )
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                build(root, root / "state-of-the-web.db")
            self.assertIn("unknown", str(raised.exception))

    def test_a_rejected_build_leaves_the_published_database_intact(self):
        """Staging: a failed build must not destroy the database it replaces."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_publication(root, CATALOG, catalog_path="results/atomic/catalog.json")
            output = root / "state-of-the-web.db"
            build(root, output)
            published = output.read_bytes()

            # Make the next build fail validation.
            inventory = json.loads((root / "results/atomic/inventory.json").read_text())
            inventory["counts"]["complete"] = 99
            (root / "results/atomic/inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

            with self.assertRaises(SystemExit):
                build(root, output)
            self.assertTrue(output.is_file(), "the published database was destroyed by a failed build")
            self.assertEqual(output.read_bytes(), published, "the published database was modified")
            self.assertFalse(output.with_name(f"{output.name}.staging").exists(), "staging file was left behind")


if __name__ == "__main__":
    unittest.main(verbosity=2)
