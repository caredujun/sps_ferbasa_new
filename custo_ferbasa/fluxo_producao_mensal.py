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
import time
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


_NEG = 'neg'


def _separar_negativos(consumo, valores, itens_consumo):
    """🌟 NOVO (21/10/2026): resolve as chaves (_NEG, id) de montar_grafo. Equipamento que também consumiu o item
    (qtde > 0): o gerado fica no id NEGATIVO (-id), com o mesmo cadastro do item em itens_consumo (código, descrição,
    unidade) -- caixa de subproduto própria ('sub:-id'). Senão: soma no próprio id, exatamente como antes."""
    for op in list(consumo):
        itens = consumo[op]
        for chave in [k for k in itens if isinstance(k, tuple)]:
            cid = chave[1]
            q = itens.pop(chave)
            v = valores[op].pop(chave, None) if chave in valores[op] else None
            destino = -cid if itens.get(cid, 0.0) > 0 else cid
            if destino != cid:
                itens_consumo[destino] = itens_consumo[cid]
            itens[destino] = itens.get(destino, 0.0) + q
            if v is not None:
                r = valores[op][destino]
                r[0] += v[0]
                r[1] += v[1]


def montar_grafo(linhas, itens_producao, itens_consumo, ids_selecionados, ggf_linhas=None, pct_var_ggf=None,
                 agrupar_finais=True):
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
    # 🌟 CORRIGIDO (21/10/2026): o mesmo item CONSUMIDO (qtde > 0) e GERADO como subproduto (qtde < 0, sequência 35) no
    # mesmo equipamento -- se anulava na soma e sumia do desenho (nem MP nem SP).
    # Agora o negativo é somado à parte (chave NEG + id) e, se o equipamento também consumiu o item, vira uma entrada
    # própria (id negativo, com o mesmo cadastro do item). Sem consumo positivo, volta a ser o próprio id (como antes).
    itens_consumo = dict(itens_consumo)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        grupos[l['pro_men_grupo_maquina_id']] = l.get('grupo') or {'codigo': str(l['pro_men_grupo_maquina_id']), 'nome': ''}
        chave_prod = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                      l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave_prod not in ja_contado:
            ja_contado.add(chave_prod)
            producao[op] += _num(l['pro_men_qtde_produzida'])
        cid_l = l['pro_men_item_consumo_id'] if _num(l['pro_men_qtde_consumo']) >= 0 else (_NEG, l['pro_men_item_consumo_id'])
        consumo[op][cid_l] += _num(l['pro_men_qtde_consumo'])
        # Valor da linha, como na procedure custo_variavel_adicionado: total = (material + GGF) x qtde; variável =
        # material x qtde + a parte variável do GGF (% do item de produção com o mesmo código do item consumido).
        qtd, material, ggf = _num(l['pro_men_qtde_consumo']), _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
        pct = pct_var_ggf.get(itens_consumo[l['pro_men_item_consumo_id']]['codigo'], 0.0) if ggf > 0 else 0.0
        if _num(l['pro_men_qtde_produzida']) > 0:      # como na procedure: só linhas com produção
            reg = valores[op][cid_l]
            reg[0] += (material + ggf) * qtd
            reg[1] += material * qtd + ggf * qtd * (pct if pct > 0 else 0.0) / 100.0
        if _num(l['pro_men_qtde_produzida']) > 0:
            ordens_com_producao[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))
    _separar_negativos(consumo, valores, itens_consumo)

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
                        'unidade': item['unidade'], 'producao': sum(producao[o] for o in ops),
                        'membros': [f"op:{o[0]}:{o[1]}" for o in ops]}   # 🌟 16/10/2026: quem faz o item (custo consolidado)
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
        # 🌟 NOVO (16/10/2026): produto escolhido feito por MAIS DE UM equipamento (ex.: vários fornos) -- à frente deles,
        # uma caixa de AGRUPAMENTO (a mesma caixa de item de vários grupos de máquina do meio do fluxo), com a produção
        # somada e o custo consolidado. Cada equipamento continua com a sua análise individual.
        if agrupar_finais and len(ops) > 1:
            n_ag = nos[no_item(codigo)]
            n_ag['agrupa_final'] = True
            n_ag['membros'] = [f"op:{o[0]}:{o[1]}" for o in sorted(ops, key=lambda o: grupos[o[1]]['codigo'])]

    # 🌟 NOVO (16/10/2026): DOIS OU MAIS produtos escolhidos (com a mesma unidade) -- à frente deles, um quadrado de
    # CONSOLIDADO somando os produtos: recebe uma ligação de cada produto (da caixa de agrupamento dele, quando o produto
    # é feito em vários equipamentos; senão, do equipamento). Produção, custo (ponderado pela produção), MP, SP e balanço
    # somados. Produtos com unidades diferentes não são somados (sem o quadrado).
    info_cons = {'criado': False, 'motivo': 'só 1 produto escolhido', 'produtos': [], 'consumo_cruzado': [], 'fora': []}
    if agrupar_finais:
        prods = []
        for item_id in sorted(ids_selecionados, key=lambda i: itens_producao.get(i, {}).get('codigo', '')):
            ops_i = sorted([op for op in producao if op[0] == item_id and producao[op] > 0], key=lambda o: grupos[o[1]]['codigo'])
            if ops_i:
                prods.append((item_id, ops_i))
        # 🌟 NOVO (16/10/2026 -- opção c): produto escolhido que é INSUMO de outro produto escolhido fica FORA do consolidado
        # -- senão o mesmo material entraria duas vezes (ex.: 8050001 bruto e o 8000102 granulado feito dele).
        # 🌟 CORRIGIDO (16/10/2026): só conta como insumo quando a cadeia do outro produto consome a MAIOR PARTE (>= 50%)
        # da produção dele. Reaproveitamento / recirculação entre produtos paralelos (ex.: finos de um FeCr refundidos no
        # forno de outro, os círculos RC) não tira ninguém do consolidado.
        def ops_a_montante(ops_ini):
            vistos_o, fila = set(ops_ini), list(ops_ini)
            while fila:
                op = fila.pop()
                for cons_id, qtd in consumo[op].items():
                    if qtd <= 0:
                        continue
                    for p in operacoes_por_codigo.get(itens_consumo[cons_id]['codigo'], []):
                        if p not in vistos_o:
                            vistos_o.add(p)
                            fila.append(p)
            return vistos_o
        cadeia_ops = {i: ops_a_montante(ops_i) for i, ops_i in prods}
        def consumo_por(codigo, ops_consumidoras, excluir):
            return sum(q for op in ops_consumidoras if op not in excluir
                       for cid, q in consumo[op].items() if q > 0 and itens_consumo[cid]['codigo'] == codigo)
        fora_cons = []
        # 🌟 NOVO (16/10/2026): o PORQUÊ do consolidado (criado ou não) fica no resultado -- grafo['consolidado']
        info_cons['produtos'] = [{'codigo': itens_producao[i]['codigo'], 'unidade': itens_producao[i]['unidade'],
                                  'producao': sum(producao[o] for o in ops_i)} for i, ops_i in prods]
        partes = {}
        for i, ops_i in prods:
            cod_i, prod_i = itens_producao[i]['codigo'], sum(producao[o] for o in ops_i)
            for j, _x in prods:
                if j == i or prod_i <= 0:
                    continue
                partes[(i, j)] = consumo_por(cod_i, cadeia_ops[j], set(ops_i)) / prod_i
                info_cons['consumo_cruzado'].append({'item': cod_i, 'na_cadeia_de': itens_producao[j]['codigo'], 'parte': partes[(i, j)]})
        # 🌟 CORRIGIDO (16/10/2026): com recirculação (ex.: finos que voltam aos fornos passando pela britagem, que tritura
        # os DOIS produtos), a cadeia de cada produto "contém" o outro -- os dois aparecem consumidos ~100% um na cadeia
        # do outro. Isso é MÚTUO e quer dizer produtos paralelos: ninguém sai. Só sai o produto A que a cadeia de B
        # consome (>= 50%) SEM que a cadeia de A consuma B do mesmo jeito (ex.: bruto -> granulado).
        for (i, j), parte in partes.items():
            if parte >= PARTE_MINIMA_INSUMO_CONSOLIDADO and partes.get((j, i), 0.0) < PARTE_MINIMA_INSUMO_CONSOLIDADO and i not in fora_cons:
                fora_cons.append(i)
        prods = [(i, o) for i, o in prods if i not in fora_cons]
        # 🌟 CORRIGIDO (16/10/2026): unidade comparada sem espaços e sem diferença de maiúsculas ('T', 't ', ...)
        unidades = {str(itens_producao[i]['unidade'] or '').strip().upper() for i, _o in prods}
        info_cons['fora'] = [itens_producao[i]['codigo'] for i in fora_cons]
        if len(prods) < 2:
            info_cons['motivo'] = 'menos de 2 produtos para somar' + (' (fora: insumo de outro produto)' if fora_cons else '')
        elif len(unidades) != 1:
            info_cons['motivo'] = 'unidades diferentes: ' + ', '.join(sorted(unidades))
        if len(prods) >= 2 and len(unidades) == 1:
            info_cons['criado'] = True
            info_cons['motivo'] = 'criado'
            nid_c = 'item:__CONSOLIDADO__'
            nos[nid_c] = {'id': nid_c, 'tipo': 'item', 'agrupa_final': True, 'consolidado_geral': True,
                          'item_codigo': 'CONSOLIDADO',
                          'item_descricao': ' + '.join(itens_producao[i]['codigo'] for i, _o in prods),
                          'unidade': itens_producao[prods[0][0]]['unidade'], 'producao': sum(producao[o] for _i, ops_i in prods for o in ops_i),
                          'membros': [f"op:{o[0]}:{o[1]}" for _i, ops_i in prods for o in ops_i],
                          'produtos': [itens_producao[i]['codigo'] for i, _o in prods],
                          'fora_consolidado': [itens_producao[i]['codigo'] for i in fora_cons]}
            for item_id, ops_i in prods:
                codigo_i = itens_producao[item_id]['codigo']
                origem = f"item:{codigo_i}" if len(ops_i) > 1 and f"item:{codigo_i}" in nos else no_operacao(ops_i[0])
                aresta(origem, nid_c, 'producao', sum(producao[o] for o in ops_i), itens_producao[item_id]['unidade'])

    # Total consumido de cada matéria-prima (soma das ligações que saem dela)
    for a in arestas:
        n = nos.get(a['de'])
        if n and n['tipo'] == 'mp':
            n['total'] = n.get('total', 0.0) + (a['qtd'] or 0.0)
    for a in arestas:
        n = nos.get(a['para'])
        if n and n['tipo'] == 'sub':
            n['total'] = n.get('total', 0.0) + (a['qtd'] or 0.0)

    # 🌟 ALTERADO: + o GERADO COMO SUBPRODUTO (consumo negativo em outros equipamentos), que entra junto com o produzido:
    #    estoque/perdas = produzido + gerado como subproduto - consumo neste fluxo - consumo de outros produtos.
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
    # 🌟 NOVO (16/10/2026): quem DO DESENHO consome cada item (consumo > 0) e o que cada equipamento do desenho produz --
    # para mostrar, no balanço, QUEM consumiu "neste fluxo" e o que VOLTA (recirculação) para os equipamentos do item.
    codigo_do_cid = {cid: ic['codigo'] for cid, ic in itens_consumo.items()}
    consome_no_fluxo = defaultdict(lambda: defaultdict(float))       # código -> op do desenho -> qtde consumida
    for op in ops_no_fluxo:
        for cid, q in consumo[op].items():
            if q > 0:
                consome_no_fluxo[codigo_do_cid[cid]][op] += q

    def a_frente(codigo):
        # equipamentos do desenho à frente do item: quem o consome, quem consome o que esses produzem, e assim por diante
        vistos, fila = set(), list(consome_no_fluxo.get(codigo, {}))
        while fila:
            op = fila.pop()
            if op in vistos:
                continue
            vistos.add(op)
            fila.extend(o for o in consome_no_fluxo.get(itens_producao[op[0]]['codigo'], {}) if o not in vistos)
        return vistos

    def consumidores_e_devolvido(codigo):
        prod_do_item = [o for o in operacoes_por_codigo.get(codigo, []) if o in ops_no_fluxo]
        consumidores = []
        for op, q in sorted(consome_no_fluxo.get(codigo, {}).items(), key=lambda kv: -kv[1]):
            consumidores.append({'grupo': grupos[op[1]]['codigo'], 'grupo_nome': grupos[op[1]]['nome'],
                                 'produto': itens_producao[op[0]]['codigo'], 'descricao': itens_producao[op[0]]['descricao'],
                                 'qtd': q, 'proprio': op in prod_do_item})   # proprio: o equipamento do item (recirculação nele mesmo)
        frente = a_frente(codigo) - set(prod_do_item)
        codigos_frente = {itens_producao[o[0]]['codigo'] for o in frente}
        devolvido = {}
        for p in prod_do_item:
            for cid, q in consumo[p].items():
                c = codigo_do_cid[cid]
                if q > 0 and c in codigos_frente:
                    ic = itens_consumo[cid]
                    r = devolvido.setdefault(c, {'item': c, 'descricao': ic['descricao'], 'unidade': ic['unidade'], 'qtd': 0.0, 'para': set()})
                    r['qtd'] += q
                    r['para'].add(grupos[p[1]]['codigo'])
        dev = [dict(r, para=', '.join(sorted(r['para']))) for r in sorted(devolvido.values(), key=lambda r: -r['qtd'])]
        return consumidores, dev

    for codigo in codigos_do_desenho:
        produzido = sum(producao[o] for o in operacoes_por_codigo.get(codigo, []))
        neste_fluxo, outros, geradores = 0.0, {}, {}
        for op, itens in consumo.items():
            # 🌟 21/10/2026: gerado (negativo) e consumido (positivo) contam separados -- não se anulam
            qs = [itens.get(cid, 0.0) for cid in cons_ids_por_codigo[codigo]]
            gerado_op = sum(q for q in qs if q < 0)
            qtd = sum(q for q in qs if q > 0)
            if gerado_op < 0:
                # 🌟 NOVO: consumo negativo = o item foi GERADO como subproduto nesse equipamento (ex.: parte da produção
                # de outro produto, fora de especificação, desviada para este item -- o negativo é a bonificação no
                # custo do equipamento). Entra no balanço como entrada, ao lado do produzido.
                prod = itens_producao[op[0]]
                reg = geradores.setdefault(prod['codigo'], {'produto': prod['codigo'], 'descricao': prod['descricao'],
                                                            'grupos': set(), 'qtd': 0.0})
                reg['grupos'].add(grupos[op[1]]['codigo'])
                reg['qtd'] += -gerado_op
            if qtd == 0:
                continue
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
        lista_ger = sorted(geradores.values(), key=lambda r: -r['qtd'])
        gerado = sum(r['qtd'] for r in lista_ger)
        balancos[codigo] = {
            'produzido': produzido, 'gerado_subproduto': gerado, 'neste_fluxo': neste_fluxo, 'outros_produtos': total_outros,
            'sem_consumo': produzido + gerado - neste_fluxo - total_outros,
            'outros': [{**r, 'grupos': ', '.join(sorted(r['grupos']))} for r in lista[:50]],
            'outros_total_itens': len(lista),
            'geradores': [{**r, 'grupos': ', '.join(sorted(r['grupos']))} for r in lista_ger[:50]],
            'geradores_total_itens': len(lista_ger),
        }
        balancos[codigo]['consumidores_fluxo'], balancos[codigo]['devolvido_fluxo'] = consumidores_e_devolvido(codigo)
    # 🌟 ALTERADO: o balanço vai em TODAS as caixas. No equipamento cujo item também é feito em outros grupos de
    # máquina, o balanço é o do ITEM (todos os grupos juntos) -- o consumo registra o item, não quem o fez --, e o
    # nó leva balanco_do_item=True pra tela dizer isso.
    for n in nos.values():
        if n['tipo'] in ('item', 'op'):
            n['balanco'] = balancos.get(n['item_codigo'])
            if n.get('consolidado_geral'):
                # 🌟 NOVO (16/10/2026): soma dos balanços dos produtos (o detalhe de quem gerou / consumiu fica em cada produto)
                bs = [balancos[c] for c in n['produtos'] if c in balancos]
                n['balanco'] = {k: sum(b[k] for b in bs) for k in ('produzido', 'gerado_subproduto', 'neste_fluxo', 'outros_produtos', 'sem_consumo')}
                n['balanco'].update({'outros': [], 'outros_total_itens': 0, 'geradores': [], 'geradores_total_itens': 0})
                n['balanco']['consumidores_fluxo'] = [dict(x, de=c) for c in n['produtos'] if c in balancos for x in balancos[c].get('consumidores_fluxo', [])]
                n['balanco']['devolvido_fluxo'] = [dict(x, de=c) for c in n['produtos'] if c in balancos for x in balancos[c].get('devolvido_fluxo', [])]
            if n['tipo'] == 'op' and len(operacoes_por_codigo.get(n['item_codigo'], [])) > 1:
                n['balanco_do_item'] = True

    # 🌟 NOVO: custos (total, variável, fixo/outros) de cada operação do desenho e dos produtos escolhidos.
    # 🌟 ALTERADO: conta (Conta Contábil x Centro de Custo) com GGF no período e tipo fora de V/F/O (ex.: A =
    # aguardando caracterização) BLOQUEIA o custo -- mesma regra da Genealogia (custo_ferbasa/genealogia.py), pra
    # as duas nunca divergirem. O desenho do fluxo sai normalmente; só os custos (círculo C) não.
    invalidas = {}
    for g in ggf_linhas or []:
        if g.get('tipo') not in ('V', 'F', 'O'):
            invalidas.setdefault(g.get('conta_cc_id'), '{} (tipo {})'.format(g.get('conta_descricao') or 'conta id %s' % g.get('conta_cc_id'), g.get('tipo')))
    if invalidas:
        lista = sorted(invalidas.values())
        custos_produtos = []
        aviso_custo = ('Custos NÃO calculados: no período há GGF lançado em conta(s) (Conta Contábil x Centro de Custo) com '
                       'tipo diferente de V, F ou O. Classifique-as e monte o fluxo de novo. Contas: ' +
                       '; '.join(lista[:20]) + ('; ... e mais %d' % (len(lista) - 20) if len(lista) > 20 else ''))
    else:
        custos_produtos, aviso_custo = calcular_custos(
            nos, producao, consumo, valores, ordens_com_producao, operacoes_por_codigo, itens_producao, itens_consumo,
            grupos, ggf_linhas or [], ids_selecionados)

    _consolidar_agrupamentos_finais(nos)

    return {
        'consolidado': info_cons,
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
#     ordens com produção): variável = valor x % (V: % da conta/CC; F: 100 - %; O: 0). Conta com outro tipo (ex.: A)
#     bloqueia o cálculo dos custos (ver montar_grafo);
#   - fixo/outros = total - variável; tudo dividido pela produção do equipamento no período (custo por unidade).
#   Como na procedure, só entram linhas com produção > 0.
# ---------------------------------------------------------------------------------------------------------------
def _parte_variavel_ggf(valor, tipo, percentual):
    """Parcela variável do GGF distribuído -- mesma regra da procedure custo_variavel_adicionado."""
    if tipo == 'F':
        pct = 100.0 - _num(percentual)
    elif tipo == 'O' or not tipo:
        pct = 0.0
    else:                       # 'V' (tipo fora de V/F/O nem chega aqui: bloqueia antes, em montar_grafo)
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
            op_no = (int(nid.split(':')[1]), int(nid.split(':')[2]))
            c = por_unidade(op_no)
            soma_no = c.pop('_soma')
            no['custo'] = c
            # 🌟 NOVO (14/10/2026): estrutura do custo contábil do equipamento -- produção, soma (total, variável) e cada
            # insumo consumido [código, qtde, valor total, valor variável]. O chat usa para refazer o gráfico em escada e
            # a contribuição de cada equipamento quando entram caixas pelo círculo MV (mesma conta de _contribuicoes).
            ins, sub = {}, {}
            for cons_id, qtd in consumo[op_no].items():
                if qtd == 0:
                    continue
                t, v = valores[op_no].get(cons_id, (0.0, 0.0))
                ic = itens_consumo[cons_id]
                destino = ins if qtd > 0 else sub
                # 🌟 [código, qtde (positiva), valor total, valor variável, descrição, unidade] -- subproduto: valores negativos
                r = destino.setdefault(ic['codigo'], [ic['codigo'], 0.0, 0.0, 0.0, ic.get('descricao') or '', ic.get('unidade') or ''])
                r[1] += abs(qtd)
                r[2] += t
                r[3] += v
            no['cx'] = {'p': producao[op_no], 's': [soma_no[0], soma_no[1]], 'i': list(ins.values()), 'g': list(sub.values())}

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
PARTE_MINIMA_INSUMO_CONSOLIDADO = 0.5   # 🌟 16/10/2026: ver montar_grafo (consolidado dos produtos)
CORTE_ESCADA = 0.01       # no gráfico, quem contribui com menos de 1% do custo do produto vai para "Demais equipamentos"


def _consolidar_agrupamentos_finais(nos):
    """
    🌟 NOVO (16/10/2026): custo CONSOLIDADO na caixa de agrupamento do produto final (agrupa_final) -- a soma dos
    equipamentos que fazem o produto, ponderada pela produção de cada um: n['custo'] (mesma forma do custo do
    equipamento: materiais, subprodutos, GGF, unitário total e variável), n['cx'] (produção, soma e os itens
    consumidos / gerados somados pelo código) e n['custo']['escada'] (as barras de cada equipamento x participação na
    produção -- a soma das barras é o custo consolidado). Total = sum(custo_k x produção_k) / produção somada.
    """
    for nid, n in nos.items():
        # 🌟 ALTERADO (16/10/2026): TODA caixa de item de vários grupos de máquina (também as do meio do fluxo, ex.: o
        # 8050001 dos fornos antes da britagem) ganha o custo consolidado dos equipamentos que fazem o item.
        if n.get('tipo') != 'item':
            continue
        ids_m = set(n.get('membros') or [])
        membros = [m for m in nos.values() if m.get('tipo') == 'op' and (m['id'] in ids_m if ids_m else m.get('item_codigo') == n['item_codigo'])
                   and (m.get('final') or not n.get('agrupa_final')) and (m.get('custo') or {}).get('producao', 0) > 0]
        P = sum(m['custo']['producao'] for m in membros)
        if not membros or P <= 0:
            continue
        def media(chave, sub):
            return sum(m['custo'][chave][sub] * m['custo']['producao'] for m in membros) / P
        custo = {'producao': P}
        for chave in ('materiais', 'sub', 'ggf'):
            custo[chave] = {'total': media(chave, 'total'), 'variavel': media(chave, 'variavel')}
        custo['unit_total'] = sum(m['custo']['unit_total'] * m['custo']['producao'] for m in membros) / P
        custo['unit_variavel'] = sum(m['custo']['unit_variavel'] * m['custo']['producao'] for m in membros) / P
        custo['membros'] = [m['id'] for m in membros]
        barras = {}
        for m in membros:
            esc = (m['custo'] or {}).get('escada')
            if not esc:
                continue
            w = m['custo']['producao'] / P
            for b in esc['barras']:
                r = barras.get(b['nome'])
                if r is None:
                    r = barras[b['nome']] = dict(b, total=0.0, variavel=0.0, necessidade=0.0)
                r['distancia'] = max(r['distancia'], b['distancia'])
                r['total'] += b['total'] * w
                r['variavel'] += b['variavel'] * w
                r['necessidade'] += b['necessidade'] * w
        if barras:
            custo['escada'] = {'barras': sorted(barras.values(), key=lambda b: (-b['distancia'], b['nome'])),
                               'corte': CORTE_ESCADA,
                               'final': {'total': custo['unit_total'], 'variavel': custo['unit_variavel']}}
        n['custo'] = custo
        cxs = [m['cx'] for m in membros if m.get('cx')]
        if cxs:
            def junta(chave):
                acc = {}
                for cx in cxs:
                    for x in cx.get(chave) or []:
                        r = acc.get(x[0])
                        if r is None:
                            r = acc[x[0]] = [x[0], 0.0, 0.0, 0.0, x[4], x[5]]
                        r[1] += x[1]
                        r[2] += x[2]
                        r[3] += x[3]
                return list(acc.values())
            n['cx'] = {'p': sum(cx['p'] for cx in cxs), 's': [sum(cx['s'][0] for cx in cxs), sum(cx['s'][1] for cx in cxs)],
                       'i': junta('i'), 'g': junta('g')}


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
    linhas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf = _carregar_periodo(
        empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim)
    grafo = montar_grafo(linhas, itens_producao, itens_consumo, set(ids_selecionados),
                         ggf_linhas=ggf_linhas, pct_var_ggf=pct_var_ggf)
    # 🌟 NOVO (14/10/2026): custo do produto final mês a mês (gráfico de linhas da evolução do custo)
    grafo['custo_mensal'] = custo_mensal_consolidado(linhas, itens_producao, itens_consumo, ids_selecionados,
                                                     ggf_linhas, pct_var_ggf)
    # 🌟 NOVO (15/10/2026): evolução mensal do custo de CADA equipamento do desenho (botão do círculo C)
    meses = sorted({l['pro_men_ano_mes'] for l in linhas})
    ids_ops = {n['id'] for n in grafo['nos'] if n['tipo'] == 'op'}
    grafo['meses'] = meses
    grafo['custo_mensal_op'] = _evolucao_ops_compacta(
        _custos_mensais_ops(linhas, ggf_linhas, itens_consumo, pct_var_ggf), ids_ops, meses)
    # 🌟 NOVO (16/10/2026): evolução mensal de cada matéria-prima / subproduto de cada equipamento (círculo MP / SP e
    # balanço do link "Produção")
    grafo['itens_mensal_op'] = _itens_mensais_compacta(_itens_mensais_ops(linhas, itens_consumo, pct_var_ggf), ids_ops, meses)
    return grafo


def _carregar_periodo(empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim):
    """Lê do banco o que montar_grafo precisa (Produção Mensal do período, itens, grupos, GGF distribuído, % variável)."""
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
         'percentual': g['dis_ggf_men_conta_cc__con_con_cen_cus_percentual'],
         'conta_cc_id': g['dis_ggf_men_conta_cc_id'],
         'conta_descricao': '{} {} / CC {}'.format(g['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo'] or '',
                                                   g['dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao'] or '',
                                                   g['dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo'] or '').strip()}
        for g in (_da_empresa(TbDistribuicaoGGFMensal, empresa_id)
                  .filter(dis_ggf_men_ano_mes__gte=ano_mes_inicio, dis_ggf_men_ano_mes__lte=ano_mes_fim,
                          dis_ggf_men_item_producao_id__in=ids_prod, dis_ggf_men_grupo_maquina_id__in=ids_grupo)
                  .order_by()
                  .values('dis_ggf_men_ano_mes', 'dis_ggf_men_ordem_producao', 'dis_ggf_men_item_producao_id',
                          'dis_ggf_men_grupo_maquina_id', 'dis_ggf_men_valor',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_tipo',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_percentual', 'dis_ggf_men_conta_cc_id',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao',
                          'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo'))
    ]
    return linhas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf


# ---------------------------------------------------------------------------------------------------------------
# 🌟 NOVO: FLUXO REAL POR ROTA
#
# A partir da mesma Produção Mensal, um fluxo para cada ROTA do produto -- cada equipamento real (grupo de máquina)
# onde o produto final é produzido --, só com equipamentos reais:
#   - os equipamentos VAZIO (reclassificações manuais de estoque) são DESCONSIDERADOS: as linhas deles saem e a
#     ligação que passava por eles não é refeita (quem consumiu um item continua ligado só a quem o produziu de
#     verdade; se o item só existiu por reclassificação, a cadeia para ali) -- decisão de 13/10/2026;
#   - o consumo entre um equipamento e outro é o consumo específico real do período (quantidade consumida /
#     produção do equipamento que consome), como no fluxo da Produção Mensal.
# ---------------------------------------------------------------------------------------------------------------
GRUPO_VAZIO = 'VAZIO'


def remover_vazio(linhas):
    """As linhas da Produção Mensal sem as dos equipamentos VAZIO (reclassificações)."""
    return [l for l in linhas if ((l.get('grupo') or {}).get('codigo') or '') != GRUPO_VAZIO]


NOME_MOVIMENTACAO = 'Movimentação'


def _eh_vazio(l):
    return ((l.get('grupo') or {}).get('codigo') or '') == GRUPO_VAZIO


def filtrar_vazio_rotas(linhas, itens_producao):
    """
    🌟 NOVO (13/10/2026): para o fluxo real por rota. A movimentação VAZIO de um item que NÃO tem produção em
    equipamento real no período é o único caminho até quem o fez de verdade (ex.: 6000130 <- 6000160 da mineração;
    carvão 7170007 <- 7170005): ela FICA, aparece no desenho como um quadrado "Movimentação" (com o GGF dela, se
    houver -- frete, por exemplo) e a cadeia segue para trás dela. A movimentação VAZIO de um item que TAMBÉM é
    produzido em equipamento real continua desconsiderada (é só reclassificação; não vira rota).
    """
    producao_real = defaultdict(float)
    vistos = set()
    for l in linhas:
        if _eh_vazio(l):
            continue
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            producao_real[itens_producao[l['pro_men_item_producao_id']]['codigo']] += _num(l['pro_men_qtde_produzida'])
    saida = []
    for l in linhas:
        if _eh_vazio(l):
            if producao_real.get(itens_producao[l['pro_men_item_producao_id']]['codigo'], 0.0) > 0:
                continue
            l = dict(l)
            l['grupo'] = dict(l.get('grupo') or {}, codigo=GRUPO_VAZIO, nome=NOME_MOVIMENTACAO)
        saida.append(l)
    return saida


def _custos_ops(linhas, ggf_linhas):
    """
    Custo TOTAL de cada operação (item, grupo) com a mesma conta de montar_grafo/calcular_custos:
    soma de (material + GGF do item consumido) x qtde das linhas com produção + GGF distribuído das ordens com
    produção. Devolve ({op: total}, {op: produção}, {op: [linhas da op]}).
    """
    producao, vistos = defaultdict(float), set()
    total = defaultdict(float)
    ordens = defaultdict(set)
    por_op = defaultdict(list)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        por_op[op].append(l)
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            producao[op] += _num(l['pro_men_qtde_produzida'])
        if _num(l['pro_men_qtde_produzida']) > 0:
            total[op] += (_num(l.get('pro_men_valor_material')) + _num(l.get('pro_men_valor_ggf'))) * _num(l['pro_men_qtde_consumo'])
            ordens[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))
    ggf = defaultdict(float)
    for g in ggf_linhas or []:
        op = (g['item_id'], g['grupo_id'])
        if (g['ano_mes'], g['ordem']) in ordens.get(op, ()):
            ggf[op] += _num(g['valor'])
    return {op: total[op] + ggf[op] for op in producao}, producao, por_op


def _ordem_de_tras_pra_frente(linhas, itens_producao, itens_consumo, codigo_final):
    """Códigos dos itens da cadeia do produto final, cada um DEPOIS dos que ele consome (recirculação: corta)."""
    produtores, entradas = defaultdict(set), defaultdict(set)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        produtores[itens_producao[op[0]]['codigo']].add(op)
        if _num(l['pro_men_qtde_consumo']) > 0:
            entradas[op].add(itens_consumo[l['pro_men_item_consumo_id']]['codigo'])
    ordem, visitados = [], {codigo_final}
    pilha = [(codigo_final, iter(sorted({c for op in produtores[codigo_final] for c in entradas[op]})))]
    while pilha:
        codigo, filhos = pilha[-1]
        proximo = next((c for c in filhos if c in produtores and c not in visitados), None)
        if proximo is None:
            pilha.pop()
            ordem.append(codigo)
        else:
            visitados.add(proximo)
            pilha.append((proximo, iter(sorted({c for op in produtores[proximo] for c in entradas[op]}))))
    return ordem


def carregar_rotas_reais(empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim):
    """
    Um fluxo por rota: para cada produto escolhido, um grafo (no formato de montar_grafo) para cada equipamento real
    que o produziu no período, do que mais produziu para o que menos produziu. Devolve [{'produto_id', 'grupo_codigo',
    'grupo_nome', 'producao', 'participacao', 'grafo'}].
    """
    import time
    inicio = time.time()
    linhas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf = _carregar_periodo(
        empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim)
    lido = time.time()
    _TEMPOS_ROTA.clear()
    rotas = montar_rotas_reais(linhas, itens_producao, itens_consumo, ids_selecionados, ggf_linhas, pct_var_ggf)
    print(f"[fluxo_real] {len(linhas)} linhas lidas em {lido - inicio:.1f}s; {len(rotas)} rota(s) montadas em "
          f"{time.time() - lido:.1f}s (círculo MV {_TEMPOS_ROTA['mv']:.1f}s; custo mês a mês {_TEMPOS_ROTA['mensal']:.1f}s)")
    return rotas


PARTICIPACAO_MINIMA_ROTA = 0.05   # 🌟 NOVO (14/10/2026): participação mínima de um equipamento no item para virar rota
MAX_ROTAS = 50


def _distancia_ao_final(grafo):
    """Distância (em ligações) de cada nó até o produto final, andando de trás pra frente."""
    antes = defaultdict(list)
    for ar in grafo['arestas']:
        if ar['tipo'] != 'ciclo':
            antes[ar['para']].append(ar['de'])
    dist, fila = {}, [n['id'] for n in grafo['nos'] if n.get('final')]
    for nid in fila:
        dist[nid] = 0
    while fila:
        atual = fila.pop(0)
        for anterior in antes[atual]:
            if anterior not in dist:
                dist[anterior] = dist[atual] + 1
                fila.append(anterior)
    return dist


def _rotas_por_agrupamento(linhas, itens_producao, itens_consumo, produto_id):
    """
    🌟 ALTERADO (regra de 13/10/2026): cada AGRUPAMENTO -- item produzido por mais de um equipamento real -- divide a
    cadeia; cada combinação de equipamentos escolhidos nos agrupamentos é uma ROTA. Vindo do produto final para trás:
      rotas(item)  = uma escolha para cada equipamento que produz o item (se for só um, não divide);
      rotas(equip) = combinações das rotas de cada item que ele consome (os produzidos na cadeia).
    Participação da rota = produto das participações (produção no período) de cada equipamento escolhido no seu
    agrupamento. Devolve [(escolhas {código do item: op escolhida}, participação)], da maior para a menor, no
    máximo MAX_ROTAS, e o total de rotas encontradas.
    """
    producao_op, vistos = defaultdict(float), set()
    entradas = defaultdict(set)                        # op -> códigos de itens consumidos (consumo > 0)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            producao_op[op] += _num(l['pro_men_qtde_produzida'])
        if _num(l['pro_men_qtde_consumo']) > 0:
            entradas[op].add(itens_consumo[l['pro_men_item_consumo_id']]['codigo'])
    produtores = defaultdict(list)                     # código do item -> [(op, produção)]
    for op, prod in producao_op.items():
        if prod > 0:
            produtores[itens_producao[op[0]]['codigo']].append((op, prod))

    # 🌟 NOVO (14/10/2026): um agrupamento só DIVIDE as rotas se acontece na MESMA ÁREA do equipamento que faz o produto
    # final -- área = os 3 primeiros caracteres do código do grupo de máquina (F01, F02, F16...). Ex.: 8000102 é feito
    # na F01BTAC (área F01): o 8050001 (fornos F01FN..) divide; o concentrado Comisa (F02CCOIT / F02CPED) e o lump
    # Jacurici (F16BRX / F16BJIG / F16BSM) não dividem -- entram com todos os equipamentos juntos (valor contábil).
    area_do_grupo = {}
    for l in linhas:
        area_do_grupo[l['pro_men_grupo_maquina_id']] = ((l.get('grupo') or {}).get('codigo') or '')[:3].upper()
    codigo_final_ = itens_producao[produto_id]['codigo']
    areas_finais = {area_do_grupo.get(op[1], '') for op, _p in produtores.get(codigo_final_, [])}

    def na_area(codigo):
        lista = produtores.get(codigo, [])
        return len(lista) > 1 and all(area_do_grupo.get(op[1], '') in areas_finais for op, _p in lista)

    # 🌟 NOVO (14/10/2026): PARTICIPAÇÃO MÍNIMA para dividir. Num agrupamento da área, cada equipamento com pelo menos
    # PARTICIPACAO_MINIMA_ROTA (5%) da produção do item no período vira uma rota; os abaixo disso entram JUNTO com o
    # maior (mesma rota). Se sobrar só um equipamento com 5% ou mais, o item não divide (todos juntos). Os grupos
    # repartem os equipamentos do item, então a média das rotas continua igual ao valor contábil.
    membros = {}                                       # (código, op líder) -> {ops do grupo}
    dividem = set()
    for codigo in list(produtores):
        if not na_area(codigo):
            continue
        lista = produtores[codigo]
        total = sum(p for _op, p in lista)
        grandes = [(op, p) for op, p in lista if total > 0 and p / total >= PARTICIPACAO_MINIMA_ROTA]
        if len(grandes) < 2:
            continue
        maior = max(grandes, key=lambda x: x[1])[0]
        prod_grupo = {op: p for op, p in grandes}
        grupo = {op: {op} for op, _p in grandes}
        for op, p in lista:
            if op not in grupo:
                grupo[maior].add(op)
                prod_grupo[maior] += p
        for lider, ops in grupo.items():
            membros[(codigo, lider)] = ops
            entradas[lider] = set().union(*(entradas[o] for o in ops))
        produtores[codigo] = list(prod_grupo.items())
        dividem.add(codigo)

    def divide(codigo):
        return codigo in dividem

    contagem = {'total': 0}
    memo = {}

    def combinar(lista_a, lista_b):
        # 🌟 ALTERADO (14/10/2026 -- desempenho): as duas listas vêm da maior para a menor participação; guarda só as
        # MAX_ROTAS*4 maiores combinações (um "heap") e, para cada rota de lista_a, para de percorrer lista_b assim que
        # o produto fica menor que a menor das guardadas (daí pra frente só diminui).
        import heapq
        limite = MAX_ROTAS * 4
        heap, cont = [], 0
        for ca, pa in lista_a:
            if len(heap) >= limite and pa * (lista_b[0][1] if lista_b else 0) <= heap[0][0]:
                break
            for cb, pb in lista_b:
                prod = pa * pb
                if len(heap) >= limite and prod <= heap[0][0]:
                    break
                junta = dict(ca)
                ok = True
                for k, v in cb.items():
                    if k in junta and junta[k] != v:     # o mesmo item escolhido diferente em dois ramos: incoerente
                        ok = False
                        break
                    junta[k] = v
                if ok:
                    cont += 1
                    if len(heap) < limite:
                        heapq.heappush(heap, (prod, cont, junta))
                    else:
                        heapq.heapreplace(heap, (prod, cont, junta))
        saida = [(j, p) for p, _c, j in heap]
        saida.sort(key=lambda x: -x[1])
        return saida

    def rotas_item(codigo, caminho):
        if codigo in caminho:                          # recirculação: não abre de novo
            return [({}, 1.0)]
        chave = (codigo, caminho)
        if chave in memo:
            return memo[chave]
        lista = produtores.get(codigo, [])
        if not lista:
            return [({}, 1.0)]                         # comprado (ou só reclassificado): a cadeia para aqui
        if len(lista) > 1 and not divide(codigo):
            # agrupamento fora da área: não divide -- segue para trás com todos os equipamentos juntos
            sub = [({}, 1.0)]
            for c in sorted({c for op, _p in lista for c in entradas[op]}):
                if c in produtores:
                    sub = combinar(sub, rotas_item(c, caminho | {codigo}))
            memo[chave] = sub
            return sub
        total = sum(p for _op, p in lista)
        saida = []
        for op, prod in sorted(lista, key=lambda x: -x[1]):
            sub = [({}, 1.0)]
            for c in sorted(entradas[op]):
                if c in produtores:
                    sub = combinar(sub, rotas_item(c, caminho | {codigo}))
            for esc, part in sub:
                esc = dict(esc)
                esc[codigo] = op
                saida.append((esc, part * prod / total))
        saida.sort(key=lambda x: -x[1])
        memo[chave] = saida[:MAX_ROTAS * 4]
        return memo[chave]

    def contar(codigo, caminho, memo_contagem={}):
        """Número EXATO de rotas (sem montar todas): soma pelos equipamentos, multiplica pelos itens consumidos."""
        if codigo in caminho or not produtores.get(codigo):
            return 1
        chave = (codigo, caminho)
        if chave not in memo_contagem and len(produtores[codigo]) > 1 and not divide(codigo):
            m = 1
            for c in {c for op, _p in produtores[codigo] for c in entradas[op]}:
                if c in produtores:
                    m *= contar(c, caminho | {codigo})
            memo_contagem[chave] = m
        if chave not in memo_contagem:
            n = 0
            for op, _prod in produtores[codigo]:
                m = 1
                for c in entradas[op]:
                    if c in produtores:
                        m *= contar(c, caminho | {codigo})
                n += m
            memo_contagem[chave] = n
        return memo_contagem[chave]

    codigo_final = itens_producao[produto_id]['codigo']
    todas = rotas_item(codigo_final, frozenset())
    return todas[:MAX_ROTAS], contar(codigo_final, frozenset(), {}), produtores, membros


MAX_PRODUTORES_ORIGEM = 15     # por item consumido por uma movimentação: os equipamentos reais que mais produziram
MAX_FRAGMENTOS_MV = 600        # por rota: quantos equipamentos podem ter o pedaço de fluxo do círculo MV pronto


def _fragmentos_vazio(linhas_todas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf):
    """
    🌟 NOVO (13/10/2026): no fluxo real os equipamentos VAZIO (movimentações) ficam de fora -- e com eles os insumos
    que só existem por movimentação (ex.: 6000130 <- VAZIO <- 6000160 da mineração). O círculo MV de cada equipamento
    põe no desenho, UM NÍVEL por clique, quem entrega a ele:
      - equipamento real: as movimentações VAZIO que produziram os insumos dele SEM produção em equipamento real no
        período;
      - movimentação VAZIO: quem produziu cada item que ela consumiu (outras movimentações VAZIO e os equipamentos
        reais que mais produziram o item).
    Cada caixa que entra tem o seu próprio círculo MV -- o usuário vai clicando até onde quiser. Só desenho: não muda
    rota nem custo da rota. Devolve (pedaco(op) -> {'n', 'nos', 'arestas'} ou None, qtd_mv(op) -> int).
    """
    def vazio_linha(l):
        return ((l.get('grupo') or {}).get('codigo') or '') == GRUPO_VAZIO
    linhas_op = defaultdict(list)
    prod_real_op = defaultdict(float)
    eh_vazio = set()
    tem_vazio, tem_real, vistos = set(), set(), set()
    ops_vazio_codigo, ops_real_codigo = defaultdict(set), defaultdict(set)
    for l in linhas_todas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        codigo = itens_producao[op[0]]['codigo']
        linhas_op[op].append(l)
        if vazio_linha(l):
            eh_vazio.add(op)
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave in vistos:
            continue
        vistos.add(chave)
        if _num(l['pro_men_qtde_produzida']) <= 0:
            continue
        if vazio_linha(l):
            tem_vazio.add(codigo)
            ops_vazio_codigo[codigo].add(op)
        else:
            tem_real.add(codigo)
            ops_real_codigo[codigo].add(op)
            prod_real_op[op] += _num(l['pro_men_qtde_produzida'])
    so_vazio = tem_vazio - tem_real            # itens que só existem por movimentação no período

    def entradas(op):
        """Códigos que o clique no MV abre e as linhas do equipamento que os consomem."""
        linhas = [l for l in linhas_op.get(op, []) if _num(l['pro_men_qtde_consumo']) > 0]
        if op in eh_vazio:
            linhas = [l for l in linhas if itens_consumo[l['pro_men_item_consumo_id']]['codigo'] in (tem_vazio | tem_real)]
        else:
            linhas = [l for l in linhas if itens_consumo[l['pro_men_item_consumo_id']]['codigo'] in so_vazio]
        return {itens_consumo[l['pro_men_item_consumo_id']]['codigo'] for l in linhas}, linhas

    contagem = {}

    def qtd_mv(op):
        if op not in contagem:
            contagem[op] = len(entradas(op)[0])
        return contagem[op]

    cache = {}

    def pedaco(op):
        if op in cache:
            return cache[op]
        cache[op] = None
        codigos, linhas = entradas(op)
        if not codigos:
            return None
        frag, incluidas = list(linhas), {op}
        for codigo in sorted(codigos):
            produtores = list(ops_vazio_codigo.get(codigo, ()))
            if op in eh_vazio:
                produtores += sorted(ops_real_codigo.get(codigo, ()), key=lambda o: -prod_real_op[o])[:MAX_PRODUTORES_ORIGEM]
            for pr in produtores:
                if pr not in incluidas:
                    incluidas.add(pr)
                    frag.extend(linhas_op[pr])
        try:
            g = montar_grafo(frag, itens_producao, itens_consumo, {op[0]}, ggf_linhas=ggf_linhas, pct_var_ggf=pct_var_ggf,
                              agrupar_finais=False)   # pedaço do MV: sem caixa de agrupamento
        except FluxoProducaoMensalError:
            return None
        id_op = 'op:%s:%s' % op
        nos = []
        for n in g['nos']:
            if n['id'] == id_op:
                continue
            n = dict(n)
            n['final'] = False
            if n.get('custo'):
                n['custo'] = {k: v for k, v in n['custo'].items() if k != 'contribuicoes'}
            n.pop('balanco', None)     # o pedaço não tem todos os consumidores do período: balanço sairia errado
            n['mv_origem'] = True
            if n['tipo'] == 'op':
                _p, a, b = n['id'].split(':')
                n['mv_n'] = qtd_mv((int(a), int(b)))
            nos.append(n)
        # só as ligações que chegam no equipamento clicado e as que formam o item de vários produtores
        cache[op] = {'n': len(codigos), 'nos': nos, 'arestas': g['arestas']}
        return cache[op]
    return pedaco, qtd_mv

def _escala_por_equipamento(linhas_rota, itens_producao, itens_consumo, escolhas, produtores):
    """
    🌟 NOVO (14/10/2026): {op: fator} para os equipamentos da rota que estão À FRENTE de algum agrupamento -- o fator
    é o produto das participações do equipamento escolhido em cada agrupamento que está atrás dele (cada agrupamento
    conta uma vez, mesmo que chegue por dois caminhos). Equipamento sem agrupamento atrás: fora do dicionário (fator 1).
    """
    participacao = {}
    for codigo, op in escolhas.items():
        lista = produtores.get(codigo) or []
        total = sum(p for _o, p in lista)
        if len(lista) > 1 and total > 0:
            participacao[codigo] = next((p for o, p in lista if o == op), 0.0) / total
    if not participacao:
        return {}
    produtores_rota, entradas, vistos = defaultdict(set), defaultdict(set), set()
    for l in linhas_rota:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            if _num(l['pro_men_qtde_produzida']) > 0:
                produtores_rota[itens_producao[op[0]]['codigo']].add(op)
        if _num(l['pro_men_qtde_consumo']) > 0:
            entradas[op].add(itens_consumo[l['pro_men_item_consumo_id']]['codigo'])
    escala = {}
    for op in {(l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id']) for l in linhas_rota}:
        atras, visitados, pilha = set(), {op}, [op]
        while pilha:
            atual = pilha.pop()
            for codigo in entradas.get(atual, ()):
                if codigo in participacao:
                    atras.add(codigo)
                for pr in produtores_rota.get(codigo, ()):
                    if pr not in visitados:
                        visitados.add(pr)
                        pilha.append(pr)
        if atras:
            k = 1.0
            for codigo in atras:
                k *= participacao[codigo]
            escala[op] = k
    return escala


def _custo_final_do_mes(linhas_mes, itens_producao, itens_consumo, produto_id, ggf_mes, pct_var_ggf):
    """
    🌟 NOVO (14/10/2026): custo por unidade do produto final num mês -- só as linhas dos equipamentos que fazem o
    produto final naquele mês (valor contábil dos insumos + GGF distribuído do mês), a mesma conta do período.
    """
    finais = [l for l in linhas_mes if l['pro_men_item_producao_id'] == produto_id]
    if not finais:
        return None
    try:
        g = montar_grafo(finais, itens_producao, itens_consumo, {produto_id}, ggf_linhas=ggf_mes, pct_var_ggf=pct_var_ggf)
    except FluxoProducaoMensalError:
        return None
    c = next(iter(g.get('custos_produtos') or []), None)
    if not c or c['producao'] <= 0:
        return None
    return {'mes': finais[0]['pro_men_ano_mes'], 'producao': c['producao'], 'total': c['unit_total'], 'variavel': c['unit_variavel']}


def custo_mensal_consolidado(linhas, itens_producao, itens_consumo, ids_selecionados, ggf_linhas, pct_var_ggf):
    """{código do produto: [{mes, producao, total, variavel}]} -- o custo do produto final mês a mês (fluxo completo)."""
    meses = sorted({l['pro_men_ano_mes'] for l in linhas})
    saida = {}
    for produto_id in ids_selecionados:
        if produto_id not in itens_producao:
            continue
        lista = []
        for m in meses:
            r = _custo_final_do_mes([l for l in linhas if l['pro_men_ano_mes'] == m], itens_producao, itens_consumo, produto_id,
                                    [g for g in ggf_linhas or [] if g['ano_mes'] == m], pct_var_ggf)
            if r:
                lista.append(r)
        saida[itens_producao[produto_id]['codigo']] = lista
    return saida


def _estrutura_mes(linhas_mes, ggf_mes, itens_producao, itens_consumo, pct_var_ggf, produto_id):
    """
    🌟 NOVO (14/10/2026 -- desempenho): tudo o que o custo da rota num mês precisa, calculado UMA vez por mês (e não
    uma vez por rota e por mês): total contábil e produção de cada equipamento, o valor de cada insumo consumido por
    ele, os produtores de cada item, o custo médio de cada item e o custo (total e variável) de cada equipamento que
    faz o produto final. Com isso o custo de cada rota no mês sai em poucas contas (_custo_final_rota_no_mes), com o
    MESMO resultado de _aplicar_fatores_valor_contabil + _custo_final_do_mes.
    """
    total, producao, _por_op = _custos_ops(linhas_mes, ggf_mes)
    val = defaultdict(lambda: defaultdict(float))            # op -> código consumido -> (material + GGF) x qtde
    val_var = defaultdict(lambda: defaultdict(float))        # só p/ o produto final: parte variável
    produtores_todos, entradas = defaultdict(set), defaultdict(set)
    for l in linhas_mes:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        produtores_todos[itens_producao[op[0]]['codigo']].add(op)
        q = _num(l['pro_men_qtde_consumo'])
        if q <= 0:
            continue
        codigo = itens_consumo[l['pro_men_item_consumo_id']]['codigo']
        entradas[op].add(codigo)
        if _num(l['pro_men_qtde_produzida']) > 0:
            mat, ggf = _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
            val[op][codigo] += (mat + ggf) * q
            pct = pct_var_ggf.get(codigo, 0.0) if ggf > 0 else 0.0
            val_var[op][codigo] += mat * q + ggf * q * (pct if pct > 0 else 0.0) / 100.0
    produtores = defaultdict(list)
    medio = defaultdict(lambda: [0.0, 0.0])
    for op, prod in producao.items():
        if prod > 0:
            codigo = itens_producao[op[0]]['codigo']
            produtores[codigo].append(op)
            medio[codigo][0] += total[op]
            medio[codigo][1] += prod
    finais = {}
    for op in [o for o in producao if o[0] == produto_id and producao[o] > 0]:
        try:
            g = montar_grafo([l for l in linhas_mes if (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id']) == op],
                             itens_producao, itens_consumo, {produto_id}, ggf_linhas=ggf_mes, pct_var_ggf=pct_var_ggf)
        except FluxoProducaoMensalError:
            continue
        c = next(iter(g.get('custos_produtos') or []), None)
        if c and c['producao'] > 0:
            finais[op] = (c['unit_total'] * c['producao'], c['unit_variavel'] * c['producao'], c['producao'])
    variavel = {op: r[2] for (_m, op), r in _custos_mensais_ops(linhas_mes, ggf_mes, itens_consumo, pct_var_ggf).items()}
    return {'total': total, 'variavel': variavel, 'producao': producao, 'val': val, 'val_var': val_var, 'produtores': produtores,
            'produtores_todos': produtores_todos, 'entradas': entradas, 'medio': medio, 'finais': finais}


def _custo_final_rota_no_mes(est, fora, codigo_final):
    """Custo (total, variável) por unidade do produto final da rota no mês -- ver _estrutura_mes."""
    # ordem: cada item depois dos que ele consome (mesma regra de _ordem_de_tras_pra_frente, só com os da rota)
    def filhos(codigo):
        return iter(sorted({c for op in est['produtores_todos'].get(codigo, ()) if op not in fora
                            for c in est['entradas'].get(op, ())}))
    tem_produtor = lambda c: any(op not in fora for op in est['produtores_todos'].get(c, ()))
    ordem, visitados = [], {codigo_final}
    pilha = [(codigo_final, filhos(codigo_final))]
    while pilha:
        codigo, it = pilha[-1]
        proximo = next((c for c in it if tem_produtor(c) and c not in visitados), None)
        if proximo is None:
            pilha.pop()
            ordem.append(codigo)
        else:
            visitados.add(proximo)
            pilha.append((proximo, filhos(proximo)))
    fator = {}
    for codigo in ordem:
        if codigo == codigo_final:
            continue
        base_total, base_prod = est['medio'].get(codigo, (0.0, 0.0))
        if base_prod <= 0 or base_total == 0:
            continue
        ops = [o for o in est['produtores'].get(codigo, ()) if o not in fora]
        prod_r = sum(est['producao'][o] for o in ops)
        if prod_r <= 0:
            continue
        total_r = sum(est['total'][o] + sum((fator.get(c, 1.0) - 1.0) * v for c, v in est['val'][o].items()) for o in ops)
        fator[codigo] = (total_r / prod_r) / (base_total / base_prod)
    t = v = p = 0.0
    for op, (ft, fv, fp) in est['finais'].items():
        if op in fora:
            continue
        t += ft + sum((fator.get(c, 1.0) - 1.0) * x for c, x in est['val'][op].items())
        v += fv + sum((fator.get(c, 1.0) - 1.0) * x for c, x in est['val_var'][op].items())
        p += fp
    if p <= 0:
        return None, fator
    return {'producao': p, 'total': t / p, 'variavel': v / p}, fator


def _custo_op_rota_no_mes(est, op, fator):
    """(produção, custo total/un, custo variável/un) de um equipamento da rota no mês, com os fatores da rota."""
    prod = est['producao'].get(op, 0.0)
    if prod <= 0:
        return None
    t = est['total'].get(op, 0.0) + sum((fator.get(c, 1.0) - 1.0) * x for c, x in est['val'][op].items())
    v = est['variavel'].get(op, 0.0) + sum((fator.get(c, 1.0) - 1.0) * x for c, x in est['val_var'][op].items())
    return prod, t / prod, v / prod


def _custos_mensais_ops(linhas, ggf_linhas, itens_consumo, pct_var_ggf):
    """
    🌟 NOVO (15/10/2026): custo contábil de CADA equipamento em cada mês, numa passada só pelas linhas -- a mesma conta
    de montar_grafo/calcular_custos feita mês a mês: total = (material + GGF do item) x qtde das linhas com produção +
    GGF distribuído das ordens com produção; variável = material x qtde + parte variável do GGF do item (% da
    Genealogia) + parte variável do GGF distribuído (V/F/O da Conta x CC). Devolve {(mês, op): [produção, total, variável]}.
    """
    pct_var_ggf = pct_var_ggf or {}
    acc, vistos, ordens = {}, set(), defaultdict(set)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        mes = l['pro_men_ano_mes']
        r = acc.get((mes, op))
        if r is None:
            r = acc[(mes, op)] = [0.0, 0.0, 0.0]
        qp = _num(l['pro_men_qtde_produzida'])
        chave = (mes, l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'], l['pro_men_ordem_producao'],
                 l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            r[0] += qp
        if qp > 0:
            qtd, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
            pct = pct_var_ggf.get(itens_consumo[l['pro_men_item_consumo_id']]['codigo'], 0.0) if ggf > 0 else 0.0
            r[1] += (mat + ggf) * qtd
            r[2] += mat * qtd + ggf * qtd * (pct if pct > 0 else 0.0) / 100.0
            ordens[op].add((mes, l['pro_men_ordem_producao']))
    for g in ggf_linhas or []:
        op = (g['item_id'], g['grupo_id'])
        if (g['ano_mes'], g['ordem']) in ordens.get(op, ()):
            r = acc.get((g['ano_mes'], op))
            if r is not None:
                valor = _num(g['valor'])
                r[1] += valor
                r[2] += _parte_variavel_ggf(valor, g.get('tipo'), g.get('percentual'))
    return acc


def _itens_mensais_ops(linhas, itens_consumo, pct_var_ggf):
    """
    🌟 NOVO (16/10/2026): por mês e equipamento, cada item consumido (pelo CÓDIGO): [qtde, valor total, valor variável]
    -- a mesma conta de montar_grafo (qtde de todas as linhas; valor só das linhas com produção > 0). Qtde negativa =
    subproduto (valores negativos = crédito). Usado na evolução mensal de cada matéria-prima / subproduto.
    Devolve {(mês, op): {(código, gerado?): [qtde, total, variável]}}.
    """
    pct_var_ggf = pct_var_ggf or {}
    acc = {}
    for l in linhas:
        qtd = _num(l['pro_men_qtde_consumo'])
        if qtd == 0:
            continue
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        codigo = itens_consumo[l['pro_men_item_consumo_id']]['codigo']
        por_item = acc.get((l['pro_men_ano_mes'], op))
        if por_item is None:
            por_item = acc[(l['pro_men_ano_mes'], op)] = {}
        # 🌟 21/10/2026: chave (código, gerado?) -- consumido e gerado no mesmo mês não se anulam
        r = por_item.get((codigo, qtd < 0))
        if r is None:
            r = por_item[(codigo, qtd < 0)] = [0.0, 0.0, 0.0]
        r[0] += qtd
        if _num(l['pro_men_qtde_produzida']) > 0:
            mat, ggf = _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
            pct = pct_var_ggf.get(codigo, 0.0) if ggf > 0 else 0.0
            r[1] += (mat + ggf) * qtd
            r[2] += mat * qtd + ggf * qtd * (pct if pct > 0 else 0.0) / 100.0
    return acc


def _itens_mensais_compacta(acc, ids_ops, meses):
    """{id do nó: {código: [[índice do mês, qtde, total, variável], ...]}} -- qtde com sinal (negativa = subproduto)."""
    idx = {m: i for i, m in enumerate(meses)}
    saida = {}
    for (mes, op), por_item in acc.items():
        nid = 'op:%s:%s' % op
        if nid not in ids_ops or mes not in idx:
            continue
        destino = saida.setdefault(nid, {})
        for (codigo, _g), (q, t, v) in por_item.items():
            destino.setdefault(codigo, []).append([idx[mes], round(q, 4), round(t, 2), round(v, 2)])
    for por_item in saida.values():
        for lista in por_item.values():
            lista.sort()
    return saida


def _evolucao_ops_compacta(acc, ids_ops, meses):
    """{id do nó: [[índice do mês, produção, custo total/un, custo variável/un], ...]} -- só meses com produção."""
    idx = {m: i for i, m in enumerate(meses)}
    saida = defaultdict(list)
    for (mes, op), (prod, t, v) in acc.items():
        nid = 'op:%s:%s' % op
        if nid in ids_ops and prod > 0 and mes in idx:
            saida[nid].append([idx[mes], round(prod, 4), round(t / prod, 4), round(v / prod, 4)])
    for lista in saida.values():
        lista.sort()
    return dict(saida)


def _base_valor_contabil(linhas, ggf_linhas, itens_producao, itens_consumo):
    """Custo de cada equipamento com TODOS juntos (valor contábil) e o custo médio de cada item (ponderado pela produção)."""
    total_base, producao_base, por_op = _custos_ops(linhas, ggf_linhas)
    custo_medio_base = defaultdict(lambda: [0.0, 0.0])
    for op, prod in producao_base.items():
        if prod > 0:
            c = custo_medio_base[itens_producao[op[0]]['codigo']]
            c[0] += total_base[op]
            c[1] += prod
    ids_consumo_por_codigo = defaultdict(set)
    for cid, ic in itens_consumo.items():
        ids_consumo_por_codigo[ic['codigo']].add(cid)
    return {'total': total_base, 'por_op': por_op, 'medio': custo_medio_base, 'ids_consumo': ids_consumo_por_codigo,
            'producao': producao_base}


def _aplicar_fatores_valor_contabil(base, linhas_rota, ggf_rota, itens_producao, itens_consumo, codigo_final):
    """
    🌟 ALTERADO (13/10/2026): custo da rota SEMPRE a partir do VALOR CONTÁBIL. Cada item da cadeia entra no
    consumidor pelo valor contábil da linha do consumidor x (custo do item NESTA rota / custo médio do item com TODOS
    os equipamentos juntos, ponderado pela produção). De trás pra frente: cada item depois dos que ele consome -- assim
    a diferença de uma mina, carvoaria, moinho ou forno escolhido chega até o produto final. Os fatores têm média 1
    (ponderada pela participação), então a média das rotas é EXATAMENTE o custo com todos os equipamentos juntos (o
    valor contábil) -- total, variável e fixo. Muda linhas_rota no lugar.
    """
    total_base, _por_op, custo_medio_base, ids_consumo_por_codigo = base['total'], base['por_op'], base['medio'], base['ids_consumo']
    _t, producao_rota, por_op_rota = _custos_ops(linhas_rota, ggf_rota)
    ggf_op = {op: total_base[op] - sum((_num(l.get('pro_men_valor_material')) + _num(l.get('pro_men_valor_ggf')))
                                       * _num(l['pro_men_qtde_consumo'])
                                       for l in _por_op[op] if _num(l['pro_men_qtde_produzida']) > 0)
              for op in por_op_rota}
    ops_por_codigo = defaultdict(list)
    for op in por_op_rota:
        if producao_rota[op] > 0:
            ops_por_codigo[itens_producao[op[0]]['codigo']].append(op)
    consumidoras = defaultdict(list)          # código consumido -> linhas (qtde > 0) que o consomem
    for l in linhas_rota:
        if _num(l['pro_men_qtde_consumo']) > 0:
            consumidoras[itens_consumo[l['pro_men_item_consumo_id']]['codigo']].append(l)
    for codigo in _ordem_de_tras_pra_frente(linhas_rota, itens_producao, itens_consumo, codigo_final):
        if codigo == codigo_final:
            continue
        base_total, base_prod = custo_medio_base.get(codigo, (0.0, 0.0))
        if base_prod <= 0 or base_total == 0:
            continue
        total_r = sum(sum((_num(l.get('pro_men_valor_material')) + _num(l.get('pro_men_valor_ggf')))
                          * _num(l['pro_men_qtde_consumo'])
                          for l in por_op_rota[op] if _num(l['pro_men_qtde_produzida']) > 0) + ggf_op[op]
                      for op in ops_por_codigo[codigo])
        prod_r = sum(producao_rota[op] for op in ops_por_codigo[codigo])
        if prod_r <= 0:
            continue
        fator = (total_r / prod_r) / (base_total / base_prod)
        if abs(fator - 1.0) < 1e-12:
            continue
        for l in consumidoras.get(codigo, ()):
            l['pro_men_valor_material'] = _num(l.get('pro_men_valor_material')) * fator
            l['pro_men_valor_ggf'] = _num(l.get('pro_men_valor_ggf')) * fator



MES_AGREGADO = 'PERÍODO'


def _agregar_periodo(linhas, ggf_linhas):
    """
    🌟 NOVO (14/10/2026 -- desempenho): soma as linhas da Produção Mensal do período por equipamento (item x grupo) x
    item consumido, separando consumo positivo/negativo e linhas com/sem produção (só as com produção entram no
    custo, como na procedure). Valor material e valor GGF viram o preço médio ponderado pela quantidade -- então
    qtde x valor é exatamente a soma do período. A produção do equipamento é a soma do período (uma "ordem" só,
    MES_AGREGADO). O GGF distribuído é somado por equipamento x conta (tipo e %), só o das ordens que tiveram produção
    -- o mesmo que montar_grafo casaria mês a mês. Devolve (linhas, ggf_linhas) no mesmo formato de antes.
    """
    producao, vistos, ordens, grupo_de = defaultdict(float), set(), defaultdict(set), {}
    soma = {}
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        grupo_de.setdefault(op, (l.get('grupo'), l['pro_men_estabelecimento_id']))
        qp = _num(l['pro_men_qtde_produzida'])
        chave = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                 l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        if chave not in vistos:
            vistos.add(chave)
            producao[op] += qp
        if qp > 0:
            ordens[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))
        q = _num(l['pro_men_qtde_consumo'])
        k = (op, l['pro_men_item_consumo_id'], q >= 0, qp > 0)
        r = soma.setdefault(k, [0.0, 0.0, 0.0])
        r[0] += q
        r[1] += _num(l.get('pro_men_valor_material')) * q
        r[2] += _num(l.get('pro_men_valor_ggf')) * q
    saida = []
    # por equipamento: primeiro as linhas "com produção" (levam a produção do período), depois as "sem produção"
    for (op, cid, _sinal, com_prod), (q, vm, vg) in sorted(soma.items(), key=lambda kv: (kv[0][0], not kv[0][3])):
        grupo, estab = grupo_de[op]
        saida.append({'pro_men_ano_mes': MES_AGREGADO, 'pro_men_estabelecimento_id': estab,
                      'pro_men_grupo_maquina_id': op[1], 'pro_men_ordem_producao': MES_AGREGADO,
                      'pro_men_item_producao_id': op[0],
                      'pro_men_qtde_produzida': producao[op] if com_prod else 0.0,
                      'pro_men_item_consumo_id': cid, 'pro_men_qtde_consumo': q,
                      'pro_men_valor_material': (vm / q) if q else 0.0, 'pro_men_valor_ggf': (vg / q) if q else 0.0,
                      'grupo': grupo})
    ggf = {}
    for g in ggf_linhas or []:
        op = (g['item_id'], g['grupo_id'])
        if (g['ano_mes'], g['ordem']) not in ordens.get(op, ()):
            continue
        k = (op, g.get('tipo'), g.get('percentual'), g.get('conta_cc_id'), g.get('conta_descricao'))
        ggf[k] = ggf.get(k, 0.0) + _num(g['valor'])
    ggf_saida = [{'ano_mes': MES_AGREGADO, 'ordem': MES_AGREGADO, 'item_id': op[0], 'grupo_id': op[1], 'valor': v,
                  'tipo': tipo, 'percentual': pct, 'conta_cc_id': cc, 'conta_descricao': desc}
                 for (op, tipo, pct, cc, desc), v in ggf.items()]
    return saida, ggf_saida


def _somente_cadeia(linhas, itens_producao, itens_consumo, ids_selecionados):
    """
    🌟 NOVO (14/10/2026 -- desempenho): só as linhas dos equipamentos da cadeia dos produtos escolhidos (quem produz o
    produto final, quem produz o que eles consomem, e assim por diante). As demais linhas da Produção Mensal da empresa
    não entram em nenhuma conta da rota -- e eram dezenas de milhares, percorridas várias vezes por rota.
    """
    por_codigo, entradas = defaultdict(set), defaultdict(set)
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        por_codigo[itens_producao[op[0]]['codigo']].add(op)
        if _num(l['pro_men_qtde_consumo']) > 0:
            entradas[op].add(itens_consumo[l['pro_men_item_consumo_id']]['codigo'])
    fila = [itens_producao[i]['codigo'] for i in ids_selecionados if i in itens_producao]
    vistos, ops = set(fila), set()
    while fila:
        codigo = fila.pop()
        for op in por_codigo.get(codigo, ()):
            if op in ops:
                continue
            ops.add(op)
            for c in entradas.get(op, ()):
                if c not in vistos:
                    vistos.add(c)
                    fila.append(c)
    return [l for l in linhas if (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id']) in ops]


_TEMPOS_ROTA = defaultdict(float)     # diagnóstico de desempenho (vai para o log em carregar_rotas_reais)


def montar_rotas_reais(linhas, itens_producao, itens_consumo, ids_selecionados, ggf_linhas=None, pct_var_ggf=None):
    """Parte sem banco de carregar_rotas_reais (pra poder ser testada isoladamente)."""
    # 🌟 13/10/2026: os equipamentos VAZIO (reclassificações) são DESCONSIDERADOS no fluxo real (decisão do usuário).
    # 🌟 NOVO (14/10/2026 -- desempenho): para o PERÍODO, as linhas são somadas por equipamento x item consumido (o
    # mesmo total, a mesma quantidade e o mesmo valor contábil -- ver _agregar_periodo): de 100 mil linhas (7 anos)
    # ficam poucos milhares. As linhas mês a mês só são usadas no custo mês a mês.
    ggf_linhas = ggf_linhas or []
    linhas_agr, ggf_agr = _agregar_periodo(linhas, ggf_linhas)
    pedaco_mv, qtd_mv = _fragmentos_vazio(linhas_agr, itens_producao, itens_consumo, ggf_agr, pct_var_ggf or {})
    linhas_mes_a_mes = _somente_cadeia(remover_vazio(linhas), itens_producao, itens_consumo, ids_selecionados)
    linhas = _somente_cadeia(remover_vazio(linhas_agr), itens_producao, itens_consumo, ids_selecionados)
    rotas = []
    base = _base_valor_contabil(linhas, ggf_agr, itens_producao, itens_consumo)
    linhas_do_mes, ggf_do_mes = defaultdict(list), defaultdict(list)
    for l in linhas_mes_a_mes:
        linhas_do_mes[l['pro_men_ano_mes']].append(l)
    for g in ggf_linhas:
        ggf_do_mes[g['ano_mes']].append(g)
    meses, base_mes = sorted(linhas_do_mes), {}
    itens_mes_todos = None              # 🌟 NOVO (16/10/2026): itens de cada equipamento mês a mês (feito uma vez)
    ggf_linhas = ggf_agr
    for produto_id in ids_selecionados:
        codigo_final = itens_producao[produto_id]['codigo']
        escolhidas, total_rotas, produtores, membros = _rotas_por_agrupamento(linhas, itens_producao, itens_consumo, produto_id)
        grupo_ops = lambda codigo, lider: membros.get((codigo, lider), {lider})
        for escolhas, participacao in escolhidas:
            # tira as linhas (e o GGF) dos equipamentos NÃO escolhidos em cada agrupamento da rota
            fora = set()
            for codigo, op in escolhas.items():
                fora |= {o for lider, _p in produtores[codigo] if lider != op for o in grupo_ops(codigo, lider)}
            linhas_rota = [dict(l) for l in linhas if (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id']) not in fora]
            ggf_rota = [g for g in ggf_linhas if (g['item_id'], g['grupo_id']) not in fora]
            pct_rota = dict(pct_var_ggf or {})
            # 🌟 ALTERADO (13/10/2026): custo da rota SEMPRE a partir do VALOR CONTÁBIL. Cada item da cadeia entra no
            # consumidor pelo valor contábil da linha do consumidor x (custo do item NESTA rota / custo médio do item
            # com TODOS os equipamentos juntos, ponderado pela produção). De trás pra frente: cada item depois dos
            # que ele consome -- assim a diferença de uma mina, carvoaria, moinho ou forno escolhido chega até o
            # produto final. Os fatores têm média 1 (ponderada pela participação), então a média das rotas é
            # EXATAMENTE o custo com todos os equipamentos juntos (o valor contábil) -- total, variável e fixo.
            _aplicar_fatores_valor_contabil(base, linhas_rota, ggf_rota, itens_producao, itens_consumo, codigo_final)
            # 🌟 NOVO (14/10/2026): custo do produto final da rota mês a mês -- o mesmo ajuste pelo valor contábil, feito
            # com os dados de cada mês (a média das rotas de cada mês, ponderada pela participação no mês, é o custo do
            # mês com todos os equipamentos juntos).
            mensal = []
            fatores_mes = {}
            t0 = time.time()
            for m in meses:
                if m not in base_mes:
                    base_mes[m] = _estrutura_mes(linhas_do_mes[m], ggf_do_mes.get(m, []), itens_producao, itens_consumo,
                                                 pct_var_ggf or {}, produto_id)
                r, fator_m = _custo_final_rota_no_mes(base_mes[m], fora, codigo_final)
                fatores_mes[m] = fator_m
                if r:
                    r['mes'] = m
                    # produção da rota no mês = produção do produto final x participação dos escolhidos no mês
                    prod_mes = base_mes[m]['producao']
                    for codigo, op_esc in escolhas.items():
                        lista_ag = produtores.get(codigo) or []
                        if len(lista_ag) > 1 and codigo != codigo_final:
                            tot_m = sum(prod_mes.get(o, 0.0) for lider, _p in lista_ag for o in grupo_ops(codigo, lider))
                            esc_m = sum(prod_mes.get(o, 0.0) for o in grupo_ops(codigo, op_esc))
                            r['producao'] *= (esc_m / tot_m) if tot_m > 0 else 0.0
                    mensal.append(r)
            _TEMPOS_ROTA['mensal'] += time.time() - t0
            # 🌟 NOVO (14/10/2026): QUANTIDADES da rota. Quem está à frente de um agrupamento recebe só a parte que veio do
            # equipamento escolhido: produção, consumos e GGF de cada equipamento multiplicados pela participação
            # (produção do escolhido / produção do agrupamento) de cada agrupamento que está atrás dele na rota. Ex.: rota
            # FN01, que fez 28,79% do 8050001 -- a britagem aparece consumindo 28,79% do que consumiu de todos os fornos
            # (e produzindo 28,79%). Tudo do equipamento muda na mesma proporção, então custo por unidade e consumo
            # específico NÃO mudam (e a média das rotas continua igual ao valor contábil).
            escala = _escala_por_equipamento(linhas_rota, itens_producao, itens_consumo, escolhas, produtores)
            if escala:
                for l in linhas_rota:
                    k = escala.get((l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id']))
                    if k is not None:
                        l['pro_men_qtde_produzida'] = _num(l['pro_men_qtde_produzida']) * k
                        l['pro_men_qtde_consumo'] = _num(l['pro_men_qtde_consumo']) * k
                ggf_rota = [dict(g, valor=_num(g['valor']) * escala[(g['item_id'], g['grupo_id'])])
                            if (g['item_id'], g['grupo_id']) in escala else g for g in ggf_rota]
            grafo = montar_grafo(linhas_rota, itens_producao, itens_consumo, {produto_id},
                                 ggf_linhas=ggf_rota, pct_var_ggf=pct_rota)
            # 🌟 NOVO: círculo MV -- em cada equipamento, quantos itens o clique abre (mv_n) e, por equipamento (inclusive os
            # que entram pelos cliques, nível a nível), o pedaço de fluxo que entra no desenho (mv_fragmentos).
            grafo['rota_real'] = True
            # 🌟 CORRIGIDO (14/10/2026): (1) mês em que o equipamento escolhido num agrupamento NÃO produziu não é mês
            # da rota -- sai do gráfico (antes aparecia com custo e produção zero); (2) a produção da rota em cada mês é
            # a do produto final no mês x participação dos escolhidos no mês, AJUSTADA para que a soma dos meses seja
            # exatamente a produção da rota no período (a do quadrado do equipamento final).
            mensal = [r for r in mensal if r['producao'] > 1e-9]
            prod_periodo = sum(c['producao'] for c in grafo.get('custos_produtos') or [] if c['codigo'] == codigo_final)
            soma_meses = sum(r['producao'] for r in mensal)
            if soma_meses > 0 and prod_periodo > 0:
                for r in mensal:
                    r['producao'] *= prod_periodo / soma_meses
            grafo['custo_mensal'] = {codigo_final: mensal}
            # 🌟 NOVO (15/10/2026): evolução mensal do custo de CADA equipamento da rota (botão do círculo C) -- mesmo
            # ajuste pelo valor contábil do mês; produção do mês x a mesma proporção da rota no quadrado (escala).
            t0 = time.time()
            idx_mes = {m: i for i, m in enumerate(meses)}
            evol = defaultdict(list)
            # 🌟 NOVO (16/10/2026): evolução mensal de cada item consumido / subproduto do equipamento na rota -- qtde x a
            # mesma proporção da produção do equipamento na rota (escala; no equipamento final, a produção da rota no mês);
            # valor do item consumido ajustado pelo fator do valor contábil da rota no mês (o mesmo do custo do equipamento).
            if itens_mes_todos is None:
                itens_mes_todos = _itens_mensais_ops(linhas_mes_a_mes, itens_consumo, pct_var_ggf)
            itens_evol = {}
            def guarda_itens(nid, op, m, k_m):
                por_item = itens_mes_todos.get((m, op))
                if not por_item:
                    return
                f = fatores_mes.get(m, {})
                destino = itens_evol.setdefault(nid, {})
                for (codigo, _g), (q, t, v) in por_item.items():
                    fc = f.get(codigo, 1.0) if q > 0 else 1.0
                    destino.setdefault(codigo, []).append([idx_mes[m], round(q * k_m, 4), round(t * fc * k_m, 2), round(v * fc * k_m, 2)])
            for n in grafo['nos']:
                if n['tipo'] != 'op':
                    continue
                _p, a, b = n['id'].split(':')
                op = (int(a), int(b))
                k = escala.get(op, 1.0) if escala else 1.0
                if n['item_codigo'] == codigo_final and len([x for x in grafo['nos'] if x['tipo'] == 'op'
                                                              and x['item_codigo'] == codigo_final]) == 1:
                    # equipamento do produto final: a mesma evolução do produto (mesma produção mês a mês)
                    evol[n['id']] = [[idx_mes[r['mes']], round(r['producao'], 4), round(r['total'], 4), round(r['variavel'], 4)]
                                     for r in mensal]
                    for r in mensal:
                        p_op = base_mes[r['mes']]['producao'].get(op, 0.0)
                        if p_op > 0:
                            guarda_itens(n['id'], op, r['mes'], r['producao'] / p_op)
                    continue
                for m in meses:
                    x = _custo_op_rota_no_mes(base_mes[m], op, fatores_mes.get(m, {}))
                    if x and x[0] * k > 1e-9:
                        evol[n['id']].append([idx_mes[m], round(x[0] * k, 4), round(x[1], 4), round(x[2], 4)])
                        guarda_itens(n['id'], op, m, k)
            grafo['meses'] = meses
            grafo['custo_mensal_op'] = dict(evol)
            grafo['itens_mensal_op'] = itens_evol
            _TEMPOS_ROTA['mensal'] += time.time() - t0
            fragmentos, fila = {}, []
            for n in grafo['nos']:
                if n['tipo'] == 'op':
                    _p, a, b = n['id'].split(':')
                    n['mv_n'] = qtd_mv((int(a), int(b)))
                    if n['mv_n']:
                        fila.append((int(a), int(b)))
            while fila and len(fragmentos) < MAX_FRAGMENTOS_MV:
                op = fila.pop(0)
                chave = 'op:%s:%s' % op
                if chave in fragmentos:
                    continue
                t0 = time.time()
                frag = pedaco_mv(op)
                _TEMPOS_ROTA['mv'] += time.time() - t0
                if not frag:
                    continue
                fragmentos[chave] = frag
                for x in frag['nos']:
                    if x['tipo'] == 'op' and x.get('mv_n') and x['id'] not in fragmentos:
                        _p, a, b = x['id'].split(':')
                        fila.append((int(a), int(b)))
            grafo['mv_fragmentos'] = fragmentos
            # rótulo: os equipamentos escolhidos nos agrupamentos, do produto final para trás
            grupos_por_op = {}
            for l in linhas:
                grupos_por_op[(l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])] = (l.get('grupo') or {}).get('codigo', '')
            divisoes = [(codigo, op) for codigo, op in escolhas.items() if len(produtores[codigo]) > 1]
            dist = _distancia_ao_final(grafo)
            divisoes.sort(key=lambda x: (dist.get('op:%s:%s' % x[1], 999), x[0]))
            # equipamentos abaixo de 5% que entraram junto com o maior: "(+n)" no nome
            nomes = [grupos_por_op.get(op, str(op[1])) + (' (+%d)' % (len(grupo_ops(c, op)) - 1) if len(grupo_ops(c, op)) > 1 else '')
                     for c, op in divisoes] or \
                [grupos_por_op.get(escolhas.get(itens_producao[produto_id]['codigo']), '')]
            rotulo_completo = ' / '.join(nomes)
            rotulo = ' / '.join(nomes[:3]) + (' +%d' % (len(nomes) - 3) if len(nomes) > 3 else '')
            rotas.append({'produto_id': produto_id, 'rotulo': rotulo, 'rotulo_completo': rotulo_completo,
                          'divisoes': [{'item': c, 'grupo': grupos_por_op.get(op, '')} for c, op in divisoes],
                          'participacao': 100.0 * participacao, 'total_rotas': total_rotas, 'grafo': grafo})
    return rotas



# ---------------------------------------------------------------------------------------------------------------
# 🌟 NOVO (17/10/2026): "Custo Ferbasa - Fluxo/Balanço de Subprodutos". Para cada subproduto escolhido (item que
# algum equipamento gerou com consumo NEGATIVO na Produção Mensal), na EMPRESA INTEIRA e no período:
#   quem GEROU (consumo negativo), quem PRODUZIU (o item também é fabricado: produção com o mesmo código) e quem
#   CONSUMIU (consumo positivo), com quantidade e valor contábil (crédito dado a quem gerou x cobrado de quem
#   consumiu -- mesma regra do custo: só linhas com produção têm valor).
#   saldo = gerado + produzido - consumido (positivo: sobrou -- estoque, venda, perdas; negativo: veio de estoque).
# Num mesmo equipamento, o consumo negativo e o positivo do item são contados SEPARADOS (ele pode gerar e consumir).
# Os equipamentos VAZIO (movimentações / reclassificações) entram como os outros e são marcados (vazio=True).
# ---------------------------------------------------------------------------------------------------------------
MAX_EQUIP_SUBPRODUTO = 300      # por lado (gerou / produziu / consumiu) de cada subproduto
EQUIPAMENTOS_INICIAIS_SUBPRODUTOS = 60   # quantos equipamentos entram no desenho de início (o resto fica na lista)


def subprodutos_com_geracao(empresa_id):
    """[{'id', 'codigo', 'descricao', 'unidade', 'minimo', 'maximo'}] dos itens com consumo negativo (gerados como
    subproduto) na Produção Mensal da empresa, somados pelo código, em ordem de descrição. 'id' = o menor id de
    TbItensConsumo do código (a tela de seleção trabalha com números)."""
    from django.db.models import Min, Max
    from .models import TbItensConsumo
    por_id = {r['pro_men_item_consumo_id']: (r['minimo'], r['maximo']) for r in
              producao_mensal_da_empresa(empresa_id).filter(pro_men_qtde_consumo__lt=0).order_by()
              .values('pro_men_item_consumo_id').annotate(minimo=Min('pro_men_ano_mes'), maximo=Max('pro_men_ano_mes'))}
    por_codigo = {}
    for i in TbItensConsumo.objects.filter(id__in=list(por_id)).values('id', 'ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade'):
        mi, ma = por_id[i['id']]
        r = por_codigo.setdefault(i['ite_con_codigo'], {'id': i['id'], 'codigo': i['ite_con_codigo'],
                                                        'descricao': i['ite_con_descricao'] or '', 'unidade': i['ite_con_unidade'] or '',
                                                        'minimo': mi, 'maximo': ma})
        r['id'] = min(r['id'], i['id'])
        r['minimo'], r['maximo'] = min(r['minimo'], mi), max(r['maximo'], ma)
    return sorted(por_codigo.values(), key=lambda r: (r['descricao'].lower(), r['codigo']))


def montar_fluxo_subprodutos(linhas, itens_producao, itens_consumo, codigos, pct_var_ggf=None, ggf_linhas=None):
    """Função pura (sem banco). linhas: as da Produção Mensal do período (com 'grupo'), como em montar_grafo.
    🌟 ALTERADO (18/10/2026): + CUSTO (valor contábil, total e variável -- mesma conta de montar_grafo):
      geração  = crédito dado a quem gerou ((material + GGF do item) x qtde gerada; variável = material + a parte
                 variável do GGF pelo % variável do item);
      consumo  = valor cobrado de quem consumiu (mesma conta);
      produção = custo de quem FABRICA o item: todos os consumos do equipamento (linhas com produção, créditos de
                 subprodutos incluídos) + GGF distribuído das ordens com produção -- como no Montar Fluxo."""
    pct_var_ggf = pct_var_ggf or {}
    codigos = set(codigos)
    producao = defaultdict(float)
    ja_contado = set()
    grupos = {}
    # op -> código -> [qtde gerada, total gerado, variável gerado, qtde consumida, total consumido, variável consumido]
    # (na geração, os valores com o sinal trocado: crédito positivo)
    mov = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))
    custo_op = defaultdict(lambda: [0.0, 0.0])          # op -> [total, variável] de tudo o que consumiu (com produção)
    ordens = defaultdict(set)
    cons_op = defaultdict(lambda: defaultdict(float))   # op -> id item consumido -> qtde líquida (negativa = gerado)
    val_op = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))   # op -> id item consumido -> [total, variável]
    for l in linhas:
        op = (l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'])
        grupos[op[1]] = l.get('grupo') or {'codigo': str(op[1]), 'nome': ''}
        chave_prod = (l['pro_men_ano_mes'], l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'],
                      l['pro_men_ordem_producao'], l['pro_men_item_producao_id'])
        qp = _num(l['pro_men_qtde_produzida'])
        if chave_prod not in ja_contado:
            ja_contado.add(chave_prod)
            producao[op] += qp
        ic = itens_consumo[l['pro_men_item_consumo_id']]
        qtd = _num(l['pro_men_qtde_consumo'])
        cons_op[op][l['pro_men_item_consumo_id']] += qtd
        total = variavel = 0.0
        if qp > 0:      # como no custo (procedure): só linhas com produção têm valor
            material, ggf = _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
            pct = pct_var_ggf.get(ic['codigo'], 0.0) if ggf > 0 else 0.0
            total = (material + ggf) * qtd
            variavel = material * qtd + ggf * qtd * (pct if pct > 0 else 0.0) / 100.0
            custo_op[op][0] += total
            custo_op[op][1] += variavel
            val_op[op][l['pro_men_item_consumo_id']][0] += total
            val_op[op][l['pro_men_item_consumo_id']][1] += variavel
            ordens[op].add((l['pro_men_ano_mes'], l['pro_men_ordem_producao']))
        if ic['codigo'] not in codigos or qtd == 0:
            continue
        reg = mov[op][ic['codigo']]
        if qtd < 0:
            reg[0] += -qtd
            reg[1] += -total
            reg[2] += -variavel
        else:
            reg[3] += qtd
            reg[4] += total
            reg[5] += variavel
    # GGF distribuído (só interessa a quem fabrica algum subproduto escolhido)
    for g in ggf_linhas or []:
        op = (g['item_id'], g['grupo_id'])
        if (g['ano_mes'], g['ordem']) in ordens.get(op, ()):
            v = _num(g['valor'])
            custo_op[op][0] += v
            custo_op[op][1] += _parte_variavel_ggf(v, g.get('tipo'), g.get('percentual'))

    def equip(op):
        g, prod = grupos[op[1]], itens_producao[op[0]]
        return {'grupo': g['codigo'], 'grupo_nome': g['nome'], 'vazio': (g['codigo'] or '').strip().upper() == GRUPO_VAZIO,
                'produto': prod['codigo'], 'descricao': prod['descricao'], 'nid': f"op:{op[0]}:{op[1]}"}

    info = {}
    for ic in itens_consumo.values():
        if ic['codigo'] in codigos and ic['codigo'] not in info:
            info[ic['codigo']] = ic
    ops_por_codigo = defaultdict(list)
    for op in producao:
        c = itens_producao[op[0]]['codigo']
        if c in codigos:
            ops_por_codigo[c].append(op)
            if c not in info:
                info[c] = {'codigo': c, 'descricao': itens_producao[op[0]]['descricao'], 'unidade': itens_producao[op[0]]['unidade']}

    itens, por_codigo_ops = [], {}
    for codigo in codigos:
        if codigo not in info:
            continue
        ger, con, prod = [], [], []
        for op, porc in mov.items():
            r = porc.get(codigo)
            if not r:
                continue
            if r[0] > 1e-12:
                ger.append(dict(equip(op), qtd=r[0], valor=r[1], variavel=r[2], _op=op))
            if r[3] > 1e-12:
                con.append(dict(equip(op), qtd=r[3], valor=r[4], variavel=r[5], _op=op))
        for op in ops_por_codigo.get(codigo, []):
            if producao[op] > 0:
                prod.append(dict(equip(op), qtd=producao[op], valor=custo_op[op][0], variavel=custo_op[op][1],
                                 produziu=True, _op=op))
        for lst in (ger, con, prod):
            lst.sort(key=lambda r: -r['qtd'])
        soma = lambda lst, k: sum(r[k] for r in lst)
        gerado, produzido, consumido = soma(ger, 'qtd'), soma(prod, 'qtd'), soma(con, 'qtd')
        ic = info[codigo]
        por_codigo_ops[codigo] = (ger, con, prod)
        sem_op = lambda lst: [{k: v for k, v in r.items() if k != '_op'} for r in lst[:MAX_EQUIP_SUBPRODUTO]]
        itens.append({
            'codigo': codigo, 'descricao': ic['descricao'] or '', 'unidade': ic['unidade'] or '',
            'gerado': gerado, 'credito': soma(ger, 'valor'), 'credito_variavel': soma(ger, 'variavel'),
            'produzido': produzido, 'custo_producao': soma(prod, 'valor'), 'custo_producao_variavel': soma(prod, 'variavel'),
            'consumido': consumido, 'cobrado': soma(con, 'valor'), 'cobrado_variavel': soma(con, 'variavel'),
            'saldo': gerado + produzido - consumido,
            'geradores': sem_op(ger), 'geradores_n': len(ger),
            'produtores': sem_op(prod), 'produtores_n': len(prod),
            'consumidores': sem_op(con), 'consumidores_n': len(con),
        })
    itens.sort(key=lambda r: (str(r['descricao']).lower(), r['codigo']))

    # 🌟 O DESENHO no mesmo formato do Montar Fluxo (nós + ligações), para usar o mesmo desenho:
    #   quem GEROU   --(subproduto: qtde gerada, gerado/produção do equipamento)-->   caixa do SUBPRODUTO
    #   quem PRODUZIU --(producao: qtde produzida)-->                                 caixa do SUBPRODUTO
    #   caixa do SUBPRODUTO --(consumo: qtde consumida, consumido/produção do equipamento)-->   quem CONSUMIU
    # Cada equipamento leva o que GEROU / PRODUZIU / CONSUMIU de cada subproduto ('papeis', com o custo -- círculo C).
    nos, arestas = {}, []

    def no_op(op):
        nid = f"op:{op[0]}:{op[1]}"
        if nid not in nos:
            g, prod = grupos[op[1]], itens_producao[op[0]]
            nos[nid] = {'id': nid, 'tipo': 'op', 'final': False, 'grupo_codigo': g['codigo'], 'grupo_nome': g['nome'],
                        'item_codigo': prod['codigo'], 'item_descricao': prod['descricao'], 'unidade': prod['unidade'],
                        'producao': producao.get(op, 0.0), 'papeis': []}
        return nid

    def esp(q, op):
        p = producao.get(op, 0.0)
        return (q / p) if p > 0 else None

    for idx, r in enumerate(itens):
        codigo, u = r['codigo'], r['unidade']
        ger, con, prod = por_codigo_ops[codigo]
        sid = f"item:SUB:{codigo}"
        nos[sid] = {'id': sid, 'tipo': 'item', 'subproduto': True, 'idx': idx, 'item_codigo': codigo,
                    'item_descricao': r['descricao'], 'unidade': u, 'producao': r['gerado'] + r['produzido'],
                    'gerado': r['gerado'], 'produzido': r['produzido'], 'consumido': r['consumido'], 'saldo': r['saldo'],
                    'membros': [],
                    'cg': {'qtd': r['gerado'], 'total': r['credito'], 'variavel': r['credito_variavel']},
                    'cp': {'qtd': r['produzido'], 'total': r['custo_producao'], 'variavel': r['custo_producao_variavel']},
                    'cc': {'qtd': r['consumido'], 'total': r['cobrado'], 'variavel': r['cobrado_variavel']}}
        for e in ger:
            op = e['_op']
            u_op = itens_producao[op[0]]['unidade']
            arestas.append({'de': no_op(op), 'para': sid, 'tipo': 'subproduto', 'qtd': e['qtd'], 'unidade': u,
                            'especifico': esp(e['qtd'], op), 'unidade_esp': f"{u}/{u_op}"})
            nos[no_op(op)]['papeis'].append({'tipo': 'gerou', 'codigo': codigo, 'qtd': e['qtd'], 'unidade': u,
                                             'total': e['valor'], 'variavel': e['variavel']})
        for e in prod:
            op = e['_op']
            arestas.append({'de': no_op(op), 'para': sid, 'tipo': 'producao', 'qtd': e['qtd'], 'unidade': u,
                            'especifico': None, 'unidade_esp': ''})
            nos[no_op(op)]['papeis'].append({'tipo': 'produziu', 'codigo': codigo, 'qtd': e['qtd'], 'unidade': u,
                                             'total': e['valor'], 'variavel': e['variavel']})
        for e in con:
            op = e['_op']
            u_op = itens_producao[op[0]]['unidade']
            arestas.append({'de': sid, 'para': no_op(op), 'tipo': 'consumo', 'qtd': e['qtd'], 'unidade': u,
                            'especifico': esp(e['qtd'], op), 'unidade_esp': f"{u}/{u_op}"})
            nos[no_op(op)]['papeis'].append({'tipo': 'consumiu', 'codigo': codigo, 'qtd': e['qtd'], 'unidade': u,
                                             'total': e['valor'], 'variavel': e['variavel']})
    # 🌟 NOVO (18/10/2026): de cada equipamento do desenho, o que entrou e saiu (cx) e o custo por unidade -- o mesmo do
    # Montar Fluxo, para o link "Produção:" (balanço de entradas e saídas, com o custo de cada item)
    for nid, no in nos.items():
        if no['tipo'] != 'op':
            continue
        op = (int(nid.split(':')[1]), int(nid.split(':')[2]))
        ins, sub = {}, {}
        for cid, qtd in cons_op[op].items():
            if abs(qtd) < 1e-12:
                continue
            ic = itens_consumo[cid]
            t, v = val_op[op].get(cid, (0.0, 0.0))
            r = (ins if qtd > 0 else sub).setdefault(ic['codigo'], [ic['codigo'], 0.0, 0.0, 0.0, ic.get('descricao') or '', ic.get('unidade') or ''])
            r[1] += abs(qtd)
            r[2] += t
            r[3] += v
        p = producao.get(op, 0.0)
        no['cx'] = {'p': p, 's': [custo_op[op][0], custo_op[op][1]], 'i': list(ins.values()), 'g': list(sub.values())}
        no['custo'] = {'producao': p, 'unit_total': custo_op[op][0] / p if p > 0 else 0.0,
                       'unit_variavel': custo_op[op][1] / p if p > 0 else 0.0}
    return {'tipo_fluxo': 'subprodutos', 'itens': itens, 'nos': list(nos.values()), 'arestas': arestas,
            'equipamentos_iniciais': EQUIPAMENTOS_INICIAIS_SUBPRODUTOS}


def carregar_fluxo_subprodutos(empresa_id, codigos, ano_mes_inicio, ano_mes_fim):
    """Lê a Produção Mensal do período (empresa inteira), o GGF distribuído e o % variável -- os mesmos dados do Montar
    Fluxo -- e monta o fluxo/balanço dos subprodutos.
    🌟 ALTERADO (18/10/2026): + evolução mensal de cada equipamento do desenho (custo da produção e cada item consumido /
    gerado), para os links de evolução dos quadros de custo e do "Produção:"."""
    linhas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf = _carregar_periodo(empresa_id, [], ano_mes_inicio, ano_mes_fim)
    grafo = montar_fluxo_subprodutos(linhas, itens_producao, itens_consumo, codigos, pct_var_ggf, ggf_linhas)
    meses = sorted({l['pro_men_ano_mes'] for l in linhas})
    ids_ops = {n['id'] for n in grafo['nos'] if n['tipo'] == 'op'}
    grafo['meses'] = meses
    grafo['custo_mensal_op'] = _evolucao_ops_compacta(
        _custos_mensais_ops(linhas, ggf_linhas, itens_consumo, pct_var_ggf), ids_ops, meses)
    grafo['itens_mensal_op'] = _itens_mensais_compacta(_itens_mensais_ops(linhas, itens_consumo, pct_var_ggf), ids_ops, meses)
    return grafo
