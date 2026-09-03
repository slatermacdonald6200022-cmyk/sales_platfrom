import os
import json
import datetime
from pathlib import Path
import pandas as pd
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse, Http404, FileResponse
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.urls import reverse

from accounts.permissions import (
    can_manage_files,
    can_view_final_dataset,
    get_user_role,
    is_manager,
    ROLE_DIRECTOR,
    ROLE_MANAGER,
)

from uploads.processors.snapshot_engine import create_full_snapshot
from uploads.processors.export_manager_facts import get_latest_manager_report
from uploads.views import (
    MANAGERS_LIST,
    get_user_manager,
    scan_raw_directory,
    user_can_access_manager,
)
from .models import ProcessingRun

DATA_DIR = Path(settings.BASE_DIR) / "data"
RAW_DIR = DATA_DIR / "raw"
FINAL_DIR = DATA_DIR / "processed" / "final"
MANAGER_REPORTS_DIR = DATA_DIR / "processed" / "manager_reports"


def get_user_manager_reports(user):
    """Возвращает только доступные пользователю результаты обработки."""
    manager_files, _ = scan_raw_directory(str(RAW_DIR))
    reports = []
    for manager in MANAGERS_LIST:
        if manager['id'] not in manager_files or not user_can_access_manager(user, manager):
            continue
        report_path = get_latest_manager_report(MANAGER_REPORTS_DIR, manager['id'])
        source_path = RAW_DIR / manager_files[manager['id']]['stored_filename']
        if report_path and source_path.exists() and report_path.stat().st_mtime >= source_path.stat().st_mtime:
            reports.append({'id': manager['id'], 'name': manager['name']})
    return reports


def get_latest_final_file():
    """Находит самый свежий файл витрины FINAL_SALES_FACT_TABLE.xlsx."""
    if not FINAL_DIR.exists():
        return None
    final_files = sorted(list(FINAL_DIR.glob("**/FINAL_SALES_FACT_TABLE.xlsx")), reverse=True)
    return final_files[0] if final_files else None


def get_missing_files():
    """Проверяет полноту загрузки всех 10 планов и файла 1С."""
    mgr_map, fact_1c = scan_raw_directory(str(RAW_DIR))
    missing_managers = [m['name'] for m in MANAGERS_LIST if m['id'] not in mgr_map]
    has_1c = bool(fact_1c)
    return missing_managers, has_1c, len(mgr_map)


def _stored_path(path_value):
    if not path_value:
        return ''
    try:
        return str(Path(path_value).resolve().relative_to(Path(settings.BASE_DIR).resolve()))
    except (OSError, ValueError):
        return ''


def _resolve_stored_path(path_value):
    if not path_value:
        raise Http404('Файл не найден.')
    base_dir = Path(settings.BASE_DIR).resolve()
    candidate = (base_dir / path_value).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise Http404('Некорректный путь к файлу.') from exc
    if not candidate.is_file():
        raise Http404('Файл не найден.')
    return candidate


@login_required
def processing_page_view(request):
    """Страница модуля 02 с предпросмотром данных и контролем готовности всех 10 файлов."""
    user = request.user
    is_admin = can_manage_files(user)
    can_view_final = can_view_final_dataset(user)
    if not is_admin and not is_manager(user):
        raise PermissionDenied('Раздел обработки недоступен для этой роли.')

    missing_managers, has_1c, uploaded_count = get_missing_files()
    all_files_ready = (len(missing_managers) == 0) and has_1c

    latest_file = get_latest_final_file() if can_view_final else None
    file_exists = latest_file is not None and latest_file.exists()

    columns = []
    preview_rows = []

    if file_exists:
        try:
            df = pd.read_excel(latest_file, nrows=10)
            columns = list(df.columns)
            for _, r in df.iterrows():
                row_dict = {}
                for col in columns:
                    val = r[col]
                    if pd.isna(val):
                        row_dict[col] = "—"
                    elif isinstance(val, float):
                        row_dict[col] = f"{val:,.2f}".replace(",", " ") if val != int(val) else f"{int(val):,}".replace(",", " ")
                    elif isinstance(val, int):
                        row_dict[col] = f"{val:,}".replace(",", " ")
                    else:
                        row_dict[col] = str(val)
                preview_rows.append(row_dict)
        except Exception:
            file_exists = False

    context = {
        'is_admin': is_admin,
        'can_view_final': can_view_final,
        'file_exists': file_exists,
        'columns': columns,
        'preview_rows': preview_rows,
        'uploaded_count': uploaded_count,
        'total_managers': len(MANAGERS_LIST),
        'missing_managers': missing_managers,
        'has_1c': has_1c,
        'all_files_ready': all_files_ready,
        'manager_reports': get_user_manager_reports(user),
    }
    return render(request, 'analytics/processing.html', context)


@login_required
def run_etl_api(request):
    """API запуска ETL с обязательной валидацией всех 10 файлов менеджеров."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Метод не поддерживается'}, status=405)

    if not can_manage_files(request.user):
        return JsonResponse({'status': 'error', 'message': 'Запуск обработки доступен только администратору.'}, status=403)

    # Валидация полноты входного пакета
    missing_managers, has_1c, uploaded_count = get_missing_files()
    if not has_1c:
        return JsonResponse({
            'status': 'error',
            'message': 'Не загружен файл с фактическими данными.'
        }, status=400)

    if missing_managers:
        missing_str = ", ".join(missing_managers)
        return JsonResponse({
            'status': 'error',
            'message': f'Загружено {uploaded_count} из 10 файлов. Не хватает файлов менеджеров: {missing_str}.'
        }, status=400)

    processing_run = ProcessingRun.objects.create(user=request.user)

    try:
        run_folder = timezone.localtime().strftime('%Y-%m-%d_%H-%M-%S-%f')
        final_df = create_full_snapshot(raw_dir=str(RAW_DIR), date_str=run_folder)

        if final_df is None or final_df.empty:
            raise ValueError('Не удалось подготовить итоговые данные.')

        columns = list(final_df.columns)
        preview_df = final_df.head(10)
        preview_rows = []

        for _, r in preview_df.iterrows():
            row_dict = {}
            for col in columns:
                val = r[col]
                if pd.isna(val):
                    row_dict[col] = "—"
                elif isinstance(val, float):
                    row_dict[col] = f"{val:,.2f}".replace(",", " ") if val != int(val) else f"{int(val):,}".replace(",", " ")
                elif isinstance(val, int):
                    row_dict[col] = f"{val:,}".replace(",", " ")
                else:
                    row_dict[col] = str(val)
            preview_rows.append(row_dict)

        processing_info = final_df.attrs.get('processing_info', {})
        manager_reports = {
            manager_id: _stored_path(path)
            for manager_id, path in processing_info.get('manager_reports', {}).items()
            if _stored_path(path)
        }
        processing_run.status = ProcessingRun.STATUS_SUCCESS
        processing_run.finished_at = timezone.now()
        processing_run.report_year = processing_info.get('report_year')
        processing_run.report_month = processing_info.get('report_month')
        processing_run.source_currency = processing_info.get('source_currency', '')
        processing_run.exchange_rate = processing_info.get('exchange_rate')
        processing_run.source_files = processing_info.get(
            'source_files',
            processing_info.get('actual_files', []),
        )
        processing_run.total_actual_rows = processing_info.get('total_actual_rows', 0)
        processing_run.matched_rows = processing_info.get('matched_rows', 0)
        processing_run.unmatched_rows = processing_info.get('unmatched_rows', 0)
        processing_run.matched_amount_cny = processing_info.get('matched_amount_cny', 0.0)
        processing_run.unmatched_amount_cny = processing_info.get('unmatched_amount_cny', 0.0)
        processing_run.source_amount = processing_info.get('source_amount', 0.0)
        processing_run.final_file = _stored_path(processing_info.get('final_path'))
        processing_run.unmatched_file = _stored_path(processing_info.get('unmatched_path'))
        processing_run.manager_reports = manager_reports
        processing_run.save()

        return JsonResponse({
            'status': 'success',
            'message': 'Обработка завершена. Итоговый файл и файлы менеджеров готовы.',
            'columns': columns,
            'preview_rows': preview_rows,
            'total_rows': len(final_df),
            'manager_reports': get_user_manager_reports(request.user),
            'history_url': reverse('processing_run_detail', args=[processing_run.pk]),
        })

    except Exception as e:
        processing_run.status = ProcessingRun.STATUS_FAILED
        processing_run.finished_at = timezone.now()
        processing_run.error_message = str(e)
        processing_run.save(update_fields=['status', 'finished_at', 'error_message'])
        return JsonResponse({'status': 'error', 'message': f'Не удалось завершить обработку: {str(e)}'}, status=500)


def _manager_id_for_user(user):
    manager = get_user_manager(user)
    return manager['id'] if manager else None


def _can_open_run(user, processing_run):
    role = get_user_role(user)
    if role == ROLE_MANAGER:
        manager_id = _manager_id_for_user(user)
        return bool(manager_id and manager_id in processing_run.manager_reports)
    if role == ROLE_DIRECTOR:
        return processing_run.status == ProcessingRun.STATUS_SUCCESS
    return can_view_final_dataset(user)


@login_required
def processing_history(request):
    """История запусков с ограничением данных по роли пользователя."""
    role = get_user_role(request.user)
    runs = list(ProcessingRun.objects.select_related('user').all()[:100])
    manager_id = _manager_id_for_user(request.user) if role == ROLE_MANAGER else None
    if role == ROLE_MANAGER:
        runs = [run for run in runs if manager_id and manager_id in run.manager_reports]
    elif role == ROLE_DIRECTOR:
        runs = [run for run in runs if run.status == ProcessingRun.STATUS_SUCCESS]
    elif not can_view_final_dataset(request.user):
        raise PermissionDenied('История обработок недоступна для этой роли.')

    return render(request, 'analytics/processing_history.html', {
        'runs': runs,
        'show_full_details': can_view_final_dataset(request.user),
        'is_director': role == ROLE_DIRECTOR,
    })


@login_required
def processing_run_detail(request, run_id):
    processing_run = get_object_or_404(
        ProcessingRun.objects.select_related('user'),
        pk=run_id,
    )
    if not _can_open_run(request.user, processing_run):
        raise PermissionDenied('Эта запись обработки недоступна.')

    role = get_user_role(request.user)
    manager_id = _manager_id_for_user(request.user) if role == ROLE_MANAGER else None
    return render(request, 'analytics/processing_run_detail.html', {
        'run': processing_run,
        'show_full_details': can_view_final_dataset(request.user),
        'is_director': role == ROLE_DIRECTOR,
        'manager_id': manager_id,
        'has_manager_report': bool(manager_id and manager_id in processing_run.manager_reports),
    })


@login_required
def download_processing_file(request, run_id, file_kind):
    processing_run = get_object_or_404(ProcessingRun, pk=run_id)
    if not _can_open_run(request.user, processing_run):
        raise PermissionDenied('Эта запись обработки недоступна.')

    if file_kind == 'manager':
        manager_id = _manager_id_for_user(request.user)
        if can_view_final_dataset(request.user):
            manager_id = request.GET.get('manager') or manager_id
        if not manager_id or manager_id not in processing_run.manager_reports:
            raise PermissionDenied('Файл менеджера недоступен.')
        stored_path = processing_run.manager_reports[manager_id]
    elif file_kind == 'final' and can_view_final_dataset(request.user):
        stored_path = processing_run.final_file
    elif file_kind == 'unmatched' and can_view_final_dataset(request.user):
        stored_path = processing_run.unmatched_file
    else:
        raise PermissionDenied('Файл недоступен для этой роли.')

    file_path = _resolve_stored_path(stored_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=file_path.name)


@login_required
def download_final_excel(request):
    """Скачивание сформированного файла FINAL_SALES_FACT_TABLE.xlsx."""
    if not can_view_final_dataset(request.user):
        raise PermissionDenied('Итоговая таблица доступна только аналитику и администратору.')
    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        raise Http404("Итоговый файл не найден.")

    with open(latest_file, 'rb') as fh:
        response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response['Content-Disposition'] = f'attachment; filename="{latest_file.name}"'
        return response
