import tempfile
from pathlib import Path
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from accounts.models import SalesManager, Profile
from accounts.manager_registry import active_source
from analytics.models import ProcessingRun


class ManagerDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('deletion_admin', is_superuser=True)
        self.user = User.objects.create_user('delete_test', password='Test-Only-Pass-4488!')
        self.manager = SalesManager.objects.create(
            key='deletiontest', name='Тестовый менеджер', user=self.user, legacy_username=self.user.username)
        self.url = reverse('manager_delete', args=[self.manager.pk])
        self.client.force_login(self.admin)

    def test_get_and_incorrect_confirmation_do_not_delete(self):
        self.assertContains(self.client.get(self.url), 'Удалить окончательно')
        for data in [{}, {'confirmation': 'wrong'}]:
            self.assertEqual(self.client.post(self.url, data).status_code, 200)
            self.assertTrue(SalesManager.objects.filter(pk=self.manager.pk).exists())
            self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_confirmed_delete_removes_account_but_preserves_files_and_history(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            source = Path(folder) / 'data' / 'raw' / 'plan_deletiontest_example.xlsx'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'original')
            archive = Path(folder) / 'archive.xlsx'
            archive.write_bytes(b'archived')
            run = ProcessingRun.objects.create(user=self.user, status='success', final_file=str(archive),
                                               manager_reports={self.manager.key: str(archive)})
            session = Client()
            session.force_login(self.user)
            response = self.client.post(self.url, {'confirmation': self.user.username})
            self.assertRedirects(response, reverse('manage_managers'))
            self.assertFalse(SalesManager.objects.filter(pk=self.manager.pk).exists())
            self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
            self.assertFalse(Profile.objects.filter(user_id=self.user.pk).exists())
            self.assertEqual(session.get(reverse('profile')).status_code, 302)
            self.assertEqual(source.read_bytes(), b'original')
            self.assertEqual(archive.read_bytes(), b'archived')
            run.refresh_from_db()
            self.assertIsNone(run.user_id)
            self.assertEqual(run.manager_reports, {self.manager.key: str(archive)})
            self.assertFalse(active_source(source.name))
            replacement = User.objects.create_user('delete_test')
            self.assertNotEqual(replacement.pk, self.user.pk)
            self.assertEqual(self.client.post(self.url, {'confirmation': 'delete_test'}).status_code, 404)

    def test_only_superuser_can_delete_and_csrf_is_required(self):
        for role in ['manager', 'analyst', 'director']:
            self.user.profile.role = role
            self.user.profile.save()
            self.client.force_login(self.user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, {'confirmation': self.user.username}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.admin)
        self.assertEqual(secure.post(self.url, {'confirmation': self.user.username}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_privileged_accounts_cannot_be_deleted(self):
        for field in ['is_staff', 'is_superuser']:
            setattr(self.user, field, True)
            self.user.save()
            self.assertEqual(self.client.post(self.url, {'confirmation': self.user.username}).status_code, 403)
            setattr(self.user, field, False)
        self.user.save()
        self.user.profile.role = 'analyst'
        self.user.profile.save()
        self.assertEqual(self.client.post(self.url, {'confirmation': self.user.username}).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_inactive_and_unlinked_records_can_be_deleted(self):
        self.manager.is_active = False
        self.manager.save()
        self.assertEqual(self.client.post(self.url, {'confirmation': self.user.username}).status_code, 302)
        unlinked = SalesManager.objects.create(key='unlinkedtest', name='Без аккаунта')
        response = self.client.post(reverse('manager_delete', args=[unlinked.pk]), {'confirmation': unlinked.name})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SalesManager.objects.filter(pk=unlinked.pk).exists())
