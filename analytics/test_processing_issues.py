import json
import tempfile
from pathlib import Path
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from .models import ProcessingRun


class ProcessingIssuesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('issues_admin', is_superuser=True)
        self.client.force_login(self.user)

    def test_saved_evidence_filters_pagination_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            final = Path(folder) / 'data' / 'processed' / 'final' / 'run' / 'FINAL.xlsx'
            final.parent.mkdir(parents=True)
            final.touch()
            item = {'client': '<script>alert(1)</script>', 'article': 'A', 'product_code': 'OLD',
                    'period': '2026-08', 'quantity': 7, 'reason': 'Коды отличаются', 'manager': 'Manager',
                    'candidates': [{'product_code': 'NEW', 'price': 12, 'forecast_quantity': 4,
                                    'source_file': 'manager.xlsx', 'source_sheet': 'План', 'source_row': 18}]}
            final.with_name('matching_diagnostics.json').write_text(
                json.dumps({'version': 1, 'items': [item] * 21}), encoding='utf-8')
            run = ProcessingRun.objects.create(status='success', final_file=str(final))
            url = reverse('processing_issues', args=[run.pk])
            response = self.client.get(url, {'q': 'Manager', 'page': 2})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context['issue_page']), 1)
            self.assertContains(response, 'NEW')
            self.assertContains(response, 'manager.xlsx')
            self.assertNotContains(response, '<script>alert(1)</script>')
            self.assertContains(response, 'q=Manager')
            self.assertContains(self.client.get(reverse('processing_run_detail', args=[run.pk])), url)
            other = User.objects.create_user('issues_other')
            self.client.force_login(other)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_legacy_run_explains_missing_details(self):
        run = ProcessingRun.objects.create(status='success')
        response = self.client.get(reverse('processing_issues', args=[run.pk]))
        self.assertContains(response, 'подробные варианты строк не сохранялись')
