from django.http import QueryDict
from django.test import TestCase
import pandas as pd
from .dashboard import prepare_frame, selections, apply_filters, facet_options, deviations, build_context, PRICE


def frame():
    return prepare_frame(pd.DataFrame([
        {'Менеджер': manager, 'Клиент': client, 'Артикул': product, 'Наименование': product,
         'Год': 2026, 'Номер месяца': month, 'AOP, CNY': plan, 'Факт, CNY': fact,
         'AOP, шт': plan / 10, PRICE: 10}
        for manager, client, product, month, plan, fact in [
            ('A', 'X', 'P', 1, 100, 0), ('B', 'Y', 'P', 1, 1000, 5000),
            ('C', 'Z', 'Q', 2, 0, 100), ('A', 'Z', 'Q', 2, 200, 200),
            ('A', 'X', 'P', 12, 1000, 0)]]))


class DashboardTests(TestCase):
    def test_whole_year_and_all_time_period_options(self):
        df = frame()
        old = df.iloc[[0]].copy()
        old['Год'] = 2025
        df = pd.concat([df, old], ignore_index=True)
        yearly = build_context(df, QueryDict('period=year_2026'), '/missing/final.xlsx', (2026, 1))
        self.assertEqual(yearly['selected']['period'], ['2026-01', '2026-02', '2026-12'])
        period_filter = next(f for f in yearly['filters'] if f['key'] == 'period')
        self.assertEqual(period_filter['years'], ['2025', '2026'])
        all_time = build_context(df, QueryDict('period=all_time'), '/missing/final.xlsx', (2026, 1))
        self.assertEqual(all_time['selected']['period'], ['2025-01', '2026-01', '2026-02', '2026-12'])
        self.assertEqual(yearly['chart']['company_values'], all_time['chart']['company_values'])

    def test_company_year_is_independent_of_all_filters(self):
        baseline = build_context(frame(), QueryDict(''), '/missing/final.xlsx', (2026, 1))
        filtered = build_context(frame(), QueryDict('manager=missing&client=X&article=Q&period=2025-01&department=Trailers'), '/missing/final.xlsx', (2026, 1))
        self.assertEqual(baseline['chart']['company_values'], filtered['chart']['company_values'])
        self.assertEqual(len(filtered['chart']['company_months']), 12)
        self.assertEqual(filtered['chart']['company_values'][0][0], 1100)
        self.assertEqual(filtered['chart']['company_year'], 2026)

    def test_department_filters_are_linked_to_manager(self):
        df = frame()
        df['Менеджер'] = df['Менеджер'].replace({'A': 'Редько Вадим', 'B': 'Ушаков Алексей', 'C': 'Измайлов'})
        df = prepare_frame(df)
        selected = selections(QueryDict('department=Truck%26BUS&period='), df, '2026-01')
        self.assertEqual(set(apply_filters(df, selected)['Менеджер']), {'Редько Вадим'})
        self.assertEqual(facet_options(df, selected)['manager'], ['Редько Вадим'])
        selected = selections(QueryDict('department=Trailers&department=Aftermarket&period='), df, '2026-01')
        self.assertEqual(set(apply_filters(df, selected)['Менеджер']), {'Измайлов', 'Ушаков Алексей'})

    def test_multiselect_is_or_within_and_between(self):
        df = frame()
        selected = selections(QueryDict('manager=A&manager=B&article=P&period=2026-01'), df, '2026-01')
        result = apply_filters(df, selected)
        self.assertEqual(set(result['Менеджер']), {'A', 'B'})
        self.assertEqual(result['Факт, CNY'].sum(), 5000)

    def test_product_restricts_manager_client_and_period(self):
        df = frame()
        selected = selections(QueryDict('article=Q&period='), df, '2026-01')
        options = facet_options(df, selected)
        self.assertEqual(options['manager'], ['A', 'C'])
        self.assertEqual(options['client'], ['Z'])
        self.assertEqual(options['period'], ['2026-02'])

    def test_period_union_never_double_counts(self):
        df = frame()
        selected = selections(QueryDict('period=year_2026&period=month_2026_01'), df, '2026-01')
        self.assertEqual(len(apply_filters(df, selected)), len(df))

    def test_invalid_choice_does_not_become_whole_company(self):
        df = frame()
        selected = selections(QueryDict('manager=missing'), df, '2026-01')
        self.assertTrue(apply_filters(df, selected).empty)

    def test_anomalies_skip_unknown_period_and_detect_zero_and_high(self):
        result = deviations(frame(), {'2026-01', '2026-02'})
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['unknown_periods'], ['2026-12'])
        self.assertEqual(result['rows'][0]['manager'], 'B')

    def test_anomalies_skip_missing_price(self):
        df = frame()
        df.loc[df['Менеджер'] == 'A', PRICE] = 0
        self.assertEqual(deviations(df, {'2026-01'})['count'], 1)

    def test_dashboard_kpi_and_chart_have_same_filter(self):
        context = build_context(frame(), QueryDict('manager=A&period=2026-01&period=2026-02'), '/missing/final.xlsx', (2026, 1))
        self.assertEqual(context['kpi']['month_fact'], '200 ¥')
        self.assertEqual(context['chart']['month_values'][2], [0, 200])
