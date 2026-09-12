from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from parameters.models import PerfilUsuario


class Command(BaseCommand):
    help = "Cria PerfilUsuario para usuários existentes que ainda não têm um."

    def handle(self, *args, **options):
        User = get_user_model()
        criados = 0
        for usuario in User.objects.all():
            _, foi_criado = PerfilUsuario.objects.get_or_create(usuario=usuario)
            if foi_criado:
                criados += 1
        total = User.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"{criados} perfil(is) criado(s). {total - criados} já existiam."
        ))