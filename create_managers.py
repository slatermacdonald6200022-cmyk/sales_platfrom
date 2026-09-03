import os
import django

# Инициализация окружения Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User
from accounts.models import Profile

# Полный список 10 менеджеров из ваших исходных файлов
MANAGERS_DATA = [
    {"username": "tsarev", "name": "Царев Михаил"},
    {"username": "khusnutdinov", "name": "Хуснутдинов"},
    {"username": "redko", "name": "Редько"},
    {"username": "izmaylov", "name": "Измайлов"},
    {"username": "mustafin", "name": "Мустафин"},
    {"username": "polyakov", "name": "Поляков"},
    {"username": "prasolov_soloviev", "name": "Прасолов Николай, Соловьёв Виктор"},
    {"username": "fomichev", "name": "Фомичев Владимир"},
    {"username": "khoroshevsky", "name": "Александр Хорошевский"},
    {"username": "ushakov", "name": "Ушаков Алексей"},
]

print("Создание учетных записей менеджеров...")

for m in MANAGERS_DATA:
    # Создаем или находим пользователя Django
    user, created = User.objects.get_or_create(
        username=m['username'],
        defaults={'first_name': m['name']}
    )
    if created:
        # Пароль назначается отдельно и никогда не хранится в исходном коде.
        user.set_unusable_password()
        user.save(update_fields=['password'])

    # Привязываем профиль с ролью менеджера
    profile, _ = Profile.objects.get_or_create(user=user)
    profile.role = 'manager'
    profile.manager_name = m['name']
    profile.save()

    status = "создан" if created else "уже существует"
    print(f"  {m['name']} (логин: {m['username']}) — {status}")

print("\nУчетные записи подготовлены.")
print("Назначьте каждому пользователю пароль отдельной командой:")
print("python manage.py changepassword <логин>")
