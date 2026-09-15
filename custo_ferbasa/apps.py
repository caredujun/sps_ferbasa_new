from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CustoFerbasaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'custo_ferbasa'
    verbose_name = _(' Custo Ferbasa - CF')  # 1 espaço