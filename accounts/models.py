from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    ROLE_CHOICES = [
        ('manager', 'Менеджер'),
        ('analyst', 'Аналитик'),
        ('director', 'Директор'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    manager_name = models.CharField(max_length=255, verbose_name="ФИО Менеджера", blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='manager', verbose_name="Роль")
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, verbose_name="Аватарка")

    def __str__(self):
        return f"{self.manager_name or self.user.username} ({self.get_role_display()})"