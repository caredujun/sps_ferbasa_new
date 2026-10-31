"""
🌟 NOVO (16/10/2026): diagnóstico da EVOLUÇÃO MENSAL do custo de um produto -- mostra os meses fora do padrão (custo por
unidade total, variável ou fixo/outros muito longe da mediana) e, em cada um, de onde vem o valor: produção do mês,
material, GGF do item (nas linhas da Produção Mensal) e GGF distribuído (por tipo V/F/O), com as linhas maiores. Uso:
    python manage.py shell -c "from custo_ferbasa.diagnostico_mensal import rodar; rodar('8050031', '2019/01', '2026/04')"
"""
from collections import defaultdict
from statistics import median


def rodar(codigo, inicio, fim, empresa_id=1, fator=5.0):
    from .models import TbItensProducao
    from .fluxo_producao_mensal import _carregar_periodo, _num, _parte_variavel_ggf
    item = TbItensProducao.objects.filter(ite_pro_codigo=codigo).values('id', 'ite_pro_unidade').first()
    if not item:
        print('item não encontrado:', codigo)
        return
    pid = item['id']
    linhas, itens_producao, itens_consumo, ggf_linhas, pct_var_ggf = _carregar_periodo(empresa_id, [pid], inicio, fim)
    por_mes = defaultdict(lambda: {'prod': 0.0, 'mat_t': 0.0, 'mat_v': 0.0, 'ggfi_t': 0.0, 'ggfi_v': 0.0,
                                   'dist': defaultdict(lambda: [0.0, 0.0]), 'ordens': set(), 'vistos': set(), 'lin_mat': [], 'lin_ggf': []})
    for l in linhas:
        if l['pro_men_item_producao_id'] != pid:
            continue
        m = l['pro_men_ano_mes']
        r = por_mes[m]
        chave = (l['pro_men_estabelecimento_id'], l['pro_men_grupo_maquina_id'], l['pro_men_ordem_producao'])
        qp = _num(l['pro_men_qtde_produzida'])
        if chave not in r['vistos']:
            r['vistos'].add(chave)
            r['prod'] += qp
        if qp <= 0:
            continue
        r['ordens'].add((l['pro_men_item_producao_id'], l['pro_men_grupo_maquina_id'], l['pro_men_ordem_producao']))
        q, mat, ggf = _num(l['pro_men_qtde_consumo']), _num(l.get('pro_men_valor_material')), _num(l.get('pro_men_valor_ggf'))
        cod = itens_consumo[l['pro_men_item_consumo_id']]['codigo']
        pct = pct_var_ggf.get(cod, 0.0) if ggf > 0 else 0.0
        r['mat_t'] += mat * q
        r['mat_v'] += mat * q
        r['ggfi_t'] += ggf * q
        r['ggfi_v'] += ggf * q * (pct if pct > 0 else 0.0) / 100.0
        r['lin_mat'].append((mat * q + ggf * q, cod, q, mat, ggf, pct, l['pro_men_ordem_producao']))
    for g in ggf_linhas:
        r = por_mes.get(g['ano_mes'])
        if r is None or (g['item_id'], g['grupo_id'], g['ordem']) not in r['ordens']:
            continue
        v = _num(g['valor'])
        d = r['dist'][g.get('tipo')]
        d[0] += v
        d[1] += _parte_variavel_ggf(v, g.get('tipo'), g.get('percentual'))
        r['lin_ggf'].append((v, g.get('tipo'), g.get('percentual'), g.get('conta_descricao'), g['ordem']))
    tab = []
    for m in sorted(por_mes):
        r = por_mes[m]
        if r['prod'] <= 0:
            continue
        t = r['mat_t'] + r['ggfi_t'] + sum(d[0] for d in r['dist'].values())
        v = r['mat_v'] + r['ggfi_v'] + sum(d[1] for d in r['dist'].values())
        tab.append((m, r['prod'], t / r['prod'], v / r['prod'], (t - v) / r['prod']))
    if not tab:
        print('sem produção no período')
        return
    med = median(abs(x[2]) for x in tab) or 1.0
    print('meses com produção:', len(tab), '| mediana do custo total/un:', round(med, 2))
    fora = [x for x in tab if max(abs(x[2]), abs(x[3]), abs(x[4])) > fator * med]
    print('meses fora do padrão (> %.0fx a mediana):' % fator, len(fora))
    for m, prod, tu, vu, fu in fora:
        r = por_mes[m]
        print('\n=== %s  produção %.4f %s  | total/un %.2f  variável/un %.2f  fixo/un %.2f' % (m, prod, item['ite_pro_unidade'], tu, vu, fu))
        print('   material: total %.2f | GGF do item: total %.2f variável %.2f' % (r['mat_t'], r['ggfi_t'], r['ggfi_v']))
        for tipo, (dt, dv) in sorted(r['dist'].items(), key=lambda kv: str(kv[0])):
            print('   GGF distribuído tipo %s: total %.2f variável %.2f' % (tipo, dt, dv))
        print('   maiores linhas de GGF distribuído (valor, tipo, %, conta, ordem):')
        for x in sorted(r['lin_ggf'], key=lambda x: -abs(x[0]))[:10]:
            print('     ', round(x[0], 2), x[1], x[2], x[3], x[4])
        print('   maiores linhas de consumo (valor, item, qtde, material, ggf, %var, ordem):')
        for x in sorted(r['lin_mat'], key=lambda x: -abs(x[0]))[:10]:
            print('     ', round(x[0], 2), x[1], round(x[2], 4), x[3], x[4], x[5], x[6])
