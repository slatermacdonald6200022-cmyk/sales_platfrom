from django.db import migrations


def bind_legacy(apps, schema_editor):
    Manager = apps.get_model('accounts', 'SalesManager')
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('accounts', 'Profile')
    for manager in Manager.objects.filter(user__isnull=True).exclude(legacy_username=''):
        names = [manager.legacy_username]
        if manager.key == 'prasolov':
            names.append('prasolov_soloviev')
        user = User.objects.filter(username__in=names, is_superuser=False, is_staff=False).first()
        if not user or Manager.objects.filter(user_id=user.pk).exists():
            continue
        profile, _ = Profile.objects.get_or_create(user_id=user.pk, defaults={
            'role': 'manager', 'manager_name': manager.name,
        })
        if profile.role != 'manager':
            continue
        manager.user_id = user.pk
        manager.save(update_fields=['user'])


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_salesmanager')]
    operations = [migrations.RunPython(bind_legacy, migrations.RunPython.noop)]
