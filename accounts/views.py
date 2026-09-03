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
def dashboard_view(request):
    """
    Интерактивный аналитический дашборд.
    Считывает FINAL_SALES_FACT_TABLE.xlsx, фильтрует срезы и рассчитывает KPI.
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    is_admin = can_view_company_dashboard(user)
    manager_name = getattr(profile, 'manager_name', '') or user.get_full_name() or user.username

    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': 'Данные ещё не подготовлены. Запустите обработку файлов.'
        })

    try:
        df = pd.read_excel(latest_file)
    except Exception as e:
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': f'Не удалось открыть итоговые данные: {str(e)}'
        })

    # Приведение числовых колонок
    for num_col in ['AOP, CNY', 'Прогноз, CNY', 'Факт, CNY', 'AOP, шт', 'Прогноз, шт', 'Факт, шт']:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0.0)

    if 'Год' not in df.columns or 'Номер месяца' not in df.columns:
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': 'В итоговой таблице отсутствуют столбцы периода.'
        })
    df['Год'] = pd.to_numeric(df['Год'], errors='coerce')
    df['Номер месяца'] = pd.to_numeric(df['Номер месяца'], errors='coerce')
    df = df.dropna(subset=['Год', 'Номер месяца']).copy()
    df['Год'] = df['Год'].astype(int)
    df['Номер месяца'] = df['Номер месяца'].astype(int)

    try:
        reporting_year, reporting_month = get_reporting_period(latest_file, df)
    except ValueError as exc:
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': str(exc),
        })

    # Ограничение датафрейма для менеджера
    if not is_admin:
        assigned_manager = get_user_manager(user)
        if assigned_manager is None or 'Менеджер' not in df.columns:
            df = df.iloc[0:0].copy()
        else:
            matched_rows = df['Менеджер'].apply(
                lambda value: manager_name_matches(assigned_manager, value)
            )
            # При ошибке сопоставления возвращаем пустой набор, а не данные всей компании.
            df = df[matched_rows].copy()

    # Получение параметров фильтрации из GET-запроса
    selected_manager = request.GET.get('manager', '')
    selected_article = request.GET.get('article', '')
    selected_client = request.GET.get('client', '')
    selected_period = request.GET.get('period', 'reporting_period')

    # АВТОПОДСТАНОВКА МЕНЕДЖЕРА ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ
    if not is_admin:
        if 'Менеджер' in df.columns:
            available_mgrs = [m for m in df['Менеджер'].dropna().unique() if manager_name.lower() in str(m).lower()]
            selected_manager = available_mgrs[0] if available_mgrs else manager_name
        else:
            selected_manager = manager_name

    # -------------------------------------------------------------
    # СОЗАВИСИМОЕ ФОРМИРОВАНИЕ СПИСКОВ ДЛЯ СЕЛЕКТОРОВ
    # -------------------------------------------------------------
    # 1. Список менеджеров
    managers_list = sorted([str(m) for m in df['Менеджер'].dropna().unique() if str(m).strip()]) if 'Менеджер' in df.columns else []

    # 2. Срез по менеджеру
    mgr_df = df.copy()
    if selected_manager:
        mgr_df = mgr_df[mgr_df['Менеджер'] == selected_manager]

    # 3. Список клиентов (все клиенты выбранного менеджера)
    clients_list = sorted([str(c) for c in mgr_df['Клиент'].dropna().unique() if str(c).strip()]) if 'Клиент' in mgr_df.columns else []

    # 4. Список номенклатуры (товары менеджера, но если выбран клиент — только его товары)
    art_df = mgr_df.copy()
    if selected_client:
        art_df = art_df[art_df['Клиент'] == selected_client]

    if 'Наименование' in art_df.columns and art_df['Наименование'].notna().any():
        articles_list = sorted([str(a) for a in art_df['Наименование'].dropna().unique() if str(a).strip()])
    elif 'Артикул' in art_df.columns:
        articles_list = sorted([str(a) for a in art_df['Артикул'].dropna().unique() if str(a).strip()])
    else:
        articles_list = []

    # Сброс невалидных параметров
    if selected_client and selected_client not in clients_list:
        selected_client = ''
    if selected_article and selected_article not in articles_list:
        selected_article = ''

    # 5. Опции селектора периодов
    available_years = sorted(df['Год'].dropna().unique().astype(int).tolist())
    reporting_month_name = MONTH_NAMES_RU.get(reporting_month, f'Месяц {reporting_month}')
    periods_options = [
        ('reporting_period', f'Отчётный период — {reporting_month_name} {reporting_year}'),
        ('all_time', 'За все время'),
    ]
    for y in available_years:
        periods_options.append((f'year_{y}', f'{y} год'))

    periods_df = df[['Год', 'Номер месяца', 'Месяц']].drop_duplicates().sort_values(by=['Год', 'Номер месяца'])
    for _, prow in periods_df.iterrows():
        y = int(prow['Год'])
        m_num = int(prow['Номер месяца'])
        m_name = prow['Месяц'] if pd.notna(prow['Месяц']) else f"Месяц {m_num}"
        periods_options.append((f"month_{y}_{m_num:02d}", f"{m_name} {y}"))

    # -------------------------------------------------------------
    # РАСЧЕТ ДАННЫХ ДЛЯ KPI И ГРАФИКОВ
    # -------------------------------------------------------------
    target_year = reporting_year
    target_month = reporting_month

    if selected_period in {'reporting_period', 'current_year'}:
        selected_period = 'reporting_period'
        year_slice = df[df['Год'] == target_year]
        period_df = year_slice[year_slice['Номер месяца'] == target_month]
        month_slice = period_df
        ytd_slice = year_slice[year_slice['Номер месяца'] <= target_month]
    elif selected_period == 'all_time':
        period_df = df
        month_slice = df[(df['Год'] == target_year) & (df['Номер месяца'] == target_month)]
        year_slice = df
        ytd_slice = df
    elif selected_period.startswith('year_'):
        target_year = int(selected_period.split('_')[1])
        period_df = df[df['Год'] == target_year]
        month_slice = period_df[period_df['Номер месяца'] == target_month]
        year_slice = period_df
        ytd_slice = period_df
    elif selected_period.startswith('month_'):
        _, y_str, m_str = selected_period.split('_')
        target_year = int(y_str)
        target_month = int(m_str)
        period_df = df[(df['Год'] == target_year) & (df['Номер месяца'] == target_month)]
        month_slice = period_df
        year_slice = df[df['Год'] == target_year]
        ytd_slice = year_slice[year_slice['Номер месяца'] <= target_month]
    else:
        period_df = df
        month_slice = df
        year_slice = df
        ytd_slice = df

    def apply_filters(source_df):
        f = source_df.copy()
        if selected_manager:
            f = f[f['Менеджер'] == selected_manager]
        if selected_client:
            f = f[f['Клиент'] == selected_client]
        if selected_article:
            if 'Наименование' in f.columns:
                f = f[f['Наименование'] == selected_article]
            elif 'Артикул' in f.columns:
                f = f[f['Артикул'] == selected_article]
        return f

    f_month_slice = apply_filters(month_slice)
    f_year_slice = apply_filters(year_slice)
    f_ytd_slice = apply_filters(ytd_slice)
    f_period_df = apply_filters(period_df)

    # 1. Расчет KPI
    m_aop = float(f_month_slice['AOP, CNY'].sum())
    m_forecast = float(f_month_slice['Прогноз, CNY'].sum())
    m_fact = float(f_month_slice['Факт, CNY'].sum())

    ytd_aop = float(f_ytd_slice['AOP, CNY'].sum())
    full_year_forecast = float(f_year_slice['Прогноз, CNY'].sum())
    ytd_fact = float(f_ytd_slice['Факт, CNY'].sum())

    pct_month_aop = (m_fact / m_aop * 100) if m_aop > 0 else 0.0
    pct_ytd_aop = (ytd_fact / ytd_aop * 100) if ytd_aop > 0 else 0.0
    ytd_forecast_sum = float(f_ytd_slice['Прогноз, CNY'].sum())
    pct_ytd_forecast = (ytd_fact / ytd_forecast_sum * 100) if ytd_forecast_sum > 0 else 0.0

    kpi = {
        'month_aop': format_currency(m_aop),
        'month_forecast': format_currency(m_forecast),
        'month_fact': format_currency(m_fact),
        'ytd_aop': format_currency(ytd_aop),
        'full_year_forecast': format_currency(full_year_forecast),
        'ytd_fact': format_currency(ytd_fact),
        'pct_month_aop': format_percent(pct_month_aop),
        'pct_ytd_aop': format_percent(pct_ytd_aop),
        'pct_ytd_forecast': format_percent(pct_ytd_forecast),
        'color_month_aop': get_percent_color(pct_month_aop),
        'color_ytd_aop': get_percent_color(pct_ytd_aop),
        'color_ytd_forecast': get_percent_color(pct_ytd_forecast),
    }

    # 2. Динамический график по менеджерам
    dyn_managers = []
    dyn_aop = []
    dyn_forecast = []
    dyn_fact = []

    if not f_period_df.empty and 'Менеджер' in f_period_df.columns:
        dyn_grp = f_period_df.groupby('Менеджер').agg({
            'AOP, CNY': 'sum',
            'Прогноз, CNY': 'sum',
            'Факт, CNY': 'sum'
        }).reset_index().sort_values(by='AOP, CNY', ascending=False)

        for _, r in dyn_grp.iterrows():
            dyn_managers.append(str(r['Менеджер']))
            dyn_aop.append(round(float(r['AOP, CNY']), 2))
            dyn_forecast.append(round(float(r['Прогноз, CNY']), 2))
            dyn_fact.append(round(float(r['Факт, CNY']), 2))

    # 3. Статичный график по 12 месяцам компании
    static_month_labels = [
        "01 Январь", "02 Февраль", "03 Март", "04 Апрель", "05 Май", "06 Июнь",
        "07 Июль", "08 Август", "09 Сентябрь", "10 Октябрь", "11 Ноябрь", "12 Декабрь"
    ]
    static_company_aop = [0.0] * 12
    static_company_forecast = [0.0] * 12
    static_company_fact = [0.0] * 12

    comp_year_df = df[df['Год'] == target_year] if target_year else df
    if not comp_year_df.empty:
        comp_m_grp = comp_year_df.groupby('Номер месяца').agg({
            'AOP, CNY': 'sum',
            'Прогноз, CNY': 'sum',
            'Факт, CNY': 'sum'
        }).reset_index()

        for _, r in comp_m_grp.iterrows():
            idx = int(r['Номер месяца']) - 1
            if 0 <= idx < 12:
                static_company_aop[idx] = round(float(r['AOP, CNY']), 2)
                static_company_forecast[idx] = round(float(r['Прогноз, CNY']), 2)
                static_company_fact[idx] = round(float(r['Факт, CNY']), 2)

    # Проверка, активен ли хотя бы один фильтр (для кнопки сброса)
    filters_active = bool(
        (is_admin and selected_manager) or
        selected_article or
        selected_client or
        (selected_period != 'reporting_period')
    )

    context = {
        'has_data': True,
        'is_admin': is_admin,
        'manager_name': manager_name,
        'managers_list': managers_list,
        'articles_list': articles_list,
        'clients_list': clients_list,
        'periods_options': periods_options,
        'selected_manager': selected_manager,
        'selected_article': selected_article,
        'selected_client': selected_client,
        'selected_period': selected_period,
        'filters_active': filters_active,
        'target_year': target_year,
        'reporting_period_label': f'{reporting_month_name} {reporting_year}',
        'kpi': kpi,
        'dyn_managers': json.dumps(dyn_managers, ensure_ascii=False),
        'dyn_aop': json.dumps(dyn_aop),
        'dyn_forecast': json.dumps(dyn_forecast),
        'dyn_fact': json.dumps(dyn_fact),
        'static_labels': json.dumps(static_month_labels, ensure_ascii=False),
        'static_aop': json.dumps(static_company_aop),
        'static_forecast': json.dumps(static_company_forecast),
        'static_fact': json.dumps(static_company_fact),
    }
    return render(request, 'accounts/dashboard.html', context)
