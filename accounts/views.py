import os
import json
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.conf import settings

# Реестр менеджеров (Хуснутдинов строго без имени)
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
    Аналитическая панель показателей (BI): 4 селектора, 3 группы KPI и графики.
    """
    user = request.user
    profile = getattr(user, 'profile', None)
    is_admin = user.is_superuser or (profile and profile.role in ['director', 'analyst'])
    manager_name = getattr(profile, 'manager_name', None) or user.get_full_name() or user.username

    # Получаем выбранные значения селекторов из GET-запроса
    selected_manager = request.GET.get('manager', 'all')
    selected_product = request.GET.get('product', 'all')
    selected_client = request.GET.get('client', 'all')
    selected_period = request.GET.get('period', '2026-06')

    # Каркас структуры KPI показателей
    kpi = {
        # Месячные показатели
        'month_aop_cny': None,
        'month_forecast_cny': None,
        'month_fact_cny': None,
        # Годовые показатели
        'ytd_aop_cny': None,
        'yee_forecast_cny': None,
        'ytd_fact_cny': None,
        # Процентные показатели
        'pct_month_fact_aop': None,
        'pct_ytd_fact_aop': None,
        'pct_ytd_fact_forecast': None,
    }

    # Списки для заполнения селекторов
    products_list = []
    clients_list = []
    periods_list = [
        {'id': '2026-01', 'name': 'Январь 2026'},
        {'id': '2026-02', 'name': 'Февраль 2026'},
        {'id': '2026-03', 'name': 'Март 2026'},
        {'id': '2026-04', 'name': 'Апрель 2026'},
        {'id': '2026-05', 'name': 'Май 2026'},
        {'id': '2026-06', 'name': 'Июнь 2026'},
    ]

    # Каркас для графиков Chart.js
    chart_months = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    chart_aop = []
    chart_forecast = []
    chart_fact = []

    # Попытка чтения данных из итоговой витрины ETL (если файл существует)
    fact_file = os.path.join(settings.BASE_DIR, 'data', 'processed', 'FINAL_SALES_FACT_TABLE.xlsx')
    if os.path.exists(fact_file):
        try:
            df = pd.read_excel(fact_file)
            if 'Клиент' in df.columns:
                clients_list = sorted(df['Клиент'].dropna().unique().tolist())
            if 'Номенклатура' in df.columns:
                products_list = sorted(df['Номенклатура'].dropna().unique().tolist())
            elif 'Артикул' in df.columns:
                products_list = sorted(df['Артикул'].dropna().astype(str).unique().tolist())
        except Exception:
            pass

    context = {
        'is_admin': is_admin,
        'manager_name': manager_name,
        'managers_list': MANAGERS_LIST,
        'products_list': products_list,
        'clients_list': clients_list,
        'periods_list': periods_list,
        'selected_manager': selected_manager,
        'selected_product': selected_product,
        'selected_client': selected_client,
        'selected_period': selected_period,
        'kpi': kpi,
        'chart_labels': json.dumps(chart_months),
        'chart_aop': json.dumps(chart_aop),
        'chart_forecast': json.dumps(chart_forecast),
        'chart_fact': json.dumps(chart_fact),
    }
    return render(request, 'accounts/dashboard.html', context)


@login_required
def profile_view(request):
    user = request.user
    profile = getattr(user, 'profile', None)

    if request.method == 'POST' and 'avatar' in request.FILES and profile:
        profile.avatar = request.FILES['avatar']
        profile.save()
        messages.success(request, 'Фото профиля успешно обновлено.')
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