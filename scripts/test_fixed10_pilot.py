#!/usr/bin/env python3
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_fixed10_pilot import ROOT, canonical, validate

class Fixed10ValidationTests(unittest.TestCase):
    def setUp(self):
        self.source=ROOT/'journey-pilot'
    def mutated(self, change):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'journey-pilot'
        import shutil; shutil.copytree(self.source,root)
        pilot=json.loads((root/'data/pilot.json').read_text()); change(pilot)
        (root/'data/pilot.json').write_bytes(canonical(pilot))
        return root
    def assertRejected(self, change):
        with self.assertRaises(ValueError): validate(self.mutated(change))
    def test_current_publication_passes(self): validate(self.source)
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

if __name__=='__main__': unittest.main()
