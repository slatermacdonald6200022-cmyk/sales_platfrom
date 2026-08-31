import pandas as pd
import glob
import os

from uploads.processors.normalize_manager import normalize_all_managers
from uploads.processors.normalize_1c import normalize_1c_file
from uploads.processors.snapshot_engine import clean_key, parse_period_key

print("🔍 Запуск диагностики ключей сопоставления...")

# 1. Поиск файла 1С в папке data/raw
raw_dir = "data/raw"
files_1c = [
    os.path.join(raw_dir, f) for f in os.listdir(raw_dir)
    if (f.startswith("fact_1c_") or "1c" in f.lower() or "факт" in f.lower())
    and (f.endswith(".xlsx") or f.endswith(".xls"))
]

if not files_1c:
    print("❌ В папке data/raw не найден файл выгрузки 1С!")
    exit()

target_1c_file = files_1c[0]
print(f"📖 Читаем 1С: {target_1c_file}")
fact_1c = normalize_1c_file(target_1c_file)
print(f"📖 Читаем планы менеджеров из: {raw_dir}")
plans = normalize_all_managers(raw_dir)

if fact_1c.empty or plans.empty:
    print(f"❌ Данные не прочитаны: 1С (строк: {len(fact_1c)}), Планы (строк: {len(plans)})")
    exit()

# 2. Определяем имена колонок в 1С
client_col_1c = "client" if "client" in fact_1c.columns else "Клиент"
art_col_1c = "product_article" if "product_article" in fact_1c.columns else "Артикул"
qty_col_1c = "actual_qty_1c" if "actual_qty_1c" in fact_1c.columns else ("Факт, шт" if "Факт, шт" in fact_1c.columns else "actual_qty")

# 3. Формируем ключи сопоставления
plans["_k_client"] = clean_key(plans["Клиент"])
plans["_k_art"] = clean_key(plans["Артикул"])
plans["_k_period"] = parse_period_key(plans)
plans["_full_key"] = plans["_k_client"] + " | " + plans["_k_art"] + " | " + plans["_k_period"]

fact_1c["_k_client"] = clean_key(fact_1c[client_col_1c])
fact_1c["_k_art"] = clean_key(fact_1c[art_col_1c])
fact_1c["_k_period"] = parse_period_key(fact_1c)
fact_1c["_full_key"] = fact_1c["_k_client"] + " | " + fact_1c["_k_art"] + " | " + fact_1c["_k_period"]

# 4. Анализ
matched = fact_1c[fact_1c["_full_key"].isin(plans["_full_key"])]
unmatched = fact_1c[~fact_1c["_full_key"].isin(plans["_full_key"])]

print("\n" + "="*50)
print(f"📊 РЕЗУЛЬТАТЫ СРАВНЕНИЯ КЛЮЧЕЙ:")
print(f"• Всего строк в выгрузке 1С: {len(fact_1c):,}")
print(f"• Найдено точных совпадений с планом: {len(matched):,}")
print(f"• НЕ НАШЛОСЬ в плане: {len(unmatched):,}")
print("="*50 + "\n")

# Проверка периодов
print("Периоды в планах:", plans["_k_period"].unique()[:5])
print("Периоды в 1С:", fact_1c["_k_period"].unique())

if not unmatched.empty:
    print("\n⚠️ Примеры строк из 1С, которые не привязались к плану:")
    cols_to_show = [client_col_1c, art_col_1c, "_k_period", qty_col_1c]
    print(unmatched[cols_to_show].head(15))