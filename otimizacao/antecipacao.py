"""
🌟 NOVO (22/10/2026): PRODUÇÃO ANTECIPADA dentro do Otimizar.

O Otimizar resolve cada período sozinho (parameters.tasks.otimizar_cenario_celery). Quando TODOS os períodos terminam,
esta rotina resolve os períodos otimizados JUNTOS, permitindo produzir num período para vender num período à frente
(estoque), e -- se a margem de contribuição total aumentar -- o resultado com antecipação passa a ser o do cenário.

Parâmetros por PRODUTO (TbProdutos, por cenário):
  - pro_horizonte_estoque: quantos períodos, no máximo, o produto pode ficar em estoque entre a produção e a venda
    (0 = não antecipa);
  - pro_estoque_inicial: disponível para vender a partir do 1º período, sem usar capacidade;
  - pro_estoque_final: tem que sobrar EXATAMENTE esse estoque no fim do último período.

Modelo integrado:
  - o MESMO modelo de cada período (otimizacao.sensibilidade.montar_modelo_periodo -- as funções do banco do Otimizar)
    entra inteiro, período a período;
  - x(v, a, b) = volume da variável v (produto / mercado / fluxo) PRODUZIDO no período a e VENDIDO no período b, com
    0 <= b - a <= horizonte do produto. b = a é o que o Otimizar já faz;
  - restrições de PRODUÇÃO (equipamento gargalo E, conjunto de equipamentos CE, insumo IC) valem no período em que se
    produz; restrições de VENDA (produto P e produto/mercado PM, mínimos e máximos) valem no período em que se vende;
  - margem por unidade de x(v, a, b) = margem do Otimizar no período a (preço a - custo variável a)
                                       + (preço b - preço a) - (outbound b - outbound a)
                                       - WACC do período x custo de produção a x (b - a)
    (o WACC da Taxa de Desconto do cenário já é a taxa do período do tipo do cenário; custo de produção = custo
    variável - parcela outbound). Na contabilidade: o custo da venda antecipada é o custo de produção do período em
    que foi produzida, e o custo financeiro do estoque é DESPESA FINANCEIRA (gravado à parte, por período);
  - estoque inicial: vendido nos primeiros períodos (até o horizonte), sem usar capacidade, valorizado pelo custo de
    produção do 1º período; estoque final: produzido nos últimos períodos (até o horizonte) e mantido até o fim.
    Estoque final - estoque inicial não vendido = exatamente o informado.
Com b = a a margem é exatamente a do Otimizar: o resultado integrado nunca é pior que a soma dos períodos isolados.
"""
import time
from collections import defaultdict

import numpy as np

TIPOS_VENDA = ('P', 'PM')                  # valem no período em que se vende; o resto, no período em que se produz
TOL = 1e-6
GANHO_MINIMO = 1.0                         # moeda: abaixo disso não vale trocar o resultado do Otimizar


class AntecipacaoError(Exception):
    pass


def _fetch(sql, params):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def wacc_do_cenario(cid, ordens):
    """{dau_order: taxa do período (fração)} -- Taxa de Desconto (WACC) do cenário, já na taxa do período do tipo do
    cenário (Mensal = % ao mês, Trimestral = % ao trimestre, Anual = % ao ano). Sem registro: a média dos outros."""
    taxa = {r['dau_order']: _f(r['valor']) for r in _fetch(
        """select d.dau_order, d.dau_valor as valor from tabelas_tbtaxadescontodaugther d
           join tabelas_tbtaxadesconto t on t.id = d.mae_id
           where t.tbcenarios_id = %s and d.dau_order = any(%s)""", [cid, list(ordens)])}
    media = sum(taxa.values()) / len(taxa) if taxa else 0.0
    return {o: taxa.get(o, media) / 100.0 for o in ordens}


def parametros_produtos(cid):
    """{produto_id: {'codigo', 'horizonte', 'ei', 'ef'}} -- TbProdutos do cenário."""
    return {r['id']: {'codigo': r['codigo'], 'horizonte': max(0, int(r['h'] or 0)), 'ei': max(0.0, _f(r['ei'])),
                      'ef': max(0.0, _f(r['ef']))} for r in _fetch(
        """select id, pro_codigo as codigo, pro_horizonte_estoque as h, pro_estoque_inicial as ei, pro_estoque_final as ef
           from produtos_tbprodutos where tbcenarios_id = %s""", [cid])}


def _valores_variaveis(cid, ordens):
    """{(var_id, dau_order): (preço, custo variável, outbound, parcela itens, parcela inbound, parcela manutenção)} --
    TbProdutoMercadoFluxoDaugther."""
    return {(r['mae_id'], r['dau_order']): (_f(r['preco']), _f(r['custo']), _f(r['outbound']), _f(r['itens']), _f(r['inbound']),
                                            _f(r['manut'])) for r in _fetch(
        """select mae_id, dau_order, dau_valor_4 as preco, dau_valor_5 as custo, dau_valor_6 as outbound,
                  dau_valor_8 as itens, dau_valor_9 as inbound, dau_valor_10 as manut
           from otimizacao_tbprodutomercadofluxodaugther where tbcenarios_id = %s and dau_order = any(%s)""", [cid, list(ordens)])}


def _produto_das_variaveis(cid, ids_vars):
    m = {r['id']: r['p'] for r in _fetch("""select id, pro_mer_flu_produto_id as p from otimizacao_tbprodutomercadofluxo
                                            where tbcenarios_id = %s""", [cid])}
    return np.array([m.get(v) for v in ids_vars], dtype=object)


def _resolver_esparso(c, A, b, bounds):
    from scipy.optimize import linprog
    r = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs')
    if r.success:
        return r
    if r.status != 2:
        for metodo in ('highs-ds', 'highs-ipm'):
            r2 = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method=metodo)
            if r2.success:
                return r2
    return None


def ranging_esparso(c, A, b, bounds):
    """O mesmo de parameters.tasks.obter_ranging, com a matriz ESPARSA (o modelo integrado é grande demais para a densa)."""
    import highspy
    import scipy.sparse as sp
    A = sp.csr_matrix(A)
    n_var, n_con = A.shape[1], A.shape[0]
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    inf = highspy.kHighsInf
    lower = np.array([bd[0] if bd[0] is not None else -inf for bd in bounds], dtype=float)
    upper = np.array([bd[1] if bd[1] is not None else inf for bd in bounds], dtype=float)
    h.addVars(n_var, lower, upper)
    h.changeColsCost(n_var, np.arange(n_var, dtype=np.int32), np.array(c, dtype=float))
    h.addRows(n_con, np.full(n_con, -inf), np.array(b, dtype=float), A.nnz,
              A.indptr[:-1].astype(np.int32), A.indices.astype(np.int32), A.data.astype(float))
    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        return None
    _, rg = h.getRanging()
    return {"dn": np.array(rg.row_bound_dn.value_), "up": np.array(rg.row_bound_up.value_),
            "dn_ou_var": np.array(rg.row_bound_dn.ou_var_), "up_ou_var": np.array(rg.row_bound_up.ou_var_), "n_var": n_var}


def _mais_proximo_do_isolado(c, A, b, bounds, otimo, grupos, n, T, x_iso):
    """Entre as soluções com margem = ótimo (tolerância numérica), a que minimiza a soma dos desvios absolutos de vendas
    e produção (por variável e período) em relação ao resultado dos períodos isolados. None se não resolver."""
    import scipy.sparse as sp
    from scipy.optimize import linprog
    N = A.shape[1]
    linhas_v, linhas_p, cols = [], [], []
    for gi, (tipo, a, bb) in enumerate(grupos):
        base = gi * n
        for j in range(n):
            if tipo in ('x', 'ei'):
                linhas_v.append(bb * n + j)
                cols.append(('v', base + j))
            if tipo in ('x', 'ef'):
                linhas_p.append(a * n + j)
                cols.append(('p', base + j))
    rv = [k for k, (t, _c) in zip(range(len(cols)), cols) if t == 'v']
    S = sp.csr_matrix((np.ones(len(linhas_v)), (linhas_v, [cols[k][1] for k in rv])), shape=(T * n, N))
    rp = [k for k, (t, _c) in zip(range(len(cols)), cols) if t == 'p']
    P = sp.csr_matrix((np.ones(len(linhas_p)), (linhas_p, [cols[k][1] for k in rp])), shape=(T * n, N))
    alvo = x_iso.reshape(-1)
    M = T * n
    I = sp.identity(M, format='csr')
    Z = sp.csr_matrix((A.shape[0], M))
    tol = max(1e-6 * abs(otimo), 1e-3)
    A2 = sp.vstack([
        sp.hstack([A, Z, Z]),
        sp.hstack([sp.csr_matrix(np.asarray(c).reshape(1, -1)), sp.csr_matrix((1, M)), sp.csr_matrix((1, M))]),
        sp.hstack([S, -I, sp.csr_matrix((M, M))]),
        sp.hstack([-S, -I, sp.csr_matrix((M, M))]),
        sp.hstack([P, sp.csr_matrix((M, M)), -I]),
        sp.hstack([-P, sp.csr_matrix((M, M)), -I]),
    ], format='csr')
    b2 = np.concatenate([b, [otimo + tol], alvo, -alvo, alvo, -alvo])
    c2 = np.concatenate([np.zeros(N), np.ones(2 * M)])
    r2 = linprog(c2, A_ub=A2, b_ub=b2, bounds=list(bounds) + [(0, None)] * (2 * M), method='highs')
    if not r2.success:
        print(f"[antecipacao] 2º passo (mais próximo do isolado) sem solução: {r2.message}")
        return None
    return r2.x[:N]


def montar_integrado(cid, mods, prods, ctx=None):
    """🌟 Modelo integrado (todos os períodos juntos, com produção antecipada) a partir dos modelos de cada período
    (mods = [(dau_order, modelo)] -- os de montar_modelo_periodo, ou modificados por uma análise de sensibilidade: c, A,
    b, bounds e info de cada período). ctx: {'vals', 'wacc', 'produto_v'} já carregados (evita ir ao banco de novo).
    Devolve um dicionário com a matriz (A_g, b_g, c_g, bounds), os grupos de colunas e o mapa das linhas; None se nenhum
    produto pode antecipar nem tem estoque."""
    import scipy.sparse as sp
    ordens = [o for o, _m in mods]
    T = len(mods)
    ids_vars = [v for v, _f in mods[0][1]['vars']]
    n = len(ids_vars)
    ctx = ctx or {}
    vals = ctx['vals'] if 'vals' in ctx else _valores_variaveis(cid, ordens)
    wacc = ctx['wacc'] if 'wacc' in ctx else wacc_do_cenario(cid, ordens)
    produto_v = ctx['produto_v'] if 'produto_v' in ctx else _produto_das_variaveis(cid, ids_vars)
    # 🌟 29/10/2026: preço, custo, outbound e parcelas só dependem dos valores do banco (ctx) -- calculados uma vez e
    # guardados no ctx (a sensibilidade monta o modelo integrado 169 vezes com os mesmos valores)
    chave_vals = ('_vals_arrays', tuple(ordens), len(ids_vars), ids_vars[0] if ids_vars else None, ids_vars[-1] if ids_vars else None)
    if chave_vals in ctx:
        hz, preco, custo, outb, parc = ctx[chave_vals]
    else:
        hz = np.array([prods.get(p, {}).get('horizonte', 0) for p in produto_v])
        preco = np.array([[vals.get((v, o), (0, 0, 0))[0] for v in ids_vars] for o in ordens])
        custo = np.array([[vals.get((v, o), (0, 0, 0))[1] for v in ids_vars] for o in ordens])
        outb = np.array([[vals.get((v, o), (0, 0, 0))[2] for v in ids_vars] for o in ordens])
        # parcelas do custo variável (itens, inbound, manutenção) -- o Consolidar usa as do período da VENDA; a venda
        # antecipada tem as do período da PRODUÇÃO (ajuste gravado por produto e período da venda)
        parc = np.array([[[(tuple(vals.get((v, o), ())) + (0,) * 6)[3 + q] for v in ids_vars] for o in ordens] for q in range(3)])
        if ctx is not None and 'vals' in ctx:
            ctx[chave_vals] = (hz, preco, custo, outb, parc)
    custo_prod = np.maximum(custo - outb, 0.0)
    pode_produzir = np.array([[m['bounds'][j][1] is None or m['bounds'][j][1] > 0 for j in range(n)] for _o, m in mods])
    margem_oti = np.array([-m['c'] for _o, m in mods])
    Hmax = int(hz.max()) if n else 0

    ei = {p: v['ei'] for p, v in prods.items() if v['ei'] > 0 and (produto_v == p).any()}
    ef = {p: v['ef'] for p, v in prods.items() if v['ef'] > 0 and (produto_v == p).any()}
    sem_var = [prods[p]['codigo'] for p, v in prods.items() if (v['ei'] > 0 or v['ef'] > 0) and not (produto_v == p).any()]
    if Hmax == 0 and not ei and not ef:
        return None

    # ---- colunas (grupos de n variáveis) ----
    grupos = []          # ('x', a, b) produz em a e vende em b; ('ei', None, b) estoque inicial vendido em b; ('ef', a, None)
    for a in range(T):
        for b in range(a, T):
            if ordens[b] - ordens[a] > Hmax:
                break
            grupos.append(('x', a, b))
    if ei:
        for b in range(T):
            if ordens[b] - ordens[0] <= Hmax:
                grupos.append(('ei', None, b))
    prods_estoque = sorted(set(ei) | set(ef))
    if prods_estoque:
        for a in range(T):
            if ordens[-1] - ordens[a] <= Hmax:
                grupos.append(('ef', a, None))

    # ---- linhas: blocos de produção e de venda de cada período ----
    blocos, b_vec, mapa_linha = [], [], []
    for a, (o, m) in enumerate(mods):
        lp = [r for r, t in enumerate(m['info']) if t[0] not in TIPOS_VENDA]
        lv = [r for r, t in enumerate(m['info']) if t[0] in TIPOS_VENDA]
        for tb, rows in (('prod', lp), ('venda', lv)):
            if rows:
                blocos.append((tb, a, rows))
                b_vec.extend(m['b'][rows].tolist())
                mapa_linha.extend([('m', a, r) for r in rows])
    if not blocos:
        raise AntecipacaoError("O modelo não tem restrições.")

    # 🌟 29/10/2026: cada bloco (linhas de produção ou de venda de um período) vira matriz esparsa UMA vez -- antes era
    # convertido de novo para cada grupo de colunas que o usa; e, entre uma montagem e outra, a conversão é reaproveitada
    # enquanto a matriz do período for a mesma (o mesmo objeto)
    cache_csr = ctx.setdefault('_csr', {}) if ctx is not None and 'vals' in ctx else {}
    usados = set()

    def csr_de(a, tb, rows):
        A = mods[a][1]['A']
        k = (id(A), tb, a)
        usados.add(k)
        ent = cache_csr.get(k)
        if ent is None or ent[0] is not A:
            ent = cache_csr[k] = (A, sp.csr_matrix(A[rows]))
        return ent[1]

    def bloco(tb, a, rows, g):
        tipo, ga, gb = g
        if tb == 'prod' and tipo in ('x', 'ef') and ga == a:
            return csr_de(a, tb, rows)
        if tb == 'venda' and tipo in ('x', 'ei') and gb == a:
            return csr_de(a, tb, rows)
        return None

    grade = [[bloco(tb, a, rows, g) for g in grupos] for tb, a, rows in blocos]
    alturas = [len(rows) for _tb, _a, rows in blocos]
    # estoque inicial: vendido <= estoque; estoque final: (produzido p/ o fim) - (inicial vendido) = final - inicial
    for p in sorted(ei):
        mask = (produto_v == p).astype(float).reshape(1, -1)
        grade.append([sp.csr_matrix(mask) if g[0] == 'ei' else None for g in grupos])
        b_vec.append(ei[p])
        mapa_linha.append(('EI', p, None))
        alturas.append(1)
    for p in prods_estoque:
        mask = (produto_v == p).astype(float).reshape(1, -1)
        alvo = ef.get(p, 0.0) - ei.get(p, 0.0)
        for sinal in (1.0, -1.0):
            grade.append([sp.csr_matrix(sinal * mask) if g[0] == 'ef' else (sp.csr_matrix(-sinal * mask) if g[0] == 'ei' else None)
                          for g in grupos])
            b_vec.append(sinal * alvo)
            mapa_linha.append(('EF', p, sinal))
            alturas.append(1)
    for k in range(len(grupos)):
        if all(linha[k] is None for linha in grade):
            grade[0][k] = sp.csr_matrix((alturas[0], n))
    for i, linha in enumerate(grade):
        if all(x is None for x in linha):
            linha[0] = sp.csr_matrix((alturas[i], n))
    A_g = sp.bmat(grade, format='csr')
    for k in [k for k in cache_csr if k not in usados]:      # só fica o que esta montagem usou
        del cache_csr[k]
    b_g = np.array(b_vec, dtype=float)

    # ---- objetivo e limites ----
    c_g, bounds, custo_fin_unit = [], [], []
    for tipo, a, b in grupos:
        if tipo == 'x':
            k = ordens[b] - ordens[a]
            cf = wacc[ordens[a]] * custo_prod[a] * k
            mg = margem_oti[a] + (preco[b] - preco[a]) - (outb[b] - outb[a]) - cf if k else margem_oti[a].copy()
            ok = pode_produzir[a] & (hz >= k)
        elif tipo == 'ei':
            k = ordens[b] - ordens[0]
            cf = wacc[ordens[0]] * custo_prod[0] * k
            mg = margem_oti[b] + custo_prod[b] - custo_prod[0] - cf
            ok = np.array([produto_v[j] in ei for j in range(n)]) & (hz >= k)
        else:
            k = ordens[-1] - ordens[a]
            cf = wacc[ordens[a]] * custo_prod[a] * k
            mg = -custo_prod[a] - cf
            ok = pode_produzir[a] & np.array([produto_v[j] in prods_estoque for j in range(n)]) & (hz >= k)
        c_g.extend((-mg).tolist())
        custo_fin_unit.append(cf if k else np.zeros(n))
        bounds.extend([(0, None) if ok[j] else (0, 0) for j in range(n)])
    c_g = np.array(c_g)
    return {'vals': vals, 'wacc': wacc, 'produto_v': produto_v, 'hz': hz, 'preco': preco, 'custo': custo, 'outb': outb, 'custo_prod': custo_prod, 'parc': parc, 'pode_produzir': pode_produzir, 'margem_oti': margem_oti, 'Hmax': Hmax, 'ei': ei, 'ef': ef, 'sem_var': sem_var, 'grupos': grupos, 'prods_estoque': prods_estoque, 'blocos': blocos, 'b_vec': b_vec, 'mapa_linha': mapa_linha, 'A_g': A_g, 'b_g': b_g, 'c_g': c_g, 'bounds': bounds, 'custo_fin_unit': custo_fin_unit, 'ordens': ordens, 'T': T, 'n': n, 'ids_vars': ids_vars}


def otimizar_com_antecipacao(cid, ordens, rotulo_periodo=str, calcular_ranging=True):
    """Resolve os períodos otimizados juntos. Devolve um dicionário com o resultado (ver o fim da função); 'aplicado'
    diz se o resultado com antecipação é melhor que o do Otimizar (períodos isolados)."""
    import scipy.sparse as sp
    from otimizacao.sensibilidade import montar_modelo_periodo, _resolver
    t0 = time.time()
    ordens = sorted(int(o) for o in ordens)
    prods = parametros_produtos(cid)
    base = {'aplicado': False, 'ordens': ordens, 'produtos': prods, 'motivo': ''}
    if not any(p['horizonte'] > 0 or p['ei'] > 0 or p['ef'] > 0 for p in prods.values()):
        base['motivo'] = 'Nenhum produto com horizonte de estoque, estoque inicial ou estoque final.'
        return base
    if not ordens:
        base['motivo'] = 'Nenhum período otimizado.'
        return base

    mods = []
    for o in ordens:
        m = montar_modelo_periodo(cid, o)
        if m['vars']:
            mods.append((o, m))
    if not mods:
        base['motivo'] = 'Nenhum período com variáveis.'
        return base
    ordens = [o for o, _m in mods]
    T = len(mods)
    ids_vars = [v for v, _f in mods[0][1]['vars']]
    for o, m in mods:
        if [v for v, _f in m['vars']] != ids_vars:
            raise AntecipacaoError(f"As variáveis do período {rotulo_periodo(o)} não são as mesmas dos outros períodos.")
    n = len(ids_vars)

    iso = []
    for o, m in mods:
        r = _resolver(m['c'], m['A'], m['b'], m['bounds'])
        if r is None:
            raise AntecipacaoError(f"O período {rotulo_periodo(o)} não tem solução isolada.")
        iso.append(r)

    M = montar_integrado(cid, mods, prods)
    if M is None:
        base['motivo'] = 'Nenhum produto com horizonte de estoque maior que zero.'
        return base
    (vals, wacc, produto_v, hz, preco, custo, outb, custo_prod, parc, pode_produzir, margem_oti, Hmax, ei, ef, sem_var, grupos,
     prods_estoque, blocos, b_vec, mapa_linha, A_g, b_g, c_g, bounds, custo_fin_unit) = (M[k] for k in (
        'vals', 'wacc', 'produto_v', 'hz', 'preco', 'custo', 'outb', 'custo_prod', 'parc', 'pode_produzir', 'margem_oti', 'Hmax',
        'ei', 'ef', 'sem_var', 'grupos', 'prods_estoque', 'blocos', 'b_vec', 'mapa_linha', 'A_g', 'b_g', 'c_g', 'bounds',
        'custo_fin_unit'))

    r = _resolver_esparso(c_g, A_g, b_g, bounds)
    if r is None:
        base['motivo'] = ("Sem solução com antecipação (os estoques inicial/final pedidos não cabem na capacidade ou nos "
                          "limites de venda) -- mantido o resultado do Otimizar.")
        return base
    x = r.x
    # 🌟 22/10/2026: 2º passo -- com a MESMA margem máxima, a solução mais parecida com a do Otimizar (períodos isolados).
    # O modelo costuma ter várias soluções com a mesma margem (ex.: dois fluxos com a mesma margem por hora no gargalo);
    # sem este passo, produtos que não ganham nada com a antecipação podiam mudar de fluxo/mercado só por empate.
    x2 = _mais_proximo_do_isolado(c_g, A_g, b_g, bounds, float(r.fun), grupos, n, T, np.array([ri.x for ri in iso]))
    if x2 is not None:
        x = x2

    # ---- vendas e produção por período e variável ----
    col = {}
    pos = 0
    for g in grupos:
        col[g] = slice(pos, pos + n)
        pos += n
    vendas = np.zeros((T, n))
    producao = np.zeros((T, n))
    custo_fin = np.zeros((T, n))      # despesa financeira do estoque, no período em que o produto está parado
    margem_int_prod = defaultdict(float)
    # por produto e período: margem de contribuição (contábil, SEM o custo financeiro -- que é despesa financeira) no
    # período da venda (estoque final: no período da produção) e o custo financeiro no período em que o produto está parado
    margem_int_pp = defaultdict(lambda: np.zeros(T))
    custo_fin_pp = defaultdict(lambda: np.zeros(T))
    antecipacoes = []
    for gi, g in enumerate(grupos):
        tipo, a, b = g
        xs = x[col[g]]
        mg = -c_g[col[g]]
        if tipo in ('x', 'ei'):
            vendas[b] += xs
        if tipo in ('x', 'ef'):
            producao[a] += xs
        ini = a if a is not None else 0
        fim = b if b is not None else T
        for j in np.nonzero(xs > TOL)[0]:
            cfu = float(custo_fin_unit[gi][j]) if np.ndim(custo_fin_unit[gi]) else 0.0
            margem_int_prod[produto_v[j]] += float((mg[j] + cfu) * xs[j])
            margem_int_pp[produto_v[j]][b if b is not None else a] += float((mg[j] + cfu) * xs[j])
            # despesa financeira distribuída pelos períodos em estoque (do período da produção até o anterior à venda)
            per = list(range(ini, min(fim, T)))
            if per and cfu > 0:
                for t in per:
                    custo_fin[t, j] += cfu * xs[j] / len(per)
                    custo_fin_pp[produto_v[j]][t] += cfu * xs[j] / len(per)
            if tipo == 'x' and b == a:
                continue
            antecipacoes.append({'tipo': {'x': 'antecipada', 'ei': 'estoque_inicial', 'ef': 'estoque_final'}[tipo],
                                 'var': ids_vars[j], 'produto_id': produto_v[j],
                                 'produzido_em': ordens[a] if a is not None else None,
                                 'vendido_em': ordens[b] if b is not None else None,
                                 'volume': float(xs[j]), 'margem_unit': float(mg[j]), 'custo_estoque_unit': cfu})
    mct_int = -float(r.fun)
    mct_iso = sum(-float(ri.fun) for ri in iso)
    margem_iso_prod = defaultdict(float)
    margem_iso_pp = defaultdict(lambda: np.zeros(T))
    for a in range(T):
        for j in np.nonzero(iso[a].x > TOL)[0]:
            margem_iso_prod[produto_v[j]] += float(margem_oti[a, j] * iso[a].x[j])
            margem_iso_pp[produto_v[j]][a] += float(margem_oti[a, j] * iso[a].x[j])

    # 🌟 Consolidar: ajuste das parcelas do custo (itens, inbound, manutenção) das vendas antecipadas, no período da venda
    # (custo do período da produção - custo do período da venda) e o VALOR do estoque no fim de cada período (pelo custo
    # das parcelas do período em que foi produzido; estoque inicial pelo do 1º período)
    ajuste_custo = defaultdict(lambda: np.zeros((3, T)))
    valor_estoque = defaultdict(lambda: np.zeros(T))
    for g in grupos:
        tipo, a, b = g
        if tipo == 'x' and b == a:
            continue
        xs = x[col[g]]
        ap = a if a is not None else 0
        for j in np.nonzero(xs > TOL)[0]:
            unit = parc[:, ap, j]
            if b is not None:
                ajuste_custo[produto_v[j]][:, b] += xs[j] * (unit - parc[:, b, j])
            if tipo == 'ei':
                continue      # estoque inicial: o valor entra abaixo (o que ainda não foi vendido em cada período)
            fim = b if b is not None else T
            for t in range(a, fim):
                valor_estoque[produto_v[j]][t] += xs[j] * float(unit.sum())
    for p in ei:      # estoque inicial ainda não vendido (o vendido já entrou acima, até o período da venda)
        vend = np.zeros(T)
        un = 0.0
        for g in grupos:
            if g[0] == 'ei':
                xs = x[col[g]]
                vend[g[2]] += float(xs[produto_v == p].sum())
        js = np.nonzero(produto_v == p)[0]
        if len(js):
            un = float(parc[:, 0, js[0]].sum())
        valor_estoque[p] = valor_estoque[p] + np.maximum(ei[p] - np.cumsum(vend), 0.0) * un

    # estoque de cada produto no fim de cada período (antecipado em trânsito + inicial ainda não vendido + final)
    estoque = defaultdict(lambda: np.zeros(T))
    for g in grupos:
        tipo, a, b = g
        if tipo == 'ei' or (tipo == 'x' and b == a):
            continue
        xs = x[col[g]]
        fim = b if b is not None else T
        for j in np.nonzero(xs > TOL)[0]:
            for t in range(a, fim):
                estoque[produto_v[j]][t] += xs[j]
    for p in ei:
        vend = np.zeros(T)
        for g in grupos:
            if g[0] == 'ei':
                vend[g[2]] += float(x[col[g]][produto_v == p].sum())
        estoque[p] = estoque[p] + np.maximum(ei[p] - np.cumsum(vend), 0.0)

    ganho = mct_int - mct_iso
    # com estoque inicial ou final informado, o resultado do Otimizar (períodos isolados) não os respeita -- vale o integrado
    obrigatorio = bool(ei or ef)
    aplicado = obrigatorio or ganho > max(GANHO_MINIMO, 1e-7 * abs(mct_iso))

    # ---- preços sombra e faixas por período (do modelo integrado), na ordem das linhas do modelo de cada período ----
    residuo = b_g - A_g @ x          # da solução final (os preços sombra do 1º passo valem para qualquer solução ótima)
    marg = [np.zeros(len(m['info'])) for _o, m in mods]
    resid = [np.zeros(len(m['info'])) for _o, m in mods]
    for i, mp in enumerate(mapa_linha):
        if mp[0] == 'm':
            marg[mp[1]][mp[2]] = r.ineqlin.marginals[i]
            resid[mp[1]][mp[2]] = residuo[i]
    ranging_p = [None] * T
    if aplicado and calcular_ranging:
        try:
            rg = ranging_esparso(c_g, A_g, b_g, bounds)
        except Exception as erro:
            print(f"[antecipacao] ranging falhou: {erro!r}")
            rg = None
        if rg is not None:
            ncol = A_g.shape[1]

            def local(idx, a):
                if idx is None or idx < 0:
                    return -1
                if idx < ncol:
                    return int(idx % n)
                mp = mapa_linha[idx - ncol] if idx - ncol < len(mapa_linha) else None
                return n + mp[2] if mp and mp[0] == 'm' and mp[1] == a else -1
            for a, (_o, m) in enumerate(mods):
                R = len(m['info'])
                d = {'dn': np.zeros(R), 'up': np.zeros(R), 'dn_ou_var': np.full(R, -1), 'up_ou_var': np.full(R, -1), 'n_var': n}
                ranging_p[a] = d
            for i, mp in enumerate(mapa_linha):
                if mp[0] != 'm':
                    continue
                a, rr = mp[1], mp[2]
                ranging_p[a]['dn'][rr] = rg['dn'][i]
                ranging_p[a]['up'][rr] = rg['up'][i]
                ranging_p[a]['dn_ou_var'][rr] = local(int(rg['dn_ou_var'][i]), a)
                ranging_p[a]['up_ou_var'][rr] = local(int(rg['up_ou_var'][i]), a)

    return {
        'aplicado': aplicado, 'obrigatorio': obrigatorio,
        'motivo': ('Estoque inicial/final informado: vale o resultado com estoques.' if obrigatorio and ganho <= max(GANHO_MINIMO, 1e-7 * abs(mct_iso))
                   else ('' if aplicado else 'A antecipação não aumenta a margem -- mantido o resultado do Otimizar.')),
        'ordens': ordens, 'produtos': prods, 'ids_vars': ids_vars, 'produto_v': produto_v, 'mods': mods,
        'mct_isolado': mct_iso, 'mct_integrado': mct_int, 'ganho': ganho,
        'vendas': vendas, 'producao': producao, 'custo_financeiro': custo_fin,
        'vendas_iso': np.array([ri.x for ri in iso]),
        'margem_iso_produto': dict(margem_iso_prod), 'margem_int_produto': dict(margem_int_prod),
        'margem_iso_pp': dict(margem_iso_pp), 'margem_int_pp': dict(margem_int_pp), 'custo_fin_pp': dict(custo_fin_pp),
        'estoque': {p: v for p, v in estoque.items()}, 'antecipacoes': antecipacoes,
        'ajuste_custo': dict(ajuste_custo), 'valor_estoque': dict(valor_estoque),
        'marginals': marg, 'residual': resid, 'ranging': ranging_p, 'wacc': wacc,
        'produtos_sem_variavel': sem_var, 'segundos': round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------------------------------------------------------
# 🌟 NOVO (22/10/2026): o CENÁRIO FINAL para as análises (sensibilidades, ocupação mínima). Quando o Otimizar aplicou a
# produção antecipada (TbOtimizacaoAntecipacao.oti_ant_aplicado), as análises têm que resolver o modelo INTEGRADO (todos
# os períodos juntos, com estoque) -- não cada período sozinho. CenarioFinal guarda os modelos de cada período e resolve
# o integrado com as alterações que a análise pedir (c, A, b, bounds, info de um ou mais períodos).
# ---------------------------------------------------------------------------------------------------------------
def antecipacao_aplicada(cid):
    try:
        r = _fetch("""select count(*) as n from otimizacao_tbotimizacaoantecipacao
                      where tbcenarios_id = %s and oti_ant_aplicado = true""", [cid])
        return bool(r and r[0]['n'])
    except Exception:
        return False        # tabela ainda não migrada


class ResultadoFinal:
    def __init__(self, M, r):
        self.M, self.fun, self.x = M, float(r.fun), r.x
        T, n, grupos = M['T'], M['n'], M['grupos']
        self.vendas = np.zeros((T, n))
        self.producao = np.zeros((T, n))
        self.mct_periodo = np.zeros(T)        # margem atribuída ao período da venda (estoque final: ao da produção)
        for gi, (tipo, a, b) in enumerate(grupos):
            xs = self.x[gi * n:(gi + 1) * n]
            cs = M['c_g'][gi * n:(gi + 1) * n]
            if tipo in ('x', 'ei'):
                self.vendas[b] += xs
                self.mct_periodo[b] += -float(np.dot(cs, xs))
            else:
                self.mct_periodo[a] += -float(np.dot(cs, xs))
            if tipo in ('x', 'ef'):
                self.producao[a] += xs
        self.folgas = M['b_g'] - M['A_g'] @ self.x
        self._linha = {(mp[1], mp[2]): i for i, mp in enumerate(M['mapa_linha']) if mp[0] == 'm'}
        self.idx = {o: a for a, o in enumerate(M['ordens'])}

    @property
    def mct(self):
        return -self.fun

    def folga(self, o, r):
        """b - A.x da linha r do modelo do período o (None se a linha não está no modelo)."""
        i = self._linha.get((self.idx[o], r))
        return None if i is None else float(self.folgas[i])

    def producao_de(self, o):
        return self.producao[self.idx[o]]

    def vendas_de(self, o):
        return self.vendas[self.idx[o]]

    def mct_de(self, o):
        return float(self.mct_periodo[self.idx[o]])


class CenarioFinal:
    def __init__(self, cid, ordens):
        self.cid = cid
        self.tempos = {}        # 🌟 29/10/2026: medição (montar_integrado / resolver)
        self._inc = None
        self.ativo = antecipacao_aplicada(cid)
        self.mods, self.ordens = [], []
        if not self.ativo:
            return
        from otimizacao.sensibilidade import montar_modelo_periodo
        t = time.time()
        for o in sorted(int(x) for x in ordens):
            m = montar_modelo_periodo(cid, o)
            if m['vars']:
                self.mods.append((o, m))
        self.tempos['preparar_modelos_periodos'] = time.time() - t
        self.ordens = [o for o, _m in self.mods]
        if not self.mods:
            self.ativo = False
            return
        t = time.time()
        self.prods = parametros_produtos(cid)
        ids_vars = [v for v, _f in self.mods[0][1]['vars']]
        self.ctx = {'vals': _valores_variaveis(cid, self.ordens), 'wacc': wacc_do_cenario(cid, self.ordens),
                    'produto_v': _produto_das_variaveis(cid, ids_vars)}
        self.tempos['preparar_valores'] = time.time() - t

    def modelo(self, o):
        return dict(self.mods)[o]

    def resolver(self, alteracoes=None, incremental=False):
        """alteracoes: {dau_order: {'c':..., 'A':..., 'b':..., 'bounds':..., 'info':...}} -- o que muda no modelo do
        período. Devolve ResultadoFinal ou None (sem solução).
        🌟 29/10/2026: incremental=True (análises de sensibilidade, que resolvem o MESMO modelo muitas vezes mudando só
        alguns custos e coeficientes): o modelo fica montado no HiGHS e, a cada chamada, só o que mudou é trocado e a
        otimização parte da solução anterior (warm start) -- o mesmo ótimo, muito mais rápido. Se o HiGHS não chegar ao
        ótimo por outro motivo que não 'sem solução', resolve do zero como antes."""
        alteracoes = alteracoes or {}
        mods2 = [(o, {**m, **alteracoes.get(o, {})}) for o, m in self.mods]
        t = time.time()
        M = montar_integrado(self.cid, mods2, self.prods, self.ctx)
        self.tempos['montar_integrado'] = self.tempos.get('montar_integrado', 0.0) + time.time() - t
        if M is None:
            return None
        t = time.time()
        r = None
        if incremental:
            if self._inc is None:
                self._inc = _SolverIncremental()
            r = self._inc.resolver(M['c_g'], M['A_g'], M['b_g'], M['bounds'])
            if r == 'sem_solucao':
                self.tempos['resolver'] = self.tempos.get('resolver', 0.0) + time.time() - t
                return None
        if r is None:
            r = _resolver_esparso(M['c_g'], M['A_g'], M['b_g'], M['bounds'])
            if incremental:
                self.tempos['resolver_do_zero'] = self.tempos.get('resolver_do_zero', 0) + 1
        self.tempos['resolver'] = self.tempos.get('resolver', 0.0) + time.time() - t
        return None if r is None else ResultadoFinal(M, r)


class _SolverIncremental:
    """🌟 NOVO (29/10/2026): o modelo integrado mantido no HiGHS (highspy) entre uma otimização e a próxima. A cada
    chamada compara com o modelo anterior e troca só os custos (c) e os coeficientes (A) que mudaram -- e os limites das
    linhas (b), se mudarem; se o tamanho ou os limites das variáveis mudarem, monta de novo. Devolve um objeto com .x e
    .fun (como o linprog), 'sem_solucao', ou None (não chegou ao ótimo: quem chama resolve do zero)."""

    def __init__(self):
        self.h = None

    def _montar(self, c, A, b, bounds):
        import highspy
        self.h = h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        inf = highspy.kHighsInf
        lower = np.array([bd[0] if bd[0] is not None else -inf for bd in bounds], dtype=float)
        upper = np.array([bd[1] if bd[1] is not None else inf for bd in bounds], dtype=float)
        n_var, n_con = A.shape[1], A.shape[0]
        h.addVars(n_var, lower, upper)
        h.changeColsCost(n_var, np.arange(n_var, dtype=np.int32), np.array(c, dtype=float))
        h.addRows(n_con, np.full(n_con, -inf), np.array(b, dtype=float), A.nnz,
                  A.indptr[:-1].astype(np.int32), A.indices.astype(np.int32), A.data.astype(float))
        self.c, self.A, self.b, self.bounds = np.array(c, dtype=float), A.copy(), np.array(b, dtype=float), list(bounds)

    def _atualizar(self, c, A, b):
        import highspy
        h = self.h
        c = np.array(c, dtype=float)
        mud = np.nonzero(c != self.c)[0]
        if len(mud):
            h.changeColsCost(len(mud), mud.astype(np.int32), c[mud])
        D = (A - self.A).tocoo()
        if D.nnz:
            # valor NOVO em cada posição que mudou (0 se a posição deixou de existir)
            ncol = A.shape[1]
            chave = D.row.astype(np.int64) * ncol + D.col
            An = A.tocoo()
            chave_n = An.row.astype(np.int64) * ncol + An.col
            ordem = np.argsort(chave_n)
            chave_n, val_n = chave_n[ordem], An.data[ordem]
            pos = np.searchsorted(chave_n, chave)
            pos_ok = np.minimum(pos, max(len(chave_n) - 1, 0))
            novo = np.where((pos < len(chave_n)) & (chave_n[pos_ok] == chave), val_n[pos_ok] if len(val_n) else 0.0, 0.0)
            for r_, c_, v_ in zip(D.row.tolist(), D.col.tolist(), novo.tolist()):
                h.changeCoeff(r_, c_, v_)
        b = np.array(b, dtype=float)
        mud_b = np.nonzero(b != self.b)[0]
        if len(mud_b):
            h.changeRowsBounds(len(mud_b), mud_b.astype(np.int32), np.full(len(mud_b), -highspy.kHighsInf), b[mud_b])
        self.c, self.A, self.b = c, A.copy(), b

    def resolver(self, c, A, b, bounds):
        import highspy
        from types import SimpleNamespace
        try:
            if self.h is None or A.shape != self.A.shape or list(bounds) != self.bounds:
                self._montar(c, A, b, bounds)
            else:
                self._atualizar(c, A, b)
            self.h.run()
            st = self.h.getModelStatus()
            if st == highspy.HighsModelStatus.kOptimal:
                return SimpleNamespace(x=np.array(self.h.getSolution().col_value, dtype=float),
                                       fun=float(self.h.getInfo().objective_function_value))
            if st == highspy.HighsModelStatus.kInfeasible:
                return 'sem_solucao'
            self.h = None           # estado duvidoso: a próxima monta do zero
            return None
        except Exception as erro:
            print(f"[antecipacao] HiGHS incremental falhou ({type(erro).__name__}: {erro}); resolvendo do zero", flush=True)
            self.h = None
            return None
