#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from scripts.reconcile_atomic_run import (
    canonical_origin,
    derive_principle_status,
    disposition_for,
    validate_artifact_files,
)


class ReconciliationSemanticsTest(unittest.TestCase):
    def test_root_trailing_slash_does_not_change_origin_identity(self):
        self.assertEqual(canonical_origin("https://Example.com/"), canonical_origin("https://example.com"))

    def test_blocked_check_keeps_principle_incomplete(self):
        rows = [{"status": "pass"}, {"status": "blocked"}]
        self.assertEqual(derive_principle_status(rows), "incomplete")

    def test_issues_remain_issues_even_when_other_checks_are_blocked(self):
        rows = [{"status": "issues"}, {"status": "blocked"}]
        self.assertEqual(derive_principle_status(rows), "issues")

    def test_partial_disposition_is_not_complete(self):
        coverage = {"complete": False, "expected": 58, "judged": 19, "blocked": 39}
        self.assertEqual(disposition_for(coverage), "exhaustedPartial")

    def test_fully_blocked_disposition_remains_explicit(self):
        coverage = {"complete": False, "expected": 58, "judged": 0, "blocked": 58}
        self.assertEqual(disposition_for(coverage), "exhaustedBlocked")

    def test_declared_artifact_must_exist_at_exact_extensionless_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evidence").mkdir()
            (root / "evidence/discoverability").write_text("{}", encoding="utf-8")
            wrong = {"artifacts": [{"path": "evidence/discoverability.json"}]}
            exact = {"artifacts": [{"path": "evidence/discoverability"}]}
            self.assertIn("declared path is missing", validate_artifact_files(wrong, root)[0])
            self.assertEqual(validate_artifact_files(exact, root), [])

    def test_top_level_artifact_is_valid_only_at_declared_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "coverage-manifest.json").write_text("{}", encoding="utf-8")
            wrong = {"artifacts": [{"path": "evidence/coverage-manifest.json"}]}
            exact = {"artifacts": [{"path": "coverage-manifest.json"}]}
            self.assertIn("declared path is missing", validate_artifact_files(wrong, root)[0])
            self.assertEqual(validate_artifact_files(exact, root), [])

    def test_artifact_path_cannot_escape_evidence_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {"artifacts": [{"path": "../outside.json"}]}
            self.assertIn("escapes evidence root", validate_artifact_files(report, root)[0])


if __name__ == "__main__":
    unittest.main()
