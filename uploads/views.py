import os
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings

# Список 10 менеджеров (Хуснутдинов строго без имени)
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
    """Проверка, является ли пользователь администратором/аналитиком"""
    profile = getattr(user, 'profile', None)
    return user.is_superuser or (profile and profile.role in ['analyst', 'director'])


@login_required
def upload_view(request):
    """
    Этап 1: Загрузка файлов с разделением прав (RBAC).
    """
    user = request.user
    is_admin = check_is_admin(user)

    # Определяем, какие поля показывать
    if is_admin:
        # Админ видит всех 10 менеджеров
        user_managers = MANAGERS_LIST
    else:
        # Менеджер видит ТОЛЬКО свою строку
        user_managers = [m for m in MANAGERS_LIST if m['username'] == user.username or m['id'] in user.username]
        if not user_managers:
            # Если логин не совпал с id, берем имя из профиля
            mgr_name = getattr(getattr(user, 'profile', None), 'manager_name', user.username)
            user_managers = [{'id': user.username, 'name': mgr_name, 'username': user.username}]

    if request.method == 'POST':
        upload_dir = os.path.join(settings.BASE_DIR, 'data', 'raw')
        os.makedirs(upload_dir, exist_ok=True)
        uploaded_count = 0

        # Файл 1С может сохранять только администратор
        if is_admin and 'file_1c' in request.FILES:
            file_1c = request.FILES['file_1c']
            with open(os.path.join(upload_dir, file_1c.name), 'wb+') as dest:
                for chunk in file_1c.chunks():
                    dest.write(chunk)
            uploaded_count += 1

        # Сохранение файлов менеджеров, доступных текущему пользователю
        for mgr in user_managers:
            field_name = f"file_manager_{mgr['id']}"
            if field_name in request.FILES:
                mgr_file = request.FILES[field_name]
                with open(os.path.join(upload_dir, mgr_file.name), 'wb+') as dest:
                    for chunk in mgr_file.chunks():
                        dest.write(chunk)
                uploaded_count += 1

        if uploaded_count > 0:
            messages.success(request, f'Файл успешно загружен на сервер!')
            if is_admin:
                return redirect('readiness')
            return redirect('home')
        else:
            messages.warning(request, 'Пожалуйста, выберите файл для загрузки.')

    context = {
        'is_admin': is_admin,
        'managers': user_managers,
    }
    return render(request, 'uploads/upload.html', context)


@login_required
def readiness_view(request):
    """
    Этап 2: Обработка файлов (доступно администратору и аналитикам).
    """
    is_admin = check_is_admin(request.user)
    context = {
        'is_admin': is_admin,
    }
    return render(request, 'uploads/processing_readiness.html', context)


@login_required
def compare_view(request):
    """Сравнение срезов"""
    return render(request, 'uploads/compare.html')