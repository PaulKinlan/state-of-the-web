#!/usr/bin/env python3
"""Crawler suite: a Mode 1 pass must actually collect modern-web evidence.

`scripts/audit_runner2.py` is the documented Mode 1 CDP evidence pass. Bead
state-of-the-web-if6 recorded that it collected no Chrome 134+ feature evidence,
so a sweep could complete with those checks unmeasured. These tests call the real
runner functions against real pages in headless Chrome over file:// URLs, so no
network is required:

  python3 -m unittest scripts.test_audit_runner_modern_web -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'scripts' / 'fixtures' / 'modern-web-features.html'
CHROME_CANDIDATES = ['/usr/bin/google-chrome-stable', '/usr/bin/google-chrome', '/usr/bin/chromium']

sys.path.insert(0, str(ROOT / 'scripts'))
import audit_runner2  # noqa: E402  (import must be side-effect free: main is guarded)

FAMILIES = ('viewTransitions', 'scrollDrivenAnimations', 'anchorPositioning',
            'scrollStateChrome', 'gesturePlatforms')


@contextlib.contextmanager
def temp_cwd():
    """Keep the runner's relative `evidence/` writes out of the repository."""
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            yield Path(tmp)
        finally:
            os.chdir(cwd)


class ModernWebCollectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not any(Path(candidate).exists() for candidate in CHROME_CANDIDATES):
            raise unittest.SkipTest('no Chrome binary available')

    def test_probe_path_resolves_to_the_committed_expression(self):
        """A wrong probe path would silently collect nothing."""
        probe = Path(audit_runner2.MODERN_WEB_PROBE)
        self.assertTrue(probe.exists(), f'probe expression missing at {probe}')
        self.assertEqual(probe.name, 'modern-web-features.js')
        self.assertEqual(Path(audit_runner2.EVIDENCE_CLI).name, 'cli.mjs')

    def test_collect_modern_web_reads_real_usage_from_a_real_page(self):
        with temp_cwd():
            report, artifact = audit_runner2.collect_modern_web('fixture.test', FIXTURE.as_uri())
            self.assertTrue(report.get('ok'), report)
            self.assertEqual(report.get('probe'), 'modern-web-features')
            for family in FAMILIES:
                with self.subTest(family=family):
                    self.assertTrue(report['css']['families'][family]['used'], f'{family} not detected')
            self.assertTrue(Path(artifact).exists(), 'evidence artifact was not written')
            self.assertEqual(json.loads(Path(artifact).read_text())['url'], report['url'])
            self.assertEqual(report['artifact'], artifact)

    def test_collect_modern_web_fails_closed_when_the_page_never_loads(self):
        """No usable evidence must never come back looking like a measurement."""
        missing = (FIXTURE.parent / 'does-not-exist.html').as_uri()
        with temp_cwd():
            report, artifact = audit_runner2.collect_modern_web('missing.test', missing)
            self.assertIs(report.get('ok'), False, report)
            self.assertTrue(report.get('error'), 'failure must carry a reason')
            self.assertIn('navigation failed', report['error'])
            self.assertEqual(report['artifact'], artifact)

    def test_mode1_record_carries_modern_web_evidence(self):
        """The end-to-end assertion: a Mode 1 site record includes the probe."""
        with temp_cwd() as tmp:
            record = audit_runner2.audit_site('fixture.test', 1, url=FIXTURE.as_uri())
            record_path = Path(tmp) / 'evidence' / 'fixture.test' / 'modern-web-features.json'
            self.assertTrue(record_path.exists(), 'run did not retain the probe artifact')
        evidence = record['evidence']
        self.assertIn('modern_web', evidence)
        self.assertIn('modern_web_artifact', evidence)
        self.assertTrue(evidence['modern_web']['ok'], evidence['modern_web'])
        families = evidence['modern_web']['css']['families']
        self.assertTrue(families['anchorPositioning']['used'])
        self.assertTrue(families['scrollDrivenAnimations']['used'])

    def test_default_url_is_the_https_origin(self):
        """The crawler's normal path must keep building the manifest URL."""
        calls = []
        original_collect = audit_runner2.collect_modern_web
        original_run = audit_runner2.run_evidence
        audit_runner2.collect_modern_web = lambda domain, url: (
            calls.append((domain, url)) or ({'ok': True, 'css': {'families': {}}}, 'x'))
        audit_runner2.run_evidence = lambda primitive, url, **kwargs: {'observed': {}}
        try:
            with temp_cwd():
                audit_runner2.audit_site('example.com', 3)
        finally:
            audit_runner2.collect_modern_web = original_collect
            audit_runner2.run_evidence = original_run
        self.assertEqual(calls, [('example.com', 'https://www.example.com/')])


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
