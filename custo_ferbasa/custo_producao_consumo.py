"""
🌟 NOVO (27/10/2026): "Custo Ferbasa - Custo Produção x Custo Consumo" (SÓ LEITURA: não grava nada).

Para os itens de produção escolhidos, compara, mês a mês, o custo do item QUANDO É PRODUZIDO com o custo com que ele
ENTRA NO CONSUMO de quem o consome -- os dois pelo valor contábil da Produção Mensal (as mesmas regras da procedure
custo_variavel_adicionado e da ação "Montar Fluxo de Produção Base Produção Mensal"):

  PRODUÇÃO (todos os equipamentos que produzem o item, inclusive os do grupo VAZIO, como no Fluxo Completo Consolidado):
    quantidade = qtde produzida, uma vez por (ano/mês, estabelecimento, grupo, ordem, item);
    total      = soma de (valor material + valor GGF) x qtde consumida das linhas com produção
                 + GGF distribuído (Distribuição GGF Mensal) das ordens com produção;
    variável   = valor material x qtde + parte variável do GGF do item consumido (% do item de produção de mesmo código)
                 + parte variável do GGF distribuído (tipo V/F/O da Conta x CC).
  CONSUMO (as linhas em que o item aparece como ITEM DE CONSUMO, com consumo positivo, em linhas com produção):
    quantidade = qtde consumida;
    total      = (valor material + valor GGF) x qtde consumida;
    variável   = valor material x qtde + valor GGF x qtde x % variável do GGF do item.
  fixo/outros = total - variável (nos dois lados).

  🌟 GERAÇÃO COMO SUBPRODUTO (28/10/2026): as linhas em que o item aparece como ITEM DE CONSUMO com consumo NEGATIVO
  (gerado por um equipamento), em linhas com produção -- o crédito que o equipamento gerador recebeu:
    quantidade = qtde gerada (sinal trocado: positiva);
    total      = (valor material + valor GGF) x qtde gerada;
    variável   = valor material x qtde + valor GGF x qtde x % variável do GGF do item.
  PRODUÇÃO + GERAÇÃO = soma das duas (consolidado). O consumo não muda.

Vários itens escolhidos: os valores são somados (a média por unidade fica ponderada pelas quantidades). O período
inteiro também é a soma dos meses (média ponderada).
"""
from collections import defaultdict

from .fluxo_producao_mensal import (_num, _parte_variavel_ggf, _custos_mensais_ops, producao_mensal_da_empresa,
                                    _da_empresa, itens_com_producao)


class CustoProducaoConsumoError(RuntimeError):
    pass


def _cod(c):
    return str(c or '').strip().upper()


def itens_producao_ou_subproduto(empresa_id):
    """🌟 NOVO (28/10/2026): a lista inicial da ação -- itens com Produção Mensal (produzidos) + itens gerados como
    subproduto (consumo negativo), juntos pelo código. [{'id', 'codigo', 'descricao', 'unidade', 'minimo', 'maximo',
    'produzido', 'subproduto'}]. id = o do item de produção; se o código só existe como subproduto, -id do item de consumo
    (a tela de seleção trabalha com números)."""
    from .fluxo_producao_mensal import subprodutos_com_geracao
    por_codigo = {}
    for i, minimo, maximo in itens_com_producao(empresa_id):
        c = _cod(i.ite_pro_codigo)
        r = por_codigo.get(c)
        if r is None:
            por_codigo[c] = {'id': i.id, 'codigo': str(i.ite_pro_codigo).strip(), 'descricao': i.ite_pro_descricao or '',
                             'unidade': i.ite_pro_unidade or '', 'minimo': minimo, 'maximo': maximo,
                             'produzido': True, 'subproduto': False}
        else:
            r['minimo'], r['maximo'] = min(r['minimo'], minimo), max(r['maximo'], maximo)
    for s in subprodutos_com_geracao(empresa_id):
        c = _cod(s['codigo'])
        r = por_codigo.get(c)
        if r is None:
            por_codigo[c] = {'id': -s['id'], 'codigo': str(s['codigo']).strip(), 'descricao': s['descricao'] or '',
                             'unidade': s['unidade'] or '', 'minimo': s['minimo'], 'maximo': s['maximo'],
                             'produzido': False, 'subproduto': True}
        else:
            r['subproduto'] = True
            r['minimo'], r['maximo'] = min(r['minimo'], s['minimo']), max(r['maximo'], s['maximo'])
    return sorted(por_codigo.values(), key=lambda r: (' '.join(r['descricao'].split()).lower(), r['codigo']))


def calcular_custo_producao_x_consumo(empresa_id, ids_itens, ano_mes_inicio, ano_mes_fim):
    """ids_itens: ids da lista itens_producao_ou_subproduto (positivo = item de produção; negativo = -id do item de
    consumo, para o item que só existe como subproduto)."""
    from .models import TbItensProducao, TbItensConsumo, TbDistribuicaoGGFMensal, TbGruposMaquinas

    ids_prod = [i for i in ids_itens if i > 0]
    ids_sub = [-i for i in ids_itens if i < 0]
    escolhidos = list(TbItensProducao.objects.filter(id__in=ids_prod).values('id', 'ite_pro_codigo', 'ite_pro_descricao', 'ite_pro_unidade'))
    escolhidos += [{'id': -i['id'], 'ite_pro_codigo': i['ite_con_codigo'], 'ite_pro_descricao': i['ite_con_descricao'],
                    'ite_pro_unidade': i['ite_con_unidade']}
                   for i in TbItensConsumo.objects.filter(id__in=ids_sub).values('id', 'ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade')]
    if not escolhidos:
        raise CustoProducaoConsumoError("Nenhum dos itens escolhidos existe mais.")
    codigos = {_cod(i['ite_pro_codigo']) for i in escolhidos}

    # produtores: todos os itens de produção da empresa com o mesmo código (cadastros repetidos contam juntos)
    ids_produtores = [i.id for i, _a, _b in itens_com_producao(empresa_id) if _cod(i.ite_pro_codigo) in codigos]
    ids_consumo = [i['id'] for i in _da_empresa(TbItensConsumo, empresa_id).values('id', 'ite_con_codigo')
                   if _cod(i['ite_con_codigo']) in codigos]

    campos = ('pro_men_ano_mes', 'pro_men_estabelecimento_id', 'pro_men_grupo_maquina_id', 'pro_men_ordem_producao',
              'pro_men_item_producao_id', 'pro_men_qtde_produzida', 'pro_men_item_consumo_id', 'pro_men_qtde_consumo',
              'pro_men_valor_material', 'pro_men_valor_ggf')
    base = producao_mensal_da_empresa(empresa_id).filter(pro_men_ano_mes__gte=ano_mes_inicio, pro_men_ano_mes__lte=ano_mes_fim)
    linhas_prod = list(base.filter(pro_men_item_producao_id__in=ids_produtores).order_by().values(*campos))
    linhas_cons = list(base.filter(pro_men_item_consumo_id__in=ids_consumo, pro_men_qtde_consumo__gt=0,
                                   pro_men_qtde_produzida__gt=0).order_by().values(*campos))
    # 🌟 NOVO (28/10/2026): o item GERADO como subproduto (consumo negativo), nas linhas com produção (as que têm valor)
    linhas_ger = list(base.filter(pro_men_item_consumo_id__in=ids_consumo, pro_men_qtde_consumo__lt=0,
                                  pro_men_qtde_produzida__gt=0).order_by().values(*campos))

    # 🌟 NOVO: TODAS as linhas dos equipamentos consumidores (equipamento x item que ele produz) -- para a produção deles
    # (consumo específico) e para tudo o que eles consumiram no mês (o link do equipamento consumidor)
    chaves_cons = {(l['pro_men_grupo_maquina_id'], l['pro_men_item_producao_id']) for l in linhas_cons + linhas_ger}
    linhas_consumidores = []
    if chaves_cons:
        linhas_consumidores = [l for l in base.filter(pro_men_item_producao_id__in={i for _g, i in chaves_cons},
                                                      pro_men_grupo_maquina_id__in={g for g, _i in chaves_cons},
                                                      pro_men_qtde_produzida__gt=0).order_by().values(*campos)
                               if (l['pro_men_grupo_maquina_id'], l['pro_men_item_producao_id']) in chaves_cons]

    ids_itens_consumo = {l['pro_men_item_consumo_id'] for l in linhas_prod + linhas_cons + linhas_ger + linhas_consumidores}
    itens_consumo = {i['id']: {'codigo': str(i['ite_con_codigo'] or '').strip(),
                               'descricao': ' '.join(str(i['ite_con_descricao'] or '').split()),   # sem quebras de linha
                               'unidade': i['ite_con_unidade'] or ''}
                     for i in TbItensConsumo.objects.filter(id__in=ids_itens_consumo - {None})
                     .values('id', 'ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade')}
    itens_consumo.setdefault(None, {'codigo': '', 'descricao': '', 'unidade': ''})        # linha sem item de consumo
    # % variável do GGF por código (cadastro do item de produção de mesmo código) -- como no Montar Fluxo
    codigos_consumidos = {v['codigo'] for v in itens_consumo.values()}
    pct_var_ggf = {}
    for codigo, pct in _da_empresa(TbItensProducao, empresa_id).values_list('ite_pro_codigo', 'ite_pro_percentual_variavel_ggf'):
        c = str(codigo or '').strip()
        if c in codigos_consumidos and c not in pct_var_ggf:
            pct_var_ggf[c] = _num(pct)

    ggf_linhas = [
        {'ano_mes': g['dis_ggf_men_ano_mes'], 'ordem': g['dis_ggf_men_ordem_producao'], 'item_id': g['dis_ggf_men_item_producao_id'],
         'grupo_id': g['dis_ggf_men_grupo_maquina_id'], 'valor': g['dis_ggf_men_valor'],
         'tipo': g['dis_ggf_men_conta_cc__con_con_cen_cus_tipo'], 'percentual': g['dis_ggf_men_conta_cc__con_con_cen_cus_percentual']}
        for g in (_da_empresa(TbDistribuicaoGGFMensal, empresa_id)
                  .filter(dis_ggf_men_ano_mes__gte=ano_mes_inicio, dis_ggf_men_ano_mes__lte=ano_mes_fim,
                          dis_ggf_men_item_producao_id__in=ids_produtores)
                  .order_by()
                  .values('dis_ggf_men_ano_mes', 'dis_ggf_men_ordem_producao', 'dis_ggf_men_item_producao_id',
                          'dis_ggf_men_grupo_maquina_id', 'dis_ggf_men_valor', 'dis_ggf_men_conta_cc__con_con_cen_cus_tipo',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_percentual'))
    ]

    # ---- PRODUÇÃO: a mesma conta do custo mensal de cada equipamento do Montar Fluxo
    prod = defaultdict(lambda: [0.0, 0.0, 0.0])        # mes -> [qtde, total, variável]
    prod_eq = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))     # 🌟 mes -> grupo -> [qtde, total, variável]
    for (mes, op), (q, t, v) in _custos_mensais_ops(linhas_prod, ggf_linhas, itens_consumo, pct_var_ggf).items():
        for r in (prod[mes], prod_eq[mes][op[1]]):
            r[0] += q
            r[1] += t
            r[2] += v

    # 🌟 NOVO: o que justifica o custo de produção de cada equipamento produtor -- as MESMAS parcelas de
    # _custos_mensais_ops: itens consumidos nas linhas com produção (mes, grupo) -> item -> [q, t, v] e o GGF
    # distribuído das ordens com produção (mes, grupo) -> conta/tipo -> [t, v]
    prod_consumos = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    prod_ggf = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    ordens_com_producao = defaultdict(set)
    for l in linhas_prod:
        if _num(l['pro_men_qtde_produzida']) <= 0:
            continue
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        ordens_com_producao[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))
        q, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l['pro_men_valor_material']), _num(l['pro_men_valor_ggf'])
        if q == 0:
            continue
        ic = itens_consumo.get(l['pro_men_item_consumo_id'], {'codigo': '', 'descricao': '', 'unidade': ''})
        pct = pct_var_ggf.get(ic['codigo'], 0.0) if ggf > 0 else 0.0
        r = prod_consumos[(l['pro_men_ano_mes'], l['pro_men_grupo_maquina_id'])][(ic['codigo'], ic['descricao'], ic['unidade'], q < 0)]
        r[0] += q
        r[1] += (mat + ggf) * q
        r[2] += mat * q + ggf * q * (pct if pct > 0 else 0.0) / 100.0
    for g in ggf_linhas:
        op = (g['item_id'], g['grupo_id'])
        if (g['ano_mes'], g['ordem']) not in ordens_com_producao.get(op, ()):
            continue
        valor = _num(g['valor'])
        rotulo = {'V': 'Variável', 'F': 'Fixo', 'O': 'Outros'}.get(g.get('tipo') or '', 'Sem tipo')   # % na tela
        r = prod_ggf[(g['ano_mes'], g['grupo_id'])][rotulo]
        r[0] += valor
        r[1] += _parte_variavel_ggf(valor, g.get('tipo'), g.get('percentual'))

    # ---- CONSUMO: valor contábil das linhas em que o item foi consumido
    cons = defaultdict(lambda: [0.0, 0.0, 0.0])
    cons_eq = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))     # 🌟 mes -> (grupo, item produzido) -> [...]
    for l in linhas_cons:
        q, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l['pro_men_valor_material']), _num(l['pro_men_valor_ggf'])
        pct = pct_var_ggf.get(itens_consumo.get(l['pro_men_item_consumo_id'], {}).get('codigo'), 0.0) if ggf > 0 else 0.0
        t, v = (mat + ggf) * q, mat * q + ggf * q * (pct if pct > 0 else 0.0) / 100.0
        for r in (cons[l['pro_men_ano_mes']], cons_eq[l['pro_men_ano_mes']][(l['pro_men_grupo_maquina_id'], l['pro_men_item_producao_id'])]):
            r[0] += q
            r[1] += t
            r[2] += v

    # ---- 🌟 GERAÇÃO COMO SUBPRODUTO: crédito dado a quem gerou (qtde e valores com o sinal trocado: positivos)
    ger = defaultdict(lambda: [0.0, 0.0, 0.0])
    ger_eq = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))      # mes -> (grupo, item produzido) -> [...]
    for l in linhas_ger:
        q, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l['pro_men_valor_material']), _num(l['pro_men_valor_ggf'])
        pct = pct_var_ggf.get(itens_consumo.get(l['pro_men_item_consumo_id'], {}).get('codigo'), 0.0) if ggf > 0 else 0.0
        t, v = (mat + ggf) * q, mat * q + ggf * q * (pct if pct > 0 else 0.0) / 100.0
        for r in (ger[l['pro_men_ano_mes']], ger_eq[l['pro_men_ano_mes']][(l['pro_men_grupo_maquina_id'], l['pro_men_item_producao_id'])]):
            r[0] += -q
            r[1] += -t
            r[2] += -v

    # 🌟 NOVO: detalhe por EQUIPAMENTO de cada mês (quem produziu / quem consumiu), em listas compactas:
    #   produção: [código do equipamento, nome, qtde, total, variável]
    #   consumo:  [código do equipamento, nome, código do item que ele produz, descrição, qtde, total, variável]
    ids_grupo = ({g for d in prod_eq.values() for g in d} | {g for d in cons_eq.values() for g, _i in d}
                 | {g for d in ger_eq.values() for g, _i in d})
    grupos = {g['id']: (str(g['gru_maq_codigo'] or '').strip(), g['gru_maq_nome'] or '')
              for g in TbGruposMaquinas.objects.filter(id__in=ids_grupo).values('id', 'gru_maq_codigo', 'gru_maq_nome')}
    ids_item = {i for d in list(cons_eq.values()) + list(ger_eq.values()) for _g, i in d}
    # 🌟 NOVO: PRODUÇÃO de cada equipamento consumidor (todas as ordens do mês, uma vez por ordem) -- para o consumo
    # específico do item (qtde consumida / qtde produzida pelo equipamento)
    prod_consumidor = defaultdict(float)        # (mes, grupo, item produzido) -> qtde produzida
    # 🌟 NOVO: tudo o que cada equipamento consumidor consumiu: (mes, grupo, item produzido) -> código consumido -> [q, t, v]
    consumo_consumidor = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    vistos = set()
    for l in linhas_consumidores:
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        k3 = (l['pro_men_ano_mes'], l['pro_men_grupo_maquina_id'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            prod_consumidor[k3] += _num(l['pro_men_qtde_produzida'])
        q, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l['pro_men_valor_material']), _num(l['pro_men_valor_ggf'])
        if q == 0 or l['pro_men_item_consumo_id'] is None:
            continue
        ic = itens_consumo.get(l['pro_men_item_consumo_id'], {'codigo': '', 'descricao': '', 'unidade': ''})
        pct = pct_var_ggf.get(ic['codigo'], 0.0) if ggf > 0 else 0.0
        r = consumo_consumidor[k3][(ic['codigo'], ic['descricao'], ic['unidade'], q < 0)]
        r[0] += q
        r[1] += (mat + ggf) * q
        r[2] += mat * q + ggf * q * (pct if pct > 0 else 0.0) / 100.0
    itens_prod = {i['id']: (str(i['ite_pro_codigo'] or '').strip(), i['ite_pro_descricao'] or '')
                  for i in TbItensProducao.objects.filter(id__in=ids_item).values('id', 'ite_pro_codigo', 'ite_pro_descricao')}

    def lista_prod(por_grupo, meses_da_linha):
        # [equipamento, nome, qtde, total, variável,
        #  itens consumidos: [[código, descrição, unidade, gerado?, qtde, total, variável], ...],
        #  GGF distribuído: [[tipo da conta (V/F/O e %), total, variável], ...]]
        juntos = defaultdict(lambda: [0.0, 0.0, 0.0])        # o mesmo código de equipamento cadastrado 2x = 1 linha
        itens = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
        ggfs = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for g, r in por_grupo.items():
            k = grupos.get(g, (str(g), ''))
            for n in range(3):
                juntos[k][n] += r[n]
            for m in meses_da_linha:
                for ci, rr in prod_consumos.get((m, g), {}).items():
                    for n in range(3):
                        itens[k][ci][n] += rr[n]
                for rot, rr in prod_ggf.get((m, g), {}).items():
                    for n in range(2):
                        ggfs[k][rot][n] += rr[n]
        saida = []
        for k, r in juntos.items():
            li = sorted(([ci[0], ci[1], ci[2], ci[3]] + [round(x, 6) for x in rr] for ci, rr in itens[k].items()), key=lambda x: (x[3], -abs(x[5])))
            lg = sorted(([rot] + [round(x, 6) for x in rr] for rot, rr in ggfs[k].items()), key=lambda x: -abs(x[1]))
            saida.append([k[0], k[1]] + [round(x, 6) for x in r] + [li, lg])
        return sorted(saida, key=lambda x: -x[2])

    def lista_cons(por_chave, meses_da_linha):
        # [equipamento, nome, item produzido, descrição, qtde consumida, total, variável, PRODUÇÃO do equipamento,
        #  TUDO o que o equipamento consumiu: [[código, descrição, unidade, gerado?, qtde, total, variável], ...]]
        juntos = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        consumos = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
        for (g, i), r in por_chave.items():
            k = grupos.get(g, (str(g), '')) + itens_prod.get(i, (str(i), ''))
            for n in range(3):
                juntos[k][n] += r[n]
            for m in meses_da_linha:
                juntos[k][3] += prod_consumidor.get((m, g, i), 0.0)
                for ci, rr in consumo_consumidor.get((m, g, i), {}).items():
                    for n in range(3):
                        consumos[k][ci][n] += rr[n]
        saida = []
        for k, r in juntos.items():
            itens = sorted(([ci[0], ci[1], ci[2], ci[3]] + [round(x, 6) for x in rr] for ci, rr in consumos[k].items()),
                           key=lambda x: (x[3], -abs(x[5])))
            saida.append(list(k) + [round(x, 6) for x in r] + [itens])
        return sorted(saida, key=lambda x: -x[4])

    def somar(dicts):
        tot = defaultdict(lambda: [0.0, 0.0, 0.0])
        for d in dicts:
            for k, r in d.items():
                for n in range(3):
                    tot[k][n] += r[n]
        return tot

    meses = sorted(set(prod) | set(cons) | set(ger))
    unidades = sorted({(i['ite_pro_unidade'] or '').strip() for i in escolhidos} - {''})

    def lado(r):
        q, t, v = r
        f = t - v
        un = lambda x: (x / q) if q else None
        return {'qtde': q, 'total': t, 'variavel': v, 'fixo': f,
                'total_un': un(t), 'variavel_un': un(v), 'fixo_un': un(f)}

    linhas = []
    tot_p, tot_c, tot_g = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    for m in meses:
        p, c, g = prod.get(m, [0.0, 0.0, 0.0]), cons.get(m, [0.0, 0.0, 0.0]), ger.get(m, [0.0, 0.0, 0.0])
        for k in range(3):
            tot_p[k] += p[k]
            tot_c[k] += c[k]
            tot_g[k] += g[k]
        linhas.append({'mes': m, 'producao': lado(p), 'geracao': lado(g), 'producao_total': lado([p[k] + g[k] for k in range(3)]),
                       'consumo': lado(c),
                       'equip': {'producao': lista_prod(prod_eq.get(m, {}), [m]), 'geracao': lista_cons(ger_eq.get(m, {}), [m]),
                                 'consumo': lista_cons(cons_eq.get(m, {}), [m])}})
    return {
        'itens': [{'codigo': str(i['ite_pro_codigo']).strip(), 'descricao': i['ite_pro_descricao'] or '', 'unidade': i['ite_pro_unidade'] or ''}
                  for i in escolhidos],
        'unidade': unidades[0] if len(unidades) == 1 else '',
        'unidades_diferentes': len(unidades) > 1,
        'periodo': {'inicio': ano_mes_inicio, 'fim': ano_mes_fim},
        'meses': linhas,
        'total': {'mes': 'Período', 'producao': lado(tot_p), 'geracao': lado(tot_g),
                  'producao_total': lado([tot_p[k] + tot_g[k] for k in range(3)]), 'consumo': lado(tot_c),
                  'equip': {'producao': lista_prod(somar(prod_eq.values()), meses), 'geracao': lista_cons(somar(ger_eq.values()), meses),
                            'consumo': lista_cons(somar(cons_eq.values()), meses)}},
        'com_geracao': True,
    }
