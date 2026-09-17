"""Identity of the complete input batch, including the actual export."""
import hashlib
from pathlib import Path
from accounts.manager_registry import managers


def input_state(raw_dir):
    raw = Path(raw_dir)
    roster = managers()
    active_ids = {m['id'] for m in roster}
    files = {}
    for path in sorted(raw.glob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.xlsx', '.xls', '.xlsm'}:
            continue
        if path.name.startswith('plan_') and path.name.split('_', 2)[1] not in active_ids:
            continue
        before = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
            raise ValueError('Исходный файл меняется. Повторите обработку после завершения загрузки.')
        files[path.name] = [after.st_size, after.st_mtime_ns, after.st_ctime_ns, digest]
    return {'version': 1, 'files': files, 'managers': roster}
