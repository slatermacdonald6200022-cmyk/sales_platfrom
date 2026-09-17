"""Current-page downloads never fall back to older archived results."""
import json
from pathlib import Path
from django.conf import settings
from .models import ProcessingRun
from uploads.input_state import input_state


def result_path(stored):
    if not stored:
        return None
    base = Path(settings.BASE_DIR).resolve()
    path = (base / stored).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def current_run():
    run = ProcessingRun.objects.order_by('-created_at', '-pk').first()
    if not run or run.status != ProcessingRun.STATUS_SUCCESS:
        return None
    final = result_path(run.final_file)
    if not final:
        return None
    try:
        saved = json.loads(final.with_name('input_state.json').read_text(encoding='utf-8'))
        if saved.get('version') == 1 and saved == input_state(Path(settings.BASE_DIR) / 'data' / 'raw'):
            return run
    except (OSError, ValueError, TypeError):
        pass
    return None
