import os
import datetime
import json
from pathlib import Path
import pandas as pd
import numpy as np

from .normalize_manager import normalize_all_managers
from .normalize_1c import normalize_1c_file
from .export_manager_facts import export_all_manager_fact_files
from .combined_report import export_combined_report

USHAKOV_CLIENTS_KEYWORDS = [
    "норма", "тдспа", "мегаавтозапчасть", "набиева", "бав-движение",
    "стфк камаз", "комтранс", "евротехпарт", "автофургон", "ато трейд",
    "автотрак", "автозапчасть", "бренор", "тракдрайв", "мир грузовиков",
    "нитавто", "тонарь", "платформа", "майер групп", "траксторбел"
]

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

MONTH_RU_TO_NUM = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12
}


def is_ushakov_client(client_str: str) -> bool:
    if pd.isna(client_str):
        return False
    c_lower = str(client_str).lower()
    return any(kw in c_lower for kw in USHAKOV_CLIENTS_KEYWORDS)


def clean_key(series: pd.Series) -> pd.Series:
    """Очищает ключи (клиент, артикул) для сопоставления без погрешностей форматирования."""
    s = series.fillna("").astype(str).str.strip().str.lower().str.replace("ё", "е")
    # Убираем хвосты .0 у артикулов
    s = s.str.replace(r"\.0$", "", regex=True)
    return s


def clean_client_key(series: pd.Series) -> pd.Series:
    """Сопоставляет русскую часть двуязычных названий без юридической формы."""
    s = clean_key(series).str.split('/', n=1).str[0]
    s = s.str.replace(r'[«»"“”„]', ' ', regex=True)
    s = s.str.replace(r'\b(?:ооо|ао|пао|зао|оао)\b', ' ', regex=True)
    s = s.str.replace(r'[^0-9a-zа-я]+', ' ', regex=True)
    return s.str.replace(r'\s+', ' ', regex=True).str.strip()


def parse_period_key(df: pd.DataFrame) -> pd.Series:
    """Гарантированно формирует единый ключ периода в формате YYYY-MM."""
    years = None
    months = None

    if "Год" in df.columns:
        years = pd.to_numeric(df["Год"], errors="coerce").fillna(2026).astype(int).astype(str)
    elif "year" in df.columns:
        years = pd.to_numeric(df["year"], errors="coerce").fillna(2026).astype(int).astype(str)

    if "Номер месяца" in df.columns:
        months = pd.to_numeric(df["Номер месяца"], errors="coerce").fillna(1).astype(int).apply(lambda x: f"{x:02d}")
    elif "month_num" in df.columns:
        months = pd.to_numeric(df["month_num"], errors="coerce").fillna(1).astype(int).apply(lambda x: f"{x:02d}")
    elif "Месяц" in df.columns:
        # Если в колонке Месяц записано текстовое название (например, "Май") или "2026-05"
        m_str = df["Месяц"].astype(str).str.strip().str.lower()
        if m_str.str.contains(r"^\d{4}-\d{1,2}$").all():
            return m_str.apply(lambda x: f"{int(x.split('-')[0]):04d}-{int(x.split('-')[1]):02d}")
        months = m_str.map(MONTH_RU_TO_NUM).fillna(1).astype(int).apply(lambda x: f"{x:02d}")
    elif "month" in df.columns:
        m_str = df["month"].astype(str).str.strip()
        if m_str.str.contains(r"^\d{4}-\d{1,2}$").all():
            return m_str.apply(lambda x: f"{int(x.split('-')[0]):04d}-{int(x.split('-')[1]):02d}")
        months = pd.to_numeric(m_str, errors="coerce").fillna(1).astype(int).apply(lambda x: f"{x:02d}")

    if years is not None and months is not None:
        return years + "-" + months

    return pd.Series(["2026-05"] * len(df), index=df.index)


def filter_1c_trash(df_1c: pd.DataFrame) -> pd.DataFrame:
    """Отсекает служебные строки 1С (номера заказов, параметры, пустые артикулы)."""
    if df_1c.empty:
        return df_1c

    art_col = "product_article" if "product_article" in df_1c.columns else "Артикул"
    client_col = "client" if "client" in df_1c.columns else "Клиент"

    df_clean = df_1c[df_1c[art_col].notna() & (df_1c[art_col].astype(str).str.strip() != "")].copy()
    trash_pattern = r"(?:заказ клиента|реализация|параметр|валовая прибыль|итого|всего|отчет)"
    df_clean = df_clean[~df_clean[art_col].astype(str).str.contains(trash_pattern, case=False, na=False)]
    df_clean = df_clean[~df_clean[client_col].astype(str).str.contains(trash_pattern, case=False, na=False)]
    return df_clean


def extract_existing_facts(final_file_path: Path) -> pd.DataFrame:
    """Извлекает уже накопленные исторические факты из предыдущей витрины."""
    if not final_file_path or not final_file_path.exists():
        return pd.DataFrame()

    try:
        df_old = pd.read_excel(final_file_path)
        required_cols = ["Клиент", "Артикул", "Год", "Номер месяца", "Факт, шт", "Факт, CNY"]
        if not all(col in df_old.columns for col in required_cols):
            return pd.DataFrame()

        dimension_cols = [
            col for col in [
                'Код товара', 'Класс товара', 'Производственный индекс',
                'Менеджер', 'Поставщик', 'Наименование',
                'Цена, юань, без НДС 1 п/г 2026',
            ] if col in df_old
        ]
        df_facts = df_old[required_cols + dimension_cols].copy()
        df_facts["Факт, шт"] = pd.to_numeric(df_facts["Факт, шт"], errors="coerce").fillna(0.0)
        df_facts["Факт, CNY"] = pd.to_numeric(df_facts["Факт, CNY"], errors="coerce").fillna(0.0)

        df_facts = df_facts[(df_facts["Факт, шт"] != 0) | (df_facts["Факт, CNY"] != 0)]
        if df_facts.empty:
            return pd.DataFrame()

        df_facts["_key_client"] = clean_key(df_facts["Клиент"])
        df_facts["_key_article"] = clean_key(df_facts["Артикул"])
        df_facts["_key_month"] = (
            df_facts["Год"].astype(str) + "-" +
            df_facts["Номер месяца"].astype(int).apply(lambda x: f"{x:02d}")
        )

        return df_facts[["_key_client", "_key_article", "_key_month", "Клиент", "Артикул", "Факт, шт", "Факт, CNY"] + dimension_cols].drop_duplicates(
            subset=["_key_client", "_key_article", "_key_month"] + dimension_cols
        )
    except Exception as e:
        print(f"⚠️ Ошибка при чтении истории фактов: {e}")
        return pd.DataFrame()


def merge_plans_with_1c(plans_df, actuals_1c_df, existing_facts_df=None):
    from .matching import merge
    return merge(plans_df, actuals_1c_df, existing_facts_df)


def create_full_snapshot(raw_dir="data/raw", date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    print(f"🚀 Запуск генерации среза за дату: {date_str}")

    # 1. Сборка планов менеджеров
    plans_df = normalize_all_managers(raw_dir)
    if plans_df.empty:
        print(f"❌ В папке '{raw_dir}' не найдены файлы планов менеджеров.")
        return None

    processed_root = Path(raw_dir).resolve().parent / 'processed'
    snap_dir = processed_root / 'snapshots' / date_str
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "plans_snapshot.xlsx"
    print(f"✅ Срез планов сохранен: {snap_file} ({len(plans_df):,} строк)")

    # Archive snapshots are comparison-only, never an input to a new run.
    final_dir_base = processed_root / 'final'
    source_periods = parse_period_key(plans_df)
    source_qty = pd.to_numeric(plans_df.get('Факт, шт', pd.Series(0.0, index=plans_df.index)), errors='coerce').fillna(0)
    known_fact_periods = set(source_periods[source_qty != 0])

    # 3. Поиск и парсинг выгрузок 1С
    # В выгрузке используется только количество; оценка по цене плана.

    actuals_dfs = []
    actual_files = []
    report_periods = set()
    for f in os.listdir(raw_dir):
        if (f.startswith("fact_1c_") or "1c" in f.lower() or "факт" in f.lower()) and (
                f.endswith(".xlsx") or f.endswith(".xls")):
            file_1c_path = os.path.join(raw_dir, f)
            print(f"📖 Чтение отчета 1С: {f}")
            df_1c = normalize_1c_file(file_1c_path)
            report_period = df_1c.attrs.get('report_period')
            if report_period:
                report_periods.add(report_period)
            if not df_1c.empty:
                actuals_dfs.append(df_1c)
                actual_files.append(f)

    all_actuals = pd.concat(actuals_dfs, ignore_index=True) if actuals_dfs else pd.DataFrame()

    if not report_periods:
        raise ValueError('Не удалось определить отчётный период файла с фактическими данными.')
    if len(report_periods) != 1:
        periods_label = ', '.join(sorted(report_periods))
        raise ValueError(
            f'Найдены выгрузки за разные отчётные периоды: {periods_label}. '
            'Оставьте файл только за один месяц.'
        )
    if all_actuals.empty:
        raise ValueError('В выгрузке не найдено строк с фактическими данными для обработки.')

    report_period = next(iter(report_periods))
    report_year, report_month = (int(part) for part in report_period.split('-'))

    # 4. Слияние (строгий LEFT JOIN по ключу YYYY-MM)
    final_df = merge_plans_with_1c(plans_df, all_actuals)

    final_dir = final_dir_base / date_str
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / "FINAL_SALES_FACT_TABLE.xlsx"

    matching_report = final_df.attrs.get('matching_report', {})
    unmatched_df = matching_report.get('unmatched_df', pd.DataFrame())
    unmatched_path = None
    if unmatched_df is not None and not unmatched_df.empty:
        unmatched_path = final_dir / 'UNMATCHED_FACTS.xlsx'
        unmatched_df.to_excel(unmatched_path, index=False)

    # Персональные копии исходных планов с заполненными количественными фактами.
    manager_results = export_all_manager_fact_files(
        raw_dir=raw_dir,
        final_df=final_df,
        date_str=date_str,
        periods={report_period},
    )

    combined_result = export_combined_report(
        raw_dir, manager_results, final_dir / 'COMBINED_MANAGER_FACTS.xlsx'
    )

    metadata = {
        'fact_periods': sorted(known_fact_periods | {report_period}),
        'report_period': report_period,
        'report_year': report_year,
        'report_month': report_month,
        'processed_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'source_currency': 'QTY',
        'valuation_method': 'quantity_price',
        'exchange_rate': None,
        'source_amount': 0.0,
        'missing_price_rows': matching_report.get('missing_price_rows', 0),
        'combined_rows': combined_result['rows'],
        'actual_files': actual_files,
        'source_files': sorted(
            filename for filename in os.listdir(raw_dir)
            if filename.startswith('plan_') and filename.lower().endswith(('.xlsx', '.xlsm'))
        ) + actual_files,
        'total_actual_rows': matching_report.get('total_rows', 0),
        'matched_rows': matching_report.get('matched_rows', 0),
        'unmatched_rows': matching_report.get('unmatched_rows', 0),
        'history_conflict_rows': matching_report.get('history_conflict_rows', 0),
        'retained_history_rows': matching_report.get('retained_history_rows', 0),
        'matched_amount_cny': matching_report.get('matched_amount_cny', 0.0),
        'unmatched_amount_cny': matching_report.get('unmatched_amount_cny', 0.0),
    }
    metadata_path = final_dir / 'processing_metadata.json'
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    # Итоговый файл служит признаком полностью завершённой обработки.
    temporary_snapshot = snap_file.with_name('plans_snapshot.pending.xlsx')
    temporary_final = final_path.with_name('FINAL.pending.xlsx')
    plans_df.to_excel(temporary_snapshot, index=False)
    final_df.to_excel(temporary_final, index=False)
    os.replace(temporary_snapshot, snap_file)
    os.replace(temporary_final, final_path)

    final_df.attrs['processing_info'] = {
        **metadata,
        'final_path': str(final_path.resolve()),
        'unmatched_path': str(unmatched_path.resolve()) if unmatched_path else '',
        'manager_reports': {
            manager_id: str(result['path'].resolve())
            for manager_id, result in manager_results.items()
        },
    }

    print(
        f"✅ Итоговая витрина создана: {final_path} "
        f"({len(final_df):,} строк, период {report_period})"
    )
    return final_df
