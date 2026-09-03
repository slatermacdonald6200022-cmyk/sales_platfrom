from django.db import models
from django.conf import settings


class ProcessingRun(models.Model):
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_SUCCESS, 'Завершена'),
        (STATUS_FAILED, 'Ошибка'),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='processing_runs',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    report_year = models.PositiveIntegerField(null=True, blank=True)
    report_month = models.PositiveSmallIntegerField(null=True, blank=True)
    source_currency = models.CharField(max_length=3, blank=True)
    exchange_rate = models.FloatField(null=True, blank=True)
    source_files = models.JSONField(default=list, blank=True)
    total_actual_rows = models.PositiveIntegerField(default=0)
    matched_rows = models.PositiveIntegerField(default=0)
    unmatched_rows = models.PositiveIntegerField(default=0)
    matched_amount_cny = models.FloatField(default=0)
    unmatched_amount_cny = models.FloatField(default=0)
    source_amount = models.FloatField(default=0)
    final_file = models.CharField(max_length=500, blank=True)
    unmatched_file = models.CharField(max_length=500, blank=True)
    manager_reports = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Обработка #{self.pk} — {self.get_status_display()}'

    @property
    def period_label(self):
        if not self.report_year or not self.report_month:
            return '—'
        return f'{self.report_month:02d}.{self.report_year}'
