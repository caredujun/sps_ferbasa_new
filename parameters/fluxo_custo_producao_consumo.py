"""
🌟 NOVO (27/10/2026): Ação Comum "Custo Ferbasa - Custo Produção x Custo Consumo" (só consulta: não altera nada).

Mesmo caminho do "Montar Fluxo de Produção Base Produção Mensal": o usuário marca um ou mais itens de produção (só os
que têm Produção Mensal), escolhe o período (ano/mês início e fim) e recebe a tabela mês a mês com o custo do item
quando PRODUZIDO x o custo com que ele ENTROU NO CONSUMO -- total, variável e fixo/outros, valor e por unidade. O cálculo
fica em custo_ferbasa/custo_producao_consumo.py; a tela desenha a tabela (com "Exportar Excel") a partir do marcador
[CUSTO_PRODUCAO_CONSUMO:...].

As etapas ('cpc_itens', 'cpc_periodo') são registradas no mesmo despachante das outras ações de processar
(fluxo_criar_cenario._HANDLERS_PROCESSAR) quando este módulo é importado (agents.py importa iniciar_fluxo_...).
"""
import re

from . import fluxo_criar_cenario as _fcc
from .fluxo_criar_cenario import (FLUXO_PROCESSAR, _get_estado, _encerrar_fluxo, _equ_json, _equ_marcador,
                                  _cfx_form_periodo, _cmp_moeda)


def iniciar_fluxo_custo_producao_x_consumo(usuario):
    from custo_ferbasa.custo_producao_consumo import itens_producao_ou_subproduto
    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        return "Não consegui identificar a sua empresa. Escolha uma empresa ativa antes."
    if not itens_producao_ou_subproduto(empresa_id):
        return ("Não encontrei nenhum registro em Produção Mensal. Atualize a Produção Mensal (Custo Ferbasa) antes de "
                "comparar o custo de produção com o custo de consumo.")
    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'cpc_itens'
    estado.dados_coletados = {'empresa_id': empresa_id}
    estado.save()
    return _cpc_texto_itens(estado.dados_coletados)


def _tipo_item_cpc(r):
    return ('produção e subproduto' if r['produzido'] and r['subproduto'] else
            'subproduto' if r['subproduto'] else 'produção')


def _cpc_texto_itens(dados, aviso=''):
    # 🌟 28/10/2026: além dos itens de produção, os SUBPRODUTOS (itens gerados com consumo negativo na Produção Mensal)
    from custo_ferbasa.custo_producao_consumo import itens_producao_ou_subproduto
    itens = [{'id': r['id'], 'codigo': r['codigo'], 'nome': r['descricao'], 'status': 'escolha',
              'motivo': f"{r['descricao']} ({_tipo_item_cpc(r)})", 'marcado': False,
              'titulo': f"{r['descricao']} -- {_tipo_item_cpc(r)} -- Produção Mensal de {r['minimo']} a {r['maximo']}"}
             for r in itens_producao_ou_subproduto(dados['empresa_id'])]
    return (aviso + "Quais **itens de produção ou subprodutos** você quer comparar? Marque um ou mais e confirme -- para "
            "cada mês mostro o custo do item **quando produzido**, **quando gerado como subproduto**, os dois "
            "**consolidados** e o custo com que ele **entrou no consumo** (total, variável e fixo/outros). Com mais de um "
            "item, os valores são a média ponderada dos itens escolhidos. Também dá pra digitar os códigos separados por "
            "vírgula (ou \"cancelar\" pra desistir).\n\n"
            + _equ_marcador('SELECAO_PRODUTOS', {'produtos': itens, 'rotulo_confirmar': 'Confirmar itens',
                                                  'ordem_alternavel': 'nome', 'rotulo_marcar': 'Marcar todos'}))


def _etapa_cpc_itens(estado, texto):
    from custo_ferbasa.custo_producao_consumo import itens_producao_ou_subproduto
    dados = estado.dados_coletados or {}
    disponiveis = {r['id']: r for r in itens_producao_ou_subproduto(dados['empresa_id'])}
    entrada = _equ_json(texto)
    ids = []
    if entrada is not None and isinstance(entrada.get('produtos'), list):
        for valor in entrada['produtos']:
            try:
                ids.append(int(valor))
            except (TypeError, ValueError):
                pass
    else:       # códigos digitados
        por_codigo = {str(r['codigo']).strip().upper(): r['id'] for r in disponiveis.values()}
        desconhecidos = []
        for parte in re.split(r'[,;\s]+', (texto or '').strip()):
            if not parte:
                continue
            if parte.upper() in por_codigo:
                ids.append(por_codigo[parte.upper()])
            else:
                desconhecidos.append(parte)
        if desconhecidos:
            return _cpc_texto_itens(dados, aviso="Não encontrei com Produção Mensal (produção ou subproduto): **" + ", ".join(desconhecidos) + "**.\n\n")
    ids = [i for i in dict.fromkeys(ids) if i in disponiveis]
    if not ids:
        return _cpc_texto_itens(dados, aviso="Marque pelo menos um item.\n\n")
    minimo = min(disponiveis[i]['minimo'] for i in ids)
    maximo = max(disponiveis[i]['maximo'] for i in ids)
    codigos = [disponiveis[i]['codigo'] for i in ids]
    nomes = [f"{' '.join(disponiveis[i]['descricao'].split()) or codigos[k]} / {codigos[k]}" for k, i in enumerate(ids)]
    estado.etapa_atual = 'cpc_periodo'
    estado.dados_coletados = {**dados, 'itens_ids': ids, 'itens_codigos': codigos, 'itens_nomes': nomes,
                              'ano_mes_minimo': minimo, 'ano_mes_maximo': maximo}
    estado.save()
    resumo = ', '.join(codigos) if len(codigos) <= 12 else ', '.join(codigos[:12]) + f" e mais {len(codigos) - 12}"
    return (f"Itens: **{resumo}**. A Produção Mensal deles vai de **{minimo}** a **{maximo}**. Escolha o período "
            "(ano/mês início e fim) e confirme abaixo, ou clica em \"Cancelar\".\n\n"
            + _cfx_form_periodo(minimo, maximo, estado.dados_coletados))


def _etapa_cpc_periodo(estado, texto):
    from custo_ferbasa.custo_producao_consumo import calcular_custo_producao_x_consumo, CustoProducaoConsumoError
    dados = estado.dados_coletados or {}
    minimo, maximo = dados['ano_mes_minimo'], dados['ano_mes_maximo']
    periodos = re.findall(r'\d{4}/\d{2}', texto or '')
    if (texto or '').strip().lower() in ('sim', 's'):
        periodos = [minimo, maximo]
    if len(periodos) != 2:
        return ("Não consegui entender esse período. Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                + _cfx_form_periodo(minimo, maximo, dados))
    inicio, fim = periodos
    if fim > maximo:
        return (f"Na Tabela de Produção Mensal só existem dados até **{maximo}** para esses itens -- o fim ({fim}) não "
                "pode passar disso. Ajuste abaixo, ou clica em \"Cancelar\".\n\n" + _cfx_form_periodo(max(inicio, minimo), maximo, dados))
    if inicio < minimo:
        return (f"Na Tabela de Produção Mensal só existem dados a partir de **{minimo}** para esses itens -- o início "
                f"({inicio}) não pode ser antes disso. Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                + _cfx_form_periodo(minimo, min(fim, maximo), dados))
    if fim < inicio:
        return (f"O fim ({fim}) não pode ser antes do início ({inicio}). Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                + _cfx_form_periodo(inicio, fim, dados))
    try:
        res = calcular_custo_producao_x_consumo(dados['empresa_id'], dados['itens_ids'], inicio, fim)
    except CustoProducaoConsumoError as erro:
        _encerrar_fluxo(estado)
        return f"Não consegui comparar: {erro}"
    moeda = _cmp_moeda(estado)
    _encerrar_fluxo(estado)
    nomes = ', '.join(dados.get('itens_nomes') or dados.get('itens_codigos', []))
    br = lambda v: '—' if v is None else f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    t = res['total']
    un = f"/{res['unidade']}" if res['unidade'] else '/un'
    brq = lambda v: f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    def trecho(rotulo, x):
        if not x['qtde']:
            return f"**{rotulo}**: sem quantidade no período"
        return (f"**{rotulo}** ({brq(x['qtde'])} {res['unidade'] or 'un'}): custo total **{moeda}{br(x['total_un'])}{un}** · "
                f"variável {moeda}{br(x['variavel_un'])} · fixo/outros {moeda}{br(x['fixo_un'])}")
    texto_resposta = (f"**Custo Produção x Custo Consumo — {nomes}** ({inicio} a {fim})\n\n"
                      "No período (média ponderada):\n"
                      f"- {trecho('Produção', t['producao'])}\n"
                      f"- {trecho('Geração como subproduto', t['geracao'])}\n"
                      f"- {trecho('Produção + geração (consolidado)', t['producao_total'])}\n"
                      f"- {trecho('Consumo', t['consumo'])}")
    if res['unidades_diferentes']:
        texto_resposta += ("\n\n⚠️ Os itens escolhidos têm unidades diferentes: a média por unidade soma quantidades de "
                           "unidades diferentes.")
    if not res['meses']:
        texto_resposta += "\n\nNão há produção nem consumo desses itens no período."
    payload = {**res, 'titulo': f"Custo Produção x Custo Consumo — {nomes}", 'periodo': f"{inicio} a {fim}",
               'moeda': moeda.strip()}
    return texto_resposta + "\n\n" + _equ_marcador('CUSTO_PRODUCAO_CONSUMO', payload)


# registra as etapas no despachante das ações de processar (mesmo dicionário do Montar Fluxo)
_fcc._HANDLERS_PROCESSAR.update({
    'cpc_itens': _etapa_cpc_itens,
    'cpc_periodo': _etapa_cpc_periodo,
})
