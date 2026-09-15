from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EquipamentosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'equipamentos'
    verbose_name = _("     EQUIPAMENTOS")  # 5 espaços