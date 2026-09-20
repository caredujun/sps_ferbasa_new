from django.core.management.base import BaseCommand
from parameters.models import AgenteConfig, TbEmpresa


class Command(BaseCommand):
    help = (
        "Copia o(s) Comportamento(s) Agente IA já existentes (de antes da migration que "
        "adicionou o campo empresa) para CADA empresa cadastrada, evitando que o "
        "comportamento configurado hoje deixe de valer pra todo mundo depois da mudança "
        "pra 'um conjunto de comportamentos por empresa'. Rodar UMA VEZ, depois de aplicar "
        "a migration (python manage.py makemigrations && python manage.py migrate)."
    )

    def handle(self, *args, **options):
        configs_sem_empresa = list(AgenteConfig.objects.filter(empresa__isnull=True))
        if not configs_sem_empresa:
            self.stdout.write(self.style.WARNING(
                "Não encontrei nenhum Comportamento Agente IA sem empresa (empresa=None) -- "
                "ou já foi migrado antes, ou nunca existiu nenhum. Nada a fazer."
            ))
            return

        # 🌟 Usa o que estiver marcado como "ativo" (o comportamento de
        # verdade em uso hoje); se por algum motivo não tiver nenhum
        # ativo entre os sem empresa, usa o primeiro como referência.
        config_referencia = next((c for c in configs_sem_empresa if c.ativo), configs_sem_empresa[0])

        total_empresas = 0
        total_criados = 0
        for empresa in TbEmpresa.objects.all():
            total_empresas += 1
            if AgenteConfig.objects.filter(empresa=empresa).exists():
                self.stdout.write(f"{empresa.emp_nome}: já tem comportamento próprio, pulando.")
                continue
            AgenteConfig.objects.create(
                nome=config_referencia.nome,
                prompt_sistema=config_referencia.prompt_sistema,
                ativo=True,
                empresa=empresa,
            )
            total_criados += 1
            self.stdout.write(f"{empresa.emp_nome}: comportamento copiado.")

        self.stdout.write(self.style.SUCCESS(
            f"\n{total_criados} comportamento(s) criado(s), de {total_empresas} empresa(s) no total."
        ))
        self.stdout.write(
            "Os registros antigos sem empresa (empresa=None) continuam no banco, mas não são "
            "mais usados pelo Agente IA -- pode revisar e apagar manualmente pelo Admin, se quiser."
        )