import os
import re
import datetime
import pandas as pd
import numpy as np

BASE_COLUMN_ALIASES = {
    'product_class': ['класс товара'],
    'production_index': ['производственный индекс', 'при'],
    'product_code': ['код из 1с', 'код 1с', 'код товара'],
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

# Поля каталога комплектов. Они добавляются к строкам плана как справочная
# информация и не меняют количество/стоимость до тех пор, пока факт явно не
# пришёл по артикулу комплекта.
BUNDLE_COLUMNS = {
    'bundle_article': 'Артикул комплекта',
    'bundle_component_qty': 'Количество в комплекте',
    'bundle_flag': 'Признак комплекта',
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
        return f"{int(y):04d}-{int(m):02d}" if 1 <= int(m) <= 12 else None

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
    s = str(val).lower().replace('\n', ' ')

    # Денежные, процентные и сценарные колонки не являются количеством.
    if any(k in s for k in ['cny', 'юан', '%', 'негатив', 'накоп', 'продажи', 'цена', 'валют']):
        return 'ignore'

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


def identify_base_columns(values):
    """Приоритет смысловых заголовков, а не первого вхождения слова «товар»."""
    texts = [normalize_text(v).lower().replace('ё', 'е') for v in values]
    result = {}
    for field, aliases in BASE_COLUMN_ALIASES.items():
        candidates = [(i, text) for i, text in enumerate(texts)
                      if any(alias in text for alias in aliases)]
        if field == 'product_article':
            candidates = [(i, t) for i, t in candidates if 'артикул' in t or 'номер изделия' in t or t in {'article', 'код товара'}]
        elif field == 'product_name':
            candidates = [(i, t) for i, t in candidates if 'наименование' in t or t in {'описание', 'товар', 'название'}]
            candidates.sort(key=lambda pair: (0 if 'рабочее наименование' in pair[1] else 1 if pair[1] == 'наименование' else 2))
        elif field == 'price_cny':
            candidates = [(i, t) for i, t in candidates if 'цена' in t and ('юань' in t or 'cny' in t)]
        elif field == 'production_index':
            candidates = [(i, t) for i, t in candidates if t in {'производственный индекс', 'при'}]
        elif field == 'product_code':
            candidates = [(i, t) for i, t in candidates
                          if t in {'код из 1с', 'код 1с', 'код товара', 'код из 1 c'}]
        if candidates:
            result[field] = candidates[0][0]
    return result


def price_columns(df, header_idx):
    """Возвращает явно подписанные цены CNY с годом и полугодием."""
    result = []
    for col, value in enumerate(df.iloc[header_idx]):
        text = normalize_text(value).lower()
        below = normalize_text(df.iloc[header_idx + 1, col]).lower() if header_idx + 1 < len(df) else ''
        if 'цена' not in text and 'цена' in below:
            text = text + ' ' + below
        if 'цена' not in text or not ('юань' in text or 'cny' in text):
            continue
        # Ближайшая надпись над ценой имеет приоритет над общей шапкой.
        label = text
        if not re.search(r'20\d{2}', label):
            for row in range(header_idx - 1, max(-1, header_idx - 3), -1):
                candidate = normalize_text(df.iloc[row, col]).lower()
                if re.search(r'20\d{2}', candidate):
                    label = candidate
                    break
        year_match = re.search(r'20\d{2}', label)
        year = int(year_match.group()) if year_match else None
        half = 0
        if re.search(r'\b(?:ii|2)\s*(?:пол|п/г|$)', label) or re.search(r'20\d{2}\s*-\s*ii\b', label):
            half = 2
        elif re.search(r'\b(?:i|1)\s*(?:пол|п/г|$)', label) or re.search(r'20\d{2}\s*-\s*i\b', label):
            half = 1
        result.append((col, year, half))
    return result


def numeric_cell(value):
    if pd.isna(value) or value == '':
        return 0.0
    try:
        result = float(str(value).replace(' ', '').replace('\xa0', '').replace(',', '.'))
        return result if np.isfinite(result) else 0.0
    except (ValueError, TypeError):
        return 0.0


def extract_bundle_catalog(df):
    """Читает нормализованный лист состава комплектов ``S_ВСЕ``.

    Формат файла Прасолова допускает служебные строки перед заголовком, поэтому
    заголовок ищется по смысловым названиям колонок. Возвращается словарь
    ``комплект -> {компонент -> количество}``; повреждённые строки пропускаются.
    """
    if df is None or df.empty:
        return {}
    header = None
    for i in range(min(len(df), 30)):
        values = {normalize_text(v).lower().replace('ё', 'е') for v in df.iloc[i]}
        if any('комплект' in v for v in values) and any('артикул' in v for v in values):
            header = i
            break
    if header is None:
        return {}
    columns = [normalize_text(v).lower().replace('ё', 'е') for v in df.iloc[header]]
    def find(*terms):
        for idx, value in enumerate(columns):
            if value in terms:
                return idx
        for idx, value in enumerate(columns):
            if any(term in value for term in terms):
                return idx
        return None
    bundle_col = find('комплект')
    article_col = find('артикул')
    qty_col = find('кол-во', 'количество', 'кол.')
    if bundle_col is None or article_col is None:
        return {}
    catalog = {}
    for values in df.iloc[header + 1:].itertuples(index=False, name=None):
        if max(bundle_col, article_col) >= len(values):
            continue
        bundle = normalize_article(values[bundle_col])
        component = normalize_article(values[article_col])
        if not bundle or not component or bundle.lower() in {'nan', 'none'} or component.lower() in {'nan', 'none'}:
            continue
        qty = numeric_cell(values[qty_col]) if qty_col is not None and qty_col < len(values) else 1.0
        catalog.setdefault(bundle, {})[component] = qty or 1.0
    return catalog


def period_price(row, candidates, month):
    year, number = map(int, month.split('-'))
    half = 1 if number <= 6 else 2
    exact = [c for c in candidates if c[1] == year and c[2] in (0, half)]
    exact.sort(key=lambda c: c[2] != half)
    generic = [c for c in candidates if c[1] is None] if len(candidates) == 1 else []
    # Не переносим цену другого года/полугодия без явного основания.
    chosen = exact or generic
    return numeric_cell(row.iloc[chosen[0][0]]) if chosen else 0.0


def extract_manager_from_filename(filename):
    from accounts.manager_registry import source_manager
    registered = source_manager(filename)
    if registered:
        return registered.name
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


def process_manager_sheet(df, filename="", default_manager="", bundle_catalog=None):
    """Разворачивание одного листа книги менеджера в плоскую таблицу (UNPIVOT)."""
    header_idx = None
    for i in range(min(15, len(df))):
        row_str = " ".join([str(x).lower() for x in df.iloc[i].dropna()])
        if ('артикул' in row_str or 'номер изделия' in row_str or 'код' in row_str) and \
                ('клиент' in row_str or 'контрагент' in row_str or 'покупатель' in row_str):
            header_idx = i
            break

    if header_idx is None:
        return pd.DataFrame()

    header_row_main = df.iloc[header_idx]
    header_row_sub = df.iloc[header_idx + 1] if header_idx + 1 < len(df) else pd.Series(dtype=object)

    base_cols = identify_base_columns(header_row_main)
    prices = price_columns(df, header_idx)

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
    is_ushakov = filename.startswith('plan_ushakov_') or "ушаков" in manager_val.lower() or "ушаков" in filename.lower()

    records = []
    bundle_catalog = bundle_catalog or {}
    bundle_articles = set(bundle_catalog)
    component_bundles = {}
    for bundle, components in bundle_catalog.items():
        for component, qty in components.items():
            component_bundles.setdefault(component, []).append((bundle, qty))
    for source_row, row in data_df.iterrows():
        art = normalize_article(row.iloc[base_cols['product_article']])
        if not art or art.lower() in ['nan', 'none', 'итого', 'всего', '']:
            continue

        raw_client = normalize_text(row.iloc[client_col]) if client_col is not None else "Не указан"
        if not raw_client or raw_client.lower() in ['nan', 'none', 'итого', 'всего']:
            continue

        if is_ushakov:
            client_name = "Клиенты Ушакова (Пул)"
        else:
            client_name = raw_client

        supp = normalize_text(row.iloc[supplier_col]) if supplier_col is not None else ""
        prod_name = normalize_text(row.iloc[base_cols['product_name']]) if 'product_name' in base_cols else ""
        product_class = normalize_text(row.iloc[base_cols['product_class']]) if 'product_class' in base_cols else ''
        is_bundle = product_class.casefold() == 's' or art in bundle_articles or 'комплект' in prod_name.casefold() or 'набор' in prod_name.casefold()
        component_qty = ''
        linked_bundle = ''
        if art in bundle_articles:
            component_qty = ''
            linked_bundle = art
        elif len(component_bundles.get(art, [])) == 1:
            linked_bundle, component_qty = component_bundles[art][0]

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
            price_val = period_price(row, prices, m_str)
            y_str, m_num = m_str.split('-')
            records.append({
                '_Исходная строка': int(source_row) + 1,
                '_Исходные клиенты': raw_client,
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
                'Код товара': normalize_article(row.iloc[base_cols['product_code']]) if 'product_code' in base_cols else '',
                'Класс товара': product_class,
                'Производственный индекс': normalize_article(row.iloc[base_cols['production_index']]) if 'production_index' in base_cols else '',
                'Признак комплекта': 'Комплект' if is_bundle else ('Компонент комплекта' if linked_bundle else ''),
                'Артикул комплекта': linked_bundle,
                'Количество в комплекте': component_qty,
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
        from accounts.manager_registry import active_source
        if not active_source(fname):
            continue
        fpath = os.path.join(raw_dir, fname)
        try:
            excel_file = pd.ExcelFile(fpath)
            mgr_name = extract_manager_from_filename(fname)

            # Лист S_ВСЕ является справочником состава и не содержит месячных
            # показателей. Читаем его один раз и прикладываем метаданные к
            # строкам комплектов на рабочих листах.
            bundle_catalog = {}
            bundle_sheet = next((name for name in excel_file.sheet_names if name.strip().casefold() == 's_все'), None)
            if bundle_sheet:
                bundle_catalog = extract_bundle_catalog(pd.read_excel(excel_file, sheet_name=bundle_sheet, header=None))

            for sheet in excel_file.sheet_names:
                if sheet.strip().casefold() == 's_все':
                    continue
                if any(s in sheet.lower() for s in ['свод', 'итог', 'сводная', 'лист1', 'sheet1']) and len(
                        excel_file.sheet_names) > 1:
                    continue
                df_sheet = pd.read_excel(excel_file, sheet_name=sheet, header=None)
                res_df = process_manager_sheet(df_sheet, filename=fname, default_manager=mgr_name,
                                                bundle_catalog=bundle_catalog)
                if not res_df.empty:
                    res_df['_Исходный файл'] = fname
                    res_df['_Исходный лист'] = sheet
                    all_dfs.append(res_df)
        except Exception as e:
            raise ValueError(f'Не удалось прочитать файл «{fname}»: {e}') from e

    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()
