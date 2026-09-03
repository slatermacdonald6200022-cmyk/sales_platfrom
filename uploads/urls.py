from django.urls import path
from . import views

urlpatterns = [
    path('', views.upload_view, name='upload_files'),
    path('download-manager-report/<str:manager_id>/', views.download_manager_report_view, name='download_manager_report'),
    path('delete/<str:file_type>/<str:target_id>/', views.delete_file_view, name='delete_file'),
    path('readiness/', views.readiness_view, name='readiness'),
    path('compare/', views.compare_view, name='compare'),
]
