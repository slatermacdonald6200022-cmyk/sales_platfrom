import os
import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache

from .processors.compare_engine import get_available_snapshot_dates, compare_snapshots

MANAGERS_LIST = [
    {'id': 'tsarev', 'name': 'Царев Михаил', 'username': 'tsarev'},
    {'id': 'khusnutdinov', 'name': 'Хуснутдинов', 'username': 'khusnutdinov'},
    {'id': 'redko', 'name': 'Редько', 'username': 'redko'},
    {'id': 'izmaylov', 'name': 'Измайлов', 'username': 'izmaylov'},
    {'id': 'mustafin', 'name': 'Мустафин', 'username': 'mustafin'},
    {'id': 'polyakov', 'name': 'Поляков', 'username': 'polyakov'},
    {'id': 'prasolov', 'name': 'Прасолов Н. / Соловьёв В.', 'username': 'prasolov'},
    {'id': 'fomichev', 'name': 'Фомичев Владимир', 'username': 'fomichev'},
    {'id': 'khoroshevsky', 'name': 'Хорошевский Александр', 'username': 'khoroshevsky'},
    {'id': 'ushakov', 'name': 'Ушаков Алексей', 'username': 'ushakov'},
]


def check_is_admin(user):
    """Проверка прав: администратор или аналитик"""
    if user.is_superuser or user.is_staff:
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.role in ['analyst', 'director'])


def get_instant_file_info(file_path):
    """Мгновенный сбор данных файловой системы без распаковки Excel (0.001 сек)"""
    if not os.path.exists(file_path):
        return None

    stat = os.stat(file_path)
    modified_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%d.%m.%Y %H:%M')

    size_bytes = stat.st_size
    if size_bytes > 1024 * 1024:
        size_str = f"{round(size_bytes / (1024 * 1024), 2)} МБ"
    else:
        size_str = f"{round(size_bytes / 1024, 1)} КБ"

    # Структуру берем только из кэша (если считалась при загрузке)
    cache_key = f"excel_meta_{os.path.basename(file_path)}"
    meta = cache.get(cache_key) or {'rows': '—', 'cols': '—', 'sheets_count': 1, 'sheet_names': 'Основной'}

    return {
        'modified': modified_time,
        'size': size_str,
        'rows': meta.get('rows', '—'),
        'cols': meta.get('cols', '—'),
        'sheets_count': meta.get('sheets_count', 1),
        'sheet_names': meta.get('sheet_names', '—')
    }


def scan_raw_directory(upload_dir):
    """Быстрое сопоставление файлов со слотами менеджеров"""
    if not os.path.exists(upload_dir):
        return {}, None

    files = os.listdir(upload_dir)
    manager_files = {}
    fact_1c_info = None

    for fname in files:
        fpath = os.path.join(upload_dir, fname)
        if not os.path.isfile(fpath):
            continue

        fname_lower = fname.lower()
        if not (fname_lower.endswith('.xlsx') or fname_lower.endswith('.xls')):
            continue

        # 1. 1C:ERP
        if fname.startswith('fact_1c_') or '1c' in fname_lower or 'факт' in fname_lower:
            orig_name = fname.replace('fact_1c_', '', 1) if fname.startswith('fact_1c_') else fname
            info = get_instant_file_info(fpath)
            if info:
                fact_1c_info = {
                    'stored_filename': fname,
                    'display_name': orig_name,
                    **info
                }
            continue

        # 2. Файлы менеджеров
        for mgr in MANAGERS_LIST:
            mgr_id = mgr['id']
            prefix = f"plan_{mgr_id}_"
            surname = mgr['name'].lower().split()[0]
            if fname.startswith(prefix) or surname in fname_lower:
                orig_name = fname.replace(prefix, '', 1) if fname.startswith(prefix) else fname
                info = get_instant_file_info(fpath)
                if info:
                    manager_files[mgr_id] = {
                        'stored_filename': fname,
                        'display_name': orig_name,
                        **info
                    }
                break

    return manager_files, fact_1c_info


def remove_old_manager_files(upload_dir, mgr_id):
    """Удаление старых версий файлов менеджера"""
    prefix = f"plan_{mgr_id}_"
    if not os.path.exists(upload_dir):
        return
    for fname in os.listdir(upload_dir):
        if fname.startswith(prefix):
            try:
                os.remove(os.path.join(upload_dir, fname))
            except OSError:
                pass


@login_required
def upload_view(request):
    """Сверхбыстрый модуль загрузки файлов"""
    user = request.user
    is_admin = check_is_admin(user)
    upload_dir = os.path.join(settings.BASE_DIR, 'data', 'raw')
    os.makedirs(upload_dir, exist_ok=True)

    if request.method == 'POST':
        # 1. Загрузка 1С (Админ)
        if is_admin and 'file_1c' in request.FILES:
            f_1c = request.FILES['file_1c']
            for fname in os.listdir(upload_dir):
                if fname.startswith('fact_1c_'):
                    try:
                        os.remove(os.path.join(upload_dir, fname))
                    except OSError:
                        pass

            clean_name = f_1c.name.replace(' ', '_')
            dest_path = os.path.join(upload_dir, f"fact_1c_{clean_name}")
            with open(dest_path, 'wb+') as dest:
                for chunk in f_1c.chunks():
                    dest.write(chunk)
            messages.success(request, f'Выгрузка 1С «{f_1c.name}» сохранена.')
            return redirect('upload_files')

        # 2. Загрузка планов менеджеров
        for mgr in MANAGERS_LIST:
            field_name = f"file_manager_{mgr['id']}"
            if field_name in request.FILES:
                if not is_admin:
                    user_matched = (
                            mgr['username'] == user.username or
                            mgr['id'] in user.username.lower() or
                            (hasattr(user, 'profile') and user.profile.manager_name and mgr['name'].startswith(
                                user.profile.manager_name.split()[0]))
                    )
                    if not user_matched:
                        continue

                m_file = request.FILES[field_name]
                remove_old_manager_files(upload_dir, mgr['id'])

                clean_name = m_file.name.replace(' ', '_')
                dest_path = os.path.join(upload_dir, f"plan_{mgr['id']}_{clean_name}")
                with open(dest_path, 'wb+') as dest:
                    for chunk in m_file.chunks():
                        dest.write(chunk)
                messages.success(request, f'Файл для менеджера {mgr["name"]} сохранен.')
                return redirect('upload_files')

    manager_files_map, fact_1c_info = scan_raw_directory(upload_dir)

    if is_admin:
        target_managers = MANAGERS_LIST
    else:
        target_managers = [
            m for m in MANAGERS_LIST
            if m['username'] == user.username or m['id'] in user.username.lower() or (
                    hasattr(user, 'profile') and user.profile.manager_name and m['name'].startswith(
                user.profile.manager_name.split()[0])
            )
        ]
        if not target_managers:
            mgr_title = getattr(getattr(user, 'profile', None), 'manager_name',
                                None) or user.get_full_name() or user.username
            target_managers = [{'id': user.username, 'name': mgr_title, 'username': user.username}]

    slots = []
    for mgr in target_managers:
        f_info = manager_files_map.get(mgr['id'])
        slots.append({
            'id': mgr['id'],
            'name': mgr['name'],
            'username': mgr['username'],
            'is_uploaded': bool(f_info),
            'info': f_info,
        })

    context = {
        'is_admin': is_admin,
        'slots': slots,
        'total_managers_count': len(MANAGERS_LIST),
        'uploaded_managers_count': len(manager_files_map),
        'fact_1c': fact_1c_info,
    }
    return render(request, 'uploads/upload.html', context)


@login_required
def delete_file_view(request, file_type, target_id):
    """Удаление файла менеджера или файла 1С"""
    user = request.user
    is_admin = check_is_admin(user)
    upload_dir = os.path.join(settings.BASE_DIR, 'data', 'raw')

    if file_type == 'fact_1c':
        if not is_admin:
            messages.error(request, 'Недостаточно прав для удаления выгрузки 1С.')
            return redirect('upload_files')
        for fname in os.listdir(upload_dir):
            if fname.startswith('fact_1c_') or fname == target_id:
                try:
                    os.remove(os.path.join(upload_dir, fname))
                except OSError:
                    pass
        messages.success(request, 'Файл 1С:ERP удален.')

    elif file_type == 'manager':
        if not is_admin:
            can_delete = (target_id == user.username or target_id in user.username.lower())
            if not can_delete:
                messages.error(request, 'Вы можете удалить только свой файл.')
                return redirect('upload_files')

        remove_old_manager_files(upload_dir, target_id)
        messages.success(request, 'Файл плана удален.')

    return redirect('upload_files')


@login_required
def readiness_view(request):
    """Модуль готовности файлов и ETL"""
    return render(request, 'uploads/processing_readiness.html')


@login_required
def compare_view(request):
    """Модуль сопоставления исторических срезов планов и расчета дельты"""
    is_admin = check_is_admin(request.user)

    raw_dates = get_available_snapshot_dates(base_dir=os.path.join(settings.BASE_DIR, 'data', 'processed', 'snapshots'))
    available_slices = [{'id': d, 'name': f"Срез планов от {d}"} for d in raw_dates]

    slice_a = request.GET.get('slice_a', '')
    slice_b = request.GET.get('slice_b', '')
    manager_filter = request.GET.get('manager', 'all')

    if not is_admin:
        manager_filter = request.user.username

    comparison_result = None
    is_calculated = False

    if slice_a and slice_b and slice_a in raw_dates and slice_b in raw_dates:
        comparison_result = compare_snapshots(
            date_a=slice_a,
            date_b=slice_b,
            manager_filter=manager_filter,
            base_dir=os.path.join(settings.BASE_DIR, 'data', 'processed', 'snapshots')
        )
        is_calculated = comparison_result is not None

    context = {
        'is_admin': is_admin,
        'available_slices': available_slices,
        'managers': MANAGERS_LIST,
        'slice_a': slice_a,
        'slice_b': slice_b,
        'manager_filter': manager_filter,
        'is_calculated': is_calculated,
        'summary': comparison_result['summary'] if is_calculated else None,
        'comparison_rows': comparison_result['rows'] if is_calculated else [],
    }
    return render(request, 'uploads/compare.html', context)