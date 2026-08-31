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
    """Форматирует число с разделением тысяч: 12 345 678 ¥."""
    if val is None or np.isnan(val) or val == 0:
        return "0 ¥"
    return f"{val:,.0f}".replace(",", " ") + " ¥"


def format_percent(val):
    """Форматирует процент: 86.4%."""
    if val is None or np.isnan(val) or val == 0:
        return "0.0%"
    return f"{val:.1f}%"


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = authenticate(username=form.cleaned_data.get('username'), password=form.cleaned_data.get('password'))
            if user:
                login(request, user)
                return redirect('home')
    else:
        form = AuthenticationForm()
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def home_view(request):
    return render(request, 'accounts/home.html')


@login_required
def profile_view(request):
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
    Интерактивный аналитический дашборд:
    - 4 селектора (Менеджер, Артикул/Номенклатура, Клиент, Период)
    - 9 карточек KPI (Месячные, Годовые, Процентные)
    - 2 интерактивных графика Chart.js
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

    # Ограничение видимости для роли менеджера
    if not is_admin and manager_name:
        if 'Менеджер' in df.columns:
            df = df[df['Менеджер'].astype(str).str.contains(manager_name, case=False, na=False)]

    # Списки для 4-х селекторов
    managers_list = sorted(
        [str(m) for m in df['Менеджер'].dropna().unique() if str(m).strip()]) if 'Менеджер' in df.columns else []
    clients_list = sorted(
        [str(c) for c in df['Клиент'].dropna().unique() if str(c).strip()]) if 'Клиент' in df.columns else []

    # Наименования / Артикулы
    if 'Наименование' in df.columns and df['Наименование'].notna().any():
        articles_list = sorted([str(a) for a in df['Наименование'].dropna().unique() if str(a).strip()])
    elif 'Артикул' in df.columns:
        articles_list = sorted([str(a) for a in df['Артикул'].dropna().unique() if str(a).strip()])
    else:
        articles_list = []

    # Формирование списка периодов (Год-Месяц)
    periods_df = df[['Год', 'Номер месяца', 'Месяц']].drop_duplicates().sort_values(by=['Год', 'Номер месяца'])
    periods_list = []
    for _, prow in periods_df.iterrows():
        y = int(prow['Год'])
        m_num = int(prow['Номер месяца'])
        m_name = prow['Месяц'] if pd.notna(prow['Месяц']) else f"Месяц {m_num}"
        val = f"{y}-{m_num:02d}"
        label = f"{m_name} {y}"
        periods_list.append((val, label))

    # Получение значений фильтров из GET-параметров
    selected_manager = request.GET.get('manager', '')
    selected_article = request.GET.get('article', '')
    selected_client = request.GET.get('client', '')
    selected_period = request.GET.get('period', '')

    # Значение периода по умолчанию — последний доступный период
    if not selected_period and periods_list:
        selected_period = periods_list[-1][0]

    # Парсим выбранный Год и Месяц
    try:
        sel_year, sel_month = map(int, selected_period.split('-'))
    except Exception:
        sel_year, sel_month = 2026, 5

    # Фильтрация среза
    filtered_df = df.copy()
    if selected_manager:
        filtered_df = filtered_df[filtered_df['Менеджер'] == selected_manager]
    if selected_client:
        filtered_df = filtered_df[filtered_df['Клиент'] == selected_client]
    if selected_article:
        if 'Наименование' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['Наименование'] == selected_article]
        elif 'Артикул' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['Артикул'] == selected_article]

    # 1. МЕСЯЧНЫЕ ПОКАЗАТЕЛИ (за выбранный месяц sel_year, sel_month)
    month_df = filtered_df[(filtered_df['Год'] == sel_year) & (filtered_df['Номер месяца'] == sel_month)]
    m_aop = float(month_df['AOP, CNY'].sum())
    m_forecast = float(month_df['Прогноз, CNY'].sum())
    m_fact = float(month_df['Факт, CNY'].sum())

    # 2. ГОДОВЫЕ ПОКАЗАТЕЛИ (за выбранный sel_year)
    year_df = filtered_df[filtered_df['Год'] == sel_year]
    ytd_df = year_df[year_df['Номер месяца'] <= sel_month]
    ytd_aop = float(ytd_df['AOP, CNY'].sum())
    ytd_fact = float(ytd_df['Факт, CNY'].sum())
    full_year_forecast = float(year_df['Прогноз, CNY'].sum())

    # 3. ПРОЦЕНТНЫЕ ПОКАЗАТЕЛИ (%)
    pct_fact_aop_month = (m_fact / m_aop * 100) if m_aop > 0 else 0.0
    pct_fact_aop_ytd = (ytd_fact / ytd_aop * 100) if ytd_aop > 0 else 0.0
    ytd_forecast_sum = float(ytd_df['Прогноз, CNY'].sum())
    pct_fact_forecast_ytd = (ytd_fact / ytd_forecast_sum * 100) if ytd_forecast_sum > 0 else 0.0

    kpi = {
        'month_aop': format_currency(m_aop),
        'month_forecast': format_currency(m_forecast),
        'month_fact': format_currency(m_fact),
        'ytd_aop': format_currency(ytd_aop),
        'full_year_forecast': format_currency(full_year_forecast),
        'ytd_fact': format_currency(ytd_fact),
        'pct_fact_aop_month': format_percent(pct_fact_aop_month),
        'pct_fact_aop_ytd': format_percent(pct_fact_aop_ytd),
        'pct_fact_forecast_ytd': format_percent(pct_fact_forecast_ytd),
    }

    # 4. ДАННЫЕ ДЛЯ ГРАФИКОВ (12 месяцев выбранного года)
    chart_labels = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    chart_aop = [0.0] * 12
    chart_forecast = [0.0] * 12
    chart_fact = [0.0] * 12

    if not year_df.empty:
        monthly_grp = year_df.groupby('Номер месяца').agg({
            'AOP, CNY': 'sum',
            'Прогноз, CNY': 'sum',
            'Факт, CNY': 'sum'
        }).reset_index()

        for _, r in monthly_grp.iterrows():
            idx = int(r['Номер месяца']) - 1
            if 0 <= idx < 12:
                chart_aop[idx] = round(float(r['AOP, CNY']), 2)
                chart_forecast[idx] = round(float(r['Прогноз, CNY']), 2)
                chart_fact[idx] = round(float(r['Факт, CNY']), 2)

    # Бублик исполнения годового прогноза
    fact_val = round(ytd_fact, 2)
    remain_val = round(max(0.0, full_year_forecast - ytd_fact), 2)
    chart_doughnut = [fact_val, remain_val] if (fact_val + remain_val) > 0 else [0, 100]

    context = {
        'has_data': True,
        'manager_name': manager_name,
        'managers_list': managers_list,
        'articles_list': articles_list,
        'clients_list': clients_list,
        'periods_list': periods_list,
        'selected_manager': selected_manager,
        'selected_article': selected_article,
        'selected_client': selected_client,
        'selected_period': selected_period,
        'kpi': kpi,
        'chart_labels': json.dumps(chart_labels, ensure_ascii=False),
        'chart_aop': json.dumps(chart_aop),
        'chart_forecast': json.dumps(chart_forecast),
        'chart_fact': json.dumps(chart_fact),
        'chart_doughnut': json.dumps(chart_doughnut),
    }
    return render(request, 'accounts/dashboard.html', context)