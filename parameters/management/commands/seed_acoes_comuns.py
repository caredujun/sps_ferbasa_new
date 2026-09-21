from django.core.management.base import BaseCommand
from parameters.models import AcaoComum, TbEmpresa


# 🌟 NOVO (Ações Comuns por empresa): catálogo fechado das ações
# individuais que existem hoje no menu "Ações Comuns" do chat. Rodar
# esse comando UMA VEZ depois de aplicar a migration que cria a tabela
# AcaoComum (python manage.py makemigrations && python manage.py
# migrate) -- ele cadastra as ações (se ainda não existirem) e já
# HABILITA todas elas pra TODAS as empresas já cadastradas, pra ninguém
# perder acesso a nada que já funcionava antes dessa funcionalidade
# existir. Depois disso, um superusuário pode ir na tela de Empresa e
# DESMARCAR só as ações que quiser restringir pra uma empresa
# específica.
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

    ('Cenário', 'criar', 'Cenário - Criar Novo'),
    ('Cenário', 'mudar', 'Cenário - Mudar Tipo/Período'),
    ('Cenário', 'status', 'Cenário - Ver Status'),
    ('Cenário', 'limpar', 'Cenário - Limpar'),
    ('Cenário', 'otimizar', 'Cenário - Otimizar'),
    ('Cenário', 'consolidar', 'Cenário - Consolidar'),
    ('Cenário', 'ciclo_completo', 'Cenário - Ciclo Completo (Limpar+Otimizar+Consolidar)'),
    ('Cenário', 'excluir', 'Cenário - Excluir'),
    ('Cenário', 'exportar_excel', 'Cenário - Exportar Excel Resultados Financeiros do Cenário Ativo'),
    ('Cenário', 'exportar_dados_otimizacao', 'Cenário - Exportar Dados de Otimização do Cenário Ativo'),

    # 🌟 NOVO: primeira ação do grupo "Custo Ferbasa" -- cadastrada só
    # como entrada no catálogo por enquanto (aparece na tela de Empresa
    # pra habilitar/desabilitar), SEM nenhuma lógica de chat/detector
    # implementada ainda no Agente IA. Isso vem numa próxima etapa.
    ('Custo Ferbasa', 'atualizar_producao_ggf_mensal', 'Custo Ferbasa - Atualizar Produção e GGF Mensal'),
]


class Command(BaseCommand):
    help = "Cadastra o catálogo de Ações Comuns e habilita todas pras empresas já existentes."

    def handle(self, *args, **options):
        acoes_criadas = []
        for categoria, chave, nome_exibicao in ACOES:
            acao, criada = AcaoComum.objects.get_or_create(
                categoria=categoria, chave=chave,
                defaults={'nome_exibicao': nome_exibicao},
            )
            acoes_criadas.append(acao)
            if criada:
                self.stdout.write(f"Criada: {acao}")
            else:
                self.stdout.write(f"Já existia: {acao}")

        total_empresas = 0
        for empresa in TbEmpresa.objects.all():
            empresa.acoes_comuns_habilitadas.add(*acoes_criadas)
            total_empresas += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{len(acoes_criadas)} ação(ões) no catálogo, habilitadas pra {total_empresas} empresa(s)."
        ))