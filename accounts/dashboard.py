"""Единые фильтры, показатели и проверяемые правила отклонений."""
import json
from pathlib import Path

import pandas as pd
from django.conf import settings

FIELDS = {'manager': 'Менеджер', 'client': 'Клиент', 'article': 'Артикул', 'period': '_period', 'department': 'Отдел'}
MONEY = ['AOP, CNY', 'Прогноз, CNY', 'Факт, CNY']
QUANTITY = ['AOP, шт', 'Прогноз, шт', 'Факт, шт']
PRICE = 'Цена, юань, без НДС 1 п/г 2026'
MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def money(value):
    return f'{float(value):,.0f}'.replace(',', ' ') + ' ¥'


def prepare_frame(frame):
    from .departments import department_for_manager
    df = frame.copy()
    for col in MONEY + QUANTITY + [PRICE]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0) if col in df else 0.0
    for col in ['Менеджер', 'Клиент', 'Артикул', 'Наименование', 'Класс товара', 'Производственный индекс']:
        df[col] = df[col].fillna('').astype(str).str.strip() if col in df else ''
    df['Артикул'] = df['Артикул'].str.replace(r'\.0$', '', regex=True)
    departments = {manager: department_for_manager(manager) for manager in df['Менеджер'].unique()}
    df['Отдел'] = df['Менеджер'].map(departments)
    for col in ['Год', 'Номер месяца']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df[df['Год'].between(2000, 2100) & df['Номер месяца'].between(1, 12)].copy()
    df[['Год', 'Номер месяца']] = df[['Год', 'Номер месяца']].astype(int)
    df['_period'] = df['Год'].astype(str) + '-' + df['Номер месяца'].astype(str).str.zfill(2)
    return df


def selections(params, df, reporting):
    selected = {key: list(dict.fromkeys(value for value in params.getlist(key) if value)) for key in FIELDS}
    requested = selected['period'] if 'period' in params else [reporting]
    months = set()
    for value in requested:
        if value in {'reporting_period', 'current_year'}:
            months.add(reporting)
        elif value == 'all_time':
            months.update(df['_period'])
        elif value.startswith('year_'):
            months.update(p for p in df['_period'] if p.startswith(value[5:] + '-'))
        elif value.startswith('month_'):
            months.add(value[6:].replace('_', '-'))
        else:
            months.add(value)
    selected['period'] = sorted(months)
    return selected


def apply_filters(df, selected, exclude=None):
    mask = pd.Series(True, index=df.index)
    for key, col in FIELDS.items():
        if key != exclude and selected[key]:
            mask &= df[col].isin(selected[key])
    return df.loc[mask]


def facet_options(df, selected):
    return {key: sorted(v for v in apply_filters(df, selected, exclude=key)[col].unique() if v)
            for key, col in FIELDS.items()}


def deviations(df, known_periods):
    low = float(getattr(settings, 'SALES_DEVIATION_LOW_RATIO', .5))
    high = float(getattr(settings, 'SALES_DEVIATION_HIGH_RATIO', 2.0))
    minimum = float(getattr(settings, 'SALES_DEVIATION_MIN_CNY', 1000))
    eligible = df[df['_period'].isin(known_periods)].copy()
    missing = ((eligible[PRICE] <= 0) & ((eligible[QUANTITY] != 0).any(axis=1)))
    eligible = eligible.loc[~missing]
    keys = ['Менеджер', 'Клиент', 'Артикул', 'Класс товара', 'Производственный индекс', '_period']
    grouped = eligible.groupby(keys, as_index=False).agg({'AOP, CNY': 'sum', 'Факт, CNY': 'sum', 'Наименование': 'first'})
    grouped['delta'] = grouped['Факт, CNY'] - grouped['AOP, CNY']
    found = []
    for row in grouped.to_dict('records'):
        plan, actual = row['AOP, CNY'], row['Факт, CNY']
        delta = actual - plan
        reason = ''
        if plan > 0 and actual == 0:
            reason = 'Нет учтённых продаж при плане'
        elif plan == 0 and actual > 0:
            reason = 'Продажи без плана'
        elif plan > 0 and actual / plan <= low and abs(delta) >= minimum:
            reason = 'Продажи ниже плана'
        elif plan > 0 and actual / plan >= high and abs(delta) >= minimum:
            reason = 'Продажи выше плана'
        if reason:
            found.append({'manager': row['Менеджер'], 'client': row['Клиент'], 'article': row['Артикул'],
                          'product': row['Наименование'], 'period': row['_period'], 'reason': reason,
                          'product_class': row['Класс товара'], 'production_index': row['Производственный индекс'],
                          'plan': money(plan), 'actual': money(actual), 'delta': money(delta),
                          'amount': abs(delta), 'ratio': f'{actual / plan:.0%}' if plan > 0 else '—'})
    found.sort(key=lambda row: row['amount'], reverse=True)
    contributors = []
    for col, title in [('Менеджер', 'Менеджеры'), ('Клиент', 'Клиенты'), ('Артикул', 'Товары')]:
        contribution = grouped.groupby(col)['delta'].sum()
        contribution = contribution.loc[contribution.abs().sort_values(ascending=False).index].head(5)
        contributors.append({'title': title, 'rows': [{'name': str(name), 'delta': money(value)}
                                                     for name, value in contribution.items() if value != 0]})
    return {'rows': found, 'count': len(found), 'contributors': contributors,
            'rule': f'Ниже {low:.0%} или выше {high:.0%} плана при разнице от {money(minimum)}; нулевые продажи и продажи без плана — всегда.',
            'unknown_periods': sorted(set(df['_period']) - set(known_periods))}


def build_context(frame, params, final_file, reporting, include_deviations=True):
    df = prepare_frame(frame)
    report_key = f'{reporting[0]:04d}-{reporting[1]:02d}'
    selected = selections(params, df, report_key)
    # Нулевые структурные месяцы нужны для соединения, но не расширяют фильтры.
    active = df[(df[MONEY + QUANTITY] != 0).any(axis=1)]
    available = facet_options(active, selected)
    all_options = {key: sorted(v for v in active[col].unique() if v) for key, col in FIELDS.items()}
    # Выбор, не имеющий совпадений, не сбрасывается в «вся компания».
    filters = []
    titles = {'manager': 'Менеджер', 'client': 'Клиент', 'article': 'Товар', 'period': 'Период', 'department': 'Отдел'}
    products = df.drop_duplicates('Артикул').set_index('Артикул')['Наименование'].to_dict()
    for key in FIELDS:
        options = []
        for value in sorted(set(all_options[key]) | set(selected[key])):
            label = value
            if key == 'article':
                label = f'{value} — {products.get(value, "")}'.rstrip(' —')
            elif key == 'period' and len(value) == 7 and value[4] == '-' and value[5:].isdigit() and 1 <= int(value[5:]) <= 12:
                label = f'{MONTHS[int(value[5:]) - 1]} {value[:4]}'
            options.append({'value': value, 'label': label, 'selected': value in selected[key], 'available': value in available[key]})
        filters.append({'key': key, 'title': titles[key], 'options': options, 'count': len(selected[key]),
                        'years': sorted({value[:4] for value in all_options['period']}) if key == 'period' else []})
    current = apply_filters(df, selected)
    entity = apply_filters(df, {**selected, 'period': []})
    years = sorted(current['Год'].unique())
    annual = entity[entity['Год'].isin(years)]
    max_months = current.groupby('Год')['Номер месяца'].max().to_dict()
    ytd = annual[annual['Номер месяца'] <= annual['Год'].map(max_months)]
    kpi = {'month_aop': money(current['AOP, CNY'].sum()), 'month_forecast': money(current['Прогноз, CNY'].sum()),
           'month_fact': money(current['Факт, CNY'].sum()), 'ytd_aop': money(ytd['AOP, CNY'].sum()),
           'full_year_forecast': money(annual['Прогноз, CNY'].sum()), 'ytd_fact': money(ytd['Факт, CNY'].sum())}
    def percent(numerator, denominator):
        return f'{numerator / denominator:.1%}' if denominator > 0 else '—'
    kpi.update(pct_month_aop=percent(current['Факт, CNY'].sum(), current['AOP, CNY'].sum()),
               pct_ytd_aop=percent(ytd['Факт, CNY'].sum(), ytd['AOP, CNY'].sum()),
               pct_ytd_forecast=percent(ytd['Факт, CNY'].sum(), ytd['Прогноз, CNY'].sum()))
    grouped = current.groupby('Менеджер')[MONEY].sum().sort_values('AOP, CNY', ascending=False)
    timeline = current.groupby('_period')[MONEY].sum().sort_index()
    department_totals = current.groupby('Отдел')[MONEY].sum()
    company_year = df[df['Год'] == reporting[0]].groupby('Номер месяца')[MONEY].sum().reindex(range(1, 13), fill_value=0)
    chart = {'managers': list(grouped.index), 'months': list(timeline.index),
             'departments': list(department_totals.index),
             'department_values': [department_totals[col].tolist() for col in MONEY],
             'company_year': int(reporting[0]), 'company_months': MONTHS,
             'company_values': [company_year[col].tolist() for col in MONEY],
             'manager_values': [grouped[col].tolist() for col in MONEY],
             'month_values': [timeline[col].tolist() for col in MONEY]}
    known = set(df.loc[(df['Факт, CNY'] != 0) | (df['Факт, шт'] != 0), '_period'])
    known.add(report_key)
    unmatched = 0
    try:
        metadata = json.loads(Path(final_file).with_name('processing_metadata.json').read_text(encoding='utf-8'))
        known.update(metadata.get('fact_periods', []))
        unmatched = metadata.get('unmatched_rows', 0)
    except (OSError, ValueError):
        pass
    missing = (current[PRICE] <= 0) & ((current[QUANTITY] != 0).any(axis=1))
    return {'has_data': True, 'filters': filters, 'selected': selected, 'kpi': kpi, 'chart': chart,
            'facet_rows': active[list(FIELDS.values())].drop_duplicates().values.tolist(),
            'anomalies': deviations(current, known) if include_deviations else None, 'missing_prices': int(missing.sum()),
            'unmatched_count': unmatched, 'empty_selection': current.empty,
            'reporting_period_label': f'{MONTHS[reporting[1] - 1]} {reporting[0]}',
            'dyn_managers': json.dumps(chart['managers'], ensure_ascii=False)}
