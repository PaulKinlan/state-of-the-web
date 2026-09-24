#!/usr/bin/env python3
"""Real browser/CDP acceptance checks; no external network or source re-fetches."""
import collections
import http.server
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.test_modern_web_probe import ROOT, PROBE, chrome_binary, evidence_cli, missing_harness

COLLECTOR = ROOT / 'scripts' / 'collect_modern_web.mjs'


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        case = parse_qs(parsed.query).get('case', ['plain'])[0]
        with self.server.lock:
            self.server.requests[self.path] += 1
        status = 200
        headers = {}
        if parsed.path == '/bundle.js':
            mime = 'text/javascript'
            if case == 'call':
                body = "document.startViewTransition(() => document.querySelector('p').textContent = 'Transition ran');"
            elif case == 'mention':
                body = "// startViewTransition is mentioned, not called.\nwindow.example = 'SOURCE_BODY_CANARY';"
            elif case == 'dead':
                body = "if (false) document.startViewTransition(() => {});"
            elif case == 'oversize':
                body = '/*' + 'x' * (1024 * 1024 + 1) + '*/'
            elif case == 'large':
                body = '/*' + 'x' * (900 * 1024) + '*/'
            elif case == 'missing':
                status, body = 404, '// startViewTransition in an HTTP error is not evidence'
            else:
                body = "document.body.dataset.loaded = 'yes';"
        elif parsed.path == '/redirect.js':
            mime, status, body = 'text/javascript', 302, ''
            headers['Location'] = '/bundle.js?case=call'
        else:
            mime = 'text/html'
            if case == 'cross':
                scripts = f'<script src="{self.server.other_origin}/bundle.js?case=call&token=QUERY_CANARY"></script>'
            elif case == 'redirect':
                scripts = '<script src="/redirect.js"></script>'
            elif case == 'many':
                scripts = ''.join(f'<script src="/bundle.js?case=plain&id={index}"></script>' for index in range(40))
            elif case == 'total':
                scripts = ''.join(f'<script src="/bundle.js?case=large&id={index}"></script>' for index in range(6))
            elif case == 'iframe':
                scripts = '<iframe src="/?case=call"></iframe>'
            elif case == 'csp':
                headers['Content-Security-Policy'] = "script-src 'none'"
                scripts = '<script src="/bundle.js?case=call"></script>'
            elif case == 'dynamic':
                scripts = "<script>import('/bundle.js?case=call')</script>"
            else:
                scripts = f'<script src="/bundle.js?case={case}"></script>'
            body = '<!doctype html><meta charset="utf-8"><title>External script fixture</title><p>Unchanged</p>' + scripts
        data = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionError:
            pass


class ExternalScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not chrome_binary():
            raise missing_harness('Chrome missing for external-script browser tests')
        evidence_cli()
        cls.servers = []
        cls.threads = []
        for _ in range(2):
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
            server.requests = collections.Counter()
            server.lock = threading.Lock()
            cls.servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.origin = f'http://127.0.0.1:{cls.servers[0].server_port}'
        cls.servers[0].other_origin = f'http://127.0.0.1:{cls.servers[1].server_port}'

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)

    def setUp(self):
        for server in self.servers:
            with server.lock:
                server.requests.clear()

    def collect(self, case):
        with tempfile.TemporaryDirectory() as tmp:
            expression = Path(tmp) / 'probe.js'
            # Observe the fixture DOM separately; the collector itself does NOT
            # claim to instrument runtime calls or infer usage from source text.
            expression.write_text('(() => { const report = ' + PROBE.read_text() +
                                  '; report.fixtureText = document.querySelector("p").textContent; return report; })()')
            result = subprocess.run(
                ['node', str(COLLECTOR), self.origin + '/?case=' + case,
                 '--harness', str(evidence_cli()), '--expr-file', str(expression), '--wait', '500', '--quiet'],
                capture_output=True, text=True, timeout=65,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_cross_origin_bundle_is_inspected_without_re_requesting_it(self):
        report = self.collect('cross')
        self.assertEqual(report['fixtureText'], 'Transition ran', 'the real external bundle did not execute')
        self.assertFalse(report['viewTransitions']['apiReferencedInInlineScript'])
        self.assertTrue(report['viewTransitions']['apiReferencedInExternalScript'])
        self.assertEqual(report['viewTransitions']['runtimeUsage'], 'not-measured')
        self.assertFalse(report['css']['families']['viewTransitions']['used'], 'script text is not CSS adoption')
        evidence = report['scriptInspection']
        self.assertEqual(evidence['inspected'], 1)
        self.assertFalse(evidence['partial'], evidence)
        self.assertNotIn('QUERY_CANARY', json.dumps(evidence))
        self.assertEqual(self.servers[1].requests['/bundle.js?case=call&token=QUERY_CANARY'], 1,
                         'collector re-requested the source instead of reading the captured response')

    def test_mentions_and_dead_calls_are_not_reported_as_runtime_usage(self):
        for case in ('mention', 'dead'):
            with self.subTest(case=case):
                report = self.collect(case)
                self.assertEqual(report['fixtureText'], 'Unchanged')
                self.assertTrue(report['viewTransitions']['apiReferencedInExternalScript'])
                self.assertEqual(report['viewTransitions']['runtimeUsage'], 'not-measured')
                self.assertFalse(report['css']['families']['viewTransitions']['used'])
                self.assertNotIn('SOURCE_BODY_CANARY', json.dumps(report), 'source bodies must not be retained')

    def test_plain_bundle_and_browser_support_are_not_adoption(self):
        report = self.collect('plain')
        self.assertTrue(report['viewTransitions']['apiAvailable'])
        self.assertFalse(report['viewTransitions']['apiReferencedInExternalScript'])
        self.assertFalse(report['css']['families']['viewTransitions']['used'])
        self.assertEqual(report['scriptInspection']['inspected'], 1)

    def test_dynamic_import_is_captured(self):
        report = self.collect('dynamic')
        self.assertEqual(report['fixtureText'], 'Transition ran')
        self.assertTrue(report['viewTransitions']['apiReferencedInExternalScript'])
        self.assertEqual(report['scriptInspection']['inspected'], 1)

    def test_redirect_inspects_final_body_once(self):
        report = self.collect('redirect')
        self.assertEqual(report['fixtureText'], 'Transition ran')
        self.assertEqual(report['scriptInspection']['inspected'], 1)
        self.assertTrue(report['viewTransitions']['apiReferencedInExternalScript'])
        self.assertTrue(report['scriptInspection']['sources'][0]['url'].endswith('/bundle.js'))
        self.assertEqual(self.servers[0].requests['/redirect.js'], 1)
        self.assertEqual(self.servers[0].requests['/bundle.js?case=call'], 1)

    def test_child_frame_scripts_are_outside_the_reported_scope(self):
        report = self.collect('iframe')
        self.assertEqual(report['scriptInspection']['captured'], 0)
        self.assertFalse(report['viewTransitions']['apiReferencedInExternalScript'])

    def test_http_and_csp_failures_stay_partial_not_negative_evidence(self):
        for case in ('missing', 'csp'):
            with self.subTest(case=case):
                report = self.collect(case)
                evidence = report['scriptInspection']
                self.assertTrue(evidence['partial'], evidence)
                self.assertEqual(evidence['unreadable'], 1)
                self.assertTrue(evidence['sources'][0]['reason'])
                self.assertFalse(report['viewTransitions']['apiReferencedInExternalScript'])

    def test_individual_and_total_byte_limits_are_reported(self):
        for case in ('oversize', 'total'):
            with self.subTest(case=case):
                evidence = self.collect(case)['scriptInspection']
                self.assertTrue(evidence['partial'], evidence)
                self.assertLessEqual(evidence['bytesInspected'], evidence['limits']['totalBytes'])
                for source in evidence['sources']:
                    self.assertLessEqual(source.get('bytesInspected', 0), evidence['limits']['perScriptBytes'])
                if case == 'oversize':
                    self.assertEqual(evidence['inspected'], 0)
                    self.assertEqual(evidence['sources'][0]['reason'], 'per-script-byte-limit')

    def test_both_callers_retain_script_evidence_and_refresh_css_only_cache(self):
        from scripts import audit_runner2, modern_web_probe
        from scripts.test_audit_runner_modern_web import temp_cwd
        url = self.origin + '/?case=call'
        with temp_cwd() as tmp:
            output = tmp / 'crawler'
            stale = output / modern_web_probe.slug(url) / 'modern-web-features.json'
            stale.parent.mkdir(parents=True)
            stale.write_text(json.dumps({'ok': True, 'url': url, 'css': {}}))
            first = modern_web_probe.probe(None, url, output)
            self.assertTrue(first['ok'], first)
            self.assertFalse(first.get('cached', False), 'old CSS-only evidence skipped script inspection')
            retained = json.loads(stale.read_text())
            self.assertEqual(retained['scriptInspection']['version'], 1)
            self.assertTrue(retained['viewTransitions']['apiReferencedInExternalScript'])
            second = modern_web_probe.probe(None, url, output)
            self.assertTrue(second.get('cached'), second)
            self.assertEqual(self.servers[0].requests['/bundle.js?case=call'], 1)
            report, artifact = audit_runner2.collect_modern_web('fixture.test', url)
            self.assertTrue(report['ok'], report)
            self.assertTrue(report['viewTransitions']['apiReferencedInExternalScript'])
            self.assertEqual(json.loads(Path(artifact).read_text())['scriptInspection'], report['scriptInspection'])
            self.assertEqual(self.servers[0].requests['/bundle.js?case=call'], 2)

    def test_script_count_limit_keeps_an_explicit_omission_count(self):
        evidence = self.collect('many')['scriptInspection']
        self.assertEqual(evidence['captured'], evidence['limits']['scripts'])
        self.assertEqual(evidence['omittedRequestEvents'], 8)
        self.assertTrue(evidence['partial'])


if __name__ == '__main__':
    unittest.main()
