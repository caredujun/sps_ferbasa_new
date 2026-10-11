"""
🌟 NOVO: monta o FLUXO DE PRODUÇÃO a partir da Produção Mensal (TbProducaoMensal) -- SÓ LEITURA, não grava nada.

Usado pela Ação Comum "Custo Ferbasa - Montar Fluxo de Produção pela Produção Mensal" do Agente IA: o usuário escolhe
um ou mais itens de produção e um período (ano/mês início e fim), e o chat desenha o fluxo em colunas, como o editor.

Como a cadeia é montada (de trás pra frente, a partir dos itens escolhidos):

  - OPERAÇÃO = um item de produção num grupo de máquina. A quantidade produzida no período soma cada combinação
    (ano/mês, estabelecimento, grupo de máquina, ordem de produção, item) UMA vez só -- ela se repete em todas as linhas
    de consumo daquela ordem.
  - O consumo de cada item (pro_men_qtde_consumo) é somado no período, por operação.
  - Um item de consumo é "produzido aqui" quando o CÓDIGO dele (TbItensConsumo.ite_con_codigo) é igual ao código de um
    item de produção (TbItensProducao.ite_pro_codigo) que teve produção no período. Nesse caso a cadeia continua
    pra trás a partir das operações que o produziram.
      * produzido por UMA operação: a ligação sai direto dessa operação;
      * produzido por VÁRIAS operações (grupos de máquina diferentes): aparece um "nó do item", com uma ligação
        de cada operação (a quantidade que cada uma produziu) -- assim não é preciso ratear o consumo entre elas.
  - Item de consumo que não é produzido no período = MATÉRIA-PRIMA (ponta de entrada do fluxo).
  - Consumo NEGATIVO (bonificação / subproduto) = SAÍDA da operação; não é seguido pra trás.
  - Se a cadeia voltar a um item que já está no caminho (reciclagem), a ligação é marcada como CICLO e não é seguida.

Ligações de consumo levam a quantidade total consumida no período e o consumo específico (consumo / produção da
operação que consome, no mesmo período).

🌟 NOVO: CUSTO por unidade de cada operação (equipamento) e dos produtos escolhidos -- total, variável e fixo/outros,
pelo valor contábil (igual à procedure custo_variavel_adicionado) -- ver calcular_custos() mais abaixo.
"""
from collections import defaultdict

# 🌟 ALTERADO: o chat desenha só os EQUIPAMENTOS_INICIAIS equipamentos mais próximos do produto final; os outros ficam numa
# lista abaixo do desenho, de onde o usuário pode colocá-los no desenho. Por isso o fluxo inteiro vai pro chat e não há
# mais o limite de 600 caixas -- LIMITE_CAIXAS só protege contra um fluxo absurdo (a resposta inteira vai pro histórico).
EQUIPAMENTOS_INICIAIS = 30
LIMITE_CAIXAS = 3000


class FluxoProducaoMensalError(RuntimeError):
    pass


def _num(v):
    return float(v) if v is not None else 0.0


def montar_grafo(linhas, itens_producao, itens_consumo, ids_selecionados, ggf_linhas=None, pct_var_ggf=None):
    """
    Função pura (sem banco), pra poder ser testada isoladamente.

    linhas: iteráveis de dicts com pro_men_ano_mes, pro_men_estabelecimento_id, pro_men_grupo_maquina_id,
            pro_men_ordem_producao, pro_men_item_producao_id, pro_men_qtde_produzida, pro_men_item_consumo_id,
            pro_men_qtde_consumo -- já filtradas pela empresa e pelo período.
    itens_producao: {id: {'codigo', 'descricao', 'unidade'}}   (TbItensProducao)
    itens_consumo:  {id: {'codigo', 'descricao', 'unidade', 'tipo'}}   (TbItensConsumo)
    grupos são lidos de linhas[...]['grupo'] -> {'codigo', 'nome'} (preenchido por carregar_grafo).
    ids_selecionados: ids de TbItensProducao escolhidos pelo usuário (os produtos finais do desenho).
    🌟 NOVO (custos): as linhas podem trazer pro_men_valor_material e pro_men_valor_ggf (valores UNITÁRIOS do item
    consumido); ggf_linhas = a Distribuição de GGF Mensal do período ([{ano_mes, ordem, item_id, grupo_id, valor,
    tipo, percentual}]); pct_var_ggf = {código do item: % variável do GGF} (TbItensProducao.ite_pro_percentual_
    variavel_ggf). Sem eles o custo sai zerado.
    """
    producao = defaultdict(float)               # (item_prod_id, grupo_id) -> qtde produzida no período
    ja_contado = set()
    consumo = defaultdict(lambda: defaultdict(float))   # op -> item_consumo_id -> qtde
    valores = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))   # op -> item_consumo_id -> [total, variável]
    ordens_com_producao = defaultdict(set)      # op -> {(ano_mes, ordem)} com produção (pra casar o GGF distribuído)
    pct_var_ggf = pct_var_ggf or {}
    grupos = {}
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        grupos[l['pro_men_grupo_maquina_id']] = l.get('grupo') or {'codigo': str(l['pro_men_grupo_maquina_id']), 'nome': ''}
        chave_prod = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                      l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave_prod not in ja_contado:
            ja_contado.add(chave_prod)
            producao[op] += _num(l['pro_men_qtde_produzida'])
        consumo[op][l['pro_men_item_consumo_id']] += _num(l['pro_men_qtde_consumo'])
        # Valor da linha, como na procedure custo_variavel_adicionado: total = (material + GGF) x qtde; variável =
        # material x qtde + a parte variável do GGF (% do item de produção com o mesmo código do item consumido).
        qtd, material, ggf = _num(l['pro_men_qtde_consumo']), _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
        pct = pct_var_ggf.get(itens_consumo[l['pro_men_item_consumo_id']]['codigo'], 0.0) if ggf > 0 else 0.0
        if _num(l['pro_men_qtde_produzida']) > 0:      # como na procedure: só linhas com produção
            reg = valores[op][l['pro_men_item_consumo_id']]
            reg[0] += (material + ggf) * qtd
            reg[1] += material * qtd + ggf * qtd * (pct if pct > 0 else 0.0) / 100.0
        if _num(l['pro_men_qtde_produzida']) > 0:
            ordens_com_producao[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))

    operacoes_por_codigo = defaultdict(list)    # código do item produzido -> [op, ...]
    for op in producao:
        operacoes_por_codigo[itens_producao[op[0]]['codigo']].append(op)
    for codigo in operacoes_por_codigo:
        operacoes_por_codigo[codigo].sort(key=lambda op: grupos[op[1]]['codigo'])

    nos, arestas = {}, []
    ids_arestas = set()

    def no_operacao(op):
        nid = f"op:{op[0]}:{op[1]}"
        if nid not in nos:
            item, grupo = itens_producao[op[0]], grupos[op[1]]
            nos[nid] = {'id': nid, 'tipo': 'op', 'final': op[0] in ids_selecionados,
                        'grupo_codigo': grupo['codigo'], 'grupo_nome': grupo['nome'],
                        'item_codigo': item['codigo'], 'item_descricao': item['descricao'],
                        'unidade': item['unidade'], 'producao': producao[op]}
        return nid

    def aresta(de, para, tipo, qtd=None, unidade='', especifico=None, unidade_esp=''):
        chave = (de, para, tipo)
        if chave in ids_arestas:
            return
        ids_arestas.add(chave)
        arestas.append({'de': de, 'para': para, 'tipo': tipo, 'qtd': qtd, 'unidade': unidade,
                        'especifico': especifico, 'unidade_esp': unidade_esp})

    def no_item(codigo):
        """Nó do item produzido por VÁRIAS operações, com uma ligação de cada uma."""
        nid = f"item:{codigo}"
        if nid not in nos:
            ops = operacoes_por_codigo[codigo]
            item = itens_producao[ops[0][0]]
            nos[nid] = {'id': nid, 'tipo': 'item', 'item_codigo': codigo, 'item_descricao': item['descricao'],
                        'unidade': item['unidade'], 'producao': sum(producao[o] for o in ops)}
            for o in ops:
                aresta(no_operacao(o), nid, 'producao', producao[o], item['unidade'])
        return nid

    expandidas = set()
    ciclos = 0

    def expandir(op, caminho):
        nonlocal ciclos
        if op in expandidas:
            return
        expandidas.add(op)
        destino = no_operacao(op)
        prod = producao[op]
        unidade_prod = itens_producao[op[0]]['unidade']
        for cons_id, qtd in sorted(consumo[op].items(), key=lambda kv: itens_consumo[kv[0]]['codigo']):
            ic = itens_consumo[cons_id]
            esp = (qtd / prod) if prod else None
            u_esp = f"{ic['unidade']}/{unidade_prod}"
            if qtd < 0:     # bonificação / subproduto: sai da operação
                nid = f"sub:{cons_id}"
                if nid not in nos:
                    nos[nid] = {'id': nid, 'tipo': 'sub', 'item_codigo': ic['codigo'], 'item_descricao': ic['descricao'],
                                'unidade': ic['unidade']}
                aresta(destino, nid, 'subproduto', -qtd, ic['unidade'], -esp if esp is not None else None, u_esp)
                continue
            produtoras = operacoes_por_codigo.get(ic['codigo'], [])
            if not produtoras:   # matéria-prima
                nid = f"mp:{cons_id}"
                if nid not in nos:
                    nos[nid] = {'id': nid, 'tipo': 'mp', 'item_codigo': ic['codigo'], 'item_descricao': ic['descricao'],
                                'unidade': ic['unidade'], 'tipo_item': ic.get('tipo') or ''}
                aresta(nid, destino, 'consumo', qtd, ic['unidade'], esp, u_esp)
                continue
            origem = no_operacao(produtoras[0]) if len(produtoras) == 1 else no_item(ic['codigo'])
            if ic['codigo'] in caminho:     # voltou a um item que já está no caminho: ciclo, não segue
                ciclos += 1
                aresta(origem, destino, 'ciclo', qtd, ic['unidade'], esp, u_esp)
                continue
            aresta(origem, destino, 'consumo', qtd, ic['unidade'], esp, u_esp)
            for p in produtoras:
                expandir(p, caminho | {ic['codigo']})
            if sum(1 for n in nos.values() if n['tipo'] in ('op', 'item')) > LIMITE_CAIXAS:
                raise FluxoProducaoMensalError(
                    f"O fluxo passou de {LIMITE_CAIXAS} caixas -- grande demais para mandar ao chat. Escolha menos itens "
                    "ou um período menor.")

    sem_producao = []
    for item_id in ids_selecionados:
        codigo = itens_producao[item_id]['codigo'] if item_id in itens_producao else None
        ops = [op for op in producao if op[0] == item_id]
        if not ops:
            sem_producao.append(codigo or str(item_id))
            continue
        for op in sorted(ops, key=lambda o: grupos[o[1]]['codigo']):
            no_operacao(op)
            expandir(op, frozenset({codigo}))

    # Total consumido de cada matéria-prima (soma das ligações que saem dela)
    for a in arestas:
        n = nos.get(a['de'])
        if n and n['tipo'] == 'mp':
            n['total'] = n.get('total', 0.0) + (a['qtd'] or 0.0)
    for a in arestas:
        n = nos.get(a['para'])
        if n and n['tipo'] == 'sub':
            n['total'] = n.get('total', 0.0) + (a['qtd'] or 0.0)

    # 🌟 NOVO: BALANÇO de cada item produzido no desenho -- produzido x consumido neste fluxo x consumido por outros
    # produtos (o que a Produção Mensal do período registra, mesmo fora da cadeia escolhida) x o que sobra (sem consumo
    # registrado: estoque, venda direta ou perdas; negativo = consumiu-se mais do que se produziu no período, veio de
    # estoque). Vai no nó do item de vários grupos de máquina e no equipamento que é o único produtor do item.
    ops_no_fluxo = {(int(n['id'].split(':')[1]), int(n['id'].split(':')[2])) for n in nos.values() if n['tipo'] == 'op'}
    codigos_do_desenho = {n['item_codigo'] for n in nos.values() if n['tipo'] in ('op', 'item')}
    cons_ids_por_codigo = defaultdict(list)
    for cid, ic in itens_consumo.items():
        if ic['codigo'] in codigos_do_desenho:
            cons_ids_por_codigo[ic['codigo']].append(cid)
    balancos = {}
    for codigo in codigos_do_desenho:
        produzido = sum(producao[o] for o in operacoes_por_codigo.get(codigo, []))
        neste_fluxo, outros = 0.0, {}
        for op, itens in consumo.items():
            qtd = sum(itens.get(cid, 0.0) for cid in cons_ids_por_codigo[codigo])
            if qtd <= 0:
                continue                    # negativo é geração (subproduto), não consumo
            if op in ops_no_fluxo:
                neste_fluxo += qtd
            else:
                prod = itens_producao[op[0]]
                reg = outros.setdefault(prod['codigo'], {'produto': prod['codigo'], 'descricao': prod['descricao'],
                                                         'grupos': set(), 'qtd': 0.0})
                reg['grupos'].add(grupos[op[1]]['codigo'])
                reg['qtd'] += qtd
        lista = sorted(outros.values(), key=lambda r: -r['qtd'])
        total_outros = sum(r['qtd'] for r in lista)
        balancos[codigo] = {
            'produzido': produzido, 'neste_fluxo': neste_fluxo, 'outros_produtos': total_outros,
            'sem_consumo': produzido - neste_fluxo - total_outros,
            'outros': [{**r, 'grupos': ', '.join(sorted(r['grupos']))} for r in lista[:50]],
            'outros_total_itens': len(lista),
        }
    for n in nos.values():
        if n['tipo'] == 'item' or (n['tipo'] == 'op' and len(operacoes_por_codigo.get(n['item_codigo'], [])) == 1):
            n['balanco'] = balancos.get(n['item_codigo'])

    # 🌟 NOVO: custos (total, variável, fixo/outros) de cada operação do desenho e dos produtos escolhidos.
    custos_produtos, aviso_custo = calcular_custos(
        nos, producao, consumo, valores, ordens_com_producao, operacoes_por_codigo, itens_producao, itens_consumo,
        grupos, ggf_linhas or [], ids_selecionados)

    return {
        'equipamentos_iniciais': EQUIPAMENTOS_INICIAIS,
        'custos_produtos': custos_produtos,
        'aviso_custo': aviso_custo,
        'nos': list(nos.values()),
        'arestas': arestas,
        'resumo': {
            'operacoes': sum(1 for n in nos.values() if n['tipo'] == 'op'),
            'materias_primas': sum(1 for n in nos.values() if n['tipo'] == 'mp'),
            'subprodutos': sum(1 for n in nos.values() if n['tipo'] == 'sub'),
            'itens_varios_produtores': sum(1 for n in nos.values() if n['tipo'] == 'item'),
            'ligacoes': len(arestas),
            'ciclos': ciclos,
        },
        'sem_producao': sem_producao,
    }


# ---------------------------------------------------------------------------------------------------------------
# 🌟 NOVO: CUSTOS -- pelo VALOR CONTÁBIL (custo médio ponderado de estoque, que vem do ERP na Produção Mensal), igual à
# procedure custo_variavel_adicionado (Custo Total / Variável / Fixo-Outros "(P)"):
#   - cada linha de consumo do equipamento (item x grupo de máquina): total = (valor material + valor GGF) x qtde;
#     variável = material x qtde + GGF x qtde x %var/100 (%var = ite_pro_percentual_variavel_ggf do item de produção
#     com o mesmo código do item consumido; só quando > 0). Consumo negativo (subproduto/bonificação) entra negativo;
#   - mais o GGF lançado no equipamento pela Distribuição de GGF Mensal (mesmo ano/mês, ordem, item e grupo, só nas
#     ordens com produção): variável = valor x % (V ou A: % da conta/CC; F: 100 - %; O ou sem tipo: 0);
#   - fixo/outros = total - variável; tudo dividido pela produção do equipamento no período (custo por unidade).
#   Como na procedure, só entram linhas com produção > 0.
# ---------------------------------------------------------------------------------------------------------------
def _parte_variavel_ggf(valor, tipo, percentual):
    """Parcela variável do GGF distribuído -- mesma regra da procedure custo_variavel_adicionado."""
    if tipo == 'F':
        pct = 100.0 - _num(percentual)
    elif tipo == 'O' or not tipo:
        pct = 0.0
    else:                       # 'V' (e 'A', que a procedure trata igual)
        pct = _num(percentual)
    return valor * pct / 100.0


def calcular_custos(nos, producao, consumo, valores, ordens_com_producao, operacoes_por_codigo, itens_producao,
                    itens_consumo, grupos, ggf_linhas, ids_selecionados):
    """Grava n['custo'] (por unidade produzida) em cada operação do desenho. Devolve (custos dos produtos escolhidos,
    aviso ou '')."""
    ops = {(int(n['id'].split(':')[1]), int(n['id'].split(':')[2])) for n in nos.values() if n['tipo'] == 'op'}
    ops |= {op for op in producao if op[0] in ids_selecionados}
    # 🌟 NOVO: e tudo o que elas consomem da cadeia (mesmo o que o desenho não expandiu) -- entra na CONTRIBUIÇÃO.
    fila = list(ops)
    while fila:
        op = fila.pop()
        for cons_id, qtd in consumo[op].items():
            if qtd > 0:
                for p in operacoes_por_codigo.get(itens_consumo[cons_id]['codigo'], []):
                    if p not in ops:
                        ops.add(p)
                        fila.append(p)

    ggf = defaultdict(lambda: [0.0, 0.0])
    for g in ggf_linhas:
        op = (g['item_id'], g['grupo_id'])
        if op in ops and (g['ano_mes'], g['ordem']) in ordens_com_producao[op]:
            valor = _num(g['valor'])
            ggf[op][0] += valor
            ggf[op][1] += _parte_variavel_ggf(valor, g.get('tipo'), g.get('percentual'))

    totais = {}
    for op in ops:
        consumo_mat, sub = [0.0, 0.0], [0.0, 0.0]
        for cons_id, (total, variavel) in valores[op].items():
            alvo = sub if consumo[op].get(cons_id, 0.0) < 0 else consumo_mat
            alvo[0] += total
            alvo[1] += variavel
        totais[op] = {'materiais': consumo_mat, 'sub': sub, 'ggf': list(ggf[op])}

    def por_unidade(op):
        prod = producao[op]
        t = totais[op]
        un = lambda par: {'total': par[0] / prod, 'variavel': par[1] / prod} if prod > 0 else {'total': 0.0, 'variavel': 0.0}
        soma = [t['materiais'][0] + t['sub'][0] + t['ggf'][0], t['materiais'][1] + t['sub'][1] + t['ggf'][1]]
        return {'producao': prod, 'materiais': un(t['materiais']), 'sub': un(t['sub']), 'ggf': un(t['ggf']),
                'unit_total': un(soma)['total'], 'unit_variavel': un(soma)['variavel'], '_soma': soma}

    for nid, no in nos.items():
        if no['tipo'] == 'op':
            c = por_unidade((int(nid.split(':')[1]), int(nid.split(':')[2])))
            c.pop('_soma')
            no['custo'] = c

    aviso = _contribuicoes(nos, ops, producao, consumo, valores, operacoes_por_codigo, itens_producao, itens_consumo,
                           grupos, por_unidade, ids_selecionados)

    # Custo de cada produto escolhido: soma dos grupos de máquina que o fazem / produção somada.
    produtos = []
    for item_id in sorted(ids_selecionados, key=lambda i: itens_producao.get(i, {}).get('codigo', '')):
        ops_item = [op for op in ops if op[0] == item_id]
        soma_prod = sum(producao[op] for op in ops_item)
        if not ops_item or soma_prod <= 0:
            continue
        somas = [por_unidade(op)['_soma'] for op in ops_item]
        item = itens_producao[item_id]
        produtos.append({
            'codigo': item['codigo'], 'descricao': item['descricao'], 'unidade': item['unidade'], 'producao': soma_prod,
            'unit_total': sum(x[0] for x in somas) / soma_prod,
            'unit_variavel': sum(x[1] for x in somas) / soma_prod,
        })
    return produtos, aviso


# 🌟 NOVO: CONTRIBUIÇÃO de cada equipamento no custo do produto final (gráfico em escada) -- SÓ COM VALORES CONTÁBEIS.
#
# O custo contábil por unidade do equipamento k é:
#     c_k = acrescentado_k + soma_j( valor contábil do insumo j, vindo de outro equipamento do fluxo, por unidade de k )
#   acrescentado_k = (materiais que NÃO vêm de outro equipamento do fluxo + subprodutos + GGF distribuído) / produção_k
# O valor contábil de cada insumo é repartido entre os equipamentos que o produziram NA MESMA PROPORÇÃO do custo
# contábil deles (o que cada um acrescentou e o que veio de antes) -- e assim por diante, cadeia acima, do produto final
# para trás. A soma das contribuições é exatamente o custo contábil do produto final.
# RECIRCULAÇÃO (material que volta a um equipamento já no caminho, inclusive o próprio): o valor contábil dele fica no
# equipamento que o consome -- não é repartido de novo para trás (senão a conta dá voltas e pode não ter resposta).
# Total e variável são repartidos cada um com os seus próprios valores; fixo/outros = total - variável.
CORTE_ESCADA = 0.01       # no gráfico, quem contribui com menos de 1% do custo do produto vai para "Demais equipamentos"


def _contribuicoes(nos, ops, producao, consumo, valores, operacoes_por_codigo, itens_producao, itens_consumo,
                   grupos, por_unidade, ids_selecionados):
    ops = sorted(ops)
    unit = {op: por_unidade(op) for op in ops}
    custo = {op: (unit[op]['unit_total'], unit[op]['unit_variavel']) for op in ops}

    acrescentado = {}
    insumos = {op: [] for op in ops}           # (produtor, qtde por unidade de op, valor total/un, valor variável/un)
    for op in ops:
        prod = producao[op]
        soma = list(unit[op]['_soma'])
        for cons_id, qtd in consumo[op].items():
            produtores = operacoes_por_codigo.get(itens_consumo[cons_id]['codigo'], [])
            soma_prod = sum(producao[p] for p in produtores)
            if qtd <= 0 or not produtores or soma_prod <= 0 or prod <= 0:
                continue
            total, variavel = valores[op][cons_id]
            soma[0] -= total
            soma[1] -= variavel
            for p in produtores:
                part = producao[p] / soma_prod
                insumos[op].append((p, qtd / prod * part, total / prod * part, variavel / prod * part))
        acrescentado[op] = (soma[0] / prod, soma[1] / prod) if prod > 0 else (0.0, 0.0)

    finais = [op for op in ops if op[0] in ids_selecionados and producao[op] > 0]
    if not finais:
        return ''

    def cadeia(final):
        """Percorre a cadeia do produto final pra trás. Devolve (ordem do final pra trás, ligações que seguem -- sem as
        de recirculação --, distância mais longa de cada equipamento até o final)."""
        cinza, preto, pos_ordem, segue = set(), set(), [], defaultdict(set)
        pilha = [(final, iter({p for p, *_r in insumos[final]}))]
        cinza.add(final)
        while pilha:
            k, filhos = pilha[-1]
            proximo = next(filhos, None)
            if proximo is None:
                pilha.pop()
                cinza.discard(k)
                preto.add(k)
                pos_ordem.append(k)
                continue
            if proximo == k or proximo in cinza:
                continue                        # recirculação: fica com quem consome
            segue[k].add(proximo)
            if proximo not in preto:
                cinza.add(proximo)
                pilha.append((proximo, iter({p for p, *_r in insumos[proximo]})))
        ordem = list(reversed(pos_ordem))
        dist = {final: 0}
        for k in ordem:
            for p in segue[k]:
                dist[p] = max(dist.get(p, 0), dist.get(k, 0) + 1)
        return ordem, segue, dist

    rotulo = lambda op: (grupos[op[1]]['codigo'], itens_producao[op[0]]['codigo'])
    por_no = defaultdict(list)
    for final in finais:
        ordem, segue, dist = cadeia(final)
        peso = {k: [0.0, 0.0, 0.0] for k in ordem}     # [quantidade física, fração do total, fração do variável]
        peso[final] = [1.0, 1.0, 1.0]
        contrib = {k: [0.0, 0.0] for k in ordem}
        for k in ordem:                                  # do final pra trás: cada um já recebeu tudo o que lhe cabe
            wq, wt, wv = peso[k]
            proprio = [acrescentado[k][0], acrescentado[k][1]]
            for p, q, vt, vv in insumos[k]:
                if p not in segue[k]:                    # recirculação: o valor do insumo fica com k
                    proprio[0] += vt
                    proprio[1] += vv
                    continue
                peso[p][0] += wq * q
                for pos, valor, w in ((1, vt, wt), (2, vv, wv)):
                    base = custo[p][pos - 1]
                    if abs(base) > 1e-9:
                        peso[p][pos] += w * valor / base
                    else:                                # produtor sem custo: o valor fica com ele mesmo
                        contrib[p][pos - 1] += w * valor
            contrib[k][0] += wt * proprio[0]
            contrib[k][1] += wv * proprio[1]

        barras = {}
        for op in ordem:
            if abs(contrib[op][0]) < 1e-12 and abs(contrib[op][1]) < 1e-12 and abs(peso[op][0]) < 1e-12:
                continue
            chave = rotulo(op)
            # Mesmo equipamento produzindo o mesmo produto (mesmo código de grupo e de item) = uma barra só.
            b = barras.setdefault(chave, {'id': f"op:{op[0]}:{op[1]}", 'nome': f"{chave[0]} · {chave[1]}",
                                          'descricao': itens_producao[op[0]]['descricao'] or '',
                                          'distancia': dist.get(op, 0), 'necessidade': 0.0,
                                          'unidade': itens_producao[op[0]]['unidade'], 'total': 0.0, 'variavel': 0.0})
            b['distancia'] = max(b['distancia'], dist.get(op, 0))
            b['necessidade'] += peso[op][0]
            b['total'] += contrib[op][0]
            b['variavel'] += contrib[op][1]
            por_no[f"op:{op[0]}:{op[1]}"].append({
                'final': f"{rotulo(final)[0]} · {rotulo(final)[1]}", 'final_id': f"op:{final[0]}:{final[1]}",
                'unidade_final': itens_producao[final[0]]['unidade'], 'necessidade': peso[op][0],
                'total': contrib[op][0], 'variavel': contrib[op][1]})
        no_final = nos.get(f"op:{final[0]}:{final[1]}")
        if no_final is not None:
            no_final['custo']['escada'] = {
                'barras': sorted(barras.values(), key=lambda b: (-b['distancia'], b['nome'])),
                'corte': CORTE_ESCADA,
                'final': {'total': custo[final][0], 'variavel': custo[final][1]},
            }
    for nid, lista in por_no.items():
        if nid in nos and nos[nid]['tipo'] == 'op':
            nos[nid]['custo']['contribuicoes'] = lista
    return ''


# ---------------------------------------------------------------------------------------------------------------
# Acesso ao banco
# ---------------------------------------------------------------------------------------------------------------
def producao_mensal_da_empresa(empresa_id):
    """Linhas de Produção Mensal da empresa. Registros antigos, de antes do multi-empresa (empresa vazia), só são
    usados se a empresa não tiver nenhum registro próprio."""
    from .models import TbProducaoMensal
    qs = TbProducaoMensal.objects.filter(empresa_id=empresa_id)
    if not qs.exists():
        qs = TbProducaoMensal.objects.filter(empresa__isnull=True)
    return qs


def itens_com_producao(empresa_id):
    """[(TbItensProducao, ano_mes_minimo, ano_mes_maximo)] dos itens que têm Produção Mensal, em ordem de nome (descrição)."""
    from django.db.models import Min, Max
    from django.db.models.functions import Lower
    from .models import TbItensProducao
    limites = {r['pro_men_item_producao_id']: (r['minimo'], r['maximo']) for r in
               producao_mensal_da_empresa(empresa_id).order_by().values('pro_men_item_producao_id')
               .annotate(minimo=Min('pro_men_ano_mes'), maximo=Max('pro_men_ano_mes'))}
    itens = TbItensProducao.objects.filter(id__in=list(limites)).order_by(Lower('ite_pro_descricao'), 'ite_pro_codigo')
    return [(i, *limites[i.id]) for i in itens]


def _da_empresa(modelo, empresa_id):
    """Registros da empresa; os antigos, sem empresa, só quando a empresa não tem nenhum (mesma regra da Produção Mensal)."""
    qs = modelo.objects.filter(empresa_id=empresa_id)
    if not qs.exists():
        qs = modelo.objects.filter(empresa__isnull=True)
    return qs


def carregar_grafo(empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim):
    from .models import TbItensProducao, TbItensConsumo, TbGruposMaquinas, TbDistribuicaoGGFMensal
    linhas = list(producao_mensal_da_empresa(empresa_id)
                  .filter(pro_men_ano_mes__gte=ano_mes_inicio, pro_men_ano_mes__lte=ano_mes_fim)
                  .order_by()
                  .values('pro_men_ano_mes', 'pro_men_estabelecimento_id', 'pro_men_grupo_maquina_id',
                          'pro_men_ordem_producao', 'pro_men_item_producao_id', 'pro_men_qtde_produzida',
                          'pro_men_item_consumo_id', 'pro_men_qtde_consumo',
                          'pro_men_valor_material', 'pro_men_valor_ggf'))
    ids_prod = {l['pro_men_item_producao_id'] for l in linhas} | set(ids_selecionados)
    ids_cons = {l['pro_men_item_consumo_id'] for l in linhas}
    ids_grupo = {l['pro_men_grupo_maquina_id'] for l in linhas}
    itens_producao = {i['id']: {'codigo': i['ite_pro_codigo'], 'descricao': i['ite_pro_descricao'], 'unidade': i['ite_pro_unidade']}
                      for i in TbItensProducao.objects.filter(id__in=ids_prod).values('id', 'ite_pro_codigo', 'ite_pro_descricao', 'ite_pro_unidade')}
    itens_consumo = {i['id']: {'codigo': i['ite_con_codigo'], 'descricao': i['ite_con_descricao'], 'unidade': i['ite_con_unidade'],
                               'tipo': i['ite_con_tipo']}
                     for i in TbItensConsumo.objects.filter(id__in=ids_cons).values('id', 'ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade', 'ite_con_tipo')}
    grupos = {g['id']: {'codigo': g['gru_maq_codigo'], 'nome': g['gru_maq_nome']}
              for g in TbGruposMaquinas.objects.filter(id__in=ids_grupo).values('id', 'gru_maq_codigo', 'gru_maq_nome')}
    for l in linhas:
        l['grupo'] = grupos.get(l['pro_men_grupo_maquina_id'])

    # 🌟 NOVO (custos): % variável do GGF por código de item e a Distribuição de GGF Mensal do período.
    codigos_consumidos = {i['codigo'] for i in itens_consumo.values()}
    pct_var_ggf = {}
    for codigo, pct in (_da_empresa(TbItensProducao, empresa_id).filter(ite_pro_codigo__in=codigos_consumidos)
                        .values_list('ite_pro_codigo', 'ite_pro_percentual_variavel_ggf')):
        pct_var_ggf[codigo] = _num(pct)
    ggf_linhas = [
        {'ano_mes': g['dis_ggf_men_ano_mes'], 'ordem': g['dis_ggf_men_ordem_producao'],
         'item_id': g['dis_ggf_men_item_producao_id'], 'grupo_id': g['dis_ggf_men_grupo_maquina_id'],
         'valor': g['dis_ggf_men_valor'], 'tipo': g['dis_ggf_men_conta_cc__con_con_cen_cus_tipo'],
         'percentual': g['dis_ggf_men_conta_cc__con_con_cen_cus_percentual']}
        for g in (_da_empresa(TbDistribuicaoGGFMensal, empresa_id)
                  .filter(dis_ggf_men_ano_mes__gte=ano_mes_inicio, dis_ggf_men_ano_mes__lte=ano_mes_fim,
                          dis_ggf_men_item_producao_id__in=ids_prod, dis_ggf_men_grupo_maquina_id__in=ids_grupo)
                  .order_by()
                  .values('dis_ggf_men_ano_mes', 'dis_ggf_men_ordem_producao', 'dis_ggf_men_item_producao_id',
                          'dis_ggf_men_grupo_maquina_id', 'dis_ggf_men_valor',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_tipo',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_percentual'))
    ]
    return montar_grafo(linhas, itens_producao, itens_consumo, set(ids_selecionados),
                        ggf_linhas=ggf_linhas, pct_var_ggf=pct_var_ggf)
