import os
import datetime
import pandas as pd
import numpy as np
from django.conf import settings

from .normalize_manager import normalize_all_managers, MONTH_NUM_TO_NAME
from .normalize_1c import parse_1c_fact_file


def run_full_snapshot_pipeline():
    """
    Запускает полный цикл:
    1. Создает папку data/processed/snapshots/YYYY-MM-DD/
    2. Сохраняет plans_snapshot.xlsx (чистые 13 колонок)
    3. Создает папку data/processed/final/YYYY-MM-DD/
    4. Сливает с 1С и сохраняет FINAL_SALES_FACT_TABLE.xlsx (строго 15 колонок)
    """
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    raw_dir = os.path.join(settings.BASE_DIR, 'data', 'raw')

    # Папки с датой
    snapshot_dir = os.path.join(settings.BASE_DIR, 'data', 'processed', 'snapshots', today_str)
    final_dir = os.path.join(settings.BASE_DIR, 'data', 'processed', 'final', today_str)

    os.makedirs(snapshot_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)

    # 1. Сборка планов менеджеров
    df_plans = normalize_all_managers(raw_dir)
    if df_plans.empty:
        return False, "Не удалось сформировать срез планов: файлы менеджеров отсутствуют или пусты."

    # Сохраняем срез планов (13 колонок)
    snapshot_path = os.path.join(snapshot_dir, 'plans_snapshot.xlsx')
    df_plans.to_excel(snapshot_path, index=False)

    # 2. Поиск и парсинг выгрузки 1С
    fact_file = None
    if os.path.exists(raw_dir):
        for f in os.listdir(raw_dir):
            if f.startswith('fact_1c_') or '1c' in f.lower() or 'факт' in f.lower():
                fact_file = os.path.join(raw_dir, f)
                break

    df_fact = parse_1c_fact_file(fact_file) if fact_file else pd.DataFrame()

    # 3. Слияние планов и факта (Строго по 15 колонкам ИТОГ.xlsx)
    if not df_fact.empty:
        # Сопоставление по ключу
        merge_keys = ['Клиент', 'Артикул', 'Год', 'Номер месяца']
        df_merged = pd.merge(df_plans, df_fact[['Клиент', 'Артикул', 'Год', 'Номер месяца', 'Факт, шт', 'Факт, CNY']],
                             on=merge_keys, how='outer', suffixes=('', '_1c'))

        # Заполнение фактов
        df_merged['Факт, шт'] = df_merged['Факт, шт_1c'].fillna(df_merged['Факт, шт']).fillna(0.0)
        df_merged['Факт, CNY'] = df_merged['Факт, CNY'].fillna(0.0)
        df_merged.drop(columns=['Факт, шт_1c'], inplace=True, errors='ignore')
    else:
        df_merged = df_plans.copy()
        df_merged['Факт, CNY'] = 0.0

    # Заполнение пропусков строковых колонок
    for col in ['AOP, шт', 'Прогноз, шт', 'Факт, шт', 'Цена, юань, без НДС 1 п/г 2026']:
        df_merged[col] = pd.to_numeric(df_merged[col], errors='coerce').fillna(0.0)

    # Расчет сумм в юанях
    df_merged['AOP, CNY'] = df_merged['AOP, шт'] * df_merged['Цена, юань, без НДС 1 п/г 2026']
    df_merged['Прогноз, CNY'] = df_merged['Прогноз, шт'] * df_merged['Цена, юань, без НДС 1 п/г 2026']

    df_merged['Месяц'] = df_merged['Номер месяца'].map(MONTH_NUM_TO_NAME).fillna(df_merged['Месяц'])

    # 15 эталонных колонок в точном порядке
    TARGET_COLUMNS = [
        'AOP, CNY',
        'Прогноз, CNY',
        'Факт, CNY',
        'AOP, шт',
        'Прогноз, шт',
        'Факт, шт',
        'Цена, юань, без НДС 1 п/г 2026',
        'Год',
        'Артикул',
        'Месяц',
        'Номер месяца',
        'Клиент',
        'Менеджер',
        'Поставщик',
        'Наименование'
    ]

    for col in TARGET_COLUMNS:
        if col not in df_merged.columns:
            df_merged[col] = "" if col in ['Клиент', 'Менеджер', 'Поставщик', 'Наименование', 'Артикул', 'Месяц'] else 0

    df_final = df_merged[TARGET_COLUMNS].copy()

    # Сохранение финальной витрины
    final_path = os.path.join(final_dir, 'FINAL_SALES_FACT_TABLE.xlsx')
    df_final.to_excel(final_path, index=False)

    return True, f"Срез за {today_str} успешно создан. Строк: {len(df_final)}."