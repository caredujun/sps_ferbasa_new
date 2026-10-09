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
"""
from collections import defaultdict

LIMITE_NOS = 600        # proteção: um desenho maior que isso não é legível no chat


class FluxoProducaoMensalError(RuntimeError):
    pass


def _num(v):
    return float(v) if v is not None else 0.0


def montar_grafo(linhas, itens_producao, itens_consumo, ids_selecionados):
    """
    Função pura (sem banco), pra poder ser testada isoladamente.

    linhas: iteráveis de dicts com pro_men_ano_mes, pro_men_estabelecimento_id, pro_men_grupo_maquina_id,
            pro_men_ordem_producao, pro_men_item_producao_id, pro_men_qtde_produzida, pro_men_item_consumo_id,
            pro_men_qtde_consumo -- já filtradas pela empresa e pelo período.
    itens_producao: {id: {'codigo', 'descricao', 'unidade'}}   (TbItensProducao)
    itens_consumo:  {id: {'codigo', 'descricao', 'unidade', 'tipo'}}   (TbItensConsumo)
    grupos são lidos de linhas[...]['grupo'] -> {'codigo', 'nome'} (preenchido por carregar_grafo).
    ids_selecionados: ids de TbItensProducao escolhidos pelo usuário (os produtos finais do desenho).
    """
    producao = defaultdict(float)               # (item_prod_id, grupo_id) -> qtde produzida no período
    ja_contado = set()
    consumo = defaultdict(lambda: defaultdict(float))   # op -> item_consumo_id -> qtde
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
            if len(nos) > LIMITE_NOS:
                raise FluxoProducaoMensalError(
                    f"O fluxo ficou grande demais para desenhar (mais de {LIMITE_NOS} caixas). Escolha menos itens.")

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

    return {
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
    """[(TbItensProducao, ano_mes_minimo, ano_mes_maximo)] dos itens que têm Produção Mensal, em ordem de código."""
    from django.db.models import Min, Max
    from .models import TbItensProducao
    limites = {r['pro_men_item_producao_id']: (r['minimo'], r['maximo']) for r in
               producao_mensal_da_empresa(empresa_id).order_by().values('pro_men_item_producao_id')
               .annotate(minimo=Min('pro_men_ano_mes'), maximo=Max('pro_men_ano_mes'))}
    itens = TbItensProducao.objects.filter(id__in=list(limites)).order_by('ite_pro_codigo')
    return [(i, *limites[i.id]) for i in itens]


def carregar_grafo(empresa_id, ids_selecionados, ano_mes_inicio, ano_mes_fim):
    from .models import TbItensProducao, TbItensConsumo, TbGruposMaquinas
    linhas = list(producao_mensal_da_empresa(empresa_id)
                  .filter(pro_men_ano_mes__gte=ano_mes_inicio, pro_men_ano_mes__lte=ano_mes_fim)
                  .order_by()
                  .values('pro_men_ano_mes', 'pro_men_estabelecimento_id', 'pro_men_grupo_maquina_id',
                          'pro_men_ordem_producao', 'pro_men_item_producao_id', 'pro_men_qtde_produzida',
                          'pro_men_item_consumo_id', 'pro_men_qtde_consumo'))
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
    return montar_grafo(linhas, itens_producao, itens_consumo, set(ids_selecionados))
