from celery import shared_task
import io
from django.http import FileResponse, HttpResponse
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from datetime import date
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.lib import colors
from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from .models import *
from django.db import connection
import boto3
import openpyxl  # Para ler Excel xlsx
from boto3 import Session
import math

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


@shared_task
def atualizar_genealogia_celery():
    cursor = connection.cursor()
    sql = "call public.atualizar_genealogia()"
    cursor.execute(sql)
    cursor.close()


@shared_task
def update_indicador_consumos_padroes(id_consumo_especifico):
    cursor = connection.cursor()
    sql = "call public.update_indicador_consumos_padroes(" + str(id_consumo_especifico) + ")"
    cursor.execute(sql)
    cursor.close()


@shared_task
def calcula_consumo_especifico(id_consumo_especifico):
    # print(id_consumo_especifico)
    cursor = connection.cursor()
    sql = "call public.consumo_especifico(" + str(id_consumo_especifico) + ")"
    cursor.execute(sql)
    cursor.close()


@shared_task
def calcula_custo_variavel_adicionado(id_custo_variavel_adicionado):
    cursor = connection.cursor()
    sql = "call public.custo_variavel_adicionado(" + str(id_custo_variavel_adicionado) + ")"
    cursor.execute(sql)
    cursor.close()


@shared_task
def calcula_regressao_linear_multipla(id_regressao_linear_multipla):
    pass
    '''
    cursor = connection.cursor()
    sql = "call public.consumo_especifico(" + str(id_consumo_especifico) + ")"
    cursor.execute(sql)
    cursor.close()
    '''


@shared_task
def importar_excel_producao_mensal_celery():
    # Nao usamos o try pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)
    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY

    response = HttpResponse(content_type='application/ms-excel')
    nome_arquivo = 'Produção Mensal.xlsx'  # Arquivo tem que estar no AWS no bucket spsferbasa
    bucket_name = 'spsferbasa'
    object_key = nome_arquivo

    # Vamos abrir o arquivo Excel que foi gravado no AWS S3
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    content = bucket_object.get()['Body'].read()

    wb = openpyxl.load_workbook(io.BytesIO(content))
    sheet = wb.active

    # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
    total_colunas = sheet.max_column
    total_linhas = sheet.max_row
    lista_colunas = []
    for i in range(1, total_colunas + 1):
        # o nome das colunas está na linha 1
        lista_colunas.append(sheet.cell(row=1, column=i).value)
    if lista_colunas == ['PERIODO', 'ESTABELECIMENTO', 'DESCRIÇÃO ESTABELECIMENTO', 'ORDEM PRODUÇÃO', 'GRUPO MÁQUINA',
                         'DESCRIÇÃO GRUPO MÁQUINA', 'ITEM PRODUÇÃO', 'DESCRIÇÃO ITEM PRODUÇÃO', 'UNIDADE',
                         'QTDADE PRODUÇÃO', 'ITEM CONSUMO', 'DESCRIÇÃO ITEM CONSUMO', 'UNIDADE', 'QTDADE CONSUMO',
                         'INDICADOR', 'VALOR MATERIAL', 'VALOR GGF', 'VALOR GGF FIXO', 'VALOR GGF VARIAVEL',
                         'VALOR GGF OUTROS', 'VALOR ULT ENTRADA', 'TIPO', 'SEQUENCIA', 'NIVEL']:
        #  Estamos usando o cabeçalho da planilha gerada pela área de TI da Ferbasa para não dar confusão. O arquivo vem em CSV. Temos que abrir no Excel e salvar com o nome
        #  Produção_Mensal.xlsx
        #  Cabeçalho do arquivo está correto.

        for i in range(total_linhas + 1):
            # print('Linha = ' + str(i))

            if i > 1 and sheet.cell(row=i, column=1).value != None:  # Temos informação na linha
                # print(sheet.cell(row=i, column=1).value)
                if sheet.cell(row=i, column=10).value > 0 and sheet.cell(row=i,
                                                                         column=11) != None:  # Porque a linha 1 é o cabeçalho

                    # Só vamos considerar se qtde_produzida for maior que zero e foi informado item de consumo
                    # Ano/mês está vindo como número sem a barra. Vamos ajustar
                    v_ano_mes = str(sheet.cell(row=i, column=1).value)[0:6]
                    v_ano_mes = v_ano_mes[0:4] + '/' + v_ano_mes[4:6]
                    v_codigo_estabelecimento = sheet.cell(row=i, column=2).value.upper()
                    # Se no campo codigo grupo máquina estiver vazio, vamos assumir 'VAZIO'
                    if sheet.cell(row=i, column=5).value is None:
                        v_codigo_grupo_maquina = 'VAZIO'
                    else:
                        v_codigo_grupo_maquina = sheet.cell(row=i, column=5).value.upper()
                    # Ordem de produção está vindo como numérico. Passamos para string.
                    v_ordem_producao = str(sheet.cell(row=i, column=4).value).replace('.', '').upper()[0:9]
                    # Item de produção está vindo como numérico. Passamos para string.
                    v_codigo_item_producao = str(sheet.cell(row=i, column=7).value).replace('.', '').upper()
                    # Item de consumo está vindo como numérico. Passamos para string.
                    v_codigo_item_consumo = str(sheet.cell(row=i, column=11).value).replace('.', '').upper()
                    if sheet.cell(row=i, column=12).value is None:
                        v_descricao_item_consumo = ''
                        v_unidade_item_consumo = ''
                        v_tipo_item_consumo = ''
                    else:
                        v_descricao_item_consumo = sheet.cell(row=i, column=12).value[0:60].upper()
                        v_unidade_item_consumo = sheet.cell(row=i, column=13).value.upper()
                        v_tipo_item_consumo = sheet.cell(row=i, column=22).value.upper()

                        # Verificando estabelecimento
                    if TbEstabelecimentos.objects.filter(est_codigo=v_codigo_estabelecimento).count() > 0:  # Existe
                        v_id_estabelecimento = TbEstabelecimentos.objects.get(est_codigo=v_codigo_estabelecimento).id
                        # Vamos atualizar o novo nome se for diferente
                        objeto = TbEstabelecimentos.objects.get(est_codigo=v_codigo_estabelecimento)
                        if objeto.est_nome != sheet.cell(row=i, column=3).value.upper():
                            objeto.est_nome = sheet.cell(row=i, column=3).value.upper()
                            objeto.save()
                    else:
                        # Não existe. Vamos criar e pegar o id
                        n = TbEstabelecimentos.objects.create(est_codigo=v_codigo_estabelecimento.upper(),
                                                              est_nome=sheet.cell(row=i, column=3).value.upper())
                        n.save()
                        v_id_estabelecimento = n.id

                    # Verificando grupo máquina
                    if TbGruposMaquinas.objects.filter(gru_maq_codigo=v_codigo_grupo_maquina).count() > 0:  # Existe
                        v_id_grupo_maquina = TbGruposMaquinas.objects.get(gru_maq_codigo=v_codigo_grupo_maquina).id
                        # Vamos atualizar o novo nome se for diferente
                        objeto = TbGruposMaquinas.objects.get(gru_maq_codigo=v_codigo_grupo_maquina)
                        if objeto.gru_maq_nome != sheet.cell(row=i, column=6).value.upper():
                            objeto.gru_maq_nome = sheet.cell(row=i, column=6).value.upper()
                            objeto.save()
                    else:
                        # Não existe. Vamos criar e pegar o id
                        n = TbGruposMaquinas.objects.create(gru_maq_codigo=v_codigo_grupo_maquina.upper(),
                                                            gru_maq_nome=sheet.cell(row=i, column=6).value.upper()
                                                            )
                        n.save()
                        v_id_grupo_maquina = n.id

                    # Verificando item de produção
                    if TbItensProducao.objects.filter(ite_pro_codigo=v_codigo_item_producao).count() > 0:  # Existe
                        v_id_item_producao = TbItensProducao.objects.get(ite_pro_codigo=v_codigo_item_producao).id
                        # Vamos atualizar a nova descrição se for diferente
                        objeto = TbItensProducao.objects.get(ite_pro_codigo=v_codigo_item_producao)
                        if objeto.ite_pro_descricao != sheet.cell(row=i, column=8).value[0:60].upper():
                            objeto.ite_pro_descricao = sheet.cell(row=i, column=8).value[0:60].upper()
                            objeto.save()
                        if objeto.ite_pro_unidade != sheet.cell(row=i, column=9).value.upper():
                            objeto.ite_pro_unidade = sheet.cell(row=i, column=9).value.upper()
                            objeto.save()
                    else:
                        # Não existe. Vamos criar e pegar o id
                        n = TbItensProducao.objects.create(ite_pro_codigo=v_codigo_item_producao.upper(),
                                                           ite_pro_descricao=sheet.cell(row=i, column=8).value[
                                                               0:60].upper(),
                                                           ite_pro_unidade=sheet.cell(row=i, column=9).value.upper())
                        n.save()
                        v_id_item_producao = n.id

                # Verificando item de consumo
                if TbItensConsumo.objects.filter(ite_con_codigo=v_codigo_item_consumo).count() > 0:  # Existe
                    v_id_item_consumo = TbItensConsumo.objects.get(ite_con_codigo=v_codigo_item_consumo).id
                    # Vamos atualizar a nova descrição se for diferente
                    objeto = TbItensConsumo.objects.get(ite_con_codigo=v_codigo_item_consumo)
                    if objeto.ite_con_descricao != v_descricao_item_consumo:
                        objeto.ite_con_descricao = v_descricao_item_consumo
                        objeto.save()
                    if objeto.ite_con_unidade != v_unidade_item_consumo:
                        objeto.ite_con_unidade = v_unidade_item_consumo
                        objeto.save()
                    if objeto.ite_con_tipo != v_tipo_item_consumo:
                        objeto.ite_con_tipo = v_tipo_item_consumo
                        objeto.save()
                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbItensConsumo.objects.create(ite_con_codigo=v_codigo_item_consumo.upper(),
                                                      ite_con_descricao=v_descricao_item_consumo,
                                                      ite_con_unidade=v_unidade_item_consumo,
                                                      ite_con_tipo=v_tipo_item_consumo)
                    n.save()
                    v_id_item_consumo = n.id

                # Vamos ver se já existe na tabela de produção mensal
                if TbProducaoMensal.objects.filter(pro_men_ano_mes=v_ano_mes,
                                                   pro_men_estabelecimento_id=v_id_estabelecimento,
                                                   pro_men_grupo_maquina_id=v_id_grupo_maquina,
                                                   pro_men_ordem_producao=v_ordem_producao,
                                                   pro_men_item_producao=v_id_item_producao,
                                                   pro_men_item_consumo=v_id_item_consumo).count() == 0:  # Não existe. Vamos criar o registro

                    # Vamos veriricar se campo pro_men_valor_material é caracter. Se sim passamos para numérico
                    # Isso é para evitar erro pois algumas vezes esse campo na planilha está vindo como caracter
                    if type(sheet.cell(row=i, column=16).value) is str:
                        valor_material = sheet.cell(row=i, column=16).value.replace(',', '.')
                    else:
                        valor_material = sheet.cell(row=i, column=16).value

                    TbProducaoMensal.objects.create(
                        pro_men_ano_mes=v_ano_mes,
                        pro_men_estabelecimento_id=v_id_estabelecimento,
                        pro_men_grupo_maquina_id=v_id_grupo_maquina,
                        pro_men_ordem_producao=v_ordem_producao,
                        pro_men_item_producao_id=v_id_item_producao,
                        pro_men_qtde_produzida=sheet.cell(row=i, column=10).value,
                        pro_men_item_consumo_id=v_id_item_consumo,
                        pro_men_qtde_consumo=sheet.cell(row=i, column=14).value,
                        pro_men_valor_material=valor_material,
                        pro_men_valor_ggf=sheet.cell(row=i, column=17).value,
                        pro_men_valor_ultima_entrada=sheet.cell(row=i, column=21).value
                    )
                else:  # Já existe. Vamos atualizar os novos valores
                    objeto = TbProducaoMensal.objects.get(pro_men_ano_mes=v_ano_mes,
                                                          pro_men_estabelecimento_id=v_id_estabelecimento,
                                                          pro_men_grupo_maquina_id=v_id_grupo_maquina,
                                                          pro_men_ordem_producao=v_ordem_producao,
                                                          pro_men_item_producao=v_id_item_producao,
                                                          pro_men_item_consumo=v_id_item_consumo)  # Pegamos todo o registro
                    objeto.pro_men_qtde_produzida = sheet.cell(row=i, column=10).value
                    objeto.pro_men_qtde_consumo = sheet.cell(row=i, column=14).value
                    objeto.pro_men_valor_material = sheet.cell(row=i, column=16).value
                    objeto.pro_men_valor_ggf = sheet.cell(row=i, column=17).value
                    objeto.pro_men_valor_ultima_entrada = sheet.cell(row=i, column=21).value
                    objeto.save()

        # Comentamos pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)
        # messages.success(self, 'Tabela Produção Mensal foi atualizada com sucesso!')

        # Deletando arquivo no AWS S3
        s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
        s3_client.delete_object(Bucket=bucket_name, Key=object_key)

    else:
        pass
        # Comentamos pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)
        # messages.error(request, 'Cabeçalho do arquivo' + nome_arquivo + ' não está correto. Favor verificar!')


@shared_task
def importar_excel_distribuicao_ggf_mensal_celery():
    # Nao usamos o try pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)
    aws_id = AWS_ACCESS_KEY_ID
    aws_secret = AWS_SECRET_ACCESS_KEY

    response = HttpResponse(content_type='application/ms-excel')
    nome_arquivo = 'Distribuição GGF Mensal.xlsx'  # Arquivo tem que estar no AWS no bucket spsferbasa
    bucket_name = 'spsferbasa'
    object_key = nome_arquivo

    # Vamos abrir o arquivo Excel que foi gravado no AWS S3
    s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
    bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
    content = bucket_object.get()['Body'].read()

    wb = openpyxl.load_workbook(io.BytesIO(content))
    sheet = wb.active

    # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
    total_colunas = sheet.max_column
    total_linhas = sheet.max_row
    lista_colunas = []
    for i in range(1, total_colunas + 1):
        # o nome das colunas está na linha 1
        lista_colunas.append(sheet.cell(row=1, column=i).value)

    if lista_colunas == ['PERIODO', 'ESTABELECIMENTO', 'DESCRIÇÃO ESTABELECIMENTO', 'ORDEM PRODUÇÃO', 'GRUPO MÁQUINA',
                         'DESCRIÇÃO GRUPO MÁQUINA', 'ITEM PRODUÇÃO', 'DESCRIÇÃO ITEM PRODUÇÃO', 'UNIDADE',
                         'QTDADE PRODUÇÃO', 'ESPECIE GGF', 'CONTA', 'DESCRIÇÃO CONTA CONTÁBIL', 'CENTRO CUSTO',
                         'DESCRIÇÃO CENTRO DE CUSTO', 'VALOR GASTOS']:

        #  Estamos usando o cabeçalho da planilha gerada pela área de TI da Ferbasa para não dar confusão. O arquivo vem em CSV. Temos que abrir no Excel e salvar com o nome
        #  Distribuição GGF Mensal.xlsx
        #  ATENÇÃO: TEM QUE SER NO FORMATO .xlsx. NÃO PODE SER .xls
        #  Cabeçalho do arquivo está correto.

        for i in range(1, total_linhas + 1):
            if i > 1 and sheet.cell(row=i,
                                    column=1).value != None:  # Porque a linha 1 é o cabeçalho e temos informação na linha
                print('Linha = ' + str(i))
                # Ano/mês está vindo como número sem a barra. Vamos ajustar
                v_ano_mes = str(sheet.cell(row=i, column=1).value)[0:6]
                v_ano_mes = v_ano_mes[0:4] + '/' + v_ano_mes[4:6]
                v_codigo_estabelecimento = sheet.cell(row=i, column=2).value.upper()
                if sheet.cell(row=i, column=5).value is None:
                    v_codigo_grupo_maquina = 'VAZIO'
                else:
                    v_codigo_grupo_maquina = sheet.cell(row=i, column=5).value.upper()
                # Ordem de produção está vindo como numérico. Passamos para string.
                v_ordem_producao = str(sheet.cell(row=i, column=4).value).replace('.', '').upper()[0:9]
                # Item de produção está vindo como numérico. Passamos para string.
                v_codigo_item_producao = str(sheet.cell(row=i, column=7).value).replace('.', '').upper()
                # Conta está vindo como numérico. Passamos para string.
                v_codigo_conta = str(sheet.cell(row=i, column=12).value).replace('.', '')[0:6].upper()
                v_descricao_conta = str(sheet.cell(row=i, column=13).value).upper()
                # Centro de Custo está vindo como numérico. Passamos para string.
                v_codigo_cc = str(sheet.cell(row=i, column=14).value).replace('.', '')[0:6].upper()
                v_descricao_cc = str(sheet.cell(row=i, column=15).value).upper()

                # Verificando estabelecimento
                if TbEstabelecimentos.objects.filter(est_codigo=v_codigo_estabelecimento).count() > 0:  # Existe
                    v_id_estabelecimento = TbEstabelecimentos.objects.get(est_codigo=v_codigo_estabelecimento).id
                    # Vamos atualizar o novo nome se for diferente
                    objeto = TbEstabelecimentos.objects.get(est_codigo=v_codigo_estabelecimento)
                    if objeto.est_nome != sheet.cell(row=i, column=3).value.upper():
                        objeto.est_nome = sheet.cell(row=i, column=3).value.upper()
                        objeto.save()
                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbEstabelecimentos.objects.create(est_codigo=v_codigo_estabelecimento.upper(),
                                                          est_nome=sheet.cell(row=i, column=3).value.upper())
                    n.save()
                    v_id_estabelecimento = n.id

                # Verificando grupo máquina
                if TbGruposMaquinas.objects.filter(gru_maq_codigo=v_codigo_grupo_maquina).count() > 0:  # Existe
                    v_id_grupo_maquina = TbGruposMaquinas.objects.get(gru_maq_codigo=v_codigo_grupo_maquina).id
                    # Vamos atualizar o novo nome se for diferente
                    objeto = TbGruposMaquinas.objects.get(gru_maq_codigo=v_codigo_grupo_maquina)
                    if objeto.gru_maq_nome != sheet.cell(row=i, column=6).value.upper():
                        objeto.gru_maq_nome = sheet.cell(row=i, column=6).value.upper()
                        objeto.save()
                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbGruposMaquinas.objects.create(gru_maq_codigo=v_codigo_grupo_maquina.upper(),
                                                        gru_maq_nome=sheet.cell(row=i, column=6).value.upper()
                                                        )
                    n.save()
                    v_id_grupo_maquina = n.id

                # Verificando item de produção
                if TbItensProducao.objects.filter(ite_pro_codigo=v_codigo_item_producao).count() > 0:  # Existe
                    v_id_item_producao = TbItensProducao.objects.get(ite_pro_codigo=v_codigo_item_producao).id
                    # Vamos atualizar a nova descrição se for diferente
                    objeto = TbItensProducao.objects.get(ite_pro_codigo=v_codigo_item_producao)
                    if objeto.ite_pro_descricao != sheet.cell(row=i, column=8).value[0:60].upper():
                        objeto.ite_pro_descricao = sheet.cell(row=i, column=8).value[0:60].upper()
                        objeto.save()
                    if objeto.ite_pro_unidade != sheet.cell(row=i, column=9).value.upper():
                        objeto.ite_pro_unidade = sheet.cell(row=i, column=9).value.upper()
                        objeto.save()
                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbItensProducao.objects.create(ite_pro_codigo=v_codigo_item_producao.upper(),
                                                       ite_pro_descricao=sheet.cell(row=i, column=8).value[
                                                           0:60].upper(),
                                                       ite_pro_unidade=sheet.cell(row=i, column=9).value.upper())
                    n.save()
                    v_id_item_producao = n.id

                '''
                # Código anterior para o layout antigo 
                # Verificando conta contábil
                # Vamos pegar a descrição da conta e a descrição do centro de custo
                # A conta está vindo em uppercase e o centro de custo em lowercase
                index_lowercase = 0
                for j, a in enumerate(sheet.cell(row=i, column=14).value):
                    if a.islower():
                        index_lowercase = j - 1
                        break

                # Logo:
                descricao_conta = sheet.cell(row=i, column=14).value[0:index_lowercase - 2].upper()[0:60]
                descricao_cc = sheet.cell(row=i, column=14).value[index_lowercase:].upper()[0:60]
                '''

                if TbContaContabil.objects.filter(con_con_codigo=v_codigo_conta).count() > 0:  # Existe
                    v_id_conta = TbContaContabil.objects.get(con_con_codigo=v_codigo_conta).id

                    objeto = TbContaContabil.objects.get(con_con_codigo=v_codigo_conta)
                    if objeto.con_con_descricao != v_descricao_conta:
                        objeto.con_con_descricao = v_descricao_conta
                        objeto.save()

                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbContaContabil.objects.create(con_con_codigo=v_codigo_conta.upper(),
                                                       con_con_descricao=v_descricao_conta
                                                       )
                    n.save()
                    v_id_conta = n.id

                # Verificando centro de custo
                # Referencia '2' significa centro de custos a partir de 2026 inclusive
                if TbCentroCusto.objects.filter(cen_cus_codigo=v_codigo_cc,
                                                cen_cus_referencia='2').count() > 0:  # Existe
                    v_id_cc = TbCentroCusto.objects.get(cen_cus_codigo=v_codigo_cc, cen_cus_referencia='2').id

                    objeto = TbCentroCusto.objects.get(cen_cus_codigo=v_codigo_cc, cen_cus_referencia='2')
                    if objeto.cen_cus_descricao != v_descricao_cc:
                        objeto.cen_cus_descricao = v_descricao_cc
                        objeto.save()

                else:
                    # Não existe. Vamos criar e pegar o id
                    n = TbCentroCusto.objects.create(cen_cus_codigo=v_codigo_cc.upper(),
                                                     cen_cus_descricao=v_descricao_cc,
                                                     cen_cus_referencia='2'
                                                     )
                    n.save()
                    v_id_cc = n.id

                # Verificando conta contábil, centro de custo e estabelecimento
                if TbContaContabilCentroCusto.objects.filter(con_con_cen_cus_conta_id=v_id_conta,
                                                             con_con_cen_cus_cc_id=v_id_cc,
                                                             con_con_cen_cus_estabelecimento_id=v_id_estabelecimento).count() > 0:  # Existe

                    v_id_conta_cc = TbContaContabilCentroCusto.objects.get(con_con_cen_cus_conta_id=v_id_conta,
                                                                           con_con_cen_cus_cc_id=v_id_cc,
                                                                           con_con_cen_cus_estabelecimento_id=v_id_estabelecimento).id

                    '''
                    # Vamos ver se a descrição é diferente da cadastrada
                    objeto = TbContaContabilCentroCusto.objects.get(con_con_cen_cus_conta_id=v_id_conta, con_con_cen_cus_cc_id=v_id_cc, con_con_cen_cus_estabelecimento_id=v_id_estabelecimento)
                    if objeto.con_con_cen_cus_descricao != sheet.cell(row=i, column=14).value[0:80].upper():
                        objeto.con_con_cen_cus_descricao = sheet.cell(row=i, column=14).value[0:80].upper()
                        objeto.save()
                    '''
                else:  # Não existe. Vamos criar e pegar o id
                    n = TbContaContabilCentroCusto.objects.create(con_con_cen_cus_conta_id=v_id_conta,
                                                                  con_con_cen_cus_cc_id=v_id_cc,
                                                                  con_con_cen_cus_estabelecimento_id=v_id_estabelecimento,
                                                                  con_con_cen_cus_tipo='A'
                                                                  )
                    n.save()
                    v_id_conta_cc = n.id

                # Vamos ver se já existe na tabela de distribuição GGF mensal
                if TbDistribuicaoGGFMensal.objects.filter(dis_ggf_men_ano_mes=v_ano_mes,
                                                          dis_ggf_men_estabelecimento_id=v_id_estabelecimento,
                                                          dis_ggf_men_grupo_maquina_id=v_id_grupo_maquina,
                                                          dis_ggf_men_ordem_producao=v_ordem_producao,
                                                          dis_ggf_men_item_producao=v_id_item_producao,
                                                          dis_ggf_men_conta_cc=v_id_conta_cc).count() == 0:  # Não existe. Vamos criar o registro
                    print('Não existe')
                    TbDistribuicaoGGFMensal.objects.create(dis_ggf_men_ano_mes=v_ano_mes,
                                                           dis_ggf_men_estabelecimento_id=v_id_estabelecimento,
                                                           dis_ggf_men_grupo_maquina_id=v_id_grupo_maquina,
                                                           dis_ggf_men_ordem_producao=v_ordem_producao,
                                                           dis_ggf_men_item_producao_id=v_id_item_producao,
                                                           dis_ggf_men_qtde_produzida=sheet.cell(row=i,
                                                                                                 column=10).value,
                                                           dis_ggf_men_conta_cc_id=v_id_conta_cc,
                                                           dis_ggf_men_valor=sheet.cell(row=i, column=16).value
                                                           )
                else:  # Já existe. Vamos atualizar os novos valores
                    print('Existe')
                    objeto = TbDistribuicaoGGFMensal.objects.get(dis_ggf_men_ano_mes=v_ano_mes,
                                                                 dis_ggf_men_estabelecimento_id=v_id_estabelecimento,
                                                                 dis_ggf_men_grupo_maquina_id=v_id_grupo_maquina,
                                                                 dis_ggf_men_ordem_producao=v_ordem_producao,
                                                                 dis_ggf_men_item_producao=v_id_item_producao,
                                                                 dis_ggf_men_conta_cc=v_id_conta_cc)  # Pegamos todo o registro
                    objeto.dis_ggf_men_qtde_produzida = sheet.cell(row=i, column=10).value
                    objeto.dis_ggf_men_valor = sheet.cell(row=i, column=16).value
                    objeto.save()

        # Deletando arquivo no AWS S3
        s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
        s3_client.delete_object(Bucket=bucket_name, Key=object_key)


    else:
        pass
        # Comentamos pois estamos executando em segundo plano e não temos como retornar mensagens (pelo menos até sabermos como fazer isso ...)
        # messages.error(request, 'Cabeçalho do arquivo' + nome_arquivo + ' não está correto. Favor verificar!')


# =======================================================================
# 🌟 NOVO: importação de Produção Mensal / Distribuição GGF Mensal a
# partir de um arquivo ENVIADO pelo usuário via chat do Agente IA (tela
# de Relatórios), em vez de um arquivo fixo gravado na AWS. Diferente do
# fluxo acima:
#   - o nome do arquivo pode ser QUALQUER UM (o de cima exige o nome
#     exato "Produção Mensal.xlsx" / "Distribuição GGF Mensal.xlsx");
#   - aceita tanto Excel (.xlsx/.xls) quanto CSV (.csv), com detecção
#     automática de separador e de tipo de cada valor;
#   - dá retorno pro usuário (quantas linhas foram criadas/atualizadas,
#     ou o motivo do erro), gravado em EstadoConversaAgente.dados_coletados
#     pra a tela de chat conferir e mostrar.
# A REGRA DE NEGÓCIO em si (validação de cabeçalho, upsert de
# Estabelecimento/Grupo Máquina/Item Produção/Item Consumo/Conta
# Contábil/Centro de Custo, e por fim de Produção Mensal / Distribuição
# GGF Mensal) é EXATAMENTE A MESMA das duas funções acima -- só a
# ORIGEM do arquivo e o formato aceito mudam. As funções acima (que lêem
# da AWS) foram deixadas intactas, sem nenhuma alteração.
# =======================================================================
import csv as _csv
import os as _os
from parameters.models import EstadoConversaAgente, RelatorioPDF


class _CelulaFake:
    """Imita a interface `cell.value` do openpyxl, pra dado vindo de CSV."""
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


class _PlanilhaUnificada:
    """
    Abstrai o acesso a células (linha/coluna, 1-based, igual ao
    openpyxl) por trás de uma lista de linhas já carregada -- permite
    reaproveitar a MESMA lógica de processamento de linha tanto pra
    planilhas Excel quanto pra CSV, sem duplicar regra de negócio.
    """

    def __init__(self, linhas):
        self._linhas = linhas

    @property
    def max_row(self):
        return len(self._linhas)

    @property
    def max_column(self):
        return max((len(l) for l in self._linhas), default=0)

    def cell(self, row, column):
        linha = self._linhas[row - 1] if 0 <= row - 1 < len(self._linhas) else []
        valor = linha[column - 1] if 0 <= column - 1 < len(linha) else None
        return _CelulaFake(valor)


def _normalizar_valor_csv(texto):
    """
    CSV não tem tipos -- tudo vem como string. Tenta inferir o tipo mais
    provável (int, depois float aceitando vírgula OU ponto decimal),
    pra bater com o que o openpyxl já entregaria naturalmente pra uma
    planilha Excel equivalente (onde número já vem como int/float
    direto, não como texto).
    """
    if texto is None:
        return None
    texto = texto.strip()
    if texto == '':
        return None
    try:
        return int(texto)
    except ValueError:
        pass
    try:
        return float(texto.replace(',', '.'))
    except ValueError:
        pass
    return texto


def _carregar_planilha_de_arquivo(caminho_arquivo):
    """
    Lê um arquivo Excel (.xlsx/.xls) OU CSV (.csv) -- qualquer nome de
    arquivo, decide o formato pela EXTENSÃO -- e devolve uma
    _PlanilhaUnificada com a mesma interface de acesso por célula que o
    resto do código já usa (sheet.cell(row=.., column=..).value).
    """
    extensao = caminho_arquivo.rsplit('.', 1)[-1].lower() if '.' in caminho_arquivo else ''

    if extensao == 'csv':
        conteudo_bruto = None
        for codificacao in ('utf-8-sig', 'latin-1'):
            try:
                with open(caminho_arquivo, encoding=codificacao) as f:
                    conteudo_bruto = f.read()
                break
            except UnicodeDecodeError:
                continue
        if conteudo_bruto is None:
            with open(caminho_arquivo, encoding='utf-8', errors='replace') as f:
                conteudo_bruto = f.read()

        linhas_texto = conteudo_bruto.splitlines()
        if not linhas_texto:
            return _PlanilhaUnificada([])

        # Detecta o separador (";" é comum em CSV exportado no Brasil,
        # "," é o padrão internacional) -- csv.Sniffer erra às vezes com
        # poucos dados, então cai pra contagem manual se não conseguir.
        try:
            dialeto = _csv.Sniffer().sniff(linhas_texto[0])
            separador = dialeto.delimiter
        except Exception:
            separador = ';' if linhas_texto[0].count(';') >= linhas_texto[0].count(',') else ','

        leitor = _csv.reader(linhas_texto, delimiter=separador)
        linhas = []
        for i, linha_bruta in enumerate(leitor):
            if i == 0:
                # Cabeçalho -- mantém como string, sem converter tipo
                # (a validação de cabeçalho compara string com string).
                linhas.append([c.strip() if c is not None else c for c in linha_bruta])
            else:
                linhas.append([_normalizar_valor_csv(c) for c in linha_bruta])
        return _PlanilhaUnificada(linhas)

    else:
        # .xlsx, .xls, .xlsm, ou qualquer outra coisa -- tenta abrir
        # como planilha Excel via openpyxl.
        wb = openpyxl.load_workbook(caminho_arquivo, data_only=True)
        sheet = wb.active
        linhas = []
        for r in range(1, sheet.max_row + 1):
            linhas.append([sheet.cell(row=r, column=c).value for c in range(1, sheet.max_column + 1)])
        return _PlanilhaUnificada(linhas)


CABECALHO_PRODUCAO_MENSAL = [
    'PERIODO', 'ESTABELECIMENTO', 'DESCRIÇÃO ESTABELECIMENTO', 'ORDEM PRODUÇÃO', 'GRUPO MÁQUINA',
    'DESCRIÇÃO GRUPO MÁQUINA', 'ITEM PRODUÇÃO', 'DESCRIÇÃO ITEM PRODUÇÃO', 'UNIDADE', 'QTDADE PRODUÇÃO',
    'ITEM CONSUMO', 'DESCRIÇÃO ITEM CONSUMO', 'UNIDADE', 'QTDADE CONSUMO', 'INDICADOR', 'VALOR MATERIAL',
    'VALOR GGF', 'VALOR GGF FIXO', 'VALOR GGF VARIAVEL', 'VALOR GGF OUTROS', 'VALOR ULT ENTRADA', 'TIPO',
    'SEQUENCIA', 'NIVEL',
]

CABECALHO_DISTRIBUICAO_GGF_MENSAL = [
    'PERIODO', 'ESTABELECIMENTO', 'DESCRIÇÃO ESTABELECIMENTO', 'ORDEM PRODUÇÃO', 'GRUPO MÁQUINA',
    'DESCRIÇÃO GRUPO MÁQUINA', 'ITEM PRODUÇÃO', 'DESCRIÇÃO ITEM PRODUÇÃO', 'UNIDADE', 'QTDADE PRODUÇÃO',
    'ESPECIE GGF', 'CONTA', 'DESCRIÇÃO CONTA CONTÁBIL', 'CENTRO CUSTO', 'DESCRIÇÃO CENTRO DE CUSTO',
    'VALOR GASTOS',
]


def _processar_planilha_producao_mensal(sheet):
    """
    Processa uma planilha (Excel OU CSV, já carregada via
    _carregar_planilha_de_arquivo) no layout de Produção Mensal.
    MESMA lógica de negócio de importar_excel_producao_mensal_celery
    (a versão que lê da AWS) -- só a fonte dos dados muda. Levanta
    ValueError se o cabeçalho não bater com o esperado.

    🌟 OTIMIZADO: a versão original consultava o banco várias vezes POR
    LINHA (existe Estabelecimento? existe Grupo Máquina? etc.) -- pra um
    arquivo com dezenas de milhares de linhas, isso virava centenas de
    milhares de idas e vindas ao banco (~15-25 por linha), levando
    minutos pra terminar. Agora carrega TODOS os cadastros auxiliares
    (e os registros de Produção Mensal já existentes) em memória UMA
    VEZ no início, e só consulta o banco de novo quando precisa CRIAR
    algo que ainda não existia -- o resultado final (o que é criado,
    atualizado, e os valores gravados) é EXATAMENTE o mesmo de antes.
    """
    total_colunas = sheet.max_column
    total_linhas = sheet.max_row
    lista_colunas = [sheet.cell(row=1, column=i).value for i in range(1, total_colunas + 1)]

    if lista_colunas != CABECALHO_PRODUCAO_MENSAL:
        raise ValueError(
            "O cabeçalho do arquivo não bate com o esperado pra Produção Mensal.\n"
            f"Esperado: {CABECALHO_PRODUCAO_MENSAL}\nEncontrado: {lista_colunas}"
        )

    cache_estabelecimentos = {e.est_codigo: e for e in TbEstabelecimentos.objects.all()}
    cache_grupos_maquina = {g.gru_maq_codigo: g for g in TbGruposMaquinas.objects.all()}
    cache_itens_producao = {p.ite_pro_codigo: p for p in TbItensProducao.objects.all()}
    cache_itens_consumo = {c.ite_con_codigo: c for c in TbItensConsumo.objects.all()}
    cache_producao_mensal = {
        (p.pro_men_ano_mes, p.pro_men_estabelecimento_id, p.pro_men_grupo_maquina_id,
         p.pro_men_ordem_producao, p.pro_men_item_producao_id, p.pro_men_item_consumo_id): p
        for p in TbProducaoMensal.objects.all()
    }

    total_criadas = 0
    total_atualizadas = 0

    for i in range(total_linhas + 1):
        if i > 1 and sheet.cell(row=i, column=1).value is not None:
            valor_qtde_produzida = sheet.cell(row=i, column=10).value
            if valor_qtde_produzida and valor_qtde_produzida > 0 and sheet.cell(row=i, column=11) != None:

                v_ano_mes = str(sheet.cell(row=i, column=1).value)[0:6]
                v_ano_mes = v_ano_mes[0:4] + '/' + v_ano_mes[4:6]
                v_codigo_estabelecimento = sheet.cell(row=i, column=2).value.upper()
                if sheet.cell(row=i, column=5).value is None:
                    v_codigo_grupo_maquina = 'VAZIO'
                else:
                    v_codigo_grupo_maquina = sheet.cell(row=i, column=5).value.upper()
                v_ordem_producao = str(sheet.cell(row=i, column=4).value).replace('.', '').upper()[0:9]
                v_codigo_item_producao = str(sheet.cell(row=i, column=7).value).replace('.', '').upper()
                v_codigo_item_consumo = str(sheet.cell(row=i, column=11).value).replace('.', '').upper()
                if sheet.cell(row=i, column=12).value is None:
                    v_descricao_item_consumo = ''
                    v_unidade_item_consumo = ''
                    v_tipo_item_consumo = ''
                else:
                    v_descricao_item_consumo = sheet.cell(row=i, column=12).value[0:60].upper()
                    v_unidade_item_consumo = sheet.cell(row=i, column=13).value.upper()
                    v_tipo_item_consumo = sheet.cell(row=i, column=22).value.upper()

                # Estabelecimento (cache -- 0 consultas se já visto antes)
                objeto = cache_estabelecimentos.get(v_codigo_estabelecimento)
                nome_planilha = sheet.cell(row=i, column=3).value.upper()
                if objeto is not None:
                    if objeto.est_nome != nome_planilha:
                        objeto.est_nome = nome_planilha
                        objeto.save()
                    v_id_estabelecimento = objeto.id
                else:
                    n = TbEstabelecimentos.objects.create(est_codigo=v_codigo_estabelecimento, est_nome=nome_planilha)
                    cache_estabelecimentos[v_codigo_estabelecimento] = n
                    v_id_estabelecimento = n.id

                # Grupo Máquina (cache)
                objeto = cache_grupos_maquina.get(v_codigo_grupo_maquina)
                nome_planilha = sheet.cell(row=i, column=6).value.upper()
                if objeto is not None:
                    if objeto.gru_maq_nome != nome_planilha:
                        objeto.gru_maq_nome = nome_planilha
                        objeto.save()
                    v_id_grupo_maquina = objeto.id
                else:
                    n = TbGruposMaquinas.objects.create(gru_maq_codigo=v_codigo_grupo_maquina,
                                                        gru_maq_nome=nome_planilha)
                    cache_grupos_maquina[v_codigo_grupo_maquina] = n
                    v_id_grupo_maquina = n.id

                # Item Produção (cache)
                objeto = cache_itens_producao.get(v_codigo_item_producao)
                descricao_planilha = sheet.cell(row=i, column=8).value[0:60].upper()
                unidade_planilha = sheet.cell(row=i, column=9).value.upper()
                if objeto is not None:
                    mudou = False
                    if objeto.ite_pro_descricao != descricao_planilha:
                        objeto.ite_pro_descricao = descricao_planilha
                        mudou = True
                    if objeto.ite_pro_unidade != unidade_planilha:
                        objeto.ite_pro_unidade = unidade_planilha
                        mudou = True
                    if mudou:
                        objeto.save()
                    v_id_item_producao = objeto.id
                else:
                    n = TbItensProducao.objects.create(ite_pro_codigo=v_codigo_item_producao,
                                                       ite_pro_descricao=descricao_planilha,
                                                       ite_pro_unidade=unidade_planilha)
                    cache_itens_producao[v_codigo_item_producao] = n
                    v_id_item_producao = n.id

                # Item Consumo (cache)
                objeto = cache_itens_consumo.get(v_codigo_item_consumo)
                if objeto is not None:
                    mudou = False
                    if objeto.ite_con_descricao != v_descricao_item_consumo:
                        objeto.ite_con_descricao = v_descricao_item_consumo
                        mudou = True
                    if objeto.ite_con_unidade != v_unidade_item_consumo:
                        objeto.ite_con_unidade = v_unidade_item_consumo
                        mudou = True
                    if objeto.ite_con_tipo != v_tipo_item_consumo:
                        objeto.ite_con_tipo = v_tipo_item_consumo
                        mudou = True
                    if mudou:
                        objeto.save()
                    v_id_item_consumo = objeto.id
                else:
                    n = TbItensConsumo.objects.create(ite_con_codigo=v_codigo_item_consumo,
                                                      ite_con_descricao=v_descricao_item_consumo,
                                                      ite_con_unidade=v_unidade_item_consumo,
                                                      ite_con_tipo=v_tipo_item_consumo)
                    cache_itens_consumo[v_codigo_item_consumo] = n
                    v_id_item_consumo = n.id

                chave = (v_ano_mes, v_id_estabelecimento, v_id_grupo_maquina, v_ordem_producao, v_id_item_producao,
                         v_id_item_consumo)
                objeto = cache_producao_mensal.get(chave)

                if type(sheet.cell(row=i, column=16).value) is str:
                    valor_material = sheet.cell(row=i, column=16).value.replace(',', '.')
                else:
                    valor_material = sheet.cell(row=i, column=16).value

                if objeto is None:
                    n = TbProducaoMensal.objects.create(
                        pro_men_ano_mes=v_ano_mes,
                        pro_men_estabelecimento_id=v_id_estabelecimento,
                        pro_men_grupo_maquina_id=v_id_grupo_maquina,
                        pro_men_ordem_producao=v_ordem_producao,
                        pro_men_item_producao_id=v_id_item_producao,
                        pro_men_qtde_produzida=sheet.cell(row=i, column=10).value,
                        pro_men_item_consumo_id=v_id_item_consumo,
                        pro_men_qtde_consumo=sheet.cell(row=i, column=14).value,
                        pro_men_valor_material=valor_material,
                        pro_men_valor_ggf=sheet.cell(row=i, column=17).value,
                        pro_men_valor_ultima_entrada=sheet.cell(row=i, column=21).value
                    )
                    cache_producao_mensal[chave] = n
                    total_criadas += 1
                else:
                    objeto.pro_men_qtde_produzida = sheet.cell(row=i, column=10).value
                    objeto.pro_men_qtde_consumo = sheet.cell(row=i, column=14).value
                    objeto.pro_men_valor_material = sheet.cell(row=i, column=16).value
                    objeto.pro_men_valor_ggf = sheet.cell(row=i, column=17).value
                    objeto.pro_men_valor_ultima_entrada = sheet.cell(row=i, column=21).value
                    objeto.save()
                    total_atualizadas += 1

    return {'total_criadas': total_criadas, 'total_atualizadas': total_atualizadas}


def _processar_planilha_distribuicao_ggf_mensal(sheet):
    """
    Processa uma planilha (Excel OU CSV) no layout de Distribuição GGF
    Mensal. MESMA lógica de negócio de
    importar_excel_distribuicao_ggf_mensal_celery -- só a fonte dos
    dados muda. Levanta ValueError se o cabeçalho não bater.

    🌟 OTIMIZADO: mesmo princípio de _processar_planilha_producao_mensal
    -- carrega os cadastros auxiliares (Estabelecimento, Grupo Máquina,
    Item Produção, Conta Contábil, Centro de Custo, Conta+CC, e os
    registros de Distribuição GGF já existentes) em memória UMA VEZ,
    em vez de consultar o banco várias vezes por linha do arquivo.
    """
    total_colunas = sheet.max_column
    total_linhas = sheet.max_row
    lista_colunas = [sheet.cell(row=1, column=i).value for i in range(1, total_colunas + 1)]

    if lista_colunas != CABECALHO_DISTRIBUICAO_GGF_MENSAL:
        raise ValueError(
            "O cabeçalho do arquivo não bate com o esperado pra Distribuição GGF Mensal.\n"
            f"Esperado: {CABECALHO_DISTRIBUICAO_GGF_MENSAL}\nEncontrado: {lista_colunas}"
        )

    cache_estabelecimentos = {e.est_codigo: e for e in TbEstabelecimentos.objects.all()}
    cache_grupos_maquina = {g.gru_maq_codigo: g for g in TbGruposMaquinas.objects.all()}
    cache_itens_producao = {p.ite_pro_codigo: p for p in TbItensProducao.objects.all()}
    cache_contas = {c.con_con_codigo: c for c in TbContaContabil.objects.all()}
    # 🌟 Só os de referência '2', igual ao filtro original -- outras
    # referências não devem ser reaproveitadas aqui de jeito nenhum.
    cache_centros_custo = {c.cen_cus_codigo: c for c in TbCentroCusto.objects.filter(cen_cus_referencia='2')}
    cache_conta_cc = {
        (r.con_con_cen_cus_conta_id, r.con_con_cen_cus_cc_id, r.con_con_cen_cus_estabelecimento_id): r
        for r in TbContaContabilCentroCusto.objects.all()
    }
    cache_distribuicao_ggf = {
        (d.dis_ggf_men_ano_mes, d.dis_ggf_men_estabelecimento_id, d.dis_ggf_men_grupo_maquina_id,
         d.dis_ggf_men_ordem_producao, d.dis_ggf_men_item_producao_id, d.dis_ggf_men_conta_cc_id): d
        for d in TbDistribuicaoGGFMensal.objects.all()
    }

    total_criadas = 0
    total_atualizadas = 0

    for i in range(1, total_linhas + 1):
        if i > 1 and sheet.cell(row=i, column=1).value is not None:
            v_ano_mes = str(sheet.cell(row=i, column=1).value)[0:6]
            v_ano_mes = v_ano_mes[0:4] + '/' + v_ano_mes[4:6]
            v_codigo_estabelecimento = sheet.cell(row=i, column=2).value.upper()
            if sheet.cell(row=i, column=5).value is None:
                v_codigo_grupo_maquina = 'VAZIO'
            else:
                v_codigo_grupo_maquina = sheet.cell(row=i, column=5).value.upper()
            v_ordem_producao = str(sheet.cell(row=i, column=4).value).replace('.', '').upper()[0:9]
            v_codigo_item_producao = str(sheet.cell(row=i, column=7).value).replace('.', '').upper()
            v_codigo_conta = str(sheet.cell(row=i, column=12).value).replace('.', '')[0:6].upper()
            v_descricao_conta = str(sheet.cell(row=i, column=13).value).upper()
            v_codigo_cc = str(sheet.cell(row=i, column=14).value).replace('.', '')[0:6].upper()
            v_descricao_cc = str(sheet.cell(row=i, column=15).value).upper()

            # Estabelecimento (cache)
            objeto = cache_estabelecimentos.get(v_codigo_estabelecimento)
            nome_planilha = sheet.cell(row=i, column=3).value.upper()
            if objeto is not None:
                if objeto.est_nome != nome_planilha:
                    objeto.est_nome = nome_planilha
                    objeto.save()
                v_id_estabelecimento = objeto.id
            else:
                n = TbEstabelecimentos.objects.create(est_codigo=v_codigo_estabelecimento, est_nome=nome_planilha)
                cache_estabelecimentos[v_codigo_estabelecimento] = n
                v_id_estabelecimento = n.id

            # Grupo Máquina (cache)
            objeto = cache_grupos_maquina.get(v_codigo_grupo_maquina)
            nome_planilha = sheet.cell(row=i, column=6).value.upper()
            if objeto is not None:
                if objeto.gru_maq_nome != nome_planilha:
                    objeto.gru_maq_nome = nome_planilha
                    objeto.save()
                v_id_grupo_maquina = objeto.id
            else:
                n = TbGruposMaquinas.objects.create(gru_maq_codigo=v_codigo_grupo_maquina, gru_maq_nome=nome_planilha)
                cache_grupos_maquina[v_codigo_grupo_maquina] = n
                v_id_grupo_maquina = n.id

            # Item Produção (cache)
            objeto = cache_itens_producao.get(v_codigo_item_producao)
            descricao_planilha = sheet.cell(row=i, column=8).value[0:60].upper()
            unidade_planilha = sheet.cell(row=i, column=9).value.upper()
            if objeto is not None:
                mudou = False
                if objeto.ite_pro_descricao != descricao_planilha:
                    objeto.ite_pro_descricao = descricao_planilha
                    mudou = True
                if objeto.ite_pro_unidade != unidade_planilha:
                    objeto.ite_pro_unidade = unidade_planilha
                    mudou = True
                if mudou:
                    objeto.save()
                v_id_item_producao = objeto.id
            else:
                n = TbItensProducao.objects.create(ite_pro_codigo=v_codigo_item_producao,
                                                   ite_pro_descricao=descricao_planilha,
                                                   ite_pro_unidade=unidade_planilha)
                cache_itens_producao[v_codigo_item_producao] = n
                v_id_item_producao = n.id

            # Conta Contábil (cache)
            objeto = cache_contas.get(v_codigo_conta)
            if objeto is not None:
                if objeto.con_con_descricao != v_descricao_conta:
                    objeto.con_con_descricao = v_descricao_conta
                    objeto.save()
                v_id_conta = objeto.id
            else:
                n = TbContaContabil.objects.create(con_con_codigo=v_codigo_conta, con_con_descricao=v_descricao_conta)
                cache_contas[v_codigo_conta] = n
                v_id_conta = n.id

            # Centro de Custo (cache, só referência '2')
            objeto = cache_centros_custo.get(v_codigo_cc)
            if objeto is not None:
                if objeto.cen_cus_descricao != v_descricao_cc:
                    objeto.cen_cus_descricao = v_descricao_cc
                    objeto.save()
                v_id_cc = objeto.id
            else:
                n = TbCentroCusto.objects.create(cen_cus_codigo=v_codigo_cc, cen_cus_descricao=v_descricao_cc,
                                                 cen_cus_referencia='2')
                cache_centros_custo[v_codigo_cc] = n
                v_id_cc = n.id

            # Conta Contábil x Centro de Custo x Estabelecimento (cache)
            chave_conta_cc = (v_id_conta, v_id_cc, v_id_estabelecimento)
            objeto = cache_conta_cc.get(chave_conta_cc)
            if objeto is not None:
                v_id_conta_cc = objeto.id
            else:
                n = TbContaContabilCentroCusto.objects.create(con_con_cen_cus_conta_id=v_id_conta,
                                                              con_con_cen_cus_cc_id=v_id_cc,
                                                              con_con_cen_cus_estabelecimento_id=v_id_estabelecimento,
                                                              con_con_cen_cus_tipo='A')
                cache_conta_cc[chave_conta_cc] = n
                v_id_conta_cc = n.id

            chave = (v_ano_mes, v_id_estabelecimento, v_id_grupo_maquina, v_ordem_producao, v_id_item_producao,
                     v_id_conta_cc)
            objeto = cache_distribuicao_ggf.get(chave)

            if objeto is None:
                n = TbDistribuicaoGGFMensal.objects.create(
                    dis_ggf_men_ano_mes=v_ano_mes,
                    dis_ggf_men_estabelecimento_id=v_id_estabelecimento,
                    dis_ggf_men_grupo_maquina_id=v_id_grupo_maquina,
                    dis_ggf_men_ordem_producao=v_ordem_producao,
                    dis_ggf_men_item_producao_id=v_id_item_producao,
                    dis_ggf_men_qtde_produzida=sheet.cell(row=i, column=10).value,
                    dis_ggf_men_conta_cc_id=v_id_conta_cc,
                    dis_ggf_men_valor=sheet.cell(row=i, column=16).value
                )
                cache_distribuicao_ggf[chave] = n
                total_criadas += 1
            else:
                objeto.dis_ggf_men_qtde_produzida = sheet.cell(row=i, column=10).value
                objeto.dis_ggf_men_valor = sheet.cell(row=i, column=16).value
                objeto.save()
                total_atualizadas += 1

    return {'total_criadas': total_criadas, 'total_atualizadas': total_atualizadas}


def _atualizar_status_importacao_cf(usuario_id, status, mensagem=None, resultado=None):
    """
    Grava o andamento da importação em EstadoConversaAgente pra tela de
    chat conferir via sondagem automática (mesmo padrão já usado pra
    exportar dados de otimização).
    """
    estado = EstadoConversaAgente.objects.filter(usuario_id=usuario_id).first()
    if estado is None:
        return
    dados = dict(estado.dados_coletados or {})
    dados['status_importacao_cf'] = status
    if mensagem is not None:
        dados['mensagem_importacao_cf'] = mensagem
    if resultado is not None:
        dados['resultado_importacao_cf'] = resultado
    estado.dados_coletados = dados
    estado.save()


@shared_task(bind=True)
def importar_producao_mensal_de_relatorio_celery(self, relatorio_id, usuario_id):
    try:
        relatorio = RelatorioPDF.objects.get(id=relatorio_id)
        sheet = _carregar_planilha_de_arquivo(relatorio.arquivo.path)
        resultado = _processar_planilha_producao_mensal(sheet)
    except ValueError as e:
        _atualizar_status_importacao_cf(usuario_id, 'erro', str(e))
        return
    except Exception as e:
        _atualizar_status_importacao_cf(usuario_id, 'erro', f"Erro inesperado ao processar o arquivo: {e}")
        return

    # Sucesso -- apaga o arquivo (mesmo comportamento do fluxo AWS, que
    # some com o arquivo original depois de importar).
    caminho_antigo = relatorio.arquivo.path
    relatorio.delete()
    if _os.path.exists(caminho_antigo):
        _os.remove(caminho_antigo)

    _atualizar_status_importacao_cf(usuario_id, 'concluido', resultado=resultado)


@shared_task(bind=True)
def importar_distribuicao_ggf_mensal_de_relatorio_celery(self, relatorio_id, usuario_id):
    try:
        relatorio = RelatorioPDF.objects.get(id=relatorio_id)
        sheet = _carregar_planilha_de_arquivo(relatorio.arquivo.path)
        resultado = _processar_planilha_distribuicao_ggf_mensal(sheet)
    except ValueError as e:
        _atualizar_status_importacao_cf(usuario_id, 'erro', str(e))
        return
    except Exception as e:
        _atualizar_status_importacao_cf(usuario_id, 'erro', f"Erro inesperado ao processar o arquivo: {e}")
        return

    caminho_antigo = relatorio.arquivo.path
    relatorio.delete()
    if _os.path.exists(caminho_antigo):
        _os.remove(caminho_antigo)

    _atualizar_status_importacao_cf(usuario_id, 'concluido', resultado=resultado)


@shared_task
def exportar_pdf_qs(lista):  # Exporta para pdf os consumos específicos informados na lista
    # Create a file-like buffer to receive PDF data.
    buffer = io.BytesIO()

    # Create the PDF object, using the buffer as its "file."
    p = canvas.Canvas(buffer)

    for qs in lista:

        # Definindo padrões a serem usados no PDF
        nome_arquivo = 'Consumo Específico SPS/CF - Formato'

        largura_pagina = 210 * mm  # Padrão A4
        altura_pagina = 297 * mm  # Padrão A4
        titulo_relatorio = 'Consumo Específico'

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4

        p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

        # Cabeçalho do PDF
        # Setando tamanho do título e escrevendo os títulos no topo da página
        p.setFont("Helvetica-Bold", 18)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

        p.setFont("Helvetica-Bold", 10)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 45, str(qs.id) + ' - ' + qs.con_esp_descricao)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 59,
                            'Área Responsável: ' + qs.get_con_esp_area_responsavel_display())
        p.line(5, altura_pagina - 65, largura_pagina - 5, altura_pagina - 65)

        line_number = altura_pagina - 78
        p.setFont("Helvetica-Bold", 8)

        # Vamos pegar os resultados do periodo
        p.drawString(15, line_number, 'Ano/Mês Início : ' + qs.con_esp_ano_mes_inicio)
        p.drawString(117, line_number, 'Ano/Mês Fim : ' + qs.con_esp_ano_mes_fim)
        p.drawString(240, line_number,
                     'Qtde Produzida : ' + locale.format_string('%.2f', qs.con_esp_qtde_produzida, True))
        p.drawString(370, line_number, 'Qtde Consumo : ' + locale.format_string('%.2f', qs.con_esp_qtde_consumo, True))
        p.drawString(510, line_number, 'Indicador : ' + locale.format_string('%.4f', qs.con_esp_indicador, True))

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)
        line_number = line_number - 11

        # Vamos pegar os itens de consumo
        p.setFont("Helvetica-Bold", 10)
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE CONSUMO')
        lista_id_item_consumo_qs = TbConsumoEspecifico.con_esp_item_consumo.through.objects.filter(
            tbconsumoespecifico_id=qs.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_consumo in lista_id_item_consumo_qs:
            # Tenho o id do item de consumo. Vamos pegar a descrição
            descricao_item_consumo = TbItensConsumo.objects.get(
                id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
            codigo_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_consumo + ' / ' + codigo_item_consumo)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os grupos máquinas
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'GRUPOS MÁQUINAS')
        lista_id_grupo_maquina_qs = TbConsumoEspecifico.con_esp_grupo_maquina.through.objects.filter(
            tbconsumoespecifico_id=qs.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquina. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(
                id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            codigo_grupo_maquina = TbGruposMaquinas.objects.get(
                id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, nome_grupo_maquina + ' / ' + codigo_grupo_maquina)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os itens de produção
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE PRODUÇÃO')
        lista_id_item_producao_qs = TbConsumoEspecifico.con_esp_item_producao.through.objects.filter(
            tbconsumoespecifico_id=qs.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de produção. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(
                id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            codigo_item_producao = TbItensProducao.objects.get(
                id=lista_id_item_producao.tbitensproducao_id).ite_pro_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_producao + ' - ' + codigo_item_producao)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os resultados históricos
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'RESULTADOS HISTÓRICOS')
        p.setFont("Helvetica-Bold", 4.5)
        line_number = line_number - 11
        p.drawCentredString(largura_pagina / 2, line_number,
                            'ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR')

        linha_inicial_historico = line_number

        # Vamos pegar o ano/mês máximo calculado para o consumo específico
        ano_mes_maximo_qs = TbConsumoEspecificoDaugther.objects.filter(mae_id=qs.id).order_by('-ano_mes')
        ano_mes_maximo = ''
        for ano_mes_row in ano_mes_maximo_qs:
            ano_mes_maximo = ano_mes_row.ano_mes
            break
        ano_maximo = int(ano_mes_maximo[0:4])
        ano_minimo = ano_maximo - 4
        coluna = 17
        for ano in range(ano_minimo, ano_maximo + 1, 1):
            line_number = linha_inicial_historico
            for mes in range(1, 13, 1):
                line_number = line_number - 11
                if mes < 10:
                    ano_mes_corrente = str(ano) + '/0' + str(mes)
                else:
                    ano_mes_corrente = str(ano) + '/' + str(mes)

                # Vamos pegar os resultados para o ano/mês corrente
                if TbConsumoEspecificoDaugther.objects.filter(mae_id=qs.id, ano_mes=ano_mes_corrente):
                    qtde_produzida = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id,
                                                                             ano_mes=ano_mes_corrente).qtde_produzida
                    qtde_consumo = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id,
                                                                           ano_mes=ano_mes_corrente).qtde_consumo
                    indicador = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id,
                                                                        ano_mes=ano_mes_corrente).indicador
                    p.drawString(coluna, line_number, ano_mes_corrente)

                    p.drawRightString(coluna + 43, line_number, locale.format_string('%.2f', qtde_produzida, True))
                    p.drawRightString(coluna + 73, line_number, locale.format_string('%.2f', qtde_consumo, True))
                    p.drawRightString(coluna + 98, line_number, locale.format_string('%.4f', indicador, True))
            coluna = coluna + 115

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Dados para o gráfico
        data = []
        rotulo_x = []

        resultado_historico_qs = TbConsumoEspecificoDaugther.objects.filter(mae_id=qs.id).order_by('ano_mes')

        for resultado_historico in resultado_historico_qs:
            data.append(float(resultado_historico.indicador))
            # Tiramos o label do eixo x pois estava sobreponto um cima do outro. Deixamos aqui para no futuro incluir se necessário
            # rotulo_x.append(resultado_historico.ano_mes)

        # Gráfico de barras
        drawing = Drawing(largura_pagina - 30, line_number - 60)

        # coordinates (from left bottom)
        x = 15
        y = 30

        bar = VerticalBarChart()
        bar.x = 30
        bar.y = 30
        bar.width = largura_pagina - 80
        bar.height = line_number * 0.7
        bar.valueAxis.valueMin = 0

        data = [data, ]
        bar.data = data

        bar.categoryAxis.categoryNames = rotulo_x
        bar.bars[0].fillColor = colors.green

        drawing.add(bar, '')
        renderPDF.draw(drawing, p, x, y, showBoundary=True)

        p.setFont("Helvetica-Bold", 14)
        line_number = line_number - 20
        p.drawCentredString(largura_pagina / 2, line_number, "Indicador")

        p.setFont("Helvetica-Oblique", 8)
        p.drawCentredString(largura_pagina / 2, 10, "Copyright © 2023 SPS Consultoria. Todos os direitos reservados.")

        # Data e Número da página
        p.drawString(10, 10, "Data: " + str(date.today().strftime("%d/%m/%Y")))
        p.drawRightString(0.975 * largura_pagina, 10, "Página: %d" % (p._pageNumber))

        # Vamos mudar de página
        p.showPage()

    p.save()

    # FileResponse sets the Content-Disposition header so that browsers
    # present the option to save the file.
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=nome_arquivo)


@shared_task
def atualiza_tabela_variaveis(id_rlm):
    # Primeiro vamos atualizar o volume de produção para o periodo informado
    cursor = connection.cursor()
    sql = "call public.producao_rlm(" + str(id_rlm) + ")"
    cursor.execute(sql)
    cursor.close()

    # Setando variáveis da regressão
    equacao_regressao = ''
    r2_regressao = 0
    sumario = ''

    # Vamos atualizar a tabela de variáveis
    # Primeiro alterando o flag e zerando o indicador da tabela filha. Isso se existirem dados
    objeto_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm)
    if objeto_qs:  # Existem dados
        for objeto in objeto_qs:
            if objeto.tipo_variavel == 'Y' or objeto.tipo_variavel == 'D':  # Se variável é dependente (Y) ou Desconsiderar (D), cancela valor do agrupamento
                objeto.agrupamento = None

            objeto.flag = False
            objeto.indicador = 0
            objeto.minimo = 0
            objeto.media = 0
            objeto.maximo = 0
            objeto.coeficiente = 0
            objeto.pt = 0
            objeto.save()

    # Vamos pegar o periodo definido para a regressão linear multipla
    inicio_periodo = TbRegressaoLinearMultipla.objects.get(id=id_rlm).reg_lin_mul_ano_mes_inicio
    fim_periodo = TbRegressaoLinearMultipla.objects.get(id=id_rlm).reg_lin_mul_ano_mes_fim

    # Vamos atualizar o status da regressão
    status_regressao = ''

    # Verificando se foi indicado grupo máquinas
    if TbRegressaoLinearMultipla.reg_lin_mul_grupo_maquina.through.objects.filter(
            tbregressaolinearmultipla_id=id_rlm).count() > 0:
        # status_regressao = 'DEFINIÇÃO DOS GRUPOS MÁQUINAS: OK.'

        # Vamos atualizar tabela de variáveis
        produto_qs = TbRegressaoLinearMultipla.reg_lin_mul_item_producao.through.objects.filter(
            tbregressaolinearmultipla_id=id_rlm)  # Temos nessa query os produtos selecionados
        lista_produto = []
        for produto in produto_qs:
            lista_produto.append(produto.tbitensproducao_id)

        grupo_maquina_qs = TbRegressaoLinearMultipla.reg_lin_mul_grupo_maquina.through.objects.filter(
            tbregressaolinearmultipla_id=id_rlm)  # Temos nessa query os grupos máquinas selecionados
        lista_grupo_maquima = []
        for grupo_maquina in grupo_maquina_qs:
            lista_grupo_maquima.append(grupo_maquina.tbgruposmaquinas_id)

        # Vamos selecionar os id dos itens de consumo para os produtos, grupos máquinas e periodo definido para a regressão. Isso na tabela de produção mensal
        item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=lista_produto,
                                                          pro_men_grupo_maquina_id__in=lista_grupo_maquima,
                                                          pro_men_ano_mes__gte=inicio_periodo,
                                                          pro_men_ano_mes__lte=fim_periodo)
        lista_item_consumo = []

        for item_consumo in item_consumo_qs:
            if item_consumo.pro_men_item_consumo_id not in lista_item_consumo:
                lista_item_consumo.append(item_consumo.pro_men_item_consumo_id)

        # Na lista_item_consumo temos o id dos itens de consumo. Vamos calcular o indicador, e lançar na tabela
        for id_lista_item_consumo in lista_item_consumo:
            # Vamos calcular o indicador para o item de consumo para o periodo informado
            cursor = connection.cursor()
            # Expressão SQL
            sql = "call public.indicador_rlm_item_consumo(" + str(id_rlm) + ", " + str(
                id_lista_item_consumo) + ", 0, 0, 0, 0)"
            cursor.execute(sql)
            resultados = cursor.fetchone()
            retorno = resultados[0]  # Valor indicador
            minimo = resultados[1]  # Valor mínimo
            maximo = resultados[2]  # Valor máximo
            media = resultados[3]  # Valor média
            cursor.close()

            if retorno != 0:  # Vamos lançar na tabela filha
                # Vamos ver a classificação do item de consumo
                descricao_classificacao = 'CONSUMO'  # Assumimos como CONSUMO

                # Se for negativo, temos que ver se é SUBPRODUTO. Se não for é DESVIO.
                if retorno < 0:
                    if TbItensConsumo.objects.get(id=id_lista_item_consumo).ite_subproduto == True:
                        descricao_classificacao = 'SUBPRODUTO'
                    else:
                        descricao_classificacao = 'DESVIO'

                if TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm,
                                                                    item_consumo_id=id_lista_item_consumo).count() == 1:
                    # Existe. Vamos atualizar
                    objeto = TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                           item_consumo_id=id_lista_item_consumo)
                    objeto.flag = True
                    objeto.indicador = retorno
                    objeto.minimo = minimo
                    objeto.maximo = maximo
                    objeto.media = media
                    objeto.classificacao = descricao_classificacao
                    objeto.save()
                else:
                    # Não existe. Vamos criar
                    TbRegressaoLinearMultiplaDaugther.objects.create(item_consumo_id=id_lista_item_consumo,
                                                                     indicador=retorno,
                                                                     minimo=minimo,
                                                                     maximo=maximo,
                                                                     media=media,
                                                                     flag=True,
                                                                     mae_id=id_rlm,
                                                                     classificacao=descricao_classificacao)
        # Verificando se tabela de variáveis existe
        if TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, flag=True).count() >= 1:
            # status_regressao = status_regressao + '\n' + 'CRIAÇÃO DA TABELA DE VARIÁVEIS: OK.'

            # Vamos verificar se foi indicado variável dependente e pelo menos uma variável independente
            if TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, tipo_variavel='Y',
                                                                flag=True).count() >= 1:
                # Vamos ver se foi indicado pelo menos uma variável independente
                if TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, tipo_variavel='X',
                                                                    flag=True).count() >= 1:
                    # status_regressao = status_regressao + '\n' + 'DEFINIÇÃO DAS VARIÁVEIS DEPENDENTES E INDEPENDENTES: OK.'
                    # Vamos verificar se os agrupamentos das variáveis independentes foram definidos

                    if TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, tipo_variavel='X',
                                                                        agrupamento=None, flag=True).count() == 0:
                        # status_regressao = status_regressao + '\n' + 'AGRUPAMENTO DAS VARIAVEIS INDEPENDENTES: OK.'
                        # Vamos ordenar o agrupamento das variáveis independentes
                        # Vamos ver quantos agrupamentos foram definidos
                        objeto_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, tipo_variavel='X',
                                                                                     flag=True).order_by('agrupamento')
                        contador_agrupamentos = 0
                        old_agrupamento = 0
                        if objeto_qs:
                            for objeto in objeto_qs:
                                if objeto.agrupamento != old_agrupamento:
                                    contador_agrupamentos = contador_agrupamentos + 1

                                old_agrupamento = objeto.agrupamento
                                objeto.agrupamento = contador_agrupamentos
                                objeto.save()

                        # Podemos agora montar a base de dados para usar na regressão linear múltipla
                        # Primeiro alterando o flag e zerando o indicador da tabela filha 1
                        objeto_qs = TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm)
                        if objeto_qs:
                            for objeto in objeto_qs:
                                objeto.flag = False
                                objeto.itens_consumo = ''
                                objeto.qtde_produzida = 0
                                objeto.qtde_consumo = 0
                                objeto.indicador = 0
                                objeto.save()

                        # Vamos agora popular a tabela de acordo com a definição das variáveis na tabela filha

                        periodo_str = inicio_periodo
                        objeto_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, flag=True).exclude(
                            tipo_variavel='D')

                        while periodo_str <= fim_periodo:

                            # Vamos calcular a qtde produzida no periodo
                            cursor = connection.cursor()
                            # Expressão SQL
                            sql = "call public.producao_rlm_periodo(" + str(id_rlm) + ", '" + periodo_str + "', 0)"
                            cursor.execute(sql)
                            retorno = cursor.fetchone()[0]
                            cursor.close()

                            for objeto in objeto_qs:  # Atenção: na query objeto_qs só tem variável Y e X
                                if objeto.tipo_variavel == 'X':
                                    chave_variavel = objeto.tipo_variavel + str(objeto.agrupamento)
                                else:
                                    chave_variavel = objeto.tipo_variavel

                                if TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm,
                                                                                     variavel=chave_variavel,
                                                                                     ano_mes=periodo_str).count() == 1:
                                    # Já existe. Vamos atualizar
                                    objeto = TbRegressaoLinearMultiplaDaugther1.objects.get(mae_id=id_rlm,
                                                                                            variavel=chave_variavel,
                                                                                            ano_mes=periodo_str)
                                    objeto.qtde_produzida = retorno
                                    objeto.flag = True
                                    objeto.save()
                                else:
                                    # Não existe. Vamos criar
                                    TbRegressaoLinearMultiplaDaugther1.objects.create(ano_mes=periodo_str,
                                                                                      qtde_produzida=retorno,
                                                                                      variavel=chave_variavel,
                                                                                      indicador=0,
                                                                                      flag=True,
                                                                                      mae_id=id_rlm)

                            year = int(periodo_str[:4])
                            month = int(periodo_str[-2:])
                            if month + 1 > 12:
                                month = 1
                                year = year + 1
                            else:
                                month = month + 1
                            if month < 10:
                                periodo_str = str(year) + '/0' + str(month)
                            else:
                                periodo_str = str(year) + '/' + str(month)

                        # Vamos agora contabilizar a quantidade de consumo na tabela filha 1
                        # queryset item_consumo_qs temos as informações de consumo para os itens de produção, grupo máquina e periodo informados
                        # Lembrando item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=lista_produto, pro_men_grupo_maquina_id__in=lista_grupo_maquima, pro_men_ano_mes__gte=inicio_periodo, pro_men_ano_mes__lte=fim_periodo)
                        for item_consumo in item_consumo_qs:
                            id_item_consumo = item_consumo.pro_men_item_consumo_id
                            ano_mes_item_consumo = item_consumo.pro_men_ano_mes
                            # Vamos pegar a chave
                            chave_variavel = ''
                            if TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                             item_consumo_id=id_item_consumo,
                                                                             flag=True).tipo_variavel == 'X':
                                chave_variavel = TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                                               item_consumo_id=id_item_consumo,
                                                                                               flag=True).tipo_variavel + str(
                                    TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                                  item_consumo_id=id_item_consumo,
                                                                                  flag=True).agrupamento)
                            else:  # Vamos ver se é Y
                                if TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                                 item_consumo_id=id_item_consumo,
                                                                                 flag=True).tipo_variavel == 'Y':
                                    chave_variavel = TbRegressaoLinearMultiplaDaugther.objects.get(mae_id=id_rlm,
                                                                                                   item_consumo_id=id_item_consumo,
                                                                                                   flag=True).tipo_variavel

                            # Vamos ver se tem na tabela filha 1. Se tem, somamos na qtde consumo
                            if chave_variavel != '':
                                if TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm,
                                                                                     ano_mes=ano_mes_item_consumo,
                                                                                     variavel=chave_variavel,
                                                                                     flag=True).count() == 1:
                                    objeto = TbRegressaoLinearMultiplaDaugther1.objects.get(mae_id=id_rlm,
                                                                                            ano_mes=ano_mes_item_consumo,
                                                                                            variavel=chave_variavel,
                                                                                            flag=True)
                                    objeto.qtde_consumo = objeto.qtde_consumo + item_consumo.pro_men_qtde_consumo
                                    objeto.save()

                        # Vamos atualizar os indicadores e os itens de consumo
                        objeto_qs = TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm, flag=True)
                        if objeto_qs:
                            for objeto in objeto_qs:
                                if objeto.qtde_produzida > 0:
                                    objeto.indicador = objeto.qtde_consumo / objeto.qtde_produzida
                                else:
                                    objeto.flag = False  # Passamos para False para não aparecer depois os anos/meses sem produção

                                v_tipo_variavel = objeto.variavel[0:1]
                                if v_tipo_variavel == 'X':
                                    v_agrupamento = int(objeto.variavel[1:])
                                    objeto_itens_consumo_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(
                                        mae_id=id_rlm, tipo_variavel=v_tipo_variavel, agrupamento=v_agrupamento,
                                        flag=True)

                                else:
                                    objeto_itens_consumo_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(
                                        mae_id=id_rlm, tipo_variavel=v_tipo_variavel, flag=True)

                                itens_consumo_descricao = ''
                                for objeto_itens_consumo in objeto_itens_consumo_qs:
                                    if len(itens_consumo_descricao) == 0:
                                        itens_consumo_descricao = str(objeto_itens_consumo.item_consumo)
                                    else:
                                        itens_consumo_descricao = itens_consumo_descricao + ' / ' + str(
                                            objeto_itens_consumo.item_consumo)

                                objeto.itens_consumo = itens_consumo_descricao
                                objeto.save()

                        # Vamos criar o dictionary com os dados para rodar a regressão
                        dictionary = {}
                        # Primeiro variável dependente
                        periodo_str = inicio_periodo
                        lista_valores = []
                        while periodo_str <= fim_periodo:
                            if TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm, ano_mes=periodo_str,
                                                                                 variavel='Y', flag=True).count() == 1:
                                lista_valores.append(float(
                                    TbRegressaoLinearMultiplaDaugther1.objects.get(mae_id=id_rlm, ano_mes=periodo_str,
                                                                                   variavel='Y', flag=True).indicador))

                            year = int(periodo_str[:4])
                            month = int(periodo_str[-2:])
                            if month + 1 > 12:
                                month = 1
                                year = year + 1
                            else:
                                month = month + 1
                            if month < 10:
                                periodo_str = str(year) + '/0' + str(month)
                            else:
                                periodo_str = str(year) + '/' + str(month)

                        total_observacoes = str(len(lista_valores))
                        if len(lista_valores) >= 5:  # Se tem mais que 5 períodos, roda a regressão
                            dictionary['Y'] = lista_valores
                            # Vamos completar o dicionário com as variáveis independentes
                            contador = 1
                            lista_independentes = []
                            while True:
                                chave_variavel = 'X' + str(contador)
                                if TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm,
                                                                                     variavel=chave_variavel,
                                                                                     flag=True).count() >= 1:
                                    lista_independentes.append(chave_variavel)
                                    periodo_str = inicio_periodo
                                    lista_valores = []
                                    while periodo_str <= fim_periodo:
                                        if TbRegressaoLinearMultiplaDaugther1.objects.filter(mae_id=id_rlm,
                                                                                             ano_mes=periodo_str,
                                                                                             variavel=chave_variavel,
                                                                                             flag=True).count() == 1:
                                            lista_valores.append(float(
                                                TbRegressaoLinearMultiplaDaugther1.objects.get(mae_id=id_rlm,
                                                                                               ano_mes=periodo_str,
                                                                                               variavel=chave_variavel,
                                                                                               flag=True).indicador))

                                        year = int(periodo_str[:4])
                                        month = int(periodo_str[-2:])
                                        if month + 1 > 12:
                                            month = 1
                                            year = year + 1
                                        else:
                                            month = month + 1
                                        if month < 10:
                                            periodo_str = str(year) + '/0' + str(month)
                                        else:
                                            periodo_str = str(year) + '/' + str(month)

                                    dictionary[chave_variavel] = lista_valores
                                    contador = contador + 1
                                else:
                                    break
                            # print(dictionary)

                            # Calculando refressão linear múltipla

                            tb_1 = pd.DataFrame(data=dictionary)

                            Y = tb_1['Y']
                            X = tb_1[lista_independentes]

                            # X = sm.add_constant(X)
                            X = sm.add_constant(X, has_constant='add')

                            reg = sm.OLS(Y, X).fit()

                            sumario = str(reg.summary())

                            # Vamos montar a equação da regressão linear múltipla
                            equacao_regressao = 'Y = '
                            for x in range(0, contador):
                                if x == 0:  # É o valor da constante
                                    valor_constante = reg.params.loc['const']
                                    if valor_constante < 0:
                                        equacao_regressao = equacao_regressao + ' -' + locale.format_string('%.4f',
                                                                                                            -valor_constante,
                                                                                                            True)
                                    else:
                                        equacao_regressao = equacao_regressao + ' ' + locale.format_string('%.4f',
                                                                                                           valor_constante,
                                                                                                           True)
                                else:
                                    # Variáveis independentes
                                    chave_variavel = 'X' + str(x)
                                    coeficiente_variavel = reg.params.loc[chave_variavel]
                                    if coeficiente_variavel < 0:
                                        equacao_regressao = equacao_regressao + ' - ' + locale.format_string('%.4f',
                                                                                                             -coeficiente_variavel,
                                                                                                             True) + chave_variavel
                                    else:
                                        equacao_regressao = equacao_regressao + ' + ' + locale.format_string('%.4f',
                                                                                                             coeficiente_variavel,
                                                                                                             True) + chave_variavel

                            r2_regressao = reg.rsquared

                            # Vamos verificar se tivemos pvalue = nan. Se sim, significa que todas de variáveis acima do total de observações.
                            objeto_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm,
                                                                                         tipo_variavel='X', flag=True)
                            pvalue_nan = False
                            for objeto in objeto_qs:
                                chave_variavel = objeto.tipo_variavel + str(objeto.agrupamento)
                                pt_variavel = reg.pvalues.loc[chave_variavel]
                                if math.isnan(pt_variavel):
                                    pvalue_nan = True
                                    break

                            if not pvalue_nan:
                                status_regressao = 'OK. CALCULADO'
                                # Vamos atualizar os coeficientes e pt
                                for objeto in objeto_qs:
                                    chave_variavel = objeto.tipo_variavel + str(objeto.agrupamento)
                                    coeficiente_variavel = reg.params.loc[chave_variavel]
                                    objeto.coeficiente = coeficiente_variavel
                                    pt_variavel = reg.pvalues.loc[chave_variavel]
                                    objeto.pt = pt_variavel
                                    objeto.save()
                            else:
                                status_regressao = 'NOK. CÁLCULO DA REGRESSÃO NÃO FOI REALIZADO. QTDE DE OBSERVAÇÕES(' + total_observacoes + ') X VARIÁVEIS(' + str(
                                    contador) + ') NÃO É SUFICIENTE.'
                                equacao_regressao = ''
                                r2_regressao = 0
                                sumario = ''

                            '''
                            # Vamos atualizar os coeficientes e pt
                            objeto_qs = TbRegressaoLinearMultiplaDaugther.objects.filter(mae_id=id_rlm, tipo_variavel='X', flag=True)
                            for objeto in objeto_qs:
                                chave_variavel = objeto.tipo_variavel + str(objeto.agrupamento)
                                coeficiente_variavel = reg.params.loc[chave_variavel]
                                pt_variavel = reg.pvalues.loc[chave_variavel]
                                objeto.coeficiente = coeficiente_variavel
                                objeto.pt = pt_variavel
                                objeto.save()
                            '''

                        else:
                            # status_regressao = status_regressao + '\n' + 'TEMOS SOMENTE ' + str(len(lista_valores)) + ' CONJUNTO(S) DE DADOS. NECESSÁRIO NO MÍNIMO 5 (CINCO).'
                            status_regressao = 'NOK. TEMOS SOMENTE ' + str(
                                len(lista_valores)) + ' CONJUNTO(S) DE DADOS. NECESSÁRIO NO MÍNIMO 5 (CINCO).'

                    else:
                        # status_regressao = status_regressao + '\n' + 'AGUARDANDO AGRUPAMENTO DAS VARIAVEIS INDEPENDENTES.'
                        status_regressao = 'NOK. AGUARDANDO AGRUPAMENTO DAS VARIAVEIS INDEPENDENTES.'
                else:
                    # status_regressao = status_regressao + '\n' + 'AGUARDANDO INDICAÇÃO DAS VARIÁVEIS INDEPENDENTES(X).'
                    status_regressao = 'NOK. AGUARDANDO INDICAÇÃO DAS VARIÁVEIS INDEPENDENTES(X).'
            else:
                # status_regressao = status_regressao + '\n' + 'AGUARDANDO INDICAÇÃO DAS VARIÁVEIS DEPENDENTES(Y).'
                status_regressao = 'NOK. AGUARDANDO INDICAÇÃO DAS VARIÁVEIS DEPENDENTES(Y).'
        else:
            # status_regressao = status_regressao + '\n' + 'TABELA VARIÁVEIS NÃO FOI CRIADA.'
            status_regressao = 'NOK. TABELA VARIÁVEIS NÃO FOI CRIADA.'
    else:
        # status_regressao = status_regressao + 'AGUARDANDO INDICAÇÃO DO GRUPO MÁQUINA.'
        status_regressao = 'NOK. AGUARDANDO INDICAÇÃO DO GRUPO MÁQUINA.'
    # Salvando status da regressão via cursor para evitar recursão no save
    cursor = connection.cursor()
    sql = "update custo_ferbasa_tbregressaolinearmultipla set reg_lin_mul_status = '" + status_regressao + "', reg_lin_mul_equacao_regressao = '" + equacao_regressao + "', reg_lin_mul_r2_regressao = " + str(
        r2_regressao) + ", reg_lin_mul_sumario = '" + sumario + "' where id = " + str(id_rlm)
    cursor.execute(sql)
    cursor.close()


@shared_task
def calcular_consumo_especifico_periodo(ano_mes_inicio, ano_mes_fim):
    cursor = connection.cursor()
    sql = "call public.consumo_especifico_periodo('" + ano_mes_inicio + "', '" + ano_mes_fim + "')"
    cursor.execute(sql)
    cursor.close()


@shared_task
def calcular_custo_variavel_adicionado_periodo(ano_mes_inicio, ano_mes_fim):
    cursor = connection.cursor()
    sql = "call public.custo_variavel_adicionado_periodo('" + ano_mes_inicio + "', '" + ano_mes_fim + "')"
    cursor.execute(sql)
    cursor.close()