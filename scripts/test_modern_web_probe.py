#!/usr/bin/env python3
"""Crawler test suite: the modern-web feature probe and its crawler wiring.

Drives the real web-uplift evidence CLI against the committed fixtures in a real
headless Chrome (anchor positioning, scroll-driven animations, view transitions,
scroll-state container queries, platform gestures). "Exit 0" is not the
assertion: the probe must report the features on the page that uses them and must
report nothing on the page that does not.

  python3 -m unittest scripts.test_modern_web_probe -v

MISSING HARNESS IS A FAILURE, NOT A SKIP. If the evidence CLI or Chrome is
absent, the browser-driven tests cannot verify anything, so the suite errors
instead of reporting OK. Set `ALLOW_SKIP_BROWSER_TESTS=1` to skip them
deliberately (a machine with no Chrome, a lint-only CI job). Overrides:
`WEB_UPLIFT_CLI` for the evidence CLI, `CHROME_BIN` for the browser -- the same
variables the crawler and the evidence CLI already honour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / 'scripts' / 'probes' / 'modern-web-features.js'
FIXTURES = ROOT / 'scripts' / 'fixtures'
MODERN_FIXTURE = FIXTURES / 'modern-web-features.html'
PLAIN_FIXTURE = FIXTURES / 'plain-page.html'
OPTED_OUT_FIXTURE = FIXTURES / 'opted-out-features.html'
CRAWLER = ROOT / 'scripts' / 'modern_web_probe.py'
SKIP_ENV = 'ALLOW_SKIP_BROWSER_TESTS'
# Mirrors cdp.mjs: CHROME_BIN wins, then the distro paths, then $PATH. Keeping
# the list in step with the harness means the suite cannot decide Chrome is
# missing while the CLI it shells out to would have found it.
CHROME_CANDIDATES = [
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
]

FEATURE_FAMILIES = [
    'viewTransitions',
    'scrollDrivenAnimations',
    'anchorPositioning',
    'scrollStateChrome',
    'gesturePlatforms',
]


def missing_harness(reason: str) -> Exception:
    """Fail hard when the browser harness is absent; skip only on request.

    A suite that silently skips its browser tests still exits 0 and prints OK,
    so "13 tests pass" can mean "the browser tests evaporated and nothing was
    verified". On a fresh machine or in CI that is a green build with zero real
    evidence -- the exact `it serves` != `it works` failure this repo forbids.
    """
    if os.environ.get(SKIP_ENV, '').strip().lower() in {'1', 'true', 'yes'}:
        return unittest.SkipTest(f'{reason}; skipped because {SKIP_ENV} is set')
    return RuntimeError(
        f'{reason}. The browser-driven tests cannot run, so passing here would prove nothing. '
        f'Install the harness, or set {SKIP_ENV}=1 to skip them deliberately.'
    )


def cli_candidates() -> list[Path]:
    override = os.environ.get('WEB_UPLIFT_CLI', '').strip()
    candidates = [Path(override).expanduser()] if override else []
    candidates.append(Path.home() / '.web-uplift' / 'evidence' / 'cli.mjs')
    return candidates


def evidence_cli() -> Path:
    for candidate in cli_candidates():
        if candidate.exists():
            return candidate
    tried = ', '.join(str(candidate) for candidate in cli_candidates())
    raise missing_harness(f'web-uplift evidence CLI not found (tried {tried}; set WEB_UPLIFT_CLI)')


def chrome_binary() -> str | None:
    """Resolve Chrome the way the evidence CLI does: CHROME_BIN, paths, $PATH."""
    override = os.environ.get('CHROME_BIN', '').strip()
    for candidate in ([override] if override else []) + CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    for name in ('google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found
    return None


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
        if not chrome_binary():
            raise missing_harness(f'no Chrome binary found (tried CHROME_BIN, {", ".join(CHROME_CANDIDATES)}, $PATH)')
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

    def test_opt_outs_and_shorthand_expansion_are_not_counted_as_usage(self):
        """Detection is value-aware: a declared property is not a used feature.

        Two real failure modes are pinned here, both observed on live origins:
        an explicit opt-out (`view-transition-name: none`, `position-anchor:
        unset` -- the microsoft.com reset), and ordinary shorthands that the
        CSSOM expands into tracked longhands (`animation:` -> `animation-
        timeline: auto`, `container:` -> `container-type: inline-size`).
        """
        report = probe(OPTED_OUT_FIXTURE.as_uri())
        self.assertEqual(report['matching'], 'value-aware')
        self.assertGreater(report['css']['rulesScanned'], 0, 'fixture CSS must be readable')
        for family in FEATURE_FAMILIES:
            with self.subTest(family=family):
                detail = report['css']['families'][family]
                self.assertFalse(detail['used'], f'{family} false positive: {detail["samples"]}')
                self.assertEqual(detail['usedCount'], 0, f'{family} counted {detail["usedCount"]} uses')
        # The opt-outs must be REPORTED, not merely dropped: the auditor needs to
        # tell "does not use the feature" apart from "explicitly turned it off".
        for family in ('viewTransitions', 'scrollDrivenAnimations', 'anchorPositioning'):
            with self.subTest(family=family, signal='optedOut'):
                self.assertGreater(report['css']['families'][family]['optedOutCount'], 0,
                                   f'{family} opt-out was dropped instead of recorded')
        self.assertFalse(report['css']['atRules']['scrollStateContainerQuery'])
        self.assertFalse(report['css']['atRules']['viewTransitionPseudo'])


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

    def test_published_manifest_parses_with_rank_and_origin(self):
        """The real manifest is `position,origin,crux_rank_bucket` with a header."""
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        rows = (ROOT / 'results' / 'atomic' / 'manifest.csv').read_text().splitlines()[:4]
        entries = modern_web_probe.rows_to_targets([line.split(',') for line in rows])
        self.assertEqual(len(entries), 3, 'header must be dropped, rows kept')
        for rank, value in entries:
            with self.subTest(rank=rank):
                self.assertIsInstance(rank, int)
                self.assertTrue(value.startswith('https://'), value)
        self.assertEqual(entries[0][0], 1)

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
            csv_path.write_text('domain\nexample.com\n')
            self.assertEqual(modern_web_probe.load_targets(str(csv_path)), [(None, 'example.com')])

    def test_timeout_is_recorded_and_chrome_profile_is_killed(self):
        """One unresponsive target must not kill the crawl or leak Chrome."""
        import subprocess as sp
        import tempfile
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        killed = []
        original_run = modern_web_probe.subprocess.run

        def fake_run(command, **kwargs):
            if command[:2] == ['pkill', '-f']:
                killed.append(list(command))
                return sp.CompletedProcess(command, 0, '', '')
            raise sp.TimeoutExpired(command, modern_web_probe.TIMEOUT,
                                    output='[browser] launching chrome (profile /tmp/web-uplift-cdp-AbC123)')

        modern_web_probe.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                exit_code, detail = modern_web_probe.evaluate_target('https://example.com/', Path(tmp) / 'out.json')
        finally:
            modern_web_probe.subprocess.run = original_run
        self.assertEqual(exit_code, 124)
        self.assertIn('timeout', detail)
        # `--` must terminate option parsing, or pkill rejects the pattern as an
        # option and kills nothing. Shape only -- the behaviour is asserted for
        # real in test_kill_profile_kills_a_real_process.
        self.assertEqual(killed, [['pkill', '-f', '--', '--user-data-dir=/tmp/web-uplift-cdp-AbC123']])

    def test_kill_profile_kills_a_real_process_and_removes_the_profile(self):
        """The cleanup must WORK, not merely look right.

        `pkill -f "--user-data-dir=..."` exits 2 with `unrecognized option` and
        kills nothing, because the pattern starts with `--`. A test that mocks
        subprocess.run cannot see that -- it asserts the argument and passes
        against broken code. This test spawns a real process whose cmdline looks
        like headless Chrome's and asserts the process is gone afterwards.
        """
        import subprocess as sp
        import time
        import uuid
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        profile = Path(f'/tmp/web-uplift-cdp-selftest-{uuid.uuid4().hex[:8]}')
        profile.mkdir(parents=True, exist_ok=True)
        stand_in = sp.Popen(['bash', '-c', f'exec -a "chrome --user-data-dir={profile} --headless" sleep 120'],
                            stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        try:
            for _ in range(50):
                if sp.run(['pgrep', '-f', f'user-data-dir={profile}'], capture_output=True).returncode == 0:
                    break
                time.sleep(0.1)
            else:
                self.fail('could not spawn the stand-in process; the cleanup path is unverified')

            modern_web_probe.kill_profile(str(profile))

            for _ in range(50):
                alive = sp.run(['pgrep', '-f', f'user-data-dir={profile}'], capture_output=True).returncode == 0
                if not alive:
                    break
                time.sleep(0.1)
            self.assertFalse(alive, 'process survived kill_profile -- Chrome would be orphaned')
            self.assertFalse(profile.exists(), 'profile directory was left on disk')
        finally:
            sp.run(['pkill', '-f', '--', f'--user-data-dir={profile}'], capture_output=True)
            stand_in.kill()
            stand_in.wait(timeout=10)
            shutil.rmtree(profile, ignore_errors=True)

    def test_failed_probe_writes_failure_evidence_and_marks_not_ok(self):
        """Fail-closed: a target with no usable evidence is recorded as a failure."""
        import tempfile
        sys.path.insert(0, str(ROOT / 'scripts'))
        import modern_web_probe
        original = modern_web_probe.evaluate_target
        modern_web_probe.evaluate_target = lambda url, out: (0, '')
        try:
            with tempfile.TemporaryDirectory() as tmp:
                item = modern_web_probe.probe(7, 'broken.example', Path(tmp))
                recorded = json.loads(Path(item['out']).read_text())
        finally:
            modern_web_probe.evaluate_target = original
        self.assertFalse(item['ok'])
        self.assertTrue(item['failure'])
        self.assertIs(recorded['ok'], False)
        self.assertEqual(recorded['url'], 'https://broken.example/')

    def test_probe_is_valid_javascript(self):
        result = subprocess.run(['node', '--check', str(PROBE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_harness_fails_hard_unless_skipping_is_opted_into(self):
        """A missing browser harness must break the build, not vanish quietly.

        This pins the fix for the green-when-absent bug: without the opt-in the
        suite must raise a hard error, and only an explicit environment variable
        may downgrade that to a skip. Otherwise `OK` can mean `the browser tests
        were never run`.
        """
        sys.path.insert(0, str(ROOT / 'scripts'))
        import test_modern_web_probe as suite
        previous = os.environ.get(SKIP_ENV)
        try:
            os.environ.pop(SKIP_ENV, None)
            self.assertIsInstance(suite.missing_harness('no harness'), RuntimeError)
            for value in ('1', 'true', 'YES'):
                with self.subTest(value=value):
                    os.environ[SKIP_ENV] = value
                    self.assertIsInstance(suite.missing_harness('no harness'), unittest.SkipTest)
            os.environ[SKIP_ENV] = '0'
            self.assertIsInstance(suite.missing_harness('no harness'), RuntimeError)
        finally:
            os.environ.pop(SKIP_ENV, None)
            if previous is not None:
                os.environ[SKIP_ENV] = previous

    def test_harness_resolution_honours_the_documented_overrides(self):
        """No machine-specific paths: resolution follows env vars, then $PATH."""
        import re
        sys.path.insert(0, str(ROOT / 'scripts'))
        import test_modern_web_probe as suite
        source = Path(suite.__file__).read_text()
        # Built as a pattern, not a literal: a literal home path in the
        # assertion would match itself and fail forever.
        hardcoded = re.compile(r'/home/[a-z][a-z0-9._-]*/').findall(source)
        self.assertEqual(hardcoded, [], f'machine-specific path(s) hardcoded in the suite: {sorted(set(hardcoded))}')
        previous = os.environ.get('WEB_UPLIFT_CLI')
        try:
            os.environ['WEB_UPLIFT_CLI'] = '/tmp/some-explicit-cli.mjs'
            self.assertEqual(suite.cli_candidates()[0], Path('/tmp/some-explicit-cli.mjs'))
        finally:
            os.environ.pop('WEB_UPLIFT_CLI', None)
            if previous is not None:
                os.environ['WEB_UPLIFT_CLI'] = previous
        self.assertEqual(suite.cli_candidates()[-1], Path.home() / '.web-uplift' / 'evidence' / 'cli.mjs')


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
