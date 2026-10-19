import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import json
import types

from scripts.audit_markdown import audit_document, coverage, normalize_nested, text_without_images


class AuditTests(unittest.TestCase):
    def test_page_mismatch_and_low_coverage_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            md = root / 'doc.md'
            md.write_text('# Hello\n' + 'hello ' * 50, encoding='utf-8')
            (root / 'middle_json.json').write_text(json.dumps({'pages': [{'page_idx': 0}], 'is_full_document': False}), encoding='utf-8')
            document = MagicMock()
            document.__len__.return_value = 2
            document.__getitem__.return_value.get_textpage.return_value.get_text_range.return_value = 'missing words ' * 30
            fake = types.SimpleNamespace(PdfDocument=MagicMock(return_value=document))
            with patch.dict('sys.modules', pypdfium2=fake):
                result = audit_document(root / 'doc.pdf', md)
            codes = [p['code'] for p in result['issues']]
            self.assertIn('PAGE_INDEX_OR_COUNT_MISMATCH', codes)
            self.assertIn('PAGE_SCOPE_PARTIAL', codes)
            self.assertEqual(codes.count('PDF_TEXT_LOW_COVERAGE'), 2)
            self.assertEqual(result['text_checkable_pages'], 2)
            document.close.assert_called_once()

    def test_scanned_page_is_unverifiable_not_certified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            md = root / 'doc.md'
            md.write_text('OCR text ' * 50, encoding='utf-8')
            document = MagicMock()
            document.__len__.return_value = 1
            document.__getitem__.return_value.get_textpage.return_value.get_text_range.return_value = ''
            fake = types.SimpleNamespace(PdfDocument=MagicMock(return_value=document))
            with patch.dict('sys.modules', pypdfium2=fake):
                result = audit_document(root / 'doc.pdf', md)
            self.assertEqual(result['text_unverifiable_pages'], 1)
            self.assertIn('PAGE_STRUCTURE_UNAVAILABLE', [p['code'] for p in result['issues']])

    def test_coverage_does_not_count_image_base64(self):
        text = text_without_images('hello\n![](data:image/png;base64,QUJD)\nworld')
        self.assertEqual(text, 'hello\n\nworld')
        self.assertEqual(coverage('hello missing', {'hello'}), (2, 0.5))

    def test_normalization_preserves_assets_and_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'pdf').mkdir()
            (root / 'pdf/doc.pdf').write_bytes(b'pdf')
            old = root / 'markdown/other/doc'
            old.mkdir(parents=True)
            (old / 'doc.md').write_text('# title\n![](image.png)', encoding='utf-8')
            (old / 'image.png').write_bytes(b'image')
            output = root / 'quality_runs/test'
            output.mkdir(parents=True)
            before = hashlib.sha256((old / 'doc.md').read_bytes()).hexdigest()
            result = normalize_nested(root, 'doc', output)
            self.assertFalse(old.exists())
            self.assertEqual(result['sha256']['doc.md'], before)
            self.assertEqual((root / 'markdown/doc/image.png').read_bytes(), b'image')
            self.assertEqual((output / 'backups/doc/image.png').read_bytes(), b'image')
            self.assertEqual(normalize_nested(root, 'doc', output)['status'], 'already_normalized')

    def test_normalization_refuses_external_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'pdf').mkdir()
            (root / 'pdf/doc.pdf').touch()
            old = root / 'markdown/other/doc'
            old.mkdir(parents=True)
            (old / 'doc.md').write_text('![](../shared.png)', encoding='utf-8')
            (old.parent / 'shared.png').write_bytes(b'image')
            with self.assertRaises(ValueError):
                normalize_nested(root, 'doc', root / 'quality_runs/test')
            self.assertTrue((old / 'doc.md').exists())


if __name__ == '__main__':
    unittest.main()
