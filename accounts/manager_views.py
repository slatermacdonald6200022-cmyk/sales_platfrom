import uuid
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied
from django.db import transaction, IntegrityError
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST, require_http_methods
from .models import SalesManager, Profile


class ManagerForm(forms.Form):
    name = forms.CharField(label='Имя менеджера', max_length=255)
    department = forms.CharField(label='Отдел', max_length=120, required=False)
    username = forms.CharField(label='Логин', max_length=150,
                               validators=User._meta.get_field('username').validators)
    email = forms.EmailField(label='Почта', required=False)
    password = forms.CharField(label='Новый пароль', required=False, widget=forms.PasswordInput,
                               help_text='При добавлении обязателен. При редактировании оставьте пустым, чтобы сохранить пароль.')

    def __init__(self, *args, manager=None, **kwargs):
        self.manager = manager
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean(self):
        data = super().clean()
        username = data.get('username')
        name = data.get('name')
        if name and SalesManager.objects.filter(name__iexact=name).exclude(
                pk=self.manager.pk if self.manager else None).exists():
            self.add_error('name', 'Менеджер с таким именем уже существует.')
        user_id = self.manager.user_id if self.manager else None
        if username and User.objects.filter(username__iexact=username).exclude(pk=user_id).exists():
            self.add_error('username', 'Этот логин уже занят.')
        if username and SalesManager.objects.filter(legacy_username__iexact=username).exclude(
                pk=self.manager.pk if self.manager else None).exists():
            self.add_error('username', 'Логин закреплён за другим менеджером.')
        password = data.get('password')
        if not user_id and not password:
            self.add_error('password', 'Укажите пароль для нового аккаунта.')
        if password:
            try:
                validate_password(password, User(username=username or '', email=data.get('email', '')))
            except forms.ValidationError as exc:
                self.add_error('password', exc)
        return data


def superuser_only(user):
    if not user.is_superuser:
        raise PermissionDenied('Управление менеджерами доступно только суперпользователю.')


@login_required
def manager_list(request):
    superuser_only(request.user)
    return render(request, 'accounts/manager_list.html', {
        'managers': SalesManager.objects.select_related('user').all(),
        'active_count': SalesManager.objects.filter(is_active=True).count(),
    })


@login_required
def manager_edit(request, pk=None):
    superuser_only(request.user)
    manager = get_object_or_404(SalesManager.objects.select_related('user'), pk=pk) if pk else None
    if manager and manager.user_id and (manager.user.is_superuser or manager.user.is_staff or manager.user.profile.role != 'manager'):
        raise PermissionDenied('Эта запись связана со служебным аккаунтом. Изменение запрещено.')
    initial = {}
    if manager:
        initial = {'name': manager.name, 'department': manager.department,
                   'username': manager.user.username if manager.user_id else manager.legacy_username,
                   'email': manager.user.email if manager.user_id else ''}
    form = ManagerForm(request.POST if request.method == 'POST' else None, initial=initial, manager=manager)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                data = form.cleaned_data
                if manager:
                    manager = SalesManager.objects.select_for_update().get(pk=manager.pk)
                else:
                    manager = SalesManager(key='m' + uuid.uuid4().hex)
                user = manager.user if manager.user_id else User()
                # Privileges are never exposed through this form.
                if user.pk and (user.is_superuser or user.is_staff or user.profile.role != 'manager'):
                    raise PermissionDenied('Изменение служебного аккаунта запрещено.')
                user.username = data['username']
                user.email = data['email']
                user.is_active = manager.is_active
                if data['password']:
                    user.set_password(data['password'])
                user.save()
                Profile.objects.update_or_create(user=user, defaults={'role': 'manager', 'manager_name': data['name']})
                manager.aliases = list(dict.fromkeys(v for v in manager.aliases + [manager.name, data['name']] if v))
                manager.name = data['name']
                manager.department = data['department']
                manager.user = user
                manager.legacy_username = user.username
                manager.save()
            messages.success(request, 'Менеджер сохранён. Список загрузки обновлён.')
            return redirect('manage_managers')
        except IntegrityError:
            form.add_error(None, 'Логин уже занят. Обновите страницу и выберите другой.')
    return render(request, 'accounts/manager_form.html', {'form': form, 'manager': manager})


@login_required
@require_POST
def manager_toggle(request, pk):
    superuser_only(request.user)
    with transaction.atomic():
        manager = get_object_or_404(SalesManager.objects.select_for_update(), pk=pk)
        action = request.POST.get('action')
        if action not in {'disable', 'enable'}:
            raise PermissionDenied('Неизвестное действие.')
        manager.is_active = action == 'enable'
        if manager.user_id:
            user = User.objects.select_for_update().get(pk=manager.user_id)
            if user.is_superuser or user.is_staff or user.profile.role != 'manager':
                raise PermissionDenied('Отключение служебного аккаунта здесь запрещено.')
            user.is_active = manager.is_active
            user.save(update_fields=['is_active'])
        manager.save(update_fields=['is_active'])
    messages.success(request, 'Менеджер включён.' if manager.is_active else
                     'Менеджер отключён. Вход закрыт, его файл больше не требуется. Архив сохранён.')
    return redirect('manage_managers')


class DeleteManagerForm(forms.Form):
    confirmation = forms.CharField(label='Подтверждение', max_length=255,
                                   widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}))

    def __init__(self, *args, expected, **kwargs):
        self.expected = expected
        super().__init__(*args, **kwargs)

    def clean_confirmation(self):
        value = self.cleaned_data['confirmation']
        if value != self.expected:
            raise forms.ValidationError('Введите указанное значение точно, чтобы подтвердить удаление.')
        return value


def _check_manager_deletion(manager, user, actor):
    if user and (user.pk == actor.pk or user.is_superuser or user.is_staff or
                 not Profile.objects.filter(user=user, role='manager').exists()):
        raise PermissionDenied('Удаление своего или служебного аккаунта через эту страницу запрещено.')


@login_required
@require_http_methods(['GET', 'POST'])
def manager_delete(request, pk):
    superuser_only(request.user)
    # The target and account are re-read and checked within the deletion transaction.
    with transaction.atomic():
        manager = get_object_or_404(SalesManager.objects.select_for_update(), pk=pk)
        user = User.objects.select_for_update().get(pk=manager.user_id) if manager.user_id else None
        _check_manager_deletion(manager, user, request.user)
        expected = user.username if user else manager.name
        form = DeleteManagerForm(request.POST if request.method == 'POST' else None, expected=expected)
        if request.method == 'POST' and form.is_valid():
            name = manager.name
            manager.delete()
            if user:
                user.delete()
            messages.success(request, f'Менеджер «{name}» удалён'
                             + (' вместе с аккаунтом. ' if user else '. ')
                             + 'Исходные файлы и архивные отчёты сохранены.')
            return redirect('manage_managers')
        context = {'manager': manager, 'account': user, 'expected': expected, 'form': form}
    return render(request, 'accounts/manager_delete.html', context)
