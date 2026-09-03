import os
import re
import datetime
import pandas as pd
import numpy as np

BASE_COLUMN_ALIASES = {
    'client': ['клиент', 'контрагент', 'покупатель', 'client', 'наименование клиента'],
    'manager': ['менеджер', 'ответственный', 'manager', 'фио'],
    'supplier': ['поставщик', 'завод', 'бренд', 'производитель', 'supplier'],
    'product_article': ['номер изделия', 'артикул', 'код товара', 'код', 'article', 'номенклатура'],
    'product_name': ['наименование', 'описание', 'товар', 'название'],
    'price_cny': ['цена', 'цена, юань', 'цена юань', 'price', 'price cny', 'cny', 'цена, cny', 'цена, юань, без ндс']
}

TOTAL_KEYWORDS = [
    'итого', 'всего', 'total', 'сумма', 'баланс', 'год',
    '2024 год', '2025 год', '2026 год', '2027 год', '2028 год',
    'руб', 'rub', 'выручка', 'план, руб', 'факт, руб'
]

MONTH_RU_TO_NUM = {
    'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'мая': 5,
    'июн': 6, 'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
}

MONTH_NUM_TO_NAME = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
}


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


def parse_month_cell(val):
    """Распознает дату месяца в ячейке шапки."""
    if pd.isna(val):
        return None
    if isinstance(val, (datetime.datetime, pd.Timestamp)):
        return val.strftime('%Y-%m')

    s = str(val).strip()
    m_match = re.search(r'(\d{4})[-_/](\d{1,2})', s)
    if m_match:
        y, m = m_match.groups()
        return f"{int(y):04d}-{int(m):02d}"

    s_low = s.lower()
    for ru_m, num in MONTH_RU_TO_NUM.items():
        if ru_m in s_low:
            y_match = re.search(r'20\d{2}', s)
            year = y_match.group(0) if y_match else "2026"
            return f"{year}-{num:02d}"
    return None


def parse_metric_type(val):
    """Определяет тип метрики: AOP, Forecast или Fact."""
    if pd.isna(val):
        return None
    s = str(val).lower()

    # Игнорируем годовые итоговые и рублевые колонки
    if any(k in s for k in TOTAL_KEYWORDS):
        return 'ignore'
    if 'прогноз' in s or 'forecast' in s or 'план-прогноз' in s:
        return 'forecast'
    if 'аор' in s or 'aop' in s or 'план' in s:
        return 'aop'
    if 'факт' in s or 'fact' in s or 'actual' in s:
        return 'fact'
    if 'ком' in s or 'comment' in s:
        return 'ignore'
    return None


def extract_manager_from_filename(filename):
    fname = filename.lower()
    mapping = {
        'tsarev': 'Царев Михаил', 'царев': 'Царев Михаил',
        'khusnutdinov': 'Хуснутдинов А.', 'хуснутдинов': 'Хуснутдинов А.',
        'redko': 'Редько Вадим', 'редько': 'Редько Вадим',
        'izmaylov': 'Измайлов', 'измайлов': 'Измайлов',
        'mustafin': 'Мустафин Ринат', 'мустафин': 'Мустафин Ринат',
        'polyakov': 'Поляков Андрей', 'поляков': 'Поляков Андрей',
        'prasolov': 'Прасолов Николай, Соловьёв Виктор', 'прасолов': 'Прасолов Николай, Соловьёв Виктор',
        'соловьев': 'Прасолов Николай, Соловьёв Виктор', 'соловьёв': 'Прасолов Николай, Соловьёв Виктор',
        'fomichev': 'Фомичев Владимир', 'фомичев': 'Фомичев Владимир',
        'khoroshevsky': 'Александр Хорошевский', 'хорошевский': 'Александр Хорошевский',
        'ushakov': 'Ушаков Алексей', 'ушаков': 'Ушаков Алексей',
    }
    for k, v in mapping.items():
        if k in fname:
            return v
    return "Неизвестен"


def process_manager_sheet(df, filename="", default_manager=""):
    """Разворачивание одного листа книги менеджера в плоскую таблицу (UNPIVOT)."""
    header_idx = None
    for i in range(min(15, len(df))):
        row_str = " ".join([str(x).lower() for x in df.iloc[i].dropna()])
        if ('артикул' in row_str or 'номер изделия' in row_str or 'код' in row_str) and \
                ('клиент' in row_str or 'контрагент' in row_str or 'покупатель' in row_str):
            header_idx = i
            break

    if header_idx is None:
        header_idx = 0

    header_row_main = df.iloc[header_idx]
    header_row_sub = df.iloc[header_idx + 1] if header_idx + 1 < len(df) else pd.Series(dtype=object)

    base_cols = {}
    for col_idx in range(df.shape[1]):
        val = str(header_row_main.iloc[col_idx]).lower()
        for field, aliases in BASE_COLUMN_ALIASES.items():
            if field not in base_cols and any(a in val for a in aliases):
                base_cols[field] = col_idx

    data_df = df.iloc[header_idx + 2:].copy() if header_idx + 1 < len(df) else df.iloc[header_idx + 1:].copy()

    month_blocks = []
    current_month = None

    for col_idx in range(df.shape[1]):
        top_val = header_row_main.iloc[col_idx]
        sub_val = header_row_sub.iloc[col_idx] if len(header_row_sub) > col_idx else ""

        top_str = str(top_val).lower() if pd.notna(top_val) else ""
        sub_str = str(sub_val).lower() if pd.notna(sub_val) else ""

        # Сброс месяца при обнаружении итоговых колонок
        if any(k in top_str for k in ['итого', 'всего', 'total', 'год', 'сумма', 'руб', 'rub']):
            current_month = None

        detected_m = parse_month_cell(top_val)
        if detected_m:
            current_month = detected_m

        metric = parse_metric_type(sub_val) or parse_metric_type(top_val)

        if current_month and metric in ['aop', 'forecast', 'fact']:
            month_blocks.append({
                'month': current_month,
                'metric': metric,
                'col_idx': col_idx
            })

    if not month_blocks or 'product_article' not in base_cols:
        return pd.DataFrame()

    client_col = base_cols.get('client')
    if client_col is not None:
        data_df.iloc[:, client_col] = data_df.iloc[:, client_col].ffill()

    supplier_col = base_cols.get('supplier')
    if supplier_col is not None:
        data_df.iloc[:, supplier_col] = data_df.iloc[:, supplier_col].ffill()

    manager_val = default_manager or extract_manager_from_filename(filename)
    is_ushakov = "ушаков" in manager_val.lower() or "ушаков" in filename.lower()

    records = []
    for _, row in data_df.iterrows():
        art = normalize_article(row.iloc[base_cols['product_article']])
        if not art or art.lower() in ['nan', 'none', 'итого', 'всего', '']:
            continue

        raw_client = normalize_text(row.iloc[client_col]) if client_col is not None else "Не указан"
        if not raw_client or raw_client.lower() in ['nan', 'none', 'итого', 'всего']:
            continue

        if is_ushakov or ';' in raw_client or len(raw_client) > 100:
            client_name = "Клиенты Ушакова (Пул)"
        else:
            client_name = raw_client

        supp = normalize_text(row.iloc[supplier_col]) if supplier_col is not None else ""
        prod_name = normalize_text(row.iloc[base_cols['product_name']]) if 'product_name' in base_cols else ""

        price_val = 0.0
        if 'price_cny' in base_cols:
            try:
                p_str = str(row.iloc[base_cols['price_cny']]).replace(' ', '').replace(',', '.')
                price_val = float(p_str)
            except Exception:
                price_val = 0.0

        row_months = {}
        for block in month_blocks:
            m = block['month']
            met = block['metric']
            val = row.iloc[block['col_idx']]
            try:
                num_val = float(str(val).replace(' ', '').replace(',', '.')) if pd.notna(val) else 0.0
            except Exception:
                num_val = 0.0

            if m not in row_months:
                row_months[m] = {'aop': 0.0, 'forecast': 0.0, 'fact': 0.0}
            row_months[m][met] = num_val

        for m_str, vals in row_months.items():
            # Если все три показателя равны 0 — пропускаем пустой месяц
            if vals['aop'] == 0 and vals['forecast'] == 0 and vals['fact'] == 0:
                continue

            y_str, m_num = m_str.split('-')
            records.append({
                'AOP, шт': vals['aop'],
                'Прогноз, шт': vals['forecast'],
                'Факт, шт': vals['fact'],
                'Ключ клиента': client_name.lower(),
                'Клиент': client_name,
                'Менеджер': manager_val,
                'Год': int(y_str),
                'Поставщик': supp,
                'Наименование': prod_name,
                'Артикул': art,
                'Цена, юань, без НДС 1 п/г 2026': price_val,
                'Месяц': m_str,
                'Номер месяца': int(m_num)
            })

    return pd.DataFrame(records)


def normalize_all_managers(raw_dir="data/raw"):
    """Сканирует папку с планами менеджеров и объединяет их в плоскую таблицу."""
    if not os.path.exists(raw_dir):
        for fallback in ["data/raw_plans", "data/raw", "data/raw_managers"]:
            if os.path.exists(fallback):
                raw_dir = fallback
                break
        else:
            os.makedirs(raw_dir, exist_ok=True)
            return pd.DataFrame()

    all_dfs = []
    files = [
        f for f in os.listdir(raw_dir)
        if (f.startswith('plan_') or 'план' in f.lower() or 'ушаков' in f.lower() or 'aop' in f.lower())
           and (f.endswith('.xlsx') or f.endswith('.xls'))
    ]

    if not files:
        return pd.DataFrame()

    for fname in files:
        fpath = os.path.join(raw_dir, fname)
        try:
            excel_file = pd.ExcelFile(fpath)
            mgr_name = extract_manager_from_filename(fname)

            for sheet in excel_file.sheet_names:
                if any(s in sheet.lower() for s in ['свод', 'итог', 'сводная', 'лист1', 'sheet1']) and len(
                        excel_file.sheet_names) > 1:
                    continue
                df_sheet = pd.read_excel(excel_file, sheet_name=sheet, header=None)
                res_df = process_manager_sheet(df_sheet, filename=fname, default_manager=mgr_name)
                if not res_df.empty:
                    all_dfs.append(res_df)
        except Exception as e:
            print(f"❌ Ошибка при обработке {fname}: {e}")

    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()
