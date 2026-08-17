from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def upload_view(request):
    # Каркас списка 10 менеджеров
    managers_slots = [
        {"name": "Царев Михаил", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Хуснутдинов", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Редько", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Измайлов", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Мустафин", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Поляков", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Прасолов Николай, Соловьёв Виктор", "status": "Не загружен", "badge_class": "bg-secondary",
         "file_name": None},
        {"name": "Фомичев Владимир", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Александр Хорошевский", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
        {"name": "Ушаков Алексей", "status": "Не загружен", "badge_class": "bg-secondary", "file_name": None},
    ]
    return render(request, 'analytics/upload.html', {'managers_slots': managers_slots})


@login_required
def processing_view(request):
    return render(request, 'analytics/processing.html')


@login_required
def dashboard_view(request):
    return render(request, 'analytics/dashboard.html')


from django.shortcuts import render

# Create your views here.
