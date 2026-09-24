#!/usr/bin/env python3
"""Real clicks distinguish framework HTML, History API routes and full documents."""
import collections
import contextlib
import http.server
import io
import json
import tempfile
import threading
import time
import re
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts import speculative_loading as check
from scripts.test_modern_web_probe import chrome_binary, evidence_cli, missing_harness


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests['POST ' + self.path] += 1
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parts = urlsplit(self.path)
        mode = parse_qs(parts.query).get('mode', ['mpa'])[0]
        self.server.requests[parts.path] += 1
        if mode == 'stall':
            time.sleep(5)
            return
        marker = '<div id="__next"></div>' if mode in ('next', 'next-spa') else ''
        if mode == 'nuxt':
            marker = '<div id="__nuxt"></div>'
        if mode == 'vue':
            marker = '<div data-server-rendered="true"></div>'
        if mode == 'base-target':
            marker = '<base target="_blank">'
        script = ''
        if mode in ('spa', 'next-spa', 'hash-spa', 'trusted-only'):
            guard = 'if (!e.isTrusted) return;' if mode == 'trusted-only' else ''
            script = f"next.onclick = e => {{ {guard} e.preventDefault(); history.pushState({{}}, '', next.href); document.querySelector('p').textContent = 'Client route'; }};"
        elif mode == 'effects':
            script = "next.onclick = e => { e.preventDefault(); fetch('/effect', {method:'POST'}); };"
        elif mode == 'navigation-api':
            script = "navigation.addEventListener('navigate', e => { if (e.canIntercept) e.intercept({handler() { document.querySelector('p').textContent = 'Client route'; }}); });"
        elif mode == 'canceled':
            script = 'next.onclick = e => e.preventDefault();'
        elif mode == 'same-url-history':
            script = "next.onclick = e => { e.preventDefault(); history.replaceState({}, '', location.href); };"
        if script:
            script = "const next = document.querySelector('#next'); " + script
        body = f'''<!doctype html><meta charset="utf-8"><title>Navigation fixture</title>
        <p>{'New document' if parts.path == '/next' else 'Source page'}</p>{marker}
        <a id="next" href="{'#/next' if mode == 'hash-spa' else '/next'}">Next article</a>
        <a href="#section">Section</a><div id="section">Section content</div>
        <a href="/download" download>Download</a><a href="/new-tab" target="_blank">New tab</a>
        <a href="/missing-destination">Missing page</a>
        <script>{script}</script>'''.encode()
        self.send_response(404 if parts.path == '/missing-destination' else 200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionError:
            pass


class NavigationBehaviourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not chrome_binary():
            raise missing_harness('Chrome required for navigation-behaviour verification')
        evidence_cli()
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.requests = collections.Counter()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.requests.clear()

    def probe(self, mode, follow=None):
        report = check.run_speculative_probe(self.origin + '/?mode=' + mode, wait_ms=100, follow_link=follow)
        self.assertTrue(report.get('ok'), report)
        return report

    def test_framework_snapshots_are_hints_not_routing_proof(self):
        for mode, hint in [('next', 'next'), ('nuxt', 'nuxt'), ('vue', 'vue-ssr'), ('spa', None)]:
            with self.subTest(mode=mode):
                report = self.probe(mode)
                self.assertEqual(report['navigationContext']['frameworkHint'], hint)
                self.assertIsNone(report['navigationContext']['isClientSideRouted'])
                self.assertEqual(check.synthesize_check_outcome(report)['status'], 'blocked')
        self.assertEqual(self.server.requests['/next'], 0, 'snapshot mode activated a link')

    def test_snapshot_never_executes_action_handlers(self):
        report = self.probe('effects')
        self.assertEqual(check.synthesize_check_outcome(report)['status'], 'blocked')
        self.assertEqual(self.server.requests['POST /effect'], 0)
        self.assertEqual(self.server.requests['/next'], 0)

    def test_framework_mpa_clicks_are_document_navigations(self):
        for mode in ('next', 'nuxt', 'mpa'):
            with self.subTest(mode=mode):
                report = self.probe(mode, '/next')
                observation = report['navigationContext']['navigationObservation']
                self.assertEqual(observation['type'], 'document', observation)
                self.assertNotEqual(observation['beforeLoaderId'], observation['afterLoaderId'])
                self.assertIs(report['navigationContext']['isClientSideRouted'], False)
                outcome = check.synthesize_check_outcome(report)
                self.assertEqual(outcome['status'], 'issues', outcome)
                self.assertIn('selected route', outcome['evidence'])
        self.assertEqual(self.server.requests['/next'], 3)

    def test_client_routes_work_with_or_without_framework_markers(self):
        for mode in ('spa', 'next-spa', 'navigation-api', 'trusted-only', 'hash-spa'):
            with self.subTest(mode=mode):
                href = '#/next' if mode == 'hash-spa' else '/next'
                report = self.probe(mode, href)
                observation = report['navigationContext']['navigationObservation']
                self.assertEqual(observation['type'], 'same-document', observation)
                self.assertEqual(observation['beforeLoaderId'], observation['afterLoaderId'])
                expected = self.origin + '/?mode=hash-spa#/next' if mode == 'hash-spa' else self.origin + '/next'
                self.assertEqual(observation['toUrl'], expected)
                outcome = check.synthesize_check_outcome(report)
                self.assertEqual(outcome['status'], 'not-applicable', outcome)
                self.assertIn("does not classify", outcome['evidence'])
        self.assertEqual(self.server.requests['/next'], 0, 'client routes made document requests')

    def test_fragment_jumps_and_canceled_or_same_url_events_remain_unknown(self):
        for mode, link in [('mpa', '#section'), ('canceled', '/next'), ('same-url-history', '/next')]:
            with self.subTest(mode=mode):
                report = self.probe(mode, link)
                self.assertIsNone(report['navigationContext']['isClientSideRouted'])
                self.assertEqual(check.synthesize_check_outcome(report)['status'], 'blocked')

    def test_no_guessing_at_missing_download_cross_origin_or_new_tab_links(self):
        for href in ('/absent', '/download', '/new-tab', 'https://example.invalid/'):
            with self.subTest(href=href):
                report = self.probe('mpa', href)
                self.assertEqual(report['navigationContext']['navigationObservation']['type'], 'unobserved')
                self.assertEqual(check.synthesize_check_outcome(report)['status'], 'blocked')
        self.assertEqual(self.server.requests['/absent'] + self.server.requests['/download'] + self.server.requests['/new-tab'], 0)
        inherited = self.probe('base-target', '/next')
        self.assertEqual(inherited['navigationContext']['navigationObservation']['type'], 'unobserved')
        self.assertEqual(self.server.requests['/next'], 0)

    def test_failed_destination_is_not_an_applicability_finding(self):
        report = self.probe('mpa', '/missing-destination')
        observation = report['navigationContext']['navigationObservation']
        self.assertEqual(observation['httpStatus'], 404)
        self.assertEqual(observation['type'], 'unobserved')
        self.assertEqual(check.synthesize_check_outcome(report)['status'], 'blocked')

    def test_stalled_driver_fails_closed_and_cleans_its_real_profile(self):
        with patch.object(check, 'kill_profile', wraps=check.kill_profile) as cleanup:
            report = check.run_speculative_probe(self.origin + '/?mode=stall', timeout=2, wait_ms=0, follow_link='/next')
        self.assertFalse(report['ok'])
        self.assertIn('timeout', report['error'])
        cleanup.assert_called_once()
        profiles = re.findall(r'/tmp/web-uplift-cdp-[A-Za-z0-9_-]+', cleanup.call_args.args[0])
        self.assertTrue(profiles, 'owned Chrome profile was not identified')
        for profile in profiles:
            self.assertFalse(Path(profile).exists(), 'profile directory leaked')
            deadline = time.monotonic() + 5
            while True:
                alive = subprocess.run(['pgrep', '-f', '--', '--user-data-dir=' + profile], capture_output=True, timeout=5)
                if alive.returncode != 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            self.assertNotEqual(alive.returncode, 0, 'owned Chrome process survived timeout')

    def test_selected_link_evidence_is_persisted_by_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'report.json'
            report = check.probe_single_target(self.origin + '/?mode=next', out, wait_ms=100, follow_link='/next')
            self.assertEqual(report['outcome']['status'], 'issues')
            self.assertEqual(json.loads(out.read_text())['probe']['navigationContext'], report['probe']['navigationContext'])


class ApplicabilityContractTest(unittest.TestCase):
    def report(self):
        return {'ok': True, 'signals': {}, 'speculationRules': {}, 'navigationContext': {
            'anchorCount': 2, 'internalLinkCount': 2, 'externalLinkCount': 0,
            'isClientSideRouted': True, 'frameworkRouter': 'next',
        }}

    def test_legacy_framework_derived_boolean_is_not_trusted(self):
        self.assertEqual(check.synthesize_check_outcome(self.report())['status'], 'blocked')

    def test_explicit_override_is_labelled_as_operator_context(self):
        for override, status in [(True, 'not-applicable'), (False, 'issues')]:
            with self.subTest(override=override):
                outcome = check.synthesize_check_outcome(self.report(), is_spa_override=override)
                self.assertEqual(outcome['status'], status)
                self.assertIn('Operator-supplied', outcome['method'])

    def test_manifest_wide_link_activation_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            check.main(['manifest.txt', '--follow-link', '/next'])
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
