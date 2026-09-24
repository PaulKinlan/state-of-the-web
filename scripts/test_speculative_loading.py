#!/usr/bin/env python3
"""Tests for the speculative-loading check definition, probe, HAR parser, and CLI runner."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import speculative_loading

FIXTURES = ROOT / "scripts" / "fixtures"


class SpeculativeLoadingDefinitionTest(unittest.TestCase):
    def test_check_definition_conforms_to_catalog_contract(self):
        c = speculative_loading.CHECK_DEFINITION
        self.assertEqual(c["principleId"], "be-fast-and-stable")
        self.assertEqual(c["checkId"], "speculative-loading")
        self.assertTrue(c["summary"])
        self.assertIn("speculationrules", c["detectableVia"])
        # F1: Check-level applicability is unrepresentable in catalog schema
        self.assertNotIn("applicability", c, "catalog check schema does not permit check-level applicability")
        # F2: Only valid guide slugs from 0.0.172 / 0.0.190 permitted
        self.assertEqual(c["guides"], ["improve-next-page-load-performance"])
        self.assertNotIn("speculative-loading-speculation-rules", c["guides"])
        self.assertNotIn("prerender-pages-chrome", c["guides"])


class SpeculativeLoadingProbeTest(unittest.TestCase):
    def test_probe_js_exists_and_is_valid_javascript(self):
        self.assertTrue(speculative_loading.PROBE_JS.exists())
        proc = subprocess.run(["node", "--check", str(speculative_loading.PROBE_JS)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_probe_detects_speculation_rules_on_fixture(self):
        url = (FIXTURES / "speculative-loading.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        self.assertTrue(report.get("ok"), report)
        self.assertTrue(report["supported"]["speculationRules"])

        rules = report["speculationRules"]
        self.assertEqual(rules["scriptsFound"], 1)
        self.assertEqual(rules["validScripts"], 1)
        self.assertEqual(rules["invalidScripts"], 0)
        self.assertEqual(len(rules["errors"]), 0)

        # Prefetch rules
        self.assertEqual(rules["prefetch"]["count"], 2)
        self.assertEqual(rules["prefetch"]["listRules"], 1)
        self.assertEqual(rules["prefetch"]["documentRules"], 1)
        self.assertIn("/article/intro", rules["prefetch"]["sampleUrls"])
        self.assertIn("eager", rules["prefetch"]["eagerness"])
        self.assertIn("moderate", rules["prefetch"]["eagerness"])

        # Prerender rules
        self.assertEqual(rules["prerender"]["count"], 1)
        self.assertEqual(rules["prerender"]["listRules"], 1)
        self.assertIn("/checkout/step-1", rules["prerender"]["sampleUrls"])
        self.assertIn("immediate", rules["prerender"]["eagerness"])

        # Signals
        signals = report["signals"]
        self.assertTrue(signals["hasSpeculationRules"])
        self.assertTrue(signals["hasPrefetch"])
        self.assertTrue(signals["hasPrerender"])
        self.assertTrue(signals["hasDocumentRules"])
        self.assertTrue(signals["hasListRules"])
        self.assertTrue(signals["hasLegacySpeculation"])
        self.assertTrue(signals["eagernessConfigured"])

        # Legacy hints
        legacy = report["legacySpeculation"]
        self.assertEqual(legacy["linkPrefetch"], 1)
        self.assertEqual(legacy["linkModulepreload"], 1)

    def test_probe_detects_malformed_speculation_rules(self):
        url = (FIXTURES / "invalid-speculation-rules.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        self.assertTrue(report.get("ok"), report)

        rules = report["speculationRules"]
        self.assertEqual(rules["scriptsFound"], 1)
        self.assertEqual(rules["validScripts"], 0)
        self.assertEqual(rules["invalidScripts"], 1)
        self.assertTrue(len(rules["errors"]) > 0)
        self.assertIn("JSON parse error", rules["errors"][0]["error"])
        self.assertFalse(report["signals"]["hasSpeculationRules"])

    def test_probe_reports_no_speculation_on_plain_page(self):
        url = (FIXTURES / "plain-page.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        self.assertTrue(report.get("ok"), report)
        self.assertEqual(report["speculationRules"]["scriptsFound"], 0)
        self.assertFalse(report["signals"]["hasSpeculationRules"])
        self.assertFalse(report["signals"]["hasPrefetch"])
        self.assertFalse(report["signals"]["hasPrerender"])

    def test_probe_fails_closed_on_nonexistent_url(self):
        url = (FIXTURES / "does-not-exist-xyz.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        self.assertFalse(report.get("ok"))
        self.assertIn("navigation failed", report.get("error", ""))


class HarSpeculationSignalsTest(unittest.TestCase):
    def test_parse_har_extracts_sec_purpose_and_speculation_rules_header(self):
        fake_har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "url": "https://example.com/api/data",
                            "method": "GET",
                            "headers": [{"name": "Accept", "value": "application/json"}],
                        },
                        "response": {
                            "status": 200,
                            "headers": [
                                {"name": "Content-Type", "value": "application/json"},
                                {"name": "Speculation-Rules", "value": "/speculationrules.json"},
                            ],
                        },
                    },
                    {
                        "request": {
                            "url": "https://example.com/next-page",
                            "method": "GET",
                            "headers": [
                                {"name": "Sec-Purpose", "value": "prefetch"},
                                {"name": "Sec-Speculation-Tags", "value": "eager-next"},
                            ],
                        },
                        "response": {
                            "status": 200,
                            "headers": [
                                {"name": "Content-Type", "value": "text/html"},
                                {"name": "Supports-Loading-Mode", "value": "credentialed-prerender"},
                            ],
                        },
                    },
                    {
                        "request": {
                            "url": "https://example.com/prerendered-page",
                            "method": "GET",
                            "headers": [
                                {"name": "Sec-Purpose", "value": "prerender"},
                            ],
                        },
                        "response": {
                            "status": 200,
                            "headers": [{"name": "Content-Type", "value": "text/html"}],
                        },
                    },
                ]
            }
        }
        res = speculative_loading.parse_har_speculation_signals(fake_har)
        self.assertEqual(res["speculativeRequestsCount"], 2)
        self.assertEqual(res["headerRulesetsCount"], 1)
        self.assertEqual(res["prerenderEligibleResponsesCount"], 1)
        self.assertTrue(res["hasSpeculativeNetworkActivity"])

        reqs = res["speculativeRequests"]
        self.assertEqual(reqs[0]["type"], "prefetch")
        self.assertEqual(reqs[0]["tags"], "eager-next")
        self.assertEqual(reqs[1]["type"], "prerender")


class OutcomeSynthesisTest(unittest.TestCase):
    def test_synthesis_pass_on_valid_rules(self):
        url = (FIXTURES / "speculative-loading.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        outcome = speculative_loading.synthesize_check_outcome(report)
        self.assertEqual(outcome["status"], "pass")
        self.assertIn("prefetch", outcome["evidence"])
        self.assertIn("prerender", outcome["evidence"])

    def test_synthesis_issues_on_malformed_rules(self):
        url = (FIXTURES / "invalid-speculation-rules.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        outcome = speculative_loading.synthesize_check_outcome(report)
        self.assertEqual(outcome["status"], "issues")
        self.assertIn("Malformed speculation rules", outcome["evidence"])

    def test_synthesis_not_applicable_on_zero_links_page(self):
        url = (FIXTURES / "plain-page.html").as_uri()
        report = speculative_loading.run_speculative_probe(url)
        outcome = speculative_loading.synthesize_check_outcome(report)
        self.assertEqual(outcome["status"], "not-applicable")
        self.assertIn("zero navigation links", outcome["evidence"])

    def test_synthesis_not_applicable_on_external_only_links(self):
        """F3: Page with 0 internal links and 8 external links must be not-applicable."""
        mock_report = {
            "ok": True,
            "signals": {"hasSpeculationRules": False},
            "speculationRules": {"errors": []},
            "navigationContext": {
                "anchorCount": 8,
                "internalLinkCount": 0,
                "externalLinkCount": 8,
                "isClientSideRouted": False,
            },
        }
        outcome = speculative_loading.synthesize_check_outcome(mock_report)
        self.assertEqual(outcome["status"], "not-applicable")
        self.assertIn("external links only", outcome["reason"])

    def test_synthesis_not_applicable_on_spa_client_side_routing(self):
        """F3: SPA with client-side routing must be not-applicable, not issues."""
        mock_report = {
            "ok": True,
            "signals": {"hasSpeculationRules": False},
            "speculationRules": {"errors": []},
            "navigationContext": {
                "anchorCount": 20,
                "internalLinkCount": 20,
                "externalLinkCount": 0,
                "isClientSideRouted": True,
                "frameworkRouter": "next",
            },
        }
        outcome = speculative_loading.synthesize_check_outcome(mock_report)
        self.assertEqual(outcome["status"], "not-applicable")
        self.assertIn("Single-page application", outcome["evidence"])
        self.assertIn("next", outcome["evidence"])

    def test_synthesis_issues_on_multipage_site_without_speculation(self):
        """Multi-page site with internal navigation links without speculation is issues."""
        mock_report = {
            "ok": True,
            "signals": {"hasSpeculationRules": False},
            "speculationRules": {"errors": []},
            "navigationContext": {
                "anchorCount": 15,
                "internalLinkCount": 12,
                "externalLinkCount": 3,
                "isClientSideRouted": False,
            },
            "legacySpeculation": {"linkPrefetch": 2},
        }
        outcome = speculative_loading.synthesize_check_outcome(mock_report)
        self.assertEqual(outcome["status"], "issues")
        self.assertIn("12 internal navigation links", outcome["evidence"])
        self.assertIn("legacy hints", outcome["evidence"])


class LeakSafetyAndPersistenceTest(unittest.TestCase):
    def test_kill_profile_terminates_profile_process_and_removes_dir(self):
        """F5: Chrome profile cleanup on timeout."""
        profile_dir = Path(tempfile.mkdtemp(prefix="web-uplift-cdp-TimeoutTest-"))
        victim = subprocess.Popen(["bash", "-c", "sleep 300; exit 0", f"--user-data-dir={profile_dir}"])
        try:
            killed = speculative_loading.kill_profile(f"[browser] launching chrome (profile {profile_dir})")
            deadline = time.time() + 5
            while victim.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            survived = victim.poll() is None
        finally:
            if victim.poll() is None:
                victim.kill()
                victim.wait(timeout=5)
            shutil.rmtree(profile_dir, ignore_errors=True)
        self.assertEqual(killed, [str(profile_dir)])
        self.assertFalse(survived, "timed-out Chrome profile process was not killed")

    def test_probe_single_target_persists_evidence(self):
        """F6: Runner persists evidence immediately."""
        url = (FIXTURES / "speculative-loading.html").as_uri()
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "out.json"
            res = speculative_loading.probe_single_target(url, out_file)
            self.assertTrue(out_file.exists())
            persisted = json.loads(out_file.read_text())
            self.assertEqual(persisted["url"], url)
            self.assertEqual(persisted["outcome"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
