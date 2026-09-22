"""Сопоставление фактов с единственной строкой плана, без размножения сумм."""
from collections import defaultdict
import re
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


def merge(plans_df, actuals_df, history=None, manual_mappings=None):
    """Archive argument is accepted for compatibility but never used as input."""
    from .snapshot_engine import clean_key, clean_client_key, parse_period_key, filter_1c_trash, is_ushakov_client, MONTH_NAMES_RU
    plans = plans_df.copy().reset_index(drop=True)
    manual_mappings = manual_mappings or {}
    actuals = filter_1c_trash(actuals_df.copy()) if actuals_df is not None else pd.DataFrame()
    ushakov = plans['Клиент'].eq('Клиенты Ушакова (Пул)') | plans['Менеджер'].astype(str).str.contains('Ушаков', case=False, na=False)
    pool_names = []
    if '_Исходные клиенты' in plans:
        for value in plans.loc[ushakov, '_Исходные клиенты'].dropna().unique():
            pool_names.extend(name.strip() for name in re.split(r'[;\n]+', str(value)) if name.strip())
    pool_keys = set(clean_client_key(pd.Series(pool_names, dtype=str))) - {''}
    other_clients = set(clean_client_key(plans.loc[~ushakov, 'Клиент']))
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

    def mapping_key(row):
        return '|'.join(str(row.get(name, '') or '').strip().casefold()
                        for name in ['_source_client', '_key_article', '_key_code', '_key_month', 'Факт, шт'])

    def manual_candidates(row):
        target = manual_mappings.get(mapping_key(row))
        if not isinstance(target, dict):
            return []
        result = list(plans.index)
        for key, value in {
            '_key_client_canonical': target.get('client'), '_key_article': target.get('article'),
            '_key_code': target.get('code'), '_key_month': target.get('period'),
            '_Класс товара': target.get('product_class'), '_Производственный индекс': target.get('production_index'),
        }.items():
            if value not in (None, '') and key in plans:
                wanted = clean_client_key(pd.Series([value])).iloc[0] if key == '_key_client_canonical' else str(value).strip().casefold()
                result = [i for i in result if (str(plans.at[i, key]).strip().casefold() == wanted)]
        return result

    checked = []
    diagnostics = []
    current_periods = set()
    if not actuals.empty:
        actuals = actuals.rename(columns={
            'client': 'Клиент', 'product_article': 'Артикул', 'actual_qty_1c': 'Факт, шт',
            'product_code': CODE, 'actual_revenue_1c': 'Факт, CNY',
            'actual_qty': 'Факт, шт', 'actual_revenue': 'Факт, CNY'})
        if CODE not in actuals:
            actuals[CODE] = ''
        actuals['_source_client'] = actuals['Клиент']
        if '_Исходные клиенты' in plans:
            client_keys = clean_client_key(actuals['Клиент'])
            mask = client_keys.isin(pool_keys) & ~client_keys.isin(other_clients)
        else:
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
            {'Клиент': 'first', '_source_client': lambda values: '; '.join(dict.fromkeys(values.astype(str))),
             'Артикул': 'first', CODE: 'first', '_key_article': 'first', '_key_code': 'first', 'Факт, шт': 'sum'}
        )
        clients = set(plans['_key_client'])
        canonical_clients = set(plans['_key_client_canonical'])
        article_pairs = set(zip(plans['_key_client'], plans['_key_article']))
        code_pairs = set(zip(plans['_key_client'], plans['_key_code']))
        canonical_article_pairs = set(zip(plans['_key_client_canonical'], plans['_key_article']))
        canonical_code_pairs = set(zip(plans['_key_client_canonical'], plans['_key_code']))
        for row in grouped.to_dict('records'):
            indices = manual_candidates(row) or candidates(row)
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
            # Locate the source without guessing ownership from a product alone.
            relevant = indices or base_candidates(row)
            basis = 'Клиент, товар и период'
            if not relevant:
                relevant = plans.index[plans['_key_client'] == row['_key_client']].tolist()
                if not relevant:
                    relevant = plans.index[plans['_key_client_canonical'] == row['_key_client_canonical']].tolist()
                in_period = [i for i in relevant if plans.at[i, '_key_month'] == row['_key_month']]
                relevant = in_period or relevant
                basis = 'По клиенту: товар или период не сопоставлен'
            def source_values(column):
                if column not in plans:
                    return ''
                return '; '.join(sorted({str(plans.at[i, column]) for i in relevant
                                         if pd.notna(plans.at[i, column]) and str(plans.at[i, column]).strip()}))
            locations = []
            for i in (relevant if basis == 'Клиент, товар и период' else []):
                if '_Исходная строка' in plans and pd.notna(plans.at[i, '_Исходная строка']):
                    locations.append(f"{plans.at[i, '_Исходный файл'] if '_Исходный файл' in plans else ''} / "
                                     f"{plans.at[i, '_Исходный лист'] if '_Исходный лист' in plans else ''} / "
                                     f"строка {int(plans.at[i, '_Исходная строка'])}")
            checked.append({'Тип строки': 'Текущая выгрузка',
                            'Менеджер': source_values('Менеджер') or 'Не определён: клиент не найден',
                            'Файл менеджера': source_values('_Исходный файл'),
                            'Где искать': '; '.join(dict.fromkeys(locations)),
                            'Основание поиска': basis if relevant else 'Нет клиента в загруженных планах',
                            'Клиент из выгрузки': row['_source_client'], 'Артикул из выгрузки': row['Артикул'],
                            'Код товара из выгрузки': row[CODE],
                            'Период': row['_key_month'], 'Факт, шт': row['Факт, шт'], 'Факт, CNY': calculated_amount,
                            **{col: row['_' + col] for col in EXTRA}, 'Причина': reason})
            if reason or calculated_amount is None:
                diagnostic_indices = indices or base_candidates(row)
                candidate_basis = 'Совпадение клиента и товара за период'
                if not diagnostic_indices:
                    # Article fallback is diagnostic only: never writes quantities.
                    diagnostic_indices = index_article.get((row['_key_client'], row['_key_article'], row['_key_month']), [])
                    diagnostic_indices = diagnostic_indices or canonical_article.get(
                        (row['_key_client_canonical'], row['_key_article'], row['_key_month']), [])
                    candidate_basis = 'Одинаковый артикул у клиента за период. Коды требуют проверки'
                def value(i, column):
                    v = plans.at[i, column] if column in plans else None
                    if v is None or pd.isna(v):
                        return None
                    return v.item() if hasattr(v, 'item') else v
                diagnostics.append({
                    'mapping_key': mapping_key(row), 'client': row['_source_client'], 'article': row['Артикул'],
                    'product_code': row[CODE], 'period': row['_key_month'], 'quantity': row['Факт, шт'],
                    'product_class': row['_Класс товара'], 'production_index': row['_Производственный индекс'],
                    'manager': source_values('Менеджер') or 'Не определён',
                    'source_files': source_values('_Исходный файл'),
                    'reason': reason or 'Не указана цена за период',
                    'candidate_basis': candidate_basis,
                    'candidates': [{
                        'manager': value(i, 'Менеджер'), 'client': value(i, 'Клиент'),
                        'article': value(i, 'Артикул'), 'product_code': value(i, CODE),
                        'product_class': value(i, 'Класс товара'),
                        'production_index': value(i, 'Производственный индекс'),
                        'price': value(i, PRICE) if value(i, PRICE) and value(i, PRICE) > 0 else None,
                        'forecast_quantity': value(i, 'Прогноз, шт'), 'aop_quantity': value(i, 'AOP, шт'),
                        'source_file': value(i, '_Исходный файл'), 'source_sheet': value(i, '_Исходный лист'),
                        'source_row': value(i, '_Исходная строка'),
                    } for i in diagnostic_indices],
                })
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
        'diagnostics': diagnostics,
    }
    return result
