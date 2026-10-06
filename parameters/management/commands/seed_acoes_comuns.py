from django.core.management.base import BaseCommand
from parameters.models import AcaoComum, TbEmpresa


ACOES = [
    ('Indicadores', 'editar', 'Indicadores - Mudar Valor'),
    ('Indicadores', 'criar', 'Indicadores - Criar Novo'),
    ('Indicadores', 'eliminar', 'Indicadores - Eliminar'),
    ('Indicadores', 'massa', 'Indicadores - Reajuste em Massa'),
    ('Indicadores', 'grafico', 'Indicadores - Plotar Gráfico'),
    ('Câmbio', 'editar', 'Câmbio - Mudar Valor'),
    ('Câmbio', 'criar', 'Câmbio - Criar Novo'),
    ('Câmbio', 'eliminar', 'Câmbio - Eliminar'),
    ('Câmbio', 'massa', 'Câmbio - Reajuste em Massa'),
    ('Câmbio', 'grafico', 'Câmbio - Plotar Gráfico'),
    ('Equipamentos', 'criar', 'Equipamentos - Criar Novo Equipamento'),
    ('Equipamentos', 'criar_ordem', 'Equipamentos - Criar Nova Ordem de Produção'),
    ('Cenário', 'criar', 'Cenário - Criar Novo'),
    ('Cenário', 'mudar', 'Cenário - Mudar Tipo/Período'),
    ('Cenário', 'status', 'Cenário - Ver Status'),
    ('Cenário', 'atualizar_fluxos', 'Cenário - Atualizar Fluxos de Produção'),
    ('Cenário', 'limpar', 'Cenário - Limpar'),
    ('Cenário', 'otimizar', 'Cenário - Otimizar'),
    ('Cenário', 'consolidar', 'Cenário - Consolidar'),
    ('Cenário', 'ciclo_completo', 'Cenário - Ciclo Completo (Limpar+Otimizar+Consolidar)'),
    ('Cenário', 'excluir', 'Cenário - Excluir'),
    ('Cenário', 'exportar_excel', 'Cenário - Exportar Excel Resultados Financeiros do Cenário Ativo'),
    ('Cenário', 'exportar_dados_otimizacao', 'Cenário - Exportar Dados de Otimização do Cenário Ativo'),
    ('Custo Ferbasa', 'atualizar_producao_ggf_mensal', 'Custo Ferbasa - Atualizar Produção e GGF Mensal'),
    ('Custo Ferbasa', 'atualizar_consumo_especifico_custo_variavel', 'Custo Ferbasa - Atualizar Consumo Específico e Custo Variável'),
    ('Fluxos de Produção', 'criar_no_editor', 'Fluxos de Produção - Atualizar Visão do Fluxo de Produção'),
    ('Fluxos de Produção', 'criar_fluxos_produto', 'Fluxos de Produção - Atualizar Fluxos de Produção por Produto'),
    ('Fluxos de Produção', 'comparar_fluxos', 'Fluxos de Produção - Comparar Fluxos de Produção'),
]


class Command(BaseCommand):
    help = 'Cadastra, corrige os nomes e habilita as Ações Comuns para as empresas.'

    def handle(self, *args, **options):
        acoes_catalogo = []
        for categoria, chave, nome_exibicao in ACOES:
            acao, criada = AcaoComum.objects.get_or_create(
                categoria=categoria,
                chave=chave,
                defaults={'nome_exibicao': nome_exibicao},
            )

            # get_or_create não corrige o nome de registros antigos.
            if acao.nome_exibicao != nome_exibicao:
                acao.nome_exibicao = nome_exibicao
                acao.save(update_fields=['nome_exibicao'])
                self.stdout.write(f'Nome atualizado: {acao}')
            elif criada:
                self.stdout.write(f'Criada: {acao}')
            else:
                self.stdout.write(f'Já existia: {acao}')

            acoes_catalogo.append(acao)

        total_empresas = 0
        for empresa in TbEmpresa.objects.all():
            empresa.acoes_comuns_habilitadas.add(*acoes_catalogo)
            total_empresas += 1

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(acoes_catalogo)} ação(ões) no catálogo, habilitadas para {total_empresas} empresa(s).'
        ))