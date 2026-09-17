"""Отделы из итоговых блоков плана продаж от 09.09.2026.

Лист 2026: U1529 (строки 6:267), U1531 (269:1383), U1533 (1385:1523).
Настройка SALES_MANAGER_DEPARTMENTS позволяет переопределить справочник.
"""
import re
from django.conf import settings

DEFAULT_DEPARTMENTS = {
    'царев': 'Truck&BUS', 'фомичев': 'Truck&BUS', 'хуснутдинов': 'Truck&BUS',
    'редько': 'Truck&BUS', 'поляков': 'Truck&BUS',
    'хорошевский': 'Trailers', 'измайлов': 'Trailers', 'мустафин': 'Trailers',
    'прасолов': 'Trailers', 'соловьев': 'Trailers', 'ушаков': 'Aftermarket',
}


def department_for_manager(manager):
    from .manager_registry import managers
    name = str(manager).lower().replace('ё', 'е')
    registered = managers(active_only=False)
    exact = {m['department'] or 'Не определён' for m in registered
             if name in {str(v).lower().replace('ё', 'е') for v in [m['name']] + m['aliases']}}
    if exact:
        return next(iter(exact)) if len(exact) == 1 else 'Не определён'
    tokens = {m['department'] or 'Не определён' for m in registered
              if any(re.search(r'(?<!\w)' + re.escape(v.lower().replace('ё', 'е')) + r'(?!\w)', name)
                     for v in m['aliases'] if v)}
    if tokens:
        return next(iter(tokens)) if len(tokens) == 1 else 'Не определён'
    mapping = getattr(settings, 'SALES_MANAGER_DEPARTMENTS', DEFAULT_DEPARTMENTS)
    matches = {department for key, department in mapping.items()
               if re.search(r'(?<!\w)' + re.escape(key.lower().replace('ё', 'е')) + r'(?!\w)', name)}
    return next(iter(matches)) if len(matches) == 1 else 'Не определён'
