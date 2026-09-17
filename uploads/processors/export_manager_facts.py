import shutil
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from .xlsx_cells import write_quantity_cells

from .normalize_manager import (
    BASE_COLUMN_ALIASES,
    extract_manager_from_filename,
    normalize_article,
    normalize_text,
    parse_metric_type,
    parse_month_cell,
    identify_base_columns,
)


def clean_key(value):
    """Формирует ключ так же, как сопоставление в итоговой витрине."""
    if pd.isna(value):
        return ''
    result = str(value).strip().lower().replace('ё', 'е')
    if result.endswith('.0'):
        result = result[:-2]
    return result


def build_fact_lookup(final_df, periods=None):
    """Возвращает факт в штуках по ключу клиент + код товара (или артикул) + месяц."""
    if final_df is None or final_df.empty:
        return {}

    required = {'Клиент', 'Артикул', 'Год', 'Номер месяца', 'Факт, шт'}
    if not required.issubset(final_df.columns):
        missing = ', '.join(sorted(required - set(final_df.columns)))
        raise ValueError(f'В итоговой витрине отсутствуют колонки: {missing}')

    extra = ['Менеджер', 'Класс товара', 'Производственный индекс']
    optional = ['Код товара'] + extra
    facts = final_df[list(required) + [col for col in optional if col in final_df]].copy()
    for col in extra:
        facts['_' + col] = facts[col].apply(clean_key) if col in facts else ''
    facts['_key_client'] = facts['Клиент'].apply(clean_key)
    facts['_key_article'] = facts['Артикул'].apply(clean_key)
    facts['_key_code'] = facts['Код товара'].apply(clean_key) if 'Код товара' in facts else ''
    facts['_key_product'] = facts['_key_article'].apply(lambda value: 'article:' + value)
    if 'Код товара' in facts:
        code_mask = facts['_key_code'] != ''
        facts.loc[code_mask, '_key_product'] = 'code:' + facts.loc[code_mask, '_key_code']
    facts['_key_month'] = (
        pd.to_numeric(facts['Год'], errors='coerce').fillna(0).astype(int).astype(str)
        + '-'
        + pd.to_numeric(facts['Номер месяца'], errors='coerce').fillna(0).astype(int).apply(lambda x: f'{x:02d}')
    )
    if periods is not None:
        facts = facts[facts['_key_month'].isin(set(periods))].copy()
    facts['Факт, шт'] = pd.to_numeric(facts['Факт, шт'], errors='coerce').fillna(0.0)

    # Одинаковый факт может повторяться в нескольких строках плана.
    # Берём одно значение, а не суммируем его повторно.
    key_columns = ['_key_client', '_key_product', '_key_month'] + ['_' + col for col in extra]
    duplicate = facts.duplicated(subset=key_columns, keep=False)
    if (facts.loc[duplicate, 'Факт, шт'] != 0).any():
        raise ValueError('Неоднозначный количественный факт: повторяется полный ключ строки.')
    # Нулевые дубли можно обнулить в текущем месяце; ненулевые запрещены выше.
    facts = facts.drop_duplicates(subset=key_columns)
    return {
        tuple(row[col] for col in key_columns): float(row['Факт, шт'])
        for _, row in facts.iterrows()
    }


def find_sheet_layout(sheet):
    """Находит ключевые колонки и месячные колонки факта на листе менеджера."""
    header_idx = None
    for row_idx in range(1, min(15, sheet.max_row) + 1):
        values = [cell.value for cell in sheet[row_idx]]
        row_text = ' '.join(str(value).lower() for value in values if value is not None)
        if ('артикул' in row_text or 'номер изделия' in row_text or 'код' in row_text) and \
                ('клиент' in row_text or 'контрагент' in row_text or 'покупатель' in row_text):
            header_idx = row_idx
            break

    if header_idx is None or header_idx + 1 > sheet.max_row:
        return None

    base_cols = {key: index + 1 for key, index in identify_base_columns(
        [cell.value for cell in sheet[header_idx]]
    ).items()}

    if 'product_article' not in base_cols or 'client' not in base_cols:
        return None

    fact_columns = []
    current_month = None
    for col_idx in range(1, sheet.max_column + 1):
        top_value = sheet.cell(header_idx, col_idx).value
        sub_value = sheet.cell(header_idx + 1, col_idx).value
        top_text = str(top_value or '').lower()

        if any(marker in top_text for marker in ['итого', 'всего', 'total', 'год', 'сумма', 'руб', 'rub']):
            current_month = None

        detected_month = parse_month_cell(top_value)
        if detected_month:
            current_month = detected_month

        metric = parse_metric_type(sub_value) or parse_metric_type(top_value)
        if current_month and metric == 'fact':
            fact_columns.append((col_idx, current_month))

    if not fact_columns:
        return None

    return {
        'header_row': header_idx,
        'client_col': base_cols['client'],
        'article_col': base_cols['product_article'],
        'code_col': base_cols.get('product_code'),
        'class_col': base_cols.get('product_class'),
        'index_col': base_cols.get('production_index'),
        'fact_columns': fact_columns,
    }


def update_manager_workbook(source_path, destination_path, fact_lookup, manager_id, periods=None):
    """Создаёт копию книги менеджера и заполняет существующие колонки факта."""
    source_path = Path(source_path)
    destination_path = Path(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = load_workbook(source_path, data_only=False, keep_links=True)
    updated_cells = 0
    updates = {}
    manager_key = clean_key(extract_manager_from_filename(source_path.name))

    try:
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            layout = find_sheet_layout(sheet)
            if not layout:
                continue

            current_client = ''
            is_ushakov = manager_id == 'ushakov'
            for row_idx in range(layout['header_row'] + 2, sheet.max_row + 1):
                raw_client = normalize_text(sheet.cell(row_idx, layout['client_col']).value)
                if raw_client and raw_client.lower() not in {'nan', 'none', 'итого', 'всего'}:
                    current_client = raw_client

                article = normalize_article(sheet.cell(row_idx, layout['article_col']).value)
                if not current_client or not article or article.lower() in {'nan', 'none', 'итого', 'всего'}:
                    continue

                client = 'Клиенты Ушакова (Пул)' if is_ushakov else current_client
                client_key = clean_key(client)
                article_key = clean_key(article)
                code_key = clean_key(sheet.cell(row_idx, layout['code_col']).value) if layout['code_col'] else ''
                product_key = 'code:' + code_key if code_key else 'article:' + article_key

                for col_idx, month_key in layout['fact_columns']:
                    if periods is not None and month_key not in periods:
                        continue
                    class_value = clean_key(sheet.cell(row_idx, layout['class_col']).value) if layout['class_col'] else ''
                    index_value = clean_key(sheet.cell(row_idx, layout['index_col']).value) if layout['index_col'] else ''
                    lookup_key = (client_key, product_key, month_key,
                                  manager_key, class_value, index_value)
                    if lookup_key not in fact_lookup:
                        continue
                    updates.setdefault(sheet_name, {})[sheet.cell(row_idx, col_idx).coordinate] = fact_lookup[lookup_key]
                    updated_cells += 1

        write_quantity_cells(source_path, destination_path, updates)
    finally:
        workbook.close()

    return updated_cells


def export_all_manager_fact_files(raw_dir, final_df, date_str, periods=None):
    """Формирует персональный файл с фактами для каждого загруженного плана."""
    raw_path = Path(raw_dir)
    output_root = raw_path.parent / 'processed' / 'manager_reports' / date_str
    # В персональные книги записывается только месяц текущей выгрузки.
    # Неоднозначно сохранённая история не должна блокировать этот этап.
    fact_lookup = build_fact_lookup(final_df, periods=periods)
    results = {}

    for source_path in raw_path.iterdir():
        filename = source_path.name
        from accounts.manager_registry import active_source
        if not active_source(filename):
            continue
        if not source_path.is_file() or not filename.startswith('plan_') or source_path.suffix.lower() not in {'.xlsx', '.xlsm'}:
            continue

        manager_id = filename.split('_', 2)[1] if '_' in filename else ''
        if not manager_id:
            continue

        original_name = filename.split('_', 2)[2] if filename.count('_') >= 2 else filename
        output_name = f'С_фактом_{original_name}'
        destination_path = output_root / manager_id / output_name
        updated_cells = update_manager_workbook(
            source_path=source_path,
            destination_path=destination_path,
            fact_lookup=fact_lookup,
            manager_id=manager_id,
            periods=periods,
        )
        results[manager_id] = {
            'path': destination_path,
            'updated_cells': updated_cells,
            'manager_name': extract_manager_from_filename(filename),
        }
        print(f'✅ Файл менеджера {manager_id}: заполнено ячеек факта — {updated_cells}')

    return results


def get_latest_manager_report(base_dir, manager_id):
    """Находит последнюю сформированную книгу конкретного менеджера."""
    base_path = Path(base_dir)
    if not base_path.exists():
        return None

    for date_dir in sorted((path for path in base_path.iterdir() if path.is_dir()), reverse=True):
        if not (base_path.parent / 'final' / date_dir.name / 'FINAL_SALES_FACT_TABLE.xlsx').is_file():
            continue
        manager_dir = date_dir / manager_id
        if not manager_dir.exists():
            continue
        reports = sorted(
            (path for path in manager_dir.iterdir() if path.is_file() and path.suffix.lower() in {'.xlsx', '.xlsm'}),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if reports:
            return reports[0]
    return None


def remove_manager_reports(base_dir, manager_id):
    """Удаляет все сформированные копии конкретного менеджера."""
    base_path = Path(base_dir)
    if not base_path.exists():
        return 0

    removed = 0
    for date_dir in (path for path in base_path.iterdir() if path.is_dir()):
        manager_dir = date_dir / manager_id
        if manager_dir.exists() and manager_dir.is_dir():
            shutil.rmtree(manager_dir)
            removed += 1
    return removed
