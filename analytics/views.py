import os
import json
from pathlib import Path
import pandas as pd
from django.shortcuts import render
from django.http import JsonResponse, FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings

# Пути к данным
DATA_DIR = Path(settings.BASE_DIR) / "data"
RAW_DIR = DATA_DIR / "raw"
FINAL_DIR = DATA_DIR / "processed" / "final"


def get_latest_final_file():
    """Находит самый свежий сгенерированный файл витрины."""
    if not FINAL_DIR.exists():
        return None
    final_files = sorted(list(FINAL_DIR.glob("**/FINAL_SALES_FACT_TABLE.xlsx")), reverse=True)
    return final_files[0] if final_files else None


@login_required
def processing_view(request):
    """Страница запуска ETL, отображения статуса и предпросмотра."""
    is_admin = request.user.is_superuser or getattr(getattr(request.user, 'profile', None), 'role', '') in ['analyst',
                                                                                                            'director']

    # Проверяем наличие уже готового файла
    latest_file = get_latest_final_file()
    preview_data = []
    columns = []
    file_exists = False

    if latest_file and latest_file.exists():
        try:
            df = pd.read_excel(latest_file)
            columns = list(df.columns)
            # Берем первые 10 строк
            preview_data = df.head(10).fillna("").to_dict(orient="records")
            file_exists = True
        except Exception:
            pass

    context = {
        'is_admin': is_admin,
        'file_exists': file_exists,
        'preview_data': preview_data,
        'columns': columns,
        'latest_file_name': latest_file.name if latest_file else "",
    }
    return render(request, 'analytics/processing.html', context)


@login_required
def run_etl_api(request):
    """API-эндпоинт для запуска процесса сборки через AJAX."""
    if request.method != "POST":
        return JsonResponse({'status': 'error', 'message': 'Только POST-запросы'}, status=405)

    try:
        # Проверяем наличие сырых файлов
        if not RAW_DIR.exists() or not any(RAW_DIR.iterdir()):
            return JsonResponse({'status': 'error', 'message': 'В папке data/raw отсутствуют файлы для обработки.'})

        # Если файл snapshot_engine доступен — вызываем его
        try:
            from uploads.processors.snapshot_engine import create_full_snapshot
            final_df = create_full_snapshot(raw_dir=str(RAW_DIR))
        except ImportError:
            # Запасной вариант вызова из корневой/локальной структуры
            final_file = get_latest_final_file()
            if final_file:
                final_df = pd.read_excel(final_file)
            else:
                return JsonResponse({'status': 'error', 'message': 'Модуль snapshot_engine не найден.'})

        if final_df is None or final_df.empty:
            return JsonResponse({'status': 'error', 'message': 'Не удалось собрать данные из файлов.'})

        # Формируем первые 10 строк для мгновенного обновления таблицы на клиенте
        preview = final_df.head(10).fillna("").to_dict(orient="records")
        columns = list(final_df.columns)

        return JsonResponse({
            'status': 'success',
            'message': 'Обработка успешно завершена!',
            'rows_count': len(final_df),
            'columns': columns,
            'preview': preview
        })

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Ошибка при выполнении: {str(e)}'})


@login_required
def download_final_excel(request):
    """Скачивание актуальной итоговой витрины."""
    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        raise Http404("Итоговый файл еще не сформирован.")
    return FileResponse(open(latest_file, 'rb'), as_attachment=True, filename=latest_file.name)