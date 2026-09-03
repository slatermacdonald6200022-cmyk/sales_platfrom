"""Единые правила доступа для пользовательских ролей платформы."""

ROLE_ADMIN = "admin"
ROLE_ANALYST = "analyst"
ROLE_DIRECTOR = "director"
ROLE_MANAGER = "manager"


def get_user_role(user):
    """Возвращает фактическую роль пользователя с учётом системного администратора."""
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return ROLE_ADMIN

    profile = getattr(user, "profile", None)
    profile_role = getattr(profile, "role", None)
    if profile_role in {ROLE_ANALYST, ROLE_DIRECTOR, ROLE_MANAGER}:
        return profile_role

    # Совместимость со служебными аккаунтами Django без профиля.
    if user.is_staff:
        return ROLE_ANALYST
    return ROLE_MANAGER


def can_manage_files(user):
    """Загрузка общих файлов, обработка и доступ к итоговому набору данных."""
    return get_user_role(user) in {ROLE_ADMIN, ROLE_ANALYST}


def can_view_company_dashboard(user):
    """Просмотр показателей по всей компании."""
    return get_user_role(user) in {ROLE_ADMIN, ROLE_ANALYST, ROLE_DIRECTOR}


def can_view_final_dataset(user):
    """Просмотр и скачивание подробной итоговой таблицы."""
    return get_user_role(user) in {ROLE_ADMIN, ROLE_ANALYST}


def can_compare_snapshots(user):
    """Сравнение сохранённых версий прогноза."""
    return get_user_role(user) in {
        ROLE_ADMIN,
        ROLE_ANALYST,
        ROLE_DIRECTOR,
        ROLE_MANAGER,
    }


def is_manager(user):
    return get_user_role(user) == ROLE_MANAGER
