from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class SalesManager(models.Model):
    key = models.SlugField(max_length=64, unique=True, editable=False)
    name = models.CharField(max_length=255, verbose_name='Имя менеджера')
    department = models.CharField(max_length=120, blank=True, verbose_name='Отдел')
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='sales_manager')
    legacy_username = models.CharField(max_length=150, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'key']

    def __str__(self):
        return self.name


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


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Каждая новая учётная запись получает профиль с безопасной ролью менеджера."""
    if created:
        Profile.objects.get_or_create(user=instance)
