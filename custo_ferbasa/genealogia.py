"""
🌟 NOVO: Genealogia dos Itens de Produção -- percentual de GGF variável e fixo/outros que cada item carrega,
somando o GGF de toda a cadeia de produção dele (substitui o cálculo das procedures atualizar_genealogia /
atualizar_genealogia_v2).

O que é calculado (por item de produção P, com TODO o histórico da Produção Mensal da empresa):

    GGF por unidade de P = GGF direto por unidade de P + soma, para cada insumo c que também é produzido,
                           de (consumo de c por unidade de P) x (GGF por unidade de c)

Separado em variável e fixo/outros. É a mesma recursão das procedures, mas resolvida de uma vez (sistema de
equações, uma vez por grupo de itens que se alimentam em ciclo), em vez de abrir a árvore caminho a caminho:
  - sem o corte de 0,0015 no consumo acumulado (que dependia da unidade: T/T x kWh/T);
  - sem o arredondamento a 4 casas a cada nível;
  - recirculação contada por inteiro (todas as voltas), não só bloqueando a volta imediata (A -> B -> A).
Quando a recirculação de um grupo é maior ou igual à produção (o sistema não fecha -- acontece com as voltas das
movimentações VAZIO), as ligações do grupo entram da maior para a menor quantidade e ficam de fora só as voltas de
menor quantidade que impediriam o grupo de fechar (decisão de 12/10/2026, "regra A2"); o item sai marcado.

O GGF direto vem da Distribuição GGF Mensal do próprio item, pelo tipo da Conta Contábil x Centro de Custo:
V -> percentual é a parte variável; F -> 100 - percentual; O -> 0. Conta com outro tipo (ex.: A, aguardando
caracterização) BLOQUEIA o cálculo: devolve a lista das contas para o usuário classificar.

Mesmas regras das procedures para os demais campos:
  - produção do item = soma, por (ano/mês, ordem de produção), da quantidade produzida (uma vez por ordem);
    em grupo de máquina VAZIO conta como "vazio" (movimentação), senão como "real";
  - consumo por par (item produzido, item consumido) = soma de todos os lançamentos; só consumo > 0 entra na
    cadeia (consumo negativo = subproduto gerado);
  - o insumo c é "produzido" quando existe item de produção com o MESMO código e produção > 0;
  - itens sem produção ficam com início '999/99', fim '0000/00' e produção 0.

As funções de cálculo não usam o banco (recebem listas), pra poderem ser testadas isoladamente;
carregar_dados(empresa_id) lê o banco e calcular_genealogia(...) calcula.
"""
from collections import defaultdict

TIPOS_VALIDOS = ('V', 'F', 'O')


def _f(v):
    return float(v) if v is not None else 0.0


def _percentual_variavel(tipo, percentual):
    """Parte variável (%) de um lançamento de GGF, pelo tipo da conta."""
    if tipo == 'V':
        return _f(percentual)
    if tipo == 'F':
        return 100.0 - _f(percentual)
    return 0.0      # 'O'


def contas_invalidas(distribuicao):
    """Contas (Conta Contábil x Centro de Custo) com tipo fora de V/F/O que têm GGF lançado -- bloqueiam o cálculo."""
    vistas = {}
    for d in distribuicao:
        if d['tipo'] not in TIPOS_VALIDOS and d['conta_cc_id'] not in vistas:
            vistas[d['conta_cc_id']] = {'conta_cc_id': d['conta_cc_id'], 'tipo': d['tipo'], 'descricao': d.get('conta_descricao', '')}
    return sorted(vistas.values(), key=lambda c: str(c['descricao']))


def _componentes_fortes(nos, saidas):
    """Tarjan iterativo. Devolve as componentes fortemente conexas já na ordem 'dependências primeiro'
    (uma componente só sai depois de todas as que ela alcança)."""
    indice, menor, na_pilha = {}, {}, set()
    pilha, componentes, contador = [], [], [0]
    for raiz in nos:
        if raiz in indice:
            continue
        trabalho = [(raiz, iter(saidas.get(raiz, ())))]
        indice[raiz] = menor[raiz] = contador[0]; contador[0] += 1
        pilha.append(raiz); na_pilha.add(raiz)
        while trabalho:
            v, filhos = trabalho[-1]
            avancou = False
            for w in filhos:
                if w not in indice:
                    indice[w] = menor[w] = contador[0]; contador[0] += 1
                    pilha.append(w); na_pilha.add(w)
                    trabalho.append((w, iter(saidas.get(w, ()))))
                    avancou = True
                    break
                if w in na_pilha:
                    menor[v] = min(menor[v], indice[w])
            if avancou:
                continue
            trabalho.pop()
            if trabalho:
                menor[trabalho[-1][0]] = min(menor[trabalho[-1][0]], menor[v])
            if menor[v] == indice[v]:
                comp = []
                while True:
                    w = pilha.pop(); na_pilha.discard(w); comp.append(w)
                    if w == v:
                        break
                componentes.append(comp)
    return componentes


def resolver_ggf_por_unidade(itens_produzidos, a, d_var, d_fix, peso=None, cortes=None):
    """
    Resolve g = d + A g (variável e fixo/outros) para os itens produzidos.
      a[P][c] = consumo de c por unidade de P (só insumos produzidos);  d_*[P] = GGF direto por unidade de P.
    Devolve (g_var, g_fix, multiplicador, sem_voltas):
      multiplicador[P] -- maior fator de recirculação do grupo do item (1 = sem recirculação);
      sem_voltas       -- itens de grupos onde a recirculação >= produção: parte das voltas (as de menor
                          quantidade) ficou de fora para o grupo fechar.
    peso[(P, c)]: quantidade consumida (ordena as ligações no caso acima); cortes: lista que recebe as ligações cortadas.
    """
    import numpy as np

    g_var, g_fix, multiplicador, sem_voltas = {}, {}, {}, set()
    saidas = {p: [c for c in a.get(p, {})] for p in itens_produzidos}
    for comp in _componentes_fortes(sorted(itens_produzidos), saidas):
        dentro = set(comp)
        ciclico = len(comp) > 1 or comp[0] in a.get(comp[0], {})
        if not ciclico:
            p = comp[0]
            g_var[p] = d_var.get(p, 0.0) + sum(q * g_var[c] for c, q in a.get(p, {}).items())
            g_fix[p] = d_fix.get(p, 0.0) + sum(q * g_fix[c] for c, q in a.get(p, {}).items())
            multiplicador[p] = 1.0
            continue

        # parte de fora do grupo (já resolvida) entra no lado direito
        pos = {p: i for i, p in enumerate(comp)}
        n = len(comp)
        m = np.eye(n)
        b = np.zeros((n, 2))
        for p in comp:
            i = pos[p]
            b[i, 0] = d_var.get(p, 0.0)
            b[i, 1] = d_fix.get(p, 0.0)
            for c, q in a.get(p, {}).items():
                if c in dentro:
                    m[i, pos[c]] -= q
                else:
                    b[i, 0] += q * g_var[c]
                    b[i, 1] += q * g_fix[c]

        # O sistema só faz sentido se a recirculação do grupo for menor que a produção: aí (I - A)^-1 >= 0.
        # Testa resolvendo com lado direito = 1 (dá o fator de recirculação de cada item).
        ok = True
        try:
            teste = np.linalg.solve(m, np.ones(n))
            x = np.linalg.solve(m, b)
            if not np.all(np.isfinite(teste)) or teste.min() < 1.0 - 1e-9 or teste.max() > 1e9:
                ok = False
        except np.linalg.LinAlgError:
            ok = False

        if ok:
            fator = float(teste.max())
            for p in comp:
                g_var[p] = float(x[pos[p], 0])
                g_fix[p] = float(x[pos[p], 1])
                multiplicador[p] = fator
            continue

        # Recirculação >= produção (o grupo "não fecha" -- acontece com as voltas das movimentações VAZIO):
        # monta o grupo de novo colocando as ligações internas da MAIOR para a MENOR quantidade consumida e deixa de
        # fora só as que fariam o grupo não fechar (raio espectral >= 1). As voltas que ficam contam por inteiro.
        internas = sorted(((p, c) for p in comp for c in a.get(p, {}) if c in dentro),
                          key=lambda e: (-(peso or {}).get(e, a[e[0]][e[1]]), e))
        bloco = np.zeros((n, n))
        cortadas = set()
        for p, c in internas:
            bloco[pos[p], pos[c]] = a[p][c]
            if max(abs(np.linalg.eigvals(bloco))) >= 0.999:
                bloco[pos[p], pos[c]] = 0.0
                cortadas.add((p, c))
        m = np.eye(n) - bloco
        x = np.linalg.solve(m, b)
        fator = float(np.linalg.solve(m, np.ones(n)).max())
        for p in comp:
            g_var[p] = float(x[pos[p], 0])
            g_fix[p] = float(x[pos[p], 1])
            multiplicador[p] = fator
            sem_voltas.add(p)
        if cortes is not None:
            cortes.extend(cortadas)
    return g_var, g_fix, multiplicador, sem_voltas


def _procedure_atual(itens, produzidos, producao, consumo, codigo_consumo, item_por_codigo, var_dir, fix_dir):
    """
    Réplica, em Python, do algoritmo da procedure atual (atualizar_genealogia / _v2) -- SÓ para o relatório de
    comparação (comando comparar_genealogia): árvore caminho a caminho, corte de 0,0015, indicador arredondado a
    4 casas, bloqueio só da volta imediata, até 51 níveis. Validada contra a procedure (diferença de 0,05 p.p.),
    só que leva segundos em vez de horas.
    """
    from decimal import Decimal, ROUND_HALF_UP

    def r4(x):
        return float(Decimal(repr(x)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))

    filha = defaultdict(list)
    for (p, ic), (q, _vm, _vg) in consumo.items():
        if round(q, 2) > 0:
            filha[p].append((ic, round(q, 2)))
    T = {i: producao[i] for i in produzidos}
    pct = {}
    for i in itens:
        v, f = var_dir[i], fix_dir[i]
        if i in produzidos:
            nivel = [(ic, r4(q / T[i]), None) for ic, q in filha[i]]
            todos = list(nivel)
            for _ in range(51):
                novo = []
                for ic, ind, pai in nivel:
                    c = item_por_codigo.get(codigo_consumo.get(ic))
                    if c not in produzidos:
                        continue
                    for ic2, q2 in filha[c]:
                        valor = ind * q2 / T[c]
                        if valor > 0.0015 and ic2 != pai:
                            novo.append((ic2, r4(valor), ic))
                if not novo:
                    break
                nivel = novo
                todos += novo
            gv = gf = 0.0
            for ic, ind, _pai in todos:
                c = item_por_codigo.get(codigo_consumo.get(ic))
                if c in produzidos:
                    gv += var_dir[c] / T[c] * ind
                    gf += fix_dir[c] / T[c] * ind
            v += gv * T[i]
            f += gf * T[i]
        pct[i] = 100.0 * v / (v + f) if v + f > 0 else None
    return pct


# Árvore genealógica (tabela TbItensProducaoDaugther1): só para CONSULTA -- os percentuais não dependem dela.
CORTE_ARVORE = 0.001         # some o ramo que pesa menos de 0,1% do valor de uma unidade do produto (~100 mil linhas no total)
MAX_NIVEIS_ARVORE = 50
MAX_LINHAS_ARVORE = 20000    # por item de produção


def _montar_arvore(produzidos, producao, consumo, codigo_consumo, item_por_codigo, var_dir, fix_dir):
    """
    Para cada item produzido P, a árvore dos insumos, caminho a caminho, com o consumo acumulado por unidade de P
    (indicador) e a chave 01, 01.02, 01.02.03... como a procedure gravava. Corrigida:
      - um caminho para de descer quando o item já apareceu nele mesmo (recirculação: a linha aparece, mas não
        abre de novo) -- a procedure só bloqueava a volta imediata;
      - o corte é pelo PESO no valor do produto (consumo acumulado x valor contábil unitário do insumo, sobre o
        valor contábil unitário do produto), igual para T, kWh ou qualquer unidade -- a procedure cortava por
        0,0015 de quantidade;
      - sem arredondamento no meio do caminho (só ao gravar, na casa decimal da tabela).
    O primeiro nível (insumos diretos) aparece sempre.
    """
    filha = defaultdict(list)
    valor_insumo_total, qtde_insumo_total = defaultdict(float), defaultdict(float)
    custo_total = defaultdict(float)
    for (p, ic), (q, vm, vg) in consumo.items():
        if q > 0:
            filha[p].append((ic, q))
            valor_insumo_total[ic] += vm + vg
            qtde_insumo_total[ic] += q
            custo_total[p] += vm + vg
    valor_unit = {ic: valor_insumo_total[ic] / qtde_insumo_total[ic] for ic in qtde_insumo_total if qtde_insumo_total[ic] > 0}
    for lista in filha.values():
        lista.sort(key=lambda x: -x[1])

    arvore = {}
    for raiz in produzidos:
        T = producao[raiz]
        valor_raiz = (custo_total[raiz] + var_dir[raiz] + fix_dir[raiz]) / T

        def peso(ic, ind):
            if valor_raiz <= 0:
                return ind if ind > 0.0015 else 0.0          # sem valor contábil: volta à regra antiga
            return ind * abs(valor_unit.get(ic, 0.0)) / valor_raiz

        linhas = []
        # pilha: (chave, item de consumo, indicador, caminho de itens produzidos, nível)
        pilha = [('%02d' % (k + 1), ic, q / T, frozenset([raiz]), 1) for k, (ic, q) in enumerate(filha[raiz])]
        pilha.reverse()
        while pilha and len(linhas) < MAX_LINHAS_ARVORE:
            chave, ic, ind, caminho, nivel = pilha.pop()
            linhas.append((chave, ic, ind))
            c = item_por_codigo.get(codigo_consumo.get(ic))
            if c not in produzidos or c in caminho or nivel >= MAX_NIVEIS_ARVORE:
                continue
            if peso(ic, ind) < CORTE_ARVORE:
                continue
            filhos, k = [], 0
            for ic2, q2 in filha[c]:
                ind2 = ind * q2 / producao[c]
                if peso(ic2, ind2) < CORTE_ARVORE:
                    continue
                k += 1
                filhos.append(('%s.%02d' % (chave, k), ic2, ind2, caminho | {c}, nivel + 1))
            pilha.extend(reversed(filhos))
        arvore[raiz] = linhas
    return arvore


def _montar_consolidado(produzidos, producao, consumo, codigo_consumo, item_por_codigo, a, cortes, d_var, d_fix, g_var, g_fix):
    """
    Quadro consolidado (TbItensProducaoDaugther2): para cada item produzido P, cada item consumido em TODA a cadeia
    aparece UMA vez, com:
      quantidade      -- quanto P carrega dele por unidade de P (todos os caminhos e todas as voltas da recirculação);
      ggf_variavel / ggf_fixo_outros -- quanto do GGF por unidade de P foi gerado onde ele é produzido
                         (zero para item comprado);
      participacao    -- % do GGF total por unidade de P que vem dele.
    Usa a matriz de requisitos totais R = (I - A)^-1, com as mesmas ligações do cálculo dos percentuais (sem as
    cortadas pela regra das movimentações), então a soma do GGF de todos os insumos + o direto de P = GGF de P.
    """
    import numpy as np

    ordem = sorted(produzidos)
    pos = {p: i for i, p in enumerate(ordem)}
    n = len(ordem)
    if not n:
        return {}
    cortadas = set(cortes)
    m = np.eye(n)
    for p in ordem:
        for c, q in a.get(p, {}).items():
            if (p, c) not in cortadas:
                m[pos[p], pos[c]] -= q
    R = np.linalg.inv(m)                       # R[P, Q] = unidades de Q por unidade de P (Q = P inclui a própria)

    # consumo por unidade de cada item produzido Q, por item de consumo
    por_unidade = defaultdict(list)
    for (q_item, ic), (q, _vm, _vg) in consumo.items():
        if q_item in pos and q > 0:
            por_unidade[q_item].append((ic, q / producao[q_item]))
    ic_produzido = {ic: item_por_codigo.get(codigo_consumo.get(ic)) for (_p, ic) in consumo}

    resultado = {}
    for p in ordem:
        linha = R[pos[p]]
        total = g_var[p] + g_fix[p]
        qtd = defaultdict(float)
        for q_item, k in pos.items():
            fator = linha[k]
            if abs(fator) < 1e-12:
                continue
            for ic, por_un in por_unidade[q_item]:
                qtd[ic] += fator * por_un
        lista = []
        for ic, quantidade in qtd.items():
            c = ic_produzido.get(ic)
            gv = gf = 0.0
            if c in pos:
                gv = linha[pos[c]] * d_var.get(c, 0.0)
                gf = linha[pos[c]] * d_fix.get(c, 0.0)
                if c == p:                      # o direto do próprio P não é "de um insumo": só o que volta pela recirculação
                    gv -= d_var.get(c, 0.0)
                    gf -= d_fix.get(c, 0.0)
            participacao = 100.0 * (gv + gf) / total if total > 0 else None
            if abs(quantidade) < 1e-12 and abs(gv + gf) < 1e-12:
                continue
            lista.append((ic, quantidade, gv, gf, participacao))
        lista.sort(key=lambda x: (-(x[4] or 0), -x[1]))
        resultado[p] = lista
    return resultado


def calcular_genealogia(linhas, itens, codigo_consumo, distribuicao, comparar_com_procedure=False, montar_arvore=False):
    """
    linhas: lançamentos da Produção Mensal da empresa -- dicts com item_producao_id, ano_mes, ordem,
            qtde_produzida, grupo_codigo, item_consumo_id, qtde_consumo, valor_material, valor_ggf.
    itens: {id do item de produção: código} (itens de produção da empresa).
    codigo_consumo: {id do item de consumo: código}.
    distribuicao: lançamentos da Distribuição GGF Mensal -- dicts com item_producao_id, valor, tipo, percentual,
                  conta_cc_id, conta_descricao.

    Devolve {'erro': ..., 'contas_invalidas': [...]} se houver conta com tipo fora de V/F/O; senão
    {'itens': {id: resultado}, 'consumos': [...], 'sem_voltas': [...]}.
    """
    invalidas = contas_invalidas(distribuicao)
    if invalidas:
        return {'erro': 'Existem contas (Conta Contábil x Centro de Custo) com GGF lançado e tipo diferente de V, F ou O. '
                        'Classifique-as antes de atualizar a genealogia.',
                'contas_invalidas': invalidas}

    # 1) produção por item: uma vez por (item, ano/mês, ordem); real x vazio pelo grupo de máquina do evento
    eventos = {}
    for l in linhas:
        chave = (l['item_producao_id'], l['ano_mes'], l['ordem'])
        q = _f(l['qtde_produzida'])
        if chave not in eventos:
            eventos[chave] = [q, l.get('grupo_codigo')]
        else:
            ev = eventos[chave]
            if q > ev[0]:
                ev[0] = q
            if (l.get('grupo_codigo') or '') > (ev[1] or ''):
                ev[1] = l.get('grupo_codigo')
    real, vazio = defaultdict(float), defaultdict(float)
    inicio, fim = {}, {}
    for (item, ano_mes, _ordem), (q, grupo) in eventos.items():
        if grupo == 'VAZIO':
            vazio[item] += q
        else:
            real[item] += q
        if item not in inicio or ano_mes < inicio[item]:
            inicio[item] = ano_mes
        if item not in fim or ano_mes > fim[item]:
            fim[item] = ano_mes

    # 2) consumo por par (produzido, consumido) -- igual à tabela filha tbitensproducaodaugther
    consumo = defaultdict(lambda: [0.0, 0.0, 0.0])
    for l in linhas:
        q = _f(l['qtde_consumo'])
        c = consumo[(l['item_producao_id'], l['item_consumo_id'])]
        c[0] += q
        c[1] += _f(l['valor_material']) * q
        c[2] += _f(l['valor_ggf']) * q
    consumos = [{'mae_id': p, 'item_consumo_id': ic, 'qtde_consumo': v[0], 'valor_material': v[1], 'valor_ggf': v[2]}
                for (p, ic), v in consumo.items() if v[0] != 0]

    # 3) GGF direto (total do histórico) por item
    var_dir, fix_dir = defaultdict(float), defaultdict(float)
    for d in distribuicao:
        pv = _percentual_variavel(d['tipo'], d['percentual'])
        valor = _f(d['valor'])
        var_dir[d['item_producao_id']] += valor * pv / 100.0
        fix_dir[d['item_producao_id']] += valor * (100.0 - pv) / 100.0

    # 4) a cadeia: insumo produzido = item de produção com o mesmo código e produção > 0
    producao = {i: real[i] + vazio[i] for i in itens}
    produzidos = {i for i in itens if producao[i] > 0}
    item_por_codigo = {}
    for i, cod in itens.items():
        if i in produzidos or cod not in item_por_codigo:
            item_por_codigo[cod] = i
    a = defaultdict(dict)
    peso = defaultdict(float)
    for (p, ic), (q, _vm, _vg) in consumo.items():
        if p not in produzidos or q <= 0:
            continue
        c = item_por_codigo.get(codigo_consumo.get(ic))
        if c is None or c not in produzidos:
            continue
        a[p][c] = a[p].get(c, 0.0) + q / producao[p]
        peso[(p, c)] += q
    d_var = {p: var_dir[p] / producao[p] for p in produzidos}
    d_fix = {p: fix_dir[p] / producao[p] for p in produzidos}
    cortes = []
    g_var, g_fix, multiplicador, sem_voltas = resolver_ggf_por_unidade(produzidos, a, d_var, d_fix, peso=peso, cortes=cortes)

    # 5) resultado por item, nos mesmos campos de TbItensProducao
    resultado = {}
    for i in itens:
        r = {
            'ano_mes_inicio': inicio.get(i, '999/99'),
            'ano_mes_fim': fim.get(i, '0000/00'),
            'qtde_produzida_real': real[i],
            'qtde_produzida_vazio': vazio[i],
            'ggf_variavel_direto': var_dir[i],
            'ggf_fixo_outros_direto': fix_dir[i],
            'ggf_variavel_pi': 0.0,
            'ggf_fixo_outros_pi': 0.0,
            'percentual_variavel_ggf': None,
            'percentual_fixo_outros_ggf': None,
            'fator_recirculacao': None,
            'recirculacao_sem_voltas': i in sem_voltas,
        }
        if i in produzidos:
            r['ggf_variavel_pi'] = (g_var[i] - d_var[i]) * producao[i]
            r['ggf_fixo_outros_pi'] = (g_fix[i] - d_fix[i]) * producao[i]
            r['fator_recirculacao'] = multiplicador.get(i)
        total = r['ggf_variavel_direto'] + r['ggf_variavel_pi'] + r['ggf_fixo_outros_direto'] + r['ggf_fixo_outros_pi']
        if total > 0:
            r['percentual_variavel_ggf'] = (r['ggf_variavel_direto'] + r['ggf_variavel_pi']) * 100.0 / total
            r['percentual_fixo_outros_ggf'] = (r['ggf_fixo_outros_direto'] + r['ggf_fixo_outros_pi']) * 100.0 / total
        resultado[i] = r
    codigo = itens.get
    arvore = consolidado = None
    if montar_arvore:
        consolidado = _montar_consolidado(produzidos, producao, consumo, codigo_consumo, item_por_codigo, a, cortes,
                                          d_var, d_fix, g_var, g_fix)
        arvore = _montar_arvore(produzidos, producao, consumo, codigo_consumo, item_por_codigo, var_dir, fix_dir)
    if comparar_com_procedure:
        for i, pct in _procedure_atual(itens, produzidos, producao, consumo, codigo_consumo, item_por_codigo,
                                       var_dir, fix_dir).items():
            resultado[i]['percentual_variavel_procedure_dados_atuais'] = pct
    return {'itens': resultado, 'consumos': consumos, 'sem_voltas': sorted(sem_voltas), 'arvore': arvore,
            'consolidado': consolidado,
            'ligacoes_cortadas': sorted((codigo(p), codigo(c), peso[(p, c)]) for p, c in cortes)}


# ---------------------------------------------------------------------------------------------------------------
# Leitura do banco (por empresa)
# ---------------------------------------------------------------------------------------------------------------
def carregar_dados(empresa_id):
    """Lê do banco tudo o que calcular_genealogia precisa, só da empresa (mesma regra de 'registros antigos sem
    empresa' do fluxo de produção mensal)."""
    from .models import TbItensProducao, TbItensConsumo, TbDistribuicaoGGFMensal
    from .fluxo_producao_mensal import producao_mensal_da_empresa, _da_empresa

    linhas = [{
        'item_producao_id': l['pro_men_item_producao_id'],
        'ano_mes': l['pro_men_ano_mes'],
        'ordem': l['pro_men_ordem_producao'],
        'qtde_produzida': l['pro_men_qtde_produzida'],
        'grupo_codigo': l['pro_men_grupo_maquina__gru_maq_codigo'],
        'item_consumo_id': l['pro_men_item_consumo_id'],
        'qtde_consumo': l['pro_men_qtde_consumo'],
        'valor_material': l['pro_men_valor_material'],
        'valor_ggf': l['pro_men_valor_ggf'],
    } for l in producao_mensal_da_empresa(empresa_id).order_by().values(
        'pro_men_item_producao_id', 'pro_men_ano_mes', 'pro_men_ordem_producao', 'pro_men_qtde_produzida',
        'pro_men_grupo_maquina__gru_maq_codigo', 'pro_men_item_consumo_id', 'pro_men_qtde_consumo',
        'pro_men_valor_material', 'pro_men_valor_ggf')]

    itens = dict(_da_empresa(TbItensProducao, empresa_id).values_list('id', 'ite_pro_codigo'))
    ids_consumo = {l['item_consumo_id'] for l in linhas}
    codigo_consumo = dict(TbItensConsumo.objects.filter(id__in=ids_consumo).values_list('id', 'ite_con_codigo'))

    distribuicao = []
    for d in _da_empresa(TbDistribuicaoGGFMensal, empresa_id).order_by().values(
            'dis_ggf_men_item_producao_id', 'dis_ggf_men_valor', 'dis_ggf_men_conta_cc_id',
            'dis_ggf_men_conta_cc__con_con_cen_cus_tipo', 'dis_ggf_men_conta_cc__con_con_cen_cus_percentual',
            'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo',
            'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao',
            'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo'):
        distribuicao.append({
            'item_producao_id': d['dis_ggf_men_item_producao_id'],
            'valor': d['dis_ggf_men_valor'],
            'tipo': d['dis_ggf_men_conta_cc__con_con_cen_cus_tipo'],
            'percentual': d['dis_ggf_men_conta_cc__con_con_cen_cus_percentual'],
            'conta_cc_id': d['dis_ggf_men_conta_cc_id'],
            'conta_descricao': '{} {} / CC {}'.format(
                d['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo'] or '',
                d['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao'] or '',
                d['dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo'] or '').strip(),
        })
    return linhas, itens, codigo_consumo, distribuicao


# ---------------------------------------------------------------------------------------------------------------
# Gravação no banco (substitui a procedure atualizar_genealogia / atualizar_genealogia_v2)
# ---------------------------------------------------------------------------------------------------------------
CHAVE_LOCK = 731001      # trava do PostgreSQL (advisory lock) enquanto a genealogia de uma empresa está sendo gravada


def genealogia_rodando(empresa_id=None):
    """True se a genealogia está sendo gravada agora (a trava está ocupada), False se não, None se não deu pra saber."""
    from django.db import connection
    if connection.vendor != 'postgresql':
        return None
    try:
        with connection.cursor() as cursor:
            sql = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND classid = %s"
            parametros = [CHAVE_LOCK]
            if empresa_id is not None:
                sql += ' AND objid = %s'
                parametros.append(int(empresa_id))
            cursor.execute(sql, parametros)
            return cursor.fetchone()[0] > 0
    except Exception:
        return None


def contas_invalidas_da_empresa(empresa_id):
    """Contas com GGF lançado na empresa e tipo fora de V/F/O (consulta leve, pra checar antes de disparar)."""
    from .models import TbDistribuicaoGGFMensal
    from .fluxo_producao_mensal import _da_empresa
    qs = (_da_empresa(TbDistribuicaoGGFMensal, empresa_id)
          .exclude(dis_ggf_men_conta_cc__con_con_cen_cus_tipo__in=TIPOS_VALIDOS)
          .order_by().values('dis_ggf_men_conta_cc_id', 'dis_ggf_men_conta_cc__con_con_cen_cus_tipo',
                             'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo',
                             'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao',
                             'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo').distinct())
    return [{'conta_cc_id': d['dis_ggf_men_conta_cc_id'], 'tipo': d['dis_ggf_men_conta_cc__con_con_cen_cus_tipo'],
             'descricao': '{} {} / CC {}'.format(d['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo'] or '',
                                                 d['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao'] or '',
                                                 d['dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo'] or '').strip()}
            for d in qs]


def texto_contas_invalidas(contas, limite=20):
    linhas = ['{} (tipo {})'.format(c['descricao'] or 'conta id %s' % c['conta_cc_id'], c['tipo']) for c in contas[:limite]]
    if len(contas) > limite:
        linhas.append('... e mais %d' % (len(contas) - limite))
    return '; '.join(linhas)


def gravar_genealogia(empresa_id):
    """
    Calcula e GRAVA a genealogia da empresa, nos mesmos campos que a procedure gravava:
      TbItensProducao: ano/mês início e fim, produção real e vazio, GGF variável/fixo direto e de produtos
                       internos (pi), percentuais variável e fixo/outros;
      TbItensProducaoDaugther: consumo por item consumido (quantidade, valor material, valor GGF);
      TbItensProducaoDaugther1: a árvore genealógica (corrigida -- ver _montar_arvore);
      TbItensProducaoDaugther2: o quadro consolidado, um insumo por linha (ver _montar_consolidado).
    Só mexe nos registros da empresa. Tudo numa transação: se der erro no meio, nada fica pela metade.
    Devolve {'erro': ..., 'contas_invalidas': [...]} quando há conta com tipo fora de V/F/O (nada é gravado), ou
    um resumo do que foi gravado.
    """
    import time
    from decimal import Decimal, ROUND_HALF_UP
    from django.db import connection, transaction
    from .models import TbItensProducao, TbItensProducaoDaugther, TbItensProducaoDaugther1, TbItensProducaoDaugther2

    inicio = time.time()
    linhas, itens, codigo_consumo, distribuicao = carregar_dados(empresa_id)
    resultado = calcular_genealogia(linhas, itens, codigo_consumo, distribuicao, montar_arvore=True)
    if 'erro' in resultado:
        return resultado

    def d2(v):
        return None if v is None else Decimal(repr(float(v))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def d4(v):
        return Decimal(repr(float(v))).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [CHAVE_LOCK, int(empresa_id)])

        objetos = list(TbItensProducao.objects.filter(id__in=list(itens)))
        for obj in objetos:
            r = resultado['itens'][obj.id]
            obj.ite_pro_ano_mes_inicio = r['ano_mes_inicio']
            obj.ite_pro_ano_mes_fim = r['ano_mes_fim']
            obj.ite_pro_qtde_produzida_real = d2(r['qtde_produzida_real'])
            obj.ite_pro_qtde_produzida_vazio = d2(r['qtde_produzida_vazio'])
            obj.ite_pro_ggf_variavel_direto = d2(r['ggf_variavel_direto'])
            obj.ite_pro_ggf_fixo_outros_direto = d2(r['ggf_fixo_outros_direto'])
            obj.ite_pro_ggf_variavel_pi = d2(r['ggf_variavel_pi'])
            obj.ite_pro_ggf_fixo_outros_pi = d2(r['ggf_fixo_outros_pi'])
            obj.ite_pro_percentual_variavel_ggf = d2(r['percentual_variavel_ggf'])
            obj.ite_pro_percentual_fixo_outros_ggf = d2(r['percentual_fixo_outros_ggf'])
        TbItensProducao.objects.bulk_update(objetos, [
            'ite_pro_ano_mes_inicio', 'ite_pro_ano_mes_fim', 'ite_pro_qtde_produzida_real', 'ite_pro_qtde_produzida_vazio',
            'ite_pro_ggf_variavel_direto', 'ite_pro_ggf_fixo_outros_direto', 'ite_pro_ggf_variavel_pi',
            'ite_pro_ggf_fixo_outros_pi', 'ite_pro_percentual_variavel_ggf', 'ite_pro_percentual_fixo_outros_ggf'],
            batch_size=500)

        TbItensProducaoDaugther.objects.filter(mae_id__in=list(itens)).delete()
        TbItensProducaoDaugther.objects.bulk_create([
            TbItensProducaoDaugther(mae_id=c['mae_id'], item_consumo_id=c['item_consumo_id'], qtde_consumo=d2(c['qtde_consumo']),
                                    valor_material=d2(c['valor_material']), valor_ggf=d2(c['valor_ggf']), empresa_id=empresa_id)
            for c in resultado['consumos'] if c['mae_id'] in itens and round(c['qtde_consumo'], 2) != 0], batch_size=2000)

        TbItensProducaoDaugther1.objects.filter(mae_id__in=list(itens)).delete()
        linhas_arvore = [
            TbItensProducaoDaugther1(mae_id=raiz, chave=chave, item_consumo_id=ic, indicador=d4(ind), empresa_id=empresa_id)
            for raiz, lista in resultado['arvore'].items() for chave, ic, ind in lista]
        TbItensProducaoDaugther1.objects.bulk_create(linhas_arvore, batch_size=5000)

        def d8(v):
            return None if v is None else Decimal(repr(float(v))).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)

        TbItensProducaoDaugther2.objects.filter(mae_id__in=list(itens)).delete()
        linhas_consolidado = [
            TbItensProducaoDaugther2(mae_id=raiz, item_consumo_id=ic, quantidade=d8(qtd), ggf_variavel=d8(gv),
                                     ggf_fixo_outros=d8(gf), participacao_ggf=d2(part), empresa_id=empresa_id)
            for raiz, lista in resultado['consolidado'].items() for ic, qtd, gv, gf, part in lista]
        TbItensProducaoDaugther2.objects.bulk_create(linhas_consolidado, batch_size=5000)

    return {'itens': len(objetos), 'consumos': len(resultado['consumos']), 'linhas_arvore': len(linhas_arvore),
            'linhas_consolidado': len(linhas_consolidado),
            'itens_movimentacao_sem_fechar': len(resultado['sem_voltas']),
            'ligacoes_cortadas': resultado['ligacoes_cortadas'], 'segundos': round(time.time() - inicio, 1)}
