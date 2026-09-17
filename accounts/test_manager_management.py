import tempfile
from pathlib import Path
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .models import SalesManager
from .manager_registry import managers, active_source
from .departments import department_for_manager
from uploads.views import get_user_manager, scan_raw_directory
from uploads.processors.normalize_manager import extract_manager_from_filename, normalize_all_managers
from uploads.test_new_format import source_book


class ManagerManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('registry_root', is_superuser=True)
        self.client.force_login(self.admin)

    def create_manager(self):
        response = self.client.post(reverse('manager_add'), {
            'name': 'Новый сотрудник', 'department': 'Новый отдел', 'username': 'new_manager',
            'email': 'manager@example.test', 'password': 'Unique-Manager-2907!',
        })
        self.assertEqual(response.status_code, 302)
        return SalesManager.objects.get(legacy_username='new_manager')

    def test_create_edit_disable_reenable_preserves_identity_and_files(self):
        manager = self.create_manager()
        self.assertEqual(len(managers()), 11)
        self.assertEqual(get_user_manager(manager.user)['id'], manager.key)
        self.assertFalse(manager.user.is_staff)
        self.assertFalse(manager.user.is_superuser)
        self.assertEqual(manager.user.profile.role, 'manager')
        key = manager.key
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            raw = Path(folder) / 'data' / 'raw'
            raw.mkdir(parents=True)
            source = raw / f'plan_{key}_С_фактом_1c_source.xlsx'
            source_book(source)
            original = source.read_bytes()
            self.assertIn(key, scan_raw_directory(raw)[0])
            self.assertEqual(extract_manager_from_filename(source.name), 'Новый сотрудник')
            self.client.post(reverse('manager_edit', args=[manager.pk]), {
                'name': 'Переименованный сотрудник', 'department': 'Другой отдел', 'username': 'new_login',
                'email': '', 'password': '',
            })
            manager.refresh_from_db()
            self.assertEqual(manager.key, key)
            self.assertEqual(department_for_manager('Новый сотрудник'), 'Другой отдел')
            self.assertEqual(extract_manager_from_filename(source.name), 'Переименованный сотрудник')
            self.assertEqual(source.read_bytes(), original)
            logged_in = Client()
            self.assertTrue(logged_in.login(username='new_login', password='Unique-Manager-2907!'))
            self.client.post(reverse('manager_toggle', args=[manager.pk]), {'action': 'disable'})
            self.assertEqual(len(managers()), 10)
            self.assertFalse(active_source(source.name))
            self.assertNotIn(key, scan_raw_directory(raw)[0])
            self.assertTrue(normalize_all_managers(str(raw)).empty)
            self.assertTrue(source.exists())
            self.assertEqual(logged_in.get(reverse('profile')).status_code, 302)
            self.client.post(reverse('manager_toggle', args=[manager.pk]), {'action': 'enable'})
            self.assertTrue(active_source(source.name))
            self.assertIn(key, scan_raw_directory(raw)[0])
            self.assertEqual(len(managers()), 11)

    def test_non_superusers_cannot_manage_and_get_cannot_disable(self):
        manager = self.create_manager()
        self.assertEqual(self.client.get(reverse('manager_toggle', args=[manager.pk])).status_code, 405)
        for role in ['manager', 'analyst', 'director']:
            user = User.objects.create_user('role_' + role)
            user.profile.role = role
            user.profile.save()
            self.client.force_login(user)
            for url in [reverse('manage_managers'), reverse('manager_add'), reverse('manager_edit', args=[manager.pk])]:
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)
            self.assertEqual(self.client.post(reverse('manager_toggle', args=[manager.pk]), {'action': 'disable'}).status_code, 403)
            self.assertNotContains(self.client.get(reverse('profile')), 'Управление менеджерами')

    def test_duplicate_login_and_csrf_rejected(self):
        self.create_manager()
        count = SalesManager.objects.count()
        response = self.client.post(reverse('manager_add'), {
            'name': 'Another', 'department': '', 'username': 'NEW_MANAGER',
            'email': '', 'password': 'Unique-Manager-2907!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'логин уже занят')
        self.assertEqual(SalesManager.objects.count(), count)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.admin)
        self.assertEqual(protected.post(reverse('manager_add'), {}).status_code, 403)

    def test_disable_legacy_manager_reduces_required_files_to_nine(self):
        from analytics import views
        from unittest.mock import patch
        manager = SalesManager.objects.get(key='redko')
        self.client.post(reverse('manager_toggle', args=[manager.pk]), {'action': 'disable'})
        with patch('analytics.views.scan_raw_directory', return_value=({}, None)):
            missing, _, _ = views.get_missing_files()
        self.assertEqual(len(missing), 9)
        self.assertNotIn(manager.name, missing)
