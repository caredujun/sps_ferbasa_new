"""
🌟 NOVO: preenchimento inicial de TbEquipamentos.equ_produtos, rodado
manualmente (não em migration) depois de aplicar o migrate que cria o
campo:

    python manage.py popular_produtos_participantes

Para cada equipamento/ordem, marca como "Produto Participante" todo
produto que tenha PELO MENOS UM fluxo de produção cadastrado
(TbFluxoProducao, "mãe" da linha do sequenciamento) em que esse
equipamento apareça como origem OU destino de algum consumo padrão.

Não precisa "parar no primeiro fluxo encontrado e pular pro próximo"
feito à mão, passo a passo -- o .distinct() da consulta já garante isso:
o banco devolve cada produto UMA vez só, não importa quantos fluxos
daquele produto contenham o equipamento -- sem processamento redundante
por fluxo repetido do mesmo produto.

Idempotente: pode rodar de novo sem problema (usa .set(), que substitui
a lista inteira a cada execução, em vez de ir só acrescentando).
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from equipamentos.models import TbEquipamentos
from fluxos.models import TbFluxoProducaoDaugther


class Command(BaseCommand):
    help = (
        'Preenche TbEquipamentos.equ_produtos: marca, pra cada equipamento/ordem, '
        'os produtos que têm esse equipamento em algum dos seus fluxos de produção.'
    )

    def handle(self, *args, **options):
        total_equipamentos = 0
        total_com_produto = 0
        equipamentos_sem_produto = []

        equipamentos = (
            TbEquipamentos.objects.all()
            .select_related('equ_codigo')
            .only('id', 'equ_codigo', 'equ_ordem_codigo', 'equ_ordem_descricao')
        )
        total_geral = equipamentos.count()

        for indice, equipamento in enumerate(equipamentos.iterator(), start=1):
            total_equipamentos += 1

            produtos_ids = (
                TbFluxoProducaoDaugther.objects
                .filter(
                    Q(flu_pro_dau_consumo_padrao__flu_con_pad_from_equipamento_id=equipamento.id)
                    | Q(flu_pro_dau_consumo_padrao__flu_con_pad_to_equipamento_id=equipamento.id)
                )
                .exclude(mae__flu_pro_produto_id__isnull=True)
                .values_list('mae__flu_pro_produto_id', flat=True)
                .distinct()
            )

            if produtos_ids:
                equipamento.equ_produtos.set(produtos_ids)
                total_com_produto += 1
            else:
                equipamento.equ_produtos.clear()
                equipamentos_sem_produto.append((equipamento.id, str(equipamento)))

            if indice % 200 == 0:
                self.stdout.write(f'  ... {indice}/{total_geral} equipamentos processados')

        self.stdout.write(self.style.SUCCESS(
            f'\nConcluído: {total_equipamentos} equipamento(s) processados. '
            f'{total_com_produto} receberam ao menos um produto participante; '
            f'{len(equipamentos_sem_produto)} ficaram sem nenhum (nenhum fluxo cadastrado com esse equipamento).'
        ))

        if equipamentos_sem_produto:
            self.stdout.write(self.style.WARNING('\nEquipamentos sem nenhum produto participante:'))
            for equipamento_id, descricao in equipamentos_sem_produto:
                self.stdout.write(f'  - [{equipamento_id}] {descricao}')