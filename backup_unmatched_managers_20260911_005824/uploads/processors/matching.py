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
    """Archive argument is accepted for compatibility but never used as input."""
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
    # Uploaded manager workbooks are the sole source of prior-period facts.
    plans['Факт, шт'] = pd.to_numeric(plans.get('Факт, шт', pd.Series(0.0, index=plans.index)), errors='coerce').fillna(0)
    if 'Факт, CNY' not in plans:
        plans['Факт, CNY'] = plans['Факт, шт'] * plans[PRICE].where(plans[PRICE] > 0)
        plans.loc[plans['Факт, шт'] == 0, 'Факт, CNY'] = 0.0
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
        # Replace, never accumulate, facts for the incoming report period only.
        plans.loc[plans['_key_month'].isin(current_periods), ['Факт, шт', 'Факт, CNY']] = 0.0
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
    current_unmatched = [row for row in checked if row['Причина']]
    unmatched = current_unmatched
    plans['Месяц'] = plans['Номер месяца'].map(MONTH_NAMES_RU)
    for col in TARGET:
        if col not in plans:
            plans[col] = ''
    result = plans[TARGET].copy()
    result.attrs['matching_report'] = {
        'total_rows': len(checked),
        'matched_rows': len(checked) - len(current_unmatched),
        'unmatched_rows': len(unmatched),
        'history_conflict_rows': 0,
        'retained_history_rows': 0,
        'history_conflicts': [],
        'matched_amount_cny': sum(row['Факт, CNY'] or 0 for row in checked if not row['Причина']),
        'unmatched_amount_cny': 0.0, 'unmatched_df': pd.DataFrame(unmatched),
        'missing_price_rows': sum(not row['Причина'] and row['Факт, CNY'] is None for row in checked),
    }
    return result
