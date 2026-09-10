"""Small process-local cache; every request checks the source file version."""
from collections import OrderedDict
from pathlib import Path
from threading import RLock

import pandas as pd

_frames = OrderedDict()
_lock = RLock()
_MAX_FILES = 2


def _version(path):
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)


def read_report(path):
    """Return a private copy, reloading after edits, replacement or a new run.

    Only source data is cached, not user-specific selections or rendered pages.
    A file changed during reading is retried rather than cached as a stable result.
    """
    path = Path(path).resolve()
    with _lock:
        for attempt in range(3):
            key = _version(path)
            if key in _frames:
                _frames.move_to_end(key)
                return _frames[key].copy(deep=True)
            frame = pd.read_excel(path)
            if _version(path) != key:
                continue
            for old in list(_frames):
                if old[0] == str(path):
                    del _frames[old]
            _frames[key] = frame
            while len(_frames) > _MAX_FILES:
                _frames.popitem(last=False)
            return frame.copy(deep=True)
    raise ValueError('Итоговый файл сейчас изменяется. Повторите открытие отчёта после сохранения.')


def clear_report_cache():
    with _lock:
        _frames.clear()
