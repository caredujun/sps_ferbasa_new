"""
Comparação entre dois fluxos de produção (SÓ LEITURA: não grava nada).

O que é comparado: as LIGAÇÕES entre os nós de cada fluxo, ou seja, os consumos padrão (TbFluxoConsumoPadrao:
equipamento/ordem "from" -> equipamento/ordem "to") que as linhas do fluxo usam. Para cada ligação que existe em um
fluxo e não no outro, mostra o que foi cadastrado no consumo padrão: o indicador (TbFluxoConsumoPadraoDaugther.dau_valor,
período a período: dau_order) -- quanto o "from" deve enviar da sua produção para produzir 1 tonelada (ou unidade) no
"to" --, e o custo variável adicionado da ligação (o mesmo cálculo que o Admin mostra no consumo padrão: custo variável
adicionado do equipamento "from", pela procedure atualiza_custo_var_adic_equipamento_order, vezes o indicador).

Ligações que os dois fluxos compartilham são o MESMO registro de consumo padrão, então têm exatamente os mesmos valores:
só são contadas, não detalhadas.
"""
from __future__ import annotations

import time
from decimal import Decimal

from django.db import connection, transaction

from .models import (
    TbFluxoConsumoPadrao,
    TbFluxoConsumoPadraoDaugther,
    TbFluxoProducao,
    TbFluxoProducaoDaugther,
    TbFluxoProducaoInputOutput,
    TbFluxoProducaoInputOutputDaugther,
)

LIMITE_CANDIDATOS = 10          # os botões do chat são no máximo 12: 10 números + "Cancelar" cabem
ORCAMENTO_CUSTO_SEGUNDOS = 20       # a procedure do custo é chamada uma vez por (equipamento "from", período)
MAX_FAIXAS_POR_LINHA = 12


class ComparacaoError(RuntimeError):
    """Erro de validação da comparação (fluxo inexistente, de outro cenário...)."""


# ---------------------------------------------------------------------------------------------------------------
# Períodos
# ---------------------------------------------------------------------------------------------------------------
def rotulo_periodo(tipo: str, inicio: str, ordem: int) -> str:
    """Mesma regra de TbFluxoConsumoPadraoDaugther.display_order: dau_order 1 = início do cenário."""
    if tipo == 'Anual':
        return str(int(inicio[:4]) - 1 + ordem)
    ano, sub = int(inicio[:4]), int(inicio[-2:])
    if tipo == 'Mensal':
        total = ano * 12 + (sub - 1) + (ordem - 1)
        return f'{total // 12}/{total % 12 + 1:02d}'
    if tipo == 'Trimestral':
        total = ano * 4 + (sub - 1) + (ordem - 1)
        return f'{total // 4}/0{total % 4 + 1}'
    return str(ordem)


# ---------------------------------------------------------------------------------------------------------------
# Busca de fluxos (um produto tem milhares: o usuário digita parte da descrição, ou o id)
# ---------------------------------------------------------------------------------------------------------------
def buscar_fluxos(cenario_id, produto_id, termo, *, excluir_id=None, limite=LIMITE_CANDIDATOS):
    """Devolve ([{'id', 'descricao'}...], total_encontrado). Só do cenário e do produto. Um número puro é tentado
    primeiro como id; se não houver fluxo com esse id, cai na busca pela descrição. Várias palavras = todas precisam
    aparecer (sem diferenciar maiúsculas)."""
    base = TbFluxoProducao.objects.filter(tbcenarios_id=cenario_id, flu_pro_produto_id=produto_id)
    if excluir_id is not None:
        base = base.exclude(id=excluir_id)
    termo = (termo or '').strip()
    qs = base.none()
    if termo.isdigit():
        qs = base.filter(id=int(termo))
    if not termo.isdigit() or not qs.exists():
        qs = base
        for palavra in termo.split():
            qs = qs.filter(flu_pro_descricao__icontains=palavra)
    total = qs.count()
    itens = list(qs.order_by('flu_pro_descricao', 'id').values('id', 'flu_pro_descricao')[:limite])
    return [{'id': i['id'], 'descricao': i['flu_pro_descricao']} for i in itens], total


# ---------------------------------------------------------------------------------------------------------------
# Custo variável adicionado do equipamento "from" num período (igual ao Admin: TbFluxoConsumoPadraoDaugther.custo_variavel)
# ---------------------------------------------------------------------------------------------------------------
def custo_adicionado_equipamento(cenario_id, equipamento_id, ordem_periodo):
    with transaction.atomic():          # savepoint: se a procedure falhar, não derruba a transação do chat
        with connection.cursor() as cursor:
            cursor.execute(
                "call public.atualiza_custo_var_adic_equipamento_order(%s, %s, %s, 0)",
                [cenario_id, equipamento_id, ordem_periodo],
            )
            return cursor.fetchone()[0]


def _codigo_produto(fluxo):
    return getattr(fluxo.flu_pro_produto, 'pro_codigo', '') if fluxo.flu_pro_produto_id else ''


def _rotulo_equipamento(ordem):
    return f"{ordem.equ_codigo.equ_cad_codigo}/{ordem.equ_ordem_codigo}"


# ---------------------------------------------------------------------------------------------------------------
# Comparação
# ---------------------------------------------------------------------------------------------------------------
def comparar_fluxos(fluxo_a_id, fluxo_b_id, cenario, *, custo_fn=None, orcamento_segundos=ORCAMENTO_CUSTO_SEGUNDOS,
                    relogio=time.monotonic):
    """
    cenario: objeto com cen_tipo, cen_inicio (e id) -- só pra nomear os períodos.
    custo_fn(cenario_id, equipamento_id, ordem_periodo) -> custo variável adicionado do equipamento (injetável nos testes).
    """
    custo_fn = custo_fn or custo_adicionado_equipamento
    fluxos = {f.id: f for f in TbFluxoProducao.objects.filter(id__in=[fluxo_a_id, fluxo_b_id]).select_related('flu_pro_produto')}
    if fluxo_a_id not in fluxos or fluxo_b_id not in fluxos:
        raise ComparacaoError("Um dos fluxos não existe mais.")
    fa, fb = fluxos[fluxo_a_id], fluxos[fluxo_b_id]
    if fa.tbcenarios_id != fb.tbcenarios_id:
        raise ComparacaoError("Os dois fluxos precisam ser do mesmo cenário.")

    def consumos_do(fluxo_id):
        return set(TbFluxoProducaoDaugther.objects.filter(mae_id=fluxo_id).order_by()
                   .values_list('flu_pro_dau_consumo_padrao_id', flat=True))

    a, b = consumos_do(fluxo_a_id), consumos_do(fluxo_b_id)
    so_a, so_b = a - b, b - a
    info = {cp.id: cp for cp in TbFluxoConsumoPadrao.objects.filter(id__in=a | b).select_related(
        'flu_con_pad_from_equipamento__equ_codigo', 'flu_con_pad_to_equipamento__equ_codigo')}

    def nos(cps):
        s = {}
        for cp_id in cps:
            for o in (info[cp_id].flu_con_pad_from_equipamento, info[cp_id].flu_con_pad_to_equipamento):
                s[o.id] = _rotulo_equipamento(o)
        return s
    nos_a, nos_b = nos(a), nos(b)
    iguais = sorted(f"{_rotulo_equipamento(info[c].flu_con_pad_from_equipamento)} → "
                    f"{_rotulo_equipamento(info[c].flu_con_pad_to_equipamento)}" for c in a & b)

    # Valores (indicador) por período das ligações diferentes
    valores = {}
    for mae_id, ordem, valor in (TbFluxoConsumoPadraoDaugther.objects.filter(mae_id__in=so_a | so_b)
                                 .order_by('mae_id', 'dau_order').values_list('mae_id', 'dau_order', 'dau_valor')):
        valores.setdefault(mae_id, {})[ordem] = valor
    ordens_periodo = sorted({o for v in valores.values() for o in v})
    periodos = [(o, rotulo_periodo(cenario.cen_tipo, cenario.cen_inicio, o)) for o in ordens_periodo]

    # Custo variável adicionado (cache por equipamento "from" x período; para no primeiro erro ou no fim do orçamento)
    cache, estado_custo = {}, {'indisponivel': False, 'incompleto': False}
    inicio = relogio()

    def custo_do_from(from_id, ordem):
        if estado_custo['indisponivel']:
            return None
        if (from_id, ordem) in cache:
            return cache[(from_id, ordem)]
        if relogio() - inicio > orcamento_segundos:
            estado_custo['incompleto'] = True
            return None
        try:
            bruto = custo_fn(cenario.id, from_id, ordem)
            cache[(from_id, ordem)] = None if bruto is None else Decimal(str(bruto))
        except Exception:
            estado_custo['indisponivel'] = True
            return None
        return cache[(from_id, ordem)]

    def aresta(cp_id):
        cp = info[cp_id]
        de, para = cp.flu_con_pad_from_equipamento, cp.flu_con_pad_to_equipamento
        vals = valores.get(cp_id, {})
        custos = {}
        for ordem, valor in vals.items():
            c = custo_do_from(de.id, ordem)
            custos[ordem] = None if c is None else c * Decimal(str(valor))
        return {'cp_id': cp_id, 'from': _rotulo_equipamento(de), 'to': _rotulo_equipamento(para), 'to_id': para.id,
                'valores': vals, 'custos': custos}

    arestas_a = [aresta(c) for c in sorted(so_a)]
    arestas_b = [aresta(c) for c in sorted(so_b)]
    grupos = {}
    for lado, lista in (('a', arestas_a), ('b', arestas_b)):
        for ar in lista:
            g = grupos.setdefault(ar['to_id'], {'to': ar['to'], 'a': [], 'b': []})
            g[lado].append(ar)

    return {
        'cenario_id': cenario.id,
        # Cada fluxo tem o SEU produto: a comparação pode ser entre fluxos de produtos diferentes.
        'a': {'id': fa.id, 'descricao': fa.flu_pro_descricao, 'produto': _codigo_produto(fa)},
        'b': {'id': fb.id, 'descricao': fb.flu_pro_descricao, 'produto': _codigo_produto(fb)},
        'qtd_iguais': len(a & b), 'qtd_so_a': len(so_a), 'qtd_so_b': len(so_b), 'iguais': iguais,
        'ordens_so_a': sorted(r for i, r in nos_a.items() if i not in nos_b),
        'ordens_so_b': sorted(r for i, r in nos_b.items() if i not in nos_a),
        'periodos': periodos,
        'grupos': sorted(grupos.values(), key=lambda g: g['to']),
        'custo_indisponivel': estado_custo['indisponivel'], 'custo_incompleto': estado_custo['incompleto'],
    }


# ---------------------------------------------------------------------------------------------------------------
# Texto do resultado (pro chat)
# ---------------------------------------------------------------------------------------------------------------
def _num(valor, casas):
    texto = f"{Decimal(str(valor)):,.{casas}f}"
    return texto.replace(',', 'X').replace('.', ',').replace('X', '.')


def _faixas(periodos, por_ordem, formatar):
    """Junta períodos seguidos com o mesmo valor: 'em todos os períodos: 1,0500' / '2026–2028: 1,0300; 2029: 1,0200'."""
    if not periodos:
        return 'sem valores lançados'
    pares = [(rotulo, formatar(por_ordem[o]) if por_ordem.get(o) is not None else 'sem valor') for o, rotulo in periodos]
    grupos = []
    for rotulo, valor in pares:
        if grupos and grupos[-1][2] == valor:
            grupos[-1][1] = rotulo
        else:
            grupos.append([rotulo, rotulo, valor])
    if len(grupos) == 1 and len(pares) > 1:
        return f"em todos os períodos: {grupos[0][2]}"
    partes = [f"{g[0]}: {g[2]}" if g[0] == g[1] else f"{g[0]}–{g[1]}: {g[2]}" for g in grupos]
    if len(partes) > MAX_FAIXAS_POR_LINHA:
        partes = partes[:MAX_FAIXAS_POR_LINHA] + [f"... (+{len(partes) - MAX_FAIXAS_POR_LINHA} faixas)"]
    return '; '.join(partes)


def formatar_comparacao(res, moeda=''):
    pa, pb = res['a']['produto'], res['b']['produto']
    titulo = (f"**Comparação dos fluxos do produto {pa}**" if pa == pb
              else f"**Comparação de fluxos de produtos diferentes: {pa} e {pb}**")
    L = [titulo,
         f"- **Fluxo A** (id {res['a']['id']}, produto {pa}): {res['a']['descricao']}",
         f"- **Fluxo B** (id {res['b']['id']}, produto {pb}): {res['b']['descricao']}", '',
         f"Ligações iguais nos dois: **{res['qtd_iguais']}** · só no A: **{res['qtd_so_a']}** · só no B: **{res['qtd_so_b']}**"]
    if not res['grupos']:
        L += ['', "Os dois fluxos usam exatamente as mesmas ligações (os mesmos consumos padrão), então o indicador "
                  "cadastrado é o mesmo em todos os pontos."]
        return '\n'.join(L)
    if res['ordens_so_a']:
        L.append("Equipamento/ordem só no A: " + ', '.join(res['ordens_so_a']))
    if res['ordens_so_b']:
        L.append("Equipamento/ordem só no B: " + ', '.join(res['ordens_so_b']))
    L += ['', "O indicador é quanto o equipamento/ordem de origem envia da sua produção para produzir 1 tonelada "
              "(ou unidade) no destino, período a período. O custo variável adicionado é o do equipamento de origem "
              "vezes o indicador (o mesmo cálculo do Admin)."]
    periodos = res['periodos']
    for g in res['grupos']:
        L += ['', f"**Para produzir em {g['to']}:**"]
        for lado, nome in (('a', 'A'), ('b', 'B')):
            if not g[lado]:
                L.append(f"- Fluxo {nome}: não tem ligação para {g['to']} (só o outro fluxo tem).")
                continue
            for ar in g[lado]:
                L.append(f"- Fluxo {nome} recebe de **{ar['from']}** — indicador: "
                         + _faixas(periodos, ar['valores'], lambda v: _num(v, 4)))
                if res['custo_indisponivel']:
                    L.append("  custo variável adicionado: não foi possível calcular")
                else:
                    L.append("  custo variável adicionado: "
                             + _faixas(periodos, ar['custos'], lambda v: f"{moeda}{_num(v, 2)}"))
    if res['custo_indisponivel']:
        L += ['', "Não consegui calcular o custo variável adicionado (a procedure do banco falhou); o indicador acima "
                  "não depende disso."]
    elif res['custo_incompleto']:
        L += ['', "O custo variável adicionado ficou incompleto: o cálculo passou do tempo limite e os períodos "
                  "restantes aparecem como \"sem valor\"."]
    return '\n'.join(L)


# ---------------------------------------------------------------------------------------------------------------
# Tabela em COLUNAS PARALELAS (pro chat desenhar): período a período, Fluxo A | Fluxo B | Variação (B - A)
# ---------------------------------------------------------------------------------------------------------------
MAX_IGUAIS_LISTADAS = 80


def _sentido(a, b):
    """Como o B está em relação ao A: maior / menor / igual (ou 'na' quando falta um dos lados)."""
    if a is None or b is None:
        return 'na'
    return 'maior' if b > a else 'menor' if b < a else 'igual'


def _delta(a, b, casas, moeda=''):
    """(Δ formatado com sinal, Δ% do A) -- None quando falta um dos lados."""
    if a is None or b is None:
        return None, None
    d = Decimal(str(b)) - Decimal(str(a))
    txt = f"{'+' if d > 0 else '-' if d < 0 else ''}{moeda}{_num(abs(d), casas)}"
    pct = None
    if Decimal(str(a)) != 0:
        p = d / Decimal(str(a)) * 100
        pct = ('+' if p > 0 else '-' if p < 0 else '') + _num(abs(p), 1) + '%'
    return txt, pct


def _faixas_de_linhas(periodos, celulas_por_ordem):
    """Junta períodos SEGUIDOS cuja linha inteira (todas as colunas) é igual; devolve [(rotulo, linha), ...]."""
    saida = []
    for ordem, rotulo in periodos:
        linha = celulas_por_ordem[ordem]
        if saida and saida[-1][2] == linha:
            saida[-1][1] = rotulo
        else:
            saida.append([rotulo, rotulo, linha])
    return [(r0 if r0 == r1 else f"{r0}–{r1}", linha) for r0, r1, linha in saida]


def dados_para_tabela(res, moeda=''):
    """
    Estrutura pronta pro chat desenhar a comparação em COLUNAS PARALELAS. Cada ligação que difere vira um bloco
    (por destino) com uma tabela: uma linha por FAIXA de períodos iguais, e as colunas
        Fluxo A (indicador, custo) | Fluxo B (indicador, custo) | Variação B - A (indicador, %, custo).
    Lado sem ligação = None (o front-end mostra "sem ligação"). Tudo já formatado em texto (pt-BR).
    """
    periodos = res['periodos']
    fmt_ind = lambda v: None if v is None else _num(v, 4)
    fmt_custo = lambda v: None if v is None else f"{moeda}{_num(v, 2)}"

    def valor(aresta, chave, ordem):
        return None if aresta is None else aresta[chave].get(ordem)

    grupos, soma_a, soma_b = [], {o: Decimal(0) for o, _ in periodos}, {o: Decimal(0) for o, _ in periodos}
    soma_ok_a, soma_ok_b = {o: True for o, _ in periodos}, {o: True for o, _ in periodos}
    for g in res['grupos']:
        arestas_a = sorted(g['a'], key=lambda x: x['from'])
        arestas_b = sorted(g['b'], key=lambda x: x['from'])
        pares = []
        for i in range(max(len(arestas_a), len(arestas_b))):
            ea = arestas_a[i] if i < len(arestas_a) else None
            eb = arestas_b[i] if i < len(arestas_b) else None
            celulas = {}
            for ordem, _rotulo in periodos:
                ia, ib = valor(ea, 'valores', ordem), valor(eb, 'valores', ordem)
                ca, cb = valor(ea, 'custos', ordem), valor(eb, 'custos', ordem)
                d_ind, pct = _delta(ia, ib, 4)
                d_custo, _p = _delta(ca, cb, 2, moeda)
                celulas[ordem] = (fmt_ind(ia), fmt_custo(ca), fmt_ind(ib), fmt_custo(cb), d_ind, pct, d_custo,
                                  _sentido(ia, ib), _sentido(ca, cb))
                if ea is not None:
                    if ca is None:
                        soma_ok_a[ordem] = False
                    else:
                        soma_a[ordem] += ca
                if eb is not None:
                    if cb is None:
                        soma_ok_b[ordem] = False
                    else:
                        soma_b[ordem] += cb
            linhas = []
            for rotulo, c in _faixas_de_linhas(periodos, celulas):
                linhas.append({
                    'periodo': rotulo,
                    'a': {'ind': c[0], 'custo': c[1]}, 'b': {'ind': c[2], 'custo': c[3]},
                    'delta': {'ind': c[4], 'pct': c[5], 'custo': c[6]},
                    'sentido_ind': c[7], 'sentido_custo': c[8],
                })
            pares.append({
                'a': None if ea is None else {'de': ea['from'], 'para': ea['to']},
                'b': None if eb is None else {'de': eb['from'], 'para': eb['to']},
                'linhas': linhas,
            })
        grupos.append({'to': g['to'], 'pares': pares})

    totais = None
    if res['grupos'] and not res['custo_indisponivel']:
        cel = {}
        for ordem, _r in periodos:
            ta = soma_a[ordem] if soma_ok_a[ordem] else None
            tb = soma_b[ordem] if soma_ok_b[ordem] else None
            d, _p = _delta(ta, tb, 2, moeda)
            cel[ordem] = (fmt_custo(ta), fmt_custo(tb), d, _sentido(ta, tb))
        totais = [{'periodo': r, 'a': c[0], 'b': c[1], 'delta': c[2], 'sentido': c[3]} for r, c in _faixas_de_linhas(periodos, cel)]

    pa, pb = res['a']['produto'], res['b']['produto']
    return {
        'titulo': (f"Comparação dos fluxos do produto {pa}" if pa == pb else f"Comparação de fluxos de produtos diferentes: {pa} e {pb}"),
        'a': dict(res['a']), 'b': dict(res['b']),
        'resumo': {'iguais': res['qtd_iguais'], 'so_a': res['qtd_so_a'], 'so_b': res['qtd_so_b'],
                   'ordens_so_a': res['ordens_so_a'], 'ordens_so_b': res['ordens_so_b']},
        'iguais': res['iguais'][:MAX_IGUAIS_LISTADAS], 'iguais_total': len(res['iguais']),
        'grupos': grupos, 'totais': totais,
        'custo_indisponivel': res['custo_indisponivel'], 'custo_incompleto': res['custo_incompleto'],
    }


# ---------------------------------------------------------------------------------------------------------------
# COMPARAÇÃO POR EQUIPAMENTO (mapa do fluxo)
#
# Um fluxo é um mapa de equipamentos por COLUNA (flu_pro_dau_coluna / flu_pro_dau_linha: o "from" de cada linha fica na
# posição (coluna, linha) e o último "to" fica na coluna seguinte -- é assim que a procedure atualiza_input_output monta
# o I/O). Dois fluxos do mesmo produto costumam passar pelos MESMOS equipamentos e diferir só na ORDEM de produção usada
# em cada um. Então a comparação é feita equipamento a equipamento (par = o mesmo equipamento, nas posições dos dois
# fluxos), e a diferença que interessa é a da ORDEM: o que muda de uma ordem pra outra.
# ---------------------------------------------------------------------------------------------------------------
def _ficha_do_no(no):
    if no is None:
        return None
    o = no['ordem']
    return {'coluna': no['coluna'], 'linha': no['linha'], 'ordem_id': o.id, 'rotulo': _rotulo_equipamento(o),
            'numero': o.equ_ordem_codigo, 'descricao': o.equ_ordem_descricao, 'tipo': str(o.equ_tipo_producao)}


def comparar_fluxos_por_equipamento(fluxo_a_id, fluxo_b_id, cenario, *, custo_fn=None,
                                    orcamento_segundos=ORCAMENTO_CUSTO_SEGUNDOS, relogio=time.monotonic):
    """
    Pareia os equipamentos dos dois fluxos e, onde a ORDEM (ou a ligação de saída) difere, levanta o que muda:
    custo variável adicionado da ordem, consumo padrão de saída (indicador) e consumos específicos, por período.
    """
    from equipamentos.models import TbEquipamentosConsumoEspecifico, TbEquipamentosConsumoEspecificoDaugther
    custo_fn = custo_fn or custo_adicionado_equipamento
    fluxos = {f.id: f for f in TbFluxoProducao.objects.filter(id__in=[fluxo_a_id, fluxo_b_id]).select_related('flu_pro_produto')}
    if fluxo_a_id not in fluxos or fluxo_b_id not in fluxos:
        raise ComparacaoError("Um dos fluxos não existe mais.")
    fa, fb = fluxos[fluxo_a_id], fluxos[fluxo_b_id]
    if fa.tbcenarios_id != fb.tbcenarios_id:
        raise ComparacaoError("Os dois fluxos precisam ser do mesmo cenário.")

    def linhas_do(fluxo_id):
        return list(TbFluxoProducaoDaugther.objects.filter(mae_id=fluxo_id)
                    .order_by('flu_pro_dau_coluna', 'flu_pro_dau_linha', 'id')
                    .values_list('flu_pro_dau_coluna', 'flu_pro_dau_linha', 'flu_pro_dau_consumo_padrao_id'))
    la, lb = linhas_do(fluxo_a_id), linhas_do(fluxo_b_id)
    cp_ids = {c for _, _, c in la + lb}
    info = {cp.id: cp for cp in TbFluxoConsumoPadrao.objects.filter(id__in=cp_ids).select_related(
        'flu_con_pad_from_equipamento__equ_codigo', 'flu_con_pad_from_equipamento__equ_tipo_producao',
        'flu_con_pad_to_equipamento__equ_codigo', 'flu_con_pad_to_equipamento__equ_tipo_producao')}

    def nos_de(linhas):
        nos = {}
        for coluna, linha, cp_id in linhas:           # o "from" de cada linha ocupa a posição (coluna, linha)
            o = info[cp_id].flu_con_pad_from_equipamento
            nos.setdefault((coluna, linha, o.id), {'coluna': coluna, 'linha': linha, 'ordem': o, 'cp': cp_id})
        if linhas:                                      # só o ÚLTIMO "to" entra: é o fim do fluxo (coluna + 1)
            coluna, linha, cp_id = linhas[-1]
            o = info[cp_id].flu_con_pad_to_equipamento
            nos.setdefault((coluna + 1, linha, o.id), {'coluna': coluna + 1, 'linha': linha, 'ordem': o, 'cp': None})
        return sorted(nos.values(), key=lambda n: (n['coluna'], n['linha']))
    nos_a, nos_b = nos_de(la), nos_de(lb)

    por_equip_a, por_equip_b = {}, {}
    for n in nos_a:
        por_equip_a.setdefault(n['ordem'].equ_codigo_id, []).append(n)
    for n in nos_b:
        por_equip_b.setdefault(n['ordem'].equ_codigo_id, []).append(n)

    pares = []
    for equip_id in set(por_equip_a) | set(por_equip_b):
        lista_a, lista_b = por_equip_a.get(equip_id, []), por_equip_b.get(equip_id, [])
        for i in range(max(len(lista_a), len(lista_b))):
            na = lista_a[i] if i < len(lista_a) else None
            nb = lista_b[i] if i < len(lista_b) else None
            ref = (na or nb)['ordem'].equ_codigo
            if na and nb:
                situacao = ('igual' if na['cp'] == nb['cp'] else 'ligacao_diferente') if na['ordem'].id == nb['ordem'].id else 'ordem_diferente'
            else:
                situacao = 'so_a' if na else 'so_b'
            pares.append({'equipamento': ref.equ_cad_codigo, 'equipamento_descricao': ref.equ_cad_descricao, 'a': na, 'b': nb,
                          'situacao': situacao, 'coluna_diferente': bool(na and nb and na['coluna'] != nb['coluna'])})
    # 🌟 ALTERADO: colunas em ordem DECRESCENTE (do fim do fluxo pro começo). Par com o mesmo equipamento em colunas
    # diferentes nos dois fluxos entra pela MAIOR das duas. Desempate: linha (crescente) e código do equipamento.
    pares.sort(key=lambda r: (-max(n['coluna'] for n in (r['a'], r['b']) if n),
                              min(n['linha'] for n in (r['a'], r['b']) if n), str(r['equipamento'])))
    for i, r in enumerate(pares):
        r['idx'] = i

    # -------- só os pares que diferem têm detalhe
    diferentes = [r for r in pares if r['situacao'] != 'igual']
    nos_dif = [n for r in diferentes for n in (r['a'], r['b']) if n]
    ordens_ids = {n['ordem'].id for n in nos_dif}
    cps_dif = {n['cp'] for n in nos_dif if n['cp']}

    valores_cp = {}
    for mae_id, ordem, valor in (TbFluxoConsumoPadraoDaugther.objects.filter(mae_id__in=cps_dif)
                                 .order_by('mae_id', 'dau_order').values_list('mae_id', 'dau_order', 'dau_valor')):
        valores_cp.setdefault(mae_id, {})[ordem] = valor

    consumos = {}                                       # ordem_id -> {item_id: {'rotulo', 'valores': {periodo: valor}}}
    itens = list(TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id__in=ordens_ids)
                 .select_related('equ_con_esp_custoitempreco').order_by('id'))
    rotulos_item = {}
    for it in itens:
        if it.equ_con_esp_custoitempreco_id not in rotulos_item:
            rotulos_item[it.equ_con_esp_custoitempreco_id] = str(it.equ_con_esp_custoitempreco)
        consumos.setdefault(it.equ_con_esp_equipamento_id, {})[it.equ_con_esp_custoitempreco_id] = {'rotulo': rotulos_item[it.equ_con_esp_custoitempreco_id], 'valores': {}, 'mae': it.id}
    mae_para_ref = {d['mae']: (oid, iid) for oid, itens_o in consumos.items() for iid, d in itens_o.items()}
    for mae_id, ordem, valor in (TbEquipamentosConsumoEspecificoDaugther.objects.filter(mae_id__in=list(mae_para_ref))
                                 .order_by('mae_id', 'dau_order').values_list('mae_id', 'dau_order', 'dau_valor')):
        oid, iid = mae_para_ref[mae_id]
        consumos[oid][iid]['valores'][ordem] = valor

    ordens_periodo = sorted({o for v in valores_cp.values() for o in v} | {o for itens_o in consumos.values() for d in itens_o.values() for o in d['valores']})
    periodos = [(o, rotulo_periodo(cenario.cen_tipo, cenario.cen_inicio, o)) for o in ordens_periodo]

    cache, estado_custo = {}, {'indisponivel': False, 'incompleto': False}
    inicio = relogio()

    def custo_da_ordem(ordem_id, periodo):
        if estado_custo['indisponivel']:
            return None
        if (ordem_id, periodo) in cache:
            return cache[(ordem_id, periodo)]
        if relogio() - inicio > orcamento_segundos:
            estado_custo['incompleto'] = True
            return None
        try:
            bruto = custo_fn(cenario.id, ordem_id, periodo)
            cache[(ordem_id, periodo)] = None if bruto is None else Decimal(str(bruto))
        except Exception:
            estado_custo['indisponivel'] = True
            return None
        return cache[(ordem_id, periodo)]

    def lado(no):
        if no is None:
            return None
        o = no['ordem']
        custo_ordem = {p: custo_da_ordem(o.id, p) for p in ordens_periodo}
        saida = None
        if no['cp']:
            cp = info[no['cp']]
            valores = valores_cp.get(no['cp'], {})
            saida = {'de': _rotulo_equipamento(cp.flu_con_pad_from_equipamento), 'para': _rotulo_equipamento(cp.flu_con_pad_to_equipamento),
                     'valores': valores,
                     'custos': {p: (None if custo_ordem.get(p) is None else custo_ordem[p] * Decimal(str(v))) for p, v in valores.items()}}
        return {'ficha': _ficha_do_no(no), 'custo_ordem': custo_ordem, 'saida': saida}

    detalhes = {}
    for r in diferentes:
        da, db = lado(r['a']), lado(r['b'])
        itens_a, itens_b = consumos.get(r['a']['ordem'].id, {}) if r['a'] else {}, consumos.get(r['b']['ordem'].id, {}) if r['b'] else {}
        chaves = sorted(set(itens_a) | set(itens_b), key=lambda k: (itens_a.get(k) or itens_b.get(k))['rotulo'])
        detalhes[r['idx']] = {
            'a': da, 'b': db,
            'consumos': [{'item': (itens_a.get(k) or itens_b.get(k))['rotulo'],
                          'a': None if k not in itens_a else itens_a[k]['valores'], 'b': None if k not in itens_b else itens_b[k]['valores']} for k in chaves],
        }

    contagem = {'igual': 0, 'ligacao_diferente': 0, 'ordem_diferente': 0, 'so_a': 0, 'so_b': 0}
    for r in pares:
        contagem[r['situacao']] += 1
    return {
        'cenario_id': cenario.id,
        'a': {'id': fa.id, 'descricao': fa.flu_pro_descricao, 'produto': _codigo_produto(fa)},
        'b': {'id': fb.id, 'descricao': fb.flu_pro_descricao, 'produto': _codigo_produto(fb)},
        'pares': pares, 'detalhes': detalhes, 'periodos': periodos, 'contagem': contagem,
        'custo_indisponivel': estado_custo['indisponivel'], 'custo_incompleto': estado_custo['incompleto'],
    }


def dados_para_tabela_equipamentos(res, moeda=''):
    """
    Estrutura pronta pro chat desenhar o MAPA por equipamento. 'versao': 2 (a tela distingue do formato antigo, por ligação).

        mapa:      uma linha por equipamento (par): Fluxo A (coluna, ordem) | Fluxo B (coluna, ordem) | situação
        detalhes:  só dos pares que diferem: ficha das duas ordens, custo variável adicionado da ordem, consumo padrão de saída
                   (indicador e custo da ligação) e consumos específicos -- tudo período a período, em FAIXAS de períodos iguais.
    """
    periodos = res['periodos']
    fmt_ind = lambda v: None if v is None else _num(v, 4)
    fmt_custo = lambda v: None if v is None else f"{moeda}{_num(v, 2)}"

    mapa, detalhes = [], []
    soma_a, soma_b = {o: Decimal(0) for o, _ in periodos}, {o: Decimal(0) for o, _ in periodos}
    ok_a, ok_b = {o: True for o, _ in periodos}, {o: True for o, _ in periodos}
    teve_ligacao = False
    for r in res['pares']:
        det = res['detalhes'].get(r['idx'])
        mapa.append({'idx': r['idx'], 'equipamento': r['equipamento'], 'descricao': r['equipamento_descricao'],
                     'a': _ficha_do_no(r['a']), 'b': _ficha_do_no(r['b']), 'situacao': r['situacao'],
                     'coluna_diferente': r['coluna_diferente'], 'detalhe': det is not None})
        if det is None:
            continue
        da, db = det['a'], det['b']

        # ---- custo variável adicionado da ORDEM (a procedure), A x B
        custo_ordem = []
        if not res['custo_indisponivel'] and periodos:
            cel = {}
            for o, _r in periodos:
                ca = da['custo_ordem'].get(o) if da else None
                cb = db['custo_ordem'].get(o) if db else None
                d, pct = _delta(ca, cb, 2, moeda)
                cel[o] = (fmt_custo(ca), fmt_custo(cb), d, pct, _sentido(ca, cb))
            custo_ordem = [{'periodo': rot, 'a': c[0], 'b': c[1], 'delta': c[2], 'pct': c[3], 'sentido': c[4]}
                           for rot, c in _faixas_de_linhas(periodos, cel)]

        # ---- consumo padrão de SAÍDA (indicador e custo da ligação), A x B -- mesmo formato do bloco por ligação
        sa = da['saida'] if da else None
        sb = db['saida'] if db else None
        saida = None
        if sa or sb:
            teve_ligacao = True
            cel = {}
            for o, _r in periodos:
                ia, ib = (sa['valores'].get(o) if sa else None), (sb['valores'].get(o) if sb else None)
                ca, cb = (sa['custos'].get(o) if sa else None), (sb['custos'].get(o) if sb else None)
                d_ind, pct = _delta(ia, ib, 4)
                d_custo, _p = _delta(ca, cb, 2, moeda)
                cel[o] = (fmt_ind(ia), fmt_custo(ca), fmt_ind(ib), fmt_custo(cb), d_ind, pct, d_custo, _sentido(ia, ib), _sentido(ca, cb))
                if sa:
                    if ca is None:
                        ok_a[o] = False
                    else:
                        soma_a[o] += ca
                if sb:
                    if cb is None:
                        ok_b[o] = False
                    else:
                        soma_b[o] += cb
            saida = {'a': None if not sa else {'de': sa['de'], 'para': sa['para']},
                     'b': None if not sb else {'de': sb['de'], 'para': sb['para']},
                     'linhas': [{'periodo': rot, 'a': {'ind': c[0], 'custo': c[1]}, 'b': {'ind': c[2], 'custo': c[3]},
                                 'delta': {'ind': c[4], 'pct': c[5], 'custo': c[6]}, 'sentido_ind': c[7], 'sentido_custo': c[8]}
                                for rot, c in _faixas_de_linhas(periodos, cel)]}

        # ---- consumos específicos das duas ordens, item a item (casados pelo "Item de Custo / Planta")
        itens = []
        for item in det['consumos']:
            va, vb = item['a'], item['b']
            cel = {}
            for o, _r in periodos:
                xa = None if va is None else va.get(o)
                xb = None if vb is None else vb.get(o)
                d, pct = _delta(xa, xb, 4)
                cel[o] = (fmt_ind(xa), fmt_ind(xb), d, pct, _sentido(xa, xb))
            itens.append({'item': item['item'], 'a_ausente': va is None, 'b_ausente': vb is None,
                          'linhas': [{'periodo': rot, 'a': c[0], 'b': c[1], 'delta': c[2], 'pct': c[3], 'sentido': c[4]}
                                     for rot, c in _faixas_de_linhas(periodos, cel)]})

        detalhes.append({'idx': r['idx'], 'equipamento': r['equipamento'], 'a': None if da is None else da['ficha'],
                         'b': None if db is None else db['ficha'], 'situacao': r['situacao'],
                         'custo_ordem': custo_ordem, 'saida': saida, 'consumos': itens})

    totais = None
    if teve_ligacao and not res['custo_indisponivel'] and periodos:
        cel = {}
        for o, _r in periodos:
            ta = soma_a[o] if ok_a[o] else None
            tb = soma_b[o] if ok_b[o] else None
            d, _p = _delta(ta, tb, 2, moeda)
            cel[o] = (fmt_custo(ta), fmt_custo(tb), d, _sentido(ta, tb))
        totais = [{'periodo': rot, 'a': c[0], 'b': c[1], 'delta': c[2], 'sentido': c[3]} for rot, c in _faixas_de_linhas(periodos, cel)]

    pa, pb = res['a']['produto'], res['b']['produto']
    n = res['contagem']
    return {
        'versao': 2,
        'titulo': (f"Comparação dos fluxos do produto {pa}" if pa == pb else f"Comparação de fluxos de produtos diferentes: {pa} e {pb}"),
        'a': dict(res['a']), 'b': dict(res['b']),
        'resumo': {'equipamentos': len(res['pares']), 'iguais': n['igual'], 'ligacao_diferente': n['ligacao_diferente'],
                   'ordem_diferente': n['ordem_diferente'], 'so_a': n['so_a'], 'so_b': n['so_b']},
        'mapa': mapa, 'detalhes': detalhes, 'totais': totais,
        'custo_indisponivel': res['custo_indisponivel'], 'custo_incompleto': res['custo_incompleto'],
    }


# ---------------------------------------------------------------------------------------------------------------
# LISTA DE FLUXOS do produto (a tela do chat mostra com rolagem: id e descrição, em ordem alfabética)
# ---------------------------------------------------------------------------------------------------------------
LIMITE_LISTA_FLUXOS = 300       # um produto pode ter centenas de milhares de fluxos: a lista mostra os primeiros e há um filtro


def listar_fluxos(cenario_id, produto_id, filtro='', *, excluir_id=None, limite=LIMITE_LISTA_FLUXOS):
    """([{'id', 'descricao'}...] em ordem alfabética da descrição, total que atende ao filtro). O filtro são palavras (todas
    precisam aparecer na descrição, sem diferenciar maiúsculas)."""
    from django.db.models.functions import Lower
    qs = TbFluxoProducao.objects.filter(tbcenarios_id=cenario_id, flu_pro_produto_id=produto_id)
    if excluir_id is not None:
        qs = qs.exclude(id=excluir_id)
    for palavra in (filtro or '').split():
        qs = qs.filter(flu_pro_descricao__icontains=palavra)
    total = qs.count()
    itens = qs.order_by(Lower('flu_pro_descricao'), 'id').values('id', 'flu_pro_descricao')[:limite]
    return [{'id': i['id'], 'descricao': i['flu_pro_descricao']} for i in itens], total


# ---------------------------------------------------------------------------------------------------------------
# COMPARAÇÃO POR CUSTO (a que explica a diferença no custo do fluxo)
#
# O custo do fluxo sai do I/O (TbFluxoProducaoInputOutput + ...Daugther): para cada equipamento/ordem do fluxo, em cada
# período, dau_valor_4 (output real) x o custo variável adicionado da ordem (procedure atualiza_custo_var_adic_equipamento_
# order), somado. Então a comparação parte do FIM do fluxo (coluna maior) e vai até o começo: cada equipamento/ordem do
# Fluxo A é procurado no Fluxo B (mesma ordem, mesma coluna); os dois comparados saem da lista; o que não achou é uma
# diferença; por fim o que sobrou no Fluxo B (que o A não tem) também é diferença. O valor mostrado é a contribuição média
# dos períodos; o detalhe traz, período a período, indicador, output real, custo da ordem e consumos específicos.
# 🌟 ALTERADO: no fim, TODAS as linhas (inclusive as que só o Fluxo B tem) são reordenadas por coluna DECRESCENTE.
# ---------------------------------------------------------------------------------------------------------------
ORCAMENTO_CUSTO_FLUXO_SEGUNDOS = 45     # a procedure de custo roda por (ordem, período) distintos dos dois fluxos


def _chave_alfabetica(texto):
    """Ordem alfabética de português: sem diferenciar maiúsculas e IGNORANDO acentos ("Água" vem antes de "Energia")."""
    import unicodedata
    return unicodedata.normalize('NFD', str(texto).lower()).encode('ascii', 'ignore').decode('ascii'), str(texto)


def _consumos_especificos(ordens_ids):
    """{ordem_id: {item_id: {'rotulo', 'valores': {periodo: valor}}}} -- itens casados pelo "Item de Custo / Planta"."""
    from equipamentos.models import TbEquipamentosConsumoEspecifico, TbEquipamentosConsumoEspecificoDaugther
    consumos, rotulos = {}, {}
    itens = (TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id__in=ordens_ids)
             .select_related('equ_con_esp_custoitempreco').order_by('id'))
    mae_para_ref = {}
    for it in itens:
        if it.equ_con_esp_custoitempreco_id not in rotulos:
            rotulos[it.equ_con_esp_custoitempreco_id] = str(it.equ_con_esp_custoitempreco)
        consumos.setdefault(it.equ_con_esp_equipamento_id, {})[it.equ_con_esp_custoitempreco_id] = {
            'rotulo': rotulos[it.equ_con_esp_custoitempreco_id], 'valores': {}}
        mae_para_ref[it.id] = (it.equ_con_esp_equipamento_id, it.equ_con_esp_custoitempreco_id)
    for mae_id, ordem, valor in (TbEquipamentosConsumoEspecificoDaugther.objects.filter(mae_id__in=list(mae_para_ref))
                                 .order_by('mae_id', 'dau_order').values_list('mae_id', 'dau_order', 'dau_valor')):
        oid, iid = mae_para_ref[mae_id]
        consumos[oid][iid]['valores'][ordem] = valor
    return consumos


def _nos_do_io(fluxo_id):
    """Os equipamentos/ordens do I/O do fluxo, do FIM (coluna maior) para o começo, com output padrão/real por período."""
    maes = list(TbFluxoProducaoInputOutput.objects.filter(mae_id=fluxo_id, flag=True)
                .select_related('flu_pro_inp_out_equipamento__equ_codigo', 'flu_pro_inp_out_equipamento__equ_tipo_producao')
                .order_by('-flu_pro_inp_out_coluna', 'flu_pro_inp_out_linha', 'id'))
    filhas = {}
    for mae_id, ordem, v3, v4 in (TbFluxoProducaoInputOutputDaugther.objects.filter(mae_id__in=[m.id for m in maes])
                                  .order_by('mae_id', 'dau_order').values_list('mae_id', 'dau_order', 'dau_valor_3', 'dau_valor_4')):
        filhas.setdefault(mae_id, {})[ordem] = (v3, v4)
    return [{'coluna': m.flu_pro_inp_out_coluna, 'linha': m.flu_pro_inp_out_linha, 'ordem': m.flu_pro_inp_out_equipamento,
             'v': filhas.get(m.id, {})} for m in maes]


def comparar_custo_fluxos(fluxo_a_id, fluxo_b_id, cenario, *, custo_fn=None, orcamento_segundos=ORCAMENTO_CUSTO_FLUXO_SEGUNDOS,
                          relogio=time.monotonic):
    """
    A comparação é por EQUIPAMENTO em cada COLUNA do fluxo (a ordem é justamente o que pode mudar): uma linha por (equipamento, coluna),
    do fim do fluxo (coluna maior) para o começo. Em cada fluxo a linha traz as ORDENS desse equipamento naquela coluna (uma, ou várias:
    "1/2/3") e o custo delas (média dos períodos de dau_valor_4 x custo variável adicionado da ordem, somado). Equipamento só num dos
    fluxos = só um lado preenchido. O mesmo equipamento em colunas DIFERENTES (um fluxo com etapa a mais) é junto numa linha só.
    """
    from itertools import zip_longest  # noqa: F401  (usado na montagem do detalhe)
    custo_fn = custo_fn or custo_adicionado_equipamento
    fluxos = {f.id: f for f in TbFluxoProducao.objects.filter(id__in=[fluxo_a_id, fluxo_b_id]).select_related('flu_pro_produto')}
    if fluxo_a_id not in fluxos or fluxo_b_id not in fluxos:
        raise ComparacaoError("Um dos fluxos não existe mais.")
    fa, fb = fluxos[fluxo_a_id], fluxos[fluxo_b_id]
    if fa.tbcenarios_id != fb.tbcenarios_id:
        raise ComparacaoError("Os dois fluxos precisam ser do mesmo cenário.")
    for f in (fa, fb):
        if not f.flu_pro_input_output_atualizado:
            raise ComparacaoError(f"O I/O do fluxo id {f.id} está desatualizado, e o custo do fluxo sai do I/O. Atualize os fluxos "
                                  "(menu Cenário > Atualizar Fluxos) e compare de novo.")
    nos_a, nos_b = _nos_do_io(fa.id), _nos_do_io(fb.id)
    for f, nos in ((fa, nos_a), (fb, nos_b)):
        if not nos:
            raise ComparacaoError(f"O fluxo id {f.id} não tem I/O (nenhum equipamento calculado). Atualize os fluxos e tente de novo.")

    # ---- agrupa por (equipamento, coluna): as ordens do mesmo equipamento na mesma coluna ficam juntas
    def agrupar(nos):
        grupos = {}
        for n in nos:                                           # já vem da coluna maior pra menor
            grupos.setdefault((n['ordem'].equ_codigo_id, n['coluna']), []).append(n)
        return grupos
    grupos_a, restantes_b = agrupar(nos_a), agrupar(nos_b)
    pares = []
    for chave, lista_a in grupos_a.items():                     # do FIM do fluxo pro começo: mesmo equipamento na MESMA coluna
        pares.append({'a': lista_a, 'b': restantes_b.pop(chave, None)})
    for par in pares:                                           # sobrou: o mesmo equipamento em OUTRA coluna do B (etapa a mais/a menos)
        if par['b'] is None:
            equip, coluna = par['a'][0]['ordem'].equ_codigo_id, par['a'][0]['coluna']
            candidatas = [k for k in restantes_b if k[0] == equip]
            if candidatas:
                par['b'] = restantes_b.pop(min(candidatas, key=lambda k: (abs(k[1] - coluna), -k[1])))
    for lista_b in restantes_b.values():                        # o que só o Fluxo B tem
        pares.append({'a': None, 'b': lista_b})

    ordens_periodo = sorted({o for nos in (nos_a, nos_b) for n in nos for o in n['v']})
    consumos = _consumos_especificos({n['ordem'].id for nos in (nos_a, nos_b) for n in nos})
    ordens_periodo = sorted(set(ordens_periodo) | {o for itens in consumos.values() for d in itens.values() for o in d['valores']})
    periodos = [(o, rotulo_periodo(cenario.cen_tipo, cenario.cen_inicio, o)) for o in ordens_periodo]

    cache, estado_custo = {}, {'indisponivel': False, 'incompleto': False}
    inicio = relogio()

    def custo_da_ordem(ordem_id, periodo):
        if estado_custo['indisponivel']:
            return None
        if (ordem_id, periodo) in cache:
            return cache[(ordem_id, periodo)]
        if relogio() - inicio > orcamento_segundos:
            estado_custo['incompleto'] = True
            return None
        try:
            bruto = custo_fn(cenario.id, ordem_id, periodo)
            cache[(ordem_id, periodo)] = None if bruto is None else Decimal(str(bruto))
        except Exception:
            estado_custo['indisponivel'] = True
            return None
        return cache[(ordem_id, periodo)]

    def calcular(no):
        custos = {p: custo_da_ordem(no['ordem'].id, p) for p in no['v']}
        contrib = {p: (None if custos[p] is None else custos[p] * Decimal(str(no['v'][p][1]))) for p in no['v']}
        validos = [c for c in contrib.values() if c is not None]
        return {**no, 'custos': custos, 'contrib': contrib, 'media': (sum(validos) / len(validos)) if validos else None}

    def media_do_grupo(lista):
        if lista is None:
            return None
        medias = [n['media'] for n in lista]
        return None if any(m is None for m in medias) else sum(medias, Decimal(0))

    for par in pares:
        par['a'] = None if par['a'] is None else [calcular(n) for n in par['a']]
        par['b'] = None if par['b'] is None else [calcular(n) for n in par['b']]
        par['media_a'], par['media_b'] = media_do_grupo(par['a']), media_do_grupo(par['b'])
        par['coluna_a'] = par['a'][0]['coluna'] if par['a'] else None
        par['coluna_b'] = par['b'][0]['coluna'] if par['b'] else None
        par['ordens_a'] = sorted({n['ordem'].equ_ordem_codigo for n in par['a']}) if par['a'] else None
        par['ordens_b'] = sorted({n['ordem'].equ_ordem_codigo for n in par['b']}) if par['b'] else None
        ref = (par['a'] or par['b'])[0]['ordem']
        par['equipamento'], par['equipamento_descricao'] = ref.equ_codigo.equ_cad_codigo, ref.equ_codigo.equ_cad_descricao

    # 🌟 ALTERADO: colunas em ordem DECRESCENTE. Antes as linhas que só o Fluxo B tem iam pro FIM da tabela, fora da ordem
    # das colunas. Agora todas são reordenadas juntas: coluna (maior primeiro; quando A e B estão em colunas diferentes,
    # vale a maior das duas), depois linha (crescente) e código do equipamento. O idx é refeito DEPOIS da ordenação,
    # pra o botão "Detalhar" continuar apontando pro detalhe certo.
    def _coluna_do_par(par):
        return max(c for c in (par['coluna_a'], par['coluna_b']) if c is not None)

    def _linha_do_par(par):
        return min(n['linha'] for n in (par['a'] or []) + (par['b'] or []))

    pares.sort(key=lambda p: (-_coluna_do_par(p), _linha_do_par(p), str(p['equipamento'])))
    for i, par in enumerate(pares):
        par['idx'] = i

    return {
        'cenario_id': cenario.id,
        'a': {'id': fa.id, 'descricao': fa.flu_pro_descricao, 'produto': _codigo_produto(fa)},
        'b': {'id': fb.id, 'descricao': fb.flu_pro_descricao, 'produto': _codigo_produto(fb)},
        'pares': pares, 'periodos': periodos, 'consumos': consumos,
        'custo_indisponivel': estado_custo['indisponivel'], 'custo_incompleto': estado_custo['incompleto'],
    }


def dados_para_tabela_custo(res, moeda=''):
    """Carga ("versao": 4) pra tela: Equipamento | Coluna | Fluxo A (Ordem, Valor) | Fluxo B (Ordem, Valor) | Delta | Detalhar."""
    from itertools import zip_longest
    periodos = res['periodos']
    ZERO = Decimal('0.005')

    def sinal(d):
        return f"{'+' if d >= ZERO else '-' if d <= -ZERO else ''}{_num(abs(d), 2)}"

    def sentido_delta(d):
        return 'na' if d is None else ('maior' if d >= ZERO else 'menor' if d <= -ZERO else 'igual')

    def texto_ordens(ordens):
        return None if ordens is None else '/'.join(str(o) for o in ordens)

    def faixas_do_par(na, nb):
        """período a período: indicador (v3), output real (v4), custo da ordem e contribuição, dos dois lados."""
        cel = {}
        for o, _r in periodos:
            lado = []
            for n in (na, nb):
                if n is None or o not in n['v']:
                    lado += [None, None, None, None]
                    continue
                v3, v4 = n['v'][o]
                c, k = n['custos'].get(o), n['contrib'].get(o)
                lado += [_num(v3, 4), _num(v4, 4), None if c is None else _num(c, 2), None if k is None else _num(k, 2)]
            ka = na['contrib'].get(o) if na and o in na['v'] else None
            kb = nb['contrib'].get(o) if nb and o in nb['v'] else None
            d = None if (ka is None and kb is None) else (kb or Decimal(0)) - (ka or Decimal(0))
            if (na and ka is None) or (nb and kb is None):
                d = None
            cel[o] = tuple(lado) + (None if d is None else sinal(d), sentido_delta(d))
        return [{'periodo': r, 'a': {'ind': c[0], 'out_real': c[1], 'custo_ordem': c[2], 'custo': c[3]},
                 'b': {'ind': c[4], 'out_real': c[5], 'custo_ordem': c[6], 'custo': c[7]}, 'delta': c[8], 'sentido': c[9]}
                for r, c in _faixas_de_linhas(periodos, cel)]

    def consumos_do_par(na, nb):
        """consumos específicos item a item: a ordem do A contra a ordem do B (quando são ordens diferentes, é a diferença entre elas)."""
        ia = res['consumos'].get(na['ordem'].id, {}) if na else None
        ib = res['consumos'].get(nb['ordem'].id, {}) if nb else None
        itens = []
        for k in sorted(set(ia or {}) | set(ib or {}), key=lambda k: _chave_alfabetica(((ia or {}).get(k) or (ib or {}).get(k))['rotulo'])):
            va = None if ia is None or k not in ia else ia[k]['valores']
            vb = None if ib is None or k not in ib else ib[k]['valores']
            cc = {}
            for o, _r in periodos:
                xa = None if va is None else va.get(o)
                xb = None if vb is None else vb.get(o)
                dd, pct = _delta(xa, xb, 4)
                cc[o] = (None if xa is None else _num(xa, 4), None if xb is None else _num(xb, 4), dd, pct, _sentido(xa, xb))
            itens.append({'item': ((ia or {}).get(k) or (ib or {}).get(k))['rotulo'], 'a_ausente': va is None, 'b_ausente': vb is None,
                          'linhas': [{'periodo': r, 'a': c[0], 'b': c[1], 'delta': c[2], 'pct': c[3], 'sentido': c[4]}
                                     for r, c in _faixas_de_linhas(periodos, cc)]})
        return itens

    linhas, detalhes = [], []
    tot_a = tot_b = Decimal(0)
    tot_ok = True
    for par in res['pares']:
        ma, mb = par['media_a'], par['media_b']
        falta_custo = (par['a'] is not None and ma is None) or (par['b'] is not None and mb is None)
        if falta_custo:
            tot_ok = False
        tot_a += ma or Decimal(0)
        tot_b += mb or Decimal(0)
        delta = None if falta_custo else (mb or Decimal(0)) - (ma or Decimal(0))
        ordens_diferem = bool(par['a'] and par['b'] and par['ordens_a'] != par['ordens_b'])
        if par['a'] and par['b']:
            if ordens_diferem:
                situacao = 'ordem_diferente'
            elif delta is None:
                situacao = 'sem_custo'
            else:
                situacao = 'valor_diferente' if abs(delta) >= ZERO else 'igual'
        else:
            situacao = 'so_a' if par['a'] else 'so_b'
        linhas.append({
            'idx': par['idx'], 'equipamento': par['equipamento'], 'descricao': par['equipamento_descricao'],
            'coluna_a': par['coluna_a'], 'coluna_b': par['coluna_b'],
            'coluna_diferente': bool(par['a'] and par['b'] and par['coluna_a'] != par['coluna_b']),
            'ordens_a': texto_ordens(par['ordens_a']), 'ordens_b': texto_ordens(par['ordens_b']),
            'a': None if ma is None else _num(ma, 2), 'b': None if mb is None else _num(mb, 2),
            'delta': None if delta is None else sinal(delta), 'sentido': sentido_delta(delta), 'situacao': situacao})

        # ---- detalhe: as ordens do A e do B, duas a duas (a mesma ordem dos dois lados, depois o que sobra de cada lado)
        da = {n['ordem'].id: n for n in par['a'] or []}
        db = {n['ordem'].id: n for n in par['b'] or []}
        ordenadas = lambda d: sorted(d, key=lambda oid: d[oid]['ordem'].equ_ordem_codigo)
        pares_ordem = [(da[o], db[o]) for o in ordenadas(da) if o in db]
        pares_ordem += list(zip_longest([da[o] for o in ordenadas(da) if o not in db], [db[o] for o in ordenadas(db) if o not in da]))
        pares_ordem.sort(key=lambda t: (t[0] or t[1])['ordem'].equ_ordem_codigo)
        detalhes.append({'idx': par['idx'], 'equipamento': par['equipamento'], 'pares': [
            {'rotulo_a': None if na is None else _rotulo_equipamento(na['ordem']), 'rotulo_b': None if nb is None else _rotulo_equipamento(nb['ordem']),
             'coluna_a': None if na is None else na['coluna'], 'coluna_b': None if nb is None else nb['coluna'],
             'mesma_ordem': bool(na and nb and na['ordem'].id == nb['ordem'].id),
             'periodos': faixas_do_par(na, nb), 'consumos': consumos_do_par(na, nb)} for na, nb in pares_ordem]})

    pa, pb = res['a']['produto'], res['b']['produto']
    delta_total = None if not tot_ok else tot_b - tot_a
    return {
        'versao': 4, 'moeda': moeda.strip(),
        'titulo': (f"Comparação de custo dos fluxos do produto {pa}" if pa == pb else f"Comparação de custo de fluxos de produtos diferentes: {pa} e {pb}"),
        'a': dict(res['a']), 'b': dict(res['b']), 'linhas': linhas, 'detalhes': detalhes,
        'total': {'a': _num(tot_a, 2) if tot_ok else None, 'b': _num(tot_b, 2) if tot_ok else None,
                  'delta': None if delta_total is None else sinal(delta_total), 'sentido': sentido_delta(delta_total)},
        'resumo': {'equipamentos': len(linhas), 'iguais': sum(1 for l in linhas if l['situacao'] == 'igual'),
                   'ordem_diferente': sum(1 for l in linhas if l['situacao'] == 'ordem_diferente'),
                   'custo_diferente': sum(1 for l in linhas if l['situacao'] == 'valor_diferente'),
                   'so_a': sum(1 for l in linhas if l['situacao'] == 'so_a'), 'so_b': sum(1 for l in linhas if l['situacao'] == 'so_b')},
        'custo_indisponivel': res['custo_indisponivel'], 'custo_incompleto': res['custo_incompleto'],
    }