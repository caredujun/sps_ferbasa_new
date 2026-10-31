"""
🌟 NOVO (16/10/2026): diagnóstico do quadrado de CONSOLIDADO dos produtos -- mostra, com os dados do banco, por que ele
foi (ou não foi) criado. Uso:
    python manage.py shell -c "from custo_ferbasa.diagnostico_consolidado import rodar; rodar(['8050031', '8050002'], '2019/01', '2026/04')"
"""


def rodar(codigos, inicio, fim, empresa_id=None):
    from .models import TbItensProducao, TbProducaoMensal
    from .fluxo_producao_mensal import carregar_grafo
    ids = list(TbItensProducao.objects.filter(ite_pro_codigo__in=codigos).values_list('id', flat=True))
    print('itens encontrados (id):', ids)
    for i in TbItensProducao.objects.filter(id__in=ids).values('id', 'ite_pro_codigo', 'ite_pro_unidade'):
        print('  ', i['ite_pro_codigo'], 'id', i['id'], 'unidade', repr(i['ite_pro_unidade']))
    empresas = [empresa_id] if empresa_id else sorted({e for e in TbProducaoMensal.objects.filter(
        pro_men_item_producao_id__in=ids).values_list('empresa_id', flat=True)}, key=lambda x: (x is None, x))
    for emp in empresas:
        print('=== empresa', emp)
        g = carregar_grafo(emp, ids, inicio, fim)
        info = g.get('consolidado') or {}
        print('criado:', info.get('criado'), '| motivo:', info.get('motivo'))
        for p in info.get('produtos', []):
            print('  produto', p['codigo'], 'unidade', repr(p['unidade']), 'produção', round(p['producao'], 2))
        for c in info.get('consumo_cruzado', []):
            print('  %s consumido na cadeia de %s: %.2f%% da produção' % (c['item'], c['na_cadeia_de'], 100 * c['parte']))
        print('  fora do consolidado:', info.get('fora'))
        print('  caixa no desenho:', any(n.get('consolidado_geral') for n in g['nos']))
