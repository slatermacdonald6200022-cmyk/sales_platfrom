import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from django.test import SimpleTestCase
from .report_cache import read_report, clear_report_cache, _frames


class ReportCacheTests(SimpleTestCase):
    def setUp(self):
        clear_report_cache()

    def tearDown(self):
        clear_report_cache()

    def test_repeat_reads_are_cached_and_callers_cannot_modify_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.xlsx'
            path.touch()
            with patch('accounts.report_cache.pd.read_excel', return_value=pd.DataFrame({'value': [1]})) as read:
                first = read_report(path)
                first.loc[0, 'value'] = 999
                self.assertEqual(read_report(path).loc[0, 'value'], 1)
                self.assertEqual(read.call_count, 1)

    def test_same_size_edit_and_new_path_reload_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.xlsx'
            path.write_bytes(b'old')
            with patch('accounts.report_cache.pd.read_excel', side_effect=[pd.DataFrame({'v': [1]}), pd.DataFrame({'v': [2]}), pd.DataFrame({'v': [3]})]) as read:
                self.assertEqual(read_report(path).loc[0, 'v'], 1)
                stamp = path.stat().st_mtime_ns + 1_000_000_000
                path.write_bytes(b'new')
                os.utime(path, ns=(stamp, stamp))
                self.assertEqual(read_report(path).loc[0, 'v'], 2)
                other = Path(folder) / 'new.xlsx'
                other.touch()
                self.assertEqual(read_report(other).loc[0, 'v'], 3)
                self.assertEqual(read.call_count, 3)
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    read_report(path)

    def test_change_during_read_is_retried_and_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.xlsx'
            path.touch()
            calls = []
            def changing_read(source):
                calls.append(source)
                if len(calls) == 1:
                    source.write_bytes(b'changed')
                return pd.DataFrame({'v': [len(calls)]})
            with patch('accounts.report_cache.pd.read_excel', side_effect=changing_read):
                self.assertEqual(read_report(path).loc[0, 'v'], 2)
                for i in range(3):
                    other = Path(folder) / f'{i}.xlsx'
                    other.touch()
                    read_report(other)
                self.assertEqual(len(_frames), 2)
