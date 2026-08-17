from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages


def login_view(request):
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
    logout(request)
    return redirect('login')


@login_required
def home_view(request):
    """Главный экран системы"""
    return render(request, 'home.html')


@login_required
def dashboard_view(request):
    """
    Экран дашборда: распределение по ролям или отображение дашборда
    """
    user = request.user
    role = getattr(getattr(user, 'profile', None), 'role', 'manager')

    # Если директор — показываем директорский дашборд, иначе — рабочий дашборд
    if role == 'director':
        template_name = 'accounts/director_dashboard.html'
    else:
        template_name = 'accounts/analyst_dashboard.html'

    context = {
        'role': role,
        'manager_name': getattr(getattr(user, 'profile', None), 'manager_name', user.username),
    }
    return render(request, template_name, context)


@login_required
def profile_view(request):
    """
    Личный кабинет пользователя с возможностью загрузки аватарки
    """
    user = request.user
    profile = getattr(user, 'profile', None)

    if request.method == 'POST':
        # Загрузка / обновление аватарки
        if 'avatar' in request.FILES and profile:
            profile.avatar = request.FILES['avatar']
            profile.save()
            messages.success(request, 'Аватарка успешно обновлена!')
            return redirect('profile')

    # Формируем ФИО и инициалы для заглушки (если фото еще не загружено)
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