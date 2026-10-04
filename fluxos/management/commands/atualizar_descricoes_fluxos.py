"""
Comando de uso ÚNICO: recalcula e grava a descrição (flu_pro_descricao)
de TODOS os TbFluxoProducao já cadastrados no banco, usando o mesmo
critério adotado em fluxos/gerar_fluxos_por_produto.py
(_construir_descricao) -- fornos como espinha dorsal, minério/transporte/
energia entre parênteses, prefixo "CT"/"CP", REFINO e LING incluídos.

Não mexe em nenhuma outra coisa do fluxo (colunas, linhas, consumo
padrão, flu_pro_erro) -- só reescreve o texto da descrição.

Uso:
    python manage.py atualizar_descricoes_fluxos              (grava de verdade)
    python manage.py atualizar_descricoes_fluxos --dry-run    (só mostra, não grava nada)
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from fluxos.gerar_fluxos_por_produto import (
    _construir_descricao, _equipamentos_do_produto, _montar_grafo, GeracaoFluxoError,
)
from fluxos.models import TbFluxoProducao, TbFluxoProducaoDaugther
from produtos.models import TbProdutos


class Command(BaseCommand):
    help = (
        'Uso único: recalcula a descrição de todos os fluxos de produção já '
        'cadastrados, no mesmo formato usado pelo gerador novo (fornos + '
        'minério/energia entre parênteses, CT/CP, REFINO/LING).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Só mostra o que mudaria, sem gravar nada no banco.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Agrupa os fluxos por (produto_id, cenario_id) -- montar o grafo
        # de equipamentos é caro; reaproveita pra todos os fluxos do
        # MESMO produto/cenário em vez de refazer fluxo a fluxo.
        grupos = defaultdict(list)
        for mae_id, produto_id, cenario_id in TbFluxoProducao.objects.values_list(
            'id', 'flu_pro_produto_id', 'tbcenarios_id'
        ):
            if produto_id is None or cenario_id is None:
                continue
            grupos[(produto_id, cenario_id)].append(mae_id)

        total_atualizados = 0
        total_sem_produto = 0
        total_erro_grafo = 0
        total_sem_conteudo = 0
        total_erro_descricao = 0

        for (produto_id, cenario_id), mae_ids in grupos.items():
            try:
                produto_codigo = TbProdutos.objects.get(id=produto_id).pro_codigo
            except TbProdutos.DoesNotExist:
                total_sem_produto += len(mae_ids)
                self.stdout.write(self.style.WARNING(
                    f'Produto id={produto_id} não encontrado -- pulando {len(mae_ids)} fluxo(s).'
                ))
                continue

            try:
                equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
                entradas_por_destino, _ = _montar_grafo(equipamentos, cenario_id)
            except GeracaoFluxoError as erro:
                total_erro_grafo += len(mae_ids)
                self.stdout.write(self.style.ERROR(
                    f'Não consegui montar o grafo do produto {produto_codigo} '
                    f'(id={produto_id}): {erro} -- pulando {len(mae_ids)} fluxo(s).'
                ))
                continue

            # cp_id -> to_id e cp_id -> from_id, montados uma vez só pro
            # produto/cenário inteiro.
            cp_para_to = {}
            cp_para_from = {}
            for to_id, entradas in entradas_por_destino.items():
                for (from_id, _criterio, cp_id) in entradas:
                    cp_para_to[cp_id] = to_id
                    cp_para_from[cp_id] = from_id

            daugthers = TbFluxoProducaoDaugther.objects.filter(mae_id__in=mae_ids).values_list(
                'mae_id', 'flu_pro_dau_consumo_padrao_id'
            )
            cp_ids_por_mae = defaultdict(list)
            for mae_id, cp_id in daugthers:
                cp_ids_por_mae[mae_id].append(cp_id)

            for mae_id in mae_ids:
                cp_ids = cp_ids_por_mae.get(mae_id, [])
                # Só entram no "plano" os consumo_padrao_id que ainda
                # existem no grafo ATUAL do produto -- um cp_id órfão
                # (cadastro mudou depois que o fluxo foi gravado) é
                # ignorado na descrição, não trava o fluxo inteiro.
                plano = tuple(
                    (cp_para_from[cp_id], '', cp_id)
                    for cp_id in cp_ids
                    if cp_id in cp_para_from
                )
                if not plano:
                    total_sem_conteudo += 1
                    continue

                try:
                    nova_descricao = _construir_descricao(plano, cp_para_to, equipamentos, produto_codigo)
                except Exception as erro:
                    total_erro_descricao += 1
                    self.stdout.write(self.style.ERROR(
                        f'Erro montando descrição do fluxo id={mae_id}: {erro}'
                    ))
                    continue

                nova_descricao = nova_descricao[:150]

                if dry_run:
                    self.stdout.write(f'[{mae_id}] {nova_descricao}')
                else:
                    TbFluxoProducao.objects.filter(id=mae_id).update(flu_pro_descricao=nova_descricao)
                total_atualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{total_atualizados} fluxo(s) {'analisado(s) (dry-run)' if dry_run else 'atualizado(s)'}.\n"
            f'{total_sem_produto} pulado(s) por produto não encontrado.\n'
            f'{total_erro_grafo} pulado(s) por erro ao montar o grafo do produto.\n'
            f'{total_sem_conteudo} pulado(s) por não ter nenhum consumo padrão reconhecido.\n'
            f'{total_erro_descricao} com erro ao montar a descrição.'
        ))