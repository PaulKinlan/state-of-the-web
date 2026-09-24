#!/usr/bin/env python3
"""Suite for the catalog guide-coverage analysis and its document.

The analysis in `docs/principles-analysis.md` is only useful if it cannot drift
from `principles.json`, and only if its verifier can actually fail. Both are
asserted here, against the real catalog and the real document:

  python3 -m unittest scripts.test_analyze_guide_coverage -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'analyze_guide_coverage.py'
DOC = ROOT / 'docs' / 'principles-analysis.md'


def run(*args, **kwargs):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 120), **kwargs)


class GuideCoverageAnalysisTest(unittest.TestCase):
    def test_document_exists_where_the_catalog_points(self):
        catalog = json.loads((ROOT / 'principles.json').read_text())
        declared = catalog['appliesAnalysis']
        self.assertEqual(declared, 'docs/principles-analysis.md')
        self.assertTrue((ROOT / declared).exists(), 'the catalog applies an analysis document that does not exist')

    def test_check_passes_against_the_committed_document(self):
        result = run('--check', str(DOC))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('OK', result.stdout)

    def test_check_fails_when_the_catalog_changes(self):
        """A verifier that cannot fail is decoration."""
        catalog = json.loads((ROOT / 'principles.json').read_text())
        for principle in catalog['principles']:
            for check in principle['checks']:
                if check['id'] == 'scroll-state-aware-chrome':
                    check['guides'] = check['guides'] + ['state-aware-sticky-headers']
        with tempfile.TemporaryDirectory() as tmp:
            variant = Path(tmp) / 'catalog-guides-only.json'
            variant.write_text(json.dumps(catalog, indent=2))
            result = run('--catalog', str(variant), '--check', str(DOC))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('OUT OF DATE', result.stderr)
        self.assertIn('referencedSlugs', result.stderr)

    def test_reported_counts_match_the_catalog(self):
        result = run()
        data = json.loads(result.stdout)
        catalog = json.loads((ROOT / 'principles.json').read_text())
        checks = [check for principle in catalog['principles'] for check in principle['checks']]
        self.assertEqual(data['checks'], len(checks))
        self.assertEqual(data['principles'], len(catalog['principles']))
        slugs = {entry for check in checks for entry in check.get('guides', []) if ' ' not in entry}
        phrases = [entry for check in checks for entry in check.get('guides', []) if ' ' in entry]
        self.assertEqual(data['referencedSlugs'], len(slugs))
        self.assertEqual(data['searchPhrases'], len(phrases))
        self.assertTrue(data['catalogChecksum'].startswith('sha256:78ccfdb'), data['catalogChecksum'])

    def test_document_records_the_generated_baseline(self):
        text = DOC.read_text()
        self.assertIn('<!-- BEGIN GENERATED guide-coverage -->', text)
        self.assertIn('<!-- guide-coverage-baseline', text)
        self.assertIn('## Changing the catalog', text)


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
