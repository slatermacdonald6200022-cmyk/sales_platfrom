from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from uploads.file_metadata import file_version, read_metadata, save_metadata
from uploads.validators import validate_actual_file, validate_manager_file


class Command(BaseCommand):
    help = 'Заполняет сведения для старых загруженных файлов, не меняя сами таблицы.'

    def handle(self, *args, **options):
        raw = Path(settings.BASE_DIR) / 'data' / 'raw'
        updated = skipped = failed = 0
        for path in sorted(raw.glob('*.xlsx')):
            if not path.name.startswith(('plan_', 'fact_1c_')):
                continue
            try:
                if read_metadata(path):
                    skipped += 1
                    continue
                before = file_version(path)
                validator = validate_manager_file if path.name.startswith('plan_') else validate_actual_file
                with path.open('rb') as stream:
                    result = validator(File(stream, name=path.name))
                save_metadata(path, result.as_cache_data(), expected_version=before)
                updated += 1
                self.stdout.write(f'Обновлено: {path.name}')
            except (OSError, ValueError) as exc:
                failed += 1
                self.stderr.write(f'Не удалось прочитать {path.name}: {exc}')
        self.stdout.write(f'Обновлено: {updated}; уже сохранено: {skipped}; ошибок: {failed}.')
