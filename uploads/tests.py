import os
import tempfile

from django.test import SimpleTestCase
from openpyxl import Workbook
from openpyxl.styles import Alignment

from .processors.normalize_1c import normalize_1c_file


class Normalize1CFileTests(SimpleTestCase):
    def test_reads_article_and_client_from_hierarchy_levels(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Период: 01.05.2026 - 31.05.2026"])
        sheet.append([])
        sheet.append([
            "Номенклатура", "", "", "", "Артикул товара", "", "Количество", "Выручка с НДС"
        ])

        rows = [
            (["Готовая продукция/Finish good", "", "", "", "", "", 10, 1000], 0),
            (["Товар А", "", "", "", "4494450600", "", 6, 600], 2),
            (["КЛИЕНТ А ООО", "", "", "", "", "", 6, 600], 4),
            (["Реализация товаров и услуг №1", "", "", "", "", "", 6, 600], 6),
            (["Заказ клиента №1", "", "", "", "", "", 6, 600], 8),
        ]
        for values, indent in rows:
            sheet.append(values)
            sheet.cell(sheet.max_row, 1).alignment = Alignment(indent=indent)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            path = tmp.name
        try:
            workbook.save(path)
            result = normalize_1c_file(path, source_currency='RUB', cny_rate=10)
        finally:
            workbook.close()
            os.unlink(path)

        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertEqual(row["Клиент"], "КЛИЕНТ А ООО")
        self.assertEqual(row["Артикул"], "4494450600")
        self.assertEqual(row["Наименование"], "Товар А")
        self.assertEqual(row["Год"], 2026)
        self.assertEqual(row["Номер месяца"], 5)
        self.assertEqual(row["Факт, шт"], 6)
        self.assertEqual(row["Факт, CNY"], 60)

# Create your tests here.
