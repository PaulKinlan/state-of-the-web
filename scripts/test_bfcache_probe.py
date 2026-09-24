#!/usr/bin/env python3
"""Suite for the bfcache prototype: probe, driver, and what the harness can prove.

Drives the real sequence (target -> away -> back) in headless Chrome over local
fixtures, so no network is needed:

  python3 -m unittest scripts.test_bfcache_probe -v

The assertions are deliberately about SIGNAL AGREEMENT, not about the outcome:
under browser automation this Chrome reports the page entered the back/forward
cache and was then evicted (`CacheFlushed`) rather than restored, and a test that
pinned "not restored" would be asserting the harness's limitation instead of the
page's behaviour. What must hold either way: the driver completes, every signal
group is reported, the signals agree with each other, and a non-restore carries an
explanation.
"""
from __future__ import annotations

import functools
import json
import subprocess
import sys
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'scripts' / 'fixtures'
DRIVER = ROOT / 'scripts' / 'bfcache_probe.mjs'
PROBE = ROOT / 'scripts' / 'probes' / 'bfcache-restore.js'
CHROME_CANDIDATES = ['/usr/bin/google-chrome-stable', '/usr/bin/google-chrome', '/usr/bin/chromium']


class FixtureServer:
    """Serve scripts/fixtures on 127.0.0.1 so the drive uses a normal origin."""

    def __init__(self):
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def drive(target, away=None, *options, timeout=240):
    command = ['node', str(DRIVER), target, '--settle', '800', *options]
    if away:
        command.insert(3, away)
    result = subprocess.run(
        command,
        capture_output=True, text=True, timeout=timeout,
    )
    payload = result.stdout[result.stdout.find('{'):]
    return result.returncode, json.loads(payload) if payload.strip() else None, result.stderr


class BfcachePrototypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not any(Path(candidate).exists() for candidate in CHROME_CANDIDATES):
            raise unittest.SkipTest('no Chrome binary available')
        for name in ('bfcache-clean.html', 'bfcache-blocked.html', 'bfcache-second.html'):
            if not (FIXTURES / name).exists():
                raise unittest.SkipTest(f'fixture missing: {name}')
        cls.server = FixtureServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_probe_expression_is_valid_javascript(self):
        result = subprocess.run(['node', '--check', str(PROBE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_driver_completes_and_reports_every_signal_group(self):
        code, report, stderr = drive(f'{self.server.base}/bfcache-clean.html')
        self.assertEqual(code, 0, stderr)
        self.assertTrue(report['ok'], report.get('error'))
        self.assertTrue(report['page']['ok'], report['page'])
        self.assertTrue(report['page']['notRestoredReasonsSupported'],
                        'Chrome must expose PerformanceNavigationTiming.notRestoredReasons')
        self.assertTrue(report['cdp'].get('returnNavigationObserved'),
                        'the return navigation never landed; the probe would read the outgoing document')
        self.assertEqual(report['page']['url'], f'{self.server.base}/bfcache-clean.html',
                         'the probe must read the returned document')

    def test_clean_page_is_never_reported_as_page_attributable(self):
        """The regression that matters: environment eviction must not blame the page.

        On a loaded machine the same clean page sometimes restores and sometimes
        comes back as a Circumstantial CacheFlushed eviction. Either is acceptable;
        a page-attributable blocker for it is not.
        """
        _, report, _ = drive(f'{self.server.base}/bfcache-clean.html')
        self.assertIsNone(report['pageAttributableBlocker'], report['verdict'])
        self.assertIn(report['verdict'], ('restored-from-bfcache', 'inconclusive-environment-eviction'),
                      report['verdict'])
        if report['restoredFromBfcache']:
            restored = [a for a in report['attempts'] if a['restored']][0]
            self.assertEqual(restored['returnNavigationType'], 'BackForwardCacheRestore')
            self.assertTrue(restored['document'].get('persisted'))
            self.assertTrue(restored['loaderIdContinuity']['documentSurvived'])

    def test_unload_handler_is_named_as_the_page_attributable_blocker(self):
        """A page-caused blocker must be named, not inferred from a missing restore."""
        _, report, _ = drive(f'{self.server.base}/bfcache-blocked.html')
        self.assertEqual(report['verdict'], 'not-restored-page-attributable', report['verdict'])
        self.assertIsNotNone(report['pageAttributableBlocker'])
        self.assertIn('UnloadHandler', report['pageAttributableBlocker']['reason'])
        self.assertEqual(report['pageAttributableBlocker']['type'], 'PageSupportNeeded')
        self.assertIn('unload-listener', json.dumps(report['page']['bfcache']['blockers']),
                      'the page-side API should name the same blocker when it is not masked')

    def test_signals_agree(self):
        _, report, _ = drive(f'{self.server.base}/bfcache-clean.html')
        restored_attempts = [a for a in report['attempts'] if a['restored']]
        for attempt in restored_attempts:
            with self.subTest(attempt=attempt['attempt']):
                self.assertTrue(attempt['returnNavigationType'] == 'BackForwardCacheRestore'
                                or attempt['document'].get('persisted') is True
                                or attempt['loaderIdContinuity']['documentSurvived'])
        self.assertEqual(report['restoredFromBfcache'], bool(restored_attempts))

    def test_fail_closed_when_the_sequence_cannot_be_driven(self):
        code, report, _ = drive('http://', None)
        self.assertEqual(code, 1)
        self.assertIs(report['ok'], False)
        self.assertTrue(report['error'], 'a failed drive must carry the reason')


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
