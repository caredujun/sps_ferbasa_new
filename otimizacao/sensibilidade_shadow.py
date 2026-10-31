"""
🌟 NOVO (20/10/2026): "Cenário - Sensibilidade Ganho Margem x Produtividade" e
"Cenário - Sensibilidade Ganho Margem x Limite Comercial".

Pergunta: quanto a MARGEM DE CONTRIBUIÇÃO TOTAL (MCT) do cenário muda se a PRODUTIVIDADE de um equipamento gargalo
(ou um LIMITE COMERCIAL máximo / mínimo de produto ou produto/mercado) aumentar ou diminuir X%?

Duas respostas lado a lado, em cada ponto da grade (de -30% a +30%) e em cada período otimizado:
  1) ESTIMATIVA PELO PREÇO SOMBRA (TbOtimizacaoShadow, gravado pelo Otimizar): ganho = sombra x variação do limite.
     Vale só dentro da faixa de validade do preço sombra (dau_valor_8/9 para o mínimo, dau_valor_10/11 para o máximo);
     fora dela o ponto é marcado como "fora da faixa" (o preço sombra muda e a estimativa deixa de valer).
  2) VALOR EXATO: o MESMO modelo do Otimizar (otimizacao.sensibilidade.montar_modelo_periodo) é resolvido de novo com
     o limite alterado. Mostra até onde o preço sombra vale e o que acontece depois (outro limite passa a segurar).

Produtividade: o coeficiente do equipamento é output / (produtividade x (1 - % paradas não programadas)); mudar a
produtividade em dp divide a linha do equipamento por (1 + dp), o que equivale a multiplicar o limite (loading time x
ocupação máxima) por (1 + dp). Por isso a estimativa usa sombra (moeda/h) x limite x dp.
Limite comercial: máximo -> b do máximo x (1 + d); mínimo -> mínimo x (1 + d) (reduzir o mínimo = d negativo).
"""
from collections import defaultdict

import numpy as np

GRADE = [-30, -25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25, 30]
# 🌟 20/10/2026: limite MÍNIMO -- a redução vai até ZERAR o mínimo (quanto se ganharia eliminando a restrição comercial)
GRADE_MINIMO = [-100, -90, -80, -70, -60, -50, -40, -30, -20, -10, 0, 10, 20, 30]   # passo uniforme (o gráfico é em categorias)
INFINITO = 1e20
PERC_CHEIO = 99.5


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch(sql, params):
    from django.db import connection
    with connection.cursor() as c:
        c.execute(sql, params)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def _sombras(cid, tipo, rid, ordens):
    """dau_order -> sombra do período (s_min, s_max, faixas)."""
    return {r['dau_order']: r for r in _fetch(
        """select dau_order, dau_valor_1 as s_min, dau_valor_2 as s_max, dau_valor_5 as vmin, dau_valor_6 as vmax,
                  dau_valor_8 as min_de, dau_valor_9 as min_ate, dau_valor_10 as max_de, dau_valor_11 as max_ate
           from otimizacao_tbotimizacaoshadow
           where tbcenarios_id = %s and oti_shadow_tipo = %s and oti_shadow_id_1 = %s and dau_order = any(%s)""",
        [cid, tipo, rid, ordens])}


def _dentro(v, de, ate):
    lo = _f(de) if de is not None and abs(_f(de)) < INFINITO else -INFINITO
    hi = _f(ate) if ate is not None and abs(_f(ate)) < INFINITO else INFINITO
    return lo - 1e-6 <= v <= hi + 1e-6


# ------------------------------------------------------------------ listas para escolher
def equipamentos_cheios(cid, ordens, rotulo_periodo):
    """Equipamentos GARGALO com ocupação >= 99,5% em pelo menos um período otimizado."""
    linhas = _fetch("""select d.mae_id, d.dau_order, d.dau_valor_3 as loading, d.dau_valor_6 as horas, d.dau_valor_4 as ocup
                       from otimizacao_tbotimizacaoequipamentosdaugther d
                       join otimizacao_tbotimizacaoequipamentos o on o.id = d.mae_id
                       join equipamentos_tbequipamentoscadastro c on c.id = o.oti_equ_equipamento_id
                       where d.tbcenarios_id = %s and d.dau_order = any(%s) and o.flag = true and c.equ_cad_gargalo = true
                       order by d.mae_id, d.dau_order""", [cid, ordens])
    por = defaultdict(list)
    for l in linhas:
        oc = 100.0 * _f(l['horas']) / _f(l['loading']) if _f(l['loading']) > 0 else (_f(l['ocup']) if l['ocup'] is not None else None)
        por[l['mae_id']].append([rotulo_periodo(l['dau_order']), None if oc is None else round(oc, 2), round(_f(l['horas']), 2), round(_f(l['loading']), 2)])
    cheios = {mid: per for mid, per in por.items() if any(p[1] is not None and p[1] >= PERC_CHEIO for p in per)}
    return cheios


def sombra_media_equipamentos(cid, ordens):
    """oti_equip_id -> preço sombra médio da hora (moeda/h) nos períodos em que o equipamento foi gargalo (sombra > 0)."""
    return {int(r['id']): _f(r['s']) for r in _fetch(
        """select oti_shadow_id_1 as id, avg(dau_valor_2) as s from otimizacao_tbotimizacaoshadow
           where tbcenarios_id = %s and oti_shadow_tipo = 'E' and dau_order = any(%s) and dau_valor_2 > 0
           group by oti_shadow_id_1""", [cid, ordens])}


def limites_comerciais_ativos(cid, ordens, rotulo_periodo):
    """Limites de produto (P) e produto/mercado (PM) que seguraram a margem (preço sombra > 0) em algum período:
    [{chave, tipo, id, lado, nome, periodos: [...], sombra_media, limite_medio}]."""
    acc = {}
    for s in _fetch("""select oti_shadow_tipo as tipo, oti_shadow_id_1 as id, oti_shadow_restricao as nome, dau_order,
                              dau_valor_1 as s_min, dau_valor_2 as s_max, dau_valor_5 as vmin, dau_valor_6 as vmax
                       from otimizacao_tbotimizacaoshadow
                       where tbcenarios_id = %s and dau_order = any(%s) and oti_shadow_tipo in ('P', 'PM')
                       order by oti_shadow_restricao, dau_order""", [cid, ordens]):
        for lado, sv, lim in (('max', s['s_max'], s['vmax']), ('min', s['s_min'], s['vmin'])):
            if _f(sv) <= 1e-9:
                continue
            k = f"{s['tipo']}:{s['id']}:{lado}"
            a = acc.setdefault(k, {'chave': k, 'tipo': s['tipo'], 'id': int(s['id']), 'lado': lado, 'nome': str(s['nome']),
                                   'periodos': [], 'soma_sombra': 0.0, 'soma_limite': 0.0})
            a['periodos'].append(rotulo_periodo(s['dau_order']))
            a['soma_sombra'] += _f(sv)
            a['soma_limite'] += _f(lim)
    saida = []
    for a in acc.values():
        n = len(a['periodos'])
        saida.append({**{k: v for k, v in a.items() if not k.startswith('soma_')},
                      'sombra_media': a['soma_sombra'] / n, 'limite_medio': a['soma_limite'] / n})
    saida.sort(key=lambda r: (-r['sombra_media'], r['nome']))      # 🌟 20/10/2026: maior preço sombra primeiro
    return saida


def detalhes_limite(cid, ordens, tipo, rid, rotulo_periodo):
    """🌟 20/10/2026: período a período (períodos otimizados) do produto/mercado (PM) ou produto (P): limite mínimo,
    limite máximo e volume recomendado pela otimização -- [[período, mín, máx, volume], ...]."""
    tabela = 'otimizacao_tbprodutomercadodaugther' if tipo == 'PM' else 'otimizacao_tbotimizacaoprodutodaugther'
    return [[rotulo_periodo(r['dau_order']), _f(r['vmin']), _f(r['vmax']), _f(r['vol'])] for r in _fetch(
        f"""select dau_order, dau_valor_1 as vmin, dau_valor_2 as vmax, dau_valor_3 as vol from {tabela}
            where mae_id = %s and dau_order = any(%s) order by dau_order""", [rid, ordens])]


# ------------------------------------------------------------------ cálculos
def _produtividade_media(cid, oti_equip_id, ordens):
    """Média simples da produtividade cadastrada (dau_valor_2 de TbEquipamentosDaugther) das ordens do equipamento
    usadas nos fluxos ativos, nos períodos analisados, e a unidade de output do cadastro."""
    r = _fetch("""select avg(ed.dau_valor_2) as prod, count(distinct eq.id) as ordens
                  from otimizacao_tbotimizacaoequipamentos o
                  join equipamentos_tbequipamentos eq on eq.equ_codigo_id = o.oti_equ_equipamento_id
                  join equipamentos_tbequipamentosdaugther ed on ed.mae_id = eq.id and ed.dau_order = any(%s) and ed.tbcenarios_id = %s
                  where o.id = %s and ed.dau_valor_2 > 0
                    and exists (select 1 from fluxos_tbfluxoproducaoinputoutput io where io.flu_pro_inp_out_equipamento_id = eq.id
                                and io.flag = true)""", [ordens, cid, oti_equip_id])
    return (_f(r[0]['prod']) if r and r[0]['prod'] is not None else None), (int(r[0]['ordens']) if r else 0)


def calcular_produtividade(cid, ordens, oti_equip_id, rotulo_periodo=str, grade=None):
    from otimizacao.sensibilidade import (montar_modelo_periodo, _resolver, _linhas_restricoes, _resumo_restricoes,
                                          _nomes_restricoes, _chave_txt, MAX_LIMITANTES_PONTO, SimilaresAcc)
    grade = grade or GRADE
    # 🌟 22/10/2026: restrições do modelo (gargalos, conjuntos de equipamentos, limites comerciais, insumos) que ficaram
    # no limite em cada variação -- ex.: um conjunto de equipamentos no máximo trava o ganho de produtividade
    no_limite = defaultdict(lambda: defaultdict(int))
    restr_vistas = {}
    sim = SimilaresAcc()           # 🌟 22/10/2026: ocupação dos similares e volume por fluxo em cada variação
    sombras = _sombras(cid, 'E', oti_equip_id, ordens)
    cap = {f['dau_order']: (_f(f['loading']), _f(f['omax']) if f['omax'] is not None else 100.0) for f in _fetch(
        """select dau_order, dau_valor_3 as loading, dau_valor_7 as omax from otimizacao_tbotimizacaoequipamentosdaugther
           where mae_id = %s and dau_order = any(%s)""", [oti_equip_id, ordens])}
    exato = np.zeros(len(grade)); estim = np.zeros(len(grade)); fora = np.zeros(len(grade), dtype=int)
    sem_sol = np.zeros(len(grade), dtype=int); horas_x = np.zeros(len(grade))
    base_total, cap_total, por_periodo = 0.0, 0.0, []
    # 🌟 NOVO (22/10/2026): cenário FINAL -- com a produção antecipada aplicada pelo Otimizar, cada variação resolve o
    # modelo INTEGRADO (todos os períodos juntos, com estoque)
    from otimizacao.antecipacao import CenarioFinal
    cf = CenarioFinal(cid, ordens)
    if cf.ativo:
        R0 = cf.resolver({})
        res_k = []
        for k, dp in enumerate(grade):
            fator = 1.0 / (1.0 + dp / 100.0)
            alt = {}
            for o, m in cf.mods:
                lin = m['linhas_e'].get(oti_equip_id)
                if lin is None:
                    continue
                A2 = m['A'].copy()
                A2[lin] = m['A'][lin] * fator
                A2[lin + 1] = m['A'][lin + 1] * fator
                alt[o] = {'A': A2}
            res_k.append((cf.resolver(alt), alt, fator))
    for i in (cf.ordens if cf.ativo else ordens):
        mod = cf.modelo(i) if cf.ativo else montar_modelo_periodo(cid, i)
        if not mod['vars'] or oti_equip_id not in mod['linhas_e']:
            continue
        if cf.ativo:
            if R0 is None:
                break
            lin = mod['linhas_e'][oti_equip_id]
            A, b = mod['A'], mod['b']
            linhas_lim = _linhas_restricoes(mod['info'], b, oti_equip_id, restr_vistas)
            sim_rows, fluxo_var = sim.preparar(mod, A, A[lin + 1] > 1e-12, oti_equip_id)
            x0 = R0.producao_de(i)
            base = R0.mct_de(i)
            base_total += base
            loading, omax = cap.get(i, (0.0, 100.0))
            limite = loading * omax / 100.0
            cap_total += limite
            sp = sombras.get(i) or {}
            smax = _f(sp.get('s_max'))
            h0 = float(np.dot(A[lin + 1], x0))
            linha = {'periodo': rotulo_periodo(i), 'mct_base': base, 'sombra_hora': smax, 'limite_horas': limite,
                     'ocupacao': 100.0 * h0 / limite if limite > 0 else None, 'pontos': []}
            for k, dp in enumerate(grade):
                R, alt, fator = res_k[k]
                ex = None if R is None else R.mct_de(i) - base      # parte do período (margem atribuída ao período da venda)
                if R is not None:
                    A2 = alt[i]['A']
                    xp = R.producao_de(i)
                    horas_x[k] += float(np.dot(A2[lin + 1], xp))
                    for chave, rr in linhas_lim:
                        f = R.folga(i, rr)
                        if f is not None and f <= 1e-6 * max(1.0, abs(b[rr])):
                            no_limite[(0, k)][chave] += 1
                    sim.acumular((0, k), A2, b, xp, sim_rows, fluxo_var)
                novo = limite * (1 + dp / 100.0)
                es = smax * (novo - limite) if smax > 1e-9 else 0.0
                valido = _dentro(novo, sp.get('max_de'), sp.get('max_ate')) if smax > 1e-9 else (novo >= h0 - 1e-6)
                if not valido:
                    fora[k] += 1
                estim[k] += es
                linha['pontos'].append([dp, ex, es, bool(valido)])
            por_periodo.append(linha)
            continue
        lin = mod['linhas_e'][oti_equip_id]
        A, b, c = mod['A'], mod['b'], mod['c']
        linhas_lim = _linhas_restricoes(mod['info'], b, oti_equip_id, restr_vistas)
        sim_rows, fluxo_var = sim.preparar(mod, A, A[lin + 1] > 1e-12, oti_equip_id)
        r0 = _resolver(c, A, b, mod['bounds'])
        if r0 is None:
            continue
        base = -float(r0.fun)
        base_total += base
        loading, omax = cap.get(i, (0.0, 100.0))
        limite = loading * omax / 100.0
        cap_total += limite
        sp = sombras.get(i) or {}
        smax = _f(sp.get('s_max'))
        h0 = float(np.dot(A[lin + 1], r0.x))
        linha = {'periodo': rotulo_periodo(i), 'mct_base': base, 'sombra_hora': smax, 'limite_horas': limite,
                 'ocupacao': 100.0 * h0 / limite if limite > 0 else None, 'pontos': []}
        for k, dp in enumerate(grade):
            fator = 1.0 / (1.0 + dp / 100.0)
            A2 = A.copy()
            A2[lin] = A[lin] * fator
            A2[lin + 1] = A[lin + 1] * fator
            r = _resolver(c, A2, b, mod['bounds'])
            ex = None if r is None else -float(r.fun) - base
            if r is None:
                sem_sol[k] += 1
            else:
                exato[k] += ex
                horas_x[k] += float(np.dot(A2[lin + 1], r.x))
                for chave, rr in linhas_lim:
                    if b[rr] - float(np.dot(A2[rr], r.x)) <= 1e-6 * max(1.0, abs(b[rr])):
                        no_limite[(0, k)][chave] += 1
                sim.acumular((0, k), A2, b, r.x, sim_rows, fluxo_var)
            # estimativa pelo preço sombra: equivale a +dp no limite de horas
            novo = limite * (1 + dp / 100.0)
            es = smax * (novo - limite) if smax > 1e-9 else 0.0
            valido = _dentro(novo, sp.get('max_de'), sp.get('max_ate')) if smax > 1e-9 else (novo >= h0 - 1e-6)
            if not valido:
                fora[k] += 1
            estim[k] += es
            linha['pontos'].append([dp, ex, es, bool(valido)])
        por_periodo.append(linha)
    if cf.ativo and R0 is not None:
        # o ganho exato é o do modelo integrado inteiro (a margem que muda num período pode vir de outro, pelo estoque)
        for k in range(len(grade)):
            R = res_k[k][0]
            if R is None:
                sem_sol[k] = len(por_periodo)
            else:
                exato[k] = R.mct - R0.mct
        base_total = R0.mct
    prod, n_ordens = _produtividade_media(cid, oti_equip_id, ordens)
    k0 = grade.index(0) if 0 in grade else 0
    restricoes = _resumo_restricoes(cid, no_limite, restr_vistas, _nomes_restricoes(cid) if no_limite else {}, 0, k0)
    rotulo_r = {r['chave']: r['rotulo'] for r in restricoes}
    similares, fluxos_rel = sim.cabecalho(cid)

    def limitantes(k):
        lst = sorted(no_limite.get((0, k), {}).items(), key=lambda kv: (-kv[1], rotulo_r.get(_chave_txt(kv[0]), '')))
        return [[rotulo_r.get(_chave_txt(ch), _chave_txt(ch)), n] for ch, n in lst[:MAX_LIMITANTES_PONTO]]
    pontos = [{'d': dp, 'exato': None if sem_sol[k] else float(exato[k]), 'sem_solucao': int(sem_sol[k]), 'limitantes': limitantes(k),
               **sim.ponto((0, k), similares, fluxos_rel, sem_sol[k]),
               'estimativa': float(estim[k]), 'fora_faixa': int(fora[k]),
               'ocupacao': 100.0 * horas_x[k] / cap_total if cap_total > 0 and not sem_sol[k] else None,
               'produtividade': prod * (1 + dp / 100.0) if prod else None} for k, dp in enumerate(grade)]
    return {'grade': grade, 'pontos': pontos, 'mct_base': base_total, 'periodos': len(por_periodo), 'por_periodo': por_periodo,
            'produtividade_media': prod, 'ordens': n_ordens, 'limite_horas': cap_total, 'restricoes_no_limite': restricoes,
            'similares': similares, 'fluxos_rel': fluxos_rel, 'modelo_integrado': bool(cf.ativo)}


def calcular_limite(cid, ordens, tipo, rid, lado, rotulo_periodo=str, grade=None):
    from otimizacao.sensibilidade import montar_modelo_periodo, _resolver
    grade = grade or (GRADE_MINIMO if lado == 'min' else GRADE)
    sombras = _sombras(cid, tipo, rid, ordens)
    exato = np.zeros(len(grade)); estim = np.zeros(len(grade)); fora = np.zeros(len(grade), dtype=int)
    sem_sol = np.zeros(len(grade), dtype=int)
    base_total, lim_total, por_periodo = 0.0, 0.0, []
    # 🌟 20/10/2026: só os períodos em que ESTE limite segurou a margem (preço sombra > 0 no lado escolhido); nos outros
    # nada é alterado (a variação da margem neles é zero). O limite de partida é a SOMA dos limites desses períodos.
    campo = 's_max' if lado == 'max' else 's_min'
    periodos_total = len(ordens)
    from otimizacao.antecipacao import CenarioFinal
    cf = CenarioFinal(cid, ordens)      # 🌟 22/10/2026: cenário final (com produção antecipada) -- modelo integrado
    ordens = [i for i in ordens if _f((sombras.get(i) or {}).get(campo)) > 1e-9]
    if cf.ativo:
        return _calcular_limite_integrado(cf, ordens, tipo, rid, lado, grade, sombras, rotulo_periodo, periodos_total)
    for i in ordens:
        mod = montar_modelo_periodo(cid, i)
        if not mod['vars']:
            continue
        linha_r = next((k for k, t in enumerate(mod['info']) if t[0] == tipo and t[1] == rid and t[2] == lado), None)
        if linha_r is None:
            continue
        A, b, c = mod['A'], mod['b'], mod['c']
        r0 = _resolver(c, A, b, mod['bounds'])
        if r0 is None:
            continue
        base = -float(r0.fun)
        base_total += base
        limite = -float(b[linha_r]) if lado == 'min' else float(b[linha_r])    # o mínimo está como -mín no modelo
        lim_total += limite
        sp = sombras.get(i) or {}
        s = _f(sp.get('s_max' if lado == 'max' else 's_min'))
        nivel = float(np.dot(A[linha_r], r0.x)) * (-1 if lado == 'min' else 1)     # volume vendido/produzido
        linha = {'periodo': rotulo_periodo(i), 'mct_base': base, 'sombra': s, 'limite': limite, 'volume': nivel, 'pontos': []}
        for k, d in enumerate(grade):
            novo = limite * (1 + d / 100.0)
            b2 = b.copy()
            b2[linha_r] = -novo if lado == 'min' else novo
            r = _resolver(c, A, b2, mod['bounds'])
            ex = None if r is None else -float(r.fun) - base
            if r is None:
                sem_sol[k] += 1
            else:
                exato[k] += ex
            if lado == 'max':
                es = s * (novo - limite) if s > 1e-9 else 0.0
                valido = _dentro(novo, sp.get('max_de'), sp.get('max_ate')) if s > 1e-9 else (novo >= nivel - 1e-6)
            else:
                es = s * (limite - novo) if s > 1e-9 else 0.0          # reduzir o mínimo aumenta a margem
                valido = _dentro(novo, sp.get('min_de'), sp.get('min_ate')) if s > 1e-9 else (novo <= nivel + 1e-6)
            if not valido:
                fora[k] += 1
            estim[k] += es
            linha['pontos'].append([d, ex, es, bool(valido)])
        por_periodo.append(linha)
    pontos = [{'d': d, 'exato': None if sem_sol[k] else float(exato[k]), 'sem_solucao': int(sem_sol[k]),
               'estimativa': float(estim[k]), 'fora_faixa': int(fora[k]),
               'limite': lim_total * (1 + d / 100.0)} for k, d in enumerate(grade)]
    return {'grade': grade, 'pontos': pontos, 'mct_base': base_total, 'periodos': len(por_periodo), 'por_periodo': por_periodo,
            'limite_total': lim_total, 'periodos_total': periodos_total}



def _calcular_limite_integrado(cf, ordens, tipo, rid, lado, grade, sombras, rotulo_periodo, periodos_total):
    """🌟 22/10/2026: calcular_limite no cenário FINAL (produção antecipada aplicada): o limite muda nos mesmos períodos
    (os que seguravam a margem), mas cada variação resolve o modelo INTEGRADO (todos os períodos juntos, com estoque)."""
    exato = np.zeros(len(grade)); estim = np.zeros(len(grade)); fora = np.zeros(len(grade), dtype=int)
    sem_sol = np.zeros(len(grade), dtype=int)
    R0 = cf.resolver({})
    if R0 is None:
        return {'grade': grade, 'pontos': [], 'mct_base': 0.0, 'periodos': 0, 'por_periodo': [], 'limite_total': 0.0,
                'periodos_total': periodos_total, 'modelo_integrado': True}
    linhas = {}
    for i in ordens:
        if i not in cf.ordens:
            continue
        m = cf.modelo(i)
        lr = next((k for k, t in enumerate(m['info']) if t[0] == tipo and t[1] == rid and t[2] == lado), None)
        if lr is not None:
            linhas[i] = lr
    lim_total = sum((-float(cf.modelo(i)['b'][lr]) if lado == 'min' else float(cf.modelo(i)['b'][lr])) for i, lr in linhas.items())
    res = []
    for k, d in enumerate(grade):
        alt = {}
        for i, lr in linhas.items():
            b2 = cf.modelo(i)['b'].copy()
            limite = -float(b2[lr]) if lado == 'min' else float(b2[lr])
            novo = limite * (1 + d / 100.0)
            b2[lr] = -novo if lado == 'min' else novo
            alt[i] = {'b': b2}
        R = cf.resolver(alt)
        res.append(R)
        if R is None:
            sem_sol[k] = len(linhas)
        else:
            exato[k] = R.mct - R0.mct
    por_periodo = []
    for i, lr in linhas.items():
        m = cf.modelo(i)
        b = m['b']
        limite = -float(b[lr]) if lado == 'min' else float(b[lr])
        sp = sombras.get(i) or {}
        s = _f(sp.get('s_max' if lado == 'max' else 's_min'))
        x0 = R0.vendas_de(i) if tipo in ('P', 'PM') else R0.producao_de(i)
        nivel = float(np.dot(m['A'][lr], x0)) * (-1 if lado == 'min' else 1)
        linha = {'periodo': rotulo_periodo(i), 'mct_base': R0.mct_de(i), 'sombra': s, 'limite': limite, 'volume': nivel, 'pontos': []}
        for k, d in enumerate(grade):
            R = res[k]
            ex = None if R is None else R.mct_de(i) - R0.mct_de(i)
            novo = limite * (1 + d / 100.0)
            if lado == 'max':
                es = s * (novo - limite) if s > 1e-9 else 0.0
                valido = _dentro(novo, sp.get('max_de'), sp.get('max_ate')) if s > 1e-9 else (novo >= nivel - 1e-6)
            else:
                es = s * (limite - novo) if s > 1e-9 else 0.0
                valido = _dentro(novo, sp.get('min_de'), sp.get('min_ate')) if s > 1e-9 else (novo <= nivel + 1e-6)
            if not valido:
                fora[k] += 1
            estim[k] += es
            linha['pontos'].append([d, ex, es, bool(valido)])
        por_periodo.append(linha)
    pontos = [{'d': d, 'exato': None if sem_sol[k] else float(exato[k]), 'sem_solucao': int(sem_sol[k]),
               'estimativa': float(estim[k]), 'fora_faixa': int(fora[k]),
               'limite': lim_total * (1 + d / 100.0)} for k, d in enumerate(grade)]
    return {'grade': grade, 'pontos': pontos, 'mct_base': R0.mct, 'periodos': len(por_periodo), 'por_periodo': por_periodo,
            'limite_total': lim_total, 'periodos_total': periodos_total, 'modelo_integrado': True}
