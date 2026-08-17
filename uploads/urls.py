from django.urls import path
from . import views

urlpatterns = [
    path('', views.upload_view, name='upload_files'),
    path('readiness/', views.readiness_view, name='readiness'),
    path('compare/', views.compare_view, name='compare_versions'),
]