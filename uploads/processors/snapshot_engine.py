import os
import datetime
import json
from pathlib import Path
import pandas as pd
import numpy as np

from .normalize_manager import normalize_all_managers
from .normalize_1c import normalize_1c_file
from .export_manager_facts import export_all_manager_fact_files

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

        df_facts = df_old[required_cols].copy()
        df_facts["Факт, шт"] = pd.to_numeric(df_facts["Факт, шт"], errors="coerce").fillna(0.0)
        df_facts["Факт, CNY"] = pd.to_numeric(df_facts["Факт, CNY"], errors="coerce").fillna(0.0)

        df_facts = df_facts[(df_facts["Факт, шт"] > 0) | (df_facts["Факт, CNY"] > 0)]
        if df_facts.empty:
            return pd.DataFrame()

        df_facts["_key_client"] = clean_key(df_facts["Клиент"])
        df_facts["_key_article"] = clean_key(df_facts["Артикул"])
        df_facts["_key_month"] = (
            df_facts["Год"].astype(str) + "-" +
            df_facts["Номер месяца"].astype(int).apply(lambda x: f"{x:02d}")
        )

        return df_facts[["_key_client", "_key_article", "_key_month", "Факт, шт", "Факт, CNY"]].drop_duplicates(
            subset=["_key_client", "_key_article", "_key_month"]
        )
    except Exception as e:
        print(f"⚠️ Ошибка при чтении истории фактов: {e}")
        return pd.DataFrame()


def merge_plans_with_1c(plans_df: pd.DataFrame, actuals_1c_df: pd.DataFrame, existing_facts_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Выполняет строгое левое соединение (LEFT JOIN): база — только строки планов.
    Факт из 1С подставляется напрямую без перемножений и формул.
    """
    plans = plans_df.copy()
    actuals = actuals_1c_df.copy() if actuals_1c_df is not None else pd.DataFrame()
    matching_report = {
        'total_rows': 0,
        'matched_rows': 0,
        'unmatched_rows': 0,
        'matched_amount_cny': 0.0,
        'unmatched_amount_cny': 0.0,
        'unmatched_df': pd.DataFrame(),
    }

    # 1. Приведение клиентов Ушакова к единому пулу в планах
    if "Менеджер" in plans.columns:
        mask_u_plan = plans["Менеджер"].astype(str).str.contains("Ушаков", case=False, na=False)
        plans.loc[mask_u_plan, "Клиент"] = "Клиенты Ушакова (Пул)"

    # 2. Обработка и агрегация отчета 1С
    actuals = filter_1c_trash(actuals)

    if not actuals.empty:
        client_col_1c = "client" if "client" in actuals.columns else "Клиент"
        art_col_1c = "product_article" if "product_article" in actuals.columns else "Артикул"
        qty_col_1c = "actual_qty_1c" if "actual_qty_1c" in actuals.columns else (
            "Факт, шт" if "Факт, шт" in actuals.columns else "actual_qty")
        rev_col_1c = "actual_revenue_1c" if "actual_revenue_1c" in actuals.columns else (
            "Факт, CNY" if "Факт, CNY" in actuals.columns else "actual_revenue")

        mask_u_1c = actuals[client_col_1c].apply(is_ushakov_client)
        actuals.loc[mask_u_1c, client_col_1c] = "Клиенты Ушакова (Пул)"

        actuals["_key_client"] = clean_key(actuals[client_col_1c])
        actuals["_key_article"] = clean_key(actuals[art_col_1c])
        actuals["_key_month"] = parse_period_key(actuals)

        actuals_grouped = actuals.groupby(["_key_client", "_key_article", "_key_month"], as_index=False).agg({
            qty_col_1c: "sum",
            rev_col_1c: "sum"
        }).rename(columns={
            qty_col_1c: "_1c_qty",
            rev_col_1c: "_1c_cny"
        })
        months_in_current_1c = set(actuals_grouped["_key_month"].unique())
        print(f"📌 Периоды из выгрузки 1С для обновления: {months_in_current_1c}")
    else:
        actuals_grouped = pd.DataFrame(columns=["_key_client", "_key_article", "_key_month", "_1c_qty", "_1c_cny"])
        months_in_current_1c = set()

    # 3. Формирование ключей у планов (строго синхронизированных)
    plans["_key_client"] = clean_key(plans["Клиент"])
    plans["_key_article"] = clean_key(plans["Артикул"])
    plans["_key_month"] = parse_period_key(plans)

    # Диагностика использует те же очищенные ключи, что и основное соединение,
    # но не участвует в расчёте итоговой таблицы.
    if not actuals.empty:
        diagnostic_rows = actuals[[
            client_col_1c, art_col_1c, '_key_client', '_key_article', '_key_month',
            qty_col_1c, rev_col_1c,
        ]].copy()
        diagnostic_rows = diagnostic_rows.rename(columns={
            client_col_1c: 'Клиент из выгрузки',
            art_col_1c: 'Артикул из выгрузки',
            qty_col_1c: 'Факт, шт',
            rev_col_1c: 'Факт, CNY',
            '_key_month': 'Период',
        })
        diagnostic_rows['Факт, шт'] = pd.to_numeric(
            diagnostic_rows['Факт, шт'], errors='coerce'
        ).fillna(0.0)
        diagnostic_rows['Факт, CNY'] = pd.to_numeric(
            diagnostic_rows['Факт, CNY'], errors='coerce'
        ).fillna(0.0)
        diagnostic_rows = diagnostic_rows.groupby(
            [
                'Клиент из выгрузки', 'Артикул из выгрузки',
                '_key_client', '_key_article', 'Период',
            ],
            as_index=False,
            dropna=False,
        ).agg({'Факт, шт': 'sum', 'Факт, CNY': 'sum'})

        plan_keys = plans[['_key_client', '_key_article', '_key_month']].drop_duplicates()
        checked = diagnostic_rows.merge(
            plan_keys,
            left_on=['_key_client', '_key_article', 'Период'],
            right_on=['_key_client', '_key_article', '_key_month'],
            how='left',
            indicator=True,
        )
        matched_mask = checked['_merge'].eq('both')
        plan_clients = set(plans['_key_client'])
        plan_pairs = set(zip(plans['_key_client'], plans['_key_article']))

        def unmatched_reason(row):
            if row['_key_client'] not in plan_clients:
                return 'Клиент не найден в планах'
            if (row['_key_client'], row['_key_article']) not in plan_pairs:
                return 'Артикул не найден у клиента'
            return 'Нет строки плана за этот период'

        unmatched = checked.loc[~matched_mask].copy()
        unmatched['Причина'] = ''
        if not unmatched.empty:
            unmatched['Причина'] = unmatched.apply(unmatched_reason, axis=1)
        unmatched_export = unmatched[[
            'Клиент из выгрузки', 'Артикул из выгрузки', 'Период',
            'Факт, шт', 'Факт, CNY', 'Причина',
        ]].copy()
        matching_report = {
            'total_rows': int(len(checked)),
            'matched_rows': int(matched_mask.sum()),
            'unmatched_rows': int((~matched_mask).sum()),
            'matched_amount_cny': float(checked.loc[matched_mask, 'Факт, CNY'].sum()),
            'unmatched_amount_cny': float(checked.loc[~matched_mask, 'Факт, CNY'].sum()),
            'unmatched_df': unmatched_export,
        }

    # 4. База соединения — только строки плана (LEFT JOIN)
    merged = pd.merge(
        plans,
        actuals_grouped,
        on=["_key_client", "_key_article", "_key_month"],
        how="left"
    )

    # 5. Приведение дат
    if "Номер месяца" in merged.columns:
        merged["Номер месяца"] = pd.to_numeric(merged["Номер месяца"], errors="coerce").fillna(1).astype(int)
    if "Год" in merged.columns:
        merged["Год"] = pd.to_numeric(merged["Год"], errors="coerce").fillna(2026).astype(int)
    merged["Месяц"] = merged["Номер месяца"].map(MONTH_NAMES_RU).fillna(merged.get("Месяц", ""))

    # 6. Расчет планов в юанях (AOP, Прогноз)
    merged["AOP, шт"] = pd.to_numeric(merged.get("AOP, шт", 0), errors="coerce").fillna(0.0)
    merged["Прогноз, шт"] = pd.to_numeric(merged.get("Прогноз, шт", 0), errors="coerce").fillna(0.0)

    price_col = "Цена, юань, без НДС 1 п/г 2026"
    merged[price_col] = pd.to_numeric(merged.get(price_col, 0), errors="coerce").fillna(0.0)

    merged["AOP, CNY"] = merged["AOP, шт"] * merged[price_col]
    merged["Прогноз, CNY"] = merged["Прогноз, шт"] * merged[price_col]

    # 7. Подтягивание истории фактов
    if existing_facts_df is not None and not existing_facts_df.empty:
        merged = pd.merge(
            merged,
            existing_facts_df.rename(columns={"Факт, шт": "_hist_qty", "Факт, CNY": "_hist_cny"}),
            on=["_key_client", "_key_article", "_key_month"],
            how="left"
        )
    else:
        # Факты из файлов менеджеров не являются источником факта.
        # Если предыдущей витрины нет, история считается пустой.
        merged["_hist_qty"] = np.nan
        merged["_hist_cny"] = np.nan

    is_in_1c_period = merged["_key_month"].isin(months_in_current_1c)

    current_qty = pd.to_numeric(merged["_1c_qty"], errors="coerce").fillna(0.0)
    current_cny = pd.to_numeric(merged["_1c_cny"], errors="coerce").fillna(0.0)
    historical_qty = pd.to_numeric(merged["_hist_qty"], errors="coerce").fillna(0.0)
    historical_cny = pd.to_numeric(merged["_hist_cny"], errors="coerce").fillna(0.0)

    # Обновляем только период, явно присутствующий в текущей выгрузке 1С.
    # Все остальные месяцы переносятся из предыдущей витрины без изменений.
    merged["Факт, шт"] = np.where(
        is_in_1c_period,
        current_qty,
        historical_qty
    )

    merged["Факт, CNY"] = np.where(
        is_in_1c_period,
        current_cny,
        historical_cny
    )

    # 8. Финальный порядок 15 колонок для DataLens
    target_cols = [
        "AOP, CNY", "Прогноз, CNY", "Факт, CNY",
        "AOP, шт", "Прогноз, шт", "Факт, шт",
        "Цена, юань, без НДС 1 п/г 2026",
        "Год", "Артикул", "Месяц", "Номер месяца",
        "Клиент", "Менеджер", "Поставщик", "Наименование"
    ]

    for col in target_cols:
        if col not in merged.columns:
            merged[col] = "" if col in ["Клиент", "Менеджер", "Поставщик", "Наименование", "Месяц", "Артикул"] else 0.0

    result = merged[target_cols]
    result.attrs['matching_report'] = matching_report
    return result


def create_full_snapshot(raw_dir="data/raw", date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    print(f"🚀 Запуск генерации среза за дату: {date_str}")

    # 1. Сборка планов менеджеров
    plans_df = normalize_all_managers(raw_dir)
    if plans_df.empty:
        print(f"❌ В папке '{raw_dir}' не найдены файлы планов менеджеров.")
        return None

    snap_dir = Path(f"data/processed/snapshots/{date_str}")
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "plans_snapshot.xlsx"
    plans_df.to_excel(snap_file, index=False)
    print(f"✅ Срез планов сохранен: {snap_file} ({len(plans_df):,} строк)")

    # 2. Поиск накопленной истории фактов
    existing_facts = pd.DataFrame()
    final_dir_base = Path("data/processed/final")
    if final_dir_base.exists():
        existing_final_files = sorted(list(final_dir_base.glob("**/FINAL_SALES_FACT_TABLE.xlsx")), reverse=True)
        if existing_final_files:
            latest_file = existing_final_files[0]
            print(f"📚 Загрузка накопленной истории фактов из: {latest_file}")
            existing_facts = extract_existing_facts(latest_file)

    # 3. Поиск и парсинг выгрузок 1С
    settings_path = Path(raw_dir) / 'fact_1c_settings.json'
    source_currency = 'RUB'
    try:
        with open(settings_path, 'r', encoding='utf-8') as settings_file:
            source_currency = str(json.load(settings_file).get('source_currency', 'RUB')).upper()
    except (OSError, ValueError, TypeError):
        pass
    if source_currency not in {'RUB', 'CNY'}:
        source_currency = 'RUB'

    currency_label = 'RUB → CNY по курсу ЦБ на дату обработки' if source_currency == 'RUB' else 'CNY без пересчёта'
    print(f"💱 Режим суммы выгрузки 1С: {currency_label}")

    actuals_dfs = []
    actual_files = []
    report_periods = set()
    exchange_rates = set()
    source_amount = 0.0
    for f in os.listdir(raw_dir):
        if (f.startswith("fact_1c_") or "1c" in f.lower() or "факт" in f.lower()) and (
                f.endswith(".xlsx") or f.endswith(".xls")):
            file_1c_path = os.path.join(raw_dir, f)
            print(f"📖 Чтение отчета 1С: {f}")
            df_1c = normalize_1c_file(file_1c_path, source_currency=source_currency)
            report_period = df_1c.attrs.get('report_period')
            if report_period:
                report_periods.add(report_period)
            if df_1c.attrs.get('exchange_rate') is not None:
                exchange_rates.add(float(df_1c.attrs['exchange_rate']))
            source_amount += float(df_1c.attrs.get('source_amount', 0.0))
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
    final_df = merge_plans_with_1c(plans_df, all_actuals, existing_facts)

    final_dir = Path(f"data/processed/final/{date_str}")
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / "FINAL_SALES_FACT_TABLE.xlsx"
    final_df.to_excel(final_path, index=False)

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
    )

    metadata = {
        'report_period': report_period,
        'report_year': report_year,
        'report_month': report_month,
        'processed_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'source_currency': source_currency,
        'exchange_rate': next(iter(exchange_rates)) if len(exchange_rates) == 1 else None,
        'source_amount': source_amount,
        'actual_files': actual_files,
        'source_files': sorted(
            filename for filename in os.listdir(raw_dir)
            if filename.startswith('plan_') and filename.lower().endswith(('.xlsx', '.xlsm'))
        ) + actual_files,
        'total_actual_rows': matching_report.get('total_rows', 0),
        'matched_rows': matching_report.get('matched_rows', 0),
        'unmatched_rows': matching_report.get('unmatched_rows', 0),
        'matched_amount_cny': matching_report.get('matched_amount_cny', 0.0),
        'unmatched_amount_cny': matching_report.get('unmatched_amount_cny', 0.0),
    }
    metadata_path = final_dir / 'processing_metadata.json'
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

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
