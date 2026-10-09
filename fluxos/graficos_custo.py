"""
🌟 NOVO: cálculo dos dois gráficos de custo de um fluxo de produção (SÓ LEITURA: não grava nada).

    1) Distribuição de Custo Variável: o custo variável médio (flu_pro_custo_variavel_medio) de TODOS os fluxos do
       mesmo produto/cenário do fluxo escolhido, em faixas (histograma).
    2) Custo por Equipamento (escada / cascata): a contribuição de custo variável de cada equipamento DENTRO do fluxo
       escolhido, consolidada por equipamento (soma as ordens dele), na ordem em que aparece na cadeia.

Antes esse cálculo ficava dentro das views do editor (fluxos/views.py). Agora fica aqui pra ser usado por dois
lugares sem duplicar nada: o editor (Assistente IA > Análise do fluxo) e o chat do Agente IA (Ação Comum
"Fluxos de Produção - Custo Variável Distribuição / Escada por Equipamento").
"""
import math

from django.db import connection

from .models import TbFluxoProducao

MAX_FAIXAS = 10                 # mesmo limite do gráfico do editor
MAX_FLUXOS_POR_FAIXA = 10       # o editor mostra os 10 de maior custo variável ao clicar numa barra


def dados_distribuicao_custo_variavel(fluxo):
    """
    Mesmo conteúdo que a view do editor sempre devolveu:
      - {'erro': ...}                                   -> fluxo sem produto
      - {'atualizado': False, 'mensagem': ...}          -> algum fluxo do produto com I/O desatualizado
      - {'atualizado': True, 'total_fluxos', 'fluxo_atual_id', 'valores': [{fluxo_id, descricao, custo_variavel_medio}]}
    """
    if fluxo.flu_pro_produto_id is None:
        return {'erro': 'Este fluxo não tem produto definido.'}

    fluxos_do_produto = TbFluxoProducao.objects.filter(
        flu_pro_produto_id=fluxo.flu_pro_produto_id,
        tbcenarios_id=fluxo.tbcenarios_id,
    )

    if fluxos_do_produto.filter(flu_pro_input_output_atualizado=False).exists():
        return {
            'atualizado': False,
            'mensagem': 'Temos fluxo de produção para esse produto desatualizado em I/O. '
                        'Favor atualizar e repetir o procedimento.',
        }

    valores = list(
        fluxos_do_produto.exclude(flu_pro_custo_variavel_medio__isnull=True)
        .values_list('id', 'flu_pro_descricao', 'flu_pro_custo_variavel_medio')
    )
    return {
        'atualizado': True,
        'total_fluxos': fluxos_do_produto.count(),
        'fluxo_atual_id': fluxo.id,
        'valores': [
            {'fluxo_id': fid, 'descricao': descricao or '', 'custo_variavel_medio': float(valor)}
            for fid, descricao, valor in valores
        ],
    }


def faixas_distribuicao(valores, fluxo_atual_id):
    """
    As FAIXAS do histograma, calculadas aqui no servidor com exatamente a mesma regra do JavaScript do editor
    (renderizarDistribuicaoCustoVariavel): até 10 faixas de mesma largura entre o menor e o maior valor. Usado pelo
    chat, que não pode receber a lista inteira (um produto pode ter centenas de milhares de fluxos): cada faixa leva
    só a quantidade e os 10 fluxos de maior custo (o que o editor mostra ao clicar numa barra).

    Devolve {'minimo', 'maximo', 'faixas': [{'inicio', 'fim', 'qtd', 'atual', 'fluxos': [...]}]} ou None se não há valores.
    """
    if not valores:
        return None
    numeros = [v['custo_variavel_medio'] for v in valores]
    minimo, maximo = min(numeros), max(numeros)
    num_faixas = min(MAX_FAIXAS, max(1, len(set(numeros))))
    amplitude = (maximo - minimo) or 1
    largura = amplitude / num_faixas

    def indice(v):
        i = math.floor((v - minimo) / largura) if largura > 0 else 0
        return min(max(i, 0), num_faixas - 1)

    por_faixa = [[] for _ in range(num_faixas)]
    for v in valores:
        por_faixa[indice(v['custo_variavel_medio'])].append(v)

    faixa_atual = -1
    for v in valores:
        if str(v['fluxo_id']) == str(fluxo_atual_id):
            faixa_atual = indice(v['custo_variavel_medio'])
            break

    faixas = []
    for i, lista in enumerate(por_faixa):
        maiores = sorted(lista, key=lambda v: v['custo_variavel_medio'], reverse=True)[:MAX_FLUXOS_POR_FAIXA]
        faixas.append({
            'inicio': minimo + i * largura,
            'fim': minimo + (i + 1) * largura,
            'qtd': len(lista),
            'atual': i == faixa_atual,
            'fluxos': maiores,
        })
    return {'minimo': minimo, 'maximo': maximo, 'faixas': faixas}


def dados_custo_por_equipamento(fluxo):
    """
    [{codigo, descricao, contribuicao_media}] -- contribuição de custo variável de cada equipamento do fluxo,
    consolidada por equipamento (soma as ordens), ordenada pela coluna em que o equipamento aparece primeiro.

    Roda a procedure calcula_contribuicao_custo_fluxo_producao, que grava o resultado numa tabela temporária
    (isolada por sessão do banco), e lê o nível 1 dela. Mesma consulta que a view do editor sempre usou.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "call public.calcula_contribuicao_custo_fluxo_producao(%s, %s)",
            [fluxo.tbcenarios_id, fluxo.id],
        )
        cursor.execute("""
            SELECT
                cad.equ_cad_codigo AS codigo,
                cad.equ_cad_descricao AS descricao,
                SUM(t.contribuicao_media) AS contribuicao_media,
                MIN(flu.flu_pro_inp_out_coluna) AS primeira_coluna
            FROM tmp_contribuicao_custo_fluxo t
            JOIN equipamentos_tbequipamentos eq ON eq.id = t.equipamento_id
            JOIN equipamentos_tbequipamentoscadastro cad ON cad.id = eq.equ_codigo_id
            JOIN fluxos_tbfluxoproducaoinputoutput flu
                ON flu.flu_pro_inp_out_equipamento_id = t.equipamento_id AND flu.mae_id = %s
            GROUP BY cad.equ_cad_codigo, cad.equ_cad_descricao
            ORDER BY primeira_coluna ASC
        """, [fluxo.id])
        colunas = [c[0] for c in cursor.description]
        linhas = [dict(zip(colunas, row)) for row in cursor.fetchall()]

    return [
        {
            'codigo': l['codigo'],
            'descricao': l['descricao'] or '',
            'contribuicao_media': float(l['contribuicao_media']),
        }
        for l in linhas
    ]