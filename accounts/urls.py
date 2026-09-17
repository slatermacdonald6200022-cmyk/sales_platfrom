from django.urls import path
from . import views
from . import manager_views

urlpatterns = [
    path('profile/managers/', manager_views.manager_list, name='manage_managers'),
    path('profile/managers/add/', manager_views.manager_edit, name='manager_add'),
    path('profile/managers/<int:pk>/edit/', manager_views.manager_edit, name='manager_edit'),
    path('profile/managers/<int:pk>/status/', manager_views.manager_toggle, name='manager_toggle'),
    path('profile/managers/<int:pk>/delete/', manager_views.manager_delete, name='manager_delete'),
    path('', views.home_view, name='home'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('deviations/', views.dashboard_view, {'deviations_page': True}, name='sales_deviations'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
]
