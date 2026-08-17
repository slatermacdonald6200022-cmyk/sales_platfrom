import os
import json
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.conf import settings


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
    return render(request, 'home.html')


@login_required
def dashboard_view(request):
    """
    Аналитический дашборд: расчет KPI, графиков и план-факт витрины
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    is_admin = user.is_superuser or (profile and profile.role in ['director', 'analyst'])
    manager_name = getattr(profile, 'manager_name', None) or user.get_full_name() or user.username

    # Ищем итоговую витрину после ETL обработки
    fact_table_path = os.path.join(settings.BASE_DIR, 'data', 'processed', 'FINAL_SALES_FACT_TABLE.xlsx')
    if not os.path.exists(fact_table_path):
        fact_table_path = os.path.join(settings.BASE_DIR, 'results', 'FINAL_SALES_FACT_TABLE.xlsx')

    # 1. Значения по умолчанию (базовые показатели из диплома)
    months_labels = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    aop_monthly = [450, 480, 510, 490, 520, 550, 530, 540, 560, 580, 570, 600]
    forecast_monthly = [440, 475, 500, 485, 515, 540, 525, 535, 550, 570, 565, 590]
    fact_monthly = [410, 460, 495, 470, 500, 530, 490, 510, 520, 540, 530, 560]

    kpi = {
        'aop_qty': 5720000,
        'forecast_qty': 5680500,
        'fact_qty': 4910200,
        'aop_cny': 1430000000,
        'forecast_cny': 1420125000,
        'fact_cny': 1227550000,
        'exec_aop_qty': 85.8,
        'exec_forecast_qty': 86.4,
        'exec_aop_cny': 85.8,
        'exec_forecast_cny': 86.4,
    }

    managers_table = [
        {'name': 'Царев Михаил', 'aop': 850000, 'forecast': 840000, 'fact': 760000, 'cny': 190000000, 'pct': 90.5},
        {'name': 'Хуснутдинов Рамиль', 'aop': 920000, 'forecast': 910000, 'fact': 780000, 'cny': 195000000, 'pct': 85.7},
        {'name': 'Фомичев Владимир', 'aop': 640000, 'forecast': 635000, 'fact': 560000, 'cny': 140000000, 'pct': 88.2},
        {'name': 'Ушаков Алексей', 'aop': 710000, 'forecast': 700000, 'fact': 590000, 'cny': 147500000, 'pct': 84.3},
        {'name': 'Редько', 'aop': 520000, 'forecast': 515000, 'fact': 440000, 'cny': 110000000, 'pct': 85.4},
        {'name': 'Измайлов', 'aop': 480000, 'forecast': 475000, 'fact': 410000, 'cny': 102500000, 'pct': 86.3},
        {'name': 'Мустафин', 'aop': 550000, 'forecast': 545000, 'fact': 470000, 'cny': 117500000, 'pct': 86.2},
        {'name': 'Поляков', 'aop': 490000, 'forecast': 485000, 'fact': 420000, 'cny': 105000000, 'pct': 86.6},
        {'name': 'Прасолов Н. / Соловьёв В.', 'aop': 560000, 'forecast': 555000, 'fact': 480000, 'cny': 120000000, 'pct': 86.5},
    ]

    # 2. Если файл от ETL уже существует — считываем реальные агрегаты
    if os.path.exists(fact_table_path):
        try:
            df = pd.read_excel(fact_table_path)
            # При наличии колонок пересчитываем суммы
            if 'План AOP' in df.columns and 'Факт' in df.columns:
                aop_sum = df['План AOP'].sum()
                forecast_sum = df['Прогноз Forecast'].sum() if 'Прогноз Forecast' in df.columns else aop_sum
                fact_sum = df['Факт'].sum()

                kpi['aop_qty'] = int(aop_sum)
                kpi['forecast_qty'] = int(forecast_sum)
                kpi['fact_qty'] = int(fact_sum)
                kpi['exec_aop_qty'] = round((fact_sum / aop_sum * 100), 1) if aop_sum else 0
                kpi['exec_forecast_qty'] = round((fact_sum / forecast_sum * 100), 1) if forecast_sum else 0
        except Exception:
            pass

    context = {
        'is_admin': is_admin,
        'manager_name': manager_name,
        'kpi': kpi,
        'managers_table': managers_table,
        'chart_labels': json.dumps(months_labels),
        'chart_aop': json.dumps(aop_monthly),
        'chart_forecast': json.dumps(forecast_monthly),
        'chart_fact': json.dumps(fact_monthly),
        'mgr_labels': json.dumps([m['name'] for m in managers_table]),
        'mgr_pcts': json.dumps([m['pct'] for m in managers_table]),
    }
    return render(request, 'accounts/dashboard.html', context)


@login_required
def profile_view(request):
    user = request.user
    profile = getattr(user, 'profile', None)

    if request.method == 'POST' and 'avatar' in request.FILES and profile:
        profile.avatar = request.FILES['avatar']
        profile.save()
        messages.success(request, 'Аватарка успешно обновлена!')
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