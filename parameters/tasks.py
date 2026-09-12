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
from produtos.models import TbProdutos
from tabelas.models import TbMercado, TbCustoItemPreco, TbCustoItem
from .models import *
from django.db.models import Q


@shared_task(bind=True)
def verifica_filhas(self, id):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'VERIFICANDO FILHAS'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()

    # 🌟 NOVO: ANALYZE (não confundir com VACUUM, que não pode rodar dentro
    # de uma transação) nas tabelas que já confirmamos sofrerem com
    # estatísticas desatualizadas em dias de uso intenso (muitas
    # criações/exclusões/mudanças de período no mesmo cenário) --
    # fluxos_tbfluxoproducao* e fluxos_tbfluxoproducaoinputoutput*. Sem
    # isso, o Postgres pode escolher um plano de execução ruim pras
    # consultas grandes que vêm a seguir, fazendo o processo levar de
    # segundos a quase 1 hora, mesmo com os índices certos no lugar.
    for tabela_para_analisar in (
        'fluxos_tbfluxoproducao',
        'fluxos_tbfluxoproducao01',
        'fluxos_tbfluxoproducaodaugther',
        'fluxos_tbfluxoproducaodaugther01',
        'fluxos_tbfluxoproducaoinputoutput',
        'fluxos_tbfluxoproducaoinputoutputdaugther',
    ):
        try:
            cursor.execute(f"ANALYZE {tabela_para_analisar}")
        except Exception as analyze_err:
            # Não deixa uma tabela com nome errado ou temporariamente
            # indisponível derrubar a task inteira -- só loga e segue.
            print(f"⚠️ Não foi possível fazer ANALYZE em {tabela_para_analisar}: {analyze_err}")

    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha

    nome_tabela = 'tabelas_tbindicadores'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcambio'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbimpostorenda'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbtaxadesconto'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcustofixo'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_2('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbdepreamorti'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'tabelas_tbcapex'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    """
    As tabela 'tabelas_tbunidadeproducao'
              'tabelas_tbmercado'
              'tabelas_tbcustotipo'
              'tabelas_tbcustoitem'
              'tabelas_tbtipoproducao',
              'tabelas_tbfamiliaproduto',
              'tabelas_tbequacaoajustepreco',
              'produtos_tbprodutos',
    não tem filha(s). Portanto, não precisa verificar. 
    """

    nome_tabela = 'tabelas_tbcustoitempreco'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_5('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentoscadastro'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_3_valor_1_boolean('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentos'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_3_valor_3_boolean('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'equipamentos_tbequipamentosconsumoespecifico'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'produtos_tbprodutomercadopreco'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_4('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'produtos_tbmercadooutbound'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoconsumopadrao'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoproducao'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_daugther01('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'fluxos_tbfluxoproducaoinputoutput'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_2_case_1_optimized('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    nome_tabela = 'otimizacao_tbotimizacaoconjuntoequipamentos'
    print(nome_tabela)
    sql = "call public.verifica_mae_filha_3('" + nome_tabela + "', " + str(id) + ")"
    cursor.execute(sql)

    # Verificamos as filhas no cenário ativo. Se não tem, cria. Se número de periodos é menor que existente na filha, deleta.
    print('Tabela Cenários')
    sql = "call public.verifica_cenario_filha(" + str(id) + ")"
    cursor.execute(sql)

    cursor.close()


@shared_task(bind=True)
def remover_cenario_celery(self, id):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'REMOVENDO CENÁRIO'
    task_result.save()
    transaction.commit()

    # Dessa forma ativa todos os post e pode demorar
    cenario = TbCenarios.objects.get(id=id)
    cenario.delete()

    # Vamos ajustar a sequência
    cursor = connection.cursor()
    sql = "select public.reset_sequence('parameters_tbcenarios','id')"
    cursor.execute(sql)
    cursor.close()


@shared_task
def limpar_cenario_tabela_mae_celery(id_cenario):
    cursor = connection.cursor()
    sql = "call public.limpa_cenario_tabelas_mae(" + str(id_cenario) + ")"
    cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def atualizar_fluxos_celery(self, id_cenario):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO FLUXOS'
    task_result.save()
    transaction.commit()

    # Atualiza os inputs e outputs de todos os fluxos de produção do cenário ativo.
    cursor = connection.cursor()
    sql = "call public.atualiza_input_output_geral(" + str(id_cenario) + ")"
    cursor.execute(sql)
    cursor.close()

@shared_task(bind=True)
def limpar_cenario_celery(self, id_cenario):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'LIMPANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()
    sql = "call public.limpa_cenario(" + str(id_cenario) + ")"
    #sql = "call public.limpa_cenario_optimized()"
    cursor.execute(sql)
    cursor.close()

@shared_task(bind=True)
def duplica_tabela_celery(self,lista_tabela, chave_pk, copiar_de_id):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'DUPLICANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()
    for nome_tabela in lista_tabela:
        # Montando a expressão sql para rodar o Stored Procedure Duplica_Tabela
        print(nome_tabela)
        sql = "call public.duplica_tabela_optimized(" + str(chave_pk) + "," + str(
            copiar_de_id) + ", '" + nome_tabela + "')"
        cursor.execute(sql)
    cursor.close()


@shared_task
def importar_cenario_ativo_celery(lista_tabela, chave_pk, copiar_de_id):
    cursor = connection.cursor()
    for nome_tabela in lista_tabela:
        # Montando a expressão sql para rodar o Stored Procedure importar_cenario_ativo
        sql = "call public.importar_cenario_ativo(" + str(chave_pk) + "," + str(copiar_de_id) + ", '" + nome_tabela + "')"
        cursor.execute(sql)
    cursor.close()


@shared_task(bind=True)
def otimizar_cenario_celery(self, periodo, cen_ativo, total_variaveis):

    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'OTIMIZANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    otimizacao = 1  # Assumimos que foi encontrado solução

    i = periodo

    qs_mae = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).order_by('pro_mer_flu_variavel')

    # Função objetivo / Versão Nova
    cursor = connection.cursor()
    sql = "SELECT * from oti_funcao_objetivo(" + str(i + 1) + ", " + str(cen_ativo) + ")"
    cursor.execute(sql)
    c = cursor.fetchall()  # Tuple que representa a função objetivo
    cursor.close()
    # Ok. Função objetivo pronta

    # Vamos montar o bound para as variáveis
    # Se no fluxo de produção tivermos algum equipamento que não está running, a variável deve ser ZERO
    bounds = []  # Lista em vazio para cadastrar o bound das variáveis
    lista_id = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).values_list('id', flat=True).order_by('pro_mer_flu_variavel')
    for j in lista_id:
        if TbProdutoMercadoFluxoDaugther.objects.get(mae_id=j, dau_order=i+1).dau_valor_7:
            bounds.append([0, None])
        else:
            bounds.append([0, 0])

    # Vamos agora cadastrar as restrições (constraints)
    # configura a matriz de coeficiente das restrições
    A_ub = []
    # lista de restrições para limites superiores (restrições menores ou iguais)
    b_ub = []
    # registra o nome de cada restrição
    nome_restricao_array = []

    # *******************************
    # RESTRIÇÕES DE EQUIPAMENTO / VERSÃO NOVA
    # *******************************
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
            # Significa que já lançou na array o mínimo e máximo de horas do equipamento.
            contador = 1


    for id_restricao in id_restricao_array:
        #id_equipamento_cadastro = TbOtimizacaoEquipamentos.objects.get(id=id_restricao).oti_equ_equipamento_id
        nome_restricao_array.append([TbOtimizacaoEquipamentos.objects.get(id=id_restricao).oti_equ_equipamento, 'E', id_restricao]) # Ultimo é o id do equipamento na tabela TbOtimizacaoEquipamentos

    #if i == 1:
    #    print(nome_restricao_array)

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    # *******************************
    # RESTRIÇÕES DE CONJUNTO EQUIPAMENTOS
    # *******************************
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
            # Significa que já lançou na array o mínimo e máximo da restrição.
            contador = 1

    for id_restricao in id_restricao_array:
        nome_restricao_array.append([TbOtimizacaoConjuntoEquipamentos.objects.get(id=id_restricao).oti_con_equ_descricao, 'CE', id_restricao]) # Ultimo é o id do conjunto de equipamentos na tabela TbOtimizacaoConjuntoEquipamentos

    #if i == 1:
    #    print(nome_restricao_array)

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    # *******************************
    # RESTRIÇÕES DE PRODUTO / VERSÃO NOVA
    # *******************************
    #print('Restrições Produtos')
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
            # Significa que já lançou na array o mínimo e máximo da restrição.
            contador = 1

    for id_restricao in id_restricao_array:
        #nome_restricao_array.append([TbProdutos.objects.get(id=id_restricao).pro_descricao, 'P', id_restricao]) # Ultimo é o id do produto na tabela TbProdutos
        nome_restricao_array.append([TbOtimizacaoProduto.objects.get(id=id_restricao).oti_pro_produto, 'P', id_restricao])  # Ultimo é o id do produto na tabela TbOtimizacaoProduto

    #if i == 1:
    #    print(nome_restricao_array)

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)


    # *******************************
    # RESTRIÇÕES DE PRODUTO/MERCADO  / VERSÃO NOVA
    # *******************************
    #print('Restrições Produtos/Mercado')
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
            # Significa que já lançou na array o mínimo e máximo da restrição.
            contador = 1

    for id_restricao in id_restricao_array:
        #nome_restricao_array.append([TbProdutos.objects.get(id=id_restricao).pro_descricao, 'PM', id_restricao]) # Ultimo é o id do produto na tabela TbProdutos
        nome_restricao_array.append([str(TbProdutoMercado.objects.get(id=id_restricao).pro_mer_produto) + '/'+ str(TbProdutoMercado.objects.get(id=id_restricao).pro_mer_mercado) , 'PM', id_restricao])  # Ultimo é o id do produto/mercado na tabela TbProdutoMercado

    A_ub.extend(new_a_ub_function)
    b_ub.extend(new_b_ub_function)

    # *******************************
    # RESTRIÇÕES DE ITENS DE CUSTO VARIÁVEL POR PLANTA DE PRODUÇÃO / VESÃO ANTIGA / NÃO FIZEMOS FUNÇÃO NO BANCO DE DADOS PARA ESSE CASO
    # *******************************
    # Podemos ter a nível de planta de produção restrição de volume de consumo para os itens de custo variável.
    # Na tabela otimizacao_tbotimizacaocustoitem temos os itens de custo que poderão ser usados nessa otimização, e na filha otimizacao_tbotimizacaocustoitemdaugther
    # temos se é para considerar os limites mínimos e máximos informados (campo Ativo) e o valores dos limites.
    # Temos que abrir a tabela otimizacao_tbotimizacaocustoitemdaugther, e ir montando a lista de restrição para cada um dos itens por período.
    # Vamos ver quantos itens de custo por planta nós temos na tabela otimizacao_tbotimizacaocustoitemdaugther que estão ativos(que devem ser considerados na restrição).
    # Cada itens irá gerar duas linhas de restrições.
    total_item_custo = TbOtimizacaoCustoItemDaugther.objects.filter(tbcenarios_id=cen_ativo, flag=True, dau_order=i + 1, dau_valor_4=True).count()


    qs_item_custo    = TbOtimizacaoCustoItemDaugther.objects.filter(tbcenarios_id=cen_ativo, flag=True, dau_order=i + 1, dau_valor_4=True).order_by('id')

    for j in range(total_item_custo):

        restricao_minimo = []
        restricao_maximo = []

        # id_mae_custo_item_preco = TbOtimizacaoCustoItemDaugther.objects.get(id=qs_item_custo[j].id, dau_order=i + 1, flag=True).mae_id
        id_mae_custo_item_preco = qs_item_custo[j].mae_id
        id_custo_item_preco = TbOtimizacaoCustoItem.objects.get(id=id_mae_custo_item_preco).oti_cus_ite_item_id

        # Vamos pegar o id na Tabela de Custo Item
        id_custo_item = TbCustoItemPreco.objects.get(id=id_custo_item_preco).cus_ite_pre_item_id

        # Vamos pegar o nome do item de custo
        nome_item_custo = TbCustoItem.objects.get(id=id_custo_item).cus_ite_nome

        limite_minimo = (-1.0) * float(qs_item_custo[j].dau_valor_1)
        limite_maximo = (+1.0) * float(qs_item_custo[j].dau_valor_2)

        nome_restricao_array.append([nome_item_custo, 'IC', id_mae_custo_item_preco, id_mae_custo_item_preco]) # o penúltimo  é o id do item de custo na tabela TbOtimizacaoCustoItem, e o último é o id do item de custo na tabela TbOtimizacaoCustoItem

        # Temos que agora ir na tabela das variáveis e verificar se o fluxo de produção está usando esse item de custo e somar o output real. O mesmo item pode
        # estar sendo usando em diferentes equipamentos do fluxo de produção.
        for k in range(total_variaveis):
            coeficiente_variavel = 0
            # id_fluxo = TbProdutoMercadoFluxo.objects.get(id=qs_mae[k].id).pro_mer_flu_produto_id
            id_fluxo = qs_mae[k].pro_mer_flu_fluxo_producao_id
            # Vamos percorrer os equipamentos do fluxo e verificar se está usando o item de custo.
            # Temos os equipamentos/order (order de produção) na tabela fluxos_tbfluxoproducaoinputoutput, campo flu_pro_inp_out_equipamento_id para campo mae_id = id_fluxo (id do fluxo de produção).
            # Vamos criar uma query_set para termos os equipamentos do fluxo
            qs_equipamentos_fluxo = TbFluxoProducaoInputOutput.objects.filter(tbcenarios_id=cen_ativo, mae_id=id_fluxo)

            # Vamos montar um query_set só com o id e id do equipamento/order
            qs_equipamento_order = qs_equipamentos_fluxo.values_list('id', 'flu_pro_inp_out_equipamento_id', )
            for qs in qs_equipamento_order:

                id_equipamento_mae = qs[0]
                id_equipamento_order = qs[1]

                # Vamos ver se esse equipamento usa o item de custo.
                if TbEquipamentosConsumoEspecifico.objects.filter(
                        equ_con_esp_equipamento_id=id_equipamento_order,
                        equ_con_esp_custoitempreco_id=id_custo_item_preco).count() > 0:
                    id_mae_equipamento_consumo_especifico = TbEquipamentosConsumoEspecifico.objects.get(
                        equ_con_esp_equipamento_id=id_equipamento_order,
                        equ_con_esp_custoitempreco_id=id_custo_item_preco).id
                    # O equipamento está usando o item de custo. Temos que pegar o consumo especifico e output real nas filhas correspondentes
                    consumo_específico = TbEquipamentosConsumoEspecificoDaugther.objects.get(
                        mae_id=id_mae_equipamento_consumo_especifico, dau_order=i + 1).dau_valor
                    # Vamos agora pegar o outputreal do equipamento no fluxo de produção
                    output_real_equipamento = TbFluxoProducaoInputOutputDaugther.objects.get(
                        mae_id=id_equipamento_mae, dau_order=i + 1).dau_valor_4
                    coeficiente_variavel = coeficiente_variavel + consumo_específico * output_real_equipamento

            # sempre o mínimo e depois o máximo
            restricao_minimo.append((-1) * coeficiente_variavel)
            restricao_maximo.append(coeficiente_variavel)

        A_ub.append(restricao_minimo)
        b_ub.append(limite_minimo)

        A_ub.append(restricao_maximo)
        b_ub.append(limite_maximo)

    if c:  # c é a lista com a função objetivo. Se estiver vazia significa que não tem nada para otimizar.
        # Vamos resolver

        A_ub = np.array(A_ub)
        b_ub = np.array(b_ub)
        c = np.array(c)

        #model_linear = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds)
        model_linear = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')

        if model_linear.success:

            ranging = obter_ranging(c, A_ub, b_ub, bounds)

            # qs_mae e' o mesmo queryset ja usado mais abaixo pra gravar resultados
            # (TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True))
            # -- ATENCAO: mudou de 5 para 6 argumentos, lembra de atualizar a
            # chamada no tasks.py tambem, senao da o mesmo TypeError de antes
            extrair_shadow_prices(model_linear, nome_restricao_array, cen_ativo, i, ranging, qs_mae)
            resultados = model_linear.x


            for k in range(total_variaveis):
                # Na queryset qs_mae eu tenho as variáveis (qs_mae = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True))
                # Na variável total_variaveis tenho o total de variáveis
                # Vamos atualizar na filha para o dau_order = i + 1 (i é o contador que estamos utilizando para percorrer todo o período)
                # Vamos usar o stored procedure salva_resultados_order para os volumes de vendas na tabela otimizacao_tbprodutomercadofluxoDaugther

                if resultados[k] > 0:
                    #print(str(qs_mae[k].id))
                    #print(str(round(resultados[k], 0)))
                    #print(str(i + 1))
                    cursor = connection.cursor()  # Abrindo cursor para lançar os resultados
                    sql = "call public.salva_resultados_order(" + str(cen_ativo) + ", " + str(qs_mae[k].id) + ", " + str(round(resultados[k], 0)) + ", " + str(i + 1) + ")"
                    cursor.execute(sql)

            cursor = connection.cursor()  # Abrindo cursor para lançar os resultados médios de preço, custo variável e margem horária
            #sql = "update otimizacao_tbotimizacaoprodutodaugther set dau_valor_5 = dau_valor_5 / dau_valor_3, dau_valor_6 = dau_valor_6 / dau_valor_3, dau_valor_7 = dau_valor_7 / dau_valor_3 where flag = true and tbcenarios_id = " + str(
            #      cen_ativo) + " and dau_valor_3 > 0 and dau_order = " + str(i + 1)
            sql = "call public.calcula_media_preco_custo_margem_horaria(" + str(cen_ativo) + ", " + str(i + 1) + ")"
            cursor.execute(sql)

        else:
            otimizacao = 0

        # Vamos atualizar o flag da filha
        t = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i+1)
        t.flag = otimizacao  # change field
        t.save()  # this will update only

        # Vamos ver ser todos os períodos foram otimizados
        if TbCenariosDaugther.objects.filter(Q(flag=1) | Q(flag=0), mae_id=cen_ativo, otimizar=1).count() == TbCenariosDaugther.objects.filter(mae_id=cen_ativo, otimizar=1).count():

            # 🌟 CORRIGIDO: usa o id explícito 'cen_ativo' (já conhecido, passado
            # como parâmetro da task) em vez de cen_ativo=True, que dentro de uma
            # Celery task cai no fallback do Manager (sem usuário no contexto) --
            # podendo, em tese, não ser exatamente o mesmo cenário que esta task
            # está processando.
            t = TbCenarios.objects.get(id=cen_ativo)
            t.flag = 3  # change field
            t.save()  # this will update only

            # Vamos calcular a média aritmética para os casos onde vendas sugeridas = ZERO
            cursor = connection.cursor()
            sql = "call public.media_preco_custo_margem_horaria_venda_zerada(" + str(cen_ativo) + ")"
            cursor.execute(sql)
            cursor.close()


def classificar_gargalo(idx_concorrente, n_var, j, nome_restricao_array):
    """
    Classifica o gargalo do lado ATIVO de uma restricao (marginal != 0)
    usando DIRETAMENTE o indice de quem sai da base no ponto de quebra
    (o mesmo indice usado para montar o texto do concorrente), em vez de
    comparar numeros -- mais preciso, porque distingue os 3 casos que
    fisicamente acontecem:

    - 'ISOLADO':             o ponto de quebra e' o OUTRO lado desta MESMA
                             restricao (ex.: reduzir o maximo esbarra no
                             proprio minimo dela). Nada mais no modelo
                             interfere -- confiavel dentro de toda a folga.
    - 'ISOLADO (variavel)':  o ponto de quebra e' outra variavel estrutural
                             (outro produto/fluxo) chegando ao proprio zero.
                             Nao envolve nenhuma OUTRA regra de negocio --
                             e' um limite quase trivial, mas tecnicamente
                             nao e' so' esta restricao. Vale menos cautela
                             que COMPARTILHADO, mas mais que ISOLADO puro.
    - 'COMPARTILHADO':       o ponto de quebra e' outra restricao nomeada,
                             DIFERENTE desta (equipamento, outro produto
                             disputando capacidade, teto de venda em outro
                             mercado, etc). Existe uma decisao de negocio
                             separada envolvida -- merece investigar antes
                             de confiar so' no marginal.

    Chamar so' para o lado ATIVO; para o lado com folga total nao ha
    classificacao (ja tratado: valido_min/max_de/ate ficam None).
    """
    if idx_concorrente is None or idx_concorrente < 0:
        return "ISOLADO"  # sem concorrente identificado (limite natural da variavel)
    if idx_concorrente < n_var:
        return "ISOLADO (variavel)"
    linha = idx_concorrente - n_var
    j2 = linha // 2
    if j2 == j:
        return "ISOLADO"  # bateu no outro lado desta mesma restricao
    return "COMPARTILHADO"


def extrair_shadow_prices(model_linear, nome_restricao_array, cen_ativo, i, ranging, qs_mae=None):

    shadow = {
        'restricoes': model_linear.ineqlin.marginals,
        'residual': model_linear.ineqlin.residual,
    }
    total_restricoes = len(nome_restricao_array)

    # ranging agora e' um dict: dn/up (valor de b_ub por linha), dn_ou_var/up_ou_var
    # (quem causa a quebra em cada ponto) e n_var (pra traduzir os indices)
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

        # --- intervalo de validade dos marginais desta restricao ---
        #
        # IMPORTANTE: Highs_getRanging so' e' confiavel para o lado ATIVO
        # (marginal != 0, folga zero). Para o lado NAO-ATIVO (com folga),
        # o retorno do getRanging descreve outra coisa (nao documentada
        # publicamente pelo HiGHS) e NAO deve ser usado como range
        # classico de RHS -- isso foi verificado empiricamente e corrigido
        # depois de um erro de interpretacao.
        #
        # Para o lado nao-ativo, o range correto e' trivial e nao precisa
        # de getRanging: vai do piso natural da variavel ate o proprio
        # valor Real atual (onde a folga se esgota).
        #
        # NULL vs sentinela: quando um lado nao tem limite (ex.: maximo
        # nao-ativo nao tem teto superior), o valor correto e' None/NULL,
        # o que exige as colunas dau_valor_8..11 como null=True no banco
        # (ver migration). Se por algum motivo isso nao puder ser feito,
        # a alternativa e' um sentinela explicito, por exemplo:
        #     SEM_LIMITE = Decimal('999999999')
        # e trocar cada `None` abaixo por `SEM_LIMITE` -- mas isso so'
        # funciona se TODO consumidor da tabela (relatorios, queries,
        # dashboards) souber tratar esse numero magico como "infinito"
        # e nao como um limite real. Prefira a migration.
        marginal_min = -shadow['restricoes'][j * 2]
        marginal_max = -shadow['restricoes'][j * 2 + 1]
        # valor_minimo/valor_maximo vem do banco como Decimal; residual/ranging
        # vem do scipy/highspy como float -- precisa converter antes de operar
        # juntos, senao Decimal + float estoura TypeError
        valor_maximo_f = float(valor_maximo)
        # valor_real = Ax no ponto otimo. Calculado a partir do lado MAXIMO
        # (valor_maximo - residual da linha j*2+1), nao do lado minimo --
        # confirmado em producao que o lado minimo pode estar com indice
        # desalinhado para certos tipos de restricao (ex.: 'E', onde
        # valor_minimo=0 e' sempre redundante com o bound x>=0 da variavel).
        # Formula: linha do maximo e' x<=maximo direta, residual=maximo-x,
        # logo x = maximo - residual.
        valor_real = valor_maximo_f - float(shadow['residual'][j * 2 + 1])

        if abs(marginal_min) > 1e-9:
            # minimo ATIVO: usa getRanging (linha -x<=-minimo, inverte e troca)
            valido_min_de = -ranging_up[j * 2] if ranging_dn is not None else None
            valido_min_ate = -ranging_dn[j * 2] if ranging_dn is not None else None
        else:
            # minimo NAO-ATIVO (sem sombra): nao mostra range pra esse lado
            valido_min_de = None
            valido_min_ate = None

        if abs(marginal_max) > 1e-9:
            # maximo ATIVO: usa getRanging (linha x<=maximo direta)
            valido_max_de = ranging_dn[j * 2 + 1] if ranging_dn is not None else None
            valido_max_ate = ranging_up[j * 2 + 1] if ranging_dn is not None else None
        else:
            # maximo NAO-ATIVO (sem sombra): nao mostra range pra esse lado
            valido_max_de = None
            valido_max_ate = None

        # classificacao de gargalo -- so' faz sentido no lado ATIVO,
        # identifica QUEM e' a entidade concorrente em cada direcao
        # (reduzir o limite / aumentar o limite), usando ou_var_ -- a
        # variavel que sai da base em cada ponto de quebra. So' preenche
        # quando o ranging esta disponivel e o lado correspondente e' ATIVO.
        idx_reduzir = idx_aumentar = None
        if ranging_dn_ouvar is not None:
            if abs(marginal_max) > 1e-9:
                idx_reduzir = ranging_dn_ouvar[j * 2 + 1]
                idx_aumentar = ranging_up_ouvar[j * 2 + 1]
            elif abs(marginal_min) > 1e-9:
                # linha do minimo e' invertida (-x<=-minimo): reduzir o
                # minimo corresponde a b_ub AUMENTANDO, e vice-versa
                idx_reduzir = ranging_up_ouvar[j * 2]
                idx_aumentar = ranging_dn_ouvar[j * 2]

        obter_nome_variavel = (lambda idx: nome_variavel_produto_mercado_fluxo(idx, qs_mae)) if qs_mae is not None else None
        concorrente_reduzir = nome_da_entidade(idx_reduzir, n_var, nome_restricao_array, obter_nome_variavel)
        concorrente_aumentar = nome_da_entidade(idx_aumentar, n_var, nome_restricao_array, obter_nome_variavel)

        # classificacao de gargalo -- usa o MESMO ponto de quebra que ja
        # gerou o texto do concorrente (reduzir p/ maximo ativo, aumentar
        # p/ minimo ativo), agora comparando indices em vez de numeros:
        # distingue "bateu na propria restricao" (ISOLADO) de "bateu em
        # outra variavel" (ISOLADO variavel) de "bateu em outra restricao
        # nomeada" (COMPARTILHADO).
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
            # NOVOS CAMPOS -- requer migration adicionando estas colunas
            # em TbOtimizacaoShadow (ex.: DecimalField null=True)
            task_shadow.dau_valor_8 = valido_min_de     # limite inferior de validade do minimo
            task_shadow.dau_valor_9 = valido_min_ate    # limite superior de validade do minimo
            task_shadow.dau_valor_10 = valido_max_de    # limite inferior de validade do maximo
            task_shadow.dau_valor_11 = valido_max_ate   # limite superior de validade do maximo
            task_shadow.dau_texto_1 = tipo_gargalo       # 'ISOLADO' / 'COMPARTILHADO' / None
            task_shadow.dau_texto_2 = concorrente_reduzir   # o que limita reduzir mais
            task_shadow.dau_texto_3 = concorrente_aumentar  # o que limita aumentar mais
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
    """
    Recebe exatamente o mesmo c, A_ub, b_ub, bounds que ja vao para o
    scipy.optimize.linprog(method='highs') e devolve o intervalo de
    validade (dn, up) do b_ub de CADA linha de A_ub, calculado de forma
    analitica a partir da base otima do HiGHS -- uma unica chamada extra,
    sem re-otimizar o LP centenas/milhares de vezes.

    Tambem devolve ou_var_ (dn e up): o indice da variavel que "sai da
    base" em cada ponto de quebra -- ou seja, a variavel ou restricao
    concorrente que passa a limitar o resultado se voce for alem daquele
    ponto. Indices 0..n_var-1 sao variaveis estruturais (produtos/fluxos);
    indices >= n_var sao folgas de linhas de A_ub (linha = indice - n_var).

    Retorna None se o modelo nao tiver solucao otima (deixa o chamador
    decidir o que fazer nesse caso, igual ao model_linear.success do scipy).
    """
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
    """
    Traduz o indice de uma variavel estrutural (coluna de A_ub/c, mesma
    ordem de qs_mae) para "Produto / Mercado / Fluxo de Producao", usando
    os campos de TbProdutoMercadoFluxo:
      pro_mer_flu_produto        (FK TbProdutos)
      pro_mer_flu_mercado        (FK TbMercado)
      pro_mer_flu_fluxo_producao (FK TbFluxoProducao)
    """
    try:
        registro = qs_mae[idx]
    except (IndexError, TypeError):
        return f"Var #{idx}"
    return (f"{registro.pro_mer_flu_produto} / "
            f"{registro.pro_mer_flu_mercado} / "
            f"{registro.pro_mer_flu_fluxo_producao}")


def nome_da_entidade(idx, n_var, nome_restricao_array, obter_nome_variavel=None):
    """
    Traduz um indice de variavel do HiGHS (vindo de ou_var_) para um nome
    legivel: outra variavel estrutural (produto/fluxo) ou outra linha de
    restricao (min/max de outra restricao do modelo).

    obter_nome_variavel: funcao opcional idx -> string, para nomear
    variaveis estruturais usando seu proprio queryset (ex.: qs_mae).
    Se nao for passada, devolve so' o indice.
    """
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
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'CONSOLIDANDO CENÁRIO'
    task_result.save()
    transaction.commit()

    # Vai consolidar o cenário recebido
    cursor = connection.cursor()

    sql = "call public.consolida_resultados(" + str(id_cenario) + ")"
    cursor.execute(sql)

    # 🌟 CORRIGIDO: usa o id explícito em vez de "where cen_ativo = true"
    # (SQL bruta, não passa pelo Manager -- continuaria batendo no cenário
    # global errado se não for corrigida aqui também).
    sql  = "update parameters_tbcenarios set flag = 1 where id = " + str(id_cenario)  # 1 significa que foi consolidado
    cursor.execute(sql)

    cursor.close()