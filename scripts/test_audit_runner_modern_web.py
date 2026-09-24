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

import shutil
import uuid

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
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

    def test_collect_modern_web_retains_nested_shadow_evidence(self):
        fixture = FIXTURE.with_name('shadow-dom-features.html').as_uri() + '#nested'
        with temp_cwd():
            report, artifact = audit_runner2.collect_modern_web('shadow.test', fixture)
            self.assertTrue(report.get('ok'), report)
            self.assertEqual(report['css']['scope']['openShadowRootsScanned'], 2)
            for family in FAMILIES:
                self.assertTrue(report['css']['families'][family]['used'], family)
            on_disk = json.loads(Path(artifact).read_text())
            self.assertEqual(on_disk['css'], report['css'])

    def test_collect_modern_web_fails_closed_when_the_page_never_loads(self):
        """No usable evidence must never come back looking like a measurement."""
        missing = (FIXTURE.parent / 'does-not-exist.html').as_uri()
        with temp_cwd():
            report, artifact = audit_runner2.collect_modern_web('missing.test', missing)
            self.assertIs(report.get('ok'), False, report)
            self.assertTrue(report.get('error'), 'failure must carry a reason')
            self.assertIn('navigation failed', report['error'])
            self.assertEqual(report['artifact'], artifact)
            on_disk = json.loads(Path(artifact).read_text())
            self.assertIs(on_disk['ok'], False, 'the artifact on disk still claims success')
            self.assertIn('navigation failed', on_disk['error'])
            self.assertEqual(on_disk['url'], missing)

    def test_stale_artifact_is_replaced_not_read_back(self):
        """A leftover success from an earlier run must never be reported as this one."""
        missing = (FIXTURE.parent / 'does-not-exist.html').as_uri()
        with temp_cwd() as tmp:
            artifact = Path(tmp) / 'evidence' / 'stale.test' / 'modern-web-features.json'
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({'probe': 'modern-web-features', 'ok': True,
                                            'url': 'https://stale.example/', 'css': {'families': {}}}))
            report, artifact_path = audit_runner2.collect_modern_web('stale.test', missing)
            on_disk = json.loads(Path(artifact_path).read_text())
        self.assertIs(report.get('ok'), False)
        self.assertIsNot(on_disk.get('ok'), True, 'stale success survived the failed run')
        self.assertIn('navigation failed', on_disk['error'])
        self.assertNotIn('stale.example', json.dumps(on_disk))

    def test_timeout_writes_a_failure_artifact(self):
        """A hung page must produce a failure payload on disk, not silence."""
        server = StallingServer()
        original_timeout = audit_runner2.PROBE_TIMEOUT
        audit_runner2.PROBE_TIMEOUT = 6
        try:
            with temp_cwd():
                report, artifact = audit_runner2.collect_modern_web('stall.test', server.url)
                on_disk = json.loads(Path(artifact).read_text())
        finally:
            audit_runner2.PROBE_TIMEOUT = original_timeout
            server.close()
        self.assertIs(report.get('ok'), False)
        self.assertIn('timed out', report['error'])
        self.assertIs(on_disk['ok'], False)
        self.assertIn('timed out', on_disk['error'])
        self.assertEqual(on_disk['url'], server.url)

    def test_nonzero_exit_is_a_failure_even_if_an_artifact_exists(self):
        """A CLI that fails must not be able to leave a success behind."""
        with temp_cwd() as tmp:
            broken = Path(tmp) / 'broken-probe.js'
            broken.write_text('this is not valid javascript (')
            original_probe = audit_runner2.MODERN_WEB_PROBE
            audit_runner2.MODERN_WEB_PROBE = str(broken)
            try:
                report, artifact = audit_runner2.collect_modern_web('broken.test', FIXTURE.as_uri())
                on_disk = json.loads(Path(artifact).read_text())
            finally:
                audit_runner2.MODERN_WEB_PROBE = original_probe
        self.assertIs(report.get('ok'), False, report)
        self.assertIn('exited', report['error'])
        self.assertIs(on_disk['ok'], False)

    def test_kill_profile_terminates_the_real_process_and_removes_the_directory(self):
        """Real cleanup, not a recorded argv shape."""
        profile_dir = Path(tempfile.mkdtemp(prefix='web-uplift-cdp-TimeoutTest'))
        victim = subprocess.Popen(['bash', '-c', 'sleep 300; exit 0', f'--user-data-dir={profile_dir}'])
        try:
            killed = audit_runner2.kill_profile(f'[browser] launching chrome (profile {profile_dir})')
            deadline = time.time() + 5
            while victim.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            survived = victim.poll() is None
        finally:
            if victim.poll() is None:
                victim.kill()
                victim.wait(timeout=10)
            shutil.rmtree(profile_dir, ignore_errors=True)
        self.assertEqual(killed, [str(profile_dir)])
        self.assertFalse(survived, 'timed-out Chrome profile process was not killed')

    def test_kill_profile_ignores_output_without_a_profile(self):
        self.assertEqual(audit_runner2.kill_profile('nothing to see here'), [])

    def test_kill_profile_finds_profiles_in_any_temp_root(self):
        """`mkdtemp` honours TMPDIR, so cleanup must not assume /tmp.

        The os3 pilot hit this for real: /tmp inode exhaustion forced TMPDIR onto
        disk-backed storage, where a /tmp-only cleanup would silently stop working.
        Names are generated per run so this test cannot match its own process.
        """
        import tempfile as _tempfile
        suffix = uuid.uuid4().hex[:8]
        for root in sorted({str(_tempfile.gettempdir()), '/var/tmp'}):
            candidate = f'{root}/web-uplift-cdp-Sweep{suffix}'
            found = audit_runner2.kill_profile(f'[browser] launching chrome (profile {candidate})')
            with self.subTest(root=root):
                self.assertIn(candidate, found, f'profile under {root} was not detected')

    def test_kill_profile_refuses_paths_outside_temp_roots(self):
        suffix = uuid.uuid4().hex[:8]
        outside = f'/etc/web-uplift-cdp-Outside{suffix}'
        self.assertEqual(audit_runner2.kill_profile(f'chrome --user-data-dir={outside}'), [])

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


class StallingServer:
    """Accepts connections and never replies, so a navigation hangs."""

    def __init__(self):
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(5)
        self.url = f'http://127.0.0.1:{self.socket.getsockname()[1]}/stall'
        self.held = []
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self.running:
            try:
                connection, _ = self.socket.accept()
                self.held.append(connection)
            except OSError:
                return

    def close(self):
        self.running = False
        for connection in self.held:
            try:
                connection.close()
            except OSError:
                pass
        try:
            self.socket.close()
        except OSError:
            pass


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
