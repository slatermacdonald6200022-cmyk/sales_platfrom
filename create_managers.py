import os
import django
import pandas as pd

# Инициализация окружения Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User
from accounts.models import UserProfile

# Полный список 10 менеджеров из ваших исходных файлов
MANAGERS_DATA = [
    {"username": "tsarev", "name": "Царев Михаил", "pass": "Tsarev2026!"},
    {"username": "khusnutdinov", "name": "Хуснутдинов", "pass": "Khusnut2026!"},
    {"username": "redko", "name": "Редько", "pass": "Redko2026!"},
    {"username": "izmaylov", "name": "Измайлов", "pass": "Izmaylov2026!"},
    {"username": "mustafin", "name": "Мустафин", "pass": "Mustafin2026!"},
    {"username": "polyakov", "name": "Поляков", "pass": "Polyakov2026!"},
    {"username": "prasolov_soloviev", "name": "Прасолов Николай, Соловьёв Виктор", "pass": "PrasSolov2026!"},
    {"username": "fomichev", "name": "Фомичев Владимир", "pass": "Fomichev2026!"},
    {"username": "khoroshevsky", "name": "Александр Хорошевский", "pass": "Khorosh2026!"},
    {"username": "ushakov", "name": "Ушаков Алексей", "pass": "Ushakov2026!"},
]

created_list = []

print("🚀 Создание учетных записей менеджеров...")

for m in MANAGERS_DATA:
    # Создаем или находим пользователя Django
    user, created = User.objects.get_or_create(
        username=m['username'],
        defaults={'first_name': m['name']}
    )
    user.set_password(m['pass'])
    user.save()

    # Привязываем профиль с ролью менеджера
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = 'manager'
    profile.manager_name = m['name']
    profile.save()

    status = "Создан" if created else "Обновлен пароль"
    print(f"  • {m['name']} (логин: {m['username']}) — {status}")

    created_list.append({
        'ФИО Менеджера (в файлах планов)': m['name'],
        'Логин для входа': m['username'],
        'Пароль': m['pass'],
        'Роль в системе': 'Менеджер по продажам'
    })

# Экспортируем удобную памятку-шпаргалку в Excel
df = pd.DataFrame(created_list)
output_file = 'accounts_credentials.xlsx'
df.to_excel(output_file, index=False)

print("\n Все 10 аккаунтов успешно зарегистрированы в базе!")
print(f"📁 Шпаргалка с доступами сохранена в: {output_file}\n")