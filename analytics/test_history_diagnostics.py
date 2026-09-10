import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from analytics.models import ProcessingRun
from uploads.processors.matching import HistoryMappingError, merge
from uploads.test_new_format import plan_rows


class ArchiveIsolationTests(SimpleTestCase):
    def test_archive_cannot_add_or_override_uploaded_facts(self):
        plans = plan_rows()
        plans['Факт, шт'] = [8, 9]
        plans['Факт, CNY'] = [80, 90]
        old = pd.DataFrame([{
            '_key_client': 'absent', '_key_article': 'absent', '_key_month': '2025-01',
            'Факт, шт': 999, 'Факт, CNY': 9999,
        }])
        result = merge(plans, pd.DataFrame(), old)
        self.assertEqual(result['Факт, шт'].tolist(), [8, 9])
        self.assertEqual(result['Факт, CNY'].tolist(), [80, 90])
        self.assertEqual(len(result), len(plans))
        self.assertEqual(result.attrs['matching_report']['history_conflicts'], [])
        self.assertEqual(result.attrs['matching_report']['unmatched_rows'], 0)

    def test_old_copy_without_facts_is_not_restored_from_archive(self):
        old = pd.DataFrame([{
            '_key_client': 'client', '_key_article': 'a1', '_key_month': '2026-01',
            'Факт, шт': 999, 'Факт, CNY': 9999,
        }])
        result = merge(plan_rows(), pd.DataFrame(), old)
        self.assertEqual(result['Факт, шт'].sum(), 0)
        self.assertEqual(result['Факт, CNY'].sum(), 0)


def conflict():
    return {'client': '=Client', 'article': 'A1', 'period': '2026-01', 'quantity': 7, 'amount': 70,
            'product_class': '', 'production_index': '', 'reason': 'Подходят несколько строк плана',
            'hint': 'Уточните индекс', 'matching_count': 2, 'available_periods': ['2026-01'],
            'candidates': [{'manager': 'Manager', 'product_class': 'Class', 'production_index': 'Index'}]}


class DiagnosticPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('diagnostic_admin', is_superuser=True)
        self.client.force_login(self.user)

    def test_failed_run_saves_full_details_and_exposes_link_and_csv(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            with patch('analytics.views.get_missing_files', return_value=([], True, 10)), patch(
                'analytics.views.create_full_snapshot', side_effect=HistoryMappingError([conflict() for _ in range(51)])
            ):
                response = self.client.post(reverse('run_etl_api'))
            self.assertEqual(response.status_code, 422)
            run = ProcessingRun.objects.latest('pk')
            self.assertEqual(run.status, 'failed')
            self.assertFalse(run.final_file)
            self.assertTrue((Path(folder) / run.unmatched_file).is_file())
            detail = self.client.get(response.json()['history_url'])
            self.assertContains(detail, 'Что мешает переносу истории: 51')
            self.assertEqual(len(detail.context['conflict_page']), 50)
            second = self.client.get(response.json()['history_url'] + '?page=2')
            self.assertEqual(len(second.context['conflict_page']), 1)
            download = self.client.get(reverse('download_processing_file', args=[run.pk, 'unmatched']))
            self.assertEqual(download.status_code, 200)
            self.assertIn("'=Client", download.content.decode('utf-8-sig'))
            manager = User.objects.create_user('diagnostic_manager')
            self.client.force_login(manager)
            self.assertEqual(self.client.get(response.json()['history_url']).status_code, 403)
            self.assertEqual(self.client.get(reverse('download_processing_file', args=[run.pk, 'unmatched'])).status_code, 403)

    def test_deviations_are_separate_and_keep_multiselect_query(self):
        from accounts.test_dashboard import frame
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'final.xlsx'
            frame().to_excel(path, index=False)
            with patch('accounts.views.get_latest_final_file', return_value=path):
                query = '?manager=A&manager=B&period=2026-01'
                dashboard = self.client.get(reverse('dashboard') + query)
                self.assertContains(dashboard, 'Посмотреть отклонения')
                self.assertNotContains(dashboard, 'Отклонения от плана:')
                self.assertIsNone(dashboard.context['anomalies'])
                detail = self.client.get(reverse('sales_deviations') + query)
                self.assertContains(detail, 'Отклонения от плана: 2')
                self.assertNotContains(detail, '<canvas')
                self.assertEqual(detail.context['selected']['manager'], ['A', 'B'])
                self.assertIn('manager=A&manager=B', detail.context['filter_query'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('sales_deviations')).status_code, 302)

    def test_deviations_pagination_does_not_drop_later_rows(self):
        from accounts.test_dashboard import frame
        row = frame().iloc[0].to_dict()
        records = [{**row, 'Артикул': f'P{i}', 'AOP, CNY': 100, 'Факт, CNY': 0} for i in range(61)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'final.xlsx'
            pd.DataFrame(records).to_excel(path, index=False)
            with patch('accounts.views.get_latest_final_file', return_value=path):
                response = self.client.get(reverse('sales_deviations') + '?period=2026-01&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['anomalies']['count'], 61)
        self.assertEqual(len(response.context['anomalies']['rows']), 11)
        self.assertContains(response, 'period=2026-01&amp;page=1')
