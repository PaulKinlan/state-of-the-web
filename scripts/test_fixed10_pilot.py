#!/usr/bin/env python3
import hashlib, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_fixed10_pilot import safe_private_text
from validate_fixed10_pilot import ROOT, canonical, private_narrative_safe, validate

ADVERSARIAL_PRIVATE_VALUES=[
    '/var/lib/web-uplift/private/site/report-full.json',
    r'C:\\Users\\paul\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 1',
    'Set-Cookie: sid=private; HttpOnly',
    'Authorization: Basic dXNlcjpwYXNz',
    'request body: user@example.org',
    'www.example.org/private/account?debug',
    'access_token=generic-value',
    'browser profile id: Profile-42',
    'artifact report-full.json retained',
    '/gb/',
    'cookie name: nfvdid',
    'cookie identifier OptanonConsent',
    'X-Frame-Options: DENY',
    'response body=private payload',
    'cookie value: private-cookie-value',
    'header value: private-header-value',
    'body: private payload',
    'token value: private-token-value',
    'profile id: Profile-42',
    'artifact name: audit-output.json',
]

class Fixed10ValidationTests(unittest.TestCase):
    def setUp(self):
        self.source=ROOT/'journey-pilot'
    def mutated(self, change):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'journey-pilot'
        import shutil; shutil.copytree(self.source,root)
        pilot=json.loads((root/'data/pilot.json').read_text()); change(pilot)
        (root/'data/pilot.json').write_bytes(canonical(pilot))
        self.refresh_manifest(root,'data/pilot.json')
        return root
    def refresh_manifest(self, root, relative):
        path=root/relative
        manifest_path=root/'data/public-manifest.json'
        manifest=json.loads(manifest_path.read_text())
        item=next(item for item in manifest['files'] if item['path']==relative)
        item['bytes']=path.stat().st_size
        item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_bytes(canonical(manifest))
    def mutated_checks(self, change):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'journey-pilot'
        import shutil; shutil.copytree(self.source,root)
        path=root/'data/check-outcomes.json'; data=json.loads(path.read_text()); change(data)
        path.write_bytes(canonical(data)); self.refresh_manifest(root,'data/check-outcomes.json')
        return root
    def mutated_public_text(self, relative, value):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'journey-pilot'
        import shutil; shutil.copytree(self.source,root)
        path=root/relative
        path.write_text(path.read_text()+f'\n<!-- {value} -->\n')
        self.refresh_manifest(root,relative)
        return root
    def assertRejected(self, change):
        with self.assertRaises(ValueError): validate(self.mutated(change))
    def assertChecksRejected(self, change):
        with self.assertRaises(ValueError): validate(self.mutated_checks(change))
    def test_current_publication_passes(self):
        result=validate(self.source)
        self.assertEqual(result['atomicSlots'],580)
    def test_unknown_field_rejected(self): self.assertRejected(lambda p:p['rows'][0].__setitem__('extra','no'))
    def test_score_field_rejected(self): self.assertRejected(lambda p:p['rows'][0].__setitem__('score',100))
    def test_denominator_drift_rejected(self): self.assertRejected(lambda p:p['cohort'].__setitem__('denominator',9))
    def test_non_origin_url_rejected(self): self.assertRejected(lambda p:p['rows'][0].__setitem__('origin','https://github.com/path?q=secret'))
    def test_local_path_rejected(self): self.assertRejected(lambda p:p['publication'].__setitem__('authorization','/home/example/private'))
    def test_invalid_media_receipt_rejected(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'journey-pilot'; import shutil; shutil.copytree(self.source,root)
        path=root/'data/media-manifest.json'; data=json.loads(path.read_text()); data['receipts'][0]['ocrReview']='pending'; path.write_bytes(canonical(data))
        with self.assertRaises(ValueError): validate(root)
    def test_atomic_totals_drift_rejected(self): self.assertChecksRejected(lambda d:d['totals'].__setitem__('issues',146))
    def test_atomic_slot_removed_rejected(self): self.assertChecksRejected(lambda d:d['sites'][0]['outcomes'].pop())
    def test_atomic_duplicate_pair_rejected(self):
        def change(d): d['sites'][2]['outcomes'][1].update({'principleId':d['sites'][2]['outcomes'][0]['principleId'],'checkId':d['sites'][2]['outcomes'][0]['checkId']})
        self.assertChecksRejected(change)
    def test_issue_without_finding_rejected(self):
        def change(d): next(o for s in d['sites'] for o in s['outcomes'] if o['displayStatus']=='issues')['findings']=[]
        self.assertChecksRejected(change)
    def test_incomplete_without_reason_rejected(self):
        def change(d): next(o for s in d['sites'] for o in s['outcomes'] if o['displayStatus']=='blocked')['reason']=None
        self.assertChecksRejected(change)
    def test_unavailable_claimed_as_tested_rejected(self):
        def change(d): d['sites'][0]['outcomes'][0].update({'displayStatus':'pass','sourceStatus':'pass','confidence':'high'})
        self.assertChecksRejected(change)
    def test_method_invalid_warning_removed_rejected(self):
        def change(d): next(o for o in d['sites'][2]['outcomes'] if o['checkId']=='no-console-errors')['methodInvalid']=False
        self.assertChecksRejected(change)
    def test_private_path_in_atomic_text_rejected(self):
        def change(d): d['sites'][2]['outcomes'][0]['evidence']['summary']='/home/private/report.json'
        self.assertChecksRejected(change)
    def test_generator_sanitizes_adversarial_private_narratives(self):
        for value in ADVERSARIAL_PRIVATE_VALUES:
            with self.subTest(value=value):
                sanitized=safe_private_text(value,'adversarial fixture')
                self.assertTrue(private_narrative_safe(sanitized),sanitized)
    def test_adversarial_private_narratives_rejected_after_manifest_rehash(self):
        for value in ADVERSARIAL_PRIVATE_VALUES:
            with self.subTest(value=value):
                def change(d, value=value): d['sites'][2]['outcomes'][0]['evidence']['summary']=value
                self.assertChecksRejected(change)
    def test_sensitive_labels_rejected_in_all_public_json_and_html(self):
        values=[
            'Set-Cookie: sid=private',
            'Authorization: Basic dXNlcjpwYXNz',
            'cookie value: private-cookie-value',
            'header value: private-header-value',
            'body: private payload',
            'token value: private-token-value',
            'profile id: Profile-42',
            'artifact name: audit-output.json',
            'user@example.org',
        ]
        for value in values:
            with self.subTest(surface='pilot.json',value=value):
                self.assertRejected(lambda p,value=value:p['rows'][0].__setitem__('reasonSummary',value))
            with self.subTest(surface='index.html',value=value):
                with self.assertRaises(ValueError): validate(self.mutated_public_text('index.html',value))
    def test_invented_catalog_pair_on_all_sites_rejected_after_manifest_rehash(self):
        def change(d):
            for site in d['sites']:
                site['outcomes'][0]['principleId']='invented-principle'
                site['outcomes'][0]['checkId']='invented-check'
        self.assertChecksRejected(change)

if __name__=='__main__': unittest.main()
