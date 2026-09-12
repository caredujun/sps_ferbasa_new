import os

from boto3 import Session
from django.contrib import messages
from django.utils.html import format_html
from xlrd import open_workbook_xls
from celery import shared_task
from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos
from fluxos.models import TbFluxoProducao, TbFluxoProducaoInputOutput, TbFluxoProducaoDaugther, \
    TbFluxoProducaoDaugther01
from parameters.models import TbCenarios
import io
from django.core.files.base import ContentFile
from django.utils.safestring import mark_safe
from reportlab.pdfgen import canvas
from produtos.models import TbProdutos
from fluxos.models import TbFluxoProducao, TbFluxoConsumoPadrao
from django.db import connection, transaction

from sps import settings
from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from django_celery_results.models import TaskResult
from django.core.mail import EmailMessage
import openpyxl  # Para ler Excel xlsx


@shared_task(bind=True)
def importar_excel_fluxo_producao_celery(self, lista):
    '''
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'IMPORTANTO FLUXOS DE PRODUÇÃO'
    task_result.save()
    transaction.commit()
    '''
    # Nao usamos o try pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)

    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY
    bucket_name = 'spsferbasa'
    object_key = 'Fluxo de Producao.xlsx'

    # Vamos abrir o arquivo Excel que foi gravado no AWS S3
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    content = bucket_object.get()['Body'].read()

    wb = openpyxl.load_workbook(io.BytesIO(content))
    wb.active = wb['fluxo']
    sheet = wb.active

    # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
    total_colunas_mae = sheet.max_column
    total_linhas_mae = sheet.max_row
    lista_colunas = []
    for i in range(1, total_colunas_mae + 1):
        lista_colunas.append(sheet.cell(row=1, column=i).value)


    if lista_colunas == ['id',
                         'flu_pro_descricao',
                         'flu_pro_produto_id',
                         'flu_pro_produto_nome',
                         'custo_var_medio',
                         'flu_pro_ativo',
                         'flu_pro_copiar_de',
                         'flu_pro_observacao',
                         'flu_pro_erro',
                         'id_origem',
                         'tbcenarios_id']:

        # Cabeçalho da mãe está correto. Vamos agora ver o cabeçalho da aba sequencia (onde temos os equipamentos From --> To)
        wb.active = wb['sequência']
        sheet = wb.active  # Abrindo a sequencia

        # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
        total_colunas_sequencia = sheet.max_column
        total_linhas_sequencia = sheet.max_row
        lista_colunas = []

        for i in range(1, total_colunas_sequencia + 1):
            lista_colunas.append(sheet.cell(row=1, column=i).value)


        if lista_colunas == ['id',
                             'flu_pro_dau_coluna',
                             'flu_pro_dau_linha',
                             'flu_pro_dau_consumo_padrao_id',
                             'flu_pro_dau_consumo_padrao_descricao',
                             'mae_id',
                             'tbcenarios_id']:

            # Sequencia também com cabeçalho correto. Podemos continuar
            # Cabeçalho da sequência está correto. Vamos agora ver o cabeçalho da aba filha

            wb.active = wb['filhas']
            sheet = wb.active  # Abrindo a filhas

            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_filha = sheet.max_column
            total_linhas_filha = sheet.max_row
            lista_colunas = []

            for i in range(1, total_colunas_filha + 1):
                lista_colunas.append(sheet.cell(row=1, column=i).value)


            if lista_colunas == ['id',
                                 'dau_order',
                                 'dau_valor',
                                 'mae_id',
                                 'tbcenarios_id']:  # Filha também com cabeçalho correto. Podemos continuar

                #  Vamos ver se o total de lançamentos na filha está de acordo com o total de mães. Vamos precisar do total de períodos do cenário ativo.

                #  Vamos obter o total de períodos para o cenário ativo
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                if (total_linhas_filha - 1) == (total_linhas_mae - 1) * total_periodos:

                    # Vamos setar a mensagem de erro com vazio. Será preenchida se ocorrer erro
                    mensagem_erro = ''
                    # Vamos setar que está tudo ok a princípio
                    ok_cenario = True

                    #  Vamos ver se o cenário informado no fluxo, sequência e na filhas é o cenário ativo.
                    # Cenário ativo
                    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                    wb.active = wb['fluxo']
                    sheet = wb.active  # Abrindo a aba fluxo


                    for j in range(1, total_linhas_mae + 1):  # A coluna do cenário é a de ordem 11 na mãe
                        if j > 1:  # Porque a linha 1 é o cabeçalho

                            if sheet.cell(j, 11).value != cen_ativo:
                                ok_cenario = False
                                mensagem_erro = 'Cenário informado na aba fluxo não é o ativo. Favor verificar!'
                                break
                    if ok_cenario:
                        wb.active = wb['sequência']
                        sheet = wb.active  # Abrindo a aba sequência

                        for j in range(1, total_linhas_sequencia + 1):  # A coluna do cenário é a de ordem 7 na mãe
                            if j > 1:  # Porque a linha 1 é o cabeçalho
                                if sheet.cell(j, 7).value != cen_ativo:
                                    ok_cenario = False
                                    mensagem_erro = 'Cenário informado na aba sequência não é o ativo. Favor verificar!'
                                    break
                        if ok_cenario:

                            wb.active = wb['filhas']
                            sheet = wb.active  # Abrindo a aba filhas

                            for j in range(1, total_linhas_filha + 1): # A coluna do cenário é a de ordem 5 na filha
                                if j > 1:  # Porque a linha 1 é o cabeçalho
                                    if sheet.cell(j, 5).value != cen_ativo:
                                        ok_cenario = False
                                        mensagem_erro = 'Cenário informado na aba filha não é o ativo. Favor verificar!'
                                        break
                    if not ok_cenario:
                        pass
                        #messages.error(request, mensagem_erro)
                    else:
                        #  Tudo ok até aqui. Vamos verificar se no arquivo tem as mães selecionadas. E se tiver, vamos ver se tem as filhas no total do período do cenário.
                        #  Vamos pegar o id das mães selecionadas.
                        for id_mae in lista:
                            # Vamos ver se tem somente uma mãe na aba mãe

                            wb.active = wb['fluxo']
                            sheet = wb.active  # Abrindo a aba fluxo

                            conta_mae = 0
                            for j in range(1, total_linhas_mae + 1):  # A coluna do id da mãe é a de ordem 1
                                if j > 1:  # Porque a linha 1 é o cabeçalho
                                    if sheet.cell(j, 1).value == id_mae[0]:  # É o id da mãe. Soma no contador.
                                        conta_mae = conta_mae + 1

                            if conta_mae == 0:
                                ok_cenario = False
                                mensagem_erro = 'Na aba mãe do arquivo em Excel não existe o id = ' + str(
                                    id_mae[0]) + '. Favor verificar!'
                                break
                            else:
                                if conta_mae > 1:
                                    # Se tiver mais que 1 também é problema
                                    ok_cenario = False
                                    mensagem_erro = 'Na aba mãe do arquivo em Excel tem mais que 1 (um) id = ' + str(
                                        id_mae[0]) + '. Favor verificar!'
                                    break
                        if not ok_cenario:
                            pass
                            #messages.error(request, mensagem_erro)
                        else:

                            #  Vamos ver se o total de lançamentos na sequência a nível de fluxo está de acordo com o total no registro gravado.
                            #  Partimos do princípio que eventual alteração no fluxo foi no id do Consumo Padrão, e não na qtde de lançamentos.
                            #  Temos que ir na aba mãe, pegar o id do fluxo. Com o id do fluxo, vamos no registro original e verificamos quantos lançamentos foram feitos na sequência.
                            #  Com esse valor voltamos na planiha Excel e verificamos quantos lançamentos foram informados para o id do fluxo

                            wb.active = wb['fluxo']
                            sheet = wb.active  # Abrindo a aba fluxo

                            for i in range(1, total_linhas_mae + 1):  # A coluna do id do fluxo é a coluna de ordem 1
                                if i > 1:  # Porque a linha 1 é o cabeçalho
                                    id_fluxo = sheet.cell(i, 1).value
                                    # Vamos ver quantos lançamentos ocorreram no registro original e comparar
                                    total_lancamentos_original = TbFluxoProducaoDaugther.objects.filter(mae_id=id_fluxo).count()
                                    # Vamos ver quantos lançamentos ocorreram na aba sequencia para o fluxo de produção
                                    total_lancamentos_sequencia = 0

                                    wb.active = wb['sequência']
                                    sheet = wb.active  # Abrindo a aba sequência

                                    for j in range(total_linhas_sequencia + 1):  # A coluna da mãe_id é a de ordem 6
                                        if j > 1 and sheet.cell(j, 6).value == id_fluxo:  # É o id do fluxo. Soma no contador.
                                            total_lancamentos_sequencia = total_lancamentos_sequencia + 1
                                    if total_lancamentos_original != total_lancamentos_sequencia:
                                        ok_cenario = False
                                        mensagem_erro = 'Total de lançamentos na aba sequência não está igual ao salvo no banco de dados. Fluxo Id = ' + str(
                                            id_fluxo) + '. Favor verificar!'
                                        break
                            if not ok_cenario:  # Total de lançamentos original diferente total_lancamentos_sequencia
                                pass
                                #messages.error(request, mensagem_erro)
                            else:
                                #  Tudo certo até aqui. Podemos continuar.
                                #  Vamos ver se na tabela filhas existe um total de filhas igual ao total de periodos

                                wb.active = wb['filhas']
                                sheet = wb.active  # Abrindo a aba sequência

                                conta_filha = 0
                                for i in range(1, total_linhas_filha + 1):  # A coluna da mãe_id é a de ordem 4
                                    if i > 1 and sheet.cell(i, 4).value == id_mae[0]:  # É o id da mãe. Soma no contador.
                                        conta_filha = conta_filha + 1

                                if conta_filha != total_periodos:
                                    ok_cenario = False
                                    mensagem_erro = 'Total de filhas para a mãe id = ' + str(id_mae[0]) + 'é igual a ' + str(conta_filha) + ', diferente de ' + str(total_periodos) + '. Favor verificar!'
                                    #messages.error(request, mensagem_erro)
                                else:
                                    for id_mae in lista:
                                        # Vamos ver agora se o dau_order nas filhas estão na sequência certa
                                        conta_dau_order = 0
                                        for j in range(1, total_linhas_filha + 1):  # A coluna da mãe_id é a de ordem 4 e a do dau_order é o 1
                                            if j > 1:  # Porque a linha 0 é o cabeçalho
                                                if sheet.cell(j, 4).value == id_mae[0]:  # É o id da mãe. Vamos somar no contador e comparar com o dau_order
                                                    conta_dau_order = conta_dau_order + 1
                                                    if conta_dau_order != sheet.cell(j, 2).value:
                                                        ok_cenario = False
                                                        mensagem_erro = 'Sequencial dau_order para a mãe id = ' + str(id_mae[0]) + ' está fora da sequencia na filha. Favor verificar!'
                                                        #messages.error(request, mensagem_erro)
                                                        break
                                        if ok_cenario:
                                            #  Vamos agora atualizar a mãe, sequência e as filhas pois tudo está Ok com o arquivo Excel

                                            wb.active = wb['fluxo']
                                            sheet = wb.active  # Abrindo a aba fluxo

                                            for i in range(1, total_linhas_mae + 1):  # A coluna do id da mãe é a de ordem 1
                                                if i > 1 and sheet.cell(i, 1).value == id_mae[0]:  # Porque a linha 0 é o cabeçalho
                                                    tab_obj = TbFluxoProducao.objects.get(id=sheet.cell(i, 1).value)
                                                    print(sheet.cell(i, 2).value)
                                                    tab_obj.flu_pro_descricao = sheet.cell(i, 2).value
                                                    tab_obj.flu_pro_produto_id = sheet.cell(i, 3).value
                                                    tab_obj.flu_pro_ativo = sheet.cell(i, 6).value
                                                    tab_obj.flu_pro_observacao = sheet.cell(i, 8).value
                                                    tab_obj.save()

                                            # Vamos agora atualizar a sequência

                                            wb.active = wb['sequência']
                                            sheet = wb.active  # Abrindo a aba sequência

                                            for i in range(1, total_linhas_sequencia + 1):  # A coluna da mãe_id é a de ordem 6
                                                if i > 1 and sheet.cell(i, 6).value == id_mae[0]:  # É o id da mãe
                                                    tab_obj = TbFluxoProducaoDaugther.objects.get(id=sheet.cell(i, 1).value, mae_id=id_mae[0])
                                                    tab_obj.flu_pro_dau_coluna = sheet.cell(i, 2).value
                                                    tab_obj.flu_pro_dau_linha = sheet.cell(i, 3).value
                                                    tab_obj.flu_pro_dau_consumo_padrao_id = sheet.cell(i, 4).value
                                                    tab_obj.save()

                                            # Vamos agora atualizar as filhas

                                            wb.active = wb['filhas']
                                            sheet = wb.active  # Abrindo a aba filhas

                                            for i in range(1, total_linhas_filha + 1):  # A coluna da mãe_id é a de ordem 4
                                                if i > 1 and sheet.cell(i, 4).value == id_mae[0]:  # É o id da mãe
                                                    # Temos que pegar o dau_order
                                                    dau_order_filha = sheet.cell(i, 2).value
                                                    tab_obj = TbFluxoProducaoDaugther01.objects.get(id=sheet.cell(i, 1).value, mae_id=id_mae[0], dau_order=dau_order_filha)
                                                    tab_obj.dau_valor = sheet.cell(i, 3).value
                                                    tab_obj.save()

                                    if ok_cenario:  # Tudo ok. Vamos mandar mensagem
                                        pass
                                        #messages.success(request, 'Tabela Fluxos de Produção foi atualizada com sucesso!')

                else:
                    pass
                    #messages.error(request, 'Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!')

            else:
                pass
                #messages.error(request, 'Cabeçalho da aba filha não está correto. Favor verificar!')

        else:
            pass
            #messages.error(request, 'Cabeçalho da aba sequência não está correto. Favor verificar!')

    else:
        pass
        #messages.error(request, 'Cabeçalho da aba mãe não está correto. Favor verificar!')

    # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo

    try:
        #pass
        bucket_object.delete()
        #messages.success(request, 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
    except:
        pass
        #messages.error(request, 'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')


@shared_task
def update_indicador(id_consumo_padrao):
    cursor = connection.cursor()
    sql = "call public.update_indicador_consumo_especifico(" + str(id_consumo_padrao) + ")"
    cursor.execute(sql)
    cursor.close()

# Quando temos alteração no consumo padrão
@shared_task
def atualiza_input_output_1_celery(id_consumo_padrao):
    cursor = connection.cursor()
    sql = "call public.atualiza_input_output_passando_id_cosumo_padrao(" + str(id_consumo_padrao) + ")"
    cursor.execute(sql)
    cursor.close()

# Para ajustar o número da coluna e linha no sequenciamento do fluxo de produção
@shared_task
def ajusta_sequencia_fluxo_celery(id_fluxo_producao):
    # Só vamos executar se existir o fluxo
    if TbFluxoProducao.objects.filter(id=id_fluxo_producao).exists():
        cursor = connection.cursor()
        sql = "call public.ajusta_sequencia_fluxo(" + str(id_fluxo_producao) + ")"
        cursor.execute(sql)
        cursor.close()

# Quando temos alteração no sequenciamento do fluxo de produção
@shared_task(bind=True)
def atualiza_input_output_celery(self,  id_fluxo_producao):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO FLUXOS (INPUT/OUTPUT)'
    task_result.save()
    transaction.commit()

    # Vamos atualizar o flag do Input/Output (I/O)
    TbFluxoProducao.objects.filter(id=id_fluxo_producao).update(flu_pro_input_output_atualizado=False)

    cursor = connection.cursor()
    sql = "call public.atualiza_input_output(" + str(id_fluxo_producao) + ")"
    cursor.execute(sql)
    cursor.close()

@shared_task(bind=True)
def atualizar_fluxo_celery(self, lista):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO FLUXOS (INPUT/OUTPUT)'
    task_result.save()
    transaction.commit()

    # Vamos atualizar o flag do Input/Output (I/O)
    TbFluxoProducao.objects.filter(id__in=lista).update(flu_pro_input_output_atualizado=False)

    # 🌟 NOVO: deriva o cenário a partir dos próprios fluxos da lista (mais
    # preciso que usar o cenário ativo global, já que essa é uma Celery task
    # sem contexto de usuário logado).
    id_cenario = TbFluxoProducao.objects.filter(id__in=lista).values_list('tbcenarios_id', flat=True).first()

    cursor = connection.cursor()
    # Observar que tem que colocar o array antes da string da lista
    sql = "call public.atualiza_input_output_lista(" + str(id_cenario) + ", array" + str(lista) + ")"
    cursor.execute(sql)
    cursor.close()

    '''
    # Vamos atualizar um a um
    cursor = connection.cursor()

    for i in lista:
        sql = "call public.atualiza_input_output(" + str(i) + ")"
        cursor.execute(sql)

    cursor.close()
    '''

@shared_task(bind=True)
def atualizar_custos_celery(self, lista):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO CUSTOS'
    task_result.save()
    transaction.commit()

    # Vamos atualizar um a um
    cursor = connection.cursor()

    for i in lista:
        sql = "call public.atualiza_custo_var_fluxo_producao(" + str(i) + ")"
        cursor.execute(sql)

    cursor.close()

@shared_task(bind=True)
def update_fluxo_lista(self, lista):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'ATUALIZANDO FLUXO (PDF)'
    task_result.save()
    transaction.commit()

    for i in lista:
        update_fluxo_celery(i)


@shared_task(bind=True)
def importar_excel_new_segundo_celery(self):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'IMPORTANDO NOVOS FLUXOS DE PRODUÇÃO'
    task_result.save()
    transaction.commit()

    # O arquivo para importar novos fluxos de produção é constituido pelas sequintes colunas
    # Descricao Fluxo
    # Id Produto
    # Nome Produto
    # Coluna
    # Linha
    # Id Consumo Padrao
    # Descricao Consumo Padrao
    # Esse arquivo tem que ser montado manualmente
    # As colunas Nome Produto e Descricao Consumo Padrao não serão importadas. São somente para descrever o produto e o consumo padrão

    # Para permitir acesso ao AWS S3
    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY
    bucket_name = 'spsferbasa'
    object_key = 'fluxoproducao_new.xls'

    # Vamos abrir o arquivo Excel que foi gravado no AWS S3
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    content = bucket_object.get()['Body'].read()

    wb = open_workbook_xls(file_contents=content)

    sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba, onde estão as informações a serem adicionadas
    # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
    total_colunas_mae = sheet.ncols
    total_linhas_mae = sheet.nrows
    lista_colunas = []
    for i in range(total_colunas_mae):
        lista_colunas.append(sheet.cell_value(0, i))

    if lista_colunas == ['Descricao Fluxo',
                         'Id Produto',
                         'Nome Produto',
                         'Coluna',
                         'Linha',
                         'Id Consumo Padrao',
                         'Descricao Consumo Padrao']:

        # Cabeçalho do arquivo está correto.
        # Vamos verificar se os dados informados estão ok.
        dados_ok = True
        # Cenário ativo
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

        for i in range(total_linhas_mae):
            if i > 0:  # Porque a linha 0 é o cabeçalho
                # Vamos ver se a informação é nova.
                # Nesse caso temos que ver se a descrição do fluxo de produção já existe.
                if TbFluxoProducao.objects.filter(flu_pro_descricao=sheet.cell_value(i, 0),
                                                  tbcenarios_id=cen_ativo).count() == 0:
                    # Não existe. Podemos contonuar.
                    # Vamos ver se o id do produto informado existe.
                    if TbProdutos.objects.filter(id=sheet.cell_value(i, 1)).count() > 0:
                        # Existe. Podemos continuar.
                        # Vamos ver se id do consumo padrão informado existe.
                        if TbFluxoConsumoPadrao.objects.filter(id=sheet.cell_value(i, 5)).count() > 0:
                            # Existe. Podemos continuar.
                            pass
                        else:
                            dados_ok = False
                            #messages.error(request, 'Não existe o consumo padrão com id = ' + str(sheet.cell_value(i, 5)) + ' cadastrado. Favor verificar!')
                            break  # Pula fora do for ...
                    else:
                        dados_ok = False
                        #messages.error(request, 'Não existe o produto com id = ' + str(sheet.cell_value(i, 1)) + ' cadastrado. Favor verificar!')
                        break  # Pula fora do for ...
                else:
                    dados_ok = False
                    #messages.error(request, 'Já existe o fluxo de produção = ' + sheet.cell_value(i, 0) + '. Favor verificar!')
                    break  # Pula fora do for ...

        if dados_ok:
            # Tudo ok com os dados. Podemos atualizar
            for i in range(total_linhas_mae):
                if i > 0:  # Porque a linha 0 é o cabeçalho
                    descricao_fluxo = sheet.cell_value(i, 0)[:150]
                    # Vamos ver se existe o fluxo cadastrado. Se não cadastra.
                    if TbFluxoProducao.objects.filter(flu_pro_descricao=descricao_fluxo,
                                                      tbcenarios_id=cen_ativo).count() == 0:
                        # Vamos cadastrar

                        TbFluxoProducao.objects.create(
                            flu_pro_descricao=sheet.cell_value(i, 0)[:150],
                            flu_pro_produto_id=int(sheet.cell_value(i, 1)),
                            flu_pro_ativo=True,
                            tbcenarios_id=cen_ativo)

                        # Temos que pegar o id da mãe que foi criado para lançar na filha
                        id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo,
                                                             tbcenarios_id=cen_ativo).id

                        # Vamos atualizar a filha usando o Stored Procedure verifica_filha_boolean (só tem um campo e é boolean
                        cursor = connection.cursor()
                        # Montando a expressão sql para rodar o Stored Procedure
                        sql = "call public.verifica_filha_boolean('fluxos_tbfluxoproducaodaugther01', " + str(
                            id_mae) + ", " + str(cen_ativo) + ", true)"
                        cursor.execute(sql)
                        cursor.close()
                    else:
                        # Fluxo de produção foi cadastrado anteriormente. Vamos pegar o id da mãe.
                        id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=sheet.cell_value(i, 0),
                                                             tbcenarios_id=cen_ativo).id

                    # Vamos cadastrar o consumo padrão na tabela de sequenciamento da produção
                    TbFluxoProducaoDaugther.objects.create(
                        flu_pro_dau_coluna=int(sheet.cell_value(i, 3)),
                        flu_pro_dau_linha=int(sheet.cell_value(i, 4)),
                        flu_pro_dau_consumo_padrao_id=int(sheet.cell_value(i, 5)),
                        mae_id=id_mae,
                        tbcenarios_id=cen_ativo)

            # Vamos informar que o arquivo foi atualizado com sucesso e eliminar o mesmo do AWS S3
            # Se chegou até aqui tudo ok. Vamos dar a mensagem de sucesso

            #messages.success(request, 'Novo(s) Fluxo(s) de Produção foi(ram) adicionado(s) com sucesso.')

            # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
            try:
                bucket_object.delete()
                #messages.success(request, 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
            except:
                pass
                #messages.error(request, 'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

    else:
        pass
        #messages.error(request, 'Cabeçalho do arquivo Excel não está correto. Favor verificar!')

@shared_task(bind=True)
def importar_excel_xlsx_new_segundo_celery(self):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'IMPORTANDO NOVOS FLUXOS DE PRODUÇÃO'
    task_result.save()
    transaction.commit()

    # Para permitir acesso ao AWS S3
    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY
    bucket_name = 'spsferbasa'
    object_key = 'fluxoproducao_new.xlsx'

    # Vamos abrir o arquivo Excel que foi gravado no AWS S3
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    content = bucket_object.get()['Body'].read()

    wb = openpyxl.load_workbook(io.BytesIO(content))
    sheet = wb.active

    # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
    total_colunas_mae = sheet.max_column
    total_linhas_mae = sheet.max_row
    lista_colunas = []
    for i in range(1, total_colunas_mae + 1):
        lista_colunas.append(sheet.cell(1, i).value)

    if lista_colunas == ['Descricao Fluxo',
                         'Id Produto',
                         'Nome Produto',
                         'Coluna',
                         'Linha',
                         'Id Consumo Padrao',
                         'Descricao Consumo Padrao']:

        # Cabeçalho do arquivo está correto.
        # Vamos verificar se os dados informados estão ok.
        dados_ok = True
        # Cenário ativo
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

        for i in range(1, total_linhas_mae + 1):
            if i > 1:  # Porque a linha 1 é o cabeçalho
                # Vamos ver se a informação é nova.
                # Nesse caso temos que ver se a descrição do fluxo de produção já existe.
                if TbFluxoProducao.objects.filter(flu_pro_descricao=sheet.cell(i, 1).value, tbcenarios_id=cen_ativo).count() == 0:
                    # Não existe. Podemos contonuar.
                    # Vamos ver se o id do produto informado existe.
                    #print(sheet.cell(i, 2).value)
                    if TbProdutos.objects.filter(id=sheet.cell(i, 2).value).count() > 0:
                        # Existe. Podemos continuar.
                        # Vamos ver se id do consumo padrão informado existe.
                        if TbFluxoConsumoPadrao.objects.filter(id=sheet.cell(i, 6).value).count() > 0:
                            # Existe. Podemos continuar.
                            pass
                        else:
                            dados_ok = False
                            messages.error(request, 'Não existe o consumo padrão com id = ' + str(sheet.cell(i, 6).value) + ' cadastrado. Favor verificar!')
                            break  # Pula fora do for ...
                    else:
                        dados_ok = False
                        messages.error(request, 'Não existe o produto com id = ' + str(sheet.cell(i, 2).value) + ' cadastrado. Favor verificar!')
                        break  # Pula fora do for ...
                else:
                    dados_ok = False
                    messages.error(request, 'Já existe o fluxo de produção = ' + sheet.cell(i, 1).value + '. Favor verificar!')
                    break  # Pula fora do for ...

        if dados_ok:
            # Tudo ok com os dados. Podemos atualizar
            for i in range(1, total_linhas_mae + 1):
                if i > 1:  # Porque a linha 1 é o cabeçalho
                    descricao_fluxo = sheet.cell(i, 1).value[:150]
                    # Vamos ver se existe o fluxo cadastrado. Se não cadastra.
                    if TbFluxoProducao.objects.filter(flu_pro_descricao=descricao_fluxo, tbcenarios_id=cen_ativo).count() == 0:
                        # Vamos cadastrar

                        TbFluxoProducao.objects.create(
                            flu_pro_descricao=sheet.cell(i, 1).value[:150],
                            flu_pro_produto_id=int(sheet.cell(i, 2).value),
                            flu_pro_ativo=True,
                            tbcenarios_id=cen_ativo)

                        # Temos que pegar o id da mãe que foi criado para lançar na filha
                        id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo, tbcenarios_id=cen_ativo).id

                        # Vamos atualizar a filha usando o Stored Procedure verifica_filha_boolean (só tem um campo e é boolean)
                        cursor = connection.cursor()
                        # Montando a expressão sql para rodar o Stored Procedure
                        sql = "call public.verifica_filha_boolean('fluxos_tbfluxoproducaodaugther01', " + str(id_mae) + ", " + str(cen_ativo) + ", true)"
                        cursor.execute(sql)
                        cursor.close()
                    else:
                        # Fluxo de produção foi cadastrado anteriormente. Vamos pegar o id da mãe.
                        id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo, tbcenarios_id=cen_ativo).id

                    # Vamos cadastrar o consumo padrão na tabela de sequenciamento da produção
                    TbFluxoProducaoDaugther.objects.create(
                        flu_pro_dau_coluna=int(sheet.cell(i, 4).value),
                        flu_pro_dau_linha=int(sheet.cell(i, 5).value),
                        flu_pro_dau_consumo_padrao_id=int(sheet.cell(i, 6).value),
                        mae_id=id_mae,
                        tbcenarios_id=cen_ativo)

            # Vamos informar que o arquivo foi atualizado com sucesso e eliminar o mesmo do AWS S3
            # Se chegou até aqui tudo ok. Vamos dar a mensagem de sucesso
            #messages.success(request, 'Novo(s) Fluxo(s) de Produção foi(ram) adicionado(s) com sucesso.')
            # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
            try:
                bucket_object.delete()
                #pass
                #messages.success(request, 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
            except:
                #pass
                messages.error(request, 'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

    else:
        #pass
        messages.error(request, 'Cabeçalho do arquivo Excel no AWS não está correto. Favor verificar!')

@shared_task(bind=True)
def excluir_output_real_zerado_lista(self, lista):
    # * self is a representation from app.Task
    task_result = TaskResult.objects.get_task(self.request.id)
    task_result.status = 'RUNNING'
    task_result.task_name = 'REMOVENDO SEQUÊNCIAS ZERADAS'
    task_result.save()
    transaction.commit()

    cursor = connection.cursor()
    sql = "call public.output_real_zero_fluxo(array" + str(lista) + ")"
    cursor.execute(sql)
    cursor.close()

@shared_task
def update_fluxo_celery(id_fluxo):
    # Create a file-like buffer to receive PDF data.
    buffer = io.BytesIO()

    # Vamos pegar o cenário ativo
    ativo = TbCenarios.objects.get(cen_ativo=True).id

    # Vamos pegar a descrição do fluxo e produto
    descricao_fluxo = TbFluxoProducao.objects.get(id=id_fluxo).flu_pro_descricao
    codigo_produto = TbProdutos.objects.get(id=TbFluxoProducao.objects.get(id=id_fluxo).flu_pro_produto_id).pro_codigo

    # Vamos definir o título do fluxo
    titulo_pdf_1 = 'FLUXO PRODUÇÃO: ' + str(id_fluxo) + ' - ' + descricao_fluxo
    titulo_pdf_2 = 'CENÁRIO: ' + str(ativo) + ' / ' + 'PRODUTO: ' + str(codigo_produto)

    # Vamos definir o nome do arquivo pdf
    nome_arquivo_pdf = 'Fluxo_' + str(id_fluxo) + '_' + str(codigo_produto) + '_Cenario_' + str(ativo) + '.pdf'

    # Vamos ajustar o nome do arquivo
    nome_arquivo_pdf = nome_arquivo_pdf.replace('/', '_')
    nome_arquivo_pdf = nome_arquivo_pdf.replace(' ', '_')

    ''' Não é necessário pois se existir, irá sobescrever. No settings colocamos AWS_S3_FILE_OVERWRITE = True
    # Vamos remover o arquivo antes de gravar a nova versão
    # Para permitir acesso ao AWS S3
    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY
    bucket_name = 'spsferbasa'
    object_key = '/pdf_file/' + nome_arquivo_pdf
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    bucket_object.delete()
    '''

    """
    Temos que dimensionar o tamanho das figuras dos equipamentos de modo que todo o fluxo fique dentro de uma página de tamanho A4.
    Precisamos saber quantas colunas tem o fluxo (total_colunas_fluxo) e o número máximo de linhas (numero_maximo_linhas) em qualquer uma das colunas.
    Vamos então obter essas informações na tabela TbFluxoProducaoInputOutput.
    """

    # Vamos definir e calcular os parâmetros que serão usados no fluxo de produção
    total_equipamentos_fluxo = TbFluxoProducaoInputOutput.objects.filter(mae_id=id_fluxo, flag=1).count()
    if total_equipamentos_fluxo > 0: # sifnifica que tem o fluxo na tabela de input/output
        # Atenção: temos que manter o flag=1 pois esse é um campo de controle. Se o fluxo for deletado, mantemos o registro na base de dados
        # para eventual uso posterior.

        # Vamos criar um queryset a partir da tabela TbFluxoProducaoInputOutput. Nessa tabela já temos os equipamentos que participam do fluxo de produção.
        qs_tbfuxoproducaoinputoutput = TbFluxoProducaoInputOutput.objects.filter(mae_id=id_fluxo, flag=1).order_by('flu_pro_inp_out_coluna', 'flu_pro_inp_out_linha')

        total_colunas_fluxo = 0
        numero_maximo_linhas = 0

        for j in range(total_equipamentos_fluxo):
            if qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_coluna > total_colunas_fluxo:
                total_colunas_fluxo = qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_coluna
            if qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_linha > numero_maximo_linhas:
                numero_maximo_linhas = qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_linha

        # Temos agora o total de colunas do fluxo de produção (total_colunas_fluxo) e o número máximo de linhas em determinada colunas (numero_maximo_linhas)

        altura_pagina = 595.2  # Padrão A4
        largura_pagina = 1500  # Padrão A4
        borda_lateral = 10
        altura_pagina_livre = altura_pagina - 2 * borda_lateral - 60 # Esse valor de 60 é para deixar espaço para colocar os títulos
        largura_pagina_livre = largura_pagina - 2 * borda_lateral

        espaço_imagem_vertical = 0.02586207 * altura_pagina #  Esse é o espaço que irá ficar entre as imagens dos equipamentos na vertical
        espaco_imagem_horizontal = 0.02 * largura_pagina # Esse é o espaço que irá ficar entre as imagens dos equipamentos na horizontal

        largura_imagem = (largura_pagina_livre - espaco_imagem_horizontal * (total_colunas_fluxo - 1)) / total_colunas_fluxo
        #altura_imagem = 1.2 * largura_imagem
        altura_imagem = ((altura_pagina_livre - espaço_imagem_vertical * (numero_maximo_linhas - 1)) / numero_maximo_linhas)
        #largura_imagem = (largura_pagina_livre - espaco_imagem_horizontal * (total_colunas_fluxo - 1)) / total_colunas_fluxo

        # Create the PDF object, using the buffer as its "file.

        p = canvas.Canvas(buffer)

        p.setPageSize((largura_pagina, altura_pagina))                  # Dimensões padrão A4 landscape
        p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

        # Setando tamanho do título e escrevendo os títulos no topo da página
        p.setFont("Helvetica-Bold", 18)

        p.drawCentredString(largura_pagina / 2, altura_pagina - 25, titulo_pdf_1)

        p.setFont("Helvetica-Bold", 12)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 50, titulo_pdf_2)

        # Definindo o tamanho do texto referente ao tipo de produto do equipamento
        # p.setFont("Helvetica-Bold", 80 / total_colunas_fluxo)

        # Vamos agora percorrer as colunas colocando as figuras no arquivo pdf
        # Definindo posição inicial x das figuras
        x_figura = 10

        # Vamos criar uma lista e dentro dessa uma outra lista com a id do equipamento, x/y in e x/y out para colocar linha ligando os equipamentos
        lista_equipamento = []

        for i in range(total_colunas_fluxo):
            # Vamos ver quantos equipamentos nós temos nessa coluna
            total_equipamentos_coluna = TbFluxoProducaoInputOutput.objects.filter(mae_id=id_fluxo, flag=1, flu_pro_inp_out_coluna=i + 1).count()

            # Definindo posição inicial y das figuras
            y_figura = altura_pagina_livre - total_equipamentos_coluna * altura_imagem - (total_equipamentos_coluna - 1) * espaço_imagem_vertical + 12
            # Atenção: observar que dividimos por 0.80 para voltar com a altura da coluna

            # Vamos selecionar os equipamentos dessa coluna montando uma queryset com ordem descendente na linha.
            qs_tbfuxoproducaoinputoutput = TbFluxoProducaoInputOutput.objects.filter(mae_id=id_fluxo, flag=1, flu_pro_inp_out_coluna=i + 1).order_by('-flu_pro_inp_out_linha')

            # Vamos ver o espaço livre na coluna. Objetivo é centralizar as figuras
            espaço_livre_coluna = altura_pagina_livre - total_equipamentos_coluna * altura_imagem - (total_equipamentos_coluna - 1) * espaço_imagem_vertical
            y_figura = (y_figura - espaço_livre_coluna / 2)

            for j in range(total_equipamentos_coluna):
                # Iniciando a lista
                lista_in_out = []

                # Vamos pegar a imagem do equipamento do fluxo de produção
                id_equipamento_tipo_producao = qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_equipamento_id

                # Vamos lançar o equipamento na lista
                lista_in_out.append(id_equipamento_tipo_producao)

                # Vamos lançar a coluna na lista
                lista_in_out.append(i + 1)

                # Vamos lançar a linha na lista
                lista_in_out.append(j + 1)

                id_equipamento = TbEquipamentos.objects.get(id=id_equipamento_tipo_producao).equ_codigo_id
                #print(id_equipamento)

                arquivo_imagem = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_imagem
                codigo_equipamento = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_codigo

                arquivo_imagem = os.path.join(settings.MEDIA_ROOT, arquivo_imagem.name)
                # Comentei a linha abaixo pois parou de funcionar quando coloquei as imagens no local. Incluido a linha acima e funcionou
                #arquivo_imagem = mark_safe('%s' % arquivo_imagem.url)  # Retorna a url do arquivo da imagem no AWS S3 ou do computador local no caso de desenvolvimento. Descobri tentando. Não achei na internet.

                # Temos a imagem. Vamos plotar no pdf
                #tempo_decorrido = datetime.now() - inicio
                #tempo_decorrido = tempo_decorrido.total_seconds()
                #if tempo_decorrido < 25:
                p.drawImage(arquivo_imagem, x_figura, y_figura, width=largura_imagem, height=altura_imagem - 100 / total_colunas_fluxo, mask=None)
                #p.drawInlineImage(arquivo_imagem, x_figura, y_figura, width=largura_imagem, height=altura_imagem)
                #p.drawImage(arquivo_imagem, round(x_figura, 10), round(y_figura, 10), width=round(largura_imagem, 10), height=round(altura_imagem, 10))

                lista_in_out.append(x_figura)
                # y_in
                lista_in_out.append(y_figura + altura_imagem / 2)
                # x_out
                lista_in_out.append(x_figura + largura_imagem)
                # y_out
                lista_in_out.append(y_figura + altura_imagem / 2)

                # Vamos lançar na lista o id do equipamento
                lista_in_out.append(qs_tbfuxoproducaoinputoutput[j].flu_pro_inp_out_envia_para_id)

                # Vamos ver se esse equipamento recebeu de algum outro equipamento

                # Vamos pegar detalhes do equipamento para mostrar acima da figura no fluxo de produção
                equipamento_tipo_producao = str(TbEquipamentos.objects.get(id=id_equipamento_tipo_producao).equ_tipo_producao)

                # Vamos calcular o x_central da figura
                x_central = x_figura + largura_imagem / 2
                #p.drawCentredString(x_central, y_figura + altura_imagem + 5 / total_colunas_fluxo, equipamento_tipo_producao)
                p.drawCentredString(x_central, y_figura + altura_imagem - 20, equipamento_tipo_producao)

                # Vamos incrementar a posição y da figura no pdf
                y_figura = y_figura + altura_imagem + espaço_imagem_vertical

                # Atualizando lista_equipamento
                lista_equipamento.append(lista_in_out)

            # Vamos incrementar a posição x da figura no pdf
            x_figura = x_figura + largura_imagem + espaco_imagem_horizontal

        # Temos que traçar agora as linhas ligando os equipamentos

        # print(lista_equipamento)

        for i in range(
                total_equipamentos_fluxo):  # A quantidade de registros na lista_equipamentos é igual ao total de equipamentps no fluxo
            if lista_equipamento[i][7] is not None:  # Significa que enviou para outro equipamento
                x_in = 0
                y_in = 0
                enviou_para = lista_equipamento[i][7]
                coluna = lista_equipamento[i][1] + 1

                # Esse equipamento deverá estar na próxima coluna. Vamos localizar e pegar as coordenadas x_in e y_in
                for j in range(total_equipamentos_fluxo):
                    if lista_equipamento[j][0] == enviou_para and lista_equipamento[j][1] == coluna:  # Achou. Vamos pegar as coordenadas x_in e y_in
                        x_in = lista_equipamento[j][3]
                        y_in = lista_equipamento[j][4]

                        break

                if x_in != 0 and y_in != 0:  # Significa que achou equipamento. Vamos traçar a linha
                    p.line(lista_equipamento[i][5], lista_equipamento[i][6], x_in, y_in)
    else: # Não tem o fluxo na tabela de input/output. Tem que criar
        altura_pagina = 595.2  # Padrão A4
        largura_pagina = 1500  # Padrão A4
        borda_lateral = 10

        # Create the PDF object, using the buffer as its "file.

        p = canvas.Canvas(buffer)

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4 landscape
        p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

        # Setando tamanho do título e escrevendo os títulos no topo da página
        p.setFont("Helvetica-Bold", 18)

        p.drawCentredString(largura_pagina / 2, altura_pagina - 25, titulo_pdf_1)

        p.setFont("Helvetica-Bold", 12)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 50, titulo_pdf_2)

        p.setFont("Helvetica-Bold", 25)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 100, 'NÃO FOI GERADO INPUT/OUT PARA ESSE FLUXO. USAR AÇÃO Atualizar Input/Output dos Fluxos Selecionados')


    # Fechando o objeto PDF, e está feito.
    p.showPage()
    p.save()

    pdf = buffer.getvalue()
    buffer.close()

    my_object = TbFluxoProducao.objects.get(id=id_fluxo)
    my_object.flu_pro_pdf_file.save(nome_arquivo_pdf, ContentFile(pdf))

    return


@shared_task
def exportar_excel_fluxo_producao_celery(lista_id, user_mail):
    nome_arquivo = 'Fluxo de Producao' + '.xlsx'

    import openpyxl
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "fluxo"

    # First row
    row_num = 1

    columns = ['id',
               'flu_pro_descricao',
               'flu_pro_produto_id',
               'flu_pro_produto_nome',
               'custo_var_medio',
               'flu_pro_ativo',
               'flu_pro_copiar_de',
               'flu_pro_observacao',
               'flu_pro_erro',
               'id_origem',
               'tbcenarios_id']

    for col_num in range(1, len(columns) + 1):
        ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

    rows = TbFluxoProducao.objects.filter(id__in=lista_id).values_list('id',
                                'flu_pro_descricao',
                                'flu_pro_produto_id',
                                'flu_pro_ativo',
                                'flu_pro_copiar_de',
                                'flu_pro_observacao',
                                'flu_pro_erro',
                                'id_origem',
                                'tbcenarios_id').order_by('id')

    # Vamos incluir o nome do produto e o custo variável médio na lista new_rows
    new_rows = []
    linha_num = 1
    for row in rows:
        col_id_produto = 2
        # Vamos pegar o nome do produto
        nome_produto = TbProdutos.objects.get(id=row[col_id_produto]).pro_codigo

        # Inserindo na lista new_rows
        list_aux = list(rows[linha_num-1])
        list_aux.insert(3, nome_produto)

        # Vamos calcular o custo variável médio do fluxo de produção
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o custo variável médio do fluxo de produção
        # Primeiro pegando o id do fluxo de produção

        id_fluxo_producao = row[0]
        # Expressão SQL
        sql = "call public.atualiza_custo_var_med_fluxo_producao(" + str(id_fluxo_producao) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Inserindo na lista new_rows
        list_aux.insert(4, retorno)
        new_rows.append(list_aux)

        linha_num += 1

    rows = new_rows

    for row in rows:
        row_num += 1
        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

    # Vamos criar a aba 'sequência' e torna-la ativa
    wb.create_sheet('sequência')
    wb.active = wb['sequência']
    ws = wb.active

    # Vamos montar uma queryset com os valores da sequencia de produção
    sequencia = TbFluxoProducaoDaugther.objects.filter(mae_id__in=lista_id).order_by('mae_id', 'flu_pro_dau_coluna', 'flu_pro_dau_linha')

    # First row
    row_num = 1

    columns = ['id',
               'flu_pro_dau_coluna',
               'flu_pro_dau_linha',
               'flu_pro_dau_consumo_padrao_id',
               'flu_pro_dau_consumo_padrao_descricao',
               'mae_id',
               'tbcenarios_id']

    for col_num in range(1, len(columns) + 1):
        ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

    rows = sequencia.values_list('id',
                                 'flu_pro_dau_coluna',
                                 'flu_pro_dau_linha',
                                 'flu_pro_dau_consumo_padrao_id',
                                 'mae_id',
                                 'tbcenarios_id').order_by('mae_id', 'flu_pro_dau_coluna', 'flu_pro_dau_linha')

    # Vamos incluir a descrição do consumo padrão na lista new_rows
    new_rows = []
    linha_num = 1
    for row in rows:
        col_id_consumo_padrao = 3
        # Vamos pegar a descrição do consumo padrão
        descricao_consumo_padrao = TbFluxoConsumoPadrao.objects.get(
            id=row[col_id_consumo_padrao]).flu_con_pad_descricao

        # Inserindo na lista new_rows
        list_aux = list(rows[linha_num-1])
        list_aux.insert(4, descricao_consumo_padrao)
        new_rows.append(list_aux)

        linha_num += 1

    rows = new_rows

    for row in rows:
        row_num += 1
        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

    # Vamos criar a aba 'filhas' e torna-la ativa
    wb.create_sheet('filhas')
    wb.active = wb['filhas']
    ws = wb.active

    filhas = TbFluxoProducaoDaugther01.objects.filter(mae_id__in=lista_id).order_by('mae_id', 'dau_order')

    # First row
    row_num = 1
    columns = ['id',
               'dau_order',
               'dau_valor',
               'mae_id',
               'tbcenarios_id']

    for col_num in range(1, len(columns) + 1):
        ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

    rows = filhas.values_list('id', 'dau_order', 'dau_valor', 'mae_id', 'tbcenarios_id').order_by('mae_id', 'dau_order')
    for row in rows:
        row_num += 1
        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

    wb.active = wb['fluxo']

    # Arquivo foi gerado. Vamos enviar para o usuário via email
    lista_e_mail = []
    lista_e_mail.append(user_mail)

    mail = EmailMessage(subject="Arquivo Excel Sistema SPS",
                        from_email='sps.consultoria.alerta@gmail.com',
                        to=lista_e_mail,
                        body="Arquivo solicitado foi gerado com sucesso. Relatório Fluxo(s) de Produção.")

    with io.BytesIO() as xls:
        wb.save(xls)
        mail.attach(filename=nome_arquivo,
                    content=xls.getvalue(),
                    mimetype="application/ms-excel")

    mail.send(fail_silently=False)


@shared_task
def remover_fluxo(lista):
    cursor = connection.cursor()
    sql = "call public.remover_fluxo(array" + str(lista) + ")"
    cursor.execute(sql)
    cursor.close()