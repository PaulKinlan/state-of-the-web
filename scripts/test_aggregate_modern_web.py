#!/usr/bin/env python3
"""Regression suite: the aggregator must refuse to produce a confident wrong number.

Adoption percentages are the output most likely to be quoted and least likely to
be re-derived, so the failure that matters is not a crash -- it is a plausible
figure computed from evidence that cannot support it. Three ways that happens,
all pinned here:

  1. Evidence from the pre-fix probe, which counted opt-outs and shorthand-
     expanded defaults as usage. Its numbers are an upper bound, not a
     measurement.
  2. Evidence mixing two probe generations, where the percentage means nothing
     because the denominator spans two definitions of "used".
  3. A single denominator, hiding that some targets were never judged and some
     were judged on partially readable CSS.

Synthetic reports keep the suite fast and make each case explicit; one test
drives the real probe so the shapes cannot drift apart silently.

  python3 -m unittest scripts.test_aggregate_modern_web -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'aggregate_modern_web.py'
PROBE = ROOT / 'scripts' / 'probes' / 'modern-web-features.js'
FIXTURES = ROOT / 'scripts' / 'fixtures'
FAMILIES = ['viewTransitions', 'scrollDrivenAnimations', 'anchorPositioning',
            'scrollStateChrome', 'gesturePlatforms']
CLI_CANDIDATES = [Path.home() / '.web-uplift' / 'evidence' / 'cli.mjs']
SKIP_ENV = 'ALLOW_SKIP_BROWSER_TESTS'


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=300)


def report(*, used=(), value_aware=True, ok=True, inaccessible=0,
           opted_out=(), inert_defaults=(), timelines=0, url='https://example.test/'):
    """A probe report with exactly the fields the aggregator reads."""
    if not ok:
        return {'probe': 'modern-web-features', 'ok': False, 'url': url,
                'error': 'navigation failed: chrome-error://chromewebdata/'}
    families = {}
    for family in FAMILIES:
        entry = {'used': family in used, 'declarations': [], 'inlineHit': False}
        if value_aware:
            entry.update({
                'samples': [], 'usedCount': 1 if family in used else 0,
                'optedOut': [], 'optedOutCount': 1 if family in opted_out else 0,
                'inertDefaults': [], 'inertDefaultCount': 1 if family in inert_defaults else 0,
            })
        families[family] = entry
    payload = {
        'probe': 'modern-web-features',
        'ok': True,
        'url': url,
        'css': {
            'sheets': {'total': 1 + inaccessible, 'readable': 1,
                       'inaccessible': inaccessible, 'unreadableUrls': []},
            'rulesScanned': 10,
            'atRules': {},
            'families': families,
        },
        'animations': {'total': timelines, 'withTimeline': timelines,
                       'scrollTimelines': timelines, 'viewTimelines': 0},
    }
    if value_aware:
        payload['matching'] = 'value-aware'
    return payload


class EvidenceTree:
    """A directory of probe reports, laid out the way the crawler writes them."""

    def __init__(self, reports):
        self._reports = reports

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for index, payload in enumerate(self._reports, 1):
            site = root / f'site-{index:03d}'
            site.mkdir()
            (site / 'modern-web-features.json').write_text(json.dumps(payload, indent=2))
        self.path = root
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False


class GenerationGuardTest(unittest.TestCase):
    def test_legacy_evidence_is_refused_by_default(self):
        """Pre-fix numbers are inflated; producing them silently is the bug."""
        with EvidenceTree([report(used=['viewTransitions'], value_aware=False)]) as tree:
            result = run(str(tree.path))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('pre-fix probe', result.stderr)
        self.assertNotIn('%', result.stdout, 'no percentage may be printed for legacy evidence')

    def test_legacy_evidence_is_labelled_when_explicitly_allowed(self):
        with EvidenceTree([report(used=['viewTransitions'], value_aware=False)]) as tree:
            result = run(str(tree.path), '--allow-legacy')
            payload_path = Path(tree.path) / 'summary.json'
            run(str(tree.path), '--allow-legacy', '--json', str(payload_path))
            payload = json.loads(payload_path.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('legacy-inflated', result.stdout)
        self.assertIn('upper bound', result.stdout)
        self.assertFalse(payload['trustworthy'])
        self.assertEqual(payload['generation'], 'legacy-inflated')

    def test_mixed_generations_are_refused_even_with_allow_legacy(self):
        """A percentage spanning two definitions of 'used' means nothing."""
        with EvidenceTree([report(used=['viewTransitions']),
                           report(used=['viewTransitions'], value_aware=False)]) as tree:
            default = run(str(tree.path))
            forced = run(str(tree.path), '--allow-legacy')
        for result in (default, forced):
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('mixes probe generations', result.stderr)

    def test_value_aware_evidence_needs_no_flag(self):
        with EvidenceTree([report(used=['anchorPositioning'])]) as tree:
            result = run(str(tree.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('value-aware', result.stdout)
        self.assertNotIn('upper bound', result.stdout)


class DenominatorTest(unittest.TestCase):
    def test_failed_probes_are_excluded_rather_than_counted_as_zero(self):
        """A target that was never judged is not a site that lacks the feature."""
        with EvidenceTree([report(used=['viewTransitions']),
                           report(ok=False),
                           report(ok=False)]) as tree:
            out = Path(tree.path) / 'out.json'
            result = run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload['denominators'], {
            'reportsFound': 3, 'judged': 1, 'failed': 2, 'fullyReadable': 1, 'partialCss': 0,
        })
        # 1 of 1 judged, NOT 1 of 3 found.
        self.assertEqual(payload['families']['viewTransitions']['adoptionOfJudged'], 100.0)
        self.assertTrue(any('unmeasured' in note for note in payload['caveats']))

    def test_partial_css_sites_are_reported_as_a_separate_denominator(self):
        """Absence in unreadable CSS is doubt, not a measured negative."""
        with EvidenceTree([report(used=['viewTransitions']),
                           report(inaccessible=3)]) as tree:
            out = Path(tree.path) / 'out.json'
            run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        detail = payload['families']['viewTransitions']
        self.assertEqual(payload['denominators']['partialCss'], 1)
        self.assertEqual(detail['adoptionOfJudged'], 50.0, 'one of two judged sites')
        self.assertEqual(detail['adoptionOfFullyReadable'], 100.0, 'one of one fully readable site')
        self.assertTrue(any('not found in the readable CSS' in note for note in payload['caveats']))

    def test_every_percentage_states_its_denominator(self):
        with EvidenceTree([report(used=['gesturePlatforms'])]) as tree:
            out = Path(tree.path) / 'out.json'
            run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        for family in FAMILIES:
            with self.subTest(family=family):
                detail = payload['families'][family]
                self.assertIn('judged', detail)
                self.assertIn('fullyReadable', detail)
        self.assertTrue(any('denominator' in note for note in payload['caveats']))

    def test_unreadable_report_file_is_a_failure_not_a_zero(self):
        with EvidenceTree([report(used=['viewTransitions'])]) as tree:
            broken = Path(tree.path) / 'site-002'
            broken.mkdir()
            (broken / 'modern-web-features.json').write_text('{ not json')
            out = Path(tree.path) / 'out.json'
            run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        self.assertEqual(payload['denominators']['failed'], 1)
        self.assertEqual(payload['denominators']['judged'], 1)


class SignalTest(unittest.TestCase):
    def test_opt_outs_and_inert_defaults_are_counted_separately(self):
        """The distinction only exists in value-aware evidence; report it."""
        with EvidenceTree([report(opted_out=['viewTransitions']),
                           report(inert_defaults=['scrollDrivenAnimations'])]) as tree:
            out = Path(tree.path) / 'out.json'
            run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        self.assertEqual(payload['families']['viewTransitions']['sitesWithDeclaredOptOut'], 1)
        self.assertEqual(payload['families']['viewTransitions']['usedSites'], 0)
        self.assertEqual(payload['families']['scrollDrivenAnimations']['sitesWithInertDefaultsOnly'], 1)

    def test_live_timelines_are_reported_as_an_independent_cross_check(self):
        """Runtime timelines survive unreadable CSS, so they corroborate."""
        with EvidenceTree([report(timelines=3, inaccessible=5)]) as tree:
            out = Path(tree.path) / 'out.json'
            run(str(tree.path), '--json', str(out))
            payload = json.loads(out.read_text())
        self.assertEqual(payload['crossCheck']['sitesWithLiveScrollOrViewTimeline'], 1)


class UsageTest(unittest.TestCase):
    def test_missing_or_empty_evidence_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run(str(Path(tmp) / 'nope')).returncode, 2)
            self.assertEqual(run(tmp).returncode, 2)


class RealProbeShapeTest(unittest.TestCase):
    """The aggregator reads fields the probe writes; pin them against drift."""

    def test_aggregates_evidence_the_current_probe_actually_produces(self):
        cli = next((candidate for candidate in CLI_CANDIDATES if candidate.exists()), None)
        if cli is None:
            import os
            if os.environ.get(SKIP_ENV, '').strip().lower() in {'1', 'true', 'yes'}:
                self.skipTest(f'evidence CLI not installed; skipped because {SKIP_ENV} is set')
            raise RuntimeError(
                f'web-uplift evidence CLI not found (tried {CLI_CANDIDATES[0]}). This test '
                f'verifies the aggregator against real probe output; set {SKIP_ENV}=1 to skip it.'
            )
        fixture = FIXTURES / 'modern-web-features.html'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site' / 'modern-web-features.json'
            out.parent.mkdir()
            result = subprocess.run(
                ['node', str(cli), 'evaluate', fixture.as_uri(),
                 '--expr-file', str(PROBE), '--out', str(out), '--quiet'],
                capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr)
            produced = json.loads(out.read_text())
            self.assertEqual(produced.get('matching'), 'value-aware',
                             'the landed probe must mark its generation')

            summary_path = Path(tmp) / 'summary.json'
            aggregated = run(str(Path(tmp)), '--json', str(summary_path))
            self.assertEqual(aggregated.returncode, 0, aggregated.stderr)
            payload = json.loads(summary_path.read_text())

        self.assertTrue(payload['trustworthy'])
        self.assertEqual(payload['denominators']['judged'], 1)
        # The fixture exercises every family, so each must register.
        for family in FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(payload['families'][family]['usedSites'], 1,
                                 f'{family} not aggregated from real probe output')


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
