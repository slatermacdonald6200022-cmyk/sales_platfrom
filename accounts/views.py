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

DATA_DIR = Path(settings.BASE_DIR) / "data"
FINAL_DIR = DATA_DIR / "processed" / "final"


def get_latest_final_file():
    """Находит самый свежий файл витрины FINAL_SALES_FACT_TABLE.xlsx."""
    if not FINAL_DIR.exists():
        return None
    final_files = sorted(list(FINAL_DIR.glob("**/FINAL_SALES_FACT_TABLE.xlsx")), reverse=True)
    return final_files[0] if final_files else None


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


def logout_view(request):
    """Выход из системы."""
    logout(request)
    return redirect('login')


@login_required
def home_view(request):
    """Главная страница платформы."""
    return render(request, 'accounts/home.html')


@login_required
def profile_view(request):
    """Личный кабинет пользователя."""
    user = request.user
    profile = getattr(user, 'profile', None)
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
    is_admin = user.is_superuser or getattr(profile, 'role', '') in ['director', 'analyst']
    manager_name = getattr(profile, 'manager_name', '') or user.get_full_name() or user.username

    latest_file = get_latest_final_file()
    if not latest_file or not latest_file.exists():
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': 'Витрина данных еще не сформирована. Выполните обработку на Шаге 2.'
        })

    try:
        df = pd.read_excel(latest_file)
    except Exception as e:
        return render(request, 'accounts/dashboard.html', {
            'has_data': False,
            'message': f'Ошибка при чтении витрины данных: {str(e)}'
        })

    # Приведение числовых колонок
    for num_col in ['AOP, CNY', 'Прогноз, CNY', 'Факт, CNY', 'AOP, шт', 'Прогноз, шт', 'Факт, шт']:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0.0)

    if 'Год' in df.columns:
        df['Год'] = pd.to_numeric(df['Год'], errors='coerce').fillna(2026).astype(int)
    if 'Номер месяца' in df.columns:
        df['Номер месяца'] = pd.to_numeric(df['Номер месяца'], errors='coerce').fillna(1).astype(int)

    # Ограничение датафрейма для менеджера
    if not is_admin and manager_name and 'Менеджер' in df.columns:
        matched_rows = df['Менеджер'].astype(str).str.contains(manager_name, case=False, na=False)
        if matched_rows.any():
            df = df[matched_rows]

    # Получение параметров фильтрации из GET-запроса
    selected_manager = request.GET.get('manager', '')
    selected_article = request.GET.get('article', '')
    selected_client = request.GET.get('client', '')
    selected_period = request.GET.get('period', 'current_year')

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
    periods_options = [
        ('current_year', 'Весь 2026 год (Текущий)'),
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
    target_year = 2026
    target_month = 5

    if selected_period == 'current_year':
        target_year = 2026
        period_df = df[df['Год'] == target_year]
        month_slice = period_df[period_df['Номер месяца'] == target_month]
        year_slice = period_df
        ytd_slice = period_df[period_df['Номер месяца'] <= target_month]
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
        (selected_period != 'current_year')
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