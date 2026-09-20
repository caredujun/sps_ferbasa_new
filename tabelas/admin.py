import csv
from django.utils.translation import gettext_lazy as _
import os
import boto3
import \
    xlwt  # xlwt para exportar para Excel formato .xls / xlrd para importar do Excel formato .xls. De acordo com internet é questão de segurança.
from boto3 import Session
from django.forms import TextInput, Textarea
from xlrd.book import open_workbook_xls
from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponse

from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from .forms import *
from .tasks import update_custo_variavel_adicionado
from parameters.models import TbCenarios
from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos
from produtos.models import TbProdutoMercadoPreco, TbMercadoOutbound
from django_object_actions import DjangoObjectActions
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME


class _EmpresaFiltradaAdminMixin:
    """
    🌟 NOVO (multi-empresa, Parte 3): filtra a listagem pela empresa
    EFETIVA do usuário logado (empresa fixa dele, ou a empresa_ativa
    escolhida, se for superusuário) -- mesmo padrão já usado em
    TbCenariosAdmin/TbEmpresaAdmin. Reaproveitado pelas 8 telas de
    tabelas independentes de cenário desse arquivo, pra não repetir a
    mesma lógica em cada uma.
    """
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            return qs.none()
        empresa_id = perfil.empresa_efetiva_id()
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)


import io
from django.http import FileResponse
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from datetime import date
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.lib import colors
import textwrap
from django.contrib.auth.models import User, Group


class TbIndicadoresDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor')
    readonly_fields = ('display_order',)

    model = TbIndicadoresDaugther
    form = TbIndicadoresDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbIndicadoresAdmin(admin.ModelAdmin):
    fields = (('ind_nome', 'ind_observacao'), 'ind_fonte',)
    list_display = ['ind_nome', 'ind_observacao']
    list_display_links = ['ind_nome']

    actions = ['exportar_excel', 'importar_excel', 'importar_excel_new', 'exportar_pdf', ]

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.change_tbindicadores'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel_new':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbIndicadores.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)

        return super(TbIndicadoresAdmin, self).changelist_view(request, extra_context)

    def exportar_pdf(self, request, queryset):

        # Vamos ver o tipo de período
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            tipo_periodo = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            tipo_periodo = 'Ano/Trimestre'
        else:
            tipo_periodo = 'Ano/Mês'

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        # Create a file-like buffer to receive PDF data.
        buffer = io.BytesIO()

        # Create the PDF object, using the buffer as its "file."
        p = canvas.Canvas(buffer)

        # Definindo padrões a serem usados no PDF
        nome_arquivo = 'Tabelas - Indicadores - ' + 'Cenário ' + cen_ativo + ' - PDF'

        largura_pagina = 210 * mm  # Padrão A4
        altura_pagina = 297 * mm  # Padrão A4
        titulo_relatorio = 'Tabelas - Indicadores'

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4

        # Vamos pegar o logo da empresa e colocar no cabeçalho do relatório
        logo_empresa = TbEmpresa.objects.get(id=1).emp_logo
        logo_empresa = mark_safe(
            '%s' % logo_empresa.url)  # Retorna a url do logoda empresa no AWS S3 ou do computador local no caso de desenvolvimento. Descobri tentando. Não achei na internet.

        # Vamos desenhar um registro por página
        # Vamos montar uma queryset com o id dos registros selecionados
        lista_id = queryset.values_list('id', )

        for registro_id in lista_id:

            p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

            # Cabeçalho do PDF
            # Setando tamanho do título e escrevendo os títulos no topo da página
            p.setFont("Helvetica-Bold", 18)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 45,
                                'Cenário : ' + cen_ativo + ' / ' + cen_ativo_nome)

            # Vamos colocar o logo da empresa e passar uma linha embaixo
            p.drawImage(logo_empresa, 10, altura_pagina - 50, width=50, height=40, mask=None)
            p.line(5, altura_pagina - 55, largura_pagina - 5, altura_pagina - 55)

            # Vamos colocar as informações do registro da tabela
            line_number = altura_pagina - 68
            p.setFont("Helvetica-Bold", 10)

            # Temos o id do registro. Vamos pegar as outras informações
            registro_nome = TbIndicadores.objects.get(id=registro_id[0]).ind_nome
            registro_observacao = TbIndicadores.objects.get(id=registro_id[0]).ind_observacao

            p.drawCentredString(largura_pagina / 2, line_number, "INDICADOR")
            line_number = line_number - 20

            p.drawString(10, line_number, 'ID: ' + str(registro_id[0]))
            p.drawString(0.1 * largura_pagina, line_number, 'Nome: ' + registro_nome)
            p.drawString(0.4 * largura_pagina, line_number, 'Observação: ')
            # Vamos quebrar o campo de observação
            for line in textwrap.wrap(registro_observacao, 50):
                p.drawString(0.55 * largura_pagina, line_number, line)
                line_number = line_number - 20
            # p.drawString(0.42 * largura_pagina, line_number, 'Observação: ' + registro_observacao)

            line_number = line_number - 30
            p.drawString(0.40 * largura_pagina, line_number, tipo_periodo)
            p.drawString(0.55 * largura_pagina, line_number, 'Valor (%)')

            # Vamos criar a filha
            filha = TbIndicadoresDaugther.objects.filter(mae_id=registro_id[0]).order_by('dau_order')
            rows = filha.values_list('dau_order', 'dau_valor').order_by('dau_order')

            # Dados para o gráfico
            data = []
            rotulo_x = []

            for row in rows:
                line_number = line_number - 20
                # Vamos agora alterar o dau_order
                # Temos que pegar o inicio do cenário
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                    # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                    inicio_periodo = inicio_periodo[:4]
                    periodo_str = str(int(inicio_periodo) - 1 + row[0])

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                    year = int(inicio_periodo[:4])
                    month = int(inicio_periodo[-2:])

                    for j in range(row[0] - 1):
                        if month + 1 > 12:
                            month = 1
                            year = year + 1
                        else:
                            month = month + 1
                    if month < 10:
                        periodo_str = str(year) + '/0' + str(month)
                    else:
                        periodo_str = str(year) + '/' + str(month)

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                    year = int(inicio_periodo[:4])
                    quarter = int(inicio_periodo[-2:])
                    for j in range(row[0] - 1):
                        if quarter + 1 > 4:
                            quarter = 1
                            year = year + 1
                        else:
                            quarter = quarter + 1
                    periodo_str = str(year) + '/0' + str(quarter)

                p.drawString(0.40 * largura_pagina, line_number, periodo_str)
                p.drawString(0.55 * largura_pagina, line_number, locale.format_string('%.4f', row[1], True))

                data.append(float(row[1]))

                rotulo_x.append(periodo_str)

            # Gráfico de barras
            drawing = Drawing(largura_pagina - 30, 200)

            # coordinates (from left bottom)
            x = 15
            y = line_number - 250

            bar = VerticalBarChart()
            bar.x = 30
            bar.y = 30
            bar.width = largura_pagina - 80
            bar.height = 150
            bar.valueAxis.valueMin = 0

            data = [data, ]
            bar.data = data

            bar.categoryAxis.categoryNames = rotulo_x
            bar.bars[0].fillColor = colors.green

            drawing.add(bar, '')
            renderPDF.draw(drawing, p, x, y, showBoundary=True)

            p.setFont("Helvetica-Bold", 14)
            p.drawCentredString(largura_pagina / 2, y + 210, "Valor (%)")

            p.setFont("Helvetica-Oblique", 8)
            p.drawCentredString(largura_pagina / 2, 10,
                                "Copyright © 2022 SPS Consultoria. Todos os direitos reservados.")

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

    exportar_pdf.short_description = _('Exportar PDF')

    def importar_excel_new(self, request, queryset):
        try:
            # Para permitit acesso ao AWS S3
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'indicadores_new.xls'

            """
            # Vamos salvar o arquivo Excel no AWS S3
            session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

            s3 = session.resource('s3')

            object = s3.Object(bucket_name, object_key)

            result = object.put(Body=open('c:/sps/excel/' + object_key, 'rb'))

            res = result.get('ResponseMetadata')

            # if res.get('HTTPStatusCode') == 200:
            #    print('File Uploaded Successfully')
            # else:
            #    print('File Not Uploaded')
            """

            # Vamos abrir o arquivo Excel que foi gravado no AWS S3
            s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
            content = bucket_object.get()['Body'].read()

            wb = open_workbook_xls(file_contents=content)

            sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba, onde estão as informações a serem copiadas
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_mae = sheet.ncols
            total_linhas_mae = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_mae):
                lista_colunas.append(sheet.cell_value(0, i))

            # if lista_colunas == ['id', 'ind_nome', 'ind_observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id']:
            if lista_colunas == ['Id', 'Nome', 'Observação', 'Valor Inicial', 'Id Origem', 'Id Cenário']:
                #  Cabeçalho da mãe está correto.
                #  Vamos ver se o cenário informado na mãe é o cenário ativo.
                # Cenário ativo
                ok_cenario = True
                cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba
                for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 5
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        if sheet.cell_value(i, 5) != cen_ativo:
                            ok_cenario = False
                            break

                if not ok_cenario:
                    # Na realidade poderiamos desconsiderar essa consistência e assumir o cenário ativo.
                    # Estamos fazendo dessa forma para forçar que na coluna de ordem 5 seja informado o cenário ativo.
                    messages.error(request, _('Cenário informado na planilha não é o ativo. Favor verificar!'))
                else:
                    # Tudo ok até aqui.
                    # Vamos ver se a informação é nova.
                    # Nesse caso temos que ver se o nome do indicador é novo. Informação está na coluna de ordem 1
                    existe = False
                    for i in range(total_linhas_mae):
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if TbIndicadores.objects.filter(ind_nome=sheet.cell_value(i, 1),
                                                            tbcenarios_id=cen_ativo).count() > 0:
                                existe = True
                                break

                    if not existe:
                        # Tudo ok até aqui. Podemos continuar
                        # Vamos agora incluir o registro e as filhas a partir do valor inicial informado.
                        # Se foi informado algum valor na coluna id nós desconsideramos.
                        for i in range(total_linhas_mae):
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                TbIndicadores.objects.create(ind_nome=sheet.cell_value(i, 1),
                                                             ind_observacao=sheet.cell_value(i, 2),
                                                             valor_inicial=sheet.cell_value(i, 3),
                                                             tbcenarios_id=cen_ativo)

                                # Temos que pegar o id da mãe para lançar na filha
                                id_mae = TbIndicadores.objects.get(ind_nome=sheet.cell_value(i, 1)).id

                                # Vamos atualizar a filha usando o Stored Procedure verifica_filha
                                cursor = connection.cursor()
                                # Montando a expressão sql para rodar o Stored Procedure verifica_filha
                                sql = "call public.verifica_filha('tabelas_" + 'tbindicadores' + "daugther', " + str(
                                    id_mae) + ", " + str(cen_ativo) + ", " + str(sheet.cell_value(i, 3)) + ")"
                                cursor.execute(sql)
                                cursor.close()

                        messages.success(request, _('Novo(s) indicador(es) foram adicionados com sucesso.'))
                        # Deletando arquivo no AWS S3
                        s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
                        s3_client.delete_object(Bucket=bucket_name, Key=object_key)

                    else:
                        messages.error(request, 'Já existe o indicador ' + sheet.cell_value(i,
                                                                                            1) + ' cadastrado para esse cenário. Favor verificar!')

            else:
                messages.error(request, _('Cabeçalho do arquivo Excel não está correto. Favor verificar!'))

        except:
            messages.error(request,
                           _('Não foi encontrado o arquivo indicadores_new.xls no diretório c:\sps\excel ou AWS S3. Favor verificar!'))

    importar_excel_new.short_description = _('Importar Excel (Novos)')

    def importar_excel(self, request, queryset):
        # try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Indicadores_Mãe_Filhas_Cenário_' + cen_ativo + '.xls'

        bucket_name = 'spsferbasa'

        object_key = nome_arquivo

        """
        # Vamos salvar o arquivo Excel no AWS S3

        session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

        s3 = session.resource('s3')

        object = s3.Object(bucket_name, object_key)

        result = object.put(Body=open('c:/sps/excel/' + object_key, 'rb'))

        res = result.get('ResponseMetadata')

        if res.get('HTTPStatusCode') == 200:
            print('File Uploaded Successfully')
        else:
            print('File Not Uploaded')
        """

        # Vamos abrir o arquivo Excel que foi gravado no AWS S3
        s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
        bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
        content = bucket_object.get()['Body'].read()

        wb = open_workbook_xls(file_contents=content)

        sheet = wb.sheet_by_index(0)  # Abrindo a mãe
        # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
        total_colunas_mae = sheet.ncols
        total_linhas_mae = sheet.nrows
        lista_colunas = []
        for i in range(total_colunas_mae):
            # o nome das colunas estão na linha 0
            lista_colunas.append(sheet.cell_value(0, i))

        # if lista_colunas == ['id', 'ind_nome', 'ind_observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id']:
        if lista_colunas == ['Id', 'Nome', 'Observação', 'Id Cenário']:
            #  Cabeçalho da mãe está correto. Vamos agora ver a filha
            sheet = wb.sheet_by_index(1)  # Abrindo a filha
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_filha = sheet.ncols
            total_linhas_filha = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_filha):
                lista_colunas.append(sheet.cell_value(0, i))

            # if lista_colunas == ['id', 'dau_order', 'dau_valor', 'mae_id', 'tbcenarios_id']:
            if lista_colunas == ['Id', 'Ordem', 'Valor (%)', 'Id Mãe']:

                #  Filha também com cabeçalho correto. Podemos continuar
                #  Vamos ver se o total de lançamentos na filha estão de acordo com o total de mães. Vamos precisar do total de períodos do cenário ativo.
                #  Vamos obter o total de períodos para o cenário ativo
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                # Na filha tiramos um linha que é o cabeçalho. Na mãe tiramos também 1 que é o cabeçalho.
                if (total_linhas_filha - 1) == (total_linhas_mae - 1) * total_periodos:
                    #  Vamos ver se o cenário informado na mãe é o cenário ativo.
                    # Cenário ativo
                    ok_cenario = True
                    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                    sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                    for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 3
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 3) != cen_ativo:
                                ok_cenario = False
                                break

                    '''# Tiramos porque na filha não tem mais a coluna tbcenarios
                    sheet = wb.sheet_by_index(1)  # Abrindo a filha
                    for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 4
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 4) != cen_ativo:
                                ok_cenario = False
                                break
                    '''

                    if not ok_cenario:
                        messages.error(request,
                                       _('Cenário informado na planilha na aba mãe não é o cenário ativo. Favor verificar!'))
                    else:
                        #  Tudo ok até aqui. Vamos verificar se no arquivo tem as mães selecionadas. E se tiver, vamos ver se tem as filhas no total do período do cenário.
                        #  Vamos pegar o id das mães selecionadas.
                        id_maes = queryset.values_list('id', )
                        tudo_ok = True
                        for id_mae in id_maes:
                            # Temos que pegar a str do id.
                            id_str = TbIndicadores.objects.get(id=id_mae[0]).ind_nome
                            # Vamos ver se tem somente uma mãe na aba mãe
                            sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                            conta_mae = 0
                            for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                if i > 0:  # Porque a linha 0 é o cabeçalho

                                    if sheet.cell_value(i, 0) == id_mae[0]:  # É o id da mãe. Soma no contador.
                                        conta_mae = conta_mae + 1

                            if conta_mae == 0:
                                messages.error(request, 'Na aba mãe do arquivo em Excel não existe o id = ' + str(
                                    id_mae[0]) + ' (' + id_str + '). Favor verificar!')
                                tudo_ok = False
                                break
                            else:
                                if conta_mae > 1:
                                    # Se tiver mais que 1 também é problema
                                    messages.error(request,
                                                   'Na aba mãe do arquivo em Excel tem mais que 1(um) id = ' + str(
                                                       id_mae[0]) + ' (' + id_str + '). Favor verificar!')
                                    tudo_ok = False
                                    break
                                else:
                                    #  Tudo certo até aqui. Podemos continuar.
                                    #  Vamos ver se na tabela filhas existe um total de filhas igual ao total de periodos
                                    sheet = wb.sheet_by_index(1)  # Abrindo a filhas
                                    conta_filha = 0
                                    for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                        if i > 0 and sheet.cell_value(i, 3) == id_mae[
                                            0]:  # É o id da mãe. Soma no contador.
                                            conta_filha = conta_filha + 1

                                    if conta_filha != total_periodos:
                                        messages.error(request, 'Total de filhas para a mãe id = ' + str(
                                            id_mae[0]) + ' (' + id_str + ')  igual a ' + str(
                                            conta_filha) + ', diferente de ' + str(
                                            total_periodos) + '. Favor verificar!')
                                    else:
                                        # Vamos ver agora se o dau_order nas filhas estão na sequência certa
                                        conta_dau_order = 0
                                        ok_dau_order = True
                                        for i in range(
                                                total_linhas_filha):  # A coluna da mãe_id é a de ordem 3 e a do dau_order é o 1
                                            if i > 0:  # Porque a linha 0 é o cabeçalho

                                                if sheet.cell_value(i, 3) == id_mae[
                                                    0]:  # É o id da mãe. Vamos somar no contador e comparar com o dau_order
                                                    conta_dau_order = conta_dau_order + 1
                                                    if conta_dau_order != sheet.cell_value(i, 1):
                                                        ok_dau_order = False
                                                        tudo_ok = False
                                                        break

                                        if not ok_dau_order:
                                            messages.error(request,
                                                           'Sequencial na coluna Ordem na aba Filhas para a mãe id = ' + str(
                                                               id_mae[
                                                                   0]) + ' (' + id_str + ')  está fora da sequência correta. Favor verificar!')
                                        else:
                                            #  Tudo ok até aqui. Podemos continuar
                                            #  Vamos agora atualizar a mãe e as filhas pois tudo está Ok com o arquivo Excel
                                            sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                                            #  id_mae[0] é o id da mãe que foi selecionado
                                            for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                                if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                                    0]:  # Porque a linha 0 é o cabeçalho
                                                    # Observar que se sheet.cell_value(i, 0) for diferente de id_mae[0] não considera
                                                    tab_obj = TbIndicadores.objects.get(id=sheet.cell_value(i, 0))
                                                    tab_obj.ind_nome = sheet.cell_value(i, 1)
                                                    tab_obj.ind_observacao = sheet.cell_value(i, 2)

                                                    tab_obj.save()

                                            # Vamos agora atualizar as filhas
                                            sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                            for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                                if i > 0 and sheet.cell_value(i, 3) == id_mae[0]:  # É o id da mãe.
                                                    # Temos que pegar o dau_order
                                                    dau_order_filha = sheet.cell_value(i, 1)
                                                    tab_obj = TbIndicadoresDaugther.objects.get(
                                                        id=sheet.cell_value(i, 0), mae_id=id_mae[0],
                                                        dau_order=dau_order_filha)
                                                    tab_obj.dau_valor = sheet.cell_value(i, 2)

                                                    tab_obj.save()

                                            '''
                                            # Tiramos pois não é necessário. Colocamos essa rotina no save da tabela.
                                            # Ou seja: se está no Save da tabela, toda vez que salvamos, a tabela roda os procedimentos que estão no Save (está lá no model).
                                            # Temos que verificar quais são as tabelas que estão usando esse indicador e atualizar os valores
                                            # Fazemos isso rodando um stored procedure chamado Atualiza_Indicador passando para o mesmo o ID do indicador
                                            # que no caso é o id_mae[0]
                                            cursor = connection.cursor()
                                            sql = "call public.atualiza_indicador(" + str(id_mae[0]) + ")"
                                            cursor.execute(sql)
                                            cursor.close()
                                            '''
                        if tudo_ok:
                            messages.success(request, _('Tabela Indicadores foi atualizada com sucesso!'))
                            # Deletando arquivo no AWS S3
                            s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
                            s3_client.delete_object(Bucket=bucket_name, Key=object_key)
                            # Observar que se na tabela em Excel tiver indicador com id diferente dos selecionados, os mesmos serão desconsiderados na atualização.

                else:
                    messages.error(request,
                                   _('Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!'))

            else:
                messages.error(request, _('Cabeçalho da aba filha não está correto. Favor verificar!'))

        else:
            messages.error(request, _('Cabeçalho da aba mãe não está correto. Favor verificar!'))

        # except:
        #    messages.error(request, 'Não foi encontrado o arquivo ' + nome_arquivo + ' no AWS S3. Favor verificar!')

    importar_excel.short_description = _('Importar Excel')

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados.

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Indicadores_Mãe_Filhas_Cenário_' + cen_ativo + '.xls'
        response['Content-Disposition'] = 'attachment; filename=' + nome_arquivo

        wb = xlwt.Workbook(encoding='utf-8')
        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        ws = wb.add_sheet('Mãe')

        # Sheet header, first row
        row_num = 0

        # columns = ['id', 'ind_nome', 'ind_observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id']
        # columns = ['Id', 'Nome', 'Observação', 'Valor Inicial', 'Id Origem', 'Id Cenário']
        columns = ['Id', 'Nome', 'Observação', 'Id Cenário']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id', 'ind_nome', 'ind_observacao', 'tbcenarios_id').order_by('id')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        # Lançando valores das filhas na página filha
        ws = wb.add_sheet('Filhas')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbIndicadoresDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['Id', 'Ordem', 'Valor (%)', 'Id Mãe']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id', 'dau_order', 'dau_valor', 'mae_id').order_by('mae_id', 'dau_order')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar Excel')

    form = TbIndicadoresFormAdmin

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbIndicadoresAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbIndicadoresDaugtherAdmin]


# Registrando
admin.site.register(TbIndicadores, TbIndicadoresAdmin)


# Divisor de tabelas .....................................................................

class TbCambioDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor')
    readonly_fields = ('display_order',)

    model = TbCambioDaugther
    form = TbCambioDaugtherFormAdmin

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 100})},
    }

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbCambioAdmin(admin.ModelAdmin):
    fields = ('cam_moeda', ('cam_moeda_imagem', 'cam_moeda_imagem_tag'), 'cam_observacao', 'cam_fonte')
    list_display = ['id', 'cam_moeda', 'cam_moeda_imagem', 'cam_moeda_imagem_tag', 'cam_observacao']
    list_display_links = ['id', 'cam_moeda']
    readonly_fields = ['cam_moeda_imagem_tag']

    actions = ['exportar_pdf', ]

    def exportar_pdf(self, request, queryset):

        # Vamos ver o tipo de período
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            tipo_periodo = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            tipo_periodo = 'Ano/Trimestre'
        else:
            tipo_periodo = 'Ano/Mês'

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        # Create a file-like buffer to receive PDF data.
        buffer = io.BytesIO()

        # Create the PDF object, using the buffer as its "file."
        p = canvas.Canvas(buffer)

        # Definindo padrões a serem usados no PDF
        nome_arquivo = 'Tabelas - Taxas de Câmbio - PDF'

        largura_pagina = 210 * mm  # Padrão A4
        altura_pagina = 297 * mm  # Padrão A4
        titulo_relatorio = 'Tabelas - Taxas de Câmbio'

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4

        # Vamos pegar o logo da empresa e colocar no cabeçalho do relatório
        logo_empresa = TbEmpresa.objects.get(id=1).emp_logo
        logo_empresa = mark_safe(
            '%s' % logo_empresa.url)  # Retorna a url do logoda empresa no AWS S3 ou do computador local no caso de desenvolvimento. Descobri tentando. Não achei na internet.

        # Vamos desenhar um registro por página
        # Vamos montar uma queryset com o id dos registros selecionados
        lista_id = queryset.values_list('id', )

        for registro_id in lista_id:

            p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

            # Cabeçalho do PDF
            # Setando tamanho do título e escrevendo os títulos no topo da página
            p.setFont("Helvetica-Bold", 18)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 45,
                                'Cenário : ' + cen_ativo + ' / ' + cen_ativo_nome)

            # Vamos colocar o logo da empresa e passar uma linha embaixo
            p.drawImage(logo_empresa, 10, altura_pagina - 50, width=50, height=40, mask=None)
            p.line(5, altura_pagina - 55, largura_pagina - 5, altura_pagina - 55)

            # Vamos colocar as informações do registro da tabela
            line_number = altura_pagina - 68
            p.setFont("Helvetica-Bold", 10)

            # Temos o id do registro. Vamos pegar as outras informações
            registro_moeda = TbCambio.objects.get(id=registro_id[0]).cam_moeda
            registro_observacao = TbCambio.objects.get(id=registro_id[0]).cam_observacao
            registro_imagem = TbCambio.objects.get(id=registro_id[0]).cam_moeda_imagem
            registro_imagem = mark_safe('%s' % registro_imagem.url)

            p.drawCentredString(largura_pagina / 2, line_number, "TAXA DE CÂMBIO")
            line_number = line_number - 20

            p.drawString(10, line_number, 'ID: ' + str(registro_id[0]))
            p.drawString(0.075 * largura_pagina, line_number, 'Nome: ' + registro_moeda)
            p.drawString(0.18 * largura_pagina, line_number, 'Observação: ')
            p.drawImage(registro_imagem, 0.75 * largura_pagina, line_number - 60, width=140, height=70, mask=None)
            # Vamos quebrar o campo de observação
            for line in textwrap.wrap(registro_observacao, 50):
                p.drawString(0.29 * largura_pagina, line_number, line)
                line_number = line_number - 15

            line_number = line_number - 25
            p.drawString(0.40 * largura_pagina, line_number, tipo_periodo)
            p.drawString(0.55 * largura_pagina, line_number, 'Valor')

            # Vamos criar a filha
            filha = TbCambioDaugther.objects.filter(mae_id=registro_id[0]).order_by('dau_order')
            rows = filha.values_list('dau_order', 'dau_valor').order_by('dau_order')

            # Dados para o gráfico
            data = []
            rotulo_x = []

            for row in rows:
                line_number = line_number - 20
                # Vamos agora alterar o dau_order
                # Temos que pegar o inicio do cenário
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                    # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                    inicio_periodo = inicio_periodo[:4]
                    periodo_str = str(int(inicio_periodo) - 1 + row[0])

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                    year = int(inicio_periodo[:4])
                    month = int(inicio_periodo[-2:])

                    for j in range(row[0] - 1):
                        if month + 1 > 12:
                            month = 1
                            year = year + 1
                        else:
                            month = month + 1
                    if month < 10:
                        periodo_str = str(year) + '/0' + str(month)
                    else:
                        periodo_str = str(year) + '/' + str(month)

                if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                    year = int(inicio_periodo[:4])
                    quarter = int(inicio_periodo[-2:])
                    for j in range(row[0] - 1):
                        if quarter + 1 > 4:
                            quarter = 1
                            year = year + 1
                        else:
                            quarter = quarter + 1
                    periodo_str = str(year) + '/0' + str(quarter)

                p.drawString(0.40 * largura_pagina, line_number, periodo_str)
                p.drawString(0.55 * largura_pagina, line_number, locale.format_string('%.4f', row[1], True))

                data.append(float(row[1]))

                rotulo_x.append(periodo_str)

            # Gráfico de barras
            drawing = Drawing(largura_pagina - 30, 200)

            # coordinates (from left bottom)
            x = 15
            y = line_number - 250

            bar = VerticalBarChart()
            bar.x = 30
            bar.y = 30
            bar.width = largura_pagina - 80
            bar.height = 150
            bar.valueAxis.valueMin = 0

            data = [data, ]
            bar.data = data

            bar.categoryAxis.categoryNames = rotulo_x
            bar.bars[0].fillColor = colors.green

            drawing.add(bar, '')
            renderPDF.draw(drawing, p, x, y, showBoundary=True)

            p.setFont("Helvetica-Bold", 14)
            p.drawCentredString(largura_pagina / 2, y + 210, "Valor")

            p.setFont("Helvetica-Oblique", 8)
            p.drawCentredString(largura_pagina / 2, 10,
                                "Copyright © 2022 SPS Consultoria. Todos os direitos reservados.")

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

    exportar_pdf.short_description = _('Exportar PDF')

    form = TbCambioFormAdmin

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 100})},
    }

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbCambioAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    '''
    def has_delete_permission(self, request, obj=None):
        if request.POST and request.POST.get('action') == 'delete_selected':
            if '1' in request.POST.getlist('_selected_action'):
                messages.add_message(request, messages.ERROR, ("ID = 1 is protected, please remove it from your selection and try again."))
                return False
            return True
        return obj is None or obj.pk != 1
    '''

    def has_delete_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = True
        return issuperuser

    # Vamos verificar se é possivel deletar o registro. Só será possível se não estiver sendo usado por outra tabela
    def has_delete_permission(self, request, obj=None):
        # Vamos ver se o usuário tem permissão para deletar o registro
        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.delete_tbcambio'):
            return False
        else:
            retorno = True
            requerente = str(request)
            if '/admintabelas/tbcambio/' in requerente:
                # Vamos ver se o id da taxa de câmbio está sendo usada por alguma tabela do cenário ativo
                while True:
                    if obj is not None:
                        # Custo Fixo
                        if TbCustoFixo.objects.filter(fix_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Depreciação e Amortização
                        if TbDepreAmorti.objects.filter(dep_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Capex
                        if TbCapex.objects.filter(cap_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Custo Item Preço
                        if TbCustoItemPreco.objects.filter(cus_ite_pre_moeda_preco=obj.cam_moeda).count() > 0:
                            retorno = False
                            break
                        if TbCustoItemPreco.objects.filter(cus_ite_pre_moeda_inbound=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Equação Ajuste de Preço
                        if TbEquacaoAjustePreco.objects.filter(equ_aju_pre_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Equação Ajuste de Preço
                        if TbEquipamentosCadastro.objects.filter(equ_cad_moeda_manutencao=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Produtos Mercado Preços
                        if TbProdutoMercadoPreco.objects.filter(pro_mer_pre_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                        # Outbound
                        if TbMercadoOutbound.objects.filter(mer_out_moeda=obj.cam_moeda).count() > 0:
                            retorno = False
                            break

                    break

            # return obj is None or retorno
            return retorno

    # Vamos remover a opção de deletar registros a partir da listagem
    # Assim de quiser deletar tem que abrir o registro.
    # Se o mesmo estiver sendo usado por alguma tabela, não irá aparecer a opção de Deletar
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra o campo cam_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbCambioDaugtherAdmin]


# Registrando
admin.site.register(TbCambio, TbCambioAdmin)


# Divisor de tabelas .....................................................................

class TbImpostoRendaDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor')
    readonly_fields = ('display_order',)

    model = TbImpostoRendaDaugther
    form = TbImpostoRendaDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbImpostoRendaAdmin(admin.ModelAdmin):
    fields = ('imp_observacao',)
    list_display = ['imp_observacao']

    # Cria os registros para os cenários As Is (Mensal, Trimestral e Anual se não existirem)
    # Temos que primeiro ver se a tabela TbImpostoRenda existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'tabelas_tbimpostorenda' in all_tables:
        # Existe. Vamos ver se os registros
        qtde = TbImpostoRenda.objects.count()

        if TbImpostoRenda.objects.filter(id=1).count() == 0:
            emp = TbImpostoRenda.objects.create(id=1,
                                                imp_observacao='IMPOSTO DE RENDA. Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=1)
            # Vamos atualizar as filhas usando o procedure verifica_filha
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbimpostorendadaugther', " + str(1) + ", " + str(
                1) + ", '32'" + ")"
            cursor.execute(sql)
            cursor.close()

        if TbImpostoRenda.objects.filter(id=2).count() == 0:
            emp = TbImpostoRenda.objects.create(id=2,
                                                imp_observacao='IMPOSTO DE RENDA. Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=2)
            # Vamos atualizar as filhas usando o procedure verifica_filha
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbimpostorendadaugther', " + str(2) + ", " + str(
                2) + ", '32'" + ")"
            cursor.execute(sql)
            cursor.close()

        if TbImpostoRenda.objects.filter(id=3).count() == 0:
            emp = TbImpostoRenda.objects.create(id=3,
                                                imp_observacao='IMPOSTO DE RENDA. Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=3)
            # Vamos atualizar as filhas usando o procedure verifica_filha
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbimpostorendadaugther', " + str(3) + ", " + str(
                3) + ", '32'" + ")"
            cursor.execute(sql)
            cursor.close()

        # Vamos ajustar a sequência da tabela tabelas_tbimpostorenda
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure ajustar_sequencia
        sql = "call public.ajustar_sequencia('tabelas_tbimpostorenda'" + ")"
        cursor.execute(sql)
        cursor.close()

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 100})},
    }

    # Tabela tipo parâmetro.
    def has_delete_permission(self, request, obj=None):
        return True

    # Vamos ver se usuário é superuser. Se sim, permite adição na tabela se não tiver dados
    def has_add_permission(self, request, obj=None):
        current_user = request.user

        # Vamos ver se tem registro na tabela empresa
        qtde = TbImpostoRenda.objects.count()  # ou pode usar qtde = len(TbEmpresa.objects.all())

        if current_user.is_superuser and qtde == 0:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    # Removemos pois permissões foram definidas a nível de grupo
    '''
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbImpostoRendaAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    inlines = [TbImpostoRendaDaugtherAdmin]


# Registrando
admin.site.register(TbImpostoRenda, TbImpostoRendaAdmin)


class TbTaxaDescontoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor')
    readonly_fields = ('display_order',)

    model = TbTaxaDescontoDaugther
    form = TbTaxaDescontoDaugtherFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbTaxaDescontoAdmin(admin.ModelAdmin):
    fields = ('tax_observacao',)
    list_display = ['tax_observacao']

    # Cria os registros para os cenários As Is (Mensal, Trimestral e Anual se não existirem)
    # Temos que primeiro ver se a tabela TbTaxaDesconto existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'tabelas_tbtaxadesconto' in all_tables:
        # Existe. Vamos ver se tem registro
        qtde = TbTaxaDesconto.objects.count()

        if TbTaxaDesconto.objects.filter(id=1).count() == 0:
            tax = TbTaxaDesconto.objects.create(id=1,
                                                tax_observacao='TAXA DESCONTO (WACC). Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=1)
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbtaxadescontodaugther', " + str(1) + ", " + str(
                1) + ", '10'" + ")"
            cursor.execute(sql)
            cursor.close()

        if TbTaxaDesconto.objects.filter(id=2).count() == 0:
            tax = TbTaxaDesconto.objects.create(id=2,
                                                tax_observacao='TAXA DESCONTO (WACC). Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=2)
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbtaxadescontodaugther', " + str(2) + ", " + str(
                2) + ", '10'" + ")"
            cursor.execute(sql)
            cursor.close()

        if TbTaxaDesconto.objects.filter(id=3).count() == 0:
            tax = TbTaxaDesconto.objects.create(id=3,
                                                tax_observacao='TAXA DESCONTO (WACC). Registro criado pelo sistema. CLICK AQUI para mostrar e ajustar se necessário.',
                                                tbcenarios_id=3)
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
            sql = "call public.verifica_filha('tabelas_tbtaxadescontodaugther', " + str(3) + ", " + str(
                3) + ", '10'" + ")"
            cursor.execute(sql)
            cursor.close()

        # Vamos ajustar a sequência da tabela tabelas_tbtaxadesconto
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure ajustar_sequencia
        sql = "call public.ajustar_sequencia('tabelas_tbtaxadesconto'" + ")"
        cursor.execute(sql)
        cursor.close()

    # Tabela tipo parâmetro.
    def has_delete_permission(self, request, obj=None):
        return True
    # Vamos ver se usuário é superuser. Se sim, permite adição na tabela se não tiver dados
    def has_add_permission(self, request, obj=None):
        current_user = request.user

        # Vamos ver se tem registro na tabela empresa
        qtde = TbTaxaDesconto.objects.count()  # ou pode usar qtde = len(TbEmpresa.objects.all())

        if current_user.is_superuser and qtde == 0:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    # Removemos pois permissões foram definidas a nível de grupos
    '''
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbTaxaDescontoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    inlines = [TbTaxaDescontoDaugtherAdmin]


# Registrando
admin.site.register(TbTaxaDesconto, TbTaxaDescontoAdmin)


# Divisor de tabelas ....................................................................

class TbCustoFixoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'valor_moeda_empresa', 'dau_valor_2')
    readonly_fields = ('display_order', 'valor_moeda_empresa',)

    model = TbCustoFixoDaugther
    form = TbCustoFixoDaugtherFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbCustoFixoAdmin(admin.ModelAdmin):
    fields = ('fix_nome', ('fix_indicador', 'fix_moeda'), 'fix_observacao', 'fix_fonte')
    list_display = ['fix_nome', 'fix_indicador', 'fix_moeda', 'fix_observacao']

    form = TbCustoFixoFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbCustoFixoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo valor_inicial_1 e valor_inicial_2 somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando, irá aparecer o(s) campo(s) para informar o valor(es) inicial(ais)
            return self.fields + ('valor_inicial_1', 'valor_inicial_2')
        return self.fields

    inlines = [TbCustoFixoDaugtherAdmin]


# Registrando
admin.site.register(TbCustoFixo, TbCustoFixoAdmin)


# Divisor de tabelas ....................................................................

class TbDepreAmortiDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor', 'valor_moeda_empresa')
    readonly_fields = ('display_order', 'valor_moeda_empresa',)

    model = TbDepreAmortiDaugther
    form = TbDepreAmortiDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbDepreAmortiAdmin(admin.ModelAdmin):
    fields = (('dep_nome', 'dep_recorrente'), ('dep_indicador', 'dep_moeda'), 'dep_observacao', 'dep_fonte')
    list_display = ['dep_nome', 'dep_recorrente', 'dep_indicador', 'dep_moeda', 'dep_observacao']

    form = TbDepreAmortiFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbDepreAmortiAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo ind_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbDepreAmortiDaugtherAdmin]


# Registrando
admin.site.register(TbDepreAmorti, TbDepreAmortiAdmin)


class TbCapexDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor', 'valor_moeda_empresa')
    readonly_fields = ('display_order', 'valor_moeda_empresa',)

    model = TbCapexDaugther
    form = TbCapexDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbCapexAdmin(admin.ModelAdmin):
    fields = (('cap_nome', 'cap_recorrente'), ('cap_indicador', 'cap_moeda'), 'cap_observacao', 'cap_fonte')
    list_display = ['cap_nome', 'cap_recorrente', 'cap_indicador', 'cap_moeda', 'cap_observacao']

    form = TbCapexFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbCapexAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo ind_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbCapexDaugtherAdmin]


# Registrando
admin.site.register(TbCapex, TbCapexAdmin)


# Divisor de tabelas ....................................................................

class TbUnidadeProducaoAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = (
    'uni_nome', ('uni_imagem', 'uni_imagem_tag'), ('uni_localizacao', 'uni_localizacao_tag'), 'uni_observacao')
    list_display = ['id', 'uni_nome', 'empresa', 'uni_imagem', 'uni_imagem_tag_small', 'uni_localizacao',
                    'uni_localizacao_tag_small']
    readonly_fields = ['uni_imagem_tag', 'uni_imagem_tag_small', 'uni_localizacao_tag', 'uni_localizacao_tag_small']
    list_display_links = ['id', 'uni_nome']

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }


admin.site.register(TbUnidadeProducao, TbUnidadeProducaoAdmin)


class TbMercadoAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    form = TbMercadoFormAdmin

    fields = ('mer_nome', ('mer_imagem', 'mer_imagem_tag'), 'mer_observacao')
    list_display = ['id', 'mer_nome', 'empresa', 'mer_imagem', 'mer_imagem_tag_small', 'mer_observacao']
    list_display_links = ['id', 'mer_nome']
    readonly_fields = ['mer_imagem_tag', 'mer_imagem_tag_small']

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }


admin.site.register(TbMercado, TbMercadoAdmin)


class TbCustoTipoAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = ('cus_tip_nome', 'cus_tip_group', 'cus_tip_observacao')
    list_display = ['id', 'cus_tip_nome', 'empresa', 'cus_tip_observacao']
    list_display_links = ['id', 'cus_tip_nome']
    filter_horizontal = ('cus_tip_group',)

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }


admin.site.register(TbCustoTipo, TbCustoTipoAdmin)


class TbCustoItemAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = (('cus_ite_nome', 'cus_ite_codigo_interno'), ('cus_ite_unidade', 'cus_ite_tipo'), 'cus_ite_imagem',
              'cus_ite_imagem_tag', 'cus_ite_observacao')
    list_display = ['id', 'cus_ite_nome', 'empresa', 'cus_ite_imagem_tag_small', 'cus_ite_imagem', 'cus_ite_tipo']
    readonly_fields = ['cus_ite_imagem_tag']
    list_display_links = ['id', 'cus_ite_nome']
    search_fields = ['cus_ite_nome', ]
    list_filter = ('cus_ite_nome', 'cus_ite_tipo',)

    form = TbCustoItemFormAdmin

    actions = ['exportar_excel', 'importar_excel', 'importar_excel_new']

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.change_tbcustoitem'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def importar_excel_new(self, request, queryset):
        try:
            # Para permitit acesso ao AWS S3
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'custoitem_new.xls'

            """ Removemos pois não funciona no Heroku. Só localmente. Fizemos um .exe para transferir
            # Vamos salvar o arquivo Excel no AWS S3
            session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

            s3 = session.resource('s3')

            object = s3.Object(bucket_name, object_key)

            result = object.put(Body=open('c:/sps/excel/' + object_key, 'rb'))

            # res = result.get('ResponseMetadata')

            # if res.get('HTTPStatusCode') == 200:
            #    print('File Uploaded Successfully')
            # else:
            #    print('File Not Uploaded')
            """

            # Vamos abrir o arquivo Excel que foi gravado no AWS S3
            s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
            content = bucket_object.get()['Body'].read()

            wb = open_workbook_xls(file_contents=content)

            sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba, onde estão as informações a serem copiadas
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_mae = sheet.ncols
            total_linhas_mae = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_mae):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id',
                                 'cus_ite_nome',
                                 'cus_ite_codigo_interno',
                                 'cus_ite_unidade',
                                 'cus_ite_imagem',
                                 'cus_ite_observacao',
                                 'cus_ite_tipo_id']:

                # Atenção: não usamos a coluna 'id'. Só mantemos para que possamos usar a mesma planilha que foi exportada
                # Cabeçalho do arquivo está correto.
                # Temos que verificar se a unidade informada no arquivo em Excel está na lista definida do sistema
                unidade_ok = True  # Vamos assumir que a princípio está ok
                for i in range(total_linhas_mae):
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        # A unidade informada está na coluna de ordem 3
                        if not sheet.cell_value(i, 3) in ['un', 'pe', 'g', 'kg', 't', 'h', 'hh', 'm3', 'Nm3', 'L', 'kw',
                                                          'kwh', 'Mw', 'Mwh']:
                            unidade_ok = False
                            break

                if unidade_ok:
                    # Vamos verificar se existe no sistema o cus_ite_tipo_id informado (coluna de ordem 6 na planilha em Excel)
                    cus_ite_tipo_id_ok = True
                    for i in range(total_linhas_mae):
                        if i > 0:  # Porque a linha 0 é o cabeçalho

                            if TbCustoTipo.objects.filter(id=sheet.cell_value(i, 6)).count() == 0:
                                cus_ite_tipo_id_ok = False
                                break

                    if cus_ite_tipo_id_ok:
                        # Vamos ver se a informação é nova.
                        # Nesse caso temos que ver se o nome do item é novo. Informação está na coluna de ordem 1
                        existe = False
                        for i in range(total_linhas_mae):
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if TbCustoItem.objects.filter(cus_ite_nome=sheet.cell_value(i, 1)).count() > 0:
                                    existe = True
                                    break

                        if not existe:
                            # Tudo ok até aqui. Podemos continuar
                            # Vamos agora incluir o registro.
                            # Se foi informado algum valor na coluna id nós desconsideramos.
                            for i in range(total_linhas_mae):
                                if i > 0:  # Porque a linha 0 é o cabeçalho
                                    TbCustoItem.objects.create(cus_ite_nome=sheet.cell_value(i, 1),
                                                               cus_ite_codigo_interno=sheet.cell_value(i, 2),
                                                               cus_ite_unidade=sheet.cell_value(i, 3),
                                                               cus_ite_observacao=sheet.cell_value(i, 5),
                                                               cus_ite_tipo_id=sheet.cell_value(i, 6))
                                    # Essa tabela não tem o campo cenário e tabela filha
                                    # Observar que não usamos a coluna 'id' (ordem 0).

                            messages.success(request, _('Novos itens de custo variável foram adicionados com sucesso.'))

                        else:
                            messages.error(request,
                                           'Já existe cadastrado no sistema item de custo variável com o nome ' + sheet.cell_value(
                                               i, 1) + ' informado na linha ' + str(
                                               i) + ' do arquivo em Excel. Favor verificar!')

                    else:
                        messages.error(request, 'Não existe no sistema o tipo de item id = ' + str(
                            sheet.cell_value(i, 6)) + ' informado na linha ' + str(
                            i) + ' do arquivo em Excel. Favor verificar!')

                else:
                    messages.error(request, 'Unidade do item na linha ' + str(
                        i) + ' da planilha não é considerada no sistema. Favor verificar!')

            else:
                messages.error(request, _('Cabeçalho do arquivo Excel não está correto. Favor verificar!'))

        except:
            messages.error(request,
                           'Não foi encontrado o arquivo ' + object_key + ' no diretório c:\sps\excel ou AWS S3. Favor verificar!')

    importar_excel_new.short_description = _('Importar Excel (Novos)')

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel_new':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbCustoItem.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbCustoItemAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        try:
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'custoitem.xls'

            """ Removemos pois não funciona no Heroku. Só localmente. Fizemos um .exe para transferir
            # Vamos salvar o arquivo Excel no AWS S3
            session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

            s3 = session.resource('s3')

            object = s3.Object(bucket_name, object_key)

            result = object.put(Body=open('c:/sps/excel/' + object_key, 'rb'))

            res = result.get('ResponseMetadata')

            if res.get('HTTPStatusCode') == 200:
                print('File Uploaded Successfully')
            else:
                print('File Not Uploaded')
            """

            # Vamos abrir o arquivo Excel que foi gravado no AWS S3
            s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
            content = bucket_object.get()['Body'].read()

            wb = open_workbook_xls(file_contents=content)

            sheet = wb.sheet_by_index(0)  # Abrindo o arquivo. Só tem a mãe.
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_mae = sheet.ncols
            total_linhas_mae = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_mae):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id', 'cus_ite_nome', 'cus_ite_codigo_interno', 'cus_ite_unidade', 'cus_ite_imagem',
                                 'cus_ite_observacao', 'cus_ite_tipo_id']:
                # Tudo ok até aqui.
                # Temos que verificar se a unidade informada no arquivo em Excel está na lista definida do sistema
                unidade_ok = True  # Vamos assumir que a princípio está ok
                for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        # A unidade informada está na coluna de ordem 3
                        if not sheet.cell_value(i, 3) in ['un', 'g', 'kg', 't', 'h', 'hh', 'm3', 'Nm3', 'L', 'kw',
                                                          'kwh', 'Mw', 'Mwh']:
                            unidade_ok = False
                            break

                if unidade_ok:
                    #  Vamos verificar se no arquivo tem o id dos items selecionados.
                    #  Vamos pegar o id dos custos items selecionados
                    id_maes = queryset.values_list('id', )
                    for id_mae in id_maes:
                        # Temos que pegar a str do id.
                        id_str = TbCustoItem.objects.get(id=id_mae[0]).cus_ite_nome
                        # Vamos ver se tem somente um id no arquivo
                        sheet = wb.sheet_by_index(0)  # Abrindo o arquivo
                        conta_mae = 0
                        for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                            if i > 0:  # Porque a linha 0 é o cabeçalho

                                if sheet.cell_value(i, 0) == id_mae[0]:  # É o id da mãe. Soma no contador.
                                    conta_mae = conta_mae + 1

                        if conta_mae == 0:
                            messages.error(request, 'Na aba mãe do arquivo em Excel não existe o id = ' + str(
                                id_mae[0]) + ' (' + id_str + '). Favor verificar!')
                            break
                        else:
                            if conta_mae > 1:
                                # Se tiver mais que 1 também é problema
                                messages.error(request, 'Na aba mãe do arquivo em Excel tem mais que 1(um) id = ' + str(
                                    id_mae[0]) + ' (' + id_str + '). Favor verificar!')
                                break
                            else:
                                # Tudo certo até aqui. Podemos continuar.
                                # Vamos verificar se existe no sistema o cus_ite_tipo_id informado (coluna de ordem 6 na planilha em Excel)
                                cus_ite_tipo_id_ok = True
                                for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                    if i > 0:  # Porque a linha 0 é o cabeçalho

                                        if TbCustoTipo.objects.filter(id=sheet.cell_value(i, 6)).count() == 0:
                                            cus_ite_tipo_id_ok = False
                                            break

                                if cus_ite_tipo_id_ok:
                                    # Tudo ok. Podemos atualizar os valores.
                                    for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                        if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                            0]:  # Porque a linha 0 é o cabeçalho
                                            tab_obj = TbCustoItem.objects.get(id=sheet.cell_value(i, 0))

                                            tab_obj.cus_ite_nome = sheet.cell_value(i, 1)
                                            tab_obj.cus_ite_codigo_interno = sheet.cell_value(i, 2)
                                            tab_obj.cus_ite_unidade = sheet.cell_value(i, 3)
                                            # Não atualizamos o campo cus_ite_imagem
                                            tab_obj.cus_ite_observacao = sheet.cell_value(i, 5)
                                            tab_obj.cus_ite_tipo_id = sheet.cell_value(i, 6)
                                            tab_obj.save()

                                else:
                                    messages.error(request, 'Não existe no sistema o tipo de item = ' + str(
                                        sheet.cell_value(i, 6)) + ' informado na linha ' + str(
                                        i) + ' do arquivo em Excel. Favor verificar!')
                                    break

                    if cus_ite_tipo_id_ok:
                        messages.success(request, _('Tabela Custo Variável - Itens atualizada com sucesso!'))

                else:
                    messages.error(request, 'Unidade do item na linha ' + str(
                        i) + ' da planilha não é considerada no sistema. Favor verificar!')
            else:
                messages.error(request, _('Cabeçalho da aba mãe não está correto. Favor verificar!'))

        except:
            messages.error(request,
                           _('Não foi encontrado o arquivo custoitem.xls no diretório c:\sps\excel ou AWS S3. Favor verificar!'))

    importar_excel.short_description = _('Importar Excel')

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="custoitem.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id', 'cus_ite_nome', 'cus_ite_codigo_interno', 'cus_ite_unidade', 'cus_ite_imagem',
                   'cus_ite_observacao', 'cus_ite_tipo_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id', 'cus_ite_nome', 'cus_ite_codigo_interno', 'cus_ite_unidade', 'cus_ite_imagem',
                                    'cus_ite_observacao', 'cus_ite_tipo_id').order_by('id')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar Excel')

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }


admin.site.register(TbCustoItem, TbCustoItemAdmin)


class TbCustoItemPrecoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_3', 'dau_valor_2', 'dau_valor_4', 'dau_valor_5')
    readonly_fields = ('display_order',)

    model = TbCustoItemPrecoDaugther
    form = TbCustoItemPrecoDaugtherFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Vamos ver se o usuário tem permissão para alterar essa tabela
        u = User.objects.get(username=request.user)
        if u.has_perm(
                'tabelas.change_tbcustoitemprecodaugther'):  # Tem permissão. Vamos ver se tem permissão para o tipo de item de custo
            # Vamos ver se é superuser
            current_user = request.user
            if current_user.is_superuser:  # Se é superuser já retorna True
                return True
            else:
                # Vamos pegar o id do tipo do item de custo
                retorno = False
                id_custo_item = obj.cus_ite_pre_item_id
                id_tipo = TbCustoItem.objects.get(id=id_custo_item).cus_ite_tipo_id
                # Já tenho o id do tipo do item de custo. Tenho que pegar o id dos grupos que foram liberados para esse tipo de item de custo
                lista_id_grupos_qs = TbCustoTipo.cus_tip_group.through.objects.filter(tbcustotipo_id=id_tipo)
                for lista_id_grupos in lista_id_grupos_qs:
                    if u.groups.filter(id=lista_id_grupos.group_id).exists():
                        retorno = True
                        break

                return retorno

        else:
            return False


class TbCustoItemPrecoAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = (('cus_ite_pre_item', 'cus_ite_pre_validado', 'cus_item_unidade', 'cus_item_tipo'), 'cus_ite_pre_unidade_producao', ('cus_ite_pre_custo_variavel_adiconado', 'periodo_inicio', 'periodo_fim', 'valor_custo_variavel_adicionado'),
              'cus_ite_imagem_tag_small', ('cus_ite_pre_indicador_preco', 'cus_ite_pre_moeda_preco'),
              ('cus_ite_pre_indicador_inbound', 'cus_ite_pre_moeda_inbound'), 'cus_ite_pre_observacao')
    list_display = ['id', 'cus_ite_pre_item', 'cus_ite_pre_validado', 'preco_medio', 'cus_item_unidade', 'cus_item_tipo', 'cus_ite_imagem_tag_small',
                    'cus_ite_pre_observacao', 'cus_ite_pre_unidade_producao', 'cus_ite_pre_indicador_preco', 'cus_ite_pre_moeda_preco',
                    'cus_ite_pre_indicador_inbound', 'cus_ite_pre_moeda_inbound']
    readonly_fields = ['cus_ite_imagem_tag_small', 'cus_item_unidade', 'cus_item_tipo', 'periodo_inicio', 'periodo_fim', 'valor_custo_variavel_adicionado', 'preco_medio']
    list_display_links = ['id', 'cus_ite_pre_item']

    class Tipo_Item_ListFilter(admin.SimpleListFilter):
        title = 'Tipo'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'tipo'



        def lookups(self, request, model_admin):
            # Vamos pegar somente itens que estão na tabela
            cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
            #qs = TbCustoItemPreco.objects.filter().distinct('cus_ite_pre_item').order_by('-ane_lote_destino_id__cus_ite_tipo_id__cus_tip_nome')
            qs = TbCustoItemPreco.objects.filter(tbcenarios_id=cen_ativo).distinct('cus_ite_pre_item_id__cus_ite_tipo_id__cus_tip_nome').order_by('cus_ite_pre_item_id__cus_ite_tipo_id__cus_tip_nome')
            # Vamos montar a tuple na mão
            tuple = []
            tuple_aux = []

            '''
            # Não iremos usar os desmarcados abaixo pois não iremos sobrepor os filtros
            if 'item_consumo' in request.GET:
                qs = qs.filter(ane_item_consumo_id=request.GET['item_consumo'])

            if 'lote_destino' in request.GET:
                qs = qs.filter(id=request.GET['lote_destino'])

            if 'status' in request.GET:
                # Vamos filtrar a queryset de acordo com o status selecionado
                # Vamos montar uma lista com os anexos que tem status de acordo com o status selecionado
                lista = []
                qs_new = TbAnexoIII.objects.filter()
                for q_new in qs_new:
                    if q_new.ane_qualidade is not None:
                        status_atual = 'FECHADO'
                    else:
                        cursor = connection.cursor()
                        # Só vamos passar o id do anexo iii
                        sql = "call public.verifica_anexoiii(" + str(q_new.id) + ", false)"
                        cursor.execute(sql)
                        retorno = cursor.fetchone()[0]
                        cursor.close()
                        if retorno:
                            status_atual = 'AGUARDANDO LIBERAÇÃO'
                        else:
                            status_atual = 'EM ANDAMENTO'

                    if status_atual == request.GET['status']:
                        lista.append(q_new.id)

                qs = qs.filter(id__in=lista)
                '''

            for q in qs:
                id_tipo = TbCustoItem.objects.get(id=q.cus_ite_pre_item_id).cus_ite_tipo_id
                nome_tipo = TbCustoTipo.objects.get(id=id_tipo).cus_tip_nome
                if nome_tipo not in tuple_aux:
                    tuple_aux.append(nome_tipo)
                    tuple.append((id_tipo, nome_tipo))
            return tuple

        def queryset(self, request, queryset):
            # ATENÇÃO: self.value() retorna uma str e não integer
            if self.value(): # Significa que foi selecionado um item do filtro
                # Temos que montar uma lista de id da tabela TbCustoItemPreco que tem o id do tipo do item = self.value()
                cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
                qs = TbCustoItemPreco.objects.filter(tbcenarios_id=cen_ativo)
                lista_id = []
                for q in qs:
                    id_tipo = TbCustoItem.objects.get(id=q.cus_ite_pre_item_id).cus_ite_tipo_id
                    if str(id_tipo) == str(self.value()):
                        lista_id.append(q.id)

                print(lista_id)
                return queryset.filter(id__in=lista_id)
            else:
                return queryset


    list_filter = (('cus_ite_pre_item', admin.RelatedOnlyFieldListFilter),
                   ('cus_ite_pre_unidade_producao', admin.RelatedOnlyFieldListFilter),
                   (Tipo_Item_ListFilter),
                    'cus_ite_pre_validado')
    search_fields = ['cus_ite_pre_item__cus_ite_nome', ]
    list_editable = ['cus_ite_pre_validado']

    actions = ['exportar_excel', 'importar_excel', 'importar_excel_new', 'update_valor_custo_variavel_adicionado_geral', 'validar_custo_item_preco', 'desvalidar_custo_item_preco']

    def validar_custo_item_preco(self, request, queryset):

        custos = queryset.values_list('id', )

        for custo in custos:
            tab_obj = TbCustoItemPreco.objects.get(id=custo[0])
            tab_obj.cus_ite_pre_validado = 1
            tab_obj.save()

    validar_custo_item_preco.short_description = _('Validar Custos Variáveis Selecionados')

    def desvalidar_custo_item_preco(self, request, queryset):

        custos = queryset.values_list('id', )

        for custo in custos:
            tab_obj = TbCustoItemPreco.objects.get(id=custo[0])
            tab_obj.cus_ite_pre_validado = 0
            tab_obj.save()

    desvalidar_custo_item_preco.short_description = _('Desvalidar Custos Variáveis Selecionados')

    def update_valor_custo_variavel_adicionado_geral(self, request, queryset):
        custos = queryset.values_list('id', )
        for custo in custos:
            if TbCustoItemPreco.objects.get(id=custo[0]).cus_ite_pre_custo_variavel_adiconado:  # Se foi indicado custo variável adicionado
                update_custo_variavel_adicionado(custo[0])
        messages.success(request, _('Update do preço pelo valor do custo variável adicionado realizado com sucesso para os itens selecionados!'))

    update_valor_custo_variavel_adicionado_geral.short_description = _("Update Preço/CustoVariável Adicionado Itens Selecionados")  # optional

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.change_tbcustoitempreco'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def importar_excel_new(self, request, queryset):
        # try:
        # Para permitir acesso ao AWS S3
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY
        bucket_name = 'spsferbasa'
        object_key = 'custoitempreco_new.xls'

        ''' Removemos pois não funciona no Heroku. Só localmente. Fizemos um .exe para transferir
        # Vamos salvar o arquivo Excel no AWS S3
        session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

        s3 = session.resource('s3')

        object = s3.Object(bucket_name, object_key)

        result = object.put(Body=open('c:/sps/excel/' + object_key, 'rb'))

        #res = result.get('ResponseMetadata')

        # if res.get('HTTPStatusCode') == 200:
        #    print('File Uploaded Successfully')
        # else:
        #    print('File Not Uploaded')
        '''

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

        if lista_colunas == ['id',
                             'cus_ite_pre_item_id',
                             'cus_ite_pre_item_nome',
                             'cus_ite_pre_unidade_producao_id',
                             'cus_ite_pre_unidade_producao_nome',
                             'cus_ite_pre_indicador_preco_id',
                             'cus_ite_pre_indicador_preco_nome',
                             'cus_ite_pre_moeda_preco',
                             'cus_ite_pre_indicador_inbound_id',
                             'cus_ite_pre_indicador_inbound_nome',
                             'cus_ite_pre_moeda_inbound',
                             'valor_inicial_1',
                             'valor_inicial_2',
                             'valor_inicial_3',
                             'valor_inicial_4',
                             'valor_inicial_5',
                             'id_origem',
                             'cenarios_id']:

            #  Cabeçalho da mãe está correto.
            #  Vamos ver se o cenário informado na mãe é o cenário ativo.
            #  Cenário ativo
            ok_cenario = True
            cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

            sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba
            for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 17
                if i > 0:  # Porque a linha 0 é o cabeçalho
                    if sheet.cell_value(i, 17) != cen_ativo:
                        ok_cenario = False
                        break

            if not ok_cenario:
                messages.error(request, _('Cenário informado na planilha não é o ativo. Favor verificar!'))
            else:
                # Tudo ok até aqui.
                # Vamos ver se a informação é nova.
                # Nesse caso temos que ver se o id do item de custo (cus_ite_pre_item_id/coluna order 1) e o id da unidade de produção (cus_ite_pre_unidade_producao_id/coluna order 3) são novos.
                existe = False
                for i in range(total_linhas_mae):
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        if TbCustoItemPreco.objects.filter(cus_ite_pre_item_id=sheet.cell_value(i, 1),
                                                           cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3),
                                                           tbcenarios_id=cen_ativo).count() > 0:
                            existe = True
                            break
                if not existe:
                    # Tudo ok até aqui. Podemos continuar.
                    # Vamos ver se id do item de custo informado existe.
                    existe = True
                    for i in range(total_linhas_mae):
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if TbCustoItem.objects.filter(id=sheet.cell_value(i, 1)).count() == 0:
                                existe = False
                                break
                    if existe:
                        # Tudo ok até aqui. Podemos continuar.
                        # Vamos ver se id da unidade de produção informado existe.
                        existe = True
                        for i in range(total_linhas_mae):
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if TbUnidadeProducao.objects.filter(id=sheet.cell_value(i, 3)).count() == 0:
                                    existe = False
                                    break
                        if existe:
                            # Tudo ok até aqui. Podemos continuar.
                            # Vamos ver se o id do indicador do preço informado existe (cus_ite_pre_indicador_preco_id/coluna order 5).
                            existe = True
                            for i in range(total_linhas_mae):
                                if i > 0:  # Porque a linha 0 é o cabeçalho
                                    if sheet.cell_value(i, 5) is not None and sheet.cell_value(i,
                                                                                               5) != '':  # Foi informado id de indicador para o preço.
                                        if TbIndicadores.objects.filter(id=sheet.cell_value(i, 5)).count() == 0:
                                            existe = False
                                            break
                            if existe:
                                # Tudo ok até aqui. Podemos continuar.
                                # Vamos ver se a moeda informada para o preço (cus_ite_pre_moeda_preco/coluna order 7).
                                # Temos que primeiro ver qual a moeda do sistema
                                moeda_sistema = TbEmpresa.objects.get().emp_moeda
                                lista_moeda = ['BRL', 'USD', 'EUR']
                                existe = True
                                for i in range(total_linhas_mae):
                                    if i > 0:  # Porque a linha 0 é o cabeçalho
                                        if not sheet.cell_value(i, 7) in lista_moeda:
                                            existe = False
                                            break
                                        else:
                                            # Vamos ver se é diferente da moeda do sistema. Se sim temos que ver se foi cadastrado câmbio para a moeda
                                            if sheet.cell_value(i, 7) != moeda_sistema:
                                                # Vamos ver se foi cadastrado câmbio para a moeda informada.
                                                if TbCambio.objects.filter(
                                                        cam_moeda=sheet.cell_value(i, 7)).count() == 0:
                                                    existe = False
                                                    break

                                if existe:
                                    # Tudo ok até aqui. Podemos continuar.
                                    # Vamos ver se o id do indicador do inbound informado existe (cus_ite_pre_indicador_inbound_id/coluna order 8).
                                    existe = True
                                    for i in range(total_linhas_mae):
                                        if i > 0:  # Porque a linha 0 é o cabeçalho
                                            if sheet.cell_value(i, 8) is not None and sheet.cell_value(i,
                                                                                                       8) != '':  # Foi informado id de indicador para o inbound.
                                                if TbIndicadores.objects.filter(id=sheet.cell_value(i, 8)).count() == 0:
                                                    existe = False
                                                    break
                                    if existe:
                                        # Tudo ok até aqui. Podemos continuar.
                                        # Vamos ver se a moeda informada para o inbound (cus_ite_pre_moeda_inbound/coluna order 10).
                                        # Temos que primeiro ver qual a moeda do sistema.
                                        # moeda_sistema = TbEmpresa.objects.get().emp_moeda
                                        # lista_moeda = ['BRL', 'USD', 'EUR']
                                        existe = True
                                        for i in range(total_linhas_mae):
                                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                                if not sheet.cell_value(i, 10) in lista_moeda:
                                                    existe = False
                                                    break
                                                else:
                                                    # Vamos ver se é diferente da moeda do sistema. Se sim temos que ver se foi cadastrado câmbio para a moeda.
                                                    if sheet.cell_value(i, 10) != moeda_sistema:
                                                        # Vamos ver se foi cadastrado câmbio para a moeda informada.
                                                        if TbCambio.objects.filter(
                                                                cam_moeda=sheet.cell_value(i, 10)).count() == 0:
                                                            existe = False
                                                            break

                                        if existe:
                                            # Vamos agora incluir o registro e a filhas a partir dos valores iniciais informados.
                                            # Se foi informado algum valor na coluna id nós desconsideramos.
                                            # Também desconsideramos os nomes que foram informados (nome item de custo, unidde de produção e indicadores)
                                            for i in range(total_linhas_mae):
                                                if i > 0:  # Porque a linha 0 é o cabeçalho
                                                    TbCustoItemPreco.objects.create(
                                                        cus_ite_pre_item_id=sheet.cell_value(i, 1),
                                                        cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3),
                                                        cus_ite_pre_indicador_preco_id=sheet.cell_value(i, 5),
                                                        cus_ite_pre_moeda_preco=sheet.cell_value(i, 7),
                                                        cus_ite_pre_indicador_inbound_id=sheet.cell_value(i, 8),
                                                        cus_ite_pre_moeda_inbound=sheet.cell_value(i, 10),
                                                        valor_inicial_1=sheet.cell_value(i, 11),
                                                        valor_inicial_2=sheet.cell_value(i, 12),
                                                        valor_inicial_3=sheet.cell_value(i, 13),
                                                        valor_inicial_4=sheet.cell_value(i, 14),
                                                        valor_inicial_5=sheet.cell_value(i, 15),
                                                        tbcenarios_id=cen_ativo)

                                                    '''
                                                    # Não precisa de nada disso. A filha é verificada ao salvar a mãe. Vide no models
                                                    # Temos que pegar o id da mãe para lançar na filha
                                                    id_mae = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).id
                                                    valor_inicial_1 = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).valor_inicial_1
                                                    valor_inicial_2 = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).valor_inicial_2
                                                    valor_inicial_3 = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).valor_inicial_3
                                                    valor_inicial_4 = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).valor_inicial_4
                                                    valor_inicial_5 = TbCustoItemPreco.objects.get(cus_ite_pre_item_id=sheet.cell_value(i, 1), cus_ite_pre_unidade_producao_id=sheet.cell_value(i, 3)).valor_inicial_5
                                                    print(id_mae)
                                                    print(valor_inicial_1)
                                                    print(valor_inicial_2)
                                                    print(valor_inicial_3)
                                                    print(str(valor_inicial_3))
                                                    print(str(valor_inicial_4))
                                                    print(str(valor_inicial_5))

                                                    # Vamos atualizar a filha usando o Stored Procedure verifica_filha_5_com_indicador
                                                    cursor = connection.cursor()
                                                    # Montando a expressão sql para rodar o Stored Procedure verifica_filha_5_com_indicador

                                                    # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
                                                    if sheet.cell_value(i, 5) is not None and sheet.cell_value(i, 5) != '':
                                                        id_indicador_1 = int(sheet.cell_value(i, 5))
                                                    else:
                                                        id_indicador_1 = int(0)

                                                    if sheet.cell_value(i, 8) is not None and sheet.cell_value(i, 8) != '':
                                                        id_indicador_2 = int(sheet.cell_value(i, 8))
                                                    else:
                                                        id_indicador_2 = int(0)

                                                    sql = "call public.verifica_filha_5_com_indicador('tabelas_" + "tbcustoitempreco" + "daugther', " + str(id_mae) + ", " + str(cen_ativo) + ", " + str(valor_inicial_1) + ", " + str(valor_inicial_2) + ", " + str(valor_inicial_3) + ", " + str(valor_inicial_4) + ", " + str(valor_inicial_5) + ", " + str(id_indicador_1) + ", " + str(id_indicador_2) + ")"
                                                    print(sql)
                                                    cursor.execute(sql)
                                                    cursor.close()
                                                    '''
                                            messages.success(request,
                                                             _('Novos itens de custo com preços foram adicionados com sucesso.'))
                                        else:
                                            messages.error(request,
                                                           'Moeda informada para o preço do item de custo ' + sheet.cell_value(
                                                               i,
                                                               6) + ' não existe ou então não foi cadastrado câmbio para a mesma. Favor verificar!')

                                    else:
                                        messages.error(request, 'Não existe o indicador com id = ' + str(
                                            sheet.cell_value(i, 5)) + ' cadastrado. Favor verificar!')

                                else:
                                    messages.error(request,
                                                   'Moeda informada para o preço do item de custo ' + sheet.cell_value(
                                                       i,
                                                       4) + ' não existe ou então não foi cadastrado câmbio para a mesma. Favor verificar!')

                            else:
                                messages.error(request, 'Não existe o indicador com id = ' + str(
                                    sheet.cell_value(i, 3)) + ' cadastrado. Favor verificar!')

                        else:
                            messages.error(request, 'Não existe a unidade de produção id = ' + str(
                                sheet.cell_value(i, 2)) + ' cadastrada. Favor verificar!')

                    else:
                        messages.error(request, 'Não existe o item de custo id = ' + str(
                            sheet.cell_value(i, 1)) + ' cadastrado. Favor verificar!')

                else:
                    messages.error(request, 'Já existe o item de custo id = ' + str(
                        int(sheet.cell_value(i, 1))) + ' e unidade de produção id = ' + str(
                        int(sheet.cell_value(i, 3))) + ' já cadastrados para esse cenário. Favor verificar!')

        else:
            messages.error(request, _('Cabeçalho do arquivo Excel não está correto. Favor verificar!'))

        # except:
        #    messages.error(request, 'Não foi encontrado o arquivo ' + object_key + ' no diretório c:\sps\excel. Favor verificar!')

    importar_excel_new.short_description = _('Importar Excel (Novos)')

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel_new':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbCustoItemPreco.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbCustoItemPrecoAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        try:
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'custoitempreco_mae_filhas.xls'

            ''' Removemos pois não funciona no Heroku. Só localmente. Fizemos um .exe para transferir
            # Vamos salvar o arquivo Excel no AWS S3
            session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

            s3 = session.resource('s3')

            object = s3.Object(bucket_name, object_key)

            os.chdir(r'c:\sps\excel')
            result = object.put(Body=open(object_key, 'rb'))

            #res = result.get('ResponseMetadata')

            #if res.get('HTTPStatusCode') == 200:
            #    print('File Uploaded Successfully')
            #else:
            #    print('File Not Uploaded')
            '''

            # Vamos abrir o arquivo Excel que foi gravado no AWS S3
            s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
            content = bucket_object.get()['Body'].read()

            wb = open_workbook_xls(file_contents=content)

            sheet = wb.sheet_by_index(0)  # Abrindo a mãe
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_mae = sheet.ncols
            total_linhas_mae = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_mae):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id',
                                 'cus_ite_pre_item_id',
                                 'cus_ite_pre_item_nome',
                                 'cus_ite_pre_unidade_producao_id',
                                 'cus_ite_pre_unidade_producao_nome',
                                 'cus_ite_pre_indicador_preco_id',
                                 'cus_ite_pre_indicador_preco_nome',
                                 'cus_ite_pre_moeda_preco',
                                 'cus_ite_pre_indicador_inbound_id',
                                 'cus_ite_pre_indicador_inbound_nome',
                                 'cus_ite_pre_moeda_inbound',
                                 'valor_inicial_1',
                                 'valor_inicial_2',
                                 'valor_inicial_3',
                                 'valor_inicial_4',
                                 'valor_inicial_5',
                                 'id_origem',
                                 'cenarios_id']:
                #  Cabeçalho da mãe está correto. Vamos agora ver a filha
                sheet = wb.sheet_by_index(1)  # Abrindo a filha
                # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
                total_colunas_filha = sheet.ncols
                total_linhas_filha = sheet.nrows
                lista_colunas = []
                for i in range(total_colunas_filha):
                    lista_colunas.append(sheet.cell_value(0, i))

                if lista_colunas == ['id', 'dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                     'dau_valor_5', 'mae_id', 'cenarios_id']:
                    #  Filha também com cabeçalho correto. Podemos continuar
                    #  Vamos ver se o total de lançamentos na filha está de acordo com o total de mães. Vamos precisar do total de períodos do cenário ativo.
                    #  Vamos obter o total de períodos para o cenário ativo
                    cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                    cursor = connection.cursor()
                    sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
                    cursor.execute(sql)
                    total_periodos = cursor.fetchone()[0]
                    cursor.close()

                    if (total_linhas_filha - 1) == (total_linhas_mae - 1) * total_periodos:
                        #  Vamos ver se o cenário informado na mãe e na filha é o cenário ativo.
                        # Cenário ativo
                        ok_cenario = True
                        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                        sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                        for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 17 na mãe
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if sheet.cell_value(i, 17) != cen_ativo:
                                    ok_cenario = False
                                    break

                        sheet = wb.sheet_by_index(1)  # Abrindo a filha
                        for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 8 na filha
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if sheet.cell_value(i, 8) != cen_ativo:
                                    ok_cenario = False
                                    break
                        if not ok_cenario:
                            messages.error(request,
                                           _('Cenário informado na planilha (aba mãe ou filhas) não é o ativo. Favor verificar!'))
                        else:
                            #  Tudo ok até aqui. Vamos verificar se no arquivo tem as mães selecionadas. E se tiver, vamos ver se tem as filhas no total do período do cenário.
                            #  Vamos pegar o id das mães selecionadas.
                            id_maes = queryset.values_list('id', )
                            tudo_ok = True
                            for id_mae in id_maes:
                                # Vamos ver se tem somente uma mãe na aba mãe
                                sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                                conta_mae = 0
                                for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                    if i > 0:  # Porque a linha 0 é o cabeçalho

                                        if sheet.cell_value(i, 0) == id_mae[0]:  # É o id da mãe. Soma no contador.
                                            conta_mae = conta_mae + 1

                                if conta_mae == 0:
                                    tudo_ok = False
                                    messages.error(request, 'Na aba mãe do arquivo em Excel não existe o id = ' + str(
                                        id_mae[0]) + '. Favor verificar!')
                                    break
                                else:
                                    if conta_mae > 1:
                                        # Se tiver mais que 1 também é problema
                                        tudo_ok = False
                                        messages.error(request,
                                                       'Na aba mãe do arquivo em Excel tem mais que 1(um) id = ' + str(
                                                           id_mae[0]) + '. Favor verificar!')
                                        break
                                    else:
                                        #  Tudo certo até aqui. Podemos continuar.
                                        #  Vamos ver se na tabela filhas existe um total de filhas igual ao total de periodos
                                        sheet = wb.sheet_by_index(1)  # Abrindo a filhas
                                        conta_filha = 0
                                        for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 7
                                            if i > 0 and sheet.cell_value(i, 7) == id_mae[
                                                0]:  # É o id da mãe. Soma no contador.
                                                conta_filha = conta_filha + 1

                                        if conta_filha != total_periodos:
                                            tudo_ok = False
                                            messages.error(request, 'Total de filhas para a mãe id = ' + str(
                                                id_mae[0]) + 'é igual a ' + str(conta_filha) + ', diferente de ' + str(
                                                total_periodos) + '. Favor verificar!')
                                        else:
                                            # Vamos ver agora se o dau_order nas filhas estão na sequência certa
                                            conta_dau_order = 0
                                            ok_dau_order = True
                                            for i in range(
                                                    total_linhas_filha):  # A coluna da mãe_id é a de ordem 7 e a do dau_order é o 1
                                                if i > 0:  # Porque a linha 0 é o cabeçalho

                                                    if sheet.cell_value(i, 7) == id_mae[
                                                        0]:  # É o id da mãe. Vamos somar no contador e comparar com o dau_order
                                                        conta_dau_order = conta_dau_order + 1
                                                        if conta_dau_order != sheet.cell_value(i, 1):
                                                            ok_dau_order = False
                                                            break

                                            if not ok_dau_order:
                                                tudo_ok = False
                                                messages.error(request, 'Sequencial dau_order para a mãe id = ' + str(
                                                    id_mae[0]) + ' está fora da sequencia na filha. Favor verificar!')
                                            else:
                                                #  Tudo ok até aqui. Podemos continuar
                                                #  Vamos agora atualizar a mãe e as filhas pois tudo está Ok com o arquivo Excel
                                                sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                                                #  id_mae[0] é o id da mãe que foi selecionado
                                                for i in range(
                                                        total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                                    if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                                        0]:  # Porque a linha 0 é o cabeçalho
                                                        tab_obj = TbCustoItemPreco.objects.get(
                                                            id=sheet.cell_value(i, 0))
                                                        tab_obj.cus_ite_pre_item_id = int(sheet.cell_value(i, 1))
                                                        tab_obj.cus_ite_pre_unidade_producao_id = sheet.cell_value(i, 3)
                                                        if sheet.cell_value(i, 5) == '':
                                                            tab_obj.cus_ite_pre_indicador_preco_id = None
                                                        else:
                                                            tab_obj.cus_ite_pre_indicador_preco_id = int(
                                                                sheet.cell_value(i, 5))
                                                        tab_obj.cus_ite_pre_moeda_preco = sheet.cell_value(i, 7)
                                                        if sheet.cell_value(i, 8) == '':
                                                            tab_obj.cus_ite_pre_indicador_inbound_id = None
                                                        else:
                                                            tab_obj.cus_ite_pre_indicador_inbound_id = int(
                                                                sheet.cell_value(i, 8))
                                                        tab_obj.cus_ite_pre_moeda_inbound = sheet.cell_value(i, 10)
                                                        tab_obj.valor_inicial_1 = sheet.cell_value(i, 11)
                                                        tab_obj.valor_inicial_2 = sheet.cell_value(i, 12)
                                                        tab_obj.valor_inicial_3 = int(sheet.cell_value(i, 13))
                                                        tab_obj.valor_inicial_4 = int(sheet.cell_value(i, 14))
                                                        tab_obj.valor_inicial_5 = int(sheet.cell_value(i, 15))
                                                        tab_obj.save()

                                                # Vamos agora atualizar as filhas
                                                sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                                for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 7
                                                    if i > 0 and sheet.cell_value(i, 7) == id_mae[
                                                        0]:  # É o id da mãe. Soma no contador.
                                                        # Temos que pegar o dau_order
                                                        dau_order_filha = sheet.cell_value(i, 1)
                                                        tab_obj = TbCustoItemPrecoDaugther.objects.get(
                                                            id=sheet.cell_value(i, 0), mae_id=id_mae[0],
                                                            dau_order=dau_order_filha)
                                                        tab_obj.dau_valor_1 = sheet.cell_value(i, 2)
                                                        tab_obj.dau_valor_2 = sheet.cell_value(i, 3)
                                                        tab_obj.dau_valor_3 = sheet.cell_value(i, 4)
                                                        tab_obj.dau_valor_4 = sheet.cell_value(i, 5)
                                                        tab_obj.dau_valor_5 = sheet.cell_value(i, 6)

                                                        tab_obj.save()

                            if tudo_ok:
                                messages.success(request, _('Tabela Custo Item Preço foi atualizada com sucesso!'))

                    else:
                        messages.error(request,
                                       _('Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!'))

                else:
                    messages.error(request, _('Cabeçalho da aba filha não está correto. Favor verificar!'))

            else:
                messages.error(request, _('Cabeçalho da aba mãe não está correto. Favor verificar!'))

        except:
            messages.error(request, _('Ocorreu um erro na importação dos dados. Favor contatar a equipe de suporte da SPS Consultoria.'))

    importar_excel.short_description = _('Importar Excel')

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="custoitempreco_mae_filhas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id',
                   'cus_ite_pre_item_id',
                   'cus_ite_pre_item_nome',
                   'cus_ite_pre_unidade_producao_id',
                   'cus_ite_pre_unidade_producao_nome',
                   'cus_ite_pre_indicador_preco_id',
                   'cus_ite_pre_indicador_preco_nome',
                   'cus_ite_pre_moeda_preco',
                   'cus_ite_pre_indicador_inbound_id',
                   'cus_ite_pre_indicador_inbound_nome',
                   'cus_ite_pre_moeda_inbound',
                   'valor_inicial_1',
                   'valor_inicial_2',
                   'valor_inicial_3',
                   'valor_inicial_4',
                   'valor_inicial_5',
                   'id_origem',
                   'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id',
                                    'cus_ite_pre_item_id',
                                    'cus_ite_pre_unidade_producao_id',
                                    'cus_ite_pre_indicador_preco_id',
                                    'cus_ite_pre_moeda_preco',
                                    'cus_ite_pre_indicador_inbound_id',
                                    'cus_ite_pre_moeda_inbound',
                                    'valor_inicial_1',
                                    'valor_inicial_2',
                                    'valor_inicial_3',
                                    'valor_inicial_4',
                                    'valor_inicial_5',
                                    'id_origem',
                                    'tbcenarios_id').order_by('id')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            col_cus_ite_pre_item_id = 1
            col_cus_ite_pre_unidade_producao_id = 2
            col_cus_ite_pre_indicador_preco_id = 3
            col_cus_ite_pre_indicador_inbound_id = 5

            if row[col_cus_ite_pre_indicador_preco_id] == None or row[col_cus_ite_pre_indicador_preco_id] == '':
                indicador_1 = 0
            else:
                indicador_1 = row[col_cus_ite_pre_indicador_preco_id]

            if row[col_cus_ite_pre_indicador_inbound_id] == None or row[col_cus_ite_pre_indicador_inbound_id] == '':
                indicador_2 = 0
            else:
                indicador_2 = row[col_cus_ite_pre_indicador_inbound_id]

            # Vamos pegar os nomes
            cus_ite_pre_item_nome = TbCustoItem.objects.get(id=row[col_cus_ite_pre_item_id]).cus_ite_nome
            cus_ite_pre_unidade_producao_nome = TbUnidadeProducao.objects.get(
                id=row[col_cus_ite_pre_unidade_producao_id]).uni_nome
            if indicador_1 != 0:
                if TbIndicadores.objects.filter(id=indicador_1).count() != 0:
                    cus_ite_pre_indicador_preco_nome = TbIndicadores.objects.get(id=indicador_1).ind_nome
                else:
                    cus_ite_pre_indicador_preco_nome = 'Não cadastrado. Verificar.'
            else:
                cus_ite_pre_indicador_preco_nome = ''
            if indicador_2 != 0:
                if TbIndicadores.objects.filter(id=indicador_2).count() != 0:
                    cus_ite_pre_indicador_inbound_nome = TbIndicadores.objects.get(id=indicador_2).ind_nome
                else:
                    cus_ite_pre_indicador_inbound_nome = 'Não cadastrado. Verificar.'
            else:
                cus_ite_pre_indicador_inbound_nome = ''

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(2, cus_ite_pre_item_nome)
            list_aux.insert(4, cus_ite_pre_unidade_producao_nome)
            list_aux.insert(6, cus_ite_pre_indicador_preco_nome)
            list_aux.insert(9, cus_ite_pre_indicador_inbound_nome)
            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows

        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        # Lançando valores das filhas na página filha
        ws = wb.add_sheet('filhas')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbCustoItemPrecoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id', 'dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4', 'dau_valor_5',
                   'mae_id', 'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id', 'dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'dau_valor_5', 'mae_id', 'tbcenarios_id').order_by('mae_id', 'dau_order')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar Excel')

    # Action
    def update_valor_custo_variavel_adicionado(self, request, obj):
        update_custo_variavel_adicionado(obj.id)
        messages.success(request, _('Update do preço pelo valor do custo variável adicionado realizado com sucesso!'))

    update_valor_custo_variavel_adicionado.label = _("Update Preço/CustoVariável Adicionado")  # optional

    change_actions = ('update_valor_custo_variavel_adicionado',)

    model = TbCustoItemPreco
    form = TbCustoItemPrecoFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    """
    # Removendo icons de edição, adição e exclusão nos campos de foreignkey do form
    def get_form(self, request, obj=None, **kwargs):
        form = super(TbCustoItemPrecoAdmin, self).get_form(request, obj, **kwargs)
        field = form.base_fields['cus_ite_pre_item']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False

        field = form.base_fields['cus_ite_pre_unidade_producao']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False

        field = form.base_fields['cus_ite_pre_indicador_preco']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False

        field = form.base_fields['cus_ite_pre_indicador_inbound']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False
        return form
    """

    # Tiramos pois colocamos a seleção na queryset. Só aparece os registros que podem ser alterados pelo usuário
    '''
    def has_change_permission(self, request, obj=None):
        if obj:
            # Vamos ver se o usuário tem permissão para alterar essa tabela
            u = User.objects.get(username=request.user)
            if u.has_perm('tabelas.change_tbcustoitempreco'): # Tem permissão. Vamos ver se tem permissão para o tipo de item de custo
                # Vamos ver se é superuser
                current_user = request.user
                if current_user.is_superuser: # Se é superuser já retorna True
                    return True
                else:
                    # Vamos pegar o id do tipo do item de custo
                    retorno = False
                    id_custo_item = obj.cus_ite_pre_item_id
                    id_tipo = TbCustoItem.objects.get(id=id_custo_item).cus_ite_tipo_id
                    # Já tenho o id do tipo do item de custo. Tenho que pegar o id dos grupos que foram liberados para esse tipo de item de custo
                    lista_id_grupos_qs = TbCustoTipo.cus_tip_group.through.objects.filter(tbcustotipo_id=id_tipo)
                    for lista_id_grupos in lista_id_grupos_qs:
                        if u.groups.filter(id=lista_id_grupos.group_id).exists():
                            retorno = True
                            break

                    return retorno

            else:
                return False
        else:
            return True
    '''

    # Mostra somente os registros do cenário ativo e que podem ser alterados pelo usuário
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Vamos montar uma lista com os id que são permitidos edição
        queryset = TbCustoItemPreco.objects.filter(
            tbcenarios=cen_ativo)  # Primeiro selecionando os registros do cenário ativo
        lista_id = []

        # Vamos ver se o usuário tem permissão para alterar essa tabela
        u = User.objects.get(username=request.user)
        current_user = request.user
        if u.has_perm(
                'tabelas.change_tbcustoitempreco'):  # Tem permissão. Vamos ver se tem permissão para o tipo de item de custo
            for queryset_id in queryset:
                # Vamos ver se é superuser
                if current_user.is_superuser:  # Se é superuser já retorna True
                    lista_id.append(queryset_id.id)
                else:
                    # Vamos pegar o id do tipo do item de custo
                    id_tipo = TbCustoItem.objects.get(id=queryset_id.cus_ite_pre_item_id).cus_ite_tipo_id
                    # Já tenho o id do tipo do item de custo. Tenho que pegar o id dos grupos que foram liberados para esse tipo de item de custo
                    lista_id_grupos_qs = TbCustoTipo.cus_tip_group.through.objects.filter(tbcustotipo_id=id_tipo)
                    for lista_id_grupos in lista_id_grupos_qs:
                        if u.groups.filter(id=lista_id_grupos.group_id).exists():
                            lista_id.append(queryset_id.id)

        queryset = queryset.filter(id__in=lista_id)
        return queryset
        # return super(TbCustoItemPrecoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, id=1)

    # Mostra os campos valor_inicial_1, valor_inicial_2, valor_inicial_3 e valor_inicial_4 somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + (
            ('valor_inicial_1', 'valor_inicial_3'), ('valor_inicial_2', 'valor_inicial_4'), 'valor_inicial_5')
        return self.fields



    inlines = [TbCustoItemPrecoDaugtherAdmin]


# Registrando
admin.site.register(TbCustoItemPreco, TbCustoItemPrecoAdmin)

# Para mostrar os equipamentos / ordem de produção que estão produzindo esse tipo de produção
class TbTipoProducaoDaugtherAdmin(admin.TabularInline):

    fields = ('equ_codigo', 'equ_ordem_codigo', 'equ_ordem_descricao')

    model = TbEquipamentos
    show_change_link = True

    # Mostra somente os equipamentos/ordem  que estão no cenário ativo e que estão em um fluxo de produção ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cursor = connection.cursor()
        sql = "call public.equipamento_ordem_fluxo_ativo(" + str(cen_ativo) + ", null)"
        cursor.execute(sql)
        lista = cursor.fetchone()[0]
        cursor.close()

        return super(TbTipoProducaoDaugtherAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, id__in=lista)

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False


class TbTipoProducaoAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = (('tip_nome', 'ordem_usando'), ('tip_imagem', 'tip_imagem_tag'), 'tip_observacao')
    list_display = ['tip_nome', 'empresa', 'ordem_usando', 'tip_imagem', 'tip_imagem_tag', 'tip_observacao']
    search_fields = ['tip_nome', ]
    readonly_fields = ['tip_imagem_tag', 'ordem_usando']

    form = TbTipoProducaoFormAdmin

    def ordem_usando(self, obj): # Para mostrar a quantidade de Equipamento/Ordem de produção produzindo o tipo de produção
                                 # Consideramos somente aquelas que estão sendo usadas em fluxos de produção ativos
        if obj.pk != None:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular
            sql = "call public.qtde_tipo_producao(" + str(cen_ativo_id) + ", " + str(obj.id) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            return retorno
        else:
            return ''

    ordem_usando.short_description = _('Qtde Equip./Ordem')

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    inlines = [TbTipoProducaoDaugtherAdmin]


admin.site.register(TbTipoProducao, TbTipoProducaoAdmin)


class TbFamiliaProdutoAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = ('fam_pro_codigo',)
    list_display = ['fam_pro_codigo', 'empresa']


# Registrando
admin.site.register(TbFamiliaProduto, TbFamiliaProdutoAdmin)


class TbGrupoCenariosAdmin(_EmpresaFiltradaAdminMixin, admin.ModelAdmin):
    fields = ('gru_cen_codigo',)
    list_display = ['gru_cen_codigo', 'empresa']


# Registrando
admin.site.register(TbGrupoCenarios, TbGrupoCenariosAdmin)


class TbEquacaoAjustePrecoAdmin(_EmpresaFiltradaAdminMixin, DjangoObjectActions, admin.ModelAdmin):
    fields = (('equ_aju_pre_descricao', 'equ_aju_pre_moeda'),
              ('equ_aju_pre_constante_a', 'equ_aju_pre_observacao_a'),
              ('equ_aju_pre_constante_b', 'equ_aju_pre_observacao_b'),
              ('equ_aju_pre_constante_c', 'equ_aju_pre_observacao_c'),
              ('equ_aju_pre_constante_d', 'equ_aju_pre_observacao_d'),
              ('equ_aju_pre_constante_e', 'equ_aju_pre_observacao_e'),
              ('equ_aju_pre_constante_f', 'equ_aju_pre_observacao_f'),
              'equ_aju_pre_formula', ('equ_aju_pre_primeiro_titulo', 'equ_aju_pre_segundo_titulo'),
              ('equ_aju_pre_var', 'equ_aju_pre_valor1', 'equ_aju_pre_valor2'))
    # 🌟 NOVO (multi-empresa): mostra empresa na listagem -- antes não
    # existia list_display nenhum aqui (Django mostrava só o __str__).
    list_display = ['equ_aju_pre_descricao', 'empresa']

    # readonly_fields = ['equ_aju_pre_valor1', 'equ_aju_pre_valor2']

    model = TbEquacaoAjustePreco
    form = TbEquacaoAjustePrecoFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    """
    #Para criar um botão na parte superior do form para testar a fórmula (fica ao lado do botão Histórico
    def testar(self, request, obj):
        print('Botão Testar foi pressionado')

    testar.label = _("Testar")  # optional
    testar.short_description = _("Testar o cálculo")  # optional

    change_actions = ('testar',)
    """


# Registrando
admin.site.register(TbEquacaoAjustePreco, TbEquacaoAjustePrecoAdmin)




