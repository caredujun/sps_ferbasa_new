"""
🌟 NOVO (29/10/2026): equipamentos usados na PRODUÇÃO de um produto, período a período (resultado do Otimizar) -- para
a dica da barra de Produção do gráfico da tabela de Produtos (TbOtimizacaoProduto). SÓ LEITURA.

Em cada período:
  - GARGALOS (equ_cad_gargalo = true) que produzem alguma coisa para o produto:
      produção usada no produto = soma, nas variáveis (produto / mercado / fluxo) do produto, de produção da variável x
      output real do equipamento no fluxo (dau_valor_4 do I/O: quanto ele produz para 1 unidade do produto final; o
      mesmo equipamento em mais de uma coluna do fluxo soma as colunas);
      produção total do equipamento = a mesma conta com as variáveis de TODOS os produtos do cenário;
      ocupação = horas usadas / loading time do período (TbOtimizacaoEquipamentosDaugther: dau_valor_6 / dau_valor_3);
      unidade = Output do cadastro do equipamento (equ_cad_output).
  - CONJUNTOS DE EQUIPAMENTOS ativos que têm algum equipamento usado na produção do produto: produção do conjunto (soma
    da produção total dos equipamentos dele -- a mesma conta da restrição do Otimizar), o limite máximo do período e a
    ocupação de cada equipamento do conjunto.
Produção da variável = dau_valor_12 (produção, com a produção antecipada); vazio = dau_valor_2 (vendas do período).
"""
from collections import defaultdict

from django.db import connection

LIMITE_SEM_MAXIMO = 9_999_999       # máximo a partir disso = sem limite


def equipamentos_producao_produto(oti_produto):
    cid = oti_produto.tbcenarios_id
    produto_id = oti_produto.oti_pro_produto_id
    filhas = list(oti_produto.tbotimizacaoprodutodaugther_set.filter(flag=True).order_by('dau_order'))
    ordens = [f.dau_order for f in filhas]
    rotulo = {f.dau_order: str(f.display_order()) for f in filhas}
    if not ordens:
        return {'periodos': {}, 'unidade_produto': ''}
    try:
        unidade_produto = str(oti_produto.oti_pro_produto.pro_unidade_producao or '').strip()      # 🌟 unidade do produto
    except Exception:
        unidade_produto = ''
    with connection.cursor() as cur:
        # produção total e do produto de cada equipamento (cadastro) em cada período
        cur.execute("""
            with vars as (
                select pmf.pro_mer_flu_fluxo_producao_id as fluxo, pmf.pro_mer_flu_produto_id as produto, d.dau_order,
                       coalesce(d.dau_valor_12, d.dau_valor_2, 0) as prod
                from otimizacao_tbprodutomercadofluxo pmf
                join otimizacao_tbprodutomercadofluxodaugther d on d.mae_id = pmf.id
                where pmf.tbcenarios_id = %s and pmf.flag = true and d.flag = true and d.dau_order = any(%s)
            ),
            eq_io as (
                select fpio.mae_id as fluxo, eq.equ_codigo_id as cad, fpiod.dau_order, sum(fpiod.dau_valor_4) as saida
                from fluxos_tbfluxoproducaoinputoutput fpio
                join equipamentos_tbequipamentos eq on eq.id = fpio.flu_pro_inp_out_equipamento_id
                join fluxos_tbfluxoproducaoinputoutputdaugther fpiod on fpiod.mae_id = fpio.id and fpiod.dau_order = any(%s)
                where fpio.flag = true and fpio.mae_id in (select distinct fluxo from vars)
                group by fpio.mae_id, eq.equ_codigo_id, fpiod.dau_order
            )
            select e.cad, v.dau_order,
                   sum(v.prod * e.saida) as total,
                   sum(case when v.produto = %s then v.prod * e.saida else 0 end) as do_produto
            from vars v
            join eq_io e on e.fluxo = v.fluxo and e.dau_order = v.dau_order
            group by e.cad, v.dau_order
        """, [cid, ordens, ordens, produto_id])
        prod = {(cad, o): (float(t or 0), float(p or 0)) for cad, o, t, p in cur.fetchall()}
        usados = {cad for (cad, _o), (_t, p) in prod.items() if p > 0}

        # conjuntos ativos do cenário e os equipamentos de cada um
        cur.execute("""select ce.id, ce.oti_con_equ_descricao, j.tbequipamentoscadastro_id
                       from otimizacao_tbotimizacaoconjuntoequipamentos ce
                       join otimizacao_tbotimizacaoconjuntoequipamentos_oti_con_equ_equ15bf j
                            on j.tbotimizacaoconjuntoequipamentos_id = ce.id
                       where ce.tbcenarios_id = %s and ce.oti_con_equ_ativo = true""", [cid])
        conjuntos, nome_conj = defaultdict(list), {}
        for ce_id, desc, cad in cur.fetchall():
            conjuntos[ce_id].append(cad)
            nome_conj[ce_id] = str(desc or ce_id)
        conjuntos = {k: v for k, v in conjuntos.items() if set(v) & usados}
        limites = {}
        if conjuntos:
            cur.execute("""select mae_id, dau_order, dau_valor_2 from otimizacao_tbotimizacaoconjuntoequipamentosdaugther
                           where mae_id = any(%s) and dau_order = any(%s)""", [list(conjuntos), ordens])
            limites = {(m, o): (None if v is None else float(v)) for m, o, v in cur.fetchall()}

        cads = sorted(usados | {c for v in conjuntos.values() for c in v})
        cad_info, ocup = {}, {}
        if cads:
            cur.execute("""select id, equ_cad_codigo, equ_cad_descricao, equ_cad_gargalo, equ_cad_output
                           from equipamentos_tbequipamentoscadastro where id = any(%s)""", [cads])
            cad_info = {i: {'codigo': str(c or i).strip(), 'descricao': ' '.join(str(d or '').split()), 'gargalo': bool(g),
                            'unidade': str(u or '').strip()} for i, c, d, g, u in cur.fetchall()}
            cur.execute("""select o.oti_equ_equipamento_id, d.dau_order, d.dau_valor_6, d.dau_valor_3
                           from otimizacao_tbotimizacaoequipamentos o
                           join otimizacao_tbotimizacaoequipamentosdaugther d on d.mae_id = o.id
                           where o.tbcenarios_id = %s and o.flag = true and d.dau_order = any(%s)
                             and o.oti_equ_equipamento_id = any(%s)""", [cid, ordens, cads])
            for cad, ordem, horas, loading in cur.fetchall():
                horas, loading = float(horas or 0), float(loading or 0)
                ocup[(cad, ordem)] = round(100.0 * horas / loading, 1) if loading > 0 else None

    def info(cad):
        return cad_info.get(cad, {'codigo': str(cad), 'descricao': '', 'gargalo': False, 'unidade': ''})

    periodos = {}
    for ordem in ordens:
        gargalos = []
        for cad in sorted(usados):
            total, do_produto = prod.get((cad, ordem), (0.0, 0.0))
            if do_produto <= 0 or not info(cad)['gargalo']:
                continue
            gargalos.append({**info(cad), 'producao_total': round(total, 2), 'producao_produto': round(do_produto, 2),
                             'percentual_produto': round(100.0 * do_produto / total, 1) if total > 0 else None,
                             'ocupacao': ocup.get((cad, ordem))})
        gargalos.sort(key=lambda r: -r['producao_produto'])
        lista_conj = []
        for ce_id, membros in conjuntos.items():
            if not any(prod.get((c, ordem), (0, 0))[1] > 0 for c in membros):
                continue        # nenhum equipamento do conjunto produziu para o produto neste período
            producao = sum(prod.get((c, ordem), (0.0, 0.0))[0] for c in membros)
            limite = limites.get((ce_id, ordem))
            if limite is not None and limite >= LIMITE_SEM_MAXIMO:
                limite = None
            unidades = {info(c)['unidade'] for c in membros} - {''}
            lista_conj.append({
                'descricao': nome_conj[ce_id], 'producao': round(producao, 2),
                'unidade': unidades.pop() if len(unidades) == 1 else '',
                'limite_maximo': limite,
                'percentual_limite': round(100.0 * producao / limite, 1) if limite else None,
                'membros': sorted([{'codigo': info(c)['codigo'], 'descricao': info(c)['descricao'],
                                    'ocupacao': ocup.get((c, ordem)),
                                    'producao_total': round(prod.get((c, ordem), (0.0, 0.0))[0], 2),     # 🌟 30/10/2026
                                    'producao_produto': round(prod.get((c, ordem), (0.0, 0.0))[1], 2),
                                    'unidade': info(c)['unidade']} for c in membros], key=lambda r: r['codigo'])})
        lista_conj.sort(key=lambda r: r['descricao'])
        periodos[rotulo.get(ordem, str(ordem))] = {'gargalos': gargalos, 'conjuntos': lista_conj}
    return {'periodos': periodos, 'unidade_produto': unidade_produto}
