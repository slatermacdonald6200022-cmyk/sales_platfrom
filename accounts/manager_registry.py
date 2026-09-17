"""One live roster for uploads, processing, permissions and reporting."""
from .models import SalesManager


def manager_dict(manager):
    return {'id': manager.key, 'name': manager.name,
            'username': manager.user.username if manager.user_id else manager.legacy_username,
            'user_id': manager.user_id, 'aliases': manager.aliases, 'department': manager.department}


def managers(active_only=True):
    query = SalesManager.objects.select_related('user')
    if active_only:
        query = query.filter(is_active=True)
    return [manager_dict(manager) for manager in query]


class ActiveManagers:
    def __iter__(self):
        return iter(managers())

    def __len__(self):
        return SalesManager.objects.filter(is_active=True).count()


def source_manager(filename):
    """Stable upload identifier takes precedence over a changeable display name."""
    if filename.startswith('plan_') and filename.count('_') >= 2:
        return SalesManager.objects.filter(key=filename.split('_', 2)[1]).first()
    return None


def active_source(filename):
    manager = source_manager(filename)
    if filename.startswith('plan_'):
        return bool(manager and manager.is_active)
    return manager is None or manager.is_active
