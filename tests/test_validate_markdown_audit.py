import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_markdown_audit import validate


class AuditIntegrityTests(unittest.TestCase):
    def fixture(self, root, status='completed'):
        summary = dict(status=status, unique_documents=1, counts=dict(checked=1, pdf_pages=2), issue_codes={'TEST': 1})
        (root / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
        row = dict(document_id='doc', pdf_pages=2, text_checkable_pages=1, text_unverifiable_pages=1, issue_count=1)
        (root / 'documents.jsonl').write_text(json.dumps(row)+'\n', encoding='utf-8')
        (root / 'issues.jsonl').write_text(json.dumps(dict(document_id='doc', code='TEST'))+'\n', encoding='utf-8')

    def test_complete_report_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            self.assertTrue(validate(root)['passed'])

    def test_unfinished_and_inconsistent_reports_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'running')
            with self.assertRaises(ValueError):
                validate(root)
            self.fixture(root)
            (root / 'issues.jsonl').write_text('', encoding='utf-8')
            with self.assertRaises(ValueError):
                validate(root)
