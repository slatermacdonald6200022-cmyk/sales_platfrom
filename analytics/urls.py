from django.urls import path
from . import views

urlpatterns = [
    path('upload/', views.upload_view, name='upload_page'),
    path('processing/', views.processing_view, name='readiness_page'),
    path('dashboard/', views.dashboard_view, name='compare_page'),
]