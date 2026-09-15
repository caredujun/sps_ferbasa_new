from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TabelasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tabelas'
    verbose_name = _("      TABELAS")  # 6 espaços