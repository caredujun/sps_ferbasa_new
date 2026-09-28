from celery import shared_task
import numpy as np
import json
import highspy

from django_celery_results.models import TaskResult
from scipy.sparse import csr_matrix
from scipy.optimize import linprog
from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos, TbEquipamentosDaugther, \
    TbEquipamentosConsumoEspecifico, TbEquipamentosCadastroDaugther, TbEquipamentosConsumoEspecificoDaugther
from fluxos.models import TbFluxoProducaoInputOutput, TbFluxoProducaoInputOutputDaugther
from otimizacao.models import TbProdutoMercadoFluxo, TbProdutoMercadoFluxoDaugther, TbOtimizacaoEquipamentos, \
    TbProdutoMercado, TbProdutoMercadoDaugther, TbOtimizacaoCustoItemDaugther, TbOtimizacaoCustoItem, \
    TbOtimizacaoEquipamentosDaugther, TbOtimizacaoProduto, TbOtimizacaoProdutoDaugther, \
    TbOtimizacaoConjuntoEquipamentos, TbOtimizacaoShadow, TbOtimizacaoConjuntoEquipamentosDaugther
from produtos.models import TbProdutos, TbProdutoMercadoPreco, TbMercadoOutbound
from tabelas.models import TbMercado, TbCustoItemPreco, TbCustoItem, TbCustoFixo, TbDepreAmorti, TbCapex, \
    TbUnidadeProducao, TbCustoTipo, TbTipoProducao, TbFamiliaProduto, TbGrupoCenarios, TbEquacaoAjustePreco
from .models import *
from django.db.models import Q


@shared_task(bind=True)
def verifica_filhas(self, id):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'VERIFICANDO FILHAS'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()

    nome_tabela = 'tabelas_tbindicadores'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcambio'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbimpostorenda'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbtaxadesconto'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcustofixo'
    sql = "call public.verifica_mae_filha_2('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbdepreamorti'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcapex'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcustoitempreco'
    sql = "call public.verifica_mae_filha_5('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentoscadastro'
    sql = "call public.verifica_mae_filha_3_valor_1_boolean('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentos'
    sql = "call public.verifica_mae_filha_3_valor_3_boolean('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentosconsumoespecifico'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'produtos_tbprodutomercadopreco'
    sql = "call public.verifica_mae_filha_4('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'produtos_tbmercadooutbound'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoconsumopadrao'
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoproducao'
    sql = "call public.verifica_mae_filha_daugther01('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoproducaoinputoutput'
    sql = "call public.verifica_mae_filha_2_case_1_optimized('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'otimizacao_tbotimizacaoconjuntoequipamentos'
    sql = "call public.verifica_mae_filha_3('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    sql = "call public.verifica_cenario_filha(" + str(id) + ")"
    cursor.execute(sql)

    cursor.close()


def _remover_cenario_de_verdade(id):
    """
    Lógica de exclusão de UM cenário -- extraída numa função própria pra
    poder ser reaproveitada tanto por remover_cenario_celery (exclusão
    avulsa de um cenário) quanto por remover_empresa_celery (que precisa
    remover TODOS os cenários da empresa, um de cada vez, antes de
    remover a empresa em si).
    """
    cenario = TbCenarios.objects.get(id=id)

    TbProdutoMercadoPreco.objects.filter(tbcenarios_id=id).update(
        pro_mer_pre_indicador=None,
        pro_mer_pre_indicador_vol_min=None,
        pro_mer_pre_indicador_vol_max=None,
    )
    TbMercadoOutbound.objects.filter(tbcenarios_id=id).update(mer_out_indicador=None)
    TbEquipamentosCadastro.objects.filter(tbcenarios_id=id).update(equ_cad_indicador_manutencao=None)
    TbCustoFixo.objects.filter(tbcenarios_id=id).update(fix_indicador=None)
    TbDepreAmorti.objects.filter(tbcenarios_id=id).update(dep_indicador=None)
    TbCapex.objects.filter(tbcenarios_id=id).update(cap_indicador=None)
    TbCustoItemPreco.objects.filter(tbcenarios_id=id).update(
        cus_ite_pre_indicador_preco=None,
        cus_ite_pre_indicador_inbound=None,
    )

    cenario.delete()

    cursor = connection.cursor()
    sql = "select public.reset_sequence('parameters_tbcenarios','id')"
    cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def remover_cenario_celery(self, id):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'REMOVENDO CENÁRIO'
    task_result.save()
    transaction.commit()

    _remover_cenario_de_verdade(id)


@shared_task(bind=True)
def remover_empresa_celery(self, id_empresa):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'REMOVENDO EMPRESA'
    task_result.save()
    transaction.commit()

    ids_cenarios = list(TbCenarios.objects.filter(empresa_id=id_empresa).values_list('id', flat=True))
    for id_cenario in ids_cenarios:
        _remover_cenario_de_verdade(id_cenario)

    TbGlossario.objects.filter(empresa_id=id_empresa).delete()
    AgenteConfig.objects.filter(empresa_id=id_empresa).delete()
    HistoricoAgente.objects.filter(empresa_id=id_empresa).delete()
    RelatorioPDF.objects.filter(empresa_id=id_empresa).delete()
    TbUnidadeProducao.objects.filter(empresa_id=id_empresa).delete()
    TbMercado.objects.filter(empresa_id=id_empresa).delete()
    TbCustoTipo.objects.filter(empresa_id=id_empresa).delete()
    TbCustoItem.objects.filter(empresa_id=id_empresa).delete()
    TbTipoProducao.objects.filter(empresa_id=id_empresa).delete()
    TbFamiliaProduto.objects.filter(empresa_id=id_empresa).delete()
    TbGrupoCenarios.objects.filter(empresa_id=id_empresa).delete()
    TbEquacaoAjustePreco.objects.filter(empresa_id=id_empresa).delete()

    empresa = TbEmpresa.objects.get(id=id_empresa)
    empresa.delete()


@shared_task
def limpar_cenario_tabela_mae_celery(id_cenario):
    cursor = connection.cursor()
    sql = "call public.limpa_cenario_tabelas_mae(" + str(id_cenario) + ")"
    cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def atualizar_fluxos_celery(self, id_cenario):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO FLUXOS'
    task_result.save()
    transaction.commit()

    # 🌟 NOVO: marca o cenário como "atualizando fluxos" (flag=7) antes de
    # começar, e "fluxos atualizados" (flag=8) ao terminar -- os 2
    # valores já estavam reservados pra isso em MENSAGENS_FLAG
    # (fluxo_criar_cenario.py), mas essa task nunca de fato os usava.
    # Sem isso, o chat não tinha como saber quando a atualização (via
    # "Limpar cenário", quando detecta fluxo desatualizado) realmente
    # termina, pra continuar sozinho.
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 7 where id = " + str(id_cenario)
    cursor.execute(sql)
    cursor.close()

    cursor = connection.cursor()
    sql = "call public.atualiza_input_output_geral(" + str(id_cenario) + ")"
    cursor.execute(sql)
    cursor.close()

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 8 where id = " + str(id_cenario)
    cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def limpar_cenario_celery(self, id_cenario):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'LIMPANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()
    sql = "call public.limpa_cenario(" + str(id_cenario) + ")"
    cursor.execute(sql)
    cursor.close()

    TbCenarios.objects.filter(id=id_cenario).update(ultimas_alteracoes='')


@shared_task(bind=True)
def duplica_tabela_celery(self, lista_tabela, chave_pk, copiar_de_id):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'DUPLICANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()
    for nome_tabela in lista_tabela:
        sql = "call public.duplica_tabela_optimized(" + str(chave_pk) + "," + str(
            copiar_de_id) + ", '" + nome_tabela + "')"
        cursor.execute(sql)
    cursor.close()


@shared_task
def importar_cenario_ativo_celery(lista_tabela, chave_pk, copiar_de_id):
    cursor = connection.cursor()
    for nome_tabela in lista_tabela:
        sql = "call public.importar_cenario_ativo(" + str(chave_pk) + "," + str(copiar_de_id) + ", '" + nome_tabela + "')"
        cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def otimizar_cenario_celery(self, periodo, cen_ativo, total_variaveis):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'OTIMIZANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    otimizacao = 1

    i = periodo

    qs_mae = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).order_by('pro_mer_flu_variavel')

    cursor = connection.cursor()
    sql = "SELECT * from oti_funcao_objetivo(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    c = cursor.fetchall()
    cursor.close()

    bounds = []
    lista_id = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).values_list('id', flat=True).order_by('pro_mer_flu_variavel')
    for j in lista_id:
        if TbProdutoMercadoFluxoDaugther.objects.get(mae_id=j, dau_order=i+1).dau_valor_7:
            bounds.append([0, None])
        else:
            bounds.append([0, 0])

    A_ub = []
    b_ub = []
    nome_restricao_array = []

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_equipamento_a_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    a_ub_function = cursor.fetchall()
    cursor.close()
    new_a_ub_function = []

    for a_list in a_ub_function:
        new = str(a_list).replace('([', '[')
        new = new.replace('],)', ']')
        new = new.replace("Decimal('0')", '0')
        new = new.replace("Decimal('", '')
        new = new.replace("')", '')
        new = json.loads(new)
        new_a_ub_function.append(new)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_equipamento_b_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    b_ub_function = cursor.fetchall()
    cursor.close()
    new_b_ub_function = []

    id_restricao_array = []
    contador = 1

    for b_list in b_ub_function:
        new = str(b_list)
        new = new.replace("(Decimal('", '')
        new = new.replace("'),)", '')
        if contador == 1:
            id_restricao_array.append(int(new))
        else:
            new_b_ub_function.append(float(new))

        contador += 1
        if contador == 4:
            contador = 1

    for id_restricao in id_restricao_array:
        nome_restricao_array.append([TbOtimizacaoEquipamentos.objects.get(id=id_restricao).oti_equ_equipamento, 'E', id_restricao])

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_conjunto_equipamento_a_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    a_ub_function = cursor.fetchall()
    cursor.close()
    new_a_ub_function = []

    for a_list in a_ub_function:
        new = str(a_list).replace('([', '[')
        new = new.replace('],)', ']')
        new = new.replace("Decimal('0')", '0')
        new = new.replace("Decimal('", '')
        new = new.replace("')", '')
        new = json.loads(new)
        new_a_ub_function.append(new)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_conjunto_equipamento_b_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    b_ub_function = cursor.fetchall()
    cursor.close()
    new_b_ub_function = []

    id_restricao_array = []
    contador = 1

    for b_list in b_ub_function:
        new = str(b_list)
        new = new.replace("(Decimal('", '')
        new = new.replace("'),)", '')
        if contador == 1:
            id_restricao_array.append(int(new))
        else:
            new_b_ub_function.append(float(new))

        contador += 1
        if contador == 4:
            contador = 1

    for id_restricao in id_restricao_array:
        nome_restricao_array.append([TbOtimizacaoConjuntoEquipamentos.objects.get(id=id_restricao).oti_con_equ_descricao, 'CE', id_restricao])

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_produto_a_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    a_ub_function = cursor.fetchall()
    cursor.close()
    new_a_ub_function = []

    for a_list in a_ub_function:
        new = str(a_list).replace('([', '[')
        new = new.replace('],)', ']')
        new = new.replace("Decimal('0')", '0')
        new = new.replace("Decimal('", '')
        new = new.replace("')", '')
        new = json.loads(new)
        new_a_ub_function.append(new)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_produto_b_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    b_ub_function = cursor.fetchall()
    cursor.close()
    new_b_ub_function = []

    id_restricao_array = []
    contador = 1

    for b_list in b_ub_function:
        new = str(b_list)
        new = new.replace("(Decimal('", '')
        new = new.replace("'),)", '')
        if contador == 1:
            id_restricao_array.append(int(new))
        else:
            new_b_ub_function.append(float(new))

        contador += 1
        if contador == 4:
            contador = 1

    for id_restricao in id_restricao_array:
        nome_restricao_array.append([TbOtimizacaoProduto.objects.get(id=id_restricao).oti_pro_produto, 'P', id_restricao])

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_produto_mercado_a_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    a_ub_function = cursor.fetchall()
    cursor.close()
    new_a_ub_function = []

    id_restricao_array = []
    contador = 1

    for a_list in a_ub_function:
        new = str(a_list).replace('([', '[')
        new = new.replace('],)', ']')
        new = new.replace("Decimal('0')", '0')
        new = new.replace("Decimal('", '')
        new = new.replace("')", '')
        new = json.loads(new)
        new_a_ub_function.append(new)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_produto_mercado_b_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    b_ub_function = cursor.fetchall()

    cursor.close()
    new_b_ub_function = []

    for b_list in b_ub_function:
        new = str(b_list)
        new = new.replace("(Decimal('", '')
        new = new.replace("'),)", '')

        if contador == 1:
            id_restricao_array.append(int(new))
        else:
            new_b_ub_function.append(float(new))

        contador += 1
        if contador == 4:
            contador = 1

    for id_restricao in id_restricao_array:
        nome_restricao_array.append([str(TbProdutoMercado.objects.get(id=id_restricao).pro_mer_produto) + '/'+ str(TbProdutoMercado.objects.get(id=id_restricao).pro_mer_mercado), 'PM', id_restricao])

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_custo_item_a_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    a_ub_function = cursor.fetchall()
    cursor.close()
    new_a_ub_function = []

    for a_list in a_ub_function:
        new = str(a_list).replace('([', '[')
        new = new.replace('],)', ']')
        new = new.replace("Decimal('0')", '0')
        new = new.replace("Decimal('", '')
        new = new.replace("')", '')
        new = json.loads(new)
        new_a_ub_function.append(new)

    cursor = connection.cursor()
    sql = "SELECT * from public.oti_restricao_custo_item_b_ub(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    b_ub_function = cursor.fetchall()
    cursor.close()
    new_b_ub_function = []

    id_restricao_array = []
    contador = 1

    for b_list in b_ub_function:
        new = str(b_list)
        new = new.replace("(Decimal('", '')
        new = new.replace("'),)", '')
        if contador == 1:
            id_restricao_array.append(int(new))
        else:
            new_b_ub_function.append(float(new))

        contador += 1
        if contador == 4:
            contador = 1

    for id_restricao in id_restricao_array:
        id_custo_item_preco = TbOtimizacaoCustoItem.objects.get(id=id_restricao).oti_cus_ite_item_id
        id_custo_item = TbCustoItemPreco.objects.get(id=id_custo_item_preco).cus_ite_pre_item_id
        nome_item_custo = TbCustoItem.objects.get(id=id_custo_item).cus_ite_nome
        nome_restricao_array.append([nome_item_custo, 'IC', id_restricao, id_restricao])

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    if c:
        A_ub = np.array(A_ub)
        b_ub = np.array(b_ub)
        c = np.array(c)

        model_linear = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')

        if model_linear.success:

            ranging = obter_ranging(c, A_ub, b_ub, bounds)

            extrair_shadow_prices(model_linear, nome_restricao_array, cen_ativo, i, ranging, qs_mae)
            resultados = model_linear.x

            for k in range(total_variaveis):
                if resultados[k] > 0:
                    cursor = connection.cursor()
                    sql = "call public.salva_resultados_order(" + str(cen_ativo) + ", " + str(qs_mae[k].id) + ", " + str(round(resultados[k], 0)) + ", " + str(i + 1) + ")"
                    cursor.execute(sql)

            cursor = connection.cursor()
            sql = "call public.calcula_media_preco_custo_margem_horaria(" + str(cen_ativo) + ", " + str(i + 1) + ")"
            cursor.execute(sql)

        else:
            otimizacao = 0

        t = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i+1)
        t.flag = otimizacao
        t.save()

        if TbCenariosDaugther.objects.filter(Q(flag=1) | Q(flag=0), mae_id=cen_ativo, otimizar=1).count() == TbCenariosDaugther.objects.filter(mae_id=cen_ativo, otimizar=1).count():

            t = TbCenarios.objects.get(id=cen_ativo)
            t.flag = 3
            t.save()

            cursor = connection.cursor()
            sql = "call public.media_preco_custo_margem_horaria_venda_zerada(" + str(cen_ativo) + ")"
            cursor.execute(sql)
            cursor.close()


def classificar_gargalo(idx_concorrente, n_var, j, nome_restricao_array):
    if idx_concorrente is None or idx_concorrente < 0:
        return "ISOLADO"
    if idx_concorrente < n_var:
        return "ISOLADO (variavel)"
    linha = idx_concorrente - n_var
    j2 = linha // 2
    if j2 == j:
        return "ISOLADO"
    return "COMPARTILHADO"


def extrair_shadow_prices(model_linear, nome_restricao_array, cen_ativo, i, ranging, qs_mae=None):

    shadow = {
        'restricoes': model_linear.ineqlin.marginals,
        'residual': model_linear.ineqlin.residual,
    }
    total_restricoes = len(nome_restricao_array)

    if ranging is not None:
        ranging_dn = ranging["dn"]
        ranging_up = ranging["up"]
        ranging_dn_ouvar = ranging["dn_ou_var"]
        ranging_up_ouvar = ranging["up_ou_var"]
        n_var = ranging["n_var"]
    else:
        ranging_dn = ranging_up = ranging_dn_ouvar = ranging_up_ouvar = n_var = None

    for j in range(total_restricoes):

        valor_minimo = 0
        valor_maximo = 0

        if nome_restricao_array[j][1] == 'E':
            valor_minimo = 0
            valor_maximo = TbOtimizacaoEquipamentosDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_3

        if nome_restricao_array[j][1] == 'CE':
            valor_minimo = TbOtimizacaoConjuntoEquipamentosDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_1
            valor_maximo = TbOtimizacaoConjuntoEquipamentosDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_2

        if nome_restricao_array[j][1] == 'P':
            valor_minimo = TbOtimizacaoProdutoDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_1
            valor_maximo = TbOtimizacaoProdutoDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_2

        if nome_restricao_array[j][1] == 'PM':
            valor_minimo = TbProdutoMercadoDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_1
            valor_maximo = TbProdutoMercadoDaugther.objects.get(mae_id=nome_restricao_array[j][2], dau_order=i + 1).dau_valor_2

        if nome_restricao_array[j][1] == 'IC':
            if TbOtimizacaoCustoItemDaugther.objects.get(mae_id=nome_restricao_array[j][3], dau_order=i + 1).dau_valor_4 == True:
                valor_minimo = TbOtimizacaoCustoItemDaugther.objects.get(mae_id=nome_restricao_array[j][3], dau_order=i + 1).dau_valor_1
                valor_maximo = TbOtimizacaoCustoItemDaugther.objects.get(mae_id=nome_restricao_array[j][3], dau_order=i + 1).dau_valor_2

        marginal_min = -shadow['restricoes'][j * 2]
        marginal_max = -shadow['restricoes'][j * 2 + 1]
        valor_maximo_f = float(valor_maximo)
        valor_real = valor_maximo_f - float(shadow['residual'][j * 2 + 1])

        if abs(marginal_min) > 1e-9:
            valido_min_de = -ranging_up[j * 2] if ranging_dn is not None else None
            valido_min_ate = -ranging_dn[j * 2] if ranging_dn is not None else None
        else:
            valido_min_de = None
            valido_min_ate = None

        if abs(marginal_max) > 1e-9:
            valido_max_de = ranging_dn[j * 2 + 1] if ranging_dn is not None else None
            valido_max_ate = ranging_up[j * 2 + 1] if ranging_dn is not None else None
        else:
            valido_max_de = None
            valido_max_ate = None

        idx_reduzir = idx_aumentar = None
        if ranging_dn_ouvar is not None:
            if abs(marginal_max) > 1e-9:
                idx_reduzir = ranging_dn_ouvar[j * 2 + 1]
                idx_aumentar = ranging_up_ouvar[j * 2 + 1]
            elif abs(marginal_min) > 1e-9:
                idx_reduzir = ranging_up_ouvar[j * 2]
                idx_aumentar = ranging_dn_ouvar[j * 2]

        obter_nome_variavel = (lambda idx: nome_variavel_produto_mercado_fluxo(idx, qs_mae)) if qs_mae is not None else None
        concorrente_reduzir = nome_da_entidade(idx_reduzir, n_var, nome_restricao_array, obter_nome_variavel)
        concorrente_aumentar = nome_da_entidade(idx_aumentar, n_var, nome_restricao_array, obter_nome_variavel)

        if abs(marginal_max) > 1e-9:
            tipo_gargalo = classificar_gargalo(idx_reduzir, n_var, j, nome_restricao_array)
        elif abs(marginal_min) > 1e-9:
            tipo_gargalo = classificar_gargalo(idx_aumentar, n_var, j, nome_restricao_array)
        else:
            tipo_gargalo = None

        if TbOtimizacaoShadow.objects.filter(
                oti_shadow_restricao=nome_restricao_array[j][0],
                oti_shadow_tipo=nome_restricao_array[j][1],
                oti_shadow_id_1=nome_restricao_array[j][2],
                dau_order=i + 1,
                tbcenarios_id=cen_ativo,
                flag=False
        ).count() > 0:
            task_shadow = TbOtimizacaoShadow.objects.get(
                oti_shadow_restricao=nome_restricao_array[j][0],
                oti_shadow_tipo=nome_restricao_array[j][1],
                oti_shadow_id_1=nome_restricao_array[j][2],
                dau_order=i + 1,
                tbcenarios_id=cen_ativo,
                flag=False
            )
            task_shadow.flag = True
            task_shadow.dau_valor_1 = -shadow['restricoes'][j * 2]
            task_shadow.dau_valor_2 = -shadow['restricoes'][j * 2 + 1]
            task_shadow.dau_valor_3 = shadow['residual'][j * 2]
            task_shadow.dau_valor_4 = shadow['residual'][j * 2 + 1]
            task_shadow.dau_valor_5 = valor_minimo
            task_shadow.dau_valor_6 = valor_maximo
            task_shadow.dau_valor_7 = valor_maximo - int(shadow['residual'][j * 2 + 1])
            task_shadow.dau_valor_8 = valido_min_de
            task_shadow.dau_valor_9 = valido_min_ate
            task_shadow.dau_valor_10 = valido_max_de
            task_shadow.dau_valor_11 = valido_max_ate
            task_shadow.dau_texto_1 = tipo_gargalo
            task_shadow.dau_texto_2 = concorrente_reduzir
            task_shadow.dau_texto_3 = concorrente_aumentar
            task_shadow.save()
            transaction.commit()
        else:
            TbOtimizacaoShadow.objects.create(
                flag=True,
                oti_shadow_restricao=nome_restricao_array[j][0],
                oti_shadow_tipo=nome_restricao_array[j][1],
                oti_shadow_id_1=nome_restricao_array[j][2],
                dau_order=i + 1,
                dau_valor_1=-shadow['restricoes'][j * 2],
                dau_valor_2=-shadow['restricoes'][j * 2 + 1],
                dau_valor_3=shadow['residual'][j * 2],
                dau_valor_4=shadow['residual'][j * 2 + 1],
                dau_valor_5=valor_minimo,
                dau_valor_6=valor_maximo,
                dau_valor_7=valor_maximo - int(shadow['residual'][j * 2 + 1]),
                dau_valor_8=valido_min_de,
                dau_valor_9=valido_min_ate,
                dau_valor_10=valido_max_de,
                dau_valor_11=valido_max_ate,
                dau_texto_1=tipo_gargalo,
                dau_texto_2=concorrente_reduzir,
                dau_texto_3=concorrente_aumentar,
                tbcenarios_id=cen_ativo,
            )
    return


def obter_ranging(c, A_ub, b_ub, bounds):
    n_var = len(c)
    n_con = len(b_ub)

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    inf = highspy.kHighsInf

    lower = np.array([b[0] if b[0] is not None else -inf for b in bounds])
    upper = np.array([b[1] if b[1] is not None else inf for b in bounds])
    h.addVars(n_var, lower, upper)
    h.changeColsCost(n_var, np.arange(n_var, dtype=np.int32), np.array(c, dtype=float))

    A_ub_arr = np.asarray(A_ub, dtype=float)
    for i in range(n_con):
        idx = np.nonzero(A_ub_arr[i])[0].astype(np.int32)
        h.addRow(-inf, float(b_ub[i]), len(idx), idx, A_ub_arr[i][idx])

    h.run()
    if h.getModelStatus() != highspy.HighsModelStatus.kOptimal:
        return None

    _, ranging = h.getRanging()
    return {
        "dn": ranging.row_bound_dn.value_,
        "up": ranging.row_bound_up.value_,
        "dn_ou_var": ranging.row_bound_dn.ou_var_,
        "up_ou_var": ranging.row_bound_up.ou_var_,
        "n_var": n_var,
    }


def nome_variavel_produto_mercado_fluxo(idx, qs_mae):
    try:
        registro = qs_mae[idx]
    except (IndexError, TypeError):
        return f"Var #{idx}"
    return (f"{registro.pro_mer_flu_produto} / "
            f"{registro.pro_mer_flu_mercado} / "
            f"{registro.pro_mer_flu_fluxo_producao}")


def nome_da_entidade(idx, n_var, nome_restricao_array, obter_nome_variavel=None):
    if idx is None or idx < 0:
        return None
    if idx < n_var:
        if obter_nome_variavel is not None:
            return f"Var: {obter_nome_variavel(idx)}"
        return f"Var #{idx}"
    linha = idx - n_var
    j2 = linha // 2
    lado = "minimo" if linha % 2 == 0 else "maximo"
    try:
        return f"Rest: {nome_restricao_array[j2][0]} ({lado})"
    except IndexError:
        return f"linha #{linha}"


@shared_task(bind=True)
def consolidar_cenario_celery(self, id_cenario):
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'CONSOLIDANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()

    sql = "call public.consolida_resultados(" + str(id_cenario) + ")"
    cursor.execute(sql)

    sql = "update parameters_tbcenarios set flag = 1 where id = " + str(id_cenario)
    cursor.execute(sql)

    cursor.close()


@shared_task(bind=True)
def exportar_dados_otimizacao_celery(self, cenario_id, usuario_id):
    """
    🌟 NOVO: gera, em segundo plano, os 6 relatórios de otimização
    (Produto, Produto-Mercado, Produto-Mercado-Fluxo, Equipamentos,
    Equipamentos-Ordem, Custo Item) do cenário informado -- rodar isso
    de forma síncrona dentro da requisição do chat arriscava estourar o
    limite de 30s de processamento por requisição do Heroku (o
    Produto-Mercado-Fluxo em especial, que chama 2 procedures do banco
    por linha). O resultado (links de download ou erro) fica gravado no
    EstadoConversaAgente do usuário, que o "Verificar" do chat consulta.
    """
    from django.contrib.auth import get_user_model
    from .models import EstadoConversaAgente, TbCenarios
    from .fluxo_criar_cenario import (
        _exportar_dados_otimizacao_produto, _exportar_dados_otimizacao_produto_mercado,
        _exportar_dados_otimizacao_produto_mercado_fluxo, _exportar_dados_otimizacao_equipamentos,
        _exportar_dados_otimizacao_equipamentos_ordem, _exportar_dados_otimizacao_custo_item,
    )

    UserModel = get_user_model()
    estado = EstadoConversaAgente.objects.filter(usuario_id=usuario_id).first()
    if estado is None:
        return

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        estado.dados_coletados = {**estado.dados_coletados, 'status': 'erro', 'mensagem': 'O cenário não existe mais.'}
        estado.save()
        return

    geradores = [
        ('Produto', _exportar_dados_otimizacao_produto),
        ('Produto-Mercado', _exportar_dados_otimizacao_produto_mercado),
        ('Produto-Mercado-Fluxo', _exportar_dados_otimizacao_produto_mercado_fluxo),
        ('Equipamentos', _exportar_dados_otimizacao_equipamentos),
        ('Equipamentos-Ordem', _exportar_dados_otimizacao_equipamentos_ordem),
        ('Custo Item', _exportar_dados_otimizacao_custo_item),
    ]

    arquivos_gerados = []
    erros = []
    for rotulo, funcao_geradora in geradores:
        try:
            caminho_completo, url, nome_arquivo = funcao_geradora(cenario)
            arquivos_gerados.append({'rotulo': rotulo, 'caminho': caminho_completo, 'url': url, 'nome_arquivo': nome_arquivo})
        except Exception as erro_geracao:
            erros.append(f"{rotulo}: {erro_geracao}")

    estado = EstadoConversaAgente.objects.filter(usuario_id=usuario_id).first()
    if estado is None or estado.dados_coletados.get('cenario_id') != cenario_id:
        return

    estado.dados_coletados = {
        **estado.dados_coletados,
        'status': 'concluido',
        'arquivos': arquivos_gerados,
        'erros': erros,
    }
    estado.save()


@shared_task(bind=True)
def enviar_email_relatorios_otimizacao_celery(self, usuario_id, arquivos):
    """
    🌟 NOVO: envia, em segundo plano, os relatórios de otimização já
    gerados (por exportar_dados_otimizacao_celery) como anexo por
    e-mail -- rodar isso de forma síncrona dentro da requisição do chat
    também arriscava estourar o limite de 30s do Heroku (6 anexos por
    SMTP pode demorar). O resultado (sucesso ou erro) fica gravado no
    EstadoConversaAgente do usuário, que o "Verificar" do chat consulta.
    `arquivos` é a mesma lista de dicts {'rotulo', 'caminho',
    'nome_arquivo', ...} salva no dados_coletados pela etapa anterior.
    """
    from django.contrib.auth import get_user_model
    from django.core.mail import EmailMessage
    from .models import EstadoConversaAgente

    UserModel = get_user_model()
    usuario = UserModel.objects.filter(id=usuario_id).first()
    if usuario is None:
        return

    email_usuario = (usuario.email or '').strip()

    def _gravar_resultado(status_email, extra=None):
        estado = EstadoConversaAgente.objects.filter(usuario_id=usuario_id).first()
        if estado is None or estado.dados_coletados.get('status_email') != 'enviando':
            return
        estado.dados_coletados = {**estado.dados_coletados, 'status_email': status_email, **(extra or {})}
        estado.save()

    if not email_usuario:
        _gravar_resultado('erro', {'mensagem': 'usuário sem e-mail cadastrado'})
        return

    try:
        mail = EmailMessage(
            subject="Relatórios de Otimização - Sistema SPS",
            from_email=None,
            to=[email_usuario],
            body="Segue em anexo os relatórios de otimização do cenário solicitados pelo chat do Agente IA.",
        )
        for arquivo in arquivos:
            with open(arquivo['caminho'], 'rb') as f:
                mail.attach(filename=arquivo['nome_arquivo'], content=f.read(),
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        mail.send(fail_silently=False)
        _gravar_resultado('enviado', {'email_usuario': email_usuario})
    except Exception as erro_envio:
        _gravar_resultado('erro', {'mensagem': str(erro_envio)})