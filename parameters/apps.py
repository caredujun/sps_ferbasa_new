from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ParametersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'parameters'
    verbose_name = _("       PARÂMETROS")  # 7 espaços