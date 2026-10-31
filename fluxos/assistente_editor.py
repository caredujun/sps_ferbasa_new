"""Serviços de leitura usados pelo Agente IA e pelo painel do editor visual.

Todas as consultas são limitadas à empresa efetiva e ao cenário ativo do
usuário. Este módulo não altera o fluxo nem as tabelas produtivas.
"""
from collections import Counter

from django.db.models import Q

from equipamentos.models import TbEquipamentos
from parameters.models import TbCenarios

from .models import TbFluxoProducao
from .sincronizacao_fluxo_visual import verificar_consistencia_grafo


class ErroContextoEditor(ValueError):
    """Erro seguro e apresentável ao usuário ao resolver o contexto do editor."""


def _empresa_efetiva_usuario(usuario):
    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        raise ErroContextoEditor('Selecione uma empresa antes de consultar os fluxos de produção.')
    return perfil, empresa_id


def _contexto_ativo_usuario(usuario):
    perfil, empresa_id = _empresa_efetiva_usuario(usuario)
    cenario_id = perfil.cenario_ativo_id if perfil else None
    if cenario_id is None:
        raise ErroContextoEditor('Você ainda não tem um cenário ativo escolhido.')

    cenario = TbCenarios.objects_real.filter(id=cenario_id, empresa_id=empresa_id).first()
    if cenario is None:
        raise ErroContextoEditor('O cenário ativo não pertence à empresa selecionada ou não existe mais.')
    return empresa_id, cenario


def rotulo_fluxo(fluxo):
    nome = (fluxo.flu_pro_nome or fluxo.flu_pro_descricao or '').strip()
    return f'{fluxo.id} - {nome}' if nome else f'Fluxo {fluxo.id}'


def url_editor_fluxo(fluxo):
    return f'/fluxo_producao/editor/{fluxo.id}/'


def listar_fluxos_do_cenario_ativo(usuario):
    """Lista somente os fluxos do cenário ativo dentro da empresa efetiva."""
    _, cenario = _contexto_ativo_usuario(usuario)
    fluxos = list(
        TbFluxoProducao.objects.filter(
            tbcenarios_id=cenario.id,
            tbcenarios__empresa_id=cenario.empresa_id,
        )
        .select_related('flu_pro_produto', 'tbcenarios')
        .order_by('flu_pro_descricao', 'id')
    )
    return cenario, fluxos


def resolver_fluxo_do_cenario_ativo(usuario, referencia=None):
    """Resolve id na empresa efetiva; nome/descrição permanecem no cenário ativo."""
    referencia = str(referencia or '').strip()

    # O editor pode estar mostrando um cenário diferente do cenário ativo.
    # Um ID explícito continua seguro porque o filtro de empresa é obrigatório.
    if referencia.isdigit():
        _, empresa_id = _empresa_efetiva_usuario(usuario)
        fluxo = TbFluxoProducao.objects.filter(
            id=int(referencia),
            tbcenarios__empresa_id=empresa_id,
        ).select_related('flu_pro_produto', 'tbcenarios').first()
        if fluxo is not None:
            return fluxo

    cenario, fluxos = listar_fluxos_do_cenario_ativo(usuario)

    if not referencia:
        if len(fluxos) == 1:
            return fluxos[0]
        if not fluxos:
            raise ErroContextoEditor(
                f'O cenário {cenario.numero_sequencial or cenario.id}/{cenario.cen_nome} não possui fluxos de produção.'
            )
        opcoes = '; '.join(rotulo_fluxo(fluxo) for fluxo in fluxos[:10])
        raise ErroContextoEditor(
            f'Encontrei {len(fluxos)} fluxos no cenário ativo. Informe o número ou o nome do fluxo. Opções: {opcoes}.'
        )

    queryset = TbFluxoProducao.objects.filter(
        tbcenarios_id=cenario.id,
        tbcenarios__empresa_id=cenario.empresa_id,
    ).select_related('flu_pro_produto', 'tbcenarios')

    correspondencias = list(
        queryset.filter(
            Q(flu_pro_nome__iexact=referencia) | Q(flu_pro_descricao__iexact=referencia)
        )[:2]
    )
    if len(correspondencias) == 1:
        return correspondencias[0]

    correspondencias = list(
        queryset.filter(
            Q(flu_pro_nome__icontains=referencia) | Q(flu_pro_descricao__icontains=referencia)
        ).order_by('flu_pro_descricao', 'id')[:11]
    )
    if len(correspondencias) == 1:
        return correspondencias[0]
    if len(correspondencias) > 1:
        opcoes = '; '.join(rotulo_fluxo(fluxo) for fluxo in correspondencias[:10])
        raise ErroContextoEditor(f'A referência "{referencia}" corresponde a mais de um fluxo: {opcoes}.')

    raise ErroContextoEditor(f'Não encontrei o fluxo "{referencia}" no cenário ativo.')


def inspecionar_fluxo(fluxo, dados_fluxo=None):
    """Converte um JSON Drawflow em resumo seguro; por padrão usa o grafo salvo."""
    if dados_fluxo is None:
        dados_fluxo = fluxo.flu_pro_dados_fluxo or {}
    dados_fluxo = dados_fluxo if isinstance(dados_fluxo, dict) else {}
    nodes = dados_fluxo.get('drawflow', {}).get('Home', {}).get('data', {}) or {}
    if not isinstance(nodes, dict):
        nodes = {}
    nodes = {str(node_id): node for node_id, node in nodes.items()}

    equipamentos_ids = set()
    equipamento_id_por_node = {}
    problemas = []
    if not nodes:
        problemas.append('O fluxo visual não possui equipamentos.')

    for node_id, node in nodes.items():
        dados_node = node.get('data') if isinstance(node, dict) else {}
        dados_node = dados_node if isinstance(dados_node, dict) else {}
        equipamento_id = dados_node.get('equipamento_id')
        try:
            equipamento_id = int(equipamento_id)
        except (TypeError, ValueError):
            equipamento_id = None
        equipamento_id_por_node[str(node_id)] = equipamento_id
        if equipamento_id is None:
            problemas.append(f'O nó {node_id} não possui um equipamento válido.')
        else:
            equipamentos_ids.add(equipamento_id)

    equipamentos = {
        equipamento.id: equipamento
        for equipamento in TbEquipamentos.objects.filter(
            id__in=equipamentos_ids,
            tbcenarios_id=fluxo.tbcenarios_id,
        ).select_related('equ_codigo')
    }

    ids_fora_do_cenario = equipamentos_ids - set(equipamentos)
    if ids_fora_do_cenario:
        lista = ', '.join(str(item) for item in sorted(ids_fora_do_cenario))
        problemas.append(f'Equipamento(s) fora do cenário ou inexistente(s): {lista}.')

    ligacoes = []
    entradas = Counter()
    saidas = Counter()
    conexoes_vistas = set()
    ids_nodes = {str(node_id) for node_id in nodes}

    def codigo_do_node(node_id):
        node = nodes.get(node_id) or {}
        node = node if isinstance(node, dict) else {}
        dados_node = node.get('data') or {}
        dados_node = dados_node if isinstance(dados_node, dict) else {}
        return dados_node.get('codigo') or node.get('name') or node_id

    for origem_id, node in nodes.items():
        origem_id = str(origem_id)
        outputs = (node.get('outputs') or {}) if isinstance(node, dict) else {}
        outputs = outputs if isinstance(outputs, dict) else {}
        for output in outputs.values():
            for conexao in (output or {}).get('connections', []):
                destino_id = str(conexao.get('node'))
                chave = (origem_id, destino_id)
                if chave in conexoes_vistas:
                    continue
                conexoes_vistas.add(chave)
                if destino_id not in ids_nodes:
                    problemas.append(f'A ligação do nó {origem_id} aponta para o nó inexistente {destino_id}.')
                    continue
                entradas[destino_id] += 1
                saidas[origem_id] += 1
                ligacoes.append({
                    'origem_node_id': origem_id,
                    'destino_node_id': destino_id,
                    'origem_codigo': codigo_do_node(origem_id),
                    'destino_codigo': codigo_do_node(destino_id),
                })

    detalhes_nos = []
    ocorrencias_por_equipamento = Counter(
        equipamento_id for equipamento_id in equipamento_id_por_node.values() if equipamento_id is not None
    )
    for node_id, node in nodes.items():
        node_id = str(node_id)
        node = node if isinstance(node, dict) else {}
        dados_node = node.get('data') or {}
        dados_node = dados_node if isinstance(dados_node, dict) else {}
        equipamento_id = equipamento_id_por_node[node_id]
        equipamento = equipamentos.get(equipamento_id)
        codigo = dados_node.get('codigo') or node.get('name') or f'Nó {node_id}'
        if equipamento is not None:
            codigo = f'{equipamento.equ_codigo.equ_cad_codigo}/{equipamento.equ_ordem_codigo}'
            descricao = equipamento.equ_ordem_descricao or ''
        else:
            descricao = ''
        detalhes_nos.append({
            'node_id': node_id,
            'equipamento_id': equipamento_id,
            'codigo': codigo,
            'descricao': descricao,
            'coluna_ocorrencia': dados_node.get('coluna_ocorrencia'),
            'entradas': entradas[node_id],
            'saidas': saidas[node_id],
            'raiz': entradas[node_id] == 0,
            'terminal': saidas[node_id] == 0,
            'isolado': entradas[node_id] == 0 and saidas[node_id] == 0,
        })

    problemas.extend(verificar_consistencia_grafo(nodes))
    problemas = list(dict.fromkeys(problemas))
    raizes = [node['codigo'] for node in detalhes_nos if node['raiz'] and not node['isolado']]
    terminais = [node['codigo'] for node in detalhes_nos if node['terminal'] and not node['isolado']]
    isolados = [node['codigo'] for node in detalhes_nos if node['isolado']]
    repetidos = [
        {'equipamento_id': equipamento_id, 'ocorrencias': quantidade}
        for equipamento_id, quantidade in sorted(ocorrencias_por_equipamento.items())
        if quantidade > 1
    ]

    cenario = fluxo.tbcenarios
    numero_cenario = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    return {
        'fluxo': {
            'id': fluxo.id,
            'nome': fluxo.flu_pro_nome or '',
            'descricao': fluxo.flu_pro_descricao or '',
            'produto': str(fluxo.flu_pro_produto) if fluxo.flu_pro_produto_id else '',
            'cenario': f'{numero_cenario}/{cenario.cen_nome}',
            'editor_url': url_editor_fluxo(fluxo),
        },
        'resumo': {
            'total_nos': len(detalhes_nos),
            'total_ligacoes': len(ligacoes),
            'total_raizes': len(raizes),
            'total_terminais': len(terminais),
            'total_isolados': len(isolados),
            'total_equipamentos_repetidos': len(repetidos),
        },
        'valido': not problemas,
        'raizes': raizes,
        'terminais': terminais,
        'isolados': isolados,
        'equipamentos_repetidos': repetidos,
        'problemas': problemas,
        'nos': detalhes_nos,
        'ligacoes': ligacoes,
    }


def formatar_inspecao_para_agente(inspecao, incluir_equipamentos=True):
    fluxo = inspecao['fluxo']
    resumo = inspecao['resumo']
    linhas = [
        f"**Fluxo {fluxo['id']} — {fluxo['descricao'] or fluxo['nome']}**",
        f"Cenário: **{fluxo['cenario']}**",
        f"Produto: **{fluxo['produto'] or 'não informado'}**",
        f"- Nós: {resumo['total_nos']}",
        f"- Ligações: {resumo['total_ligacoes']}",
        f"- Entradas do processo: {resumo['total_raizes']}",
        f"- Saídas finais: {resumo['total_terminais']}",
        f"- Isolados: {resumo['total_isolados']}",
    ]

    if inspecao['raizes']:
        linhas.append('Entradas: ' + ', '.join(inspecao['raizes']))
    if inspecao['terminais']:
        linhas.append('Saídas finais: ' + ', '.join(inspecao['terminais']))

    if inspecao['problemas']:
        linhas.append('\n**Problemas encontrados:**')
        linhas.extend(f'- {problema}' for problema in inspecao['problemas'])
    else:
        linhas.append('\n✅ O grafo salvo passou nas verificações de consistência disponíveis.')

    if incluir_equipamentos and inspecao['nos']:
        linhas.append('\n**Equipamentos:**')
        for node in inspecao['nos'][:30]:
            descricao = f" — {node['descricao']}" if node['descricao'] else ''
            linhas.append(
                f"- {node['codigo']}{descricao} "
                f"(entradas: {node['entradas']}, saídas: {node['saidas']})"
            )
        if len(inspecao['nos']) > 30:
            linhas.append(f"- … e mais {len(inspecao['nos']) - 30} nó(s).")

    linhas.append(f"\n[Abrir este fluxo no editor]({fluxo['editor_url']})")
    return '\n'.join(linhas)
