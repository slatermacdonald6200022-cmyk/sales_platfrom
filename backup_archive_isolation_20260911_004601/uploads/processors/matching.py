"""Сопоставление фактов с единственной строкой плана, без размножения сумм."""
from collections import defaultdict
import pandas as pd

EXTRA = ['Класс товара', 'Производственный индекс']
CODE = 'Код товара'
PRICE = 'Цена, юань, без НДС 1 п/г 2026'
TARGET = ['AOP, CNY', 'Прогноз, CNY', 'Факт, CNY', 'AOP, шт', 'Прогноз, шт', 'Факт, шт',
          PRICE, 'Год', 'Артикул', CODE, 'Месяц', 'Номер месяца', 'Клиент', 'Менеджер', 'Поставщик', 'Наименование'] + EXTRA


class HistoryMappingError(ValueError):
    """Все препятствия переносу истории, а не только первая проблемная строка."""
    def __init__(self, conflicts):
        self.conflicts = conflicts
        first = conflicts[0]
        super().__init__(
            f'Не удалось перенести исторические факты: {len(conflicts)} позиций. '
            f'Например: {first["client"]}, артикул {first["article"]}, '
            f'код товара {first.get("product_code") or "не указан"}, '
            f'период {first["period"]}: {first["reason"]}. '
            'Предыдущий результат не изменён. Откройте список препятствий в записи обработки.'
        )


def merge(plans_df, actuals_df, history=None):
    from .snapshot_engine import clean_key, clean_client_key, parse_period_key, filter_1c_trash, is_ushakov_client, MONTH_NAMES_RU
    plans = plans_df.copy().reset_index(drop=True)
    actuals = filter_1c_trash(actuals_df.copy()) if actuals_df is not None else pd.DataFrame()
    plans.loc[plans['Менеджер'].astype(str).str.contains('Ушаков', case=False, na=False), 'Клиент'] = 'Клиенты Ушакова (Пул)'
    for col in EXTRA:
        if col not in plans:
            plans[col] = ''
    if CODE not in plans:
        plans[CODE] = ''
    for col in ['AOP, шт', 'Прогноз, шт', PRICE]:
        plans[col] = pd.to_numeric(plans[col], errors='coerce').fillna(0)
    plans['AOP, CNY'] = plans['AOP, шт'] * plans[PRICE]
    plans['Прогноз, CNY'] = plans['Прогноз, шт'] * plans[PRICE]
    plans['Факт, шт'] = 0.0
    plans['Факт, CNY'] = 0.0
    plans['_key_client'] = clean_key(plans['Клиент'])
    plans['_key_client_canonical'] = clean_client_key(plans['Клиент'])
    plans['_key_article'] = clean_key(plans['Артикул'])
    plans['_key_code'] = clean_key(plans[CODE])
    plans['_key_month'] = parse_period_key(plans)
    for col in EXTRA:
        plans['_' + col] = clean_key(plans[col])
    index_article = defaultdict(list)
    index_code = defaultdict(list)
    canonical_article = defaultdict(list)
    canonical_code = defaultdict(list)
    for idx, row in plans.iterrows():
        index_article[(row['_key_client'], row['_key_article'], row['_key_month'])].append(idx)
        canonical_article[(row['_key_client_canonical'], row['_key_article'], row['_key_month'])].append(idx)
        if row['_key_code']:
            index_code[(row['_key_client'], row['_key_code'], row['_key_month'])].append(idx)
            canonical_code[(row['_key_client_canonical'], row['_key_code'], row['_key_month'])].append(idx)

    def base_candidates(row):
        if row.get('_key_code', ''):
            exact = index_code.get((row['_key_client'], row['_key_code'], row['_key_month']), [])
            return exact or canonical_code.get((row['_key_client_canonical'], row['_key_code'], row['_key_month']), [])
        exact = index_article.get((row['_key_client'], row['_key_article'], row['_key_month']), [])
        return exact or canonical_article.get((row['_key_client_canonical'], row['_key_article'], row['_key_month']), [])

    def candidates(row):
        indices = list(base_candidates(row))
        for col in EXTRA:
            value = row.get('_' + col, '')
            if value:
                indices = [i for i in indices if plans.at[i, '_' + col] == value]
        return indices

    checked = []
    history_conflicts = []
    retained_history_rows = []
    current_periods = set()
    if not actuals.empty:
        actuals = actuals.rename(columns={
            'client': 'Клиент', 'product_article': 'Артикул', 'actual_qty_1c': 'Факт, шт',
            'product_code': CODE, 'actual_revenue_1c': 'Факт, CNY',
            'actual_qty': 'Факт, шт', 'actual_revenue': 'Факт, CNY'})
        if CODE not in actuals:
            actuals[CODE] = ''
        mask = actuals['Клиент'].apply(is_ushakov_client)
        actuals.loc[mask, 'Клиент'] = 'Клиенты Ушакова (Пул)'
        actuals['_key_client'] = clean_key(actuals['Клиент'])
        actuals['_key_client_canonical'] = clean_client_key(actuals['Клиент'])
        actuals['_key_article'] = clean_key(actuals['Артикул'])
        actuals['_key_code'] = clean_key(actuals[CODE])
        actuals['_key_product'] = actuals['_key_article'].map(lambda value: 'article:' + value)
        code_mask = actuals['_key_code'] != ''
        actuals.loc[code_mask, '_key_product'] = 'code:' + actuals.loc[code_mask, '_key_code']
        actuals['_key_month'] = parse_period_key(actuals)
        current_periods = set(actuals['_key_month'])
        for col in EXTRA:
            actuals['_' + col] = clean_key(actuals[col]) if col in actuals else ''
        keys = ['_key_client', '_key_client_canonical', '_key_product', '_key_month'] + ['_' + c for c in EXTRA]
        actuals['Факт, шт'] = pd.to_numeric(actuals['Факт, шт'], errors='coerce').fillna(0)
        grouped = actuals.groupby(keys, as_index=False, dropna=False).agg(
            {'Клиент': 'first', 'Артикул': 'first', CODE: 'first', '_key_article': 'first', '_key_code': 'first', 'Факт, шт': 'sum'}
        )
        clients = set(plans['_key_client'])
        canonical_clients = set(plans['_key_client_canonical'])
        article_pairs = set(zip(plans['_key_client'], plans['_key_article']))
        code_pairs = set(zip(plans['_key_client'], plans['_key_code']))
        canonical_article_pairs = set(zip(plans['_key_client_canonical'], plans['_key_article']))
        canonical_code_pairs = set(zip(plans['_key_client_canonical'], plans['_key_code']))
        for row in grouped.to_dict('records'):
            indices = candidates(row)
            reason = ''
            calculated_amount = None
            if len(indices) == 1:
                idx = indices[0]
                plans.at[idx, 'Факт, шт'] += row['Факт, шт']
                price = float(plans.at[idx, PRICE])
                if price > 0:
                    calculated_amount = row['Факт, шт'] * price
                    plans.at[idx, 'Факт, CNY'] += calculated_amount
                else:
                    plans.at[idx, 'Факт, CNY'] = float('nan')
            elif len(indices) > 1:
                reason = 'Несколько строк плана: нужны класс товара и производственный индекс или уточнение дублей'
            elif row['_key_client'] not in clients and row['_key_client_canonical'] not in canonical_clients:
                reason = 'Клиент не найден в планах'
            elif row['_key_code'] and (row['_key_client'], row['_key_code']) not in code_pairs and \
                    (row['_key_client_canonical'], row['_key_code']) not in canonical_code_pairs:
                reason = 'Код товара не найден у клиента'
            elif not row['_key_code'] and (row['_key_client'], row['_key_article']) not in article_pairs and \
                    (row['_key_client_canonical'], row['_key_article']) not in canonical_article_pairs:
                reason = 'Артикул не найден у клиента'
            elif not base_candidates(row):
                reason = 'Нет строки плана за этот период'
            else:
                reason = 'Не совпали класс товара или производственный индекс'
            checked.append({'Тип строки': 'Текущая выгрузка',
                            'Клиент из выгрузки': row['Клиент'], 'Артикул из выгрузки': row['Артикул'],
                            'Код товара из выгрузки': row[CODE],
                            'Период': row['_key_month'], 'Факт, шт': row['Факт, шт'], 'Факт, CNY': calculated_amount,
                            **{col: row['_' + col] for col in EXTRA}, 'Причина': reason})
    if history is not None and not history.empty:
        plan_clients = set(plans['_key_client'])
        article_periods = defaultdict(set)
        code_periods = defaultdict(set)
        for client, article, period in index_article:
            article_periods[(client, article)].add(period)
        for client, code, period in index_code:
            code_periods[(client, code)].add(period)
        old = history.copy()
        if '_key_client_canonical' not in old:
            old['_key_client_canonical'] = (clean_client_key(old['Клиент']) if 'Клиент' in old
                                            else old['_key_client'])
        if CODE not in old:
            old[CODE] = ''
        old['_key_code'] = clean_key(old[CODE])
        for col in EXTRA:
            old['_' + col] = clean_key(old[col]) if col in old else ''
        for row in old.to_dict('records'):
            if row['_key_month'] in current_periods:
                continue
            indices = candidates(row)
            if len(indices) == 1:
                for col in ['Факт, шт', 'Факт, CNY']:
                    plans.at[indices[0], col] += float(row[col])
            elif any(float(row[col]) != 0 for col in ['Факт, шт', 'Факт, CNY']):
                base = list(base_candidates(row))
                pair_key = (row['_key_client'], row['_key_code'] or row['_key_article'])
                pair_periods = code_periods if row['_key_code'] else article_periods
                missing = [col for col in EXTRA if not row.get('_' + col)]
                if len(indices) > 1:
                    reason = f'Подходят несколько строк плана ({len(indices)})'
                    hint = ('В истории не заполнены: ' + ', '.join(missing) + '. Уточните значения по первичным данным.'
                            if missing else 'Полный ключ повторяется в планах. Проверьте дубли и принадлежность менеджерам.')
                elif base:
                    reason = 'Не совпали класс товара или производственный индекс'
                    hint = 'Сравните поля исторической записи с вариантами нового плана ниже.'
                elif row['_key_client'] not in plan_clients:
                    reason = 'Клиент отсутствует в новых планах'
                    hint = 'Проверьте наименование клиента и полноту загруженных файлов.'
                elif pair_key not in pair_periods:
                    reason = ('Код товара отсутствует у этого клиента' if row['_key_code']
                              else 'Артикул отсутствует у этого клиента')
                    hint = 'Проверьте код товара или артикул и наличие товарной строки у клиента.'
                else:
                    reason = 'В новом плане нет строки за этот месяц'
                    hint = 'Проверьте, включает ли новый план исторический период.'
                conflict = {
                    'client': str(row.get('Клиент', row['_key_client'])),
                    'article': str(row.get('Артикул', row['_key_article'])),
                    'product_code': str(row.get(CODE, '')),
                    'period': row['_key_month'],
                    'quantity': float(row['Факт, шт']),
                    'amount': float(row['Факт, CNY']),
                    'product_class': row.get('_Класс товара', ''),
                    'production_index': row.get('_Производственный индекс', ''),
                    'reason': reason, 'hint': hint, 'matching_count': len(indices),
                    'available_periods': sorted(pair_periods.get(pair_key, [])),
                    'candidates': [{'manager': str(plans.at[i, 'Менеджер']),
                                    'product_code': str(plans.at[i, CODE]),
                                    'product_class': plans.at[i, '_Класс товара'],
                                    'production_index': plans.at[i, '_Производственный индекс'],
                                    'source_file': str(plans.at[i, '_Исходный файл']) if '_Исходный файл' in plans else '',
                                    'source_sheet': str(plans.at[i, '_Исходный лист']) if '_Исходный лист' in plans else '',
                                    'source_row': int(plans.at[i, '_Исходная строка']) if '_Исходная строка' in plans else None}
                                   for i in base],
                }
                history_conflicts.append(conflict)

                year, month = (int(part) for part in row['_key_month'].split('-'))
                candidate_managers = {
                    str(plans.at[i, 'Менеджер']) for i in base
                    if str(plans.at[i, 'Менеджер']).strip()
                }
                retained_history_rows.append({
                    'AOP, CNY': 0.0,
                    'Прогноз, CNY': 0.0,
                    'Факт, CNY': float(row['Факт, CNY']),
                    'AOP, шт': 0.0,
                    'Прогноз, шт': 0.0,
                    'Факт, шт': float(row['Факт, шт']),
                    PRICE: float(row.get(PRICE, 0) or 0),
                    'Год': year,
                    'Номер месяца': month,
                    'Артикул': str(row.get('Артикул', row['_key_article'])),
                    CODE: str(row.get(CODE, '')),
                    'Клиент': str(row.get('Клиент', row['_key_client'])),
                    'Менеджер': str(row.get('Менеджер', '') or (
                        next(iter(candidate_managers)) if len(candidate_managers) == 1 else 'Не определён'
                    )),
                    'Поставщик': str(row.get('Поставщик', '') or ''),
                    'Наименование': str(row.get('Наименование', '') or ''),
                    **{col: str(row.get(col, '') or '') for col in EXTRA},
                })
    current_unmatched = [row for row in checked if row['Причина']]
    history_unmatched = [{
        'Тип строки': 'Исторический факт сохранён без перераспределения',
        'Клиент из выгрузки': item['client'],
        'Артикул из выгрузки': item['article'],
        'Код товара из выгрузки': item['product_code'],
        'Период': item['period'],
        'Факт, шт': item['quantity'],
        'Факт, CNY': item['amount'],
        'Класс товара': item['product_class'],
        'Производственный индекс': item['production_index'],
        'Причина': item['reason'],
        'Что проверить': item['hint'],
    } for item in history_conflicts]
    unmatched = current_unmatched + history_unmatched
    plans['Месяц'] = plans['Номер месяца'].map(MONTH_NAMES_RU)
    if retained_history_rows:
        retained = pd.DataFrame(retained_history_rows)
        retained['Месяц'] = retained['Номер месяца'].map(MONTH_NAMES_RU)
        plans = pd.concat([plans, retained], ignore_index=True, sort=False)
    for col in TARGET:
        if col not in plans:
            plans[col] = ''
    result = plans[TARGET].copy()
    result.attrs['matching_report'] = {
        'total_rows': len(checked) + len(history_conflicts),
        'matched_rows': len(checked) - len(current_unmatched),
        'unmatched_rows': len(unmatched),
        'history_conflict_rows': len(history_conflicts),
        'retained_history_rows': len(retained_history_rows),
        'history_conflicts': history_conflicts,
        'matched_amount_cny': sum(row['Факт, CNY'] or 0 for row in checked if not row['Причина']),
        'unmatched_amount_cny': 0.0, 'unmatched_df': pd.DataFrame(unmatched),
        'missing_price_rows': sum(not row['Причина'] and row['Факт, CNY'] is None for row in checked),
    }
    return result
