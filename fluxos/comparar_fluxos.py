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