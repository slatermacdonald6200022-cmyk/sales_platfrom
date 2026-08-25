import os
import datetime
from pathlib import Path
import pandas as pd

from .normalize_manager import normalize_all_managers
from .normalize_1c import normalize_1c_file

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


def is_ushakov_client(client_str: str) -> bool:
    if pd.isna(client_str):
        return False
    c_lower = str(client_str).lower()
    return any(kw in c_lower for kw in USHAKOV_CLIENTS_KEYWORDS)


def clean_key(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().str.replace("ё", "е")


def merge_plans_with_1c(plans_df: pd.DataFrame, actuals_1c_df: pd.DataFrame) -> pd.DataFrame:
    plans = plans_df.copy()
    actuals = actuals_1c_df.copy()

    # 1. Приведение клиентов Ушакова к единому пулу в планах
    if "Менеджер" in plans.columns:
        mask_u_plan = plans["Менеджер"].astype(str).str.contains("Ушаков", case=False, na=False)
        plans.loc[mask_u_plan, "Клиент"] = "Клиенты Ушакова (Пул)"

    # 2. Маппинг клиентов Ушакова в выгрузке 1С
    client_col_1c = "client" if "client" in actuals.columns else "Клиент"
    mask_u_1c = actuals[client_col_1c].apply(is_ushakov_client)
    actuals.loc[mask_u_1c, client_col_1c] = "Клиенты Ушакова (Пул)"

    # 3. Подготовка и агрегация данных 1С с уникальными именами столбцов
    art_col_1c = "product_article" if "product_article" in actuals.columns else "Артикул"
    month_col_1c = "month" if "month" in actuals.columns else "Месяц"
    qty_col_1c = "actual_qty_1c" if "actual_qty_1c" in actuals.columns else (
        "Факт, шт" if "Факт, шт" in actuals.columns else "actual_qty")
    rev_col_1c = "actual_revenue_1c" if "actual_revenue_1c" in actuals.columns else (
        "Факт, CNY" if "Факт, CNY" in actuals.columns else "actual_revenue")
    name_col_1c = "product_name" if "product_name" in actuals.columns else "Наименование"

    actuals["_key_client"] = clean_key(actuals[client_col_1c])
    actuals["_key_article"] = clean_key(actuals[art_col_1c])
    actuals["_key_month"] = clean_key(actuals[month_col_1c])

    actuals_grouped = actuals.groupby(["_key_client", "_key_article", "_key_month"], as_index=False).agg({
        qty_col_1c: 'sum',
        rev_col_1c: 'sum',
        client_col_1c: 'first',
        art_col_1c: 'first',
        name_col_1c: 'first'
    }).rename(columns={
        qty_col_1c: '_1c_qty',
        rev_col_1c: '_1c_cny',
        client_col_1c: '_1c_client',
        art_col_1c: '_1c_art',
        name_col_1c: '_1c_name'
    })

    # 4. Формирование ключей у планов
    plans["_key_client"] = clean_key(plans["Клиент"])
    plans["_key_article"] = clean_key(plans["Артикул"])
    plans["_key_month"] = clean_key(plans["Месяц"])

    # 5. Full Outer Join без конфликтов имен
    merged = pd.merge(
        plans,
        actuals_grouped,
        on=["_key_client", "_key_article", "_key_month"],
        how="outer"
    )

    # 6. Восстановление измерений (Dimensions)
    merged["Клиент"] = merged["Клиент"].fillna(merged["_1c_client"])
    merged["Артикул"] = merged["Артикул"].fillna(merged["_1c_art"])
    merged["Наименование"] = merged["Наименование"].fillna(merged["_1c_name"])
    merged["Менеджер"] = merged["Менеджер"].fillna("Неизвестен / Из 1С")
    merged["Поставщик"] = merged["Поставщик"].fillna("")

    # Назначение Ушакова для его пула
    merged.loc[merged["Клиент"] == "Клиенты Ушакова (Пул)", "Менеджер"] = "Ушаков Алексей"

    # Восстановление дат
    merged["Месяц"] = merged["Месяц"].fillna(merged["_key_month"])
    merged["Год"] = merged["Год"].fillna(merged["Месяц"].str.split("-").str[0]).astype(int)
    merged["Номер месяца"] = merged["Номер месяца"].fillna(merged["Месяц"].str.split("-").str[1]).astype(int)
    merged["Месяц"] = merged["Номер месяца"].map(MONTH_NAMES_RU)

    # 7. Объемы и финансовые суммы
    merged["AOP, шт"] = pd.to_numeric(merged.get("AOP, шт", 0), errors='coerce').fillna(0.0)
    merged["Прогноз, шт"] = pd.to_numeric(merged.get("Прогноз, шт", 0), errors='coerce').fillna(0.0)
    merged["Факт, шт"] = pd.to_numeric(merged.get("_1c_qty", 0), errors='coerce').fillna(0.0)

    price_col = "Цена, юань, без НДС 1 п/г 2026"
    merged[price_col] = pd.to_numeric(merged.get(price_col, 0), errors='coerce').fillna(0.0)

    merged["AOP, CNY"] = merged["AOP, шт"] * merged[price_col]
    merged["Прогноз, CNY"] = merged["Прогноз, шт"] * merged[price_col]
    merged["Факт, CNY"] = pd.to_numeric(merged.get("_1c_cny", 0), errors='coerce').fillna(0.0)

    # 8. Финальный порядок 15 колонок для DataLens
    target_cols = [
        "AOP, CNY", "Прогноз, CNY", "Факт, CNY",
        "AOP, шт", "Прогноз, шт", "Факт, шт",
        "Цена, юань, без НДС 1 п/г 2026",
        "Год", "Артикул", "Месяц", "Номер месяца",
        "Клиент", "Менеджер", "Поставщик", "Наименование"
    ]
    return merged[target_cols]


def create_full_snapshot(raw_dir="data/raw", date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime('%Y-%m-%d')

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

    # 2. Поиск и парсинг выгрузки 1С
    actuals_dfs = []
    for f in os.listdir(raw_dir):
        if (f.startswith('fact_1c_') or '1c' in f.lower() or 'факт' in f.lower()) and (
                f.endswith('.xlsx') or f.endswith('.xls')):
            file_1c_path = os.path.join(raw_dir, f)
            print(f"📖 Чтение отчета 1С: {f}")
            df_1c = normalize_1c_file(file_1c_path)
            if not df_1c.empty:
                actuals_dfs.append(df_1c)

    if actuals_dfs:
        all_actuals = pd.concat(actuals_dfs, ignore_index=True)
        final_df = merge_plans_with_1c(plans_df, all_actuals)
    else:
        print("⚠️ Файлы 1С не найдены, витрина сохраняется только с планами.")
        empty_1c = pd.DataFrame(
            columns=["client", "product_article", "month", "actual_qty_1c", "actual_revenue_1c", "product_name"])
        final_df = merge_plans_with_1c(plans_df, empty_1c)

    final_dir = Path(f"data/processed/final/{date_str}")
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / "FINAL_SALES_FACT_TABLE.xlsx"
    final_df.to_excel(final_path, index=False)

    print(f"✅ Итоговая витрина создана: {final_path} ({len(final_df):,} строк)")
    return final_df