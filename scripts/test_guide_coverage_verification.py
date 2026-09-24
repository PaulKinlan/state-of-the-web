#!/usr/bin/env python3
"""Regression suite: `--check` must not claim more than it verified.

`analyze_guide_coverage.py --check` recorded fourteen keys in the document but
compared only the keys the current run happened to derive. Without `--guides-dir`
that was eight, so six package-side numbers -- the ones that needed two `npm pack`
extractions to produce, and were therefore the least likely to be re-derived by
hand -- could drift indefinitely while the command printed OK and exited 0.

Demonstrated before the fix: setting `latestGuides` to 9999 in the committed
baseline still produced `OK`, exit 0.

These tests pin three things:
  1. a corrupted package-side number fails the check;
  2. a run that cannot derive a recorded key says so instead of printing OK;
  3. `--guides-dir` and the vendored slug fixture produce the same key set, so a
     document written one way verifies the other way.

Each test works on a COPY of the document in a temporary directory: a suite that
rewrites the committed file races anything else touching it.

  python3 -m unittest scripts.test_guide_coverage_verification -v
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'analyze_guide_coverage.py'
DOC = ROOT / 'docs' / 'principles-analysis.md'
FIXTURE = ROOT / 'scripts' / 'fixtures' / 'guide-slugs.json'
BASELINE = re.compile(r'<!-- guide-coverage-baseline\s*(\{.*?\})\s*-->', re.S)


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=180)


def recorded_keys(text: str) -> dict:
    match = BASELINE.search(text)
    if not match:
        raise AssertionError('baseline block not found in the document')
    return json.loads(match.group(1))


class CopiedDocument:
    """A regenerated copy of the analysis document in a temporary directory."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / 'principles-analysis.md'
        self.path.write_text(DOC.read_text())
        result = run('--write', str(self.path))
        if result.returncode != 0:
            raise AssertionError(f'could not regenerate the copy: {result.stderr}')
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False

    def corrupt(self, old: str, new: str):
        text = self.path.read_text()
        if old not in text:
            raise AssertionError(f'pattern not present in the document: {old!r}')
        self.path.write_text(text.replace(old, new, 1))


class CheckVerifiesWhatItClaimsTest(unittest.TestCase):
    def test_clean_document_passes_and_reports_how_much_it_verified(self):
        with CopiedDocument() as doc:
            result = run('--check', str(doc.path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('OK', result.stdout)
            self.assertIn('key(s) verified', result.stdout,
                          'a passing check should say how much it actually compared')

    def test_corrupted_package_side_number_fails_the_check(self):
        """The original defect: this printed OK and exited 0."""
        with CopiedDocument() as doc:
            doc.corrupt('"latestGuides": 147', '"latestGuides": 9999')
            result = run('--check', str(doc.path))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('OUT OF DATE', result.stderr)
            self.assertIn('latestGuides', result.stderr)

    def test_corrupted_forward_reference_fails_the_check(self):
        """`referencedAbsentFromPinned` is the finding the analysis exists for."""
        with CopiedDocument() as doc:
            keys = recorded_keys(doc.path.read_text())
            self.assertIn('referencedAbsentFromPinned', keys,
                          'the forward reference must be a recorded key, not prose')
            self.assertEqual(keys['referencedAbsentFromPinned'], ['custom-button-actions'],
                             'the catalog references a guide absent from the version it pins')
            doc.corrupt('"custom-button-actions"', '"not-a-real-slug"')
            result = run('--check', str(doc.path))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('OUT OF DATE', result.stderr)

    def test_catalog_side_number_still_fails_the_check(self):
        """The behaviour that already worked must keep working."""
        with CopiedDocument() as doc:
            doc.corrupt('"checks": 58', '"checks": 57')
            result = run('--check', str(doc.path))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('checks', result.stderr)

    def test_undecidable_keys_are_reported_rather_than_skipped(self):
        """No silent OK: a key this run could not derive is not verified.

        Without the fixture the package-side keys cannot be computed. The old
        behaviour compared the remaining keys and printed OK; the recorded
        package-side numbers were then free to drift forever.
        """
        with CopiedDocument() as doc:
            result = run('--check', str(doc.path), '--no-fixture')
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('NOT FULLY VERIFIED', result.stderr)
            self.assertIn('could not be derived', result.stderr)
            for key in ('latestGuides', 'pinnedGuides', 'referencedAbsentFromPinned'):
                with self.subTest(key=key):
                    self.assertIn(key, result.stderr)
            self.assertNotIn('OK (', result.stdout)


class FixtureAndPacksAgreeTest(unittest.TestCase):
    def test_fixture_records_slug_names_only(self):
        """No guide content is vendored -- names are enough to verify counts."""
        payload = json.loads(FIXTURE.read_text())
        packs = payload['packs']
        self.assertGreaterEqual(len(packs), 2, 'need a pinned and a later pack to compare')
        for version, slugs in packs.items():
            with self.subTest(version=version):
                self.assertTrue(all(isinstance(slug, str) for slug in slugs))
                self.assertEqual(sorted(set(slugs)), sorted(slugs), 'slugs must be unique and sorted')
                self.assertTrue(all('\n' not in slug and len(slug) < 120 for slug in slugs),
                                'entries should be slugs, not guide bodies')

    def test_fixture_covers_every_slug_the_catalog_references(self):
        """The fixture is only useful if it can answer the catalog's questions."""
        catalog = json.loads((ROOT / 'principles.json').read_text())
        slug = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
        referenced = {entry
                      for principle in catalog['principles']
                      for check in principle['checks']
                      for entry in check.get('guides', [])
                      if ' ' not in entry and slug.match(entry)}
        packs = json.loads(FIXTURE.read_text())['packs']
        union = set().union(*(set(slugs) for slugs in packs.values()))
        self.assertEqual(referenced - union, set(),
                         'the catalog references slugs no recorded pack contains')

    def test_guides_dir_and_fixture_produce_the_same_keys(self):
        """A document written one way must verify the other way.

        The two paths previously disagreed -- `--guides-dir` omitted the pack
        version keys -- so `--check` could fail purely because of which input
        the caller happened to use.
        """
        from_fixture = run()
        self.assertEqual(from_fixture.returncode, 0, from_fixture.stderr)
        fixture_keys = set(json.loads(from_fixture.stdout))

        packs = json.loads(FIXTURE.read_text())['packs']
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for version, slugs in sorted(packs.items()):
                pack = Path(tmp) / version / 'package'
                guides = pack / 'skills' / 'modern-web-guidance' / 'guides'
                guides.mkdir(parents=True)
                (pack / 'package.json').write_text(
                    json.dumps({'name': 'modern-web-guidance', 'version': version}))
                for entry in slugs:
                    (guides / f'{entry}.md').write_text(f'# {entry}\n')
                dirs += ['--guides-dir', str(Path(tmp) / version)]
            from_dirs = run(*dirs)
        self.assertEqual(from_dirs.returncode, 0, from_dirs.stderr)
        self.assertEqual(set(json.loads(from_dirs.stdout)), fixture_keys,
                         'the two input paths must record the same keys')


if __name__ == '__main__':
    sys.exit(unittest.main(verbosity=2))
