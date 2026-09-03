import os
import datetime
import json
from django.shortcuts import render, redirect
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from .processors.compare_engine import get_available_snapshot_dates, compare_snapshots
from .processors.export_manager_facts import get_latest_manager_report, remove_manager_reports

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

FACT_1C_SETTINGS_FILENAME = 'fact_1c_settings.json'


def load_fact_1c_settings(upload_dir):
    """Читает сохранённую валюту текущей выгрузки 1С."""
    settings_path = os.path.join(upload_dir, FACT_1C_SETTINGS_FILENAME)
    try:
        with open(settings_path, 'r', encoding='utf-8') as settings_file:
            settings_data = json.load(settings_file)
    except (OSError, ValueError, TypeError):
        settings_data = {}

    source_currency = str(settings_data.get('source_currency', 'RUB')).upper()
    if source_currency not in {'RUB', 'CNY'}:
        source_currency = 'RUB'
    return {'source_currency': source_currency}


def save_fact_1c_settings(upload_dir, source_currency):
    """Сохраняет валюту вместе с текущей выгрузкой 1С."""
    settings_path = os.path.join(upload_dir, FACT_1C_SETTINGS_FILENAME)
    with open(settings_path, 'w', encoding='utf-8') as settings_file:
        json.dump({'source_currency': source_currency}, settings_file, ensure_ascii=False)


def check_is_admin(user):
    """Проверка прав: администратор или аналитик"""
    if user.is_superuser or user.is_staff:
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.role in ['analyst', 'director'])


def user_can_access_manager(user, manager):
    """Проверяет право пользователя видеть файл конкретного менеджера."""
    if check_is_admin(user):
        return True
    profile = getattr(user, 'profile', None)
    profile_name = (getattr(profile, 'manager_name', '') or '').strip().lower()
    manager_surname = manager['name'].split()[0].lower()
    return (
        manager['username'] == user.username
        or manager['id'] in user.username.lower()
        or (profile_name and profile_name.startswith(manager_surname))
    )


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
    fact_1c_settings = load_fact_1c_settings(upload_dir)
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
                    'source_currency': fact_1c_settings['source_currency'],
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
            source_currency = str(request.POST.get('source_currency', 'RUB')).upper()
            if source_currency not in {'RUB', 'CNY'}:
                messages.error(request, 'Выберите валюту выгрузки: рубли или юани.')
                return redirect('upload_files')

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
            save_fact_1c_settings(upload_dir, source_currency)
            currency_label = 'рубли — пересчитать по курсу ЦБ' if source_currency == 'RUB' else 'юани — без пересчёта'
            messages.success(request, f'Выгрузка 1С «{f_1c.name}» сохранена. Валюта: {currency_label}.')
            return redirect('upload_files')

        # Изменение валюты уже загруженной выгрузки без повторной загрузки файла
        if is_admin and request.POST.get('action') == 'set_1c_currency':
            source_currency = str(request.POST.get('source_currency', '')).upper()
            if source_currency not in {'RUB', 'CNY'}:
                messages.error(request, 'Выберите валюту выгрузки: рубли или юани.')
                return redirect('upload_files')

            has_fact_1c = any(
                fname.startswith('fact_1c_') and fname.lower().endswith(('.xlsx', '.xls'))
                for fname in os.listdir(upload_dir)
            )
            if not has_fact_1c:
                messages.error(request, 'Сначала загрузите файл выгрузки 1С.')
                return redirect('upload_files')

            save_fact_1c_settings(upload_dir, source_currency)
            currency_label = 'рубли — пересчитать по курсу ЦБ' if source_currency == 'RUB' else 'юани — без пересчёта'
            messages.success(request, f'Режим обработки изменён: {currency_label}.')
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
        target_managers = [m for m in MANAGERS_LIST if user_can_access_manager(user, m)]
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
def download_manager_report_view(request, manager_id):
    """Скачивание персонального файла; менеджер не может получить чужой файл."""
    manager = next((item for item in MANAGERS_LIST if item['id'] == manager_id), None)
    if manager is None:
        raise Http404('Менеджер не найден.')
    if not user_can_access_manager(request.user, manager):
        raise PermissionDenied('Нет доступа к файлу другого менеджера.')

    reports_base_dir = os.path.join(settings.BASE_DIR, 'data', 'processed', 'manager_reports')
    report_path = get_latest_manager_report(reports_base_dir, manager_id)
    if report_path is None or not report_path.exists():
        raise Http404('Файл с фактическими значениями ещё не сформирован.')

    manager_files, _ = scan_raw_directory(os.path.join(settings.BASE_DIR, 'data', 'raw'))
    source_info = manager_files.get(manager_id)
    if not source_info:
        raise Http404('Исходный файл менеджера удалён.')
    source_path = os.path.join(settings.BASE_DIR, 'data', 'raw', source_info['stored_filename'])
    if not os.path.exists(source_path) or report_path.stat().st_mtime < os.path.getmtime(source_path):
        raise Http404('После загрузки нового исходного файла необходимо снова запустить ETL.')

    return FileResponse(
        open(report_path, 'rb'),
        as_attachment=True,
        filename=report_path.name,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


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
            target_manager = next((item for item in MANAGERS_LIST if item['id'] == target_id), None)
            can_delete = bool(target_manager and user_can_access_manager(user, target_manager))
            if not can_delete:
                messages.error(request, 'Вы можете удалить только свой файл.')
                return redirect('upload_files')

        remove_old_manager_files(upload_dir, target_id)
        reports_base_dir = os.path.join(settings.BASE_DIR, 'data', 'processed', 'manager_reports')
        remove_manager_reports(reports_base_dir, target_id)
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
