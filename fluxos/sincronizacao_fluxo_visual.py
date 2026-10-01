"""
🌟 NOVO: ponte entre a tabela filha (TbFluxoProducaoDaugther +
TbFluxoConsumoPadrao -- a tabela de CADASTRO do fluxo, com o "de/para"
de cada consumo padrão) e o editor visual de arrastar-e-soltar
(flu_pro_dados_fluxo, formato da biblioteca Drawflow).

🌟 CORRIGIDO (terceira volta): a coluna de cada equipamento é tomada
DIRETO do valor gravado na célula onde ele é remetente (não é
calculada por grafo) -- um equipamento sem quem alimente NÃO vai
automaticamente pra coluna 1; ele fica onde o cadastro diz, porque é
ali que ele entra no processo (ex: uma energia elétrica pode alimentar
direto um equipamento lá na coluna 3). Só quem nunca aparece como
remetente em nenhuma célula (um terminal puro, tipo a expedição final)
tem a coluna calculada: maior coluna entre quem manda pra ele, + 1.
Conferido manualmente contra um exemplo real de 14 células -- bateu
exatamente, equipamento por equipamento -- ver
montar_dados_fluxo_a_partir_da_tabela mais abaixo.

Duas direções:

  - montar_dados_fluxo_a_partir_da_tabela(fluxo): lê a tabela filha,
    monta a cadeia de ligações (quem manda pra quem) e calcula
    coluna/linha de cada equipamento a partir dela -- raiz (sem quem
    alimenta) sempre coluna 1; quem recebe de uma raiz, coluna 2; e
    assim sucessivamente, sempre coluna(destino) = 1 +
    maior_coluna_entre_os_remetentes. Devolve o dicionário no formato
    Drawflow. Chamada automaticamente por um sinal sempre que a tabela
    filha muda (edição no Admin, importação de Excel, etc.) -- ver o
    receiver logo abaixo -- pra flu_pro_dados_fluxo estar sempre em dia.

  - salvar_fluxo_a_partir_do_json(fluxo, dados_fluxo, ...): lê o grafo
    desenhado no editor, verifica consistência no SERVIDOR (não confia
    só na checagem do navegador), calcula coluna/linha do mesmo jeito
    (a posição exata arrastada no canvas NÃO é preservada pra esses
    campos -- vira posição de grade sequencial, sem cruzamento) e
    regrava a tabela filha.
"""
from django.db import transaction
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from equipamentos.models import TbEquipamentos, TbEquipamentosCadastro
from .models import TbFluxoProducao, TbFluxoProducaoDaugther, TbFluxoConsumoPadrao


# Espaçamento usado pra converter coluna/linha em pos_x/pos_y no canvas.
ESPACO_COLUNA = 260
ESPACO_LINHA = 190
MARGEM_X = 50
MARGEM_Y = 50
# 🌟 NOVO: altura do ícone e espaço entre equipamentos na mesma coluna
# são dinâmicos (ver montar_dados_fluxo_a_partir_da_tabela) -- essas
# constantes controlam esse cálculo.
ALTURA_MAXIMA_COLUNA = 900     # altura-alvo (px) pra coluna mais cheia do fluxo
ALTURA_ICONE_PADRAO = 80       # tamanho "normal" do ícone -- usado quando cabe folgado
ALTURA_ICONE_MINIMA = 30       # nunca menor que isso -- ícone fica ilegível abaixo disso
MARGEM_ENTRE_ICONES = 50       # 🌟 CORRIGIDO: era 20 -- com o fluxo reduzido pelo zoom automático da tela (pra caber tudo), 20px lógicos viravam poucos pixels REAIS na tela, quase invisíveis. 50px garante uma folga visível mesmo reduzido.
# 🌟 CORRIGIDO: substitui um fator proporcional (ícone × X) no cálculo
# de altura_card_maxima -- medido a partir do CSS de cada parte do card
# (fluxos/templates/fluxo_producao/editor.html): cabeçalho (padding
# 8px topo+baixo + texto do título, ~36px) + preenchimento do corpo
# (padding 10px topo+baixo, 20px) + margem abaixo do ícone (10px) +
# descrição de até 2 linhas (~38px, fonte 0.85em) -- não encolhe junto
# com o ícone, então entra como valor FIXO, somado ao ícone (que esse
# sim encolhe).
ALTURA_FIXA_NAO_ICONE = 110
# 🌟 CORRIGIDO: chegou a existir aqui uma quebra automática de linha
# quando o fluxo tinha muitas colunas (pra caber na largura da tela) --
# removida a pedido: o fluxo agora sempre fica numa faixa horizontal só,
# do início ao fim, sem quebrar; quem cuida de caber na largura da tela
# é o ZOOM no navegador (ver centralizarFluxoNaTela() em editor.html).


def _nome_equipamento(equipamento):
    cadastro = TbEquipamentosCadastro.objects.filter(id=equipamento.equ_codigo_id).first()
    codigo = cadastro.equ_cad_codigo if cadastro else '?'
    return f"{codigo}/{equipamento.equ_ordem_codigo}"


def _montar_html_no(equipamento, codigo_nome, descricao, altura_icone=80, altura_card_maxima=None):
    # 🌟 Mesmo template usado por adicionarNoEquipamento() no
    # editor.html -- mantém a aparência idêntica entre um nó desenhado
    # manualmente e um recriado aqui a partir da tabela.
    #
    # 🌟 NOVO: altura_icone é dinâmica (calculada por
    # montar_dados_fluxo_a_partir_da_tabela, uma só pra todo o fluxo --
    # ver ali) -- vai como estilo INLINE no <img>, que tem prioridade
    # sobre a regra fixa (180x80) do CSS da página, garantindo que a
    # coluna mais cheia sempre caiba na altura sem sobrepor ícones.
    #
    # 🌟 CORRIGIDO: o cálculo do espaçamento entre equipamentos levava
    # em conta só a altura da IMAGEM -- mas o cartão inteiro (cabeçalho
    # + imagem + texto de descrição) fica mais alto que isso quando a
    # descrição é longa o bastante pra quebrar em 2+ linhas, podendo
    # invadir o espaço do próximo equipamento abaixo. Agora o CARTÃO
    # INTEIRO tem altura MÁXIMA travada (via estilo inline no próprio
    # container), com overflow: hidden -- garante que ele nunca
    # ultrapassa o espaço reservado, custe o que custar ao texto (se a
    # descrição for longa, ela é cortada, mas nunca sobrepõe o vizinho).
    imagem_src = f"/fluxo_producao/equipamento_imagem/{equipamento.id}/"
    placeholder = '/static/fluxo_producao/img/equipamento-placeholder.png'
    estilo_altura_maxima = f' max-height: {altura_card_maxima}px;' if altura_card_maxima else ''
    return (
        f'<div class="equipamento-node" style="width: 200px; overflow: hidden;{estilo_altura_maxima}">'
        '<div class="equipamento-header" style="background-color: #3498db; color: white; padding: 8px;">'
        f'<div class="equipamento-titulo">{codigo_nome}</div></div>'
        '<div class="equipamento-body" style="padding: 10px; background-color: white; display: flex; flex-direction: column; align-items: center; overflow: hidden; position: relative;">'
        '<button type="button" class="selo-link-equipamento" title="Ampliar equipamento" '
        'aria-label="Ampliar equipamento">🔍</button>'
        f'<div style="width: 100%; height: {altura_icone}px; display: flex; align-items: center; justify-content: center; overflow: hidden; margin-bottom: 10px;">'
        f'<img src="{imagem_src}" alt="{codigo_nome}" class="equipamento-img" '
        f'style="width: 180px !important; height: {altura_icone}px !important; object-fit: cover;" '
        f'onerror="this.onerror=null; this.src=\'{placeholder}\';"></div>'
        '<div class="equipamento-detalhes" style="width: 100%; overflow: hidden;">'
        f'<div class="equipamento-descricao" style="font-size: 0.9em; color: #666; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;">{descricao or ""}</div>'
        '</div></div></div>'
    )



def montar_dados_fluxo_a_partir_da_tabela(fluxo, preservar_posicoes_existentes=True):
    """
    Lê TbFluxoProducaoDaugther (+ TbFluxoConsumoPadrao) deste fluxo e
    devolve o dicionário no formato Drawflow. Não salva nada -- quem
    chama decide o que fazer com o resultado.

    preservar_posicoes_existentes=True (padrão): reaproveita o
    pos_x/pos_y que cada OCORRÊNCIA (ver abaixo) já tinha no
    flu_pro_dados_fluxo ATUAL do fluxo (arrastado manualmente no editor
    em algum momento) -- só ocorrência NOVA (que apareceu agora e ainda
    não tinha posição salva) ganha posição calculada em grade. Passe
    False só quando o pedido for de verdade "recomeçar do zero,
    ignorando o que estava desenhado" -- é o que o botão "Criar Fluxo
    no Editor" do Admin faz, de propósito, com aviso prévio ao usuário.

    🌟 CORRIGIDO (quarta volta): o MESMO equipamento pode aparecer mais
    de uma vez no fluxo, em colunas diferentes -- cada vez que ele é
    remetente numa célula com uma coluna gravada DIFERENTE das outras
    vezes vira uma OCORRÊNCIA própria (um nó visual à parte), em vez de
    ser fundido num nó só. Duas células que têm o MESMO equipamento
    como remetente e a MESMA coluna continuam sendo uma ocorrência só
    (um nó, com várias ligações de saída). O destino de cada ligação
    também é identificado por (equipamento, coluna do remetente + 1) --
    então um equipamento que recebe de remetentes em colunas diferentes
    também aparece como ocorrências separadas.
    """
    posicoes_ja_salvas = {}   # (equipamento_id, coluna_gravada_da_ocorrencia) -> (pos_x, pos_y)
    if preservar_posicoes_existentes:
        dados_existentes = (fluxo.flu_pro_dados_fluxo or {}).get('drawflow', {}).get('Home', {}).get('data', {}) or {}
        for node in dados_existentes.values():
            dados_no = node.get('data') or {}
            equip_id = dados_no.get('equipamento_id')
            coluna_ocorrencia = dados_no.get('coluna_ocorrencia')
            if equip_id is not None and coluna_ocorrencia is not None and 'pos_x' in node and 'pos_y' in node:
                posicoes_ja_salvas[(int(equip_id), int(coluna_ocorrencia))] = (node['pos_x'], node['pos_y'])

    # 🌟 CORRIGIDO (terceira volta): a coluna de cada OCORRÊNCIA é
    # tomada DIRETO do valor gravado na célula onde ELE é remetente --
    # sem calcular nada por grafo. Um equipamento "raiz" (sem quem
    # alimente) NÃO vai automaticamente pra coluna 1 -- ele fica na
    # coluna que o cadastro diz, porque é ali que ele entra no processo
    # de verdade (ex: uma energia elétrica pode alimentar direto um
    # equipamento lá na coluna 3, sem passar por nada antes). Só quem
    # NUNCA aparece como remetente em NENHUMA célula (um terminal puro,
    # tipo a expedição final do fluxo) tem a coluna calculada: coluna do
    # remetente + 1. Conferido manualmente contra um exemplo real de 14
    # células -- bateu exatamente, equipamento por equipamento.
    celulas = (TbFluxoProducaoDaugther.objects
               .filter(mae_id=fluxo.id)
               .select_related('flu_pro_dau_consumo_padrao',
                                'flu_pro_dau_consumo_padrao__flu_con_pad_from_equipamento',
                                'flu_pro_dau_consumo_padrao__flu_con_pad_to_equipamento'))

    equipamento_obj_por_id = {}     # equip_id -> objeto TbEquipamentos (só pra montar nome/imagem)
    ligacoes_ocorrencias = []       # ((equip_id_de, coluna_de), (equip_id_para, coluna_para))
    linha_gravada_por_ocorrencia = {}   # (equip_id, coluna) -> flu_pro_dau_linha, quando essa ocorrência é remetente

    for celula in celulas:
        consumo = celula.flu_pro_dau_consumo_padrao
        de = consumo.flu_con_pad_from_equipamento
        para = consumo.flu_con_pad_to_equipamento
        equipamento_obj_por_id[de.id] = de
        equipamento_obj_por_id[para.id] = para

        coluna_de = celula.flu_pro_dau_coluna
        coluna_para = coluna_de + 1  # regra fixa: destino sempre 1 coluna à frente do remetente

        ocorrencia_de = (de.id, coluna_de)
        ocorrencia_para = (para.id, coluna_para)
        ligacoes_ocorrencias.append((ocorrencia_de, ocorrencia_para))
        linha_gravada_por_ocorrencia[ocorrencia_de] = celula.flu_pro_dau_linha

    # Todas as ocorrências que aparecem em algum lado de alguma ligação.
    ocorrencias = set()
    for de_oc, para_oc in ligacoes_ocorrencias:
        ocorrencias.add(de_oc)
        ocorrencias.add(para_oc)

    # Linha de quem só aparece como destino (nunca remetente NESSA
    # coluna específica): média da linha de quem manda pra essa
    # ocorrência -- só como pista de altura, nunca decide a coluna.
    predecessores_por_ocorrencia = {}
    for de_oc, para_oc in ligacoes_ocorrencias:
        predecessores_por_ocorrencia.setdefault(para_oc, []).append(de_oc)

    def linha_da_ocorrencia(oc, visitando=frozenset()):
        if oc in linha_gravada_por_ocorrencia:
            return linha_gravada_por_ocorrencia[oc]
        if oc in visitando:
            return 1  # segurança -- não deveria ocorrer num fluxo válido (sem ciclo)
        preds = predecessores_por_ocorrencia.get(oc, [])
        if not preds:
            return 1
        valores = [linha_da_ocorrencia(p, visitando | {oc}) for p in preds]
        return sum(valores) / len(valores)

    linha_final = {oc: linha_da_ocorrencia(oc) for oc in ocorrencias}

    # Normaliza coluna por ORDEM (rank), não pelo valor absoluto
    # gravado -- evita espaçamento desigual na tela se os números
    # tiverem buracos (ex: colunas 1, 2, 5, sem 3/4 -- a ordem relativa
    # se preserva, só o espaçamento vira uniforme).
    colunas_distintas = sorted({coluna for _, coluna in ocorrencias})
    rank_da_coluna = {c: i + 1 for i, c in enumerate(colunas_distintas)}

    ocorrencias_por_coluna_rank = {}
    for oc in ocorrencias:
        ocorrencias_por_coluna_rank.setdefault(rank_da_coluna[oc[1]], []).append(oc)

    # 🌟 NOVO: reduz cruzamento de ligações entre colunas vizinhas --
    # método do baricentro (o mesmo usado por ferramentas como o
    # Graphviz): a ordem vertical de cada coluna começa pela pista de
    # linha_final (acima), e depois é refinada em várias passadas,
    # reordenando cada coluna pela posição MÉDIA de quem se liga a ela
    # na coluna anterior (ida, esquerda pra direita) e na seguinte
    # (volta, direita pra esquerda), até estabilizar. Não garante zero
    # cruzamento sempre (o problema geral de minimizar cruzamentos é
    # NP-difícil), mas reduz bastante na prática -- confirmado com um
    # caso de teste com cruzamento óbvio (duas ligações em "X" entre
    # duas colunas), que passa a ficar sem cruzamento nenhum.
    for ocs in ocorrencias_por_coluna_rank.values():
        ocs.sort(key=lambda o: (linha_final[o], o[0]))

    posicao_vertical = {}
    for ocs in ocorrencias_por_coluna_rank.values():
        for i, oc in enumerate(ocs):
            posicao_vertical[oc] = i

    sucessores_por_ocorrencia = {}
    for de_oc, para_oc in ligacoes_ocorrencias:
        sucessores_por_ocorrencia.setdefault(de_oc, []).append(para_oc)

    def _reordenar_coluna(col_rank, vizinhos_de):
        ocs = ocorrencias_por_coluna_rank[col_rank]

        def _baricentro(oc):
            vizinhos = vizinhos_de.get(oc, [])
            if not vizinhos:
                return posicao_vertical[oc]  # sem vizinho na coluna adjacente -- mantém onde está
            return sum(posicao_vertical[v] for v in vizinhos) / len(vizinhos)

        ocs.sort(key=lambda o: (_baricentro(o), o[0]))
        for i, oc in enumerate(ocs):
            posicao_vertical[oc] = i

    colunas_rank_ordenadas = sorted(ocorrencias_por_coluna_rank)
    for _ in range(8):  # 8 passadas costuma ser mais que suficiente pra estabilizar
        for col_rank in colunas_rank_ordenadas[1:]:
            _reordenar_coluna(col_rank, predecessores_por_ocorrencia)
        for col_rank in reversed(colunas_rank_ordenadas[:-1]):
            _reordenar_coluna(col_rank, sucessores_por_ocorrencia)

    posicao_ocorrencia = {}   # ocorrencia (equip_id, coluna) -> (coluna_rank, linha_rank)
    for col_rank, ocs in ocorrencias_por_coluna_rank.items():
        for linha_rank, oc in enumerate(ocs, start=1):
            posicao_ocorrencia[oc] = (col_rank, linha_rank)

    # 🌟 CORRIGIDO (segunda vez): a fórmula anterior (ícone * FATOR)
    # tratava o card como se ele encolhesse PROPORCIONALMENTE ao ícone
    # -- mas cabeçalho, preenchimentos e texto da descrição têm tamanho
    # FIXO (não encolhem junto). Num ícone pequeno (coluna cheia), esse
    # conteúdo fixo sozinho já estourava o espaço calculado -- cortava
    # nas colunas mais cheias, mesmo com o ícone pequeno. Agora é
    # ALTURA_FIXA_NAO_ICONE (cabeçalho + preenchimentos + margem +
    # descrição de até 2 linhas -- medido a partir do CSS de cada parte
    # do card) + o ícone (esse sim, encolhe) -- garante espaço
    # suficiente pro conteúdo fixo em QUALQUER tamanho de ícone.
    maior_qtde_na_coluna = max((len(ocs) for ocs in ocorrencias_por_coluna_rank.values()), default=1)
    if maior_qtde_na_coluna > 1:
        espaco_disponivel_por_item = ALTURA_MAXIMA_COLUNA / maior_qtde_na_coluna
        altura_icone = max(
            ALTURA_ICONE_MINIMA,
            min(ALTURA_ICONE_PADRAO, espaco_disponivel_por_item - MARGEM_ENTRE_ICONES - ALTURA_FIXA_NAO_ICONE)
        )
    else:
        altura_icone = ALTURA_ICONE_PADRAO
    # 🌟 CORRIGIDO: altura_icone saía com casas decimais (ex: 54.1px) --
    # imagem redimensionada pra um tamanho fracionário fica borrada em
    # alguns navegadores (não alinha nos pixels da tela). Arredonda pra
    # número inteiro.
    altura_icone = round(altura_icone)
    altura_card_maxima = ALTURA_FIXA_NAO_ICONE + altura_icone  # espaço que o CARD precisa, sem cortar nada
    espaco_linha = altura_card_maxima + MARGEM_ENTRE_ICONES       # + respiro POR FORA, garantido sempre

    # 🌟 NOVO: cada coluna é centralizada verticalmente (mesma ideia já
    # usada no PDF -- ver update_fluxo_celery em tasks.py, que centraliza
    # cada coluna na altura da página) -- uma coluna com só 1 equipamento
    # fica no meio da tela, não colada no topo; uma coluna com muitos
    # ocupa mais espaço, mas ainda centralizada em torno da mesma linha
    # de referência que as outras.
    altura_maxima_entre_colunas = max(
        (len(ocs) * espaco_linha for ocs in ocorrencias_por_coluna_rank.values()), default=espaco_linha
    )
    offset_y_por_coluna = {
        col_rank: (altura_maxima_entre_colunas - len(ocs) * espaco_linha) / 2
        for col_rank, ocs in ocorrencias_por_coluna_rank.items()
    }

    # 🌟 CORRIGIDO: removida a quebra em blocos de colunas -- o fluxo
    # inteiro fica numa faixa horizontal só, por mais colunas que tenha.
    # Fluxo largo demais pra caber na tela não é mais resolvido aqui (em
    # pixel absoluto), e sim no navegador, ajustando o ZOOM da tela pra
    # caber a largura toda -- ver centralizarFluxoNaTela() no
    # editor.html, que usa o mecanismo de zoom da própria biblioteca
    # Drawflow.

    # Monta os nós -- 1 nó por OCORRÊNCIA (equipamento + coluna
    # original), não mais 1 nó por equipamento -- id do nó combina os
    # dois, pra permitir o mesmo equipamento aparecer mais de uma vez,
    # em colunas diferentes.
    nodes = {}
    for oc in ocorrencias:
        equip_id, coluna_original = oc
        equipamento = equipamento_obj_por_id[equip_id]
        chave_no = f"{equip_id}_{coluna_original}"

        if oc in posicoes_ja_salvas:
            pos_x, pos_y = posicoes_ja_salvas[oc]
        else:
            coluna_rank, linha_rank = posicao_ocorrencia[oc]
            pos_x = MARGEM_X + (coluna_rank - 1) * ESPACO_COLUNA
            pos_y = MARGEM_Y + offset_y_por_coluna[coluna_rank] + (linha_rank - 1) * espaco_linha

        codigo_nome = _nome_equipamento(equipamento)
        nodes[chave_no] = {
            'id': chave_no,
            'name': codigo_nome,
            'data': {
                'equipamento_id': equip_id,
                'coluna_ocorrencia': coluna_original,
                'codigo': codigo_nome,
                'imagem_src': f"/fluxo_producao/equipamento_imagem/{equip_id}/",
            },
            'class': 'equipamento',
            'html': _montar_html_no(equipamento, codigo_nome, equipamento.equ_ordem_descricao, altura_icone, altura_card_maxima=altura_card_maxima),
            'typenode': False,
            'inputs': {'input_1': {'connections': []}},
            'outputs': {'output_1': {'connections': []}},
            'pos_x': pos_x,
            'pos_y': pos_y,
        }

    for de_oc, para_oc in ligacoes_ocorrencias:
        chave_de = f"{de_oc[0]}_{de_oc[1]}"
        chave_para = f"{para_oc[0]}_{para_oc[1]}"
        no_de = nodes.get(chave_de)
        no_para = nodes.get(chave_para)
        if no_de is None or no_para is None:
            continue
        no_de['outputs']['output_1']['connections'].append({'node': chave_para, 'output': 'input_1'})
        no_para['inputs']['input_1']['connections'].append({'node': chave_de, 'input': 'output_1'})

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
    TbFluxoProducao.objects.filter(id=fluxo_id).update(
        flu_pro_dados_fluxo=dados,
        flu_pro_data_modificacao=timezone.now(),
    )


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
    ⚠️ SEM USO no momento -- salvar_fluxo_a_partir_do_json parou de
    chamar esta função (não regrava mais coluna/linha na tabela filha a
    partir do editor, a pedido). Deixada aqui, intacta, caso volte a
    ser necessária depois -- mas nada no arquivo chama ela agora.

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
    Recebe o JSON exportado pelo editor (Drawflow) e grava SÓ a imagem
    (flu_pro_dados_fluxo) -- nunca mexe em TbFluxoProducaoDaugther nem
    em TbFluxoConsumoPadrao. Devolve (True, None) se salvou, ou
    (False, lista_de_problemas) se recusou -- nesse caso, nada é
    alterado no banco.

    🌟 CORRIGIDO: até aqui, salvar o fluxo no editor também REGRAVAVA A
    TABELA FILHA inteira (apagando e recriando TbFluxoProducaoDaugther
    a partir do desenho, criando TbFluxoConsumoPadrao novos quando
    necessário) -- um efeito colateral grave: qualquer ajuste puramente
    visual no editor (só reposicionar um equipamento, por exemplo)
    alterava o fluxo de produção DE VERDADE. A pedido, isso foi
    removido por completo -- salvar no editor agora é só uma FOTO
    (posição de cada equipamento na tela); a tabela filha é o dado de
    produção de verdade e só muda por onde sempre mudou (Admin,
    importação de Excel) -- nunca pelo editor visual.

    valores_iniciais_ligacoes: mantido no parâmetro só por
    compatibilidade com quem já chama esta função (views_fluxos.py) --
    não é mais usado pra nada aqui, já que nenhum TbFluxoConsumoPadrao é
    criado a partir do editor.
    """
    nodes = (dados_fluxo or {}).get('drawflow', {}).get('Home', {}).get('data', {}) or {}

    if not nodes:
        return False, ["O fluxo está vazio -- arraste ao menos um equipamento antes de salvar."]

    problemas = verificar_consistencia_grafo(nodes)
    if problemas:
        return False, problemas

    equipamentos_ids = set()
    for node_id, node in nodes.items():
        equipamento_id = node.get('data', {}).get('equipamento_id')
        if equipamento_id is None:
            return False, [f"O equipamento do nó \"{node.get('name')}\" não tem equipamento_id -- foi criado de forma inválida."]
        try:
            equipamentos_ids.add(int(equipamento_id))
        except (TypeError, ValueError):
            return False, [f"O equipamento do nó \"{node.get('name')}\" tem equipamento_id inválido."]

    # Defesa em profundidade: mesmo que alguém manipule o JSON enviado
    # diretamente à API, nunca aceita um equipamento de outro cenário.
    equipamentos_validos = set(
        TbEquipamentos.objects.filter(
            id__in=equipamentos_ids,
            tbcenarios_id=fluxo.tbcenarios_id,
        ).values_list('id', flat=True)
    )
    equipamentos_fora_do_cenario = equipamentos_ids - equipamentos_validos
    if equipamentos_fora_do_cenario:
        ids_invalidos = ', '.join(str(i) for i in sorted(equipamentos_fora_do_cenario))
        return False, [
            f"O fluxo contém equipamento(s) que não pertencem a este cenário: {ids_invalidos}."
        ]

    TbFluxoProducao.objects.filter(id=fluxo.id).update(
        flu_pro_dados_fluxo=dados_fluxo,
        flu_pro_data_modificacao=timezone.now(),
    )

    return True, None
