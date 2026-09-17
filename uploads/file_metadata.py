"""Persistent validation summaries. Page rendering never opens Excel."""
import hashlib
import json
import os
import uuid
from pathlib import Path
from django.core.cache import cache


def file_version(path):
    path = Path(path).resolve()
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def metadata_path(path):
    path = Path(path).resolve()
    return path.parent / '.file_metadata' / (hashlib.sha256(path.name.encode()).hexdigest() + '.json')


def cache_key(version):
    return 'file_metadata_v1_' + hashlib.sha256(json.dumps(version).encode()).hexdigest()


def save_metadata(path, data, expected_version=None):
    version = file_version(path)
    if expected_version is not None and version != expected_version:
        raise ValueError('Файл изменился во время чтения сведений.')
    destination = metadata_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        pending.write_text(json.dumps({'version': version, 'data': data}, ensure_ascii=False), encoding='utf-8')
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    cache.set(cache_key(version), data, timeout=3600)


def read_metadata(path):
    version = file_version(path)
    key = cache_key(version)
    data = cache.get(key)
    if data is not None:
        return data
    try:
        payload = json.loads(metadata_path(path).read_text(encoding='utf-8'))
        if payload['version'] == version and isinstance(payload['data'], dict):
            data = payload['data']
            cache.set(key, data, timeout=3600)
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}
