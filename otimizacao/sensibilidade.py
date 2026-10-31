"""
🌟 NOVO (18/10/2026): "Cenário - Sensibilidade Custo x Produtividade".

Pergunta respondida: se eu mudar o CUSTO VARIÁVEL de um equipamento (ou só nos fluxos escolhidos) em Δc% e isso mudar a
PRODUTIVIDADE dele em Δp%, quanto muda a MARGEM DE CONTRIBUIÇÃO TOTAL (MCT) do cenário? Ex.: reduzir o custo em 15%
perdendo 10% de produtividade (vale a pena deixar o equipamento ocioso?) ou aumentar o custo para ganhar produtividade
num gargalo que ainda tem mercado.

Como: o MESMO modelo do Otimizar (parameters.tasks.otimizar_cenario_celery -- mesmas funções do banco: oti_funcao_objetivo
e oti_restricao_*_a_ub / _b_ub, mesmos limites das variáveis) é montado UMA vez por período e resolvido de novo (HiGHS)
para cada ponto de uma grade Δcusto x Δprodutividade:
  - custo (🌟 18/10/2026): a margem por unidade de cada variável (produto / mercado / fluxo) que passa pelo equipamento
    perde Δc x (custo variável ACUMULADO ATÉ A SAÍDA do equipamento (inclusive o adicionado nele), por unidade do produto final): o custo adicionado
    pelo próprio equipamento MAIS o de todos os equipamentos upstream que mandam material para ele no fluxo. É a mesma
    conta da procedure atualiza_custo_var_fluxo_producao_order_coluna_equipamento (custo do WIP no consolida_resultados):
    sobe pelo I/O do fluxo (TbFluxoProducaoInputOutput: coluna e "envia para") e soma, de cada linha da cadeia,
    atualiza_custo_var_adic_equipamento_order x output real (dau_valor_4 de TbFluxoProducaoInputOutputDaugther).
    Assim a redução de custo pode vir do próprio equipamento ou do custo do que chega nele;
  - produtividade: as horas por unidade do equipamento (output real / (produtividade x (1 - % paradas não programadas)))
    ficam divididas por (1 + Δp). O equipamento escolhido SEMPRE ganha a linha de capacidade (loading time x ocupação
    mín/máx), mesmo que não esteja marcado como gargalo: perdendo produtividade, ele pode virar gargalo.
  - as demais restrições do modelo ficam como no Otimizar em todos os pontos: outros equipamentos GARGALO (horas),
    CONJUNTOS DE EQUIPAMENTOS (limite de PRODUÇÃO -- soma do output real dos equipamentos do conjunto, que não muda com a
    produtividade do equipamento analisado), limites de produto, de produto/mercado e de insumo. Equipamento que não é
    gargalo não tem linha (como no Otimizar). 🌟 22/10/2026: para cada combinação, a tela mostra QUAIS dessas restrições
    ficaram no limite (ex.: um conjunto de equipamentos no máximo -- ganhar produtividade no equipamento analisado não traz ganho);
  - o custo FIXO não entra (a MCT não o enxerga); a tela tem um campo para somar uma variação de custo fixo e ver o EBITDA.
Combinação SEM solução (os limites mínimos do modelo -- venda mínima, produção mínima, ocupação mínima -- não cabem
na nova capacidade): NÃO entra como margem zero. O ponto fica marcado como inviável e, para explicar, o modelo é resolvido
de novo com folga só nos mínimos: (1) acha o menor descumprimento possível dos mínimos (relativo a cada mínimo) e
(2) com esse descumprimento, a maior MCT. Vão para a tela os mínimos que não cabem (quanto falta) e a MCT "descumprindo".
Cada ponto é um ótimo de verdade (o mix pode mudar: deixar de vender produto de margem negativa, outro equipamento virar
gargalo, um limite de mercado passar a segurar) -- não é a aproximação do preço sombra.
"""
import time
from collections import defaultdict

import numpy as np

GRADE_PADRAO = [-30, -25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25, 30]     # % (custo e produtividade)


def _cur():
    from django.db import connection
    return connection.cursor()


def _linhas_a(cur, funcao, i, cid):
    cur.execute(f"select * from public.{funcao}(%s, %s)", [i, cid])
    return [[float(v or 0) for v in r[0]] for r in cur.fetchall()]


def _limites_b(cur, funcao, i, cid):
    """b_ub vem em trios: id da restrição, mínimo, máximo (mesma leitura de otimizar_cenario_celery)."""
    cur.execute(f"select * from public.{funcao}(%s, %s)", [i, cid])
    vals = [r[0] for r in cur.fetchall()]
    ids, b = [], []
    for k in range(0, len(vals) - 2, 3):
        ids.append(int(vals[k]))
        b.extend([float(vals[k + 1] or 0), float(vals[k + 2] or 0)])
    return ids, b


def montar_modelo_periodo(cid, i):
    """Modelo do período i (dau_order) igual ao do Otimizar. Devolve dict com c, A, b, bounds, variáveis e linhas 'E'."""
    from django.db import connection
    cur = connection.cursor()
    try:
        cur.execute("select * from oti_funcao_objetivo(%s, %s)", [i, cid])
        c = [float(r[0] or 0) for r in cur.fetchall()]
        cur.execute("""select o.id, o.pro_mer_flu_fluxo_producao_id, coalesce(d.dau_valor_7, false), o.pro_mer_flu_produto_id
                       from otimizacao_tbprodutomercadofluxo o
                            left join otimizacao_tbprodutomercadofluxodaugther d on d.mae_id = o.id and d.dau_order = %s
                       where o.tbcenarios_id = %s and o.flag = true order by o.pro_mer_flu_variavel""", [i, cid])
        vars_ = cur.fetchall()
        bounds = [(0, None) if v[2] else (0, 0) for v in vars_]
        A, b, linhas_e, info = [], [], {}, []
        for tipo, fa, fb in (('E', 'oti_restricao_equipamento_a_ub', 'oti_restricao_equipamento_b_ub'),
                             ('CE', 'oti_restricao_conjunto_equipamento_a_ub', 'oti_restricao_conjunto_equipamento_b_ub'),
                             ('P', 'oti_restricao_produto_a_ub', 'oti_restricao_produto_b_ub'),
                             ('PM', 'oti_restricao_produto_mercado_a_ub', 'oti_restricao_produto_mercado_b_ub'),
                             ('IC', 'oti_restricao_custo_item_a_ub', 'oti_restricao_custo_item_b_ub')):
            linhas = _linhas_a(cur, fa, i, cid)
            ids, lim = _limites_b(cur, fb, i, cid)
            if tipo == 'E':
                for k, rid in enumerate(ids):
                    linhas_e[rid] = len(A) + 2 * k        # linha do mínimo; a do máximo é a seguinte
            # linha 2k = mínimo (-coef <= -mín), 2k+1 = máximo, na ordem dos ids do b_ub (a mesma leitura do Otimizar)
            for k in range(len(linhas)):
                info.append((tipo, ids[k // 2] if k // 2 < len(ids) else None, 'min' if k % 2 == 0 else 'max'))
            A.extend(linhas)
            b.extend(lim)
    finally:
        cur.close()
    return {'c': np.array(c, dtype=float), 'A': np.array(A, dtype=float) if A else np.zeros((0, len(c))),
            'b': np.array(b, dtype=float), 'bounds': bounds, 'vars': [(v[0], v[1]) for v in vars_], 'linhas_e': linhas_e,
            'info': info, 'produto_var': [v[3] for v in vars_]}


def _cadeia_ate_equipamento(linhas_fluxo, inicio):
    """Linhas do I/O de UM fluxo que estão na cadeia de produção das linhas 'inicio' (inclusive) -- a mesma regra da
    procedure atualiza_custo_var_fluxo_producao_order_coluna_equipamento: andando da coluna mais alta para a mais baixa,
    uma linha da coluna c entra se ela 'envia para' um equipamento que já está na cadeia na coluna c + 1.
    linhas_fluxo: {io_id: (coluna, equipamento_ordem_id, envia_para_id)}. inicio: ids de io (do equipamento escolhido)."""
    na_cadeia = set(inicio)
    por_coluna = {(linhas_fluxo[k][0], linhas_fluxo[k][1]) for k in inicio}
    col_max = max(linhas_fluxo[k][0] for k in inicio)
    for io_id, (col, eq, envia) in sorted(linhas_fluxo.items(), key=lambda t: -t[1][0]):
        if col >= col_max or io_id in na_cadeia:
            continue
        if envia is not None and (col + 1, envia) in por_coluna:
            na_cadeia.add(io_id)
            por_coluna.add((col, eq))
    return na_cadeia


def dados_equipamento_periodo(cid, i, oti_equip_id, fluxos):
    """Do equipamento escolhido (TbOtimizacaoEquipamentos), no período i, por FLUXO (só os fluxos escolhidos):
      - horas por unidade do produto final (output real / (produtividade x (1 - % paradas não programadas)));
      - custo: custo variável ACUMULADO até a SAÍDA do equipamento (inclusive o adicionado nele), por unidade do produto final (ver docstring do
        módulo) -- e, à parte, só o adicionado pelo próprio equipamento (informativo);
    e a capacidade (loading x ocupação)."""
    from django.db import connection
    cur = connection.cursor()
    try:
        cur.execute("select oti_equ_equipamento_id from otimizacao_tbotimizacaoequipamentos where id = %s", [oti_equip_id])
        cad = cur.fetchone()[0]
        cur.execute("""select fpio.id, fpio.mae_id, fpio.flu_pro_inp_out_equipamento_id, fpiod.dau_valor_4, ed.dau_valor_2, ed.dau_valor_1
                       from fluxos_tbfluxoproducaoinputoutput fpio
                            join equipamentos_tbequipamentos eq on eq.id = fpio.flu_pro_inp_out_equipamento_id
                            join fluxos_tbfluxoproducaoinputoutputdaugther fpiod on fpiod.mae_id = fpio.id and fpiod.dau_order = %s
                            join equipamentos_tbequipamentosdaugther ed on ed.mae_id = fpio.flu_pro_inp_out_equipamento_id
                                 and ed.dau_order = %s and ed.tbcenarios_id = %s
                       where fpio.flag = true and eq.equ_codigo_id = %s and fpio.tbcenarios_id = %s""", [i, i, cid, cad, cid])
        horas, custo, custo_proprio = defaultdict(float), defaultdict(float), defaultdict(float)
        cache_custo = {}

        def custo_adic(eq_ordem):
            if eq_ordem not in cache_custo:
                cur.execute("call public.atualiza_custo_var_adic_equipamento_order(%s, %s, %s, 0)", [cid, eq_ordem, i])
                r = cur.fetchone()
                cache_custo[eq_ordem] = float((r or [0])[0] or 0)
            return cache_custo[eq_ordem]

        linhas_eq = defaultdict(list)        # fluxo -> io ids do equipamento escolhido
        for io_id, fluxo, eq_ordem, out_real, prod, paradas in cur.fetchall():
            if fluxos is not None and fluxo not in fluxos:
                continue
            out_real = float(out_real or 0)
            prod, paradas = float(prod or 0), float(paradas or 0)
            if prod > 0 and paradas < 100:
                horas[fluxo] += out_real / (prod * (1 - paradas / 100.0))
            custo_proprio[fluxo] += custo_adic(eq_ordem) * out_real
            linhas_eq[fluxo].append(io_id)
        # custo acumulado até o equipamento (inclusive): cadeia upstream no I/O de cada fluxo
        for fluxo, inicio in linhas_eq.items():
            cur.execute("""select fpio.id, fpio.flu_pro_inp_out_coluna, fpio.flu_pro_inp_out_equipamento_id,
                                  fpio.flu_pro_inp_out_envia_para_id, coalesce(fpiod.dau_valor_4, 0)
                           from fluxos_tbfluxoproducaoinputoutput fpio
                                left join fluxos_tbfluxoproducaoinputoutputdaugther fpiod on fpiod.mae_id = fpio.id and fpiod.dau_order = %s
                           where fpio.mae_id = %s""", [i, fluxo])
            linhas_fluxo, saida = {}, {}
            for io_id, col, eq_ordem, envia, out_real in cur.fetchall():
                linhas_fluxo[io_id] = (int(col or 0), eq_ordem, envia)
                saida[io_id] = float(out_real or 0)
            inicio = [k for k in inicio if k in linhas_fluxo]
            if not inicio:
                continue
            for io_id in _cadeia_ate_equipamento(linhas_fluxo, inicio):
                custo[fluxo] += custo_adic(linhas_fluxo[io_id][1]) * saida.get(io_id, 0.0)
        cur.execute("""select d.dau_valor_3, d.dau_valor_5, d.dau_valor_7,
                              coalesce((select ecd.dau_valor_1 from equipamentos_tbequipamentoscadastrodaugther ecd
                                        where ecd.mae_id = %s and ecd.dau_order = %s and ecd.tbcenarios_id = %s), true)
                       from otimizacao_tbotimizacaoequipamentosdaugther d where d.mae_id = %s and d.dau_order = %s""",
                    [cad, i, cid, oti_equip_id, i])
        r = cur.fetchone() or (0, 0, 100, True)
        loading, omin, omax, running = float(r[0] or 0), float(r[1] or 0), float(r[2] or 100), bool(r[3])
    finally:
        cur.close()
    return {'horas': horas, 'custo': custo, 'custo_proprio': custo_proprio, 'loading': loading,
            'cap_min': loading * omin / 100.0 if running else 0.0, 'cap_max': loading * omax / 100.0 if running else 0.0}


# 🌟 NOVO (29/10/2026): medição do tempo (só registra; não muda nenhuma conta) -- onde a sensibilidade gasta o tempo:
# montar o modelo / ler o equipamento (banco) x resolver as otimizações (HiGHS). Vai para o log do Celery e para o
# resultado ('tempos').
_TEMPOS = defaultdict(float)
_CONTAGEM = defaultdict(int)


def _medir(chave, t_inicio):
    _TEMPOS[chave] += time.time() - t_inicio
    _CONTAGEM[chave] += 1


def _log(msg):
    print(f"[sensibilidade] {msg}", flush=True)


def _resolver(c, A, b, bounds):
    """HiGHS como no Otimizar. Se falhar por outro motivo que não 'inviável' (status 2) -- limite de iterações, dificuldade
    numérica --, tenta de novo com o simplex dual e com pontos interiores antes de desistir."""
    from scipy.optimize import linprog
    kw = {'bounds': bounds} if A.shape[0] == 0 else {'A_ub': A, 'b_ub': b, 'bounds': bounds}
    t = time.time()
    r = linprog(c, method='highs', **kw)
    _medir('otimizacoes', t)
    if r.success:
        return r
    if r.status != 2:
        for metodo in ('highs-ds', 'highs-ipm'):
            t = time.time()
            r2 = linprog(c, method=metodo, **kw)
            _medir('otimizacoes_repetidas', t)
            if r2.success:
                return r2
    return None


def _resolver_descumprindo(c, A, b, bounds, linhas_min):
    """Modelo sem solução: folga s >= 0 só nas linhas de MÍNIMO (-coef.x - s <= -mín).
    1) menor descumprimento: min soma(s_r / max(mín_r, 1)) -- relativo, para não misturar horas com toneladas;
    2) com esse descumprimento (tolerância numérica), a maior MCT.
    Devolve (resultado com x só das variáveis originais, {linha: falta}) ou (None, {})."""
    from scipy.optimize import linprog
    n, m = A.shape[1], len(linhas_min)
    if m == 0:
        return None, {}
    t_desc = time.time()
    try:
        return _resolver_descumprindo_interno(c, A, b, bounds, linhas_min, n, m)
    finally:
        _medir('sem_solucao_descumprindo', t_desc)


def _resolver_descumprindo_interno(c, A, b, bounds, linhas_min, n, m):
    from scipy.optimize import linprog
    E = np.zeros((A.shape[0], m))
    for j, r in enumerate(linhas_min):
        E[r, j] = -1.0
    A_el = np.hstack([A, E])
    pesos = np.array([1.0 / max(abs(b[r]), 1.0) for r in linhas_min])
    bnds = list(bounds) + [(0, None)] * m
    r1 = linprog(np.concatenate([np.zeros(n), pesos]), A_ub=A_el, b_ub=b, bounds=bnds, method='highs')
    if not r1.success:
        return None, {}
    lim = float(r1.fun) * (1 + 1e-7) + 1e-9
    A2 = np.vstack([A_el, np.concatenate([np.zeros(n), pesos])])
    b2 = np.concatenate([b, [lim]])
    r2 = linprog(np.concatenate([c, np.zeros(m)]), A_ub=A2, b_ub=b2, bounds=bnds, method='highs')
    if not r2.success:
        r2 = r1
        fun = float(np.dot(c, r1.x[:n]))
    else:
        fun = float(r2.fun)
    folgas = {}
    for j, r in enumerate(linhas_min):
        v = float(r2.x[n + j])
        if v > 1e-6 * max(1.0, abs(b[r])):
            folgas[r] = v
    return {'x': r2.x[:n], 'fun': fun}, folgas


def _nomes_restricoes(cid):
    """(tipo, id) -> nome, como o Otimizar gravou em TbOtimizacaoShadow."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("""select distinct on (oti_shadow_tipo, oti_shadow_id_1) oti_shadow_tipo, oti_shadow_id_1, oti_shadow_restricao
                       from otimizacao_tbotimizacaoshadow where tbcenarios_id = %s
                       order by oti_shadow_tipo, oti_shadow_id_1, dau_order desc""", [cid])
        return {(t, int(i)): str(n) for t, i, n in cur.fetchall() if i is not None}


MAX_VIOLACOES_PONTO = 8
MAX_LIMITANTES_PONTO = 6
LIMITE_SEM_MAXIMO = 9_999_999      # máximo a partir disso = "sem limite" (o mesmo de analise_resultados)
ROTULO_TIPO = {'E': 'Equipamento gargalo', 'CE': 'Conjunto de equipamentos', 'P': 'Produto', 'PM': 'Produto/mercado', 'IC': 'Insumo'}


class SimilaresAcc:
    """🌟 NOVO (22/10/2026): para mostrar para ONDE o volume vai quando o equipamento analisado ganha ou perde capacidade.
      - SIMILARES: equipamentos GARGALO (com linha no modelo) que aparecem nos fluxos ALTERNATIVOS -- outras variáveis
        (produto/mercado/fluxo) dos MESMOS produtos que passam pelo equipamento analisado -- e NÃO nos fluxos que passam
        por ele. Equipamento que não é gargalo fica de fora (não limita no Otimizar).
      - FLUXOS: os que passam pelo equipamento analisado e os alternativos -- volume (unidade do produto final) somado
        dos períodos.
    Por combinação: horas usadas e capacidade de cada similar (só dos períodos com solução) e volume de cada fluxo."""

    def __init__(self):
        self.horas = defaultdict(lambda: defaultdict(float))
        self.cap = defaultdict(lambda: defaultdict(float))
        self.vol = defaultdict(lambda: defaultdict(float))
        self.sim_ids, self.fluxos = set(), {}          # fluxo -> passa pelo equipamento analisado?

    def preparar(self, mod, A, passa, oti_equip_id):
        vf = [f for _v, f in mod['vars']]
        pv = mod.get('produto_var') or [None] * len(vf)
        prod_sel = {pv[j] for j in range(len(vf)) if passa[j]}
        alt = np.array([(pv[j] in prod_sel) and not passa[j] for j in range(len(vf))], dtype=bool)
        rows = []
        if alt.any():
            for oti, l in mod['linhas_e'].items():
                if oti == oti_equip_id:
                    continue
                row = A[l + 1]
                if (row[alt] > 1e-12).any() and not (row[passa] > 1e-12).any():
                    rows.append((oti, l + 1))
                    self.sim_ids.add(oti)
        fluxo_var = defaultdict(list)
        for j in range(len(vf)):
            if passa[j] or alt[j]:
                fluxo_var[vf[j]].append(j)
                self.fluxos[vf[j]] = self.fluxos.get(vf[j], False) or bool(passa[j])
        return rows, {f: np.array(js) for f, js in fluxo_var.items()}

    def acumular(self, chave, A2, b, x, rows, fluxo_var):
        for oti, rr in rows:
            self.horas[chave][oti] += float(np.dot(A2[rr], x))
            self.cap[chave][oti] += float(b[rr])
        for f, js in fluxo_var.items():
            self.vol[chave][f] += float(x[js].sum())

    def cabecalho(self, cid):
        """[{'id', 'codigo'}] dos similares e [{'id', 'nome', 'passa'}] dos fluxos (os que passam pelo equipamento primeiro)."""
        from django.db import connection
        cod, nome = {}, {}
        with connection.cursor() as cur:
            if self.sim_ids:
                cur.execute("""select o.id, c.equ_cad_codigo from otimizacao_tbotimizacaoequipamentos o
                               join equipamentos_tbequipamentoscadastro c on c.id = o.oti_equ_equipamento_id
                               where o.id = any(%s)""", [list(self.sim_ids)])
                cod = {i: str(c or i) for i, c in cur.fetchall()}
            if self.fluxos:
                cur.execute("select id, flu_pro_descricao from fluxos_tbfluxoproducao where id = any(%s)", [list(self.fluxos)])
                nome = {i: str(n or i) for i, n in cur.fetchall()}
        sims = sorted([{'id': i, 'codigo': cod.get(i, str(i))} for i in self.sim_ids], key=lambda r: r['codigo'])
        fls = sorted([{'id': f, 'nome': nome.get(f, str(f)), 'passa': p} for f, p in self.fluxos.items()],
                     key=lambda r: (not r['passa'], r['nome']))
        return sims, fls

    def ponto(self, chave, sims, fls, inviavel):
        """'oc_sim': ocupação (%) de cada similar e 'vol': volume de cada fluxo, na ordem do cabeçalho. Com algum período
        sem solução, só os períodos com solução (a tela avisa)."""
        if not sims and not fls:
            return {}
        h, c, v = self.horas.get(chave, {}), self.cap.get(chave, {}), self.vol.get(chave, {})
        return {'oc_sim': [round(100.0 * h.get(s['id'], 0.0) / c[s['id']], 2) if c.get(s['id'], 0) > 0 else None for s in sims],
                'vol': [round(v.get(f['id'], 0.0), 2) if chave in self.vol else None for f in fls]}


def _chave_txt(chave):
    return '%s:%s:%s' % chave


def _linhas_restricoes(info, b, oti_equip_id, vistas):
    """🌟 NOVO (22/10/2026): linhas do modelo que podem "segurar" a margem -- máximo (e mínimo > 0) de equipamento GARGALO
    (menos o analisado, que já tem a ocupação na tela), conjunto de equipamentos, produto, produto/mercado e insumo.
    Máximo "sem limite" (valor enorme) fica de fora. Devolve [((tipo, id, lado), linha)]."""
    saida = []
    for r, (tipo, rid, lado) in enumerate(info):
        if rid is None or lado not in ('min', 'max') or (tipo == 'E' and rid == oti_equip_id):
            continue
        if lado == 'min' and not b[r] < -1e-9:
            continue
        if lado == 'max' and abs(b[r]) >= LIMITE_SEM_MAXIMO:
            continue
        chave = (tipo, int(rid), lado)
        vistas[chave] = True
        saida.append((chave, r))
    return saida


def _membros_conjuntos(cid):
    """{id do conjunto: [códigos dos equipamentos]} (o mesmo cadastro da Análise dos Resultados)."""
    from django.db import connection
    membros = defaultdict(list)
    try:
        with connection.cursor() as cur:
            cur.execute("""select j.tbotimizacaoconjuntoequipamentos_id, coalesce(c.equ_cad_codigo, j.tbequipamentoscadastro_id::text)
                           from otimizacao_tbotimizacaoconjuntoequipamentos_oti_con_equ_equ15bf j
                                join otimizacao_tbotimizacaoconjuntoequipamentos ce on ce.id = j.tbotimizacaoconjuntoequipamentos_id
                                left join equipamentos_tbequipamentoscadastro c on c.id = j.tbequipamentoscadastro_id
                           where ce.tbcenarios_id = %s order by 2""", [cid])
            for ce_id, cod in cur.fetchall():
                membros[ce_id].append(str(cod))
    except Exception:
        pass
    return membros


def _resumo_restricoes(cid, no_limite, vistas, nomes, ic0, ip0):
    """Cada restrição que ficou no limite em alguma combinação: tipo, lado, nome, membros (conjunto), em quantos períodos
    estava no limite na BASE e em quantas combinações (das que têm solução) ficou no limite em algum período."""
    usadas = defaultdict(int)
    for pts in no_limite.values():
        for chave in pts:
            usadas[chave] += 1
    if not usadas:
        return []
    membros = _membros_conjuntos(cid) if any(k[0] == 'CE' for k in usadas) else {}
    base = no_limite.get((ic0, ip0), {})
    lista = []
    for chave, n in usadas.items():
        tipo, rid, lado = chave
        nome = nomes.get((tipo, rid), '%s %s' % (tipo, rid))
        lista.append({'chave': _chave_txt(chave), 'tipo': tipo, 'tipo_rotulo': ROTULO_TIPO.get(tipo, tipo), 'id': rid, 'lado': lado,
                      'nome': nome, 'rotulo': '%s %s (%s)' % (ROTULO_TIPO.get(tipo, tipo), nome, 'máximo' if lado == 'max' else 'mínimo'),
                      'membros': membros.get(rid, []) if tipo == 'CE' else [],
                      'periodos_base': int(base.get(chave, 0)), 'combinacoes': int(n)})
    ordem = {'CE': 0, 'E': 1, 'IC': 2, 'PM': 3, 'P': 4}
    lista.sort(key=lambda r: (-r['periodos_base'], -r['combinacoes'], ordem.get(r['tipo'], 9), r['nome']))
    return lista


def calcular_sensibilidade(cid, ordens, oti_equip_id, fluxos=None, grade_custo=None, grade_prod=None, rotulo_periodo=str):
    """Grade Δcusto x Δprodutividade -> MCT (soma dos períodos). fluxos: ids de fluxo de produção (None = todos)."""
    grade_custo = grade_custo or GRADE_PADRAO
    grade_prod = grade_prod or GRADE_PADRAO
    nC, nP = len(grade_custo), len(grade_prod)
    mct = np.zeros((nC, nP))              # só períodos com solução
    mct_desc = np.zeros((nC, nP))         # com os períodos sem solução resolvidos "descumprindo" os mínimos
    sem_desc = np.zeros((nC, nP), dtype=int)   # períodos que nem descumprindo os mínimos têm solução
    horas_uso = np.zeros((nC, nP))
    cap_total = 0.0
    inviavel = np.zeros((nC, nP), dtype=int)
    violacoes = defaultdict(list)          # (ic, ip) -> [[tipo, id, período, mínimo, falta]]
    periodos_inv = defaultdict(list)
    base_por_periodo, custo_eq_base, custo_proprio_base, horas_eq_base = [], 0.0, 0.0, 0.0
    # 🌟 NOVO (22/10/2026): restrições no limite em cada combinação (tipo, id, lado) -> períodos
    no_limite = defaultdict(lambda: defaultdict(int))
    restr_vistas = {}
    sim = SimilaresAcc()           # 🌟 22/10/2026: ocupação dos similares e volume por fluxo em cada combinação
    t0 = time.time()
    _TEMPOS.clear()
    _CONTAGEM.clear()
    _log(f"início: cenário {cid}, equipamento {oti_equip_id}, {len(ordens)} período(s), {nC * nP} combinações")
    # 🌟 NOVO (22/10/2026): cenário FINAL -- se o Otimizar aplicou a produção antecipada, cada combinação resolve o modelo
    # INTEGRADO (todos os períodos juntos, com estoque), e não cada período sozinho
    from otimizacao.antecipacao import CenarioFinal
    t = time.time()
    cf = CenarioFinal(cid, ordens)
    _medir('preparar_cenario', t)
    integrado = cf.ativo
    prep = []
    for i in ordens:
        if integrado and i not in cf.ordens:
            continue
        t = time.time()
        mod = cf.modelo(i) if integrado else montar_modelo_periodo(cid, i)
        _medir('montar_modelo', t)
        if not mod['vars']:
            continue
        t = time.time()
        eq = dados_equipamento_periodo(cid, i, oti_equip_id, set(fluxos) if fluxos else None)
        _medir('dados_equipamento', t)
        t_per = time.time()
        _log(f"período {rotulo_periodo(i)}: modelo com {len(mod['vars'])} variáveis x {mod['A'].shape[0]} restrições "
             f"({time.time() - t0:.0f} s até aqui)")
        n = len(mod['vars'])
        h = np.array([eq['horas'].get(f, 0.0) for _v, f in mod['vars']])
        k = np.array([eq['custo'].get(f, 0.0) for _v, f in mod['vars']])
        kp = np.array([eq['custo_proprio'].get(f, 0.0) for _v, f in mod['vars']])
        A, b = mod['A'], mod['b']
        info = list(mod['info'])
        # linha de capacidade do equipamento escolhido: a do modelo (se ele é gargalo) ou uma nova
        lin = mod['linhas_e'].get(oti_equip_id)
        if lin is not None:
            base_min, base_max = A[lin].copy(), A[lin + 1].copy()
            # só a parte dos fluxos escolhidos muda com a produtividade
            fora_min, fora_max = base_min + h, base_max - h
        else:
            # equipamento que o modelo não trata como gargalo: só o limite MÁXIMO (o mínimo de ocupação não existia no
            # modelo original -- colocá-lo mudaria o resultado base)
            A = np.vstack([A, -h, h]) if A.shape[0] else np.vstack([-h, h])
            b = np.concatenate([b, [0.0, eq['cap_max']]])
            info += [('E', oti_equip_id, 'novo'), ('E', oti_equip_id, 'max')]
            lin = A.shape[0] - 2
            fora_min, fora_max = np.zeros(n), np.zeros(n)
        linhas_min = [r for r, t in enumerate(info) if t[2] == 'min' and b[r] < -1e-9]   # só mínimos > 0
        cap_total += eq['cap_max']
        base_c = mod['c']
        rot = rotulo_periodo(i)
        linhas_lim = _linhas_restricoes(info, b, oti_equip_id, restr_vistas)
        passa = (A[lin + 1] > 1e-12) | (h > 1e-12)      # variáveis (produto/mercado/fluxo) que passam pelo equipamento
        sim_rows, fluxo_var = sim.preparar(mod, A, passa, oti_equip_id)
        if integrado:
            prep.append({'i': i, 'A': A, 'b': b, 'info': info, 'lin': lin, 'fora_min': fora_min, 'fora_max': fora_max,
                         'h': h, 'k': k, 'kp': kp, 'c': base_c, 'rot': rot, 'linhas_lim': linhas_lim,
                         'sim_rows': sim_rows, 'fluxo_var': fluxo_var})
            continue
        for ic, dc in enumerate(grade_custo):
            c2 = base_c + (dc / 100.0) * k              # c = -margem: mais custo -> c maior
            for ip, dp in enumerate(grade_prod):
                fator = 1.0 / (1.0 + dp / 100.0)
                A2 = A.copy()
                A2[lin] = fora_min - h * fator
                A2[lin + 1] = fora_max + h * fator
                r = _resolver(c2, A2, b, mod['bounds'])
                if r is None:
                    rd, folgas = _resolver_descumprindo(c2, A2, b, mod['bounds'], linhas_min)
                    if rd is not None and not folgas:
                        # o modelo com folga achou solução SEM descumprir nenhum mínimo: não era inviável (falha numérica
                        # do solver) -- vale como ponto com solução
                        mct[ic, ip] += -rd['fun']
                        mct_desc[ic, ip] += -rd['fun']
                        horas_uso[ic, ip] += float(np.dot(h * fator, rd['x']))
                        continue
                    inviavel[ic, ip] += 1
                    periodos_inv[(ic, ip)].append(rot)
                    if rd is None:
                        sem_desc[ic, ip] += 1
                        continue
                    mct_desc[ic, ip] += -rd['fun']
                    horas_uso[ic, ip] += float(np.dot(h * fator, rd['x']))
                    for rr, falta in folgas.items():
                        t = info[rr]
                        violacoes[(ic, ip)].append([t[0], t[1], rot, -float(b[rr]), falta])
                    continue
                mct[ic, ip] += -float(r.fun)
                mct_desc[ic, ip] += -float(r.fun)
                horas_uso[ic, ip] += float(np.dot(h * fator, r.x))
                for chave, rr in linhas_lim:       # 🌟 22/10/2026: quem ficou no limite nesta combinação
                    if b[rr] - float(np.dot(A2[rr], r.x)) <= 1e-6 * max(1.0, abs(b[rr])):
                        no_limite[(ic, ip)][chave] += 1
                sim.acumular((ic, ip), A2, b, r.x, sim_rows, fluxo_var)
                if dc == 0 and dp == 0:
                    base_por_periodo.append([rot, -float(r.fun)])
                    custo_eq_base += float(np.dot(k, r.x))
                    custo_proprio_base += float(np.dot(kp, r.x))
                    horas_eq_base += float(np.dot(h, r.x))
        _log(f"período {rot}: {nC * nP} combinações em {time.time() - t_per:.1f} s")
    if integrado and prep:
        _log(f"modelo integrado (produção antecipada): {len(prep)} período(s) juntos, {nC * nP} otimizações")
        # cada combinação = UMA otimização do modelo integrado (todos os períodos, com produção antecipada)
        # 🌟 29/10/2026: na ordem produtividade x custo, com o custo indo e voltando (zigue-zague) -- cada otimização muda
        # pouco em relação à anterior, e o HiGHS parte da solução anterior (CenarioFinal.resolver incremental)
        combinacoes = []
        for ip in range(nP):
            ics = range(nC) if ip % 2 == 0 else range(nC - 1, -1, -1)
            combinacoes.extend((ic, ip) for ic in ics)
        ip_atual, A2s = None, {}
        for ic, ip in combinacoes:
                dc, dp = grade_custo[ic], grade_prod[ip]
                fator = 1.0 / (1.0 + dp / 100.0)
                if ip != ip_atual:          # 🌟 29/10/2026: a matriz só muda com a produtividade -- uma vez por Δp
                    ip_atual, A2s = ip, {}
                    for p in prep:
                        A2 = p['A'].copy()
                        A2[p['lin']] = p['fora_min'] - p['h'] * fator
                        A2[p['lin'] + 1] = p['fora_max'] + p['h'] * fator
                        A2s[p['i']] = A2
                alt = {p['i']: {'c': p['c'] + (dc / 100.0) * p['k'], 'A': A2s[p['i']], 'b': p['b'], 'info': p['info']} for p in prep}
                t = time.time()
                R = cf.resolver(alt, incremental=True)
                _medir('otimizacoes_integradas', t)
                if _CONTAGEM['otimizacoes_integradas'] % 13 == 0:
                    _log(f"modelo integrado: {_CONTAGEM['otimizacoes_integradas']} de {nC * nP} ({time.time() - t0:.0f} s)")
                if R is None:
                    # sem solução: sem o diagnóstico "descumprindo os mínimos" (ele é por período) -- todos os períodos juntos
                    inviavel[ic, ip] = len(prep)
                    sem_desc[ic, ip] = len(prep)
                    periodos_inv[(ic, ip)] = [p['rot'] for p in prep]
                    continue
                mct[ic, ip] = R.mct
                mct_desc[ic, ip] = R.mct
                for p in prep:
                    xp = R.producao_de(p['i'])
                    horas_uso[ic, ip] += float(np.dot(p['h'] * fator, xp))
                    for chave, rr in p['linhas_lim']:
                        f = R.folga(p['i'], rr)
                        if f is not None and f <= 1e-6 * max(1.0, abs(p['b'][rr])):
                            no_limite[(ic, ip)][chave] += 1
                    sim.acumular((ic, ip), A2s[p['i']], p['b'], xp, p['sim_rows'], p['fluxo_var'])
                    if dc == 0 and dp == 0:
                        base_por_periodo.append([p['rot'], R.mct_de(p['i'])])
                        custo_eq_base += float(np.dot(p['k'], xp))
                        custo_proprio_base += float(np.dot(p['kp'], xp))
                        horas_eq_base += float(np.dot(p['h'], xp))
    for k_t, v_t in (cf.tempos or {}).items():         # 🌟 29/10/2026: dentro das otimizações do modelo integrado
        if k_t == 'resolver_do_zero':
            _CONTAGEM['integrado_resolvido_do_zero'] = int(v_t)
            _TEMPOS['integrado_resolvido_do_zero'] += 0.0
        else:
            _TEMPOS['integrado_' + k_t] += v_t
            # preparar_* acontece uma vez (na criação do cenário); montar/resolver, uma vez por otimização
            _CONTAGEM['integrado_' + k_t] = 1 if k_t.startswith('preparar') else _CONTAGEM['otimizacoes_integradas']
    ic0, ip0 = grade_custo.index(0), grade_prod.index(0)
    base = float(mct[ic0, ip0])
    nomes = _nomes_restricoes(cid) if (violacoes or no_limite) else {}
    restricoes = _resumo_restricoes(cid, no_limite, restr_vistas, nomes, ic0, ip0)
    rotulo_r = {r['chave']: r['rotulo'] for r in restricoes}
    similares, fluxos_rel = sim.cabecalho(cid)
    pontos = []
    for ic, dc in enumerate(grade_custo):
        for ip, dp in enumerate(grade_prod):
            inv = int(inviavel[ic, ip])
            viol = sorted(violacoes.get((ic, ip), []), key=lambda v: -(v[4] / max(abs(v[3]), 1.0)))[:MAX_VIOLACOES_PONTO]
            lim_p = sorted(no_limite.get((ic, ip), {}).items(), key=lambda kv: (-kv[1], rotulo_r.get(_chave_txt(kv[0]), '')))
            pontos.append({'dc': dc, 'dp': dp, **sim.ponto((ic, ip), similares, fluxos_rel, inv),
                           'limitantes': [[rotulo_r.get(_chave_txt(k), _chave_txt(k)), n] for k, n in lim_p[:MAX_LIMITANTES_PONTO]],
                           'mct': None if inv else float(mct[ic, ip]), 'delta': None if inv else float(mct[ic, ip]) - base,
                           'mct_desc': round(float(mct_desc[ic, ip]), 2) if inv and not sem_desc[ic, ip] else None,
                           'delta_desc': round(float(mct_desc[ic, ip]) - base, 2) if inv and not sem_desc[ic, ip] else None,
                           'horas': float(horas_uso[ic, ip]),
                           'ocupacao': 100.0 * horas_uso[ic, ip] / cap_total if cap_total > 0 else None,
                           'inviavel': inv, 'periodos_inviaveis': periodos_inv.get((ic, ip), []),
                           'violacoes': [{'tipo': v[0], 'nome': nomes.get((v[0], v[1]), f"{v[0]} {v[1]}"), 'periodo': v[2],
                                          'minimo': round(v[3], 4), 'falta': round(v[4], 4), 'atingido': round(v[3] - v[4], 4)} for v in viol]})
    # curva de equilíbrio: para cada Δp, o Δc em que a MCT fica igual à base (interpolação linear no eixo do custo)
    equilibrio = []
    for ip, dp in enumerate(grade_prod):
        col = [(grade_custo[ic], float(mct[ic, ip]) - base) for ic in range(nC) if not inviavel[ic, ip]]
        valor = None
        for (c1, d1), (c2, d2) in zip(col, col[1:]):
            if d1 == 0:
                valor = c1
                break
            if (d1 > 0) != (d2 > 0):
                valor = c1 + (c2 - c1) * (0 - d1) / (d2 - d1)
                break
        if valor is None and col and col[-1][1] == 0:
            valor = col[-1][0]
        equilibrio.append({'dp': dp, 'dc': valor})
    return {'grade_custo': grade_custo, 'grade_prod': grade_prod, 'pontos': pontos, 'base': base,
            'base_por_periodo': base_por_periodo, 'equilibrio': equilibrio,
            'custo_eq_base': custo_eq_base, 'custo_proprio_base': custo_proprio_base, 'base_custo': 'acumulado',
            'horas_eq_base': horas_eq_base, 'capacidade': cap_total, 'restricoes_no_limite': restricoes,
            'similares': similares, 'fluxos_rel': fluxos_rel, 'modelo_integrado': bool(integrado and prep),
            'periodos': len(base_por_periodo), 'segundos': round(time.time() - t0, 1),
            'tempos': _resumo_tempos(time.time() - t0)}


def _resumo_tempos(total):
    """🌟 29/10/2026: {fase: [segundos, quantidade]} e o total; também vai para o log."""
    r = {k: [round(v, 1), _CONTAGEM[k]] for k, v in sorted(_TEMPOS.items(), key=lambda kv: -kv[1])}
    _log(f"fim em {total:.0f} s -- " + '; '.join(f"{k}: {v[0]} s ({v[1]}x)" for k, v in r.items()))
    return {'total': round(total, 1), 'fases': r}
