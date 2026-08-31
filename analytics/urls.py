from django.urls import path
from . import views

urlpatterns = [
    path('processing/', views.processing_page_view, name='processing_page'),
    path('processing/run/', views.run_etl_api, name='run_etl_api'),
    path('processing/download/', views.download_final_excel, name='download_final_excel'),
]