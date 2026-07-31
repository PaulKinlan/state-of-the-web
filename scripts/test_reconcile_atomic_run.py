#!/usr/bin/env python3
import unittest

from scripts.reconcile_atomic_run import canonical_origin, derive_principle_status, disposition_for


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


if __name__ == "__main__":
    unittest.main()
