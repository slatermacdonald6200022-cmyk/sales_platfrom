import json
import os
import tempfile
from pathlib import Path
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from .models import ProcessingRun
from .current_results import current_run
from .views import get_user_manager_reports
from uploads.input_state import input_state
from accounts.models import SalesManager


class CurrentResultsTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.settings = override_settings(BASE_DIR=self.base)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.raw = self.base / 'data' / 'raw'
        self.raw.mkdir(parents=True)
        self.plan = self.raw / 'plan_redko_example.xlsx'
        self.plan.write_bytes(b'plan')
        self.actual = self.raw / 'fact_1c_august.xlsx'
        self.actual.write_bytes(b'actual')
        self.final = self.base / 'data' / 'processed' / 'final' / 'run' / 'FINAL.xlsx'
        self.final.parent.mkdir(parents=True)
        self.final.write_bytes(b'final')
        self.report = self.final.with_name('manager.xlsx')
        self.report.write_bytes(b'manager')
        self.final.with_name('COMBINED_MANAGER_FACTS.xlsx').write_bytes(b'combined')
        self.run = ProcessingRun.objects.create(status='success', final_file=str(self.final),
                                               manager_reports={'redko': str(self.report)})
        self.admin = User.objects.create_user('batch_admin', is_superuser=True)
        self.client.force_login(self.admin)

    def record_batch(self):
        self.final.with_name('input_state.json').write_text(json.dumps(input_state(self.raw)), encoding='utf-8')

    def assert_current_unavailable(self):
        self.assertIsNone(current_run())
        self.assertEqual(get_user_manager_reports(self.admin), [])
        for url in [reverse('download_final_excel'), reverse('download_combined_excel'),
                    reverse('download_manager_report', args=['redko'])]:
            self.assertEqual(self.client.get(url).status_code, 404)
        archive = self.client.get(reverse('download_processing_file', args=[self.run.pk, 'final']))
        self.assertEqual(archive.status_code, 200)
        archive.close()

    def test_legacy_outputs_hidden_until_current_batch_processed(self):
        self.assert_current_unavailable()
        self.record_batch()
        self.assertEqual(current_run().pk, self.run.pk)
        self.assertEqual(len(get_user_manager_reports(self.admin)), 1)
        for url in [reverse('download_final_excel'), reverse('download_combined_excel'),
                    reverse('download_manager_report', args=['redko'])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_actual_only_change_invalidates_all_outputs_and_keeps_archive(self):
        self.record_batch()
        self.actual.write_bytes(b'new actual')
        self.assert_current_unavailable()
        self.record_batch()
        self.assertIsNotNone(current_run())
        self.actual.unlink()
        self.assert_current_unavailable()

    def test_same_content_reupload_or_plan_change_requires_processing(self):
        self.record_batch()
        stamp = self.plan.stat().st_mtime_ns + 1_000_000_000
        os.utime(self.plan, ns=(stamp, stamp))
        self.assert_current_unavailable()
        self.record_batch()
        self.plan.write_bytes(b'changed plan')
        self.assert_current_unavailable()

    def test_failed_run_or_roster_edit_does_not_expose_previous_result(self):
        self.record_batch()
        SalesManager.objects.filter(key='redko').update(name='Changed')
        self.assert_current_unavailable()
        self.record_batch()
        self.assertIsNotNone(current_run())
        ProcessingRun.objects.create(status='failed')
        self.assert_current_unavailable()
