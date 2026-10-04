"""
Geração de fluxos de produção por produto -- versão integrada e validada.

Uso no shell Django:

    from fluxos.gerar_fluxos_por_produto import calcular_fluxos_produto, gerar_linhas_do_plano

    resultado = calcular_fluxos_produto(2076)
    print(resultado['total_fluxos'])  # 3240
    for terminal, planos in resultado['planos_por_terminal'].items():
        print(terminal, len(planos))

    # Pra um plano específico, gerar as linhas (coluna, linha, consumo_padrao_id)
    # prontas pra gravar em TbFluxoProducaoDaugther:
    linhas = gerar_linhas_do_plano(resultado['planos_por_terminal']['BRIT EXP/6'][0])

Validado contra o fluxo real mae_id=734327 (produto 2076, cenário 32):
bate exatamente -- mesmas colunas, mesmos consumos padrão por coluna. A
ordem das linhas DENTRO de uma mesma coluna pode diferir (não existe uma
ordem "certa" definida -- qualquer ordem funciona igual no editor).

🌟 A REGRA DE DUPLICAÇÃO (a parte que levou mais tempo pra descobrir):
    Quando um equipamento alimenta MAIS DE UM consumidor distinto dentro
    do MESMO fluxo (ex: TRANSP 02/2 alimentando um forno "cedo" na cadeia
    e outro forno "tarde" na cadeia), a ligação que alimenta ESSE
    equipamento (não as ligações mais atrás dele) é desenhada uma vez por
    consumidor. A expansão pra trás desse equipamento também só continua
    até esse mesmo número de vezes -- ou seja, os antepassados dele (o que
    alimenta ele) continuam aparecendo só 1 vez, mesmo que o equipamento em
    si apareça mais de uma vez.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import product
from typing import Dict, List, Tuple

from django.db import connection, transaction

from equipamentos.models import TbEquipamentos
from fluxos.models import TbFluxoConsumoPadrao, TbFluxoProducao, TbFluxoProducaoDaugther
from produtos.models import TbProdutos


class GeracaoFluxoError(RuntimeError):
    """Erro de validação da geração/contagem de fluxos."""


def _cenario_do_produto(produto_id: int) -> int:
    try:
        return TbProdutos.objects.get(id=produto_id).tbcenarios_id
    except TbProdutos.DoesNotExist:
        raise GeracaoFluxoError(f"Produto id={produto_id} não encontrado.")


def _equipamentos_do_produto(produto_id: int, cenario_id: int) -> Dict[int, TbEquipamentos]:
    qs = (
        TbEquipamentos.objects.filter(
            equ_produtos__id=produto_id,
            tbcenarios_id=cenario_id,
        )
        .select_related("equ_codigo", "equ_tipo_producao")
        .distinct()
    )
    equipamentos = {obj.id: obj for obj in qs}
    if not equipamentos:
        raise GeracaoFluxoError(
            f"Nenhum equipamento/ordem foi encontrado para produto={produto_id}, "
            f"cenário={cenario_id} (confira o campo equ_produtos)."
        )
    return equipamentos


def _criterio_de(equipamento: TbEquipamentos) -> str:
    tipo = getattr(equipamento, "equ_tipo_producao", None)
    criterio = (getattr(tipo, "tip_criterio_para_quebra_fluxo", None) or "").strip()
    if not criterio:
        raise GeracaoFluxoError(
            f"O equipamento/ordem id={equipamento.id} "
            f"({equipamento.equ_codigo.equ_cad_codigo}/{equipamento.equ_ordem_codigo}) "
            "não tem tip_criterio_para_quebra_fluxo preenchido."
        )
    return criterio


def _montar_grafo(equipamentos: Dict[int, TbEquipamentos], cenario_id: int):
    ids = set(equipamentos)
    qs = TbFluxoConsumoPadrao.objects.filter(
        tbcenarios_id=cenario_id,
        flu_con_pad_from_equipamento_id__in=ids,
        flu_con_pad_to_equipamento_id__in=ids,
    ).select_related("flu_con_pad_from_equipamento__equ_tipo_producao")

    entradas_por_destino = defaultdict(list)
    saidas_de = defaultdict(list)
    for consumo in qs:
        origem = consumo.flu_con_pad_from_equipamento
        criterio = _criterio_de(origem)
        to_id = consumo.flu_con_pad_to_equipamento_id
        entradas_por_destino[to_id].append((origem.id, criterio, consumo.id))
        saidas_de[origem.id].append(to_id)

    return entradas_por_destino, saidas_de


def _encontrar_terminais(
    equipamentos: Dict[int, TbEquipamentos],
    saidas_de: Dict[int, list],
) -> List[TbEquipamentos]:
    expedicoes = [
        equipamento
        for equipamento in equipamentos.values()
        if bool(getattr(equipamento.equ_codigo, "equ_cad_expedicao", False))
    ]
    terminais = [e for e in expedicoes if not saidas_de.get(e.id)]

    if not terminais:
        detalhes = ", ".join(
            f"{e.equ_codigo.equ_cad_codigo}/{e.equ_ordem_codigo}" for e in expedicoes
        ) or "nenhum"
        raise GeracaoFluxoError(
            "Nenhum equipamento de expedição terminal foi encontrado "
            f"(equipamentos marcados como expedição no produto: {detalhes})."
        )
    return terminais


def _original_de(equipamento_id: int, equipamentos: Dict[int, TbEquipamentos]) -> int:
    eq = equipamentos.get(equipamento_id)
    clone_de_id = getattr(eq, "equ_e_clone_de_id", None) if eq else None
    return clone_de_id if clone_de_id is not None else equipamento_id


def _e_clone(equipamento_id: int, equipamentos: Dict[int, TbEquipamentos]) -> bool:
    eq = equipamentos.get(equipamento_id)
    return bool(eq and eq.equ_e_clone_de_id is not None)


def _expandir_para_tras(
    destino_id: int,
    entradas_por_destino,
    equipamentos: Dict[int, TbEquipamentos],
    pilha: Tuple[int, ...] = (),
    max_fluxos: int = 200000,
) -> List[Tuple[Tuple[int, str, int], ...]]:
    """Fase 1: escolhe o CONJUNTO de equipamentos de cada fluxo (sem
    duplicar nada -- usado pra contar e pra saber QUAIS equipamentos
    entram em cada fluxo). A duplicação de linhas pro editor acontece
    depois, na fase 2 (gerar_linhas_do_plano)."""
    if destino_id in pilha:
        raise GeracaoFluxoError(f"Ciclo detectado envolvendo o equipamento id={destino_id}.")

    entradas = entradas_por_destino.get(destino_id, [])
    if not entradas:
        return [tuple()]

    grupos = defaultdict(list)
    for from_id, criterio, cp_id in entradas:
        grupos[criterio].append((from_id, criterio, cp_id))

    alternativas_por_criterio = []
    for criterio in sorted(grupos):
        # 🌟 Clones contam como UMA alternativa só -- entre um original e
        # seu clone no mesmo grupo, fica só o clone (ele existe
        # especificamente pra servir ESSE destino).
        membros = sorted(
            grupos[criterio],
            key=lambda m: (not _e_clone(m[0], equipamentos), m[2]),
        )
        vistos_originais = set()
        membros_efetivos = []
        for from_id, criterio_m, cp_id in membros:
            original_id = _original_de(from_id, equipamentos)
            if original_id in vistos_originais:
                continue
            vistos_originais.add(original_id)
            membros_efetivos.append((from_id, criterio_m, cp_id))

        alternativas = []
        for from_id, criterio_m, cp_id in membros_efetivos:
            for caminho_anterior in _expandir_para_tras(
                from_id, entradas_por_destino, equipamentos,
                pilha + (destino_id,), max_fluxos,
            ):
                alternativas.append(caminho_anterior + ((from_id, criterio_m, cp_id),))
        alternativas_por_criterio.append(alternativas)

    total_estimado = 1
    for alternativas in alternativas_por_criterio:
        total_estimado *= max(len(alternativas), 1)
    if total_estimado > max_fluxos:
        raise GeracaoFluxoError(
            f"A expansão excederia max_fluxos={max_fluxos} em destino={destino_id}."
        )

    resultado = []
    for combinacao in product(*alternativas_por_criterio):
        caminho_completo = tuple(a for parte in combinacao for a in parte)
        resultado.append(caminho_completo)
    return resultado


def calcular_fluxos_produto(produto_id: int, *, max_fluxos: int = 200000) -> dict:
    """Ponto de entrada único: só pede o produto_id."""
    cenario_id = _cenario_do_produto(produto_id)
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    entradas_por_destino, saidas_de = _montar_grafo(equipamentos, cenario_id)
    terminais = _encontrar_terminais(equipamentos, saidas_de)

    planos_por_terminal = {}
    terminais_info = []
    for terminal in terminais:
        rotulo = f"{terminal.equ_codigo.equ_cad_codigo}/{terminal.equ_ordem_codigo}"
        planos = _expandir_para_tras(
            terminal.id, entradas_por_destino, equipamentos, max_fluxos=max_fluxos,
        )
        planos_por_terminal[rotulo] = planos
        terminais_info.append((terminal.id, rotulo, len(planos)))

    total_fluxos = sum(qtde for _, _, qtde in terminais_info)

    return {
        "produto_id": produto_id,
        "cenario_id": cenario_id,
        "total_fluxos": total_fluxos,
        "terminais": terminais_info,
        "planos_por_terminal": planos_por_terminal,
    }


def gerar_linhas_do_plano(
    plano: Tuple[Tuple[int, str, int], ...],
    *,
    cp_para_to: Dict[int, int] = None,
) -> List[dict]:
    """
    Fase 2: a partir de UM plano já escolhido (uma das listas dentro de
    planos_por_terminal), gera as linhas (coluna, linha, consumo_padrao_id)
    prontas pra TbFluxoProducaoDaugther.

    🌟 Regra de duplicação: uma ligação (de -> para) é desenhada uma vez
    pra cada consumidor DISTINTO que o lado "para" dela tem, dentro desse
    MESMO plano. A expansão pra trás de um equipamento só continua até
    esse mesmo número de vezes -- os antepassados dele continuam
    aparecendo só uma vez, mesmo que ele próprio apareça mais de uma vez.

    VALIDADO contra o fluxo real mae_id=734327: bate exatamente (mesmas
    colunas, mesmos consumo_padrao_id por coluna).

    cp_para_to: se já tiver um mapa cp_id->to_id pronto (ex: construído
    uma vez só pra todo o produto, em comparar_fluxos_existentes),
    passa aqui pra evitar uma consulta ao banco POR PLANO -- importante
    quando essa função é chamada milhares de vezes em sequência.
    """
    # 🌟 CORRIGIDO: o "plano" bruto (vindo de _expandir_para_tras) pode ter
    # o MESMO consumo_padrao_id repetido sem necessidade -- um equipamento
    # alcançado por vários caminhos internos da recursão, sem relação com
    # a duplicação LEGÍTIMA (equipamento usado em mais de um ponto do
    # fluxo). Reduz ao conjunto de cp_ids DISTINTOS primeiro; a repetição
    # correta é recriada abaixo, pela regra de "uma vez por consumidor".
    plano_distinto = list({cp_id: (from_id, criterio, cp_id) for from_id, criterio, cp_id in plano}.values())

    ids_cp = [cp_id for (_, _, cp_id) in plano_distinto]
    if cp_para_to is not None:
        to_do_cp = {cp_id: cp_para_to[cp_id] for cp_id in ids_cp}
    else:
        consumos = TbFluxoConsumoPadrao.objects.filter(id__in=ids_cp).values(
            "id", "flu_con_pad_from_equipamento_id", "flu_con_pad_to_equipamento_id"
        )
        to_do_cp = {c["id"]: c["flu_con_pad_to_equipamento_id"] for c in consumos}

    entradas_por_destino = defaultdict(list)
    out_degree = defaultdict(int)
    for from_id, criterio, cp_id in plano_distinto:
        to_id = to_do_cp[cp_id]
        entradas_por_destino[to_id].append((from_id, cp_id))
        out_degree[from_id] += 1

    todos_from = set(from_id for (from_id, _, _) in plano_distinto)
    todos_to = set(to_do_cp.values())
    terminais_do_plano = todos_to - todos_from
    if len(terminais_do_plano) != 1:
        raise GeracaoFluxoError(
            f"Esperava exatamente 1 terminal dentro do plano, achei {len(terminais_do_plano)}."
        )
    terminal_id = next(iter(terminais_do_plano))

    contador_processamentos = defaultdict(int)
    linhas_por_coluna = defaultdict(int)
    resultado = []

    def processar(equip_id: int, coluna: int):
        for from_id, cp_id in entradas_por_destino.get(equip_id, []):
            coluna_de = coluna - 1
            linhas_por_coluna[coluna_de] += 1
            linha = linhas_por_coluna[coluna_de]
            resultado.append({
                "flu_pro_dau_coluna": coluna_de,
                "flu_pro_dau_linha": linha,
                "flu_pro_dau_consumo_padrao_id": cp_id,
            })
            contador_processamentos[from_id] += 1
            limite = max(out_degree.get(from_id, 0), 1)
            if contador_processamentos[from_id] <= limite:
                processar(from_id, coluna_de)

    maior_profundidade = _maior_profundidade(terminal_id, entradas_por_destino)
    processar(terminal_id, maior_profundidade + 1)

    return resultado


def _maior_profundidade(terminal_id: int, entradas_por_destino, memo=None) -> int:
    if memo is None:
        memo = {}
    if terminal_id in memo:
        return memo[terminal_id]
    entradas = entradas_por_destino.get(terminal_id, [])
    if not entradas:
        memo[terminal_id] = 0
        return 0
    profundidade = 1 + max(
        _maior_profundidade(from_id, entradas_por_destino, memo)
        for from_id, _ in entradas
    )
    memo[terminal_id] = profundidade
    return profundidade


_TRANSP_EXCLUIDOS = {"TRANSP 02/2", "TRANSP 03/1", "TRANSP 03/2"}


def _construir_descricao(
    plano: Tuple[Tuple[int, str, int], ...],
    cp_para_to: Dict[int, int],
    equipamentos: Dict[int, TbEquipamentos],
    produto_codigo: str,
) -> str:
    """
    Monta a descrição no mesmo estilo dos fluxos reais, ex:
        "FECR B/C C15 FN01/1 (MINE 01/1 - ENERGIA 01/1) --> FN03/9 (ENERGIA 01/1) --> LING CONV/2 --> FN04/3 (ENERGIA 01/1)"

    Regras:
    - Cadeia principal: FORNOS ("FN..."), REFINO e LING -- demais
      equipamentos de passagem (BRIT, EXP CAL, CALCINAÇÃO, QUARTZO etc.)
      são ignorados.
    - Insumos entre parênteses (só pra itens da cadeia principal): MINE,
      TRANSP e ENERGIA diretos.
        - TRANSP 02/2, TRANSP 03/1 e TRANSP 03/2 NUNCA aparecem (nem
          substituídos).
        - TRANSP 02/1 é SUBSTITUÍDO pelo equipamento que alimenta ele
          (ex: MINE 01/1), em vez de aparecer como "TRANSP 02/1".
    - "CT " na frente do código do forno, SE o produto for FESI ou FESICR
      E esse forno específico NÃO receber de TRANSP 01/1.
    """
    # 🌟 CORRIGIDO: mesma deduplicação de gerar_linhas_do_plano -- o plano
    # bruto pode ter o mesmo cp_id repetido sem necessidade.
    plano_distinto = list({cp_id: (from_id, criterio, cp_id) for from_id, criterio, cp_id in plano}.values())

    # 🌟 CORRIGIDO: essa função recebia entradas_por_destino mas nunca
    # usava -- consultava o banco de novo, do zero, a cada um dos
    # milhares de planos (achado depois de uma geração real travar por
    # minutos). Agora recebe cp_para_to já pronto (montado uma vez só
    # pro produto inteiro, pelo chamador), sem consulta nenhuma aqui.
    ids_cp = [cp_id for (_, _, cp_id) in plano_distinto]
    to_do_cp = {cp_id: cp_para_to[cp_id] for cp_id in ids_cp}

    entradas_locais = defaultdict(list)
    for from_id, criterio, cp_id in plano_distinto:
        to_id = to_do_cp[cp_id]
        entradas_locais[to_id].append(from_id)

    todos_from = set(from_id for (from_id, _, _) in plano_distinto)
    todos_to = set(to_do_cp.values())
    terminal_id = next(iter(todos_to - todos_from))

    def codigo(eid: int) -> str:
        eq = equipamentos.get(eid)
        return eq.equ_codigo.equ_cad_codigo if eq else ""

    def rotulo(eid: int) -> str:
        eq = equipamentos.get(eid)
        return f"{eq.equ_codigo.equ_cad_codigo}/{eq.equ_ordem_codigo}" if eq else str(eid)

    def e_backbone(eid: int) -> bool:
        cod = codigo(eid)
        return cod.startswith("FN") or cod.startswith("REFINO") or cod.startswith("LING")

    def e_forno(eid: int) -> bool:
        return codigo(eid).startswith("FN")

    def e_insumo(eid: int) -> bool:
        cod = codigo(eid)
        return cod.startswith("MINE") or cod.startswith("TRANSP") or cod.startswith("ENERGIA")

    def processa_insumo(from_id: int):
        rot = rotulo(from_id)
        if rot in _TRANSP_EXCLUIDOS or rot == "TRANSP 01/1":
            # 🌟 TRANSP 01/1 não aparece no parêntese -- vira o prefixo
            # "CP " no código do forno (ver abaixo).
            return None
        if rot == "TRANSP 02/1":
            fontes = entradas_locais.get(from_id, [])
            return rotulo(fontes[0]) if fontes else rot
        return rot

    visitados = set()
    sequencia = []

    def percorrer(equip_id: int):
        for from_id in entradas_locais.get(equip_id, []):
            sequencia.append(from_id)
            if from_id not in visitados:
                visitados.add(from_id)
                percorrer(from_id)

    percorrer(terminal_id)
    sequencia.reverse()

    def forno_produz_fesi_ou_fesicr(eid: int) -> bool:
        # 🌟 CORRIGIDO: é o tipo de produção DO PRÓPRIO equipamento/ordem
        # (equ_tipo_producao.tip_nome), não o produto do fluxo inteiro --
        # um forno intermediário pode produzir algo diferente do produto
        # final da cadeia.
        eq = equipamentos.get(eid)
        tipo = getattr(eq, "equ_tipo_producao", None) if eq else None
        tip_nome = (getattr(tipo, "tip_nome", None) or "").upper()
        return "FESI" in tip_nome  # cobre tanto "FESI..." quanto "FESICR..."

    partes = []
    for eid in sequencia:
        if not e_backbone(eid):
            continue
        rotulo_item = rotulo(eid)
        if e_forno(eid):
            tem_transp01 = any(rotulo(f) == "TRANSP 01/1" for f in entradas_locais.get(eid, []))
            if tem_transp01:
                rotulo_item = f"{rotulo_item} CP"
            elif forno_produz_fesi_ou_fesicr(eid):
                rotulo_item = f"{rotulo_item} CT"
        insumos = []
        for f in entradas_locais.get(eid, []):
            if e_insumo(f):
                processado = processa_insumo(f)
                if processado is not None:
                    insumos.append(processado)
        texto = f"{rotulo_item} ({' - '.join(insumos)})" if insumos else rotulo_item
        partes.append(texto)

    if not partes:
        return produto_codigo
    return f"{produto_codigo} {' --> '.join(partes)}"


@transaction.atomic
def gravar_um_fluxo(
    produto_id: int,
    cenario_id: int,
    plano: Tuple[Tuple[int, str, int], ...],
    *,
    descricao: str = "",
) -> int:
    """
    Grava UM fluxo (a partir de um plano já escolhido) em TbFluxoProducao +
    TbFluxoProducaoDaugther. NÃO apaga nem substitui fluxos existentes --
    só cria um novo. Retorna o id da mãe criada (mae_id), pra você achar
    fácil no Admin/editor.

    A descrição recebe o prefixo "[TESTE-GERADOR]" automaticamente, pra
    ficar fácil de achar (e apagar depois) os fluxos de teste. Se você não
    passar "descricao" explicitamente, ela é montada sozinha no mesmo
    estilo dos fluxos reais (produto + cadeia de escolhas, ex:
    "FESI 75 STD FN09/1 (ENERGIA 01/1) --> LING CM/6").
    """
    linhas = gerar_linhas_do_plano(plano)

    if not descricao:
        produto_codigo = TbProdutos.objects.get(id=produto_id).pro_codigo
        equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
        entradas_por_destino, _ = _montar_grafo(equipamentos, cenario_id)
        cp_para_to = {
            cp_id: to_id
            for to_id, entradas in entradas_por_destino.items()
            for (_, _, cp_id) in entradas
        }
        descricao = _construir_descricao(plano, cp_para_to, equipamentos, produto_codigo)

    mae = TbFluxoProducao.objects.create(
        flu_pro_descricao=(f"[TESTE-GERADOR] {descricao}".strip())[:150],
        flu_pro_produto_id=produto_id,
        flu_pro_ativo=True,
        tbcenarios_id=cenario_id,
    )
    TbFluxoProducaoDaugther.objects.bulk_create([
        TbFluxoProducaoDaugther(
            mae_id=mae.id,
            tbcenarios_id=cenario_id,
            flu_pro_dau_coluna=linha["flu_pro_dau_coluna"],
            flu_pro_dau_linha=linha["flu_pro_dau_linha"],
            flu_pro_dau_consumo_padrao_id=linha["flu_pro_dau_consumo_padrao_id"],
        )
        for linha in linhas
    ])

    # 🌟 NOVO: roda a verificação automaticamente -- sem isso, flu_pro_erro
    # fica parado no valor padrão ('SIM') até alguém rodar manualmente a
    # ação "Verificar Erro" no Admin.
    with connection.cursor() as cursor:
        cursor.execute("call public.verifica_fluxo(%s)", [mae.id])

    return mae.id


def _assinatura_estrutural(pares_coluna_cp) -> frozenset:
    """
    🌟 CORRIGIDO: a assinatura agora é ESTRUTURAL -- (coluna, consumo_
    padrao_id) de cada linha, não só o conjunto solto de equipamentos
    usados. Isso importa porque um fluxo pode usar exatamente o conjunto
    CERTO de equipamentos mas estar arrumado ERRADO nas colunas (caso
    real: mae_id=737567, gravado errado pelo editor antigo) -- a versão
    antiga dessa função não pegava esse tipo de erro.

    A coluna é NORMALIZADA pela distância até a ÚLTIMA coluna do próprio
    fluxo (maior_coluna - coluna), em vez de usar o número absoluto --
    assim a comparação não depende de qual número exato de coluna cada
    lado usa como ponto de partida, só da ESTRUTURA relativa.

    A "linha" fica de fora de propósito: já validamos nesta conversa que
    a ordem dentro de uma mesma coluna é arbitrária -- duas
    representações do MESMO fluxo podem numerar linha diferente sem
    nenhum significado diferente por trás.
    """
    pares = list(pares_coluna_cp)
    if not pares:
        return frozenset()
    maior_coluna = max(c for c, _ in pares)
    return frozenset((maior_coluna - c, cp_id) for c, cp_id in pares)


def comparar_fluxos_existentes(produto_id: int, *, max_fluxos: int = 200000) -> dict:
    """
    Calcula os fluxos NOVOS (sem gravar nada) e compara com os que já
    existem em TbFluxoProducao/TbFluxoProducaoDaugther pro mesmo produto,
    pela assinatura ESTRUTURAL (coluna + consumo_padrao_id de cada
    linha) -- não só a quantidade total, nem só o conjunto solto de
    equipamentos usados.

    Compara por MULTICONJUNTO (Counter), não por conjunto simples -- se
    por acaso existir mais de um fluxo gravado com a mesma assinatura
    exata (duplicata de verdade), cada ocorrência conta separada.

    Retorna um dicionário com:
        cenario_id, qtd_existente, qtd_novo, qtd_iguais, mudou (bool),
        planos_por_terminal (pra reaproveitar em gravar_todos_fluxos_produto
        sem recalcular)
    """
    resultado_calculo = calcular_fluxos_produto(produto_id, max_fluxos=max_fluxos)
    cenario_id = resultado_calculo["cenario_id"]

    # 🌟 Monta cp_id -> to_id UMA VEZ SÓ pro produto inteiro, pra não
    # precisar consultar o banco de novo a cada um dos milhares de
    # planos (gerar_linhas_do_plano aceita esse mapa pronto agora).
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    entradas_por_destino, _ = _montar_grafo(equipamentos, cenario_id)
    cp_para_to = {
        cp_id: to_id
        for to_id, entradas in entradas_por_destino.items()
        for (_, _, cp_id) in entradas
    }

    # 🌟 guarda um plano REPRESENTANTE de cada assinatura nova -- pra
    # poder criar só os que faltam, sem ter que escolher de novo entre
    # as alternativas (reaproveita o cálculo já feito).
    plano_por_assinatura = {}
    assinaturas_novas = Counter()
    for planos in resultado_calculo["planos_por_terminal"].values():
        for plano in planos:
            linhas = gerar_linhas_do_plano(plano, cp_para_to=cp_para_to)
            assinatura = _assinatura_estrutural(
                (l["flu_pro_dau_coluna"], l["flu_pro_dau_consumo_padrao_id"]) for l in linhas
            )
            assinaturas_novas[assinatura] += 1
            plano_por_assinatura.setdefault(assinatura, plano)

    maes_existentes = list(
        TbFluxoProducao.objects.filter(flu_pro_produto_id=produto_id, tbcenarios_id=cenario_id)
        .values_list("id", flat=True)
    )
    daugthers_existentes = TbFluxoProducaoDaugther.objects.filter(
        mae_id__in=maes_existentes
    ).values_list("mae_id", "flu_pro_dau_coluna", "flu_pro_dau_consumo_padrao_id")

    pares_por_mae = defaultdict(list)
    for mae_id, coluna, cp_id in daugthers_existentes:
        pares_por_mae[mae_id].append((coluna, cp_id))

    # 🌟 guarda, por assinatura, QUAIS mae_id existentes têm ela -- pra
    # saber exatamente quais apagar (só os que sobram, nunca os certos).
    mae_ids_por_assinatura = defaultdict(list)
    assinaturas_existentes = Counter()
    for mae_id in maes_existentes:
        assinatura = _assinatura_estrutural(pares_por_mae.get(mae_id, []))
        assinaturas_existentes[assinatura] += 1
        mae_ids_por_assinatura[assinatura].append(mae_id)

    comuns = assinaturas_novas & assinaturas_existentes
    sobrando_no_banco = assinaturas_existentes - assinaturas_novas  # errados/a mais
    faltando_no_banco = assinaturas_novas - assinaturas_existentes  # novos/a menos

    # 🌟 NUNCA apaga mais do que o necessário pra cada assinatura: só os
    # que sobram ALÉM do que já está certo.
    mae_ids_a_remover = []
    for assinatura, qtd in sobrando_no_banco.items():
        mae_ids_a_remover.extend(mae_ids_por_assinatura[assinatura][:qtd])

    planos_a_criar = []
    for assinatura, qtd in faltando_no_banco.items():
        planos_a_criar.extend([plano_por_assinatura[assinatura]] * qtd)

    qtd_iguais = sum(comuns.values())

    return {
        "produto_id": produto_id,
        "cenario_id": cenario_id,
        "qtd_existente": len(maes_existentes),
        "qtd_novo": sum(assinaturas_novas.values()),
        "qtd_iguais": qtd_iguais,
        "mudou": bool(mae_ids_a_remover) or bool(planos_a_criar),
        "mae_ids_a_remover": mae_ids_a_remover,
        "planos_a_criar": planos_a_criar,
        "cp_para_to": cp_para_to,
    }


@transaction.atomic
@transaction.atomic
def sincronizar_fluxos_produto(
    produto_id: int,
    cenario_id: int,
    mae_ids_a_remover: List[int],
    planos_a_criar: List[Tuple[Tuple[int, str, int], ...]],
    *,
    cp_para_to: Dict[int, int] = None,
) -> dict:
    """
    🌟 Aplica só a DIFERENÇA entre o banco e o cálculo novo (vinda de
    comparar_fluxos_existentes): apaga SÓ os fluxos que sobram/estão
    errados (mae_ids_a_remover) e cria SÓ os que estão faltando
    (planos_a_criar) -- nunca mexe no que já está certo. Isso evita
    apagar e regravar milhares de fluxos quando só 1 está errado.

    Retorna {'removidos': int, 'criados': int}.
    """
    if mae_ids_a_remover:
        TbFluxoProducao.objects.filter(id__in=mae_ids_a_remover).delete()

    produto_codigo = TbProdutos.objects.get(id=produto_id).pro_codigo
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    if cp_para_to is None:
        entradas_por_destino, _ = _montar_grafo(equipamentos, cenario_id)
        cp_para_to = {
            cp_id: to_id
            for to_id, entradas in entradas_por_destino.items()
            for (_, _, cp_id) in entradas
        }

    mae_ids_criados = []
    for plano in planos_a_criar:
        descricao = _construir_descricao(plano, cp_para_to, equipamentos, produto_codigo)
        linhas = gerar_linhas_do_plano(plano, cp_para_to=cp_para_to)

        mae = TbFluxoProducao.objects.create(
            flu_pro_descricao=descricao[:150],
            flu_pro_produto_id=produto_id,
            flu_pro_ativo=True,
            tbcenarios_id=cenario_id,
        )
        TbFluxoProducaoDaugther.objects.bulk_create([
            TbFluxoProducaoDaugther(
                mae_id=mae.id,
                tbcenarios_id=cenario_id,
                flu_pro_dau_coluna=linha["flu_pro_dau_coluna"],
                flu_pro_dau_linha=linha["flu_pro_dau_linha"],
                flu_pro_dau_consumo_padrao_id=linha["flu_pro_dau_consumo_padrao_id"],
            )
            for linha in linhas
        ])
        mae_ids_criados.append(mae.id)

    # Verifica erro só dos novos -- os antigos que ficaram intocados não
    # precisam ser reconferidos, já estavam corretos.
    with connection.cursor() as cursor:
        for mae_id in mae_ids_criados:
            cursor.execute("call public.verifica_fluxo(%s)", [mae_id])

    return {"removidos": len(mae_ids_a_remover), "criados": len(mae_ids_criados)}