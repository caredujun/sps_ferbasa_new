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

import hashlib
import logging
from collections import Counter, defaultdict
from itertools import product
from typing import Dict, List, Tuple

from django.db import connection, transaction

from equipamentos.models import TbEquipamentos
from fluxos.models import TbFluxoConsumoPadrao, TbFluxoProducao, TbFluxoProducaoDaugther
from produtos.models import TbProdutos

_log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------
# 🌟 NOVO (memória): processamento em FLUXO CONTÍNUO.
#
# O problema: a versão anterior montava, na memória, a lista com TODOS os planos de
# TODOS os terminais e, de cada plano, uma assinatura pesada (um frozenset de ~30
# pares), e só depois de ter tudo isso gravava o primeiro fluxo. Um produto com
# centenas de milhares de fluxos (o que acontece ao clonar um equipamento que
# alimenta vários pontos da cadeia, ou ao criar terminais de expedição novos) ocupava
# vários GB e o sistema operacional matava o worker do Celery (SIGKILL).
#
# Agora:
#  1) _contar_planos conta quantos planos cada terminal geraria SEM montar nenhum --
#     se passar do limite, avisa na hora com a quantidade, em vez de estourar a memória;
#  2) _expandir_para_tras_iter entrega os planos UM DE CADA VEZ (generator);
#  3) a comparação com o banco guarda só uma assinatura COMPACTA (16 bytes) dos
#     fluxos que JÁ EXISTEM; os planos novos são conferidos e descartados na hora --
#     só os que precisam ser criados ficam guardados;
#  4) a gravação é feita em LOTES, cada um com o seu commit: o que já foi gravado fica
#     gravado (não se perde tudo se o processo for interrompido) e rodar de novo só
#     cria o que ainda falta, porque a comparação é por estrutura.
# ---------------------------------------------------------------------
def _membros_efetivos_por_criterio(destino_id, entradas_por_destino, equipamentos):
    """[(criterio, [(from_id, criterio, cp_id), ...])] na mesma ordem e com a mesma regra de clones de
    _expandir_para_tras (entre um original e o clone dele, no mesmo grupo, fica só o clone)."""
    grupos = defaultdict(list)
    for from_id, criterio, cp_id in entradas_por_destino.get(destino_id, []):
        grupos[criterio].append((from_id, criterio, cp_id))
    resultado = []
    for criterio in sorted(grupos):
        membros = sorted(grupos[criterio], key=lambda m: (not _e_clone(m[0], equipamentos), m[2]))
        vistos_originais, efetivos = set(), []
        for from_id, criterio_m, cp_id in membros:
            original_id = _original_de(from_id, equipamentos)
            if original_id in vistos_originais:
                continue
            vistos_originais.add(original_id)
            efetivos.append((from_id, criterio_m, cp_id))
        resultado.append((criterio, efetivos))
    return resultado


def _contar_planos(destino_id, entradas_por_destino, equipamentos, pilha=(), memo=None) -> int:
    """Quantos planos _expandir_para_tras geraria a partir de destino_id -- sem gerar nenhum (memoizado)."""
    memo = {} if memo is None else memo
    if destino_id in pilha:
        raise GeracaoFluxoError(f"Ciclo detectado envolvendo o equipamento id={destino_id}.")
    if destino_id in memo:
        return memo[destino_id]
    if not entradas_por_destino.get(destino_id):
        memo[destino_id] = 1
        return 1
    total = 1
    for _criterio, membros in _membros_efetivos_por_criterio(destino_id, entradas_por_destino, equipamentos):
        soma = sum(
            _contar_planos(from_id, entradas_por_destino, equipamentos, pilha + (destino_id,), memo)
            for from_id, _c, _cp in membros
        )
        total *= max(soma, 1)
    memo[destino_id] = total
    return total


def _expandir_para_tras_iter(destino_id, entradas_por_destino, equipamentos, pilha=()):
    """Mesma expansão de _expandir_para_tras (mesmos planos, na mesma ordem), mas entregue UM DE CADA VEZ."""
    if destino_id in pilha:
        raise GeracaoFluxoError(f"Ciclo detectado envolvendo o equipamento id={destino_id}.")
    if not entradas_por_destino.get(destino_id):
        yield tuple()
        return

    def alternativas_do_grupo(membros):
        for from_id, criterio_m, cp_id in membros:
            for caminho_anterior in _expandir_para_tras_iter(
                from_id, entradas_por_destino, equipamentos, pilha + (destino_id,),
            ):
                yield caminho_anterior + ((from_id, criterio_m, cp_id),)

    grupos = _membros_efetivos_por_criterio(destino_id, entradas_por_destino, equipamentos)
    if len(grupos) == 1:
        yield from alternativas_do_grupo(grupos[0][1])      # um grupo só: entrega direto, sem guardar nada
        return
    # Vários grupos: cada grupo é guardado (a soma dos seus membros, bem menor que o produto entre grupos);
    # o produto cartesiano, esse sim enorme, sai um de cada vez.
    listas = [list(alternativas_do_grupo(membros)) for _criterio, membros in grupos]
    for combinacao in product(*listas):
        yield tuple(a for parte in combinacao for a in parte)


def _iterar_planos_do_produto(terminais, entradas_por_destino, equipamentos, max_fluxos):
    """Todos os planos de todos os terminais, um de cada vez. Antes de gerar o primeiro, conta tudo e
    recusa (com a quantidade) se algum terminal passar do limite -- sem ter alocado nada."""
    memo = {}
    for terminal in terminais:
        total = _contar_planos(terminal.id, entradas_por_destino, equipamentos, memo=memo)
        if total > max_fluxos:
            rotulo = f"{terminal.equ_codigo.equ_cad_codigo}/{terminal.equ_ordem_codigo}"
            raise GeracaoFluxoError(
                f"O terminal {rotulo} geraria {total:,} fluxos, acima do limite de {max_fluxos:,} "
                "(max_fluxos). Nada foi gerado."
            )
    for terminal in terminais:
        yield from _expandir_para_tras_iter(terminal.id, entradas_por_destino, equipamentos)


def _assinatura_compacta(pares_coluna_cp) -> bytes:
    """
    Mesma assinatura ESTRUTURAL de _assinatura_estrutural (coluna normalizada pela distância até a última coluna
    + consumo_padrao_id, como CONJUNTO), mas guardada como um resumo de 16 bytes em vez de um frozenset de ~30
    pares (que ocupa ~4 KB). Cabe em memória mesmo com centenas de milhares de fluxos.
    """
    pares = list(pares_coluna_cp)
    if not pares:
        return b''
    maior_coluna = max(c for c, _ in pares)
    normalizados = sorted({(maior_coluna - c, cp_id) for c, cp_id in pares})
    return hashlib.blake2b(repr(normalizados).encode('ascii'), digest_size=16).digest()


def _assinaturas_dos_fluxos_existentes(produto_id: int, cenario_id: int) -> Dict[bytes, List[int]]:
    """{assinatura compacta: [mae_id, ...]} dos fluxos que o produto já tem -- lendo as linhas filhas em
    fluxo contínuo (ordenadas por mae_id, em blocos), sem carregar a tabela inteira na memória."""
    por_assinatura = defaultdict(list)
    vistos = set()

    def fechar(mae_id, pares):
        por_assinatura[_assinatura_compacta(pares)].append(mae_id)
        vistos.add(mae_id)

    linhas = (
        TbFluxoProducaoDaugther.objects
        .filter(mae__flu_pro_produto_id=produto_id, mae__tbcenarios_id=cenario_id)
        .order_by('mae_id')
        .values_list('mae_id', 'flu_pro_dau_coluna', 'flu_pro_dau_consumo_padrao_id')
        .iterator(chunk_size=5000)
    )
    atual, pares = None, []
    for mae_id, coluna, cp_id in linhas:
        if mae_id != atual:
            if atual is not None:
                fechar(atual, pares)
            atual, pares = mae_id, []
        pares.append((coluna, cp_id))
    if atual is not None:
        fechar(atual, pares)

    # Fluxos que existem mas não têm nenhuma linha filha: assinatura vazia.
    for mae_id in (TbFluxoProducao.objects.filter(flu_pro_produto_id=produto_id, tbcenarios_id=cenario_id)
                   .order_by('id').values_list('id', flat=True).iterator(chunk_size=5000)):
        if mae_id not in vistos:
            por_assinatura[b''].append(mae_id)
    return por_assinatura


def _em_lotes(sequencia, tamanho):
    for i in range(0, len(sequencia), tamanho):
        yield sequencia[i:i + tamanho]


def comparar_fluxos_existentes(produto_id: int, *, max_fluxos: int = 200000, guardar_planos: bool = True) -> dict:
    """
    Calcula os fluxos NOVOS (sem gravar nada) e compara com os que já existem em TbFluxoProducao/
    TbFluxoProducaoDaugther pro mesmo produto, pela assinatura ESTRUTURAL (coluna + consumo_padrao_id de cada
    linha) -- não só a quantidade total, nem só o conjunto solto de equipamentos usados.

    Compara por MULTICONJUNTO: se existir mais de um fluxo gravado com a mesma assinatura exata (duplicata de
    verdade), cada ocorrência conta separada.

    🌟 Em fluxo contínuo (ver o comentário no topo deste bloco): os planos são gerados e conferidos um de cada
    vez; só guarda na memória os que precisam ser CRIADOS.

    guardar_planos=False: só CONTA o que precisa ser criado (qtd_a_criar) e não guarda os planos --
    planos_a_criar fica vazio. É o que a "situação dos fluxos" usa: não precisa dos planos, só dos números.

    Retorna um dicionário com:
        produto_id, cenario_id, qtd_existente, qtd_novo, qtd_iguais, qtd_a_criar, qtd_a_remover,
        mudou (bool), mae_ids_a_remover, planos_a_criar, cp_para_to
    """
    cenario_id = _cenario_do_produto(produto_id)
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    entradas_por_destino, saidas_de = _montar_grafo(equipamentos, cenario_id)
    terminais = _encontrar_terminais(equipamentos, saidas_de)

    # cp_id -> to_id UMA VEZ SÓ pro produto inteiro (evita uma consulta ao banco por plano).
    cp_para_to = {
        cp_id: to_id
        for to_id, entradas in entradas_por_destino.items()
        for (_, _, cp_id) in entradas
    }

    existentes = _assinaturas_dos_fluxos_existentes(produto_id, cenario_id)
    qtd_existente = sum(len(ids) for ids in existentes.values())

    planos_a_criar = []
    qtd_novo = 0
    qtd_iguais = 0
    qtd_a_criar = 0
    for plano in _iterar_planos_do_produto(terminais, entradas_por_destino, equipamentos, max_fluxos):
        qtd_novo += 1
        linhas = gerar_linhas_do_plano(plano, cp_para_to=cp_para_to)
        assinatura = _assinatura_compacta(
            (l["flu_pro_dau_coluna"], l["flu_pro_dau_consumo_padrao_id"]) for l in linhas
        )
        ids = existentes.get(assinatura)
        if ids:
            ids.pop()                    # já existe um fluxo igual no banco: aproveita e não mexe nele
            qtd_iguais += 1
        else:
            qtd_a_criar += 1               # falta no banco: será criado
            if guardar_planos:
                planos_a_criar.append(plano)
        if qtd_novo % 25000 == 0:
            _log.info("Produto %s: %s planos avaliados (%s já existem, %s a criar).",
                      produto_id, qtd_novo, qtd_iguais, qtd_a_criar)

    # O que sobrou nas listas é o que existe no banco ALÉM do que o cálculo novo pede (errado/a mais).
    mae_ids_a_remover = [mae_id for ids in existentes.values() for mae_id in ids]

    return {
        "produto_id": produto_id,
        "cenario_id": cenario_id,
        "qtd_existente": qtd_existente,
        "qtd_novo": qtd_novo,
        "qtd_iguais": qtd_iguais,
        "qtd_a_criar": qtd_a_criar,
        "qtd_a_remover": len(mae_ids_a_remover),
        "mudou": bool(mae_ids_a_remover) or qtd_a_criar > 0,
        "mae_ids_a_remover": mae_ids_a_remover,
        "planos_a_criar": planos_a_criar,
        "cp_para_to": cp_para_to,
    }


def situacao_fluxos_produto(produto_id: int, *, max_fluxos: int = 200000) -> dict:
    """
    Resumo da situação dos fluxos de UM produto, sem gravar nada e sem guardar os planos na memória:

        qtd_existente : fluxos cadastrados hoje
        qtd_esperada  : fluxos que o cálculo diz que o produto deveria ter
        qtd_corretos  : cadastrados que batem (por estrutura) com o cálculo
        qtd_a_gerar   : esperados que ainda não existem  (esperada - corretos)
        qtd_a_remover : cadastrados que estão errados/sobrando  (existente - corretos)

    Quando não dá pra calcular (produto sem equipamentos, sem terminal, passou do limite...), devolve
    {'erro': '<motivo>'} em vez de levantar -- o chamador mostra o motivo ao lado do produto.
    """
    try:
        r = comparar_fluxos_existentes(produto_id, max_fluxos=max_fluxos, guardar_planos=False)
    except GeracaoFluxoError as erro:
        return {'erro': str(erro)}
    return {
        'qtd_existente': r['qtd_existente'],
        'qtd_esperada': r['qtd_novo'],
        'qtd_corretos': r['qtd_iguais'],
        'qtd_a_gerar': r['qtd_a_criar'],
        'qtd_a_remover': r['qtd_a_remover'],
    }


def sincronizar_fluxos_produto(
    produto_id: int,
    cenario_id: int,
    mae_ids_a_remover: List[int],
    planos_a_criar: List[Tuple[Tuple[int, str, int], ...]],
    *,
    cp_para_to: Dict[int, int] = None,
    tamanho_lote: int = 100,
) -> dict:
    """
    🌟 Aplica só a DIFERENÇA entre o banco e o cálculo novo (vinda de comparar_fluxos_existentes): apaga SÓ os
    fluxos que sobram/estão errados (mae_ids_a_remover) e cria SÓ os que estão faltando (planos_a_criar) -- nunca
    mexe no que já está certo.

    🌟 Em LOTES, cada um com o seu commit (antes era uma transação única pro produto inteiro): o que já foi
    gravado fica gravado mesmo que o processo seja interrompido, e rodar de novo (comparar + sincronizar) só cria
    o que ainda falta, porque a comparação é por estrutura. Os apagamentos também são em lotes -- apagar milhares
    de fluxos de uma vez carregava tudo na memória.

    Retorna {'removidos': int, 'criados': int}.
    """
    removidos = 0
    for lote in _em_lotes(mae_ids_a_remover, 500):
        with transaction.atomic():
            TbFluxoProducao.objects.filter(id__in=lote).delete()
        removidos += len(lote)

    produto_codigo = TbProdutos.objects.get(id=produto_id).pro_codigo
    equipamentos = _equipamentos_do_produto(produto_id, cenario_id)
    if cp_para_to is None:
        entradas_por_destino, _ = _montar_grafo(equipamentos, cenario_id)
        cp_para_to = {
            cp_id: to_id
            for to_id, entradas in entradas_por_destino.items()
            for (_, _, cp_id) in entradas
        }

    criados = 0
    total = len(planos_a_criar)
    for lote in _em_lotes(planos_a_criar, tamanho_lote):
        with transaction.atomic():
            mae_ids_do_lote = []
            for plano in lote:
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
                mae_ids_do_lote.append(mae.id)

            # Verifica erro só dos novos -- os antigos que ficaram intocados já estavam corretos.
            with connection.cursor() as cursor:
                for mae_id in mae_ids_do_lote:
                    cursor.execute("call public.verifica_fluxo(%s)", [mae_id])
        criados += len(mae_ids_do_lote)
        _log.info("Produto %s: %s de %s fluxos criados.", produto_id, criados, total)

    return {"removidos": removidos, "criados": criados}