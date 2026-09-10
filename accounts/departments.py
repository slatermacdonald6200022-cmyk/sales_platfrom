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
    name = str(manager).lower().replace('ё', 'е')
    mapping = getattr(settings, 'SALES_MANAGER_DEPARTMENTS', DEFAULT_DEPARTMENTS)
    matches = {department for key, department in mapping.items()
               if re.search(r'(?<!\w)' + re.escape(key.lower().replace('ё', 'е')) + r'(?!\w)', name)}
    return next(iter(matches)) if len(matches) == 1 else 'Не определён'
