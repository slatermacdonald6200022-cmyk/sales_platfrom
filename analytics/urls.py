from django.urls import path
from . import views

urlpatterns = [
    path('history/<int:run_id>/issues/', views.processing_issues, name='processing_issues'),
    path('history/<int:run_id>/issues/map/', views.save_manual_mapping, name='save_manual_mapping'),
    path('history/<int:run_id>/issues/map/delete/', views.delete_manual_mapping, name='delete_manual_mapping'),
    path('processing/combined/', views.download_combined_excel, name='download_combined_excel'),
    path('processing/', views.processing_page_view, name='processing_page'),
    path('processing/run/', views.run_etl_api, name='run_etl_api'),
    path('processing/download/', views.download_final_excel, name='download_final_excel'),
    path('history/', views.processing_history, name='processing_history'),
    path('history/<int:run_id>/', views.processing_run_detail, name='processing_run_detail'),
    path('history/<int:run_id>/download/<str:file_kind>/', views.download_processing_file, name='download_processing_file'),
]
