"""
Diagnóstico da geração de fluxos por critério -- roda no shell Django.

Mostra, para cada equipamento de destino envolvido na cadeia do produto,
quantas arestas de entrada existem e como elas foram agrupadas por
tip_criterio_para_quebra_fluxo -- exatamente o tipo de inspeção manual que
fizemos com o FN03/3, só que para toda a cadeia de uma vez.

Uso:
    from diagnostico_criterios_fluxo import diagnosticar
    diagnosticar(2076, 32)
"""
from collections import defaultdict

from equipamentos.models import TbEquipamentos
from fluxos.models import TbFluxoConsumoPadrao


def diagnosticar(produto_id, cenario_id):
    ids_do_produto = set(
        TbEquipamentos.objects.filter(
            equ_produtos__id=produto_id, tbcenarios_id=cenario_id
        ).values_list('id', flat=True)
    )
    print(f"Equipamentos amarrados ao produto: {len(ids_do_produto)}")

    consumos = (
        TbFluxoConsumoPadrao.objects.filter(
            tbcenarios_id=cenario_id,
            flu_con_pad_from_equipamento_id__in=ids_do_produto,
            flu_con_pad_to_equipamento_id__in=ids_do_produto,
        )
        .select_related('flu_con_pad_from_equipamento__equ_codigo',
                         'flu_con_pad_from_equipamento__equ_tipo_producao',
                         'flu_con_pad_to_equipamento__equ_codigo')
    )

    por_destino = defaultdict(list)
    sem_criterio = []
    for c in consumos:
        origem = c.flu_con_pad_from_equipamento
        tipo = getattr(origem, 'equ_tipo_producao', None)
        criterio = (getattr(tipo, 'tip_criterio_para_quebra_fluxo', None) or '').strip()
        rotulo_origem = f"{origem.equ_codigo.equ_cad_codigo}/{origem.equ_ordem_codigo}"
        if not criterio:
            sem_criterio.append((c.id, rotulo_origem))
        por_destino[c.flu_con_pad_to_equipamento_id].append((rotulo_origem, criterio, c.id))

    if sem_criterio:
        print(f"\n⚠️  {len(sem_criterio)} aresta(s) com origem SEM critério preenchido:")
        for cid, rot in sem_criterio:
            print(f"   consumo_padrao id={cid}  origem={rot}")

    print(f"\nTotal de destinos distintos na cadeia: {len(por_destino)}\n")
    for destino_id, entradas in por_destino.items():
        destino = TbEquipamentos.objects.select_related('equ_codigo').get(id=destino_id)
        rotulo_destino = f"{destino.equ_codigo.equ_cad_codigo}/{destino.equ_ordem_codigo}"
        grupos = defaultdict(list)
        for rot, crit, cid in entradas:
            grupos[crit].append(rot)
        print(f"{rotulo_destino} (id={destino_id}) <- {len(entradas)} entrada(s) em {len(grupos)} critério(s):")
        total_combinacoes_aqui = 1
        for crit, membros in grupos.items():
            print(f"    critério '{crit}': {membros}  (tamanho do grupo: {len(membros)})")
            total_combinacoes_aqui *= len(membros)
        print(f"    => combinações só nesse nó (sem contar o que vem de trás): {total_combinacoes_aqui}\n")