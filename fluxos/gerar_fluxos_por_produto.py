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

from collections import defaultdict
from itertools import product
from typing import Dict, List, Tuple

from equipamentos.models import TbEquipamentos
from fluxos.models import TbFluxoConsumoPadrao
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


def gerar_linhas_do_plano(plano: Tuple[Tuple[int, str, int], ...]) -> List[dict]:
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
    """
    ids_cp = [cp_id for (_, _, cp_id) in plano]
    consumos = TbFluxoConsumoPadrao.objects.filter(id__in=ids_cp).values(
        "id", "flu_con_pad_from_equipamento_id", "flu_con_pad_to_equipamento_id"
    )
    to_do_cp = {c["id"]: c["flu_con_pad_to_equipamento_id"] for c in consumos}

    entradas_por_destino = defaultdict(list)
    out_degree = defaultdict(int)
    for from_id, criterio, cp_id in plano:
        to_id = to_do_cp[cp_id]
        entradas_por_destino[to_id].append((from_id, cp_id))
        out_degree[from_id] += 1

    todos_from = set(from_id for (from_id, _, _) in plano)
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