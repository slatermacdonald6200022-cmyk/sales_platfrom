import datetime
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

import pandas as pd
from openpyxl import load_workbook


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


def parse_number(val):
    """Безопасно преобразует число из 1С с обычными и неразрывными пробелами."""
    if pd.isna(val):
        return 0.0
    try:
        return float(str(val).replace('\xa0', '').replace(' ', '').replace(',', '.'))
    except (TypeError, ValueError):
        return 0.0


def get_current_cny_rate(rate_date=None):
    """Возвращает официальный курс ЦБ: сколько RUB стоит 1 CNY."""
    rate_date = rate_date or datetime.date.today()
    date_req = rate_date.strftime('%d/%m/%Y')
    url = f'https://www.cbr.ru/scripts/XML_daily.asp?date_req={date_req}'
    request = urllib.request.Request(
        url,
        headers={'User-Agent': 'sales-platform/1.0'}
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            root = ET.fromstring(response.read())

        for valute in root.findall('Valute'):
            if valute.findtext('CharCode') == 'CNY':
                nominal = int(valute.findtext('Nominal'))
                value = float(valute.findtext('Value').replace(',', '.'))
                rate = value / nominal
                if rate <= 0:
                    break
                return rate
    except Exception as exc:
        raise RuntimeError(
            f'Не удалось получить курс CNY ЦБ РФ на {date_req}. '
            'Загрузка остановлена, чтобы не записать рубли как юани.'
        ) from exc

    raise RuntimeError(
        f'В ответе ЦБ РФ нет корректного курса CNY на {date_req}. '
        'Загрузка остановлена, чтобы не записать рубли как юани.'
    )


def normalize_1c_file(file_path, source_currency='RUB', cny_rate=None):
    """Парсит отчет валовой прибыли 1C ERP и возвращает плоскую таблицу."""
    if not file_path or not os.path.exists(file_path):
        return pd.DataFrame()

    source_currency = str(source_currency).strip().upper()
    if source_currency not in {'RUB', 'CNY'}:
        raise ValueError('Валюта выгрузки 1С должна быть RUB или CNY.')

    # Для рублёвой выгрузки курс запрашивается один раз и применяется ко всему файлу.
    # Если выгрузка уже в CNY, сумма переносится без пересчёта и запрос к ЦБ не нужен.
    if source_currency == 'RUB':
        if cny_rate is None:
            cny_rate = get_current_cny_rate()
        cny_rate = float(cny_rate)
        if cny_rate <= 0:
            raise ValueError('Курс CNY должен быть больше нуля.')
    else:
        cny_rate = 1.0

    df_raw = pd.read_excel(file_path, header=None)

    # 1. Поиск периода в шапке. Не используем месяц по умолчанию:
    # неверно распознанная выгрузка не должна молча изменять факты другого месяца.
    detected_periods = set()
    for i in range(min(15, len(df_raw))):
        line = " ".join([str(x) for x in df_raw.iloc[i].dropna()])
        for _, month, year in re.findall(r'(\d{2})\.(\d{2})\.(\d{4})', line):
            month_number = int(month)
            if 1 <= month_number <= 12:
                detected_periods.add((int(year), month_number))

    if not detected_periods:
        raise ValueError(
            'Не удалось определить отчётный период выгрузки. '
            'В шапке файла должен быть указан диапазон дат.'
        )
    if len(detected_periods) != 1:
        raise ValueError('В шапке выгрузки указаны даты из разных месяцев.')
    period_year, period_month = detected_periods.pop()

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
    col_article = None
    col_qty = None
    col_cny = None

    for c in range(df_raw.shape[1]):
        val = str(header_row.iloc[c]).lower()
        if 'артикул' in val:
            col_article = c
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

    # В отчёте 1С наименование товара, клиент, реализация и заказ находятся
    # в одной колонке, но на разных уровнях иерархии. Настоящий артикул
    # расположен в отдельной колонке «Артикул товара».
    if col_article is None:
        return pd.DataFrame()

    workbook = load_workbook(file_path, read_only=False, data_only=True)
    worksheet = workbook.active
    has_outline_indents = any(
        (worksheet.cell(row=i + 1, column=col_client_item + 1).alignment.indent or 0) >= 2
        for i in range(header_idx + 1, min(len(df_raw), header_idx + 100))
    )

    rows_data = df_raw.iloc[header_idx + 1:].copy()
    records = []
    source_amount_total = 0.0
    current_article = None
    current_product = None

    for row_idx, row in rows_data.iterrows():
        first_cell = normalize_text(row.iloc[col_client_item])
        if not first_cell or 'итого' in first_cell.lower():
            continue

        article = normalize_article(row.iloc[col_article])
        if article:
            current_article = article
            current_product = first_cell
            continue

        if not current_article:
            continue

        indent = worksheet.cell(
            row=int(row_idx) + 1,
            column=col_client_item + 1
        ).alignment.indent or 0

        # Уровень 4 — клиент. Уровни 6 и 8 относятся к реализации и заказу,
        # уровень 0 — итоговая группа товаров. Они не должны становиться фактами.
        if has_outline_indents and not (4 <= indent < 6):
            continue

        # Запасная проверка для экспортов без сохранённых отступов Excel.
        if not has_outline_indents:
            low = first_cell.lower()
            service_markers = (
                'реализация', 'заказ клиента', 'отчет комиссионера',
                'продажи без заказа', 'параметр'
            )
            if any(marker in low for marker in service_markers):
                continue

        qty_val = parse_number(row.iloc[col_qty])
        source_amount = parse_number(row.iloc[col_cny])
        if qty_val == 0 and source_amount == 0:
            continue

        records.append({
            'Клиент': first_cell,
            'Артикул': current_article,
            'Наименование': current_product,
            'Год': period_year,
            'Номер месяца': period_month,
            'Месяц': f"{period_year}-{period_month:02d}",
            'Факт, шт': qty_val,
            'Факт, CNY': source_amount / cny_rate
        })
        source_amount_total += source_amount

    workbook.close()

    result = pd.DataFrame(records)
    result.attrs['report_year'] = period_year
    result.attrs['report_month'] = period_month
    result.attrs['report_period'] = f'{period_year:04d}-{period_month:02d}'
    result.attrs['source_currency'] = source_currency
    result.attrs['exchange_rate'] = cny_rate
    result.attrs['source_amount'] = source_amount_total
    return result


# Алиас для совместимости
parse_1c_fact_file = normalize_1c_file
process_1c_file = normalize_1c_file
