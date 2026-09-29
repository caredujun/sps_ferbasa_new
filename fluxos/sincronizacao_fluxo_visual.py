"""
🌟 NOVO: ponte entre o fluxo já processado (TbFluxoProducaoInputOutput
-- coluna/linha POR EQUIPAMENTO, com "envia para" resolvido, a MESMA
tabela que o PDF em tasks.py/update_fluxo_celery usa pra desenhar o
fluxo) e o editor visual de arrastar-e-soltar (flu_pro_dados_fluxo,
formato da biblioteca Drawflow).

🌟 CORRIGIDO: a primeira versão calculava a posição de cada equipamento
sozinha, a partir de TbFluxoProducaoDaugther (a tabela de cadastro, por
CÉLULA/consumo padrão) -- e errava a ordem visual (um equipamento raiz,
sem quem alimentar antes dele, ficava isolado longe do resto; colunas
alinhadas incorretamente; cruzamento de ligações). A tabela
TbFluxoProducaoInputOutput já resolve tudo isso -- é POPULADA pela
procedure atualiza_input_output (chamada por "Atualizar Fluxos de
Produção") a partir da tabela de cadastro, com coluna/linha por
EQUIPAMENTO já calculados certos (o PDF já prova isso -- funciona hoje
sem cruzar linha nem desalinhar coluna). Agora o editor só LÊ e confia
nela, exatamente como o PDF -- nenhuma conta própria de layout.

⚠️ Consequência importante: como essa tabela só é atualizada quando
"Atualizar Fluxos de Produção" roda (não a cada alteração da tabela de
cadastro), o editor pode mostrar uma foto desatualizada se a tabela de
cadastro mudou depois da última vez que os fluxos foram atualizados
-- daí a checagem de fluxo.flu_pro_input_output_atualizado ao final
deste arquivo, usada pra avisar o usuário nesse caso.

Duas direções:

  - montar_dados_fluxo_a_partir_da_tabela(fluxo): lê
    TbFluxoProducaoInputOutput e devolve o dicionário no formato
    Drawflow. Chamada automaticamente por um sinal sempre que a tabela
    de cadastro (TbFluxoProducaoDaugther) muda -- ver o receiver logo
    abaixo -- pra flu_pro_dados_fluxo tentar ficar em dia; mas só
    reflete de verdade a mudança depois que "Atualizar Fluxos de
    Produção" rodar (ver aviso acima).

  - salvar_fluxo_a_partir_do_json(fluxo, dados_fluxo, ...): lê o grafo
    desenhado no editor, verifica consistência no SERVIDOR (não confia
    só na checagem do navegador), calcula coluna/linha (a posição
    exata arrastada no canvas NÃO é preservada pra ESSES campos -- vira
    posição de grade sequencial, sem cruzamento) e regrava a tabela de
    cadastro (TbFluxoProducaoDaugther) -- o "Atualizar Fluxos de
    Produção" continua sendo o responsável por regravar
    TbFluxoProducaoInputOutput a partir daí.
"""
from django.db import transaction
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from equipamentos.models import TbEquipamentos, TbEquipamentosCadastro
from .models import TbFluxoProducao, TbFluxoProducaoDaugther, TbFluxoConsumoPadrao, TbFluxoProducaoInputOutput


# Espaçamento usado pra converter coluna/linha em pos_x/pos_y no canvas.
ESPACO_COLUNA = 260
ESPACO_LINHA = 190
MARGEM_X = 50
MARGEM_Y = 50
# 🌟 CORRIGIDO: chegou a existir aqui uma quebra automática de linha
# quando o fluxo tinha muitas colunas (pra caber na largura da tela) --
# removida a pedido: o fluxo agora sempre fica numa faixa horizontal só,
# do início ao fim, sem quebrar; quem cuida de caber na largura da tela
# é o ZOOM no navegador (ver centralizarFluxoNaTela() em editor.html).


def _nome_equipamento(equipamento):
    cadastro = TbEquipamentosCadastro.objects.filter(id=equipamento.equ_codigo_id).first()
    codigo = cadastro.equ_cad_codigo if cadastro else '?'
    return f"{codigo}/{equipamento.equ_ordem_codigo}"


def _montar_html_no(equipamento, codigo_nome, descricao):
    # 🌟 Mesmo template usado por adicionarNoEquipamento() no
    # editor.html -- mantém a aparência idêntica entre um nó desenhado
    # manualmente e um recriado aqui a partir da tabela.
    imagem_src = f"/fluxo_producao/equipamento_imagem/{equipamento.id}/"
    placeholder = '/static/fluxo_producao/img/equipamento-placeholder.png'
    return (
        '<div class="equipamento-node" style="width: 200px; overflow: hidden;">'
        '<div class="equipamento-header" style="background-color: #3498db; color: white; padding: 8px;">'
        f'<div class="equipamento-titulo">{codigo_nome}</div></div>'
        '<div class="equipamento-body" style="padding: 10px; background-color: white; display: flex; flex-direction: column; align-items: center;">'
        '<div style="width: 100%; height: 80px; display: flex; align-items: center; justify-content: center; overflow: hidden; margin-bottom: 10px;">'
        f'<img src="{imagem_src}" alt="{codigo_nome}" class="equipamento-img" onerror="this.onerror=null; this.src=\'{placeholder}\';"></div>'
        '<div class="equipamento-detalhes" style="width: 100%;">'
        f'<div class="equipamento-descricao" style="font-size: 0.9em; color: #666;">{descricao or ""}</div>'
        '</div></div></div>'
    )


def montar_dados_fluxo_a_partir_da_tabela(fluxo, preservar_posicoes_existentes=True):
    """
    Lê TbFluxoProducaoInputOutput (a MESMA tabela que o PDF usa -- ver
    update_fluxo_celery em tasks.py) e devolve o dicionário no formato
    Drawflow. Não salva nada -- quem chama decide o que fazer com o
    resultado.

    preservar_posicoes_existentes=True (padrão): reaproveita o
    pos_x/pos_y que cada equipamento já tinha no flu_pro_dados_fluxo
    ATUAL do fluxo (arrastado manualmente no editor em algum momento) --
    só equipamento NOVO (que apareceu agora e ainda não tinha posição
    salva) ganha posição calculada em grade. Isso evita que uma
    sincronização automática (o sinal, mais abaixo) jogue fora o
    arranjo manual do usuário. Passe False só quando o pedido for de
    verdade "recomeçar do zero, ignorando o que estava desenhado" -- é
    o que o botão "Criar Fluxo no Editor" do Admin faz, de propósito,
    com aviso prévio ao usuário.
    """
    posicoes_ja_salvas = {}
    if preservar_posicoes_existentes:
        dados_existentes = (fluxo.flu_pro_dados_fluxo or {}).get('drawflow', {}).get('Home', {}).get('data', {}) or {}
        for node in dados_existentes.values():
            equip_id = (node.get('data') or {}).get('equipamento_id')
            if equip_id is not None and 'pos_x' in node and 'pos_y' in node:
                posicoes_ja_salvas[int(equip_id)] = (node['pos_x'], node['pos_y'])

    # 🌟 CORRIGIDO: fonte de verdade agora é TbFluxoProducaoInputOutput,
    # não mais TbFluxoProducaoDaugther. flag=1 é o mesmo filtro usado
    # pelo PDF (registros mantidos mesmo se o fluxo original for
    # apagado, ver comentário de update_fluxo_celery). coluna/linha e
    # "envia_para" vêm PRONTOS -- nenhuma conta de layout própria.
    registros = (TbFluxoProducaoInputOutput.objects
                 .filter(mae_id=fluxo.id, flag=1)
                 .select_related('flu_pro_inp_out_equipamento', 'flu_pro_inp_out_envia_para'))

    equipamentos_por_id = {}
    coluna_linha_gravada = {}   # equipamento_id -> (coluna, linha) exatamente como gravado
    ligacoes = []              # (from_equipamento_id, to_equipamento_id)

    for r in registros:
        equip = r.flu_pro_inp_out_equipamento
        equipamentos_por_id[equip.id] = equip
        coluna_linha_gravada[equip.id] = (r.flu_pro_inp_out_coluna, r.flu_pro_inp_out_linha)
        if r.flu_pro_inp_out_envia_para_id:
            ligacoes.append((equip.id, r.flu_pro_inp_out_envia_para_id))

    # 🌟 CORRIGIDO: usava o NÚMERO de coluna/linha gravado direto como
    # multiplicador de posição -- se esses números têm buracos (ex: 1, 2,
    # 5, 7, sem 3/4/6, o que acontece de verdade nessa tabela), o
    # espaçamento na tela saía desigual (uns equipamentos quase colados,
    # outros com um vão enorme no meio). Agora usa a ORDEM (rank) entre
    # os valores distintos de coluna, e a ordem (rank) da linha DENTRO de
    # cada coluna -- sempre 1, 2, 3... sequencial na tela, sem buraco,
    # não importa qual seja o número real gravado. A ordem relativa
    # (quem vem antes de quem) é preservada -- só o espaçamento é que
    # deixa de copiar os buracos do número gravado.
    colunas_distintas = sorted({c for c, _ in coluna_linha_gravada.values()})
    rank_da_coluna = {c: i + 1 for i, c in enumerate(colunas_distintas)}

    equipamentos_por_coluna = {}
    for equip_id, (coluna, linha) in coluna_linha_gravada.items():
        equipamentos_por_coluna.setdefault(coluna, []).append((linha, equip_id))

    posicao_equipamento = {}   # equipamento_id -> (coluna_rank, linha_rank)
    for coluna, itens in equipamentos_por_coluna.items():
        for linha_rank, (_linha_gravada, equip_id) in enumerate(sorted(itens), start=1):
            posicao_equipamento[equip_id] = (rank_da_coluna[coluna], linha_rank)

    # 🌟 CORRIGIDO: removida a quebra em blocos de colunas -- o fluxo
    # inteiro fica numa faixa horizontal só, por mais colunas que tenha.
    # Fluxo largo demais pra caber na tela não é mais resolvido aqui (em
    # pixel absoluto), e sim no navegador, ajustando o ZOOM da tela pra
    # caber a largura toda -- ver centralizarFluxoNaTela() no
    # editor.html, que usa o mecanismo de zoom da própria biblioteca
    # Drawflow.

    # Monta os nós -- 1 nó por equipamento, id do nó = id do equipamento
    # (string, como o Drawflow espera pra chaves de node).
    nodes = {}
    for equip_id, equipamento in equipamentos_por_id.items():
        if equip_id in posicoes_ja_salvas:
            pos_x, pos_y = posicoes_ja_salvas[equip_id]
        else:
            coluna, linha = posicao_equipamento.get(equip_id, (1, 1))
            pos_x = MARGEM_X + (coluna - 1) * ESPACO_COLUNA
            pos_y = MARGEM_Y + (linha - 1) * ESPACO_LINHA
        codigo_nome = _nome_equipamento(equipamento)
        nodes[str(equip_id)] = {
            'id': equip_id,
            'name': codigo_nome,
            'data': {
                'equipamento_id': equip_id,
                'codigo': codigo_nome,
                'imagem_src': f"/fluxo_producao/equipamento_imagem/{equip_id}/",
            },
            'class': 'equipamento',
            'html': _montar_html_no(equipamento, codigo_nome, equipamento.equ_ordem_descricao),
            'typenode': False,
            'inputs': {'input_1': {'connections': []}},
            'outputs': {'output_1': {'connections': []}},
            'pos_x': pos_x,
            'pos_y': pos_y,
        }

    for de_id, para_id in ligacoes:
        no_de = nodes.get(str(de_id))
        no_para = nodes.get(str(para_id))
        if no_de is None or no_para is None:
            continue
        no_de['outputs']['output_1']['connections'].append({'node': str(para_id), 'output': 'input_1'})
        no_para['inputs']['input_1']['connections'].append({'node': str(de_id), 'input': 'output_1'})

    return {'drawflow': {'Home': {'data': nodes}}}


@receiver(post_save, sender=TbFluxoProducaoDaugther)
@receiver(post_delete, sender=TbFluxoProducaoDaugther)
def _sincronizar_dados_fluxo_apos_alteracao_celula(sender, instance, **kwargs):
    """
    🌟 NOVO: sempre que uma célula da tabela filha (linha/coluna +
    consumo padrão) for criada, alterada ou apagada -- edição no
    Admin, importação de Excel, ou qualquer outro caminho que passe
    por .save()/.delete() do Django -- regrava flu_pro_dados_fluxo da
    mãe a partir da tabela, pra o editor visual sempre abrir mostrando
    o estado atual de verdade.

    ⚠️ Não dispara em bulk_create/bulk_update/queryset.update(), que
    não passam pelos sinais do Django -- código que usa esses atalhos
    pra mexer nessa tabela precisa chamar
    montar_dados_fluxo_a_partir_da_tabela() manualmente depois (é o
    que salvar_fluxo_a_partir_do_json, abaixo, já faz).
    """
    fluxo_id = instance.mae_id
    if not fluxo_id:
        return
    if not TbFluxoProducao.objects.filter(id=fluxo_id).exists():
        return
    dados = montar_dados_fluxo_a_partir_da_tabela(TbFluxoProducao.objects.get(id=fluxo_id))
    # update() em vez de save() no objeto -- mais barato, e não dispara
    # nenhum sinal de TbFluxoProducao (não existe nenhum registrado
    # hoje, mas evita loop se um dia existir).
    TbFluxoProducao.objects.filter(id=fluxo_id).update(flu_pro_dados_fluxo=dados)


# ---------------------------------------------------------------------
# Direção contrária: do JSON do editor pra tabela filha
# ---------------------------------------------------------------------

def verificar_consistencia_grafo(nodes):
    """
    Réplica em Python de verificarConsistenciaFluxo() do editor.html --
    roda no SERVIDOR antes de salvar (o usuário pode não clicar em
    "Verificar Fluxo" antes de salvar, então não dá pra confiar só na
    checagem do navegador). Devolve uma lista de problemas (texto);
    lista vazia = fluxo consistente. Verifica: equipamento isolado
    (sem nenhuma ligação) e ciclos (equipamento que acaba alimentando
    a si mesmo, direta ou indiretamente). Não exige uma cadeia única:
    ramificação e convergência são permitidas.
    """
    problemas = []

    for node in nodes.values():
        tem_entrada = any(c.get('connections') for c in node.get('inputs', {}).values())
        tem_saida = any(c.get('connections') for c in node.get('outputs', {}).values())
        if not tem_entrada and not tem_saida and len(nodes) > 1:
            problemas.append(f"Equipamento \"{node.get('name')}\" está isolado, sem nenhuma ligação.")

    visitados = set()
    na_pilha = set()
    achou_ciclo = False

    def tem_ciclo(node_id):
        visitados.add(node_id)
        na_pilha.add(node_id)
        node = nodes.get(node_id)
        if node:
            for saida in node.get('outputs', {}).values():
                for conexao in saida.get('connections', []):
                    destino = conexao.get('node')
                    if destino not in nodes:
                        continue
                    if destino in na_pilha:
                        return True
                    if destino not in visitados and tem_ciclo(destino):
                        return True
        na_pilha.discard(node_id)
        return False

    for node_id in nodes:
        if node_id not in visitados and tem_ciclo(node_id):
            achou_ciclo = True
            break

    if achou_ciclo:
        problemas.append(
            "O fluxo tem um ciclo (um equipamento acaba alimentando a si mesmo, "
            "direta ou indiretamente). Isso não é permitido."
        )

    return problemas


def calcular_colunas_linhas(nodes):
    """
    Calcula (coluna, linha) de cada nó a partir só das ligações do
    grafo -- usado quando o fluxo vem do editor (a posição livre no
    canvas não é preservada; vira posição de grade, igual ao resto do
    sistema). Coluna = nível no grafo (maior caminho a partir de uma
    raiz, ou seja, de quem não recebe de ninguém); linha = ordem
    estável dentro da mesma coluna (por pos_y arrastado, depois por
    id, pra empate). Assume grafo sem ciclos (já garantido por
    verificar_consistencia_grafo rodando antes).
    """
    predecessores = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        for saida in node.get('outputs', {}).values():
            for conexao in saida.get('connections', []):
                destino = conexao.get('node')
                if destino in nodes:
                    predecessores[destino].append(node_id)

    coluna = {}

    def nivel(node_id, visitando=frozenset()):
        if node_id in coluna:
            return coluna[node_id]
        if node_id in visitando:
            return 0  # segurança extra -- não deveria ocorrer, grafo já validado sem ciclo
        preds = predecessores.get(node_id, [])
        if not preds:
            coluna[node_id] = 1
        else:
            coluna[node_id] = 1 + max(nivel(p, visitando | {node_id}) for p in preds)
        return coluna[node_id]

    for node_id in nodes:
        nivel(node_id)

    por_coluna = {}
    for node_id in nodes:
        por_coluna.setdefault(coluna[node_id], []).append(node_id)

    linha = {}
    for ids_da_coluna in por_coluna.values():
        ids_ordenados = sorted(ids_da_coluna, key=lambda nid: (nodes[nid].get('pos_y', 0), nid))
        for i, node_id in enumerate(ids_ordenados, start=1):
            linha[node_id] = i

    return {node_id: (coluna[node_id], linha[node_id]) for node_id in nodes}


@transaction.atomic
def salvar_fluxo_a_partir_do_json(fluxo, dados_fluxo, valores_iniciais_ligacoes=None):
    """
    Recebe o JSON exportado pelo editor (Drawflow) e regrava
    TbFluxoProducaoDaugther inteira a partir dele. Devolve
    (True, None) se salvou, ou (False, lista_de_problemas) se recusou
    -- nesse caso, NADA é alterado no banco.

    valores_iniciais_ligacoes: dict opcional {"<from_equip_id>-<to_equip_id>":
    valor} -- usado quando uma ligação NOVA (sem TbFluxoConsumoPadrao
    já cadastrado nesse cenário) precisa desse valor pra ser criada.
    Ligação nova sem esse valor é rejeitada (não dá pra criar um
    TbFluxoConsumoPadrao sem valor_inicial, que é campo obrigatório).
    """
    nodes = (dados_fluxo or {}).get('drawflow', {}).get('Home', {}).get('data', {}) or {}
    valores_iniciais_ligacoes = valores_iniciais_ligacoes or {}

    if not nodes:
        return False, ["O fluxo está vazio -- arraste ao menos um equipamento antes de salvar."]

    problemas = verificar_consistencia_grafo(nodes)
    if problemas:
        return False, problemas

    posicoes = calcular_colunas_linhas(nodes)  # {node_id: (coluna, linha)}

    equipamento_do_node = {}
    for node_id, node in nodes.items():
        equip_id = node.get('data', {}).get('equipamento_id')
        if equip_id is None:
            return False, [f"O equipamento do nó \"{node.get('name')}\" não tem equipamento_id -- foi criado de forma inválida."]
        equipamento_do_node[node_id] = int(equip_id)

    # ligações: (node_de, node_para, equip_de, equip_para)
    ligacoes = []
    for node_id, node in nodes.items():
        for saida in node.get('outputs', {}).values():
            for conexao in saida.get('connections', []):
                destino_node_id = conexao.get('node')
                if destino_node_id not in nodes:
                    continue
                ligacoes.append((
                    node_id, destino_node_id,
                    equipamento_do_node[node_id], equipamento_do_node[destino_node_id],
                ))

    if not ligacoes:
        return False, ["O fluxo precisa de pelo menos uma ligação entre dois equipamentos."]

    cenario_id = fluxo.tbcenarios_id

    consumo_padrao_por_ligacao = {}
    faltando_valor_inicial = []
    for node_de, node_para, equip_de, equip_para in ligacoes:
        existente = TbFluxoConsumoPadrao.objects.filter(
            flu_con_pad_from_equipamento_id=equip_de,
            flu_con_pad_to_equipamento_id=equip_para,
            tbcenarios_id=cenario_id,
        ).first()
        if existente:
            consumo_padrao_por_ligacao[(node_de, node_para)] = existente
            continue
        chave = f"{equip_de}-{equip_para}"
        valor_inicial = valores_iniciais_ligacoes.get(chave)
        if valor_inicial in (None, ''):
            de_nome = _nome_equipamento(TbEquipamentos.objects.get(id=equip_de))
            para_nome = _nome_equipamento(TbEquipamentos.objects.get(id=equip_para))
            faltando_valor_inicial.append(
                f"Ligação nova {de_nome} \u2192 {para_nome}: falta informar o valor inicial do consumo padrão."
            )
            continue
        descricao = (
            f"{_nome_equipamento(TbEquipamentos.objects.get(id=equip_de))} --> "
            f"{_nome_equipamento(TbEquipamentos.objects.get(id=equip_para))}"
        )[:100]  # flu_con_pad_descricao tem max_length=100
        consumo_padrao_por_ligacao[(node_de, node_para)] = TbFluxoConsumoPadrao.objects.create(
            flu_con_pad_from_equipamento_id=equip_de,
            flu_con_pad_to_equipamento_id=equip_para,
            tbcenarios_id=cenario_id,
            valor_inicial=valor_inicial,
            flu_con_pad_descricao=descricao,
        )

    if faltando_valor_inicial:
        return False, faltando_valor_inicial

    # Tudo ok -- regrava a tabela filha inteira desse fluxo a partir do grafo.
    TbFluxoProducaoDaugther.objects.filter(mae_id=fluxo.id).delete()
    novas_celulas = []
    for node_de, node_para, equip_de, equip_para in ligacoes:
        coluna, linha = posicoes[node_para]  # PREMISSA 1: posição = a do destino
        novas_celulas.append(TbFluxoProducaoDaugther(
            flu_pro_dau_coluna=coluna,
            flu_pro_dau_linha=linha,
            flu_pro_dau_consumo_padrao=consumo_padrao_por_ligacao[(node_de, node_para)],
            mae_id=fluxo.id,
            tbcenarios_id=cenario_id,
        ))
    TbFluxoProducaoDaugther.objects.bulk_create(novas_celulas)

    # 🌟 CORRIGIDO: aqui salvava flu_pro_dados_fluxo REGENERADO a partir
    # da tabela (em grade, por coluna/linha) -- isso jogava fora a
    # posição exata que o usuário acabou de arrastar no editor, mesmo
    # tendo acabado de salvar. Agora grava exatamente o dados_fluxo que
    # veio do navegador (com as posições dele) -- a única coisa que
    # muda na tabela é a TbFluxoProducaoDaugther (coluna/linha), que já
    # foi regravada acima; a posição na TELA é sempre a que o usuário
    # deixou.
    TbFluxoProducao.objects.filter(id=fluxo.id).update(flu_pro_dados_fluxo=dados_fluxo)

    return True, None


def fluxo_io_desatualizado(fluxo):
    """
    🌟 NOVO: True se TbFluxoProducaoInputOutput (a fonte usada por
    montar_dados_fluxo_a_partir_da_tabela) pode estar desatualizada em
    relação à tabela de cadastro -- ou seja, se a última vez que
    "Atualizar Fluxos de Produção" rodou é anterior à última mudança na
    tabela de cadastro. Usado só pra AVISAR o usuário antes de montar o
    editor (ou o PDF -- o mesmo aviso vale pros dois), nunca pra
    bloquear a ação.
    """
    return not bool(fluxo.flu_pro_input_output_atualizado)