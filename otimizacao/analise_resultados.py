"""
🌟 NOVO (18/10/2026): "Cenário - Análise de Resultados".

Análise GLOBAL (todos os períodos otimizados do cenário, do início ao fim) dos resultados que as etapas Limpar /
Otimizar / Consolidar gravam nas tabelas da app otimizacao, sempre em relação à MARGEM DE CONTRIBUIÇÃO TOTAL (MCT):

  - resumo do cenário: receita, custos variáveis, MCT, custo fixo, EBITDA (parameters_tbcenariosdaugther, gravado pela
    procedure consolida_resultados);
  - produtos e produtos/mercados mais rentáveis: margem total, margem por unidade e margem por hora (CMH) -- tabelas
    TbOtimizacaoProdutoDaugther e TbProdutoMercadoDaugther (média do período ponderada pelas vendas);
  - fluxos de produção (variáveis do Simplex: produto / mercado / fluxo) -- TbProdutoMercadoFluxoDaugther;
  - equipamentos: horas disponíveis (loading time) x horas usadas, ocupação, margem dos produtos que passam por ele e o
    PREÇO SOMBRA da hora (quanto a MCT aumentaria com 1 hora a mais) -- TbOtimizacaoEquipamentosDaugther + Shadow 'E';
  - limites: cada restrição do modelo (E equipamento, CE conjunto de equipamentos, P produto, PM produto/mercado, IC item
    de custo/insumo) com o preço sombra do máximo e do mínimo -- TbOtimizacaoShadow (gravada em
    parameters.tasks.extrair_shadow_prices).

PREÇO SOMBRA (como é gravado em extrair_shadow_prices; o linprog minimiza -MCT, por isso o sinal já vem trocado):
  dau_valor_2 (Sombra Máx) = quanto a MCT do período AUMENTA com 1 unidade a mais no limite MÁXIMO (> 0: o máximo está
                             segurando o resultado);
  dau_valor_1 (Sombra Mín) = quanto a MCT do período AUMENTA com 1 unidade a MENOS no limite MÍNIMO (> 0: a exigência
                             do mínimo está custando margem);
  dau_valor_10/11 = faixa do limite MÁXIMO em que o preço sombra continua valendo; dau_valor_8/9 = idem para o MÍNIMO.
  Ganho potencial (dentro da faixa, por período) = sombra x (até onde o limite pode ir sem o preço sombra mudar). Somado
  nos períodos. É uma estimativa marginal (o Simplex de cada período é independente): vale como ORDEM DE GRANDEZA e
  para priorizar o que analisar, não como valor garantido -- mudar um limite muda o mix e os outros preços sombra.
Unidade do limite: E = horas (loading time); CE / P / PM = volume do produto; IC = quantidade do item.
"""
from collections import defaultdict

INFINITO = 1e20          # o HiGHS devolve ±1e30 para "sem limite" na faixa (ranging)
MAX_LINHAS = 300         # por lista (o resto vai somado / contado)
LIMITE_SEM_MAXIMO = 9_999_999   # máximo de consumo a partir disso = "sem limite" (ex.: 99.999.999 no cadastro)


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _finito(v):
    return v is not None and abs(float(v)) < INFINITO


def _fetch(sql, params):
    from django.db import connection
    with connection.cursor() as c:
        c.execute(sql, params)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def _junta_distintos(a, b):
    a, b = (a or '').strip(), (b or '').strip()
    return a if not b or b == a else (b if not a else f"{a} - {b}")


def _info_variaveis(cid, listas):
    """🌟 23/10/2026: para cada texto de 'concorrentes' (a próxima variável / restrição que passaria a limitar, gravada pelo
    Otimizar como 'Var: Var #N' -- N = posição da variável no modelo, na ordem de pro_mer_flu_variavel --, 'Var: produto /
    mercado / fluxo' ou 'Rest: nome (lado)'), a descrição da variável (produto, mercado e fluxo de produção) para a dica
    ao passar o mouse. Devolve (por_texto, por_numero)."""
    import re
    textos = set()
    for ls in listas:
        for r in ls or []:
            for c in r.get('concorrentes') or []:
                textos.add(c)
    por_texto, por_numero = {}, {}
    if not textos:
        return por_texto, por_numero
    try:
        from otimizacao.models import TbProdutoMercadoFluxo
        qs = list(TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cid, flag=True)
                  .select_related('pro_mer_flu_produto', 'pro_mer_flu_mercado', 'pro_mer_flu_fluxo_producao')
                  .order_by('pro_mer_flu_variavel'))
    except Exception:
        qs = []

    def descricao(v):
        p, m, f = v.pro_mer_flu_produto, v.pro_mer_flu_mercado, v.pro_mer_flu_fluxo_producao
        return {'tipo': 'variavel',
                'produto': _junta_distintos(getattr(p, 'pro_codigo', '') or str(p), getattr(p, 'pro_descricao', '') or ''),
                'mercado': getattr(m, 'mer_nome', None) or str(m),
                'fluxo': _junta_distintos(str(f), getattr(f, 'flu_pro_descricao', '') or '')}

    por_nome = {f"Var: {v.pro_mer_flu_produto} / {v.pro_mer_flu_mercado} / {v.pro_mer_flu_fluxo_producao}": v for v in qs}
    for t in textos:
        if t.startswith('Var'):
            m = re.match(r'^Var:?\s*(?:Var\s*)?#\s*(\d+)\s*$', t)
            if m:
                k = int(m.group(1))
                info = descricao(qs[k]) if 0 <= k < len(qs) else {'tipo': 'variavel'}
                info['numero'] = k
                por_texto[t] = info
                por_numero[str(k)] = info
            elif t in por_nome:
                por_texto[t] = descricao(por_nome[t])
        elif t.startswith('Rest:'):
            corpo = t[5:].strip()
            lado = 'mínimo' if corpo.endswith('(minimo)') else ('máximo' if corpo.endswith('(maximo)') else '')
            nome = corpo.rsplit(' (', 1)[0] if lado else corpo
            por_texto[t] = {'tipo': 'restricao', 'restricao': nome, 'lado': lado}
    return por_texto, por_numero


def montar_analise(cenario, rotulo_periodo, simular_minimos=False):
    """cenario: TbCenarios. rotulo_periodo(dau_order) -> 'AAAA/MM'. Devolve o dicionário da análise (vai para o chat)."""
    cid = cenario.id

    # ---------------- períodos e resumo (consolida_resultados) ----------------
    per = _fetch("""select dau_order, flag, otimizar, dau_valor_1, dau_valor_2, dau_valor_3, dau_valor_4, dau_valor_5,
                           dau_valor_6, dau_valor_7, dau_valor_8, dau_valor_10, dau_valor_19, dau_valor_20
                    from parameters_tbcenariosdaugther where mae_id = %s order by dau_order""", [cid])
    otimizados = [p['dau_order'] for p in per if p['flag'] in (1, True) and p['otimizar'] in (1, True)]
    nao_otimizados = [rotulo_periodo(p['dau_order']) for p in per if p['otimizar'] in (1, True) and p['flag'] not in (1, True)]
    ords = set(otimizados)
    resumo = {k: sum(_f(p[c]) for p in per if p['dau_order'] in ords) for k, c in (
        ('receita', 'dau_valor_1'), ('itens', 'dau_valor_2'), ('inbound', 'dau_valor_3'), ('outbound', 'dau_valor_4'),
        ('manutencao', 'dau_valor_5'), ('mct', 'dau_valor_6'), ('custo_fixo', 'dau_valor_7'), ('ebitda', 'dau_valor_8'),
        ('da', 'dau_valor_10'), ('capex', 'dau_valor_19'), ('ofcf', 'dau_valor_20'))}
    resumo['custo_variavel'] = resumo['itens'] + resumo['inbound'] + resumo['outbound'] + resumo['manutencao']
    resumo['periodos'] = len(otimizados)
    resumo['inicio'] = rotulo_periodo(min(otimizados)) if otimizados else ''
    resumo['fim'] = rotulo_periodo(max(otimizados)) if otimizados else ''
    resumo['nao_otimizados'] = nao_otimizados
    resumo['mct_mensal'] = [[rotulo_periodo(p['dau_order']), _f(p['dau_valor_6']), _f(p['dau_valor_1'])] for p in per if p['dau_order'] in ords]
    if not otimizados:
        return {'tipo_fluxo': 'analise_resultados', 'resumo': resumo, 'vazio': True}

    lista_ord = sorted(ords)

    # ---------------- produtos e produtos/mercados ----------------
    def ranking_vendas(sql_mae, sql_filha, nome_de):
        maes = {r['id']: r for r in _fetch(sql_mae, [cid])}
        acc = defaultdict(lambda: {'vendas': 0.0, 'receita': 0.0, 'cv': 0.0, 'cmh_x_v': 0.0, 'v_cmh': 0.0,
                                   'no_max': 0, 'no_min': 0, 'periodos': 0, 'min_def': 0.0, 'p_min_def': 0})
        for f in _fetch(sql_filha, [cid, lista_ord]):
            a = acc[f['mae_id']]
            v = _f(f['vendas'])
            a['vendas'] += v
            a['receita'] += v * _f(f['preco'])
            a['cv'] += v * _f(f['cv'])
            if v > 0 and f['cmh'] is not None:
                a['cmh_x_v'] += _f(f['cmh']) * v
                a['v_cmh'] += v
            mx, mn = f['vmax'], f['vmin']
            if v > 0 or _f(mx) > 0:
                a['periodos'] += 1
            if mx is not None and _f(mx) > 0 and v >= _f(mx) - 0.5:
                a['no_max'] += 1
            if mn is not None and _f(mn) > 0 and v <= _f(mn) + 0.5:
                a['no_min'] += 1
            if mn is not None and _f(mn) > 0:       # 🌟 23/10/2026: mínimo DEFINIDO (pode estar acima dele, sem prender)
                a['min_def'] += _f(mn)
                a['p_min_def'] += 1
        saida = []
        for mid, a in acc.items():
            if mid not in maes:
                continue
            margem = a['receita'] - a['cv']
            saida.append({'id': mid, 'nome': nome_de(maes[mid]), 'unidade': maes[mid].get('unidade') or '',
                          'vendas': a['vendas'], 'receita': a['receita'], 'margem': margem,
                          'margem_un': margem / a['vendas'] if a['vendas'] > 0 else None,
                          'preco': a['receita'] / a['vendas'] if a['vendas'] > 0 else None,
                          'cv_un': a['cv'] / a['vendas'] if a['vendas'] > 0 else None,
                          'cmh': a['cmh_x_v'] / a['v_cmh'] if a['v_cmh'] > 0 else None,
                          'no_max': a['no_max'], 'no_min': a['no_min'], 'periodos': a['periodos'],
                          'minimo_definido': a['min_def'], 'periodos_com_minimo': a['p_min_def']})
        saida.sort(key=lambda r: -r['margem'])
        return saida

    produtos = ranking_vendas(
        """select o.id, p.pro_codigo as codigo, p.pro_unidade_producao as unidade
           from otimizacao_tbotimizacaoproduto o join produtos_tbprodutos p on p.id = o.oti_pro_produto_id
           where o.tbcenarios_id = %s and o.flag = true""",
        """select mae_id, dau_valor_3 as vendas, dau_valor_5 as preco, dau_valor_6 as cv, dau_valor_7 as cmh,
                  dau_valor_1 as vmin, dau_valor_2 as vmax
           from otimizacao_tbotimizacaoprodutodaugther where tbcenarios_id = %s and dau_order = any(%s)""",
        lambda m: str(m['codigo']))
    produtos_mercados = ranking_vendas(
        """select o.id, p.pro_codigo as codigo, p.pro_unidade_producao as unidade,
                  m.mer_nome as mercado
           from otimizacao_tbprodutomercado o join produtos_tbprodutos p on p.id = o.pro_mer_produto_id
                join tabelas_tbmercado m on m.id = o.pro_mer_mercado_id
           where o.tbcenarios_id = %s and o.flag = true""",
        """select mae_id, dau_valor_3 as vendas, dau_valor_5 as preco, dau_valor_6 as cv, dau_valor_7 as cmh,
                  dau_valor_1 as vmin, dau_valor_2 as vmax
           from otimizacao_tbprodutomercadodaugther where tbcenarios_id = %s and dau_order = any(%s)""",
        lambda m: f"{m['codigo']} / {m['mercado']}")

    # ---------------- fluxos (variáveis do Simplex) ----------------
    maes_pmf = {r['id']: r for r in _fetch(
        """select o.id, o.pro_mer_flu_fluxo_producao_id as fluxo_id, p.pro_codigo as codigo, m.mer_nome as mercado,
                  f.flu_pro_descricao as fluxo, p.pro_unidade_producao as unidade
           from otimizacao_tbprodutomercadofluxo o join produtos_tbprodutos p on p.id = o.pro_mer_flu_produto_id
                join tabelas_tbmercado m on m.id = o.pro_mer_flu_mercado_id
                join fluxos_tbfluxoproducao f on f.id = o.pro_mer_flu_fluxo_producao_id
           where o.tbcenarios_id = %s and o.flag = true""", [cid])}
    acc_f = defaultdict(lambda: {'vendas': 0.0, 'margem': 0.0, 'mh_x_v': 0.0, 'v_mh': 0.0, 'periodos_disp': 0})
    margem_por_fluxo = defaultdict(float)
    for f in _fetch("""select mae_id, dau_order, dau_valor_2 as vendas, dau_valor_3 as margem, dau_valor_7 as equip,
                              dau_valor_11 as mh
                       from otimizacao_tbprodutomercadofluxodaugther where tbcenarios_id = %s and dau_order = any(%s)""",
                    [cid, lista_ord]):
        a = acc_f[f['mae_id']]
        v, mg = _f(f['vendas']), _f(f['margem'])
        a['vendas'] += v
        a['margem'] += v * mg
        if f['equip']:
            a['periodos_disp'] += 1
        if v > 0 and f['mh'] is not None:
            a['mh_x_v'] += _f(f['mh']) * v
            a['v_mh'] += v
        if f['mae_id'] in maes_pmf:
            margem_por_fluxo[maes_pmf[f['mae_id']]['fluxo_id']] += v * mg
    fluxos = []
    fluxos_sem_producao = 0
    for mid, a in acc_f.items():
        if mid not in maes_pmf:
            continue
        if a['vendas'] <= 0.5:          # 🌟 19/10/2026: só os fluxos que tiveram produção no período
            fluxos_sem_producao += 1
            continue
        m = maes_pmf[mid]
        fluxos.append({'nome': f"{m['codigo']} / {m['mercado']} / {m['fluxo']}", 'unidade': m['unidade'] or '',
                       'vendas': a['vendas'], 'margem': a['margem'],
                       'margem_un': a['margem'] / a['vendas'] if a['vendas'] > 0 else None,
                       'cmh': a['mh_x_v'] / a['v_mh'] if a['v_mh'] > 0 else None,
                       'periodos_disp': a['periodos_disp']})
    fluxos.sort(key=lambda r: -r['margem'])
    vendidos_negativos = [r for r in fluxos if r['vendas'] > 0 and r['margem'] < 0]

    # ---------------- equipamentos ----------------
    # fluxo -> equipamentos (cadastro) que ele usa (mesma ligação da consolida_resultados)
    eq_fluxo = defaultdict(set)
    for r in _fetch("""select io.mae_id as fluxo_id, e.equ_codigo_id as equip_id
                       from fluxos_tbfluxoproducaoinputoutput io
                            join equipamentos_tbequipamentos e on e.id = io.flu_pro_inp_out_equipamento_id
                       where io.tbcenarios_id = %s""", [cid]):
        eq_fluxo[r['equip_id']].add(r['fluxo_id'])
    maes_eq = {r['id']: r for r in _fetch("""select id, oti_equ_equipamento_id as equip_id from otimizacao_tbotimizacaoequipamentos
                                              where tbcenarios_id = %s""", [cid])}
    acc_e = defaultdict(lambda: {'loading': 0.0, 'horas': 0.0, 'volume': 0.0, 'periodos_100': 0, 'periodos': 0})
    for f in _fetch("""select mae_id, dau_valor_1 as running, dau_valor_2 as volume, dau_valor_3 as loading,
                              dau_valor_4 as ocupacao, dau_valor_6 as horas
                       from otimizacao_tbotimizacaoequipamentosdaugther where tbcenarios_id = %s and dau_order = any(%s)""",
                    [cid, lista_ord]):
        a = acc_e[f['mae_id']]
        a['loading'] += _f(f['loading'])
        a['horas'] += _f(f['horas'])
        a['volume'] += _f(f['volume'])
        if _f(f['loading']) > 0:
            a['periodos'] += 1
        if _f(f['ocupacao']) >= 99.5:
            a['periodos_100'] += 1

    # ---------------- preços sombra (todas as restrições) ----------------
    sombras = _fetch("""select oti_shadow_restricao as nome, oti_shadow_tipo as tipo, oti_shadow_id_1 as id1, dau_order,
                               dau_valor_1 as s_min, dau_valor_2 as s_max, dau_valor_5 as vmin, dau_valor_6 as vmax,
                               dau_valor_7 as valor_real, dau_valor_8 as min_de, dau_valor_9 as min_ate,
                               dau_valor_10 as max_de, dau_valor_11 as max_ate, dau_texto_1 as gargalo,
                               dau_texto_2 as conc_reduzir, dau_texto_3 as conc_aumentar
                        from otimizacao_tbotimizacaoshadow where tbcenarios_id = %s and flag = true and dau_order = any(%s)""",
                     [cid, lista_ord])
    restr = {}
    # 🌟 20/10/2026: limite real do equipamento no modelo = loading time x ocupação máxima (o shadow grava só o loading)
    cap_e = {(f['mae_id'], f['dau_order']): _f(f['loading']) * (_f(f['omax']) if f['omax'] is not None else 100.0) / 100.0
             for f in _fetch("""select mae_id, dau_order, dau_valor_3 as loading, dau_valor_7 as omax
                               from otimizacao_tbotimizacaoequipamentosdaugther where tbcenarios_id = %s and dau_order = any(%s)""",
                            [cid, lista_ord])}
    for s in sombras:
        k = (s['tipo'], s['id1'])
        r = restr.setdefault(k, {'tipo': s['tipo'], 'id': s['id1'], 'nome': s['nome'], 'periodos': 0,
                                 'p_max': 0, 'p_min': 0, 's_max_soma': 0.0, 's_min_soma': 0.0,
                                 'ganho_max': 0.0, 'ganho_max_sem_limite': 0, 'ganho_min': 0.0, 'ganho_min_sem_limite': 0,
                                 'real': 0.0, 'vmax': 0.0, 'vmin': 0.0, 'gargalo': defaultdict(int),
                                 'concorrentes': defaultdict(int), 'periodos_max': [], 'periodos_min': [],
                                 'g1_max': 0.0, 'g1_max_limitado': 0, 'g1_min': 0.0, 'g1_min_limitado': 0, 'base_max': 0.0})
        r['periodos'] += 1
        r['real'] += _f(s['valor_real'])
        r['vmax'] += _f(s['vmax'])
        r['vmin'] += _f(s['vmin'])
        smax, smin = _f(s['s_max']), _f(s['s_min'])
        # limite MÁXIMO como está no modelo (b): equipamento = loading x ocupação máx.; os demais = o próprio máximo
        lim_max = cap_e.get((s['id1'], s['dau_order']), _f(s['vmax'])) if s['tipo'] == 'E' else _f(s['vmax'])
        if smax > 1e-9:
            r['p_max'] += 1
            r['s_max_soma'] += smax
            r['periodos_max'].append(rotulo_periodo(s['dau_order']))
            if _finito(s['max_ate']) and _finito(lim_max):
                r['ganho_max'] += smax * max(0.0, _f(s['max_ate']) - lim_max)
            else:
                r['ganho_max_sem_limite'] += 1
            # 🌟 20/10/2026: ganho com +1% -- equipamento: +1% de produtividade = +1% nas horas do limite (o coeficiente é
            # output / (produtividade x (1 - paradas não programadas)), então dividir por 1,01 a hora por unidade é o mesmo
            # que multiplicar o limite (loading x ocupação máx.) por 1,01); demais: +1% no limite máximo. Preso à faixa de
            # validade do preço sombra (dau_valor_11).
            passo = 0.01 * lim_max
            if _finito(s['max_ate']) and _f(s['max_ate']) - lim_max < passo:
                passo = max(0.0, _f(s['max_ate']) - lim_max)
                r['g1_max_limitado'] += 1
            r['g1_max'] += smax * passo
            r['base_max'] += lim_max
            if s['gargalo']:
                r['gargalo'][s['gargalo']] += 1
            if s['conc_aumentar']:
                r['concorrentes'][s['conc_aumentar']] += 1
        if smin > 1e-9:
            r['p_min'] += 1
            r['s_min_soma'] += smin
            r['periodos_min'].append(rotulo_periodo(s['dau_order']))
            if _finito(s['min_de']) and _finito(s['vmin']):
                r['ganho_min'] += smin * max(0.0, _f(s['vmin']) - _f(s['min_de']))
            else:
                r['ganho_min_sem_limite'] += 1
            # 🌟 20/10/2026: ganho com -1% no limite MÍNIMO (preso à faixa de validade, dau_valor_8)
            if s['tipo'] != 'E':
                passo = 0.01 * _f(s['vmin'])
                if _finito(s['min_de']) and _f(s['vmin']) - _f(s['min_de']) < passo:
                    passo = max(0.0, _f(s['vmin']) - _f(s['min_de']))
                    r['g1_min_limitado'] += 1
                r['g1_min'] += smin * passo
            if s['conc_reduzir']:
                r['concorrentes'][s['conc_reduzir']] += 1

    def limpa(r):
        o = {k: v for k, v in r.items() if k not in ('gargalo', 'concorrentes')}
        o['s_max_medio'] = r['s_max_soma'] / r['p_max'] if r['p_max'] else 0.0
        o['s_min_medio'] = r['s_min_soma'] / r['p_min'] if r['p_min'] else 0.0
        o['gargalo'] = max(r['gargalo'].items(), key=lambda kv: kv[1])[0] if r['gargalo'] else ''
        o['concorrentes'] = [c for c, _n in sorted(r['concorrentes'].items(), key=lambda kv: -kv[1])[:3]]
        return o

    restricoes = [limpa(r) for r in restr.values()]
    por_tipo = defaultdict(list)
    for r in restricoes:
        por_tipo[r['tipo']].append(r)

    # equipamentos: junta horas / ocupação + preço sombra da hora + margem que passa por ele
    sombra_e = {r['id']: r for r in por_tipo.get('E', [])}
    # nome do equipamento: o mesmo texto que a otimização grava na tabela de preços sombra (str do cadastro)
    nomes_eq = {}
    try:
        from otimizacao.models import TbOtimizacaoEquipamentos
        for o in TbOtimizacaoEquipamentos.objects.filter(tbcenarios_id=cid).select_related('oti_equ_equipamento'):
            nomes_eq[o.id] = str(o.oti_equ_equipamento)
    except Exception:
        pass
    # 🌟 19/10/2026: equipamento marcado como gargalo no cadastro (só esses têm limite de capacidade no modelo)
    cad_rows = _fetch("""select id, equ_cad_gargalo as gargalo, equ_cad_codigo as codigo from equipamentos_tbequipamentoscadastro where id in
           (select oti_equ_equipamento_id from otimizacao_tbotimizacaoequipamentos where tbcenarios_id = %s)""", [cid])
    gargalo_cad = {r['id']: bool(r['gargalo']) for r in cad_rows}
    codigo_cad = {r['id']: r['codigo'] for r in cad_rows}
    equipamentos = []
    for mid, a in acc_e.items():
        m = maes_eq.get(mid)
        if not m:
            continue
        s = sombra_e.get(mid, {})
        fl = eq_fluxo.get(m['equip_id'], set())
        equipamentos.append({'id': mid, 'nome': s.get('nome') or nomes_eq.get(mid) or codigo_cad.get(m['equip_id']) or str(mid), 'cad_id': m['equip_id'],
                             'e_gargalo': gargalo_cad.get(m['equip_id'], False), 'loading': a['loading'], 'horas': a['horas'],
                             'volume': a['volume'], 'ocupacao': 100.0 * a['horas'] / a['loading'] if a['loading'] > 0 else None,
                             'periodos': a['periodos'], 'periodos_100': a['periodos_100'],
                             'margem_passa': sum(margem_por_fluxo.get(x, 0.0) for x in fl), 'n_fluxos': len(fl),
                             'sombra_hora': s.get('s_max_medio', 0.0), 'p_gargalo': s.get('p_max', 0),
                             'ganho_max': s.get('ganho_max', 0.0), 'ganho_max_sem_limite': s.get('ganho_max_sem_limite', 0),
                             'ganho_1pct': s.get('g1_max', 0.0), 'ganho_1pct_limitado': s.get('g1_max_limitado', 0),
                             'horas_1pct': 0.01 * s.get('base_max', 0.0) / s['p_max'] if s.get('p_max') else None,
                             'gargalo': s.get('gargalo', ''), 'concorrentes': s.get('concorrentes', [])})
    for e in equipamentos:
        e['mct_hora'] = e['margem_passa'] / e['horas'] if e['horas'] > 0 else None
    equipamentos.sort(key=lambda r: (-(r['p_gargalo']), -(r['sombra_hora'] or 0), -(r['margem_passa'] or 0)))

    # insumos (itens de custo): consumo x mínimo / máximo
    maes_ic = {r['id']: r for r in _fetch(
        """select o.id, ci.cus_ite_nome as nome from otimizacao_tbotimizacaocustoitem o
                join tabelas_tbcustoitempreco cp on cp.id = o.oti_cus_ite_item_id
                join tabelas_tbcustoitem ci on ci.id = cp.cus_ite_pre_item_id
           where o.tbcenarios_id = %s""", [cid])}
    acc_i = defaultdict(lambda: {'consumo': 0.0, 'cmin': 0.0, 'cmax': 0.0, 'estoque_valor': 0.0, 'ativo': 0, 'mensal': [],
                                 'sem_max': True})
    # 🌟 19/10/2026: preço sombra do insumo POR PERÍODO, para dizer em que período o consumo ficou preso no mínimo / máximo
    sombra_ic_per = {(s['id1'], s['dau_order']): s for s in sombras if s['tipo'] == 'IC'}
    for f in _fetch("""select mae_id, dau_order, dau_valor_1 as cmin, dau_valor_2 as cmax, dau_valor_3 as consumo, dau_valor_4 as ativo,
                              dau_valor_6 as estoque_valor
                       from otimizacao_tbotimizacaocustoitemdaugther where tbcenarios_id = %s and dau_order = any(%s)
                       order by mae_id, dau_order""",
                    [cid, lista_ord]):
        a = acc_i[f['mae_id']]
        a['consumo'] += _f(f['consumo'])
        a['estoque_valor'] += _f(f['estoque_valor'])
        if f['ativo']:
            a['ativo'] += 1
            a['cmin'] += _f(f['cmin'])
            a['cmax'] += _f(f['cmax'])
            if _f(f['cmax']) < LIMITE_SEM_MAXIMO:
                a['sem_max'] = False
            sp = sombra_ic_per.get((f['mae_id'], f['dau_order'])) or {}
            if _f(sp.get('s_min')) > 1e-9:
                situacao = 'no_minimo_custando'      # obrigado a consumir: reduzir o mínimo aumenta a MCT
            elif _f(sp.get('s_max')) > 1e-9:
                situacao = 'no_maximo_faltando'      # falta insumo: aumentar o máximo aumenta a MCT
            elif _f(f['consumo']) <= _f(f['cmin']) + max(1.0, 0.0005 * _f(f['cmin'])):
                situacao = 'no_minimo_sem_custo'
            elif _f(f['consumo']) >= _f(f['cmax']) - max(1.0, 0.0005 * _f(f['cmax'])):
                situacao = 'no_maximo_sem_custo'
            else:
                situacao = 'entre_limites'
            a['mensal'].append({'periodo': rotulo_periodo(f['dau_order']), 'consumo': _f(f['consumo']), 'minimo': _f(f['cmin']),
                                'maximo': None if _f(f['cmax']) >= LIMITE_SEM_MAXIMO else _f(f['cmax']), 'situacao': situacao,
                                'sombra_min': _f(sp.get('s_min')), 'sombra_max': _f(sp.get('s_max'))})
    sombra_i = {r['id']: r for r in por_tipo.get('IC', [])}
    insumos = []
    for mid, a in acc_i.items():
        s = sombra_i.get(mid)
        if not s and not a['ativo']:
            continue          # sem limite de consumo: não é restrição
        insumos.append({'nome': (maes_ic.get(mid) or {}).get('nome') or (s or {}).get('nome') or str(mid),
                        'consumo': a['consumo'], 'cmin': a['cmin'], 'cmax': None if a['sem_max'] else a['cmax'], 'periodos_limite': a['ativo'],
                        'p_max': (s or {}).get('p_max', 0), 's_max_medio': (s or {}).get('s_max_medio', 0.0),
                        'ganho_max': (s or {}).get('ganho_max', 0.0), 'ganho_max_sem_limite': (s or {}).get('ganho_max_sem_limite', 0),
                        'p_min': (s or {}).get('p_min', 0), 's_min_medio': (s or {}).get('s_min_medio', 0.0),
                        'ganho_min': (s or {}).get('ganho_min', 0.0), 'ganho_min_sem_limite': (s or {}).get('ganho_min_sem_limite', 0),
                        'g1_max': (s or {}).get('g1_max', 0.0), 'g1_min': (s or {}).get('g1_min', 0.0),
                        'concorrentes': (s or {}).get('concorrentes', []),
                        'sem_maximo': a['sem_max'], 'mensal': a['mensal'],
                        'periodos_no_minimo': [m['periodo'] for m in a['mensal'] if m['situacao'] == 'no_minimo_custando'],
                        'minimos_presos': [{'periodo': m['periodo'], 'minimo': m['minimo'], 'consumo': m['consumo']}
                                           for m in a['mensal'] if m['situacao'] == 'no_minimo_custando'],
                        'maximos_atingidos': [{'periodo': m['periodo'], 'maximo': m['maximo'], 'consumo': m['consumo']}
                                              for m in a['mensal'] if m['situacao'] == 'no_maximo_faltando'],
                        'periodos_no_maximo': [m['periodo'] for m in a['mensal'] if m['situacao'] == 'no_maximo_faltando']})
    insumos.sort(key=lambda r: -(r['ganho_max'] + r['ganho_min'] + 1e-6 * (r['p_max'] + r['p_min'])))

    # limites comerciais (produto e produto/mercado) e conjuntos de equipamentos
    def lista_limites(tipo):
        ls = [r for r in por_tipo.get(tipo, []) if r['p_max'] or r['p_min']]
        ls.sort(key=lambda r: -(r['ganho_max'] + r['ganho_min'] + 1e-6 * (r['s_max_soma'] + r['s_min_soma'])))
        return ls

    limites_pm, limites_p, limites_ce = lista_limites('PM'), lista_limites('P'), lista_limites('CE')

    # 🌟 19/10/2026: Equipamentos/Conjunto -- limite de PRODUÇÃO (soma do output real dos equipamentos do conjunto) e, dentro
    # dele, a ocupação de cada equipamento que faz parte
    eq_por_cad = defaultdict(list)
    for e in equipamentos:
        eq_por_cad[e['cad_id']].append(e)
    sombra_ce = {r['id']: r for r in por_tipo.get('CE', [])}
    conjuntos = []
    membros_ce = defaultdict(list)
    try:
        for r in _fetch("""select j.tbotimizacaoconjuntoequipamentos_id as ce_id, j.tbequipamentoscadastro_id as cad_id, c.equ_cad_codigo as codigo
                           from otimizacao_tbotimizacaoconjuntoequipamentos_oti_con_equ_equ15bf j
                                left join equipamentos_tbequipamentoscadastro c on c.id = j.tbequipamentoscadastro_id""", []):
            membros_ce[r['ce_id']].append(r)
        for ce in _fetch("""select id, oti_con_equ_descricao as nome from otimizacao_tbotimizacaoconjuntoequipamentos
                            where tbcenarios_id = %s and oti_con_equ_ativo = true order by oti_con_equ_descricao""", [cid]):
            sr = sombra_ce.get(ce['id']) or next((r for r in restricoes if r['tipo'] == 'CE' and r['id'] == ce['id']), None) or {}
            membros = []
            for mb in membros_ce.get(ce['id'], []):
                for e in eq_por_cad.get(mb['cad_id'], []) or [{'nome': mb['codigo'] or str(mb['cad_id']), 'e_gargalo': gargalo_cad.get(mb['cad_id'], False),
                                                               'ocupacao': None, 'horas': 0.0, 'loading': 0.0, 'p_gargalo': 0, 'sombra_hora': 0.0}]:
                    membros.append({'nome': e['nome'], 'e_gargalo': e['e_gargalo'], 'ocupacao': e['ocupacao'], 'horas': e['horas'],
                                    'loading': e['loading'], 'p_gargalo': e['p_gargalo'], 'sombra_hora': e['sombra_hora']})
            real, vmax, vmin = _f(sr.get('real')), _f(sr.get('vmax')), _f(sr.get('vmin'))
            conjuntos.append({'id': ce['id'], 'nome': ce['nome'] or str(ce['id']), 'producao': real,
                              'maximo': None if vmax >= LIMITE_SEM_MAXIMO else vmax, 'minimo': vmin,
                              'utilizacao': 100.0 * real / vmax if 0 < vmax < LIMITE_SEM_MAXIMO else None,
                              'periodos': sr.get('periodos', 0), 'p_max': sr.get('p_max', 0), 'p_min': sr.get('p_min', 0),
                              's_max_medio': sr.get('s_max_medio', 0.0), 's_min_medio': sr.get('s_min_medio', 0.0),
                              'ganho_max': sr.get('ganho_max', 0.0), 'ganho_max_sem_limite': sr.get('ganho_max_sem_limite', 0),
                              'ganho_min': sr.get('ganho_min', 0.0), 'ganho_min_sem_limite': sr.get('ganho_min_sem_limite', 0),
                              'g1_max': sr.get('g1_max', 0.0), 'g1_min': sr.get('g1_min', 0.0),
                              'periodos_max': sr.get('periodos_max', []), 'periodos_min': sr.get('periodos_min', []),
                              'concorrentes': sr.get('concorrentes', []), 'membros': membros})
        conjuntos.sort(key=lambda r: (-r['p_max'], -(r['ganho_max'] or 0), r['nome']))
    except Exception:
        conjuntos = []
    cad_conj = defaultdict(list)
    for c in conjuntos:
        for mb in membros_ce.get(c['id'], []):
            cad_conj[mb['cad_id']].append(c['nome'])
    for e in equipamentos:
        e['conjuntos'] = cad_conj.get(e['cad_id'], [])

    # ---------------- o que analisar: todas as restrições ativas, pelo ganho potencial ----------------
    acao = {
        ('E', 'max'): 'Capacidade do equipamento (investimento / produtividade / paradas)',
        ('CE', 'max'): 'Capacidade do conjunto de equipamentos',
        ('CE', 'min'): 'Produção mínima exigida do conjunto de equipamentos',
        ('P', 'max'): 'Mercado: vender mais do produto (limite máximo de vendas)',
        ('P', 'min'): 'Venda mínima obrigatória do produto (contrato / estratégia)',
        ('PM', 'max'): 'Mercado: vender mais no produto/mercado (limite máximo)',
        ('PM', 'min'): 'Venda mínima obrigatória no produto/mercado (contrato)',
        ('IC', 'max'): 'Disponibilidade do insumo (suprimento)',
        ('IC', 'min'): 'Consumo mínimo exigido do insumo',
        ('E', 'min'): 'Ocupação mínima do equipamento',
    }
    rotulo_tipo = {'E': 'Equipamento', 'CE': 'Conjunto de equipamentos', 'P': 'Produto', 'PM': 'Produto/mercado', 'IC': 'Insumo'}
    # 🌟 19/10/2026: o que fazer com cada limite ativo -- dito sem ambiguidade (máximo ativo = AUMENTAR o máximo; mínimo
    # ativo = REDUZIR o mínimo), para a IA não inverter o sentido
    o_que_fazer = {
        ('E', 'max'): 'AUMENTAR a capacidade do equipamento (horas, produtividade, menos paradas)',
        ('E', 'min'): 'REDUZIR a ocupação mínima exigida do equipamento',
        ('CE', 'max'): 'AUMENTAR a capacidade do conjunto de equipamentos',
        ('CE', 'min'): 'REDUZIR a produção mínima exigida do conjunto de equipamentos',
        ('P', 'max'): 'AUMENTAR o limite máximo de vendas do produto (mais mercado)',
        ('P', 'min'): 'REDUZIR a venda mínima obrigatória do produto',
        ('PM', 'max'): 'AUMENTAR o limite máximo de vendas no produto/mercado',
        ('PM', 'min'): 'REDUZIR a venda mínima obrigatória no produto/mercado',
        ('IC', 'max'): 'AUMENTAR a disponibilidade (consumo máximo) do insumo',
        ('IC', 'min'): 'REDUZIR o consumo mínimo obrigatório do insumo (o modelo consome só porque é obrigado)',
    }
    analisar = []
    for r in restricoes:
        for lado in ('max', 'min'):
            p = r['p_' + lado]
            if not p:
                continue
            analisar.append({'tipo': rotulo_tipo.get(r['tipo'], r['tipo']), 'tipo_cod': r['tipo'], 'lado': lado, 'id': r['id'],
                             'nome': r['nome'], 'acao': acao.get((r['tipo'], lado), ''), 'periodos': p,
                             'o_que_fazer': o_que_fazer.get((r['tipo'], lado), ''),
                             'periodos_lista': r['periodos_' + lado],
                             'sombra_media': r['s_' + lado + '_medio'], 'ganho': r['ganho_' + lado],
                             'ganho_1pct': r['g1_' + lado] if not (r['tipo'] == 'E' and lado == 'min') else None,
                             'sem_limite': r['ganho_' + lado + '_sem_limite'],
                             'unidade': 'h' if r['tipo'] == 'E' else '', 'concorrentes': r['concorrentes'],
                             'gargalo': r['gargalo'] if lado == 'max' else ''})
    analisar.sort(key=lambda a: -(a['ganho'] + 1e-6 * a['sombra_media'] * a['periodos']))
    # 🌟 23/10/2026: equipamento no "o que analisar" leva se é gargalo no cadastro e a ocupação (a IA não pode adivinhar)
    eq_por_id = {e['id']: e for e in equipamentos}
    for a in analisar:
        if a['tipo_cod'] == 'E' and a['id'] in eq_por_id:
            a['e_gargalo'] = eq_por_id[a['id']]['e_gargalo']
            a['ocupacao'] = eq_por_id[a['id']]['ocupacao']

    # 🌟 19/10/2026: ocupação mínima forçada (ex.: 100% / 100%) -> quanto a MCT mudaria com 0-100% (modelo refeito)
    # só quando o usuário pediu (a pergunta é feita antes de disparar a análise)
    if simular_minimos:
        try:
            minimos_eq = relaxar_ocupacao_minima(cid, lista_ord, rotulo_periodo, {e['id']: e['nome'] for e in equipamentos})
        except Exception as erro:
            minimos_eq = {'equipamentos': [], 'todos': None, 'erro': str(erro)[:200]}
        minimos_eq['simulado'] = True
    else:
        minimos_eq = {'equipamentos': [], 'todos': None, 'simulado': False}
    for m in minimos_eq['equipamentos']:
        m['e_gargalo'] = next((e['e_gargalo'] for e in equipamentos if e['id'] == m['id']), True)
    # no "o que analisar", o mínimo do equipamento passa a usar o ganho EXATO refeito (no lugar da estimativa do preço sombra)
    exatos = {m['id']: m for m in minimos_eq['equipamentos']}
    analisar = [a for a in analisar if not (a['tipo_cod'] == 'E' and a['lado'] == 'min' and a.get('id') in exatos)]
    for m in minimos_eq['equipamentos']:
        if m['ganho'] > 0.5:
            analisar.append({'tipo': 'Equipamento', 'tipo_cod': 'E', 'lado': 'min', 'id': m['id'], 'nome': m['nome'],
                             'acao': 'Ocupação mínima do equipamento',
                             'o_que_fazer': f"REDUZIR a ocupação mínima de {m['omin']:.0f}% para 0% (faixa 0-{m['omax']:.0f}%): ganho calculado refazendo a otimização",
                             'periodos': m['periodos'], 'periodos_lista': m['periodos_lista'], 'sombra_media': 0.0,
                             'ganho': m['ganho'], 'sem_limite': 0, 'unidade': 'h', 'concorrentes': [], 'gargalo': '', 'exato': True})
    analisar.sort(key=lambda a: -(a['ganho'] + 1e-6 * a['sombra_media'] * a['periodos']))

    soma_pm = sum(r['margem'] for r in produtos_mercados)
    # 🌟 23/10/2026: conferência -- com a produção antecipada aplicada, o Consolidar custeia as vendas produzidas antes
    # com o custo do período da PRODUÇÃO (ajuste em TbOtimizacaoAntecipacaoDaugther 8/9/10); a margem dos
    # produtos/mercados usa o custo do período da VENDA. Essa parte da diferença é separada do arredondamento.
    try:
        aj = _fetch("""select coalesce(sum(coalesce(d.dau_valor_8, 0) + coalesce(d.dau_valor_9, 0) + coalesce(d.dau_valor_10, 0)), 0) as v
                       from otimizacao_tbotimizacaoantecipacaodaugther d join otimizacao_tbotimizacaoantecipacao m on m.id = d.mae_id
                       where m.tbcenarios_id = %s and m.oti_ant_aplicado = true and d.dau_order = any(%s)""", [cid, lista_ord])
        ajuste_antecipacao = _f(aj[0]['v']) if aj else 0.0
    except Exception:
        ajuste_antecipacao = 0.0
    variaveis, variaveis_numero = _info_variaveis(cid, [equipamentos, limites_pm, limites_p, limites_ce, conjuntos, insumos, analisar])
    return {
        'tipo_fluxo': 'analise_resultados',
        'resumo': dict(resumo, soma_margem_pm=soma_pm, soma_margem_fluxos=sum(r['margem'] for r in fluxos),
                       ajuste_antecipacao=ajuste_antecipacao),
        'variaveis': variaveis, 'variaveis_numero': variaveis_numero,
        'produtos': produtos[:MAX_LINHAS], 'produtos_n': len(produtos),
        'produtos_mercados': produtos_mercados[:MAX_LINHAS], 'produtos_mercados_n': len(produtos_mercados),
        'fluxos': fluxos[:MAX_LINHAS], 'fluxos_n': len(fluxos), 'fluxos_sem_producao': fluxos_sem_producao,
        'vendidos_negativos': vendidos_negativos[:MAX_LINHAS],
        'equipamentos': equipamentos[:MAX_LINHAS], 'equipamentos_n': len(equipamentos),
        'limites_pm': limites_pm[:MAX_LINHAS], 'limites_p': limites_p[:MAX_LINHAS], 'limites_ce': limites_ce[:MAX_LINHAS],
        'conjuntos': conjuntos[:MAX_LINHAS],
        'insumos': insumos[:MAX_LINHAS],
        'analisar': analisar[:MAX_LINHAS], 'analisar_n': len(analisar),
        'minimos_equipamentos': minimos_eq,
    }


def _relaxar_ocupacao_minima_integrado(cf, forc, rotulo_periodo, nomes):
    import numpy as np
    R0 = cf.resolver({})
    if R0 is None:
        return {'equipamentos': [], 'todos': None}
    eids = sorted({e for d in forc.values() for e in d})
    saida = []
    for eid in eids:
        alt, per = {}, []
        for i, d in forc.items():
            if eid not in d or i not in cf.ordens:
                continue
            m = cf.modelo(i)
            lin = m['linhas_e'].get(eid)
            if lin is None:
                continue
            b2 = m['b'].copy()
            b2[lin] = 0.0
            alt[i] = {'b': b2}
            per.append((i, lin))
        if not alt:
            continue
        R = cf.resolver(alt)
        omins = [forc[i][eid][0] for i, _l in per]
        omaxs = [forc[i][eid][1] for i, _l in per]
        loading = sum(forc[i][eid][2] for i, _l in per)
        h0 = sum(float(np.dot(cf.modelo(i)['A'][lin + 1], R0.producao_de(i))) for i, lin in per)
        if R is None:
            saida.append({'id': eid, 'nome': nomes.get(eid) or str(eid), 'periodos': 0, 'periodos_forcados': len(per),
                          'periodos_lista': [], 'omin': max(omins), 'omax': max(omaxs), 'mct_base': R0.mct, 'mct_relax': R0.mct,
                          'ganho': 0.0, 'ocup_base': 100.0 * h0 / loading if loading > 0 else None, 'ocup_relax': None,
                          'por_periodo': [], 'falhou': len(per)})
            continue
        h1 = sum(float(np.dot(cf.modelo(i)['A'][lin + 1], R.producao_de(i))) for i, lin in per)
        por = []
        for i, lin in per:
            ld = forc[i][eid][2]
            a0 = float(np.dot(cf.modelo(i)['A'][lin + 1], R0.producao_de(i)))
            a1 = float(np.dot(cf.modelo(i)['A'][lin + 1], R.producao_de(i)))
            por.append({'periodo': rotulo_periodo(i), 'ganho': R.mct_de(i) - R0.mct_de(i),
                        'ocup_base': 100.0 * a0 / ld if ld > 0 else None, 'ocup_relax': 100.0 * a1 / ld if ld > 0 else None})
        saida.append({'id': eid, 'nome': nomes.get(eid) or str(eid), 'periodos': len(per), 'periodos_forcados': len(per),
                      'periodos_lista': [p['periodo'] for p in por], 'omin': max(omins), 'omax': max(omaxs),
                      'mct_base': R0.mct, 'mct_relax': R.mct, 'ganho': R.mct - R0.mct,
                      'ocup_base': 100.0 * h0 / loading if loading > 0 else None,
                      'ocup_relax': 100.0 * h1 / loading if loading > 0 else None, 'por_periodo': por, 'falhou': 0})
    saida.sort(key=lambda r: -r['ganho'])
    alt_todos = {}
    for i, d in forc.items():
        if i not in cf.ordens:
            continue
        m = cf.modelo(i)
        b3 = m['b'].copy()
        for eid in d:
            lin = m['linhas_e'].get(eid)
            if lin is not None:
                b3[lin] = 0.0
        alt_todos[i] = {'b': b3}
    t = None
    if alt_todos:
        R = cf.resolver(alt_todos)
        if R is not None:
            t = {'mct_base': R0.mct, 'mct_relax': R.mct, 'ganho': R.mct - R0.mct, 'periodos': len(alt_todos), 'falhou': 0}
    return {'equipamentos': saida, 'todos': t, 'modelo_integrado': True}


def relaxar_ocupacao_minima(cid, lista_ord, rotulo_periodo, nomes=None):
    """🌟 19/10/2026: equipamentos com OCUPAÇÃO MÍNIMA > 0 (ex.: 100% / 100% -- obrigados a rodar cheios). Para cada um,
    o MESMO modelo do Otimizar (otimizacao.sensibilidade.montar_modelo_periodo) é resolvido de novo em cada período com a
    ocupação mínima dele em 0% (o máximo continua o mesmo), e uma vez com TODOS os mínimos em 0%. Resultado EXATO (não é
    a estimativa do preço sombra): MCT base refeita x MCT com 0-100%, e a ocupação que o modelo escolheria."""
    import numpy as np
    from otimizacao.sensibilidade import montar_modelo_periodo, _resolver
    nomes = nomes or {}
    forc = defaultdict(dict)          # período -> {oti_equip_id: (omin, omax, loading)}
    for f in _fetch("""select mae_id, dau_order, dau_valor_3 as loading, dau_valor_5 as omin, dau_valor_7 as omax
                       from otimizacao_tbotimizacaoequipamentosdaugther
                       where tbcenarios_id = %s and dau_order = any(%s) and flag = true and coalesce(dau_valor_5, 0) > 0""",
                    [cid, lista_ord]):
        forc[f['dau_order']][f['mae_id']] = (_f(f['omin']), _f(f['omax']), _f(f['loading']))
    if not forc:
        return {'equipamentos': [], 'todos': None}
    # 🌟 NOVO (22/10/2026): cenário FINAL -- com a produção antecipada aplicada pelo Otimizar, o mínimo é relaxado em todos
    # os períodos em que é exigido e o modelo INTEGRADO (todos os períodos juntos, com estoque) é resolvido de novo
    from otimizacao.antecipacao import CenarioFinal
    cf = CenarioFinal(cid, lista_ord)
    if cf.ativo:
        return _relaxar_ocupacao_minima_integrado(cf, forc, rotulo_periodo, nomes)
    acc = defaultdict(lambda: {'base': 0.0, 'relax': 0.0, 'periodos': [], 'h_base': 0.0, 'h_relax': 0.0, 'loading': 0.0,
                               'omin': [], 'omax': [], 'falhou': 0})
    todos = {'base': 0.0, 'relax': 0.0, 'periodos': 0, 'falhou': 0}
    for i in sorted(forc):
        try:
            mod = montar_modelo_periodo(cid, i)
        except Exception:
            continue
        if not mod['vars']:
            continue
        A, b, c = mod['A'], mod['b'], mod['c']
        r0 = _resolver(c, A, b, mod['bounds'])
        if r0 is None:
            continue
        base = -float(r0.fun)
        linhas = {eid: mod['linhas_e'][eid] for eid in forc[i] if eid in mod['linhas_e']}
        rot = rotulo_periodo(i)
        for eid, lin in linhas.items():
            omin, omax, loading = forc[i][eid]
            b2 = b.copy()
            b2[lin] = 0.0                                   # -horas <= 0  =>  ocupação mínima 0%
            r = _resolver(c, A, b2, mod['bounds'])
            a = acc[eid]
            a['omin'].append(omin)
            a['omax'].append(omax)
            a['loading'] += loading
            h0 = float(np.dot(A[lin + 1], r0.x))
            if r is None:
                a['falhou'] += 1
                continue
            h1 = float(np.dot(A[lin + 1], r.x))
            a['base'] += base
            a['relax'] += -float(r.fun)
            a['h_base'] += h0
            a['h_relax'] += h1
            a['periodos'].append({'periodo': rot, 'ganho': -float(r.fun) - base,
                                  'ocup_base': 100.0 * h0 / loading if loading > 0 else None,
                                  'ocup_relax': 100.0 * h1 / loading if loading > 0 else None})
        if linhas:
            b3 = b.copy()
            for lin in linhas.values():
                b3[lin] = 0.0
            r = _resolver(c, A, b3, mod['bounds'])
            if r is None:
                todos['falhou'] += 1
            else:
                todos['base'] += base
                todos['relax'] += -float(r.fun)
                todos['periodos'] += 1
    saida = []
    for eid, a in acc.items():
        n = len(a['periodos'])
        saida.append({'id': eid, 'nome': nomes.get(eid) or str(eid), 'periodos': n, 'periodos_forcados': len(a['omin']),
                      'periodos_lista': [p['periodo'] for p in a['periodos']],
                      'omin': max(a['omin']) if a['omin'] else 0.0, 'omax': max(a['omax']) if a['omax'] else 0.0,
                      'mct_base': a['base'], 'mct_relax': a['relax'], 'ganho': a['relax'] - a['base'],
                      'ocup_base': 100.0 * a['h_base'] / a['loading'] if a['loading'] > 0 else None,
                      'ocup_relax': 100.0 * a['h_relax'] / a['loading'] if a['loading'] > 0 else None,
                      'por_periodo': a['periodos'], 'falhou': a['falhou']})
    saida.sort(key=lambda r: -r['ganho'])
    t = None
    if todos['periodos']:
        t = {'mct_base': todos['base'], 'mct_relax': todos['relax'], 'ganho': todos['relax'] - todos['base'],
             'periodos': todos['periodos'], 'falhou': todos['falhou']}
    return {'equipamentos': saida, 'todos': t}
