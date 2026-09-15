from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OtimizacaoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'otimizacao'
    verbose_name = _('  Resultados Otimização')  # 2 espaços