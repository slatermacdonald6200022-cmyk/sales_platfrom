from pathlib import Path
import pandas as pd
import numpy as np


def get_available_snapshot_dates(base_dir="data/processed/snapshots"):
    """Возвращает список доступных дат срезов на основе существующих директорий."""
    p = Path(base_dir)
    if not p.exists():
        return []
    dates = [d.name for d in p.iterdir() if d.is_dir() and (d / "plans_snapshot.xlsx").exists()]
    return sorted(dates, reverse=True)


def clean_str(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().str.replace("ё", "е")


def compare_snapshots(date_a: str, date_b: str, manager_filter: str = "all", base_dir="data/processed/snapshots"):
    """
    Сравнивает два среза планов ДО наложения факта 1С.
    Возвращает словарь со сводными метриками и детальной таблицей.
    """
    path_a = Path(base_dir) / date_a / "plans_snapshot.xlsx"
    path_b = Path(base_dir) / date_b / "plans_snapshot.xlsx"

    if not path_a.exists() or not path_b.exists():
        return None

    df_a = pd.read_excel(path_a)
    df_b = pd.read_excel(path_b)

    # Фильтрация по менеджеру при необходимости
    if manager_filter and manager_filter != "all":
        df_a = df_a[df_a["Менеджер"].astype(str).str.contains(manager_filter, case=False, na=False)]
        df_b = df_b[df_b["Менеджер"].astype(str).str.contains(manager_filter, case=False, na=False)]

    for df in [df_a, df_b]:
        df["Прогноз, шт"] = pd.to_numeric(df.get("Прогноз, шт", 0), errors="coerce").fillna(0.0)

    for df in [df_a, df_b]:
        df["_k_mgr"] = clean_str(df["Менеджер"])
        df["_k_cli"] = clean_str(df["Клиент"])
        df["_k_art"] = clean_str(df["Артикул"]).str.replace(r"\.0$", "", regex=True)

    agg_a = df_a.groupby(["_k_mgr", "_k_cli", "_k_art"], as_index=False).agg({
        "Прогноз, шт": "sum",
        "Менеджер": "first",
        "Клиент": "first",
        "Артикул": "first"
    }).rename(columns={"Прогноз, шт": "val_a"})

    agg_b = df_b.groupby(["_k_mgr", "_k_cli", "_k_art"], as_index=False).agg({
        "Прогноз, шт": "sum",
        "Менеджер": "first",
        "Клиент": "first",
        "Артикул": "first"
    }).rename(columns={"Прогноз, шт": "val_b"})

    merged = pd.merge(
        agg_a,
        agg_b,
        on=["_k_mgr", "_k_cli", "_k_art"],
        how="outer",
        suffixes=("_a", "_b")
    )

    merged["Менеджер"] = merged["Менеджер_a"].fillna(merged["Менеджер_b"])
    merged["Клиент"] = merged["Клиент_a"].fillna(merged["Клиент_b"])
    merged["Артикул"] = merged["Артикул_a"].fillna(merged["Артикул_b"])
    merged["val_a"] = merged["val_a"].fillna(0.0)
    merged["val_b"] = merged["val_b"].fillna(0.0)

    merged["delta"] = merged["val_b"] - merged["val_a"]
    merged["pct_change"] = np.where(
        merged["val_a"] > 0,
        ((merged["val_b"] - merged["val_a"]) / merged["val_a"]) * 100,
        np.where(merged["val_b"] > 0, 100.0, 0.0)
    )

    merged = merged[(merged["val_a"] != 0) | (merged["val_b"] != 0)].copy()
    merged = merged.sort_values(by="delta", ascending=True)

    total_a = merged["val_a"].sum()
    total_b = merged["val_b"].sum()
    total_delta = total_b - total_a
    total_pct = ((total_b - total_a) / total_a * 100) if total_a > 0 else 0.0

    comparison_rows = []
    for _, r in merged.iterrows():
        comparison_rows.append({
            "manager": r["Менеджер"],
            "client": r["Клиент"],
            "article": r["Артикул"],
            "val_a": f"{r['val_a']:,.0f}".replace(",", " "),
            "val_b": f"{r['val_b']:,.0f}".replace(",", " "),
            "delta": f"{r['delta']:+,.0f}".replace(",", " "),
            "delta_num": r["delta"],
            "pct_change": f"{r['pct_change']:+.1f}"
        })

    return {
        "summary": {
            "total_a": f"{total_a:,.0f}".replace(",", " "),
            "total_b": f"{total_b:,.0f}".replace(",", " "),
            "total_delta": f"{total_delta:+,.0f}".replace(",", " "),
            "total_delta_num": total_delta,
            "total_pct": f"{total_pct:+.1f}"
        },
        "rows": comparison_rows
    }