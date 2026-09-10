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


class ConflictCollectionTests(SimpleTestCase):
    def test_collects_every_conflict_with_distinct_reasons(self):
        plans = plan_rows().iloc[[0]].copy()
        other = plans.copy()
        other['Производственный индекс'] = 'Other'
        plans = pd.concat([plans, other], ignore_index=True)
        plans['_Исходный файл'] = 'manager.xlsx'
        plans['_Исходный лист'] = 'План'
        plans['_Исходная строка'] = [6, 7]
        def history(client='client', article='a1', period='2026-01', **extra):
            return {'_key_client': client, '_key_article': article, '_key_month': period,
                    'Факт, шт': 7, 'Факт, CNY': 70, **extra}
        old = pd.DataFrame([history(), history(client='absent'), history(article='absent'),
                            history(period='2025-01'), history(**{'Класс товара': 'Wrong'})])
        result = merge(plans, pd.DataFrame(), old)
        rows = result.attrs['matching_report']['history_conflicts']
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]['matching_count'], 2)
        self.assertEqual(len(rows[0]['candidates']), 2)
        self.assertEqual(rows[0]['candidates'][0]['source_row'], 6)
        self.assertEqual(rows[0]['candidates'][0]['source_file'], 'manager.xlsx')
        self.assertIn('клиент отсутствует', rows[1]['reason'].lower())
        self.assertIn('артикул отсутствует', rows[2]['reason'].lower())
        self.assertIn('нет строки за этот месяц', rows[3]['reason'])
        self.assertIn('Не совпали', rows[4]['reason'])
        self.assertEqual(rows[0]['quantity'], 7)
        self.assertNotIn('Факт, шт', plans.columns)
        self.assertEqual(result.attrs['matching_report']['retained_history_rows'], 5)
        self.assertEqual(result['Факт, шт'].sum(), 35)
        self.assertEqual(result['Факт, CNY'].sum(), 350)

    def test_retained_history_is_not_duplicated_on_next_run(self):
        plans = plan_rows().iloc[[0]].copy()
        duplicate = plans.copy()
        duplicate['Производственный индекс'] = 'Other'
        plans = pd.concat([plans, duplicate], ignore_index=True)
        old = pd.DataFrame([{
            '_key_client': 'client', '_key_article': 'a1', '_key_month': '2026-01',
            'Клиент': 'Client', 'Артикул': 'A1', 'Менеджер': 'Редько Вадим',
            'Факт, шт': 7, 'Факт, CNY': 70,
        }])
        first = merge(plans, pd.DataFrame(), old)
        retained = first[first['Факт, шт'] != 0].copy()
        retained['_key_client'] = retained['Клиент'].str.lower()
        retained['_key_article'] = retained['Артикул'].str.lower()
        retained['_key_month'] = '2026-01'
        second = merge(plans, pd.DataFrame(), retained)
        self.assertEqual(first['Факт, шт'].sum(), 7)
        self.assertEqual(second['Факт, шт'].sum(), 7)
        self.assertEqual(second.attrs['matching_report']['retained_history_rows'], 1)


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
