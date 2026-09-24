#!/usr/bin/env python3
"""Crawler test suite: the modern-web feature probe and its crawler wiring.

Drives the real web-uplift evidence CLI against the committed fixtures in a real
headless Chrome (Chrome 134+ features: anchor positioning, scroll-driven
animations, view transitions, scroll-state container queries, platform gestures).
"Exit 0" is not the assertion: the probe must report the features on the page that
uses them and must report nothing on the page that does not.

  python3 -m unittest scripts.test_modern_web_probe -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / 'scripts' / 'probes' / 'modern-web-features.js'
FIXTURES = ROOT / 'scripts' / 'fixtures'
MODERN_FIXTURE = FIXTURES / 'modern-web-features.html'
PLAIN_FIXTURE = FIXTURES / 'plain-page.html'
CRAWLER = ROOT / 'scripts' / 'modern_web_probe.py'
CLI_CANDIDATES = [
    Path('/home/paulkinlan/journal/.web-uplift/evidence/cli.mjs'),
    Path.home() / '.web-uplift' / 'evidence' / 'cli.mjs',
]
CHROME_CANDIDATES = ['/usr/bin/google-chrome-stable', '/usr/bin/google-chrome', '/usr/bin/chromium']

FEATURE_FAMILIES = [
    'viewTransitions',
    'scrollDrivenAnimations',
    'anchorPositioning',
    'scrollStateChrome',
    'gesturePlatforms',
]


def evidence_cli() -> Path:
    for candidate in CLI_CANDIDATES:
        if candidate.exists():
            return candidate
    raise unittest.SkipTest('web-uplift evidence CLI not installed')


def chrome_available() -> bool:
    return any(Path(candidate).exists() for candidate in CHROME_CANDIDATES)


def probe(url: str) -> dict:
    """Run the probe in a real browser through the evidence harness."""
    result = subprocess.run(
        ['node', str(evidence_cli()), 'evaluate', url, '--expr-file', str(PROBE), '--quiet'],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f'evaluate failed for {url}:\n{result.stdout}\n{result.stderr}')
    start = result.stdout.find('{')
    if start < 0:
        raise AssertionError(f'evaluate produced no JSON for {url}:\n{result.stdout}\n{result.stderr}')
    return json.loads(result.stdout[start:])


class ModernWebProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not chrome_available():
            raise unittest.SkipTest('no Chrome binary available')
        evidence_cli()

    def test_fixture_covers_every_modern_feature_family(self):
        """The fixture must exercise -- and the probe must detect -- each family."""
        report = probe(MODERN_FIXTURE.as_uri())
        self.assertTrue(report['ok'], report)
        self.assertGreater(report['css']['rulesScanned'], 0, 'no CSS rules were scanned')
        self.assertEqual(report['css']['sheets']['inaccessible'], 0, 'fixture CSS must be readable')
        self.assertEqual(report['css']['sheets']['unreadableUrls'], [], 'fixture has no cross-origin CSS')
        for family in FEATURE_FAMILIES:
            with self.subTest(family=family):
                self.assertTrue(report['css']['families'][family]['used'], f'{family} not detected')
        self.assertTrue(report['css']['atRules']['crossDocumentViewTransitions'], '@view-transition missing')
        self.assertTrue(report['css']['atRules']['viewTransitionPseudo'], '::view-transition missing')
        self.assertTrue(report['css']['atRules']['scrollStateContainerQuery'], 'scroll-state() missing')
        self.assertTrue(report['css']['families']['anchorPositioning']['inlineHit'], 'inline anchor-name missing')
        self.assertTrue(report['viewTransitions']['apiReferencedInInlineScript'], 'startViewTransition not seen')
        self.assertIsInstance(report['viewTransitions']['apiAvailable'], bool)

    def test_platform_support_is_reported_for_chrome_134_features(self):
        """Chrome 134+ supports the catalog's modern platform features."""
        supported = probe(MODERN_FIXTURE.as_uri())['supported']
        for feature in [
            'viewTransitionName',
            'animationTimelineScroll',
            'animationTimelineView',
            'anchorName',
            'positionAnchor',
            'positionTryFallbacks',
            'scrollStateContainer',
        ]:
            with self.subTest(feature=feature):
                self.assertIs(supported[feature], True, f'{feature} unsupported in this Chrome')

    def test_plain_page_reports_no_modern_features(self):
        """No false positives: ordinary CSS must not register as modern usage."""
        report = probe(PLAIN_FIXTURE.as_uri())
        for family in FEATURE_FAMILIES:
            with self.subTest(family=family):
                self.assertFalse(report['css']['families'][family]['used'], f'{family} false positive')
        self.assertFalse(report['css']['atRules']['crossDocumentViewTransitions'])
        self.assertFalse(report['css']['atRules']['scrollStateContainerQuery'])
        self.assertFalse(report['viewTransitions']['apiReferencedInInlineScript'])


class CrawlerWiringTest(unittest.TestCase):
    def test_crawler_runs_the_modern_web_probe(self):
        source = CRAWLER.read_text()
        self.assertIn('modern-web-features.js', source, 'crawler does not run the modern-web probe')
        self.assertIn("'evaluate'", source.replace('"', "'"), 'crawler does not use the evaluate primitive')

    def test_crawler_accumulates_ordered_results_and_exits_nonzero_on_failure(self):
        source = CRAWLER.read_text()
        self.assertIn("summary['failed']", source, 'crawler does not fail closed on missing evidence')
        self.assertIn('Judgement-free', source, 'crawler does not declare its evidence as judgement-free')

    def test_crawler_skips_targets_that_already_have_evidence(self):
        source = CRAWLER.read_text()
        self.assertIn('cached', source, 'crawler is not resumable')

    def test_target_parsing_accepts_manifest_and_url_forms(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        self.assertEqual(modern_web_probe.target('42\texample.com'), (42, 'example.com'))
        self.assertEqual(modern_web_probe.target('42 example.com'), (42, 'example.com'))
        self.assertEqual(modern_web_probe.target('42,www.example.com'), (42, 'www.example.com'))
        self.assertEqual(modern_web_probe.target('example.com'), (None, 'example.com'))
        self.assertEqual(modern_web_probe.url_for('example.com'), 'https://example.com/')
        self.assertEqual(modern_web_probe.url_for('https://example.com/x'), 'https://example.com/x')

    def test_evidence_validation_rejects_chrome_error_pages(self):
        """A written file is not evidence: exit zero on an error page is a failure."""
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        import tempfile
        cases = [
            ({'ok': True, 'url': 'https://example.com/'}, True),
            ({'ok': True, 'url': 'chrome-error://chromewebdata/'}, False),
            ({'ok': True, 'url': ''}, False),
            ({'ok': False, 'url': 'https://example.com/'}, False),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, (payload, expected) in enumerate(cases):
                path = Path(tmp) / f'{index}.json'
                path.write_text(json.dumps(payload))
                with self.subTest(payload=payload):
                    self.assertIs(modern_web_probe.validate_evidence(path)[0], expected)
            unreadable = Path(tmp) / 'broken.json'
            unreadable.write_text('not json')
            self.assertFalse(modern_web_probe.validate_evidence(unreadable)[0])

    def test_manifest_rows_keep_rank_and_domain(self):
        """A TSV manifest row must become rank+domain, not one opaque target."""
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tsv = Path(tmp) / 'manifest.csv'
            tsv.write_text('1\tweb.dev\n2\twww.example.com\n')
            self.assertEqual(modern_web_probe.load_targets(str(tsv)), [(1, 'web.dev'), (2, 'www.example.com')])
            csv_path = Path(tmp) / 'manifest2.csv'
            csv_path.write_text('3,example.com\n')
            self.assertEqual(modern_web_probe.load_targets(str(csv_path)), [(3, 'example.com')])

    def test_probe_is_valid_javascript(self):
        result = subprocess.run(['node', '--check', str(PROBE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
