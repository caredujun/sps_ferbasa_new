"""
🌟 NOVO (30/10/2026): MAPA DA EXPLOSÃO de fluxos de um produto.

Quando a geração recusa um produto ("O terminal X geraria N fluxos, acima do
limite..."), esta tela mostra, no mesmo visual do editor de fluxos, o GRAFO
COMPLETO de ligações do produto descendo a partir do terminal -- todas as
origens possíveis de cada equipamento, de uma vez (não é um fluxo; é a
sobreposição de todos os fluxos que seriam gerados).

Nada é gravado no banco: nenhum TbFluxoProducao é criado, a tela é só de
leitura.

Cores:
  - cabeçalho VERMELHO: equipamento onde a quantidade se multiplica (tem
    mais de uma origem alternativa no mesmo critério de quebra);
  - ligação VERMELHA: cada uma das origens alternativas desse ponto (cada
    uma vira um fluxo diferente);
  - cabeçalho AZUL ESCURO: o terminal.
Cada cartão mostra quantos fluxos saem a partir dele (mesma contagem usada
na recusa) e, nos pontos vermelhos, o critério e o fator (x2, x3...).
"""
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render

from equipamentos.models import TbEquipamentosCadastro
from produtos.models import TbProdutos

from .gerar_fluxos_por_produto import (
    GeracaoFluxoError,
    _contar_planos,
    _encontrar_terminais,
    _equipamentos_do_produto,
    _membros_efetivos_por_criterio,
    _montar_grafo,
)

ESPACO_COLUNA = 300
ESPACO_LINHA = 210
MARGEM = 50


def _n(v):
    return f"{v:,}".replace(',', '.')


def _esc(texto):
    return (str(texto or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def montar_mapa_explosao(produto_id, terminal_id=None):
    """Devolve {'produto', 'terminais': [{'id','rotulo','total'}], 'terminal_id', 'drawflow', 'pontos',
    'ligacoes_alternativas'}. Lança GeracaoFluxoError se o produto não tiver grafo."""
    produto = TbProdutos.objects.filter(id=produto_id).first()
    if produto is None:
        raise GeracaoFluxoError(f"Produto id={produto_id} não encontrado.")
    cenario_id = produto.tbcenarios_id
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    entradas_por_destino, saidas_de = _montar_grafo(equipamentos, cenario_id)
    terminais = _encontrar_terminais(equipamentos, saidas_de)

    codigos = dict(TbEquipamentosCadastro.objects
                   .filter(id__in={e.equ_codigo_id for e in equipamentos.values()})
                   .values_list('id', 'equ_cad_codigo'))

    def rotulo(eid):
        eq = equipamentos.get(eid)
        return f"{codigos.get(eq.equ_codigo_id, '?')}/{eq.equ_ordem_codigo}" if eq else str(eid)

    memo = {}
    lista_terminais = []
    for t in terminais:
        total = _contar_planos(t.id, entradas_por_destino, equipamentos, memo=memo)
        lista_terminais.append({'id': t.id, 'rotulo': rotulo(t.id), 'total': total, 'total_txt': _n(total)})
    lista_terminais.sort(key=lambda t: -t['total'])
    if terminal_id is None or terminal_id not in {t['id'] for t in lista_terminais}:
        terminal_id = lista_terminais[0]['id']     # o que mais explode

    # Grafo descendo do terminal (só as origens efetivas, mesma regra da geração).
    grupos_de = {}
    fila, vistos = [terminal_id], {terminal_id}
    while fila:
        eid = fila.pop(0)
        grupos_de[eid] = _membros_efetivos_por_criterio(eid, entradas_por_destino, equipamentos)
        for _criterio, membros in grupos_de[eid]:
            for from_id, _c, _cp in membros:
                if from_id not in vistos:
                    vistos.add(from_id)
                    fila.append(from_id)

    # Distância MÁXIMA até o terminal -> coluna (terminal na última coluna, ligações sempre da esquerda p/ direita).
    distancia = {}

    def dist(eid, pilha=()):
        if eid in distancia:
            return distancia[eid]
        if eid in pilha:
            return 0
        destinos = [d for d in grupos_de if any(m[0] == eid for _c, ms in grupos_de[d] for m in ms)]
        distancia[eid] = 0 if eid == terminal_id or not destinos else 1 + max(dist(d, pilha + (eid,)) for d in destinos)
        return distancia[eid]

    for eid in vistos:
        dist(eid)
    maior = max(distancia.values()) if distancia else 0
    coluna = {eid: maior - d for eid, d in distancia.items()}

    # Ligações (de -> para) e marcação das alternativas.
    ligacoes, alternativas, pontos = [], set(), []
    fator_de, texto_ponto = {}, {}
    for eid, grupos in grupos_de.items():
        partes, fator = [], 1
        for criterio, membros in grupos:
            for from_id, _c, _cp in membros:
                ligacoes.append((from_id, eid))
                if len(membros) > 1:
                    alternativas.add((from_id, eid))
            if len(membros) > 1:
                fator *= len(membros)
                partes.append({'criterio': criterio, 'qtd': len(membros),
                               'origens': [rotulo(m[0]) for m in membros]})
        if fator > 1:
            fator_de[eid] = fator
            texto_ponto[eid] = ' · '.join(f"{p['criterio']} x{p['qtd']}" for p in partes)
            pontos.append({'id': eid, 'rotulo': rotulo(eid), 'nivel': distancia.get(eid, 0), 'fator': fator,
                           'fluxos': _n(memo.get(eid, 1)), 'partes': partes})
    pontos.sort(key=lambda p: (p['nivel'], p['rotulo']))

    # Ordem vertical em cada coluna: baricentro (reduz cruzamentos), poucas passadas.
    por_coluna = defaultdict(list)
    for eid in vistos:
        por_coluna[coluna[eid]].append(eid)
    for c in por_coluna:
        por_coluna[c].sort(key=lambda e: rotulo(e))
    pos = {e: i for c in por_coluna for i, e in enumerate(por_coluna[c])}
    vizinhos = defaultdict(list)
    for de, para in ligacoes:
        vizinhos[de].append(para)
        vizinhos[para].append(de)
    for _ in range(6):
        for c in sorted(por_coluna):
            por_coluna[c].sort(key=lambda e: (sum(pos[v] for v in vizinhos[e]) / len(vizinhos[e])) if vizinhos[e] else pos[e])
            for i, e in enumerate(por_coluna[c]):
                pos[e] = i
    maior_coluna = max((len(v) for v in por_coluna.values()), default=1)

    nodes = {}
    for eid in vistos:
        eq = equipamentos[eid]
        c = coluna[eid]
        offset = 0   # 🌟 v2: colunas alinhadas no TOPO (como no editor), não centralizadas
        if eid == terminal_id:
            cor, classe = '#1f3a5f', 'diag-terminal'
        elif eid in fator_de:
            cor, classe = '#c0392b', 'diag-ponto'
        else:
            cor, classe = '#3498db', ''
        selo = ''
        if eid in fator_de:
            selo = (f'<div class="diag-selo">x{_n(fator_de[eid])} &nbsp;—&nbsp; {_esc(texto_ponto[eid])}</div>')
        html = (
            f'<div class="equipamento-node {classe}" style="width:220px;border-color:{cor};">'
            f'<div class="equipamento-header" style="background-color:{cor};color:#fff;padding:6px 8px;">'
            f'<div class="equipamento-titulo">{_esc(rotulo(eid))}</div></div>'
            '<div class="equipamento-body" style="padding:6px 8px;background:#fff;">'
            f'<img src="/fluxo_producao/equipamento_imagem/{eid}/" class="diag-img" '
            'onerror="this.onerror=null;this.src=\'/static/fluxo_producao/img/equipamento-placeholder.png\';">'
            f'<div class="diag-desc">{_esc(eq.equ_ordem_descricao)}</div>'
            f'<div class="diag-conta">{_n(memo.get(eid, 1))} fluxo(s) a partir daqui</div>'
            f'{selo}</div></div>'
        )
        nodes[str(eid)] = {
            'id': eid, 'name': rotulo(eid), 'data': {'equipamento_id': eid}, 'class': 'equipamento',
            'html': html, 'typenode': False,
            'inputs': {'input_1': {'connections': []}}, 'outputs': {'output_1': {'connections': []}},
            'pos_x': MARGEM + c * ESPACO_COLUNA,
            'pos_y': MARGEM + offset + pos[eid] * ESPACO_LINHA,
        }
    for de, para in set(ligacoes):
        if str(de) in nodes and str(para) in nodes:
            nodes[str(de)]['outputs']['output_1']['connections'].append({'node': str(para), 'output': 'input_1'})
            nodes[str(para)]['inputs']['input_1']['connections'].append({'node': str(de), 'input': 'output_1'})

    terminal_info = next(t for t in lista_terminais if t['id'] == terminal_id)
    return {
        'produto': produto,
        'terminais': lista_terminais,
        'terminal': terminal_info,
        'drawflow': {'drawflow': {'Home': {'data': nodes}}},
        'pontos': pontos,
        'ligacoes_alternativas': [[str(a), str(b)] for a, b in sorted(alternativas)],
        'qtd_equipamentos': len(vistos),
    }


@login_required
def diagnostico_explosao_view(request, produto_id):
    from .views import _empresa_efetiva_id_ou_403
    empresa_id = _empresa_efetiva_id_ou_403(request.user)
    produto = TbProdutos.objects.filter(id=produto_id).select_related('tbcenarios').first()
    if produto is None:
        raise Http404("Produto não encontrado.")
    if getattr(produto.tbcenarios, 'empresa_id', None) != empresa_id:
        raise PermissionDenied("Produto de outra empresa.")

    terminal_id = request.GET.get('terminal')
    terminal_id = int(terminal_id) if terminal_id and terminal_id.isdigit() else None
    contexto = {'produto': produto}
    try:
        contexto.update(montar_mapa_explosao(produto_id, terminal_id))
    except GeracaoFluxoError as erro:
        contexto['erro'] = str(erro)
    return render(request, 'fluxo_producao/diagnostico_explosao.html', contexto)
