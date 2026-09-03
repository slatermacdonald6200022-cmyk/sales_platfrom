import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcessingRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('running', 'Выполняется'), ('success', 'Завершена'), ('failed', 'Ошибка')], default='running', max_length=16)),
                ('report_year', models.PositiveIntegerField(blank=True, null=True)),
                ('report_month', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('source_currency', models.CharField(blank=True, max_length=3)),
                ('exchange_rate', models.FloatField(blank=True, null=True)),
                ('source_files', models.JSONField(blank=True, default=list)),
                ('total_actual_rows', models.PositiveIntegerField(default=0)),
                ('matched_rows', models.PositiveIntegerField(default=0)),
                ('unmatched_rows', models.PositiveIntegerField(default=0)),
                ('matched_amount_cny', models.FloatField(default=0)),
                ('unmatched_amount_cny', models.FloatField(default=0)),
                ('source_amount', models.FloatField(default=0)),
                ('final_file', models.CharField(blank=True, max_length=500)),
                ('unmatched_file', models.CharField(blank=True, max_length=500)),
                ('manager_reports', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='processing_runs', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
