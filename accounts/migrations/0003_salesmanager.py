from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


SEED = [
    ('tsarev', 'Царев Михаил', 'Truck&BUS', ['царев']),
    ('khusnutdinov', 'Хуснутдинов А.', 'Truck&BUS', ['хуснутдинов']),
    ('redko', 'Редько Вадим', 'Truck&BUS', ['редько']),
    ('izmaylov', 'Измайлов', 'Trailers', ['измайлов']),
    ('mustafin', 'Мустафин Ринат', 'Trailers', ['мустафин']),
    ('polyakov', 'Поляков Андрей', 'Truck&BUS', ['поляков']),
    ('prasolov', 'Прасолов Николай, Соловьёв Виктор', 'Trailers', ['прасолов', 'соловьев']),
    ('fomichev', 'Фомичев Владимир', 'Truck&BUS', ['фомичев']),
    ('khoroshevsky', 'Александр Хорошевский', 'Trailers', ['хорошевский']),
    ('ushakov', 'Ушаков Алексей', 'Aftermarket', ['ушаков']),
]


def seed(apps, schema_editor):
    Manager = apps.get_model('accounts', 'SalesManager')
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('accounts', 'Profile')
    for key, name, department, aliases in SEED:
        user = User.objects.filter(username=key, is_superuser=False, is_staff=False).first()
        if user is None and key == 'prasolov':
            user = User.objects.filter(username='prasolov_soloviev', is_superuser=False, is_staff=False).first()
        if user and not Profile.objects.filter(user_id=user.pk, role='manager').exists():
            user = None
        Manager.objects.create(key=key, name=name, department=department, aliases=aliases + [name],
                               legacy_username=key, user_id=user.pk if user else None)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_profile_delete_userprofile'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='SalesManager', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.SlugField(max_length=64, unique=True, editable=False)),
            ('name', models.CharField(max_length=255, verbose_name='Имя менеджера')),
            ('department', models.CharField(max_length=120, blank=True, verbose_name='Отдел')),
            ('legacy_username', models.CharField(max_length=150, blank=True)),
            ('aliases', models.JSONField(default=list, blank=True)),
            ('is_active', models.BooleanField(default=True)),
            ('user', models.OneToOneField(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL,
                                          related_name='sales_manager', to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['name', 'key']}),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
