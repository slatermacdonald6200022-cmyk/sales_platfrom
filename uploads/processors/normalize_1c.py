import os
import re
import pandas as pd


def normalize_text(val):
    if pd.isna(val):
        return ""
    return str(val).strip().replace('\n', ' ').replace('\r', ' ')


def normalize_article(val):
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s.strip()


def normalize_1c_file(file_path):
    """Парсит отчет валовой прибыли 1C ERP и возвращает плоскую таблицу."""
    if not file_path or not os.path.exists(file_path):
        return pd.DataFrame()

    df_raw = pd.read_excel(file_path, header=None)

    # 1. Поиск периода в шапке
    period_year = 2026
    period_month = 5
    for i in range(min(15, len(df_raw))):
        line = " ".join([str(x) for x in df_raw.iloc[i].dropna()])
        date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', line)
        if date_match:
            _, m, y = date_match.groups()
            period_month = int(m)
            period_year = int(y)
            break

    # 2. Поиск строки заголовков таблицы
    header_idx = None
    for i in range(min(25, len(df_raw))):
        row_str = " ".join([str(x).lower() for x in df_raw.iloc[i].dropna()])
        if ('клиент' in row_str or 'контрагент' in row_str or 'номенклатура' in row_str) and \
           ('количество' in row_str or 'выручка' in row_str or 'продажи' in row_str):
            header_idx = i
            break

    if header_idx is None:
        header_idx = 8

    header_row = df_raw.iloc[header_idx]
    col_client_item = 0
    col_qty = None
    col_cny = None

    for c in range(df_raw.shape[1]):
        val = str(header_row.iloc[c]).lower()
        if 'количество' in val or 'кол-во' in val:
            if col_qty is None:
                col_qty = c
        if 'выручка' in val or 'сумма' in val or 'cny' in val or 'юан' in val:
            if col_cny is None:
                col_cny = c

    if col_qty is None:
        col_qty = 3
    if col_cny is None:
        col_cny = 4

    rows_data = df_raw.iloc[header_idx + 1:].copy()
    records = []
    current_client = None

    for _, row in rows_data.iterrows():
        first_cell = normalize_text(row.iloc[col_client_item])
        if not first_cell or 'итого' in first_cell.lower():
            continue

        try:
            qty_val = float(str(row.iloc[col_qty]).replace(' ', '').replace(',', '.'))
        except Exception:
            qty_val = 0.0

        try:
            cny_val = float(str(row.iloc[col_cny]).replace(' ', '').replace(',', '.'))
        except Exception:
            cny_val = 0.0

        # Определение строки клиента (без количества, длинный текст)
        if pd.isna(row.iloc[col_qty]) or (qty_val == 0 and cny_val == 0 and len(first_cell) > 3):
            current_client = first_cell
            continue

        if current_client:
            art = normalize_article(first_cell)
            records.append({
                'Клиент': current_client,
                'Артикул': art,
                'Наименование': first_cell,
                'Год': period_year,
                'Номер месяца': period_month,
                'Месяц': f"{period_year}-{period_month:02d}",
                'Факт, шт': qty_val,
                'Факт, CNY': cny_val
            })

    return pd.DataFrame(records)


# Алиас для совместимости
parse_1c_fact_file = normalize_1c_file
process_1c_file = normalize_1c_file