import os
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.core.management import call_command
from .file_metadata import read_metadata, save_metadata
from .views import get_instant_file_info
from .tests import valid_manager_upload


class FileMetadataTests(TestCase):
    def test_persists_after_cache_clear_without_reading_excel(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan_redko_example.xlsx'
            path.write_bytes(b'source')
            data = {'rows': 120, 'cols': 80, 'sheets_count': 2,
                    'sheet_names': '2026, 2027', 'periods': '01.2026–12.2027 (24 мес.)'}
            save_metadata(path, data)
            cache.clear()
            with patch('openpyxl.load_workbook', side_effect=AssertionError('Excel must not be opened')):
                info = get_instant_file_info(path)
            self.assertTrue(info['has_metadata'])
            self.assertEqual(info['rows'], 120)
            self.assertEqual(info['sheet_names'], '2026, 2027')
            self.assertEqual(path.read_bytes(), b'source')

    def test_changed_file_and_same_name_in_another_folder_do_not_reuse_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / 'a' / 'same.xlsx'
            second = Path(folder) / 'b' / 'same.xlsx'
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b'old')
            second.write_bytes(b'old')
            save_metadata(first, {'rows': 10})
            self.assertEqual(read_metadata(second), {})
            stamp = first.stat().st_mtime_ns + 1_000_000_000
            first.write_bytes(b'new')
            os.utime(first, ns=(stamp, stamp))
            self.assertEqual(read_metadata(first), {})
            self.assertFalse(get_instant_file_info(first)['has_metadata'])
            self.assertEqual(get_instant_file_info(first)['sheet_names'], '—')

    def test_backfill_is_repeatable_and_keeps_original_file(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            raw = Path(folder) / 'data' / 'raw'
            raw.mkdir(parents=True)
            path = raw / 'plan_redko_example.xlsx'
            path.write_bytes(valid_manager_upload().read())
            original = path.read_bytes()
            call_command('refresh_file_metadata', stdout=StringIO())
            cache.clear()
            self.assertTrue(get_instant_file_info(path)['has_metadata'])
            with patch('uploads.management.commands.refresh_file_metadata.validate_manager_file',
                       side_effect=AssertionError('Must not revalidate unchanged file')):
                call_command('refresh_file_metadata', stdout=StringIO())
            self.assertEqual(path.read_bytes(), original)
