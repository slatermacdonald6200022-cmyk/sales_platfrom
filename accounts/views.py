import os
import json
from pathlib import Path
import pandas as pd
import numpy as np
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.conf import settings
from django.urls import reverse
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST

from django.contrib import messages

from accounts.permissions import can_manage_files, can_view_company_dashboard, is_manager
from uploads.views import get_user_manager, manager_name_matches

DATA_DIR = Path(settings.BASE_DIR) / "data"
FINAL_DIR = DATA_DIR / "processed" / "final"

MONTH_NAMES_RU = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}


def get_latest_final_file():
    """Находит самый свежий файл витрины FINAL_SALES_FACT_TABLE.xlsx."""
    if not FINAL_DIR.exists():
        return None
    final_files = sorted(list(FINAL_DIR.glob("**/FINAL_SALES_FACT_TABLE.xlsx")), reverse=True)
    return final_files[0] if final_files else None


def get_reporting_period(final_file, df):
    """Возвращает период последней обработки, сохраняя поддержку старых витрин."""
    metadata_path = Path(final_file).with_name('processing_metadata.json')
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        year = int(metadata['report_year'])
        month = int(metadata['report_month'])
        if year >= 2000 and 1 <= month <= 12:
            return year, month
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass

    # Старые результаты не имеют файла метаданных. Для них берём самый поздний
    # период с ненулевым фактом, а если фактов нет — последний период таблицы.
    dated = df.dropna(subset=['Год', 'Номер месяца']).copy()
    if dated.empty:
        raise ValueError('В итоговой таблице не найдено корректных периодов.')
    if 'Факт, CNY' in dated.columns and 'Факт, шт' in dated.columns:
        fact_rows = dated[(dated['Факт, CNY'] != 0) | (dated['Факт, шт'] != 0)]
        if not fact_rows.empty:
            dated = fact_rows
    latest = dated.sort_values(['Год', 'Номер месяца']).iloc[-1]
    return int(latest['Год']), int(latest['Номер месяца'])


def format_currency(val):
    """Форматирует числовые денежные показатели в юанях: 12 345 678 ¥."""
    if val is None or np.isnan(val) or val == 0:
        return "0 ¥"
    return f"{val:,.0f}".replace(",", " ") + " ¥"


def format_percent(val):
    """Форматирует процентные показатели: 86.4%."""
    if val is None or np.isnan(val) or val == 0:
        return "0.0%"
    return f"{val:.1f}%"


def get_percent_color(pct):
    """Градиентная шкала цвета для процентных индикаторов."""
    if pct is None or np.isnan(pct):
        return "#64748b"
    if pct >= 130:
        return "#0f5132"
    elif pct >= 100:
        return "#198754"
    elif pct >= 90:
        return "#84cc16"
    elif pct >= 75:
        return "#eab308"
    elif pct >= 50:
        return "#f97316"
    else:
        return "#dc2626"


def login_view(request):
    """Страница авторизации."""
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                return redirect('home')
    else:
        form = AuthenticationForm()

    return render(request, 'accounts/login.html', {'form': form})


@require_POST
def logout_view(request):
    """Выход из системы."""
    logout(request)
    return redirect('login')


@login_required
def home_view(request):
    """Главная страница платформы."""
    return render(request, 'accounts/home.html', {
        'can_upload_files': can_manage_files(request.user) or is_manager(request.user),
        'can_open_processing': can_manage_files(request.user) or is_manager(request.user),
    })


@login_required
def profile_view(request):
    """
    Личный кабинет пользователя с возможностью обновления фото профиля.
    """
    user = request.user
    profile = getattr(user, 'profile', None)

    # Обработка отправки формы загрузки аватарки
    if request.method == 'POST':
        if 'avatar' in request.FILES and profile:
            profile.avatar = request.FILES['avatar']
            profile.save()
            messages.success(request, 'Фото профиля обновлено.')
            return redirect('profile')

    full_name = getattr(profile, 'manager_name', None) or user.get_full_name() or user.username
    parts = full_name.strip().split()
    initials = f"{parts[0][0]}{parts[1][0]}".upper() if len(parts) >= 2 else full_name[:2].upper()

    context = {
        'full_name': full_name,
        'initials': initials,
        'user': user,
        'profile': profile,
    }
    return render(request, 'accounts/profile.html', context)


@login_required
def dashboard_view(request, deviations_page=False):
    from .dashboard import build_context
    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        return render(request, 'accounts/dashboard.html', {
            'has_data': False, 'is_deviations_page': deviations_page,
            'message': 'Данные ещё не подготовлены. Запустите обработку файлов.'
        })
    try:
        from .report_cache import read_report
        df = read_report(latest_file)
        reporting = get_reporting_period(latest_file, df)
        context = build_context(df, request.GET, latest_file, reporting, include_deviations=deviations_page)
        context['is_deviations_page'] = deviations_page
        context['reset_url'] = reverse('sales_deviations' if deviations_page else 'dashboard')
        query = request.GET.copy()
        query.pop('page', None)
        query.pop('deviation_basis', None)
        context['filter_query'] = query.urlencode()
        if deviations_page:
            page = Paginator(context['anomalies']['rows'], 50).get_page(request.GET.get('page'))
            context['anomaly_page'] = page
            context['anomalies']['rows'] = page.object_list
    except (OSError, ValueError, KeyError) as exc:
        return render(request, 'accounts/dashboard.html', {
            'has_data': False, 'is_deviations_page': deviations_page,
            'message': f'Не удалось открыть отчёт: {exc}'
        })
    return render(request, 'accounts/dashboard.html', context)
