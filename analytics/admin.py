from django.contrib import admin

from .models import ProcessingRun


@admin.register(ProcessingRun)
class ProcessingRunAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'created_at', 'user', 'status', 'report_year', 'report_month',
        'matched_rows', 'unmatched_rows',
    )
    list_filter = ('status', 'source_currency', 'report_year', 'report_month')
    search_fields = ('user__username', 'error_message')
    readonly_fields = ('created_at', 'finished_at')
