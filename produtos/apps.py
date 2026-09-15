from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProdutosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'produtos'
    verbose_name = _('    PRODUTOS')  # 4 espaços