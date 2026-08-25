from uploads.processors.snapshot_engine import create_full_snapshot

if __name__ == "__main__":
    df = create_full_snapshot(
        raw_dir="data/raw"
    )
    if df is not None:
        print("\n🎉 Все 10 планов менеджеров и факт 1С успешно обработаны и сведены!")