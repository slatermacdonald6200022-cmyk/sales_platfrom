import os
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings

# Реестр 10 менеджеров (Хуснутдинов строго без имени)
MANAGERS_LIST = [
    {'id': 'tsarev', 'name': 'Царев Михаил', 'username': 'tsarev'},
    {'id': 'khusnutdinov', 'name': 'Хуснутдинов', 'username': 'khusnutdinov'},
    {'id': 'redko', 'name': 'Редько', 'username': 'redko'},
    {'id': 'izmaylov', 'name': 'Измайлов', 'username': 'izmaylov'},
    {'id': 'mustafin', 'name': 'Мустафин', 'username': 'mustafin'},
    {'id': 'polyakov', 'name': 'Поляков', 'username': 'polyakov'},
    {'id': 'prasolov_soloviev', 'name': 'Прасолов Н. / Соловьёв В.', 'username': 'prasolov'},
    {'id': 'fomichev', 'name': 'Фомичев Владимир', 'username': 'fomichev'},
    {'id': 'khoroshevsky', 'name': 'Хорошевский Александр', 'username': 'khoroshevsky'},
    {'id': 'ushakov', 'name': 'Ушаков Алексей', 'username': 'ushakov'},
]


def check_is_admin(user):
    profile = getattr(user, 'profile', None)
    return user.is_superuser or (profile and profile.role in ['analyst', 'director'])


@login_required
def upload_view(request):
    user = request.user
    is_admin = check_is_admin(user)

    if is_admin:
        user_managers = MANAGERS_LIST
    else:
        user_managers = [m for m in MANAGERS_LIST if m['username'] == user.username or m['id'] in user.username]
        if not user_managers:
            mgr_name = getattr(getattr(user, 'profile', None), 'manager_name', user.username)
            user_managers = [{'id': user.username, 'name': mgr_name, 'username': user.username}]

    if request.method == 'POST':
        upload_dir = os.path.join(settings.BASE_DIR, 'data', 'raw')
        os.makedirs(upload_dir, exist_ok=True)
        uploaded_count = 0

        if is_admin and 'file_1c' in request.FILES:
            file_1c = request.FILES['file_1c']
            with open(os.path.join(upload_dir, file_1c.name), 'wb+') as dest:
                for chunk in file_1c.chunks():
                    dest.write(chunk)
            uploaded_count += 1

        for mgr in user_managers:
            field_name = f"file_manager_{mgr['id']}"
            if field_name in request.FILES:
                mgr_file = request.FILES[field_name]
                with open(os.path.join(upload_dir, mgr_file.name), 'wb+') as dest:
                    for chunk in mgr_file.chunks():
                        dest.write(chunk)
                uploaded_count += 1

        if uploaded_count > 0:
            messages.success(request, 'Файлы успешно сохранены на сервере.')
            if is_admin:
                return redirect('readiness')
            return redirect('home')
        else:
            messages.warning(request, 'Файлы не были выбраны.')

    context = {
        'is_admin': is_admin,
        'managers': user_managers,
    }
    return render(request, 'uploads/upload.html', context)


@login_required
def readiness_view(request):
    is_admin = check_is_admin(request.user)
    return render(request, 'uploads/processing_readiness.html', {'is_admin': is_admin})


@login_required
def compare_view(request):
    """
    Модуль сравнения исторических срезов и версий прогнозов.
    """
    is_admin = check_is_admin(request.user)

    # Доступные для сопоставления срезы данных
    available_slices = [
        {'id': 'aop_base', 'name': 'План AOP (Базовый целевой ориентир)'},
        {'id': 'forecast_2026_05_12', 'name': 'Прогноз Forecast от 12.05.2026'},
        {'id': 'forecast_2026_05_26', 'name': 'Прогноз Forecast от 26.05.2026'},
        {'id': 'forecast_2026_06_01', 'name': 'Прогноз Forecast от 01.06.2026'},
        {'id': 'forecast_2026_06_08', 'name': 'Прогноз Forecast от 08.06.2026 (Актуальный)'},
        {'id': 'fact_1c', 'name': 'Фактическая реализация (1С:ERP)'},
    ]

    # Получаем параметры фильтрации из GET-запроса
    slice_a = request.GET.get('slice_a', '')
    slice_b = request.GET.get('slice_b', '')
    manager_filter = request.GET.get('manager', 'all')

    # Флаг: был ли инициирован расчет пользователем
    is_calculated = bool(slice_a and slice_b)

    context = {
        'is_admin': is_admin,
        'available_slices': available_slices,
        'managers': MANAGERS_LIST,
        'slice_a': slice_a,
        'slice_b': slice_b,
        'manager_filter': manager_filter,
        'is_calculated': is_calculated,
        'comparison_rows': [],  # Пустой массив для заполнения реальным расчетом
    }
    return render(request, 'uploads/compare.html', context)