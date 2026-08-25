import os
import django

# Настройка Django окружения
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from uploads.processors.snapshot_engine import run_full_snapshot_pipeline

if __name__ == '__main__':
    print("🚀 Запуск теста генерации среза по папкам с датами...")
    success, msg = run_full_snapshot_pipeline()
    print("Результат:", msg)