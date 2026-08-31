import os
import json
import datetime
from pathlib import Path
import pandas as pd
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings

from uploads.processors.snapshot_engine import create_full_snapshot
from uploads.views import MANAGERS_LIST, scan_raw_directory, check_is_admin

DATA_DIR = Path(settings.BASE_DIR) / "data"
RAW_DIR = DATA_DIR / "raw"
FINAL_DIR = DATA_DIR / "processed" / "final"


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


@login_required
def processing_page_view(request):
    """Страница модуля 02 с предпросмотром данных и контролем готовности всех 10 файлов."""
    user = request.user
    is_admin = check_is_admin(user)

    missing_managers, has_1c, uploaded_count = get_missing_files()
    all_files_ready = (len(missing_managers) == 0) and has_1c

    latest_file = get_latest_final_file()
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
        'file_exists': file_exists,
        'columns': columns,
        'preview_rows': preview_rows,
        'uploaded_count': uploaded_count,
        'total_managers': len(MANAGERS_LIST),
        'missing_managers': missing_managers,
        'has_1c': has_1c,
        'all_files_ready': all_files_ready,
    }
    return render(request, 'analytics/processing.html', context)


@login_required
def run_etl_api(request):
    """API запуска ETL с обязательной валидацией всех 10 файлов менеджеров."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Метод не поддерживается'}, status=405)

    if not check_is_admin(request.user):
        return JsonResponse({'status': 'error', 'message': 'Запуск ETL доступен только Администратору'}, status=403)

    # Валидация полноты входного пакета
    missing_managers, has_1c, uploaded_count = get_missing_files()
    if not has_1c:
        return JsonResponse({
            'status': 'error',
            'message': 'Ошибка: отсутствует файл выгрузки фактических отгрузок 1С:ERP в Модуле 01.'
        }, status=400)

    if missing_managers:
        missing_str = ", ".join(missing_managers)
        return JsonResponse({
            'status': 'error',
            'message': f'Ошибка запуска: загружено {uploaded_count} из 10 файлов. Не прикреплены планы менеджеров: {missing_str}. Загрузите все файлы в Модуле 01.'
        }, status=400)

    try:
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        final_df = create_full_snapshot(raw_dir=str(RAW_DIR), date_str=today_str)

        if final_df is None or final_df.empty:
            return JsonResponse({'status': 'error', 'message': 'Не удалось собрать витрину данных.'}, status=500)

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

        return JsonResponse({
            'status': 'success',
            'message': 'ETL трансформация успешно завершена! Сформирована целевая витрина.',
            'columns': columns,
            'preview_rows': preview_rows,
            'total_rows': len(final_df)
        })

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Ошибка при выполнении ETL: {str(e)}'}, status=500)


@login_required
def download_final_excel(request):
    """Скачивание сформированного файла FINAL_SALES_FACT_TABLE.xlsx."""
    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        raise Http404("Итоговая витрина данных не найдена.")

    with open(latest_file, 'rb') as fh:
        response = HttpResponse(fh.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response['Content-Disposition'] = f'attachment; filename="{latest_file.name}"'
        return response