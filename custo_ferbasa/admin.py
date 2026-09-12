import boto3
import xlwt  # xlwt para exportar para Excel formato .xls / xlrd para importar do Excel formato .xls. De acordo com internet é questão de segurança.
import openpyxl  # Para ler Excel xlsx
from boto3 import Session
from django.forms import Textarea
from reportlab.lib.pagesizes import elevenSeventeen
from xlrd.book import open_workbook_xls
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from equipamentos.models import TbEquipamentosConsumoEspecifico
from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from django_object_actions import DjangoObjectActions
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
import io
import operator
from django.http import FileResponse
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from datetime import date
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.lib import colors
from django.contrib.auth.models import User
from .forms import *
from .models import *
from fluxos.models import TbFluxoConsumoPadrao
from .tasks import importar_excel_producao_mensal_celery, importar_excel_distribuicao_ggf_mensal_celery, update_indicador_consumos_padroes, atualizar_genealogia_celery, calcular_consumo_especifico_periodo, calcular_custo_variavel_adicionado_periodo
from django.shortcuts import render
from django.db.models import Q

class TbItensProducaoDaugtherAdmin(admin.TabularInline):
    fields = ('item_consumo', 'qtde_consumo', 'indicador', 'valor_material', 'valor_ggf', 'tipo_insumo')
    readonly_fields = ('tipo_insumo', 'indicador')

    model = TbItensProducaoDaugther


    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False


class TbItensProducaoDaugther1Admin(admin.TabularInline):
    fields = ('chave', 'item_consumo', 'indicador')

    model = TbItensProducaoDaugther1

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False


class TbItensProducaoAdmin(admin.ModelAdmin):
    fields = (('ite_pro_codigo', 'ite_pro_descricao'), 'ite_pro_unidade', ('ite_pro_ano_mes_inicio', 'ite_pro_ano_mes_fim'), ('ite_pro_qtde_produzida_real', 'ite_pro_qtde_produzida_vazio', 'qtde_produzida_total'), ('ite_pro_percentual_variavel_ggf', 'ite_pro_percentual_fixo_outros_ggf'), ('ite_pro_ggf_variavel_direto', 'ite_pro_ggf_fixo_outros_direto'), ('ite_pro_ggf_variavel_pi', 'ite_pro_ggf_fixo_outros_pi'), 'ite_pro_observacao')
    list_display = ['id', 'ite_pro_codigo', 'ite_pro_descricao', 'ite_pro_unidade', 'ite_pro_percentual_variavel_ggf', 'ite_pro_percentual_fixo_outros_ggf']
    list_display_links = ['ite_pro_codigo', 'ite_pro_descricao']
    list_filter = ['ite_pro_codigo', 'ite_pro_descricao', 'ite_pro_percentual_variavel_ggf']
    search_fields = ['ite_pro_codigo', 'ite_pro_descricao', ]
    readonly_fields = ('ite_pro_ano_mes_inicio', 'ite_pro_ano_mes_fim', 'ite_pro_percentual_variavel_ggf', 'ite_pro_percentual_fixo_outros_ggf', 'ite_pro_qtde_produzida_real', 'ite_pro_qtde_produzida_vazio', 'qtde_produzida_total', 'ite_pro_ggf_variavel_direto', 'ite_pro_ggf_fixo_outros_direto', 'ite_pro_ggf_variavel_pi', 'ite_pro_ggf_fixo_outros_pi')

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbItensProducaoFormAdmin

    actions = ['atualizar_genealogia']

    def atualizar_genealogia(self, request, queryset):
        # Atualizando genealogia dos itens de produção
        atualizar_genealogia_celery.delay()
        messages.success(request, 'Atualizando genealogia dos Itens de Produção em segundo plano. Favor aguardar!')

    atualizar_genealogia.short_description = 'Atualizar Genealogia Itens de Produção (todos)'

    # Para permitir rodar atualizar_genealogia sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'atualizar_genealogia':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbItensProducao.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbItensProducaoAdmin, self).changelist_view(request, extra_context)

    inlines = [TbItensProducaoDaugtherAdmin, TbItensProducaoDaugther1Admin]


admin.site.register(TbItensProducao, TbItensProducaoAdmin)


class TbEstabelecimentosAdmin(admin.ModelAdmin):
    fields = (('est_codigo', 'est_nome'), 'est_observacao')
    list_display = ['est_codigo', 'est_nome', ]
    list_display_links = ['est_codigo', 'est_nome']
    list_filter = ['est_codigo', 'est_nome', ]
    search_fields = ['est_codigo', 'est_nome', ]

    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    form = TbEstabelecimentosFormAdmin


admin.site.register(TbEstabelecimentos, TbEstabelecimentosAdmin)


class TbGruposMaquinasAdmin(admin.ModelAdmin):
    fields = (('gru_maq_codigo', 'gru_maq_nome'), 'gru_maq_observacao')
    list_display = ['gru_maq_codigo', 'gru_maq_nome', ]
    list_display_links = ['gru_maq_codigo', 'gru_maq_nome']
    list_filter = ['gru_maq_codigo', 'gru_maq_nome']
    search_fields = ['gru_maq_codigo', 'gru_maq_nome']

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbGruposMaquinasFormAdmin


admin.site.register(TbGruposMaquinas, TbGruposMaquinasAdmin)


class TbItensConsumoAdmin(admin.ModelAdmin):
    fields = (('ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade'), ('ite_con_tipo', 'ite_subproduto'), 'ite_con_observacao')
    #list_display = ['ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade', 'ite_con_tipo', 'ite_subproduto', 'bonificacao']
    list_display = ['ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade', 'ite_con_tipo', 'ite_subproduto']
    list_display_links = ['ite_con_codigo', 'ite_con_descricao']
    list_filter = ['ite_con_codigo', 'ite_con_descricao', 'ite_con_tipo', 'ite_subproduto']
    list_editable = ['ite_subproduto',]
    search_fields = ['ite_con_codigo', 'ite_con_descricao', 'ite_con_tipo']

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    actions = ['exportar_excel', 'importar_excel']

    # Removendo opção de importar se usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.change_tbitensConsumo'):
            if 'importar_excel' in actions:
                del actions['importar_excel']

        return actions

    # Para permitir rodar importar_excel e exportar_excel sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'importar_excel' or request.POST['action'] == 'exportar_excel'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbItensConsumo.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)

        return super(TbItensConsumoAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        # try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Custo Ferbasa - Itens de Consumo.xls'

        bucket_name = 'spsferbasa'

        object_key = nome_arquivo

        # Vamos abrir o arquivo Excel que foi gravado no AWS S3
        s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
        bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)
        content = bucket_object.get()['Body'].read()

        wb = open_workbook_xls(file_contents=content)

        sheet = wb.sheet_by_index(0)
        # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
        total_colunas = sheet.ncols
        total_linhas = sheet.nrows
        lista_colunas = []
        for i in range(total_colunas):
            # o nome das colunas estão na linha 0
            lista_colunas.append(sheet.cell_value(0, i))

        if lista_colunas == ['Id', 'Código', 'Descrição', 'Unidade', 'Tipo', 'Subproduto', 'Observação']:
            #  Cabeçalho está correto.
            for i in range(total_linhas):  # A coluna do id da mãe é a de ordem 0
                if i > 0:  # Porque o i=0 é o cabeçalho
                    tab_obj = TbItensConsumo.objects.get(id=sheet.cell_value(i, 0))
                    tab_obj.ite_con_codigo = sheet.cell_value(i, 1)
                    tab_obj.ite_con_descricao = sheet.cell_value(i, 2)
                    tab_obj.ite_con_unidade = sheet.cell_value(i, 3)
                    tab_obj.ite_con_tipo = sheet.cell_value(i, 4)
                    tab_obj.ite_subproduto = sheet.cell_value(i, 5)
                    tab_obj.ite_con_observacao = sheet.cell_value(i, 6)
                    tab_obj.save()

            messages.success(request, 'Tabela Itens de Consumo foi atualizada com sucesso!')
            # Deletando arquivo no AWS S3
            s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            s3_client.delete_object(Bucket=bucket_name, Key=object_key)

        else:
            messages.error(request, 'Cabeçalho da planilha importada não está correto. Favor verificar!')

        # except:
        #    messages.error(request, 'Não foi encontrado o arquivo ' + nome_arquivo + ' no AWS S3. Favor verificar!')

    importar_excel.short_description = 'Importar Excel'

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados.

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Custo Ferbasa - Itens de Consumo.xls'
        response['Content-Disposition'] = 'attachment; filename=' + nome_arquivo

        wb = xlwt.Workbook(encoding='utf-8')
        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        ws = wb.add_sheet('Item de Consumo')

        # Sheet header, first row
        row_num = 0

        columns = ['Id', 'Código', 'Descrição', 'Unidade', 'Tipo', 'Subproduto', 'Observação']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id', 'ite_con_codigo', 'ite_con_descricao', 'ite_con_unidade', 'ite_con_tipo', 'ite_subproduto', 'ite_con_observacao').order_by('id')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel'

    form = TbItensConsumoFormAdmin


admin.site.register(TbItensConsumo, TbItensConsumoAdmin)


class TbContaContabilAdmin(admin.ModelAdmin):
    fields = (('con_con_codigo', 'con_con_descricao'), 'con_con_pessoal', 'con_con_observacao')
    list_display = ['con_con_codigo', 'con_con_descricao', 'con_con_pessoal']
    list_display_links = ['con_con_codigo', 'con_con_descricao']
    list_filter = ['con_con_codigo', 'con_con_descricao']
    search_fields = ['con_con_codigo', 'con_con_descricao']

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbContaContabilFormAdmin


admin.site.register(TbContaContabil, TbContaContabilAdmin)


class TbCentroCustoAdmin(admin.ModelAdmin):
    fields = (('cen_cus_codigo', 'cen_cus_referencia', 'cen_cus_descricao'), ('cen_cus_mod', 'cen_cus_moi'), 'cen_cus_observacao')
    list_display = ['cen_cus_codigo', 'cen_cus_referencia', 'cen_cus_descricao', 'cen_cus_mod', 'cen_cus_moi']
    list_display_links = ['cen_cus_codigo', 'cen_cus_descricao']
    list_filter = ['cen_cus_codigo', 'cen_cus_referencia', 'cen_cus_descricao']
    search_fields = ['cen_cus_codigo', 'cen_cus_descricao']

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbCentroCustoFormAdmin


admin.site.register(TbCentroCusto, TbCentroCustoAdmin)

class TbContaContabilCentroCustoAdmin(admin.ModelAdmin):
    fields = ('con_con_cen_cus_conta',
              ('con_con_cen_cus_cc', 'cc_referencia'),
              'con_con_cen_cus_estabelecimento',
              ('con_con_cen_cus_tipo', 'con_con_cen_cus_percentual'),
              'con_con_cen_cus_observacao'
              )

    list_display = ['con_con_cen_cus_conta', 'con_con_cen_cus_cc', 'cc_referencia', 'con_con_cen_cus_estabelecimento', 'con_con_cen_cus_tipo', 'con_con_cen_cus_percentual']
    list_filter = [('con_con_cen_cus_conta__con_con_descricao', custom_titled_filter('Conta Contábil')), ('con_con_cen_cus_cc__cen_cus_descricao', custom_titled_filter('Centro de Custo')), ('con_con_cen_cus_estabelecimento__est_nome', custom_titled_filter('Estabelecimento')), 'con_con_cen_cus_tipo']
    search_fields = ['con_con_cen_cus_conta__con_con_codigo', 'con_con_cen_cus_conta__con_con_descricao', 'con_con_cen_cus_cc__cen_cus_codigo', 'con_con_cen_cus_cc__cen_cus_descricao', 'con_con_cen_cus_estabelecimento__est_codigo', 'con_con_cen_cus_estabelecimento__est_nome']
    readonly_fields = ('cc_referencia',)

    # Opcional: Melhore a performance da listagem aqui também
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'con_con_cen_cus_conta',
            'con_con_cen_cus_cc'
        )

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbContaContabilCentroCustoAdminFormAdmin

    actions = ('exportar_excel', 'importar_excel',)

    # Removendo opção de importar se usuário não tiver permissão para editar a tabela

    def cc_referencia(self, obj):
        id_cc = obj.con_con_cen_cus_cc_id
        referencia_cc = TbCentroCusto.objects.get(id=id_cc).cen_cus_referencia
        if referencia_cc == '1':
            return '<--2025'
        else:
            return '2026-->'

    cc_referencia.short_description = 'Referência CC'

    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('tabelas.change_tbcontacontabilcentrocusto'):
            if 'importar_excel' in actions:
                del actions['importar_excel']

        return actions

    # Para permitir rodar importar_excel sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'importar_excel'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbContaContabilCentroCusto.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)

        return super(TbContaContabilCentroCustoAdmin, self).changelist_view(request, extra_context)

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados.

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Tabela Conta Contabil Centro Custo' + '.xlsx'

        response['Content-Disposition'] = 'attachment; filename=' + nome_arquivo

        import openpyxl

        wb = openpyxl.Workbook()
        sheet = wb.active

        # Call a Workbook() function of openpyxl
        # to create a new blank Workbook object
        wb = openpyxl.Workbook()

        # Get workbook active sheet
        # from the active attribute
        ws = wb.active

        # One can change the name of the title
        ws.title = "Plan 1"

        # Sheet header, first row
        row_num = 1

        #columns = ['Id', 'Descrição Conta Contábil/Centro de Custo', 'Tipo', 'Percentual (%)', 'Id Conta Contábil', 'Código Conta Contábil', 'Descrição Conta Contábil', 'Id Centro de Custo', 'Código Centro de Custo', 'Descrição Centro de Custo', 'Id Estabelecimento', 'Código Estabelecimento', 'Descrição Estabelecimento', 'Observação']
        columns = ['Id', 'Tipo', 'Percentual (%)', 'Id Conta Contábil',
                   'Código Conta Contábil', 'Descrição Conta Contábil', 'Id Centro de Custo', 'Código Centro de Custo',
                   'Descrição Centro de Custo', 'Id Estabelecimento', 'Código Estabelecimento',
                   'Descrição Estabelecimento', 'Observação']

        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

        rows = queryset.values_list('id', 'con_con_cen_cus_tipo', 'con_con_cen_cus_percentual', 'con_con_cen_cus_conta_id', 'con_con_cen_cus_conta__con_con_codigo', 'con_con_cen_cus_conta__con_con_descricao', 'con_con_cen_cus_cc_id', 'con_con_cen_cus_cc__cen_cus_codigo', 'con_con_cen_cus_cc__cen_cus_descricao', 'con_con_cen_cus_estabelecimento_id', 'con_con_cen_cus_estabelecimento__est_codigo', 'con_con_cen_cus_estabelecimento__est_nome',
                                    'con_con_cen_cus_observacao').order_by('con_con_cen_cus_conta__con_con_descricao')
        for row in rows:
            row_num += 1
            for col_num in range(1, len(columns) + 1):
                ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel'

    def importar_excel(self, request, queryset):
        # try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Tabela Conta Contabil Centro Custo.xlsx'  # Arquivo tem que estar no AWS no bucket spsferbasa
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

        if lista_colunas == ['Id', 'Tipo', 'Percentual (%)', 'Id Conta Contábil', 'Código Conta Contábil', 'Descrição Conta Contábil', 'Id Centro de Custo', 'Código Centro de Custo', 'Descrição Centro de Custo', 'Id Estabelecimento', 'Código Estabelecimento', 'Descrição Estabelecimento', 'Observação']:

            #  Cabeçalho do arquivo está correto.
            for i in range(total_linhas + 1):
                if i > 1:  # Porque a linha 1 é o cabeçalho
                    # Vamos pegar o registro pelo id informado na tabela
                    objeto = TbContaContabilCentroCusto.objects.get(id=sheet.cell(row=i, column=1).value)  # Pegamos todo o registro. Id na coluna 1
                    #objeto.con_con_cen_cus_descricao = sheet.cell(row=i, column=2).value.upper()
                    objeto.con_con_cen_cus_conta_id = sheet.cell(row=i, column=4).value
                    objeto.con_con_cen_cus_cc_id = sheet.cell(row=i, column=7).value
                    objeto.con_con_cen_cus_estabelecimento_id = sheet.cell(row=i, column=10).value
                    objeto.con_con_cen_cus_tipo = sheet.cell(row=i, column=2).value.upper()
                    objeto.con_con_cen_cus_percentual = sheet.cell(row=i, column=3).value
                    if sheet.cell(row=i, column=13).value is not None:
                        objeto.con_con_cen_cus_observacao = sheet.cell(row=i, column=13).value.upper
                    objeto.save()

            messages.success(request, nome_arquivo + ' foi atualizada com sucesso!')
            # Deletando arquivo no AWS S3
            s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
            s3_client.delete_object(Bucket=bucket_name, Key=object_key)

        else:

            messages.error(request, 'Cabeçalho do arquivo' + nome_arquivo + ' não está correto. Favor verificar!')

        # except:
        #    messages.error(request, 'Não foi encontrado o arquivo ' + nome_arquivo + ' no AWS S3. Favor verificar!')

    importar_excel.short_description = 'Importar Arquivo Excel da AWS'


admin.site.register(TbContaContabilCentroCusto, TbContaContabilCentroCustoAdmin)

class TbProducaoMensalAdmin(admin.ModelAdmin):
    fields = ('pro_men_ano_mes', 'pro_men_estabelecimento', 'pro_men_grupo_maquina', 'pro_men_ordem_producao', 'pro_men_item_producao', 'pro_men_qtde_produzida', 'pro_men_item_consumo', ('pro_men_qtde_consumo', 'indicador'), ('pro_men_valor_material', 'pro_men_valor_ggf', 'pro_men_valor_ultima_entrada'), 'pro_men_observacao')
    list_display = ['pro_men_ano_mes', 'pro_men_item_producao', 'pro_men_grupo_maquina', 'pro_men_item_consumo', 'pro_men_ordem_producao', 'indicador']
    list_display_links = ['pro_men_ano_mes', 'pro_men_item_producao', 'pro_men_item_consumo']
    list_filter = ('pro_men_ano_mes', ('pro_men_item_producao__ite_pro_descricao', custom_titled_filter('Item de Produção')), ('pro_men_grupo_maquina__gru_maq_nome', custom_titled_filter('Grupo Máquina')), ('pro_men_item_consumo__ite_con_descricao', custom_titled_filter('Item de Consumo')))
    search_fields = ['pro_men_ano_mes', 'pro_men_item_producao__ite_pro_codigo', 'pro_men_item_producao__ite_pro_descricao', 'pro_men_grupo_maquina__gru_maq_codigo', 'pro_men_grupo_maquina__gru_maq_nome', 'pro_men_item_consumo__ite_con_codigo', 'pro_men_item_consumo__ite_con_descricao', 'pro_men_ordem_producao']
    readonly_fields = ('indicador',)

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbProducaoMensalFormAdmin

    # Para formatar o campo pro_men_ano_mes com máscara
    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Para permitir rodar importar_excel e transferir_aws sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                # Fizemos um ajuste pois em tabelas grandes estoura a memória
                for u in TbProducaoMensal.objects.filter(id=1):
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)

        return super(TbProducaoMensalAdmin, self).changelist_view(request, extra_context)

    actions = ('exportar_excel', 'importar_excel')

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados.

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Produção Mensal' + '.xlsx'

        response['Content-Disposition'] = 'attachment; filename=' + nome_arquivo

        import openpyxl

        # Call a Workbook() function of openpyxl
        # to create a new blank Workbook object
        wb = openpyxl.Workbook()

        # Get workbook active sheet
        # from the active attribute
        ws = wb.active

        # One can change the name of the title
        ws.title = "Plan 1"

        # Sheet header, first row
        row_num = 1

        columns = ['Id', 'Ano/Mês', 'Estabelecimento', 'Grupo Máquina', 'Ordem Produção', 'Item Produção (Código)',  'Item Produção (Descrição)', 'Qtde Produzida', 'Item Consumo (Código)', 'Item Consumo (Descrição)', 'Qtde Consumo', 'Valor Material', 'Valor GGF', 'Valor Última Entrada', 'Observação']

        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

        rows = queryset.values_list('id', 'pro_men_ano_mes', 'pro_men_estabelecimento__est_nome', 'pro_men_grupo_maquina__gru_maq_nome', 'pro_men_ordem_producao', 'pro_men_item_producao', 'pro_men_qtde_produzida', 'pro_men_item_consumo', 'pro_men_qtde_consumo', 'pro_men_valor_material', 'pro_men_valor_ggf', 'pro_men_valor_ultima_entrada', 'pro_men_observacao')

        # Vamos incluir os nomes e valores de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[5]

            # Vamos pegar os nomes
            # Descrição do Item de Produção
            descricao_item_producao = TbItensProducao.objects.get(id=row[5]).ite_pro_descricao

            # Descrição do Item de Consumo
            descricao_item_consumo = TbItensConsumo.objects.get(id=row[7]).ite_con_descricao

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(6, descricao_item_producao)
            list_aux.insert(9, descricao_item_consumo)

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows

        for row in rows:
            row_num += 1
            for col_num in range(1, len(columns) + 1):
                ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel Registros Selecionados'

    def importar_excel(self, request, queryset):

        importar_excel_producao_mensal_celery.delay()
        #importar_excel_producao_mensal_celery()
        messages.success(request, 'Tabela Produção Mensal sendo atualizada em segundo plano!')

        '''
        # Comentamos pois jogamos para executar em segundo plano
        try:
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
            if lista_colunas == ['PERIODO', 'ESTABELECIMENTO', 'DESCRIÇÃO ESTABELECIMENTO', 'ORDEM PRODUÇÃO', 'GRUPO MÁQUINA', 'DESCRIÇÃO GRUPO MÁQUINA', 'ITEM PRODUÇÃO', 'DESCRIÇÃO ITEM PRODUÇÃO', 'UNIDADE', 'QTDADE PRODUÇÃO', 'ITEM CONSUMO', 'DESCRIÇÃO ITEM CONSUMO', 'UNIDADE', 'QTDADE CONSUMO', 'INDICADOR', 'VALOR MATERIAL', 'VALOR GGF', 'VALOR GGF FIXO', 'VALOR GGF VARIAVEL', 'VALOR GGF OUTROS', 'VALOR ULT ENTRADA', 'TIPO', 'SEQUENCIA', 'NIVEL']:
                #  Estamos usando o cabeçalho da planilha gerada pela área de TI da Ferbasa para não dar confusão. O arquivo vem em CSV. Temos que abrir no Excel e salvar com o nome
                #  Produção_Mensal.xlsx
                #  Cabeçalho do arquivo está correto.

                #  Vamos criar variáveis para ver se o registro já existe
                v_ano_mes = ''
                v_codigo_estabelecimento = ''
                v_codigo_grupo_maquina = ''
                v_ordem_producao = ''
                v_codigo_item_producao = ''
                v_codigo_item_consumo = ''

                for i in range(total_linhas + 1):

                    if i > 1 and sheet.cell(row=i, column=10).value > 0:  # Porque a linha 1 é o cabeçalho

                        # Só vamos considerar se qtde_produzida for maior que zero
                        # Ano/mês está vindo como número sem a barra. Vamos ajustar
                        v_ano_mes = str(sheet.cell(row=i, column=1).value)[0:6]
                        v_ano_mes = v_ano_mes[0:4] + '/' + v_ano_mes[4:6]
                        v_codigo_estabelecimento = sheet.cell(row=i, column=2).value.upper()
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

                            if objeto.ite_pro_descricao != sheet.cell(row=i, column=8).value.upper():

                                objeto.ite_pro_descricao = sheet.cell(row=i, column=8).value[0:60].upper()
                                objeto.save()
                            if objeto.ite_pro_unidade != sheet.cell(row=i, column=9).value.upper():
                                objeto.ite_pro_unidade = sheet.cell(row=i, column=9).value.upper()
                                objeto.save()
                        else:
                            # Não existe. Vamos criar e pegar o id
                            n = TbItensProducao.objects.create(ite_pro_codigo=v_codigo_item_producao.upper(),
                                                               ite_pro_descricao=sheet.cell(row=i, column=8).value[0:60].upper(),
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
                        if TbProducaoMensal.objects.filter(pro_men_ano_mes=v_ano_mes, pro_men_estabelecimento_id=v_id_estabelecimento, pro_men_grupo_maquina_id=v_id_grupo_maquina, pro_men_ordem_producao=v_ordem_producao, pro_men_item_producao=v_id_item_producao, pro_men_item_consumo=v_id_item_consumo).count() == 0:  # Não existe. Vamos criar o registro
                            TbProducaoMensal.objects.create(
                                pro_men_ano_mes=v_ano_mes,
                                pro_men_estabelecimento_id=v_id_estabelecimento,
                                pro_men_grupo_maquina_id=v_id_grupo_maquina,
                                pro_men_ordem_producao=v_ordem_producao,
                                pro_men_item_producao_id=v_id_item_producao,
                                pro_men_qtde_produzida=sheet.cell(row=i, column=10).value,
                                pro_men_item_consumo_id=v_id_item_consumo,
                                pro_men_qtde_consumo=sheet.cell(row=i, column=14).value,
                                pro_men_valor_material=sheet.cell(row=i, column=16).value,
                                pro_men_valor_ggf=sheet.cell(row=i, column=17).value,
                                pro_men_valor_ultima_entrada=sheet.cell(row=i, column=21).value
                            )
                        else:  # Já existe. Vamos atualizar os novos valores
                            objeto = TbProducaoMensal.objects.get(pro_men_ano_mes=v_ano_mes, pro_men_estabelecimento_id=v_id_estabelecimento, pro_men_grupo_maquina_id=v_id_grupo_maquina, pro_men_ordem_producao=v_ordem_producao, pro_men_item_producao=v_id_item_producao, pro_men_item_consumo=v_id_item_consumo)  # Pegamos todo o registro
                            objeto.pro_men_qtde_produzida = sheet.cell(row=i, column=10).value
                            objeto.pro_men_qtde_consumo = sheet.cell(row=i, column=14).value
                            objeto.pro_men_valor_material = sheet.cell(row=i, column=16).value
                            objeto.pro_men_valor_ggf = sheet.cell(row=i, column=17).value
                            objeto.pro_men_valor_ultima_entrada = sheet.cell(row=i, column=21).value
                            objeto.save()

                # Vamos calcular os % de variável no GGF para os itens de produção

                messages.success(request, 'Tabela Produção Mensal foi atualizada com sucesso!')
                # Deletando arquivo no AWS S3
                s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
                s3_client.delete_object(Bucket=bucket_name, Key=object_key)

            else:
                messages.error(request, 'Cabeçalho do arquivo' + nome_arquivo + ' não está correto. Favor verificar!')

        except:
            messages.error(request, 'Não foi encontrado o arquivo ' + nome_arquivo + ' no AWS S3. Favor verificar!')
        '''

    importar_excel.short_description = 'Importar Arquivo Excel da AWS'


admin.site.register(TbProducaoMensal, TbProducaoMensalAdmin)


class TbDistribuicaoGGFMensalAdmin(admin.ModelAdmin):
    fields = ('dis_ggf_men_ano_mes',
              'dis_ggf_men_estabelecimento',
              'dis_ggf_men_grupo_maquina',
              'dis_ggf_men_ordem_producao',
              'dis_ggf_men_item_producao',
              'dis_ggf_men_qtde_produzida',
              ('dis_ggf_men_conta_cc', # Este campo agora será uma busca dinâmica
              'referencia_cc'),
              'dis_ggf_men_valor',
              'dis_ggf_men_observacao')

    # Esta linha ativa a busca dinâmica via AJAX
    autocomplete_fields = ['dis_ggf_men_conta_cc']

    # Vamos aumentar a largura do campo
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # 1. Primeiro, deixa o Django criar o campo normalmente
        form_field = super().formfield_for_foreignkey(db_field, request, **kwargs)

        # 2. Se for o campo que queremos, injetamos o estilo no widget
        if db_field.name == "dis_ggf_men_conta_cc" and form_field:
            # Definimos uma largura fixa ou mínima para acomodar o texto longo
            form_field.widget.attrs.update({
                'style': 'min-width: 600px; width: 600px;'
            })

        return form_field

    list_display = ['dis_ggf_men_ano_mes', 'dis_ggf_men_item_producao', 'dis_ggf_men_grupo_maquina', 'dis_ggf_men_conta_cc', 'referencia_cc', 'dis_ggf_men_ordem_producao', 'valor_especifico']
    list_display_links = ['dis_ggf_men_ano_mes', 'dis_ggf_men_item_producao', 'dis_ggf_men_conta_cc']
    list_filter = ('dis_ggf_men_ano_mes', ('dis_ggf_men_item_producao__ite_pro_descricao', custom_titled_filter('Item de Produção')), ('dis_ggf_men_grupo_maquina__gru_maq_nome', custom_titled_filter('Grupo Máquina')), 'dis_ggf_men_ordem_producao')
    search_fields = ['dis_ggf_men_ano_mes', 'dis_ggf_men_item_producao__ite_pro_descricao', 'dis_ggf_men_grupo_maquina__gru_maq_nome', 'dis_ggf_men_conta_cc__con_con_cen_cus_descricao', 'dis_ggf_men_ordem_producao']
    readonly_fields = ('valor_especifico', 'referencia_cc')

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbDistribuicaoGGFMensalFormAdmin

    # Para formatar o campo dis_ggf_men_ano_mes com máscara
    class Media:
        js = ('jquery.mask.min.js', 'custom.js')


    actions = ('exportar_excel', 'importar_excel',)

    # Otimização crucial para a listagem (changelist)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Otimiza o campo dis_ggf_men_conta_cc E seus relacionamentos internos
        return qs.select_related(
            'dis_ggf_men_conta_cc',
            'dis_ggf_men_conta_cc__con_con_cen_cus_conta',
            'dis_ggf_men_conta_cc__con_con_cen_cus_cc'
        )

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados.

        response = HttpResponse(content_type='application/ms-excel')
        nome_arquivo = 'Distribuição GGF Mensal' + '.xlsx'

        response['Content-Disposition'] = 'attachment; filename=' + nome_arquivo

        import openpyxl

        #wb = openpyxl.Workbook()
        #sheet = wb.active

        # Call a Workbook() function of openpyxl
        # to create a new blank Workbook object
        wb = openpyxl.Workbook()

        # Get workbook active sheet
        # from the active attribute
        ws = wb.active

        # One can change the name of the title
        ws.title = "Plan 1"

        # Sheet header, first row
        row_num = 1

        columns = ['Id', 'Ano/Mês', 'Estabelecimento', 'Grupo Máquina', 'Ordem Produção', 'Item Produção (Código)', 'Item Produção (Descrição)', 'Qtde Produzida', 'ID Conta/CC', 'Tipo Conta/CC', 'Código Conta Contábil', 'Conta Contábil', 'Código Centro de Custo', 'Referência Centro de Custo', 'Centro de Custo', 'Valor', 'Observação']

        for col_num in range(1, len(columns) + 1):
            ws.cell(row=row_num, column=col_num).value = columns[col_num - 1]

        rows = queryset.values_list('id', 'dis_ggf_men_ano_mes', 'dis_ggf_men_estabelecimento__est_nome', 'dis_ggf_men_grupo_maquina__gru_maq_nome', 'dis_ggf_men_ordem_producao', 'dis_ggf_men_item_producao__ite_pro_codigo', 'dis_ggf_men_item_producao__ite_pro_descricao',
                                    'dis_ggf_men_qtde_produzida', 'dis_ggf_men_conta_cc_id', 'dis_ggf_men_conta_cc__con_con_cen_cus_tipo', 'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_codigo', 'dis_ggf_men_conta_cc__con_con_cen_cus_conta__con_con_descricao', 'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_codigo', 'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_referencia', 'dis_ggf_men_conta_cc__con_con_cen_cus_cc__cen_cus_descricao',
                                    'dis_ggf_men_valor', 'dis_ggf_men_observacao')

        for row in rows:
            row_num += 1
            for col_num in range(1, len(columns) + 1):
                ws.cell(row=row_num, column=col_num).value = row[col_num - 1]

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel Registros Selecionados'

    # Para permitir rodar importar_excel sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                # Fizemos um ajuste pois em tabelas grandes estoura a memória
                for u in TbDistribuicaoGGFMensal.objects.filter(id=1):
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)

        return super(TbDistribuicaoGGFMensalAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):

        importar_excel_distribuicao_ggf_mensal_celery.delay()
        messages.success(request, 'Distribuição GGF Mensal sendo atualizada em segundo plano!')

    importar_excel.short_description = 'Importar Arquivo Excel da AWS'


admin.site.register(TbDistribuicaoGGFMensal, TbDistribuicaoGGFMensalAdmin)

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


class TbConsumoEspecificoDaugtherAdmin(admin.TabularInline):
    fields = ('ano_mes', 'qtde_produzida', 'qtde_desviada', 'percentual_desvio', 'qtde_consumo', 'indicador', 'indicador_d_0', 'indicador_d_p')
    readonly_fields = ('ano_mes', 'qtde_produzida', 'qtde_desviada', 'qtde_consumo', 'indicador', 'indicador_d_0', 'indicador_d_p', 'percentual_desvio')

    model = TbConsumoEspecificoDaugther
    form = TbConsumoEspecificoDaugtherFormAdmin

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False


class TbConsumoEspecificoDaugther1Admin(admin.TabularInline):
    fields = ('codigo', 'consumo_especifico', 'indicador_atual')
    readonly_fields = ('indicador_atual',)

    model = TbConsumoEspecificoDaugther1
    form = TbConsumoEspecificoDaugther1FormAdmin
    fk_name = 'mae'


# Para mostrar os consumos padrões que estão usando o indicador
class TbIndicadorConsumoPadraoDaugtherAdmin(admin.TabularInline):
    fields = ('flu_con_pad_descricao', 'flu_con_pad_from_equipamento', 'flu_con_pad_to_equipamento', 'valor_medio_indicador', 'valor_indicador')
    readonly_fields = ('valor_indicador', 'valor_medio_indicador')

    show_change_link = True

    model = TbFluxoConsumoPadrao

    # form = TbProdutoFluxoProducaoDaugtherFormAdmin

    def valor_medio_indicador(self, obj):
        # Vamos calcular o valor médio do indicador no consumo padrão do SPS

        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_indicador_consumo_padrao(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        if retorno != None:

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.4f', retorno, True)
        else:
            retorno = ''

        return retorno

    valor_medio_indicador.short_description = 'Valor Médio SPS'

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbIndicadorConsumoPadraoDaugtherAdmin, self).get_queryset(request).filter(tbcenarios_id=cen_ativo)
        # Atenção: observar que não é preciso selecionar os registros onde o consumo específico foi indicado na tabela de consumo padrão
        # Quando colocamos essa classe como tabularinline com a tabela de consumo específico, a selecção é automática pois o id do consumo específico é o link entre as duas tabelas
        # Isso me parece ser coisa do Python mesmo ou do Django.

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

# Para mostrar os consumos específicos que estão usando o indicador
class TbIndicadorConsumoEspecificoDaugtherAdmin(admin.TabularInline):
    fields = ('equ_con_esp_equipamento', 'equ_con_esp_custoitempreco', 'valor_medio_indicador', 'valor_indicador')
    readonly_fields = ('valor_indicador', 'valor_medio_indicador')

    show_change_link = True

    model = TbEquipamentosConsumoEspecifico

    def valor_medio_indicador(self, obj):
        # Vamos calcular o valor médio do indicador cadastrado no consumo específico no SPS

        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_indicador_consumo_especifico_equipamento(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        if retorno != None:

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.4f', retorno, True)

        else:

            retorno = ''

        return retorno

    valor_medio_indicador.short_description = 'Valor Médio SPS'

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbIndicadorConsumoEspecificoDaugtherAdmin, self).get_queryset(request).filter(tbcenarios_id=cen_ativo)
        # Atenção: observar que não é preciso selecionar os registros onde o consumo específico foi indicado na tabela de consumo padrão
        # Quando colocamos essa classe como tabularinline com a tabela de consumo específico, a selecção é automática pois o id do consumo específico é o link entre as duas tabelas
        # Isso me parece ser coisa do Python mesmo ou do Django.

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbConsumoEspecificoAdmin(DjangoObjectActions, admin.ModelAdmin):
    filter_horizontal = ('con_esp_grupo_maquina', 'con_esp_item_producao', 'con_esp_item_consumo')
    list_display = ['id', 'con_esp_descricao', 'con_esp_compartilhar', 'con_esp_area_responsavel', 'con_esp_validado', 'con_esp_ano_mes_inicio', 'con_esp_ano_mes_fim', 'status_colored_model', 'desv_prod', 'indicador_sps', 'valor_medio_indicador_consumo_padrao', 'valor_medio_indicador_consumo_especifico', 'con_esp_qtde_produzida', 'con_esp_qtde_desviada', 'sps']
    list_editable = ['con_esp_validado']
    list_display_links = ['id', 'con_esp_descricao']
    readonly_fields = ('con_esp_criado_por', 'con_esp_alterado_por', 'con_esp_qtde_produzida', 'con_esp_qtde_produzida', 'con_esp_qtde_desviada', 'con_esp_qtde_consumo', 'indicador_sps', 'status_colored_model', 'sps', 'indicador_d_0', 'indicador_d_p', 'legenda', 'percentual_desvio', 'desv_prod', 'valor_medio_indicador_consumo_padrao', 'valor_medio_indicador_consumo_especifico')
    search_fields = ['con_esp_descricao',]

    def valor_medio_indicador_consumo_padrao(self, obj):
        # Vamos calcular o valor médio do indicador no consumo padrão cadastrado no SPS

        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_indicador_consumo_padrao_consumo_especifico(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos veriricar se valor está diferente do calculado
        if retorno != obj.con_esp_indicador:
            color = 'red'
        else:
            color = 'green'

        if retorno != None:
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.4f', retorno, True)
            return format_html('<b style="color:{};">{}</b>', color, retorno, )

        else:
            return ''

        #return retorno

    valor_medio_indicador_consumo_padrao.short_description = 'Valor Médio SPS (Cons. Padrão)'

    def valor_medio_indicador_consumo_especifico(self, obj):
        # Vamos calcular o valor médio do indicador cadastrado no consumo específico no SPS

        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_indicador_consumo_especifico_equipamento_consumo_especifico(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos veriricar se valor está diferente do calculado
        if retorno != obj.con_esp_indicador:
            color = 'red'
        else:
            color = 'green'


        if retorno != None:
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.4f', retorno, True)
            return format_html('<b style="color:{};">{}</b>', color, retorno,)

        else:
            return ''

        #return retorno

    valor_medio_indicador_consumo_especifico.short_description = 'Valor Médio SPS (Cons. Espec.)'

    class sps_ListFilter(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'SPS'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'sps'

        def lookups(self, request, model_admin):
            # Vamos pegar registros que estão na tabela
            qs = TbConsumoEspecifico.objects.filter()

            # Vamos montar a tuple na mão
            tuple = []
            tuple_aux = []
            for q in qs:
                # Vamos verificar se o indicador está ou não no SPS

                # Vamos pegar o cenário ativo (id e nome)
                cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

                if TbFluxoConsumoPadrao.objects.filter(flu_con_pad_consumo_especifico_id=q.id, tbcenarios_id=cen_ativo).exists() or TbEquipamentosConsumoEspecifico.objects.filter(equ_con_consumo_especifico_id=q.id, tbcenarios_id=cen_ativo).exists():
                    sps_atual = 'Sim'
                else:
                    sps_atual = 'Não'

                if sps_atual not in tuple_aux:
                    tuple_aux.append(sps_atual)
                    tuple.append((sps_atual, sps_atual))

            return tuple

        def queryset(self, request, queryset):
            lista = []
            if self.value():
                # Vamos filtrar a queryset de acordo com o sps selecionado
                # Vamos montar uma lista com os anexos que tem status de acordo com o status selecionado
                qs = TbConsumoEspecifico.objects.filter()
                for q in qs:
                    # Vamos verificar se o indicador está ou não no SPS

                    # Vamos pegar o cenário ativo (id e nome)
                    cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

                    if TbFluxoConsumoPadrao.objects.filter(flu_con_pad_consumo_especifico_id=q.id, tbcenarios_id=cen_ativo).exists() or TbEquipamentosConsumoEspecifico.objects.filter(equ_con_consumo_especifico_id=q.id, tbcenarios_id=cen_ativo).exists():
                        sps_atual = 'Sim'
                    else:
                        sps_atual = 'Não'

                    if sps_atual== self.value():
                        lista.append(q.id)

                return queryset.filter(id__in=lista)


    list_filter = ('con_esp_descricao', 'con_esp_area_responsavel', 'con_esp_validado', 'con_esp_qtde_produzida', 'con_esp_compartilhar', 'con_esp_criado_por', sps_ListFilter)

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    # Mostra campos se está ou não incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # Está incluindo
            return (('con_esp_descricao', 'con_esp_compartilhar'),) + (('con_esp_area_responsavel', 'con_esp_validado', 'con_esp_desvio_producao'), ('con_esp_ano_mes_inicio', 'con_esp_ano_mes_fim', 'con_esp_ajustar_periodo', 'con_esp_producao_minima'), 'con_esp_item_producao', 'con_esp_observacao',)
        else:  # Não está incluindo
            if str(request.user) == str(obj.con_esp_criado_por) or request.user.is_superuser: # Se usuário é quem criou ou superuser, mostra o campo compartilhado para eventual alteração
                return (('con_esp_descricao', 'con_esp_criado_por', 'con_esp_alterado_por', 'con_esp_compartilhar'),) + (('con_esp_area_responsavel', 'con_esp_validado', 'con_esp_desvio_producao'), ('con_esp_ano_mes_inicio', 'con_esp_ano_mes_fim', 'con_esp_ajustar_periodo', 'con_esp_producao_minima', 'status_colored_model'), ('con_esp_qtde_produzida', 'con_esp_qtde_desviada', 'percentual_desvio', 'con_esp_qtde_consumo', 'indicador_sps', 'sps'), ('indicador_d_0', 'indicador_d_p'), 'con_esp_item_producao', 'con_esp_grupo_maquina', 'con_esp_item_consumo', ('con_esp_equacao', 'con_esp_tipo_equacao'), 'con_esp_observacao')
            else:
                return (('con_esp_descricao', 'con_esp_criado_por', 'con_esp_alterado_por'),) + (
                ('con_esp_area_responsavel', 'con_esp_validado', 'con_esp_desvio_producao', 'sps'), ('con_esp_ano_mes_inicio', 'con_esp_ano_mes_fim', 'con_esp_ajustar_periodo', 'con_esp_producao_minima', 'status_colored_model'),
                ('con_esp_qtde_produzida', 'con_esp_qtde_desviada', 'percentual_desvio', 'con_esp_qtde_consumo', 'indicador_sps', 'sps'), ('indicador_d_0', 'indicador_d_p'), 'con_esp_item_producao', 'con_esp_grupo_maquina', 'con_esp_item_consumo', ('con_esp_equacao', 'con_esp_tipo_equacao'), 'con_esp_observacao')

        return self.fields

    form = TbConsumoEspecificoFormAdmin

    # Para formatar os campos con_esp_ano_mes_inicio e con_esp_ano_mes_fim com máscara
    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente registros criados pelo usuário, que são compartilhados. Se superUser mostra tudo
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        current_user = request.user
        if not current_user.is_superuser:
            # Vamos filtrar
            qs = qs.filter(Q(con_esp_criado_por=str(current_user)) | Q(con_esp_compartilhar=True))

        return qs

    actions = ['calcular_periodo', 'exportar_qs_analise_excel', 'exportar_qs_pdf', 'validar_consumos_especificos', 'desvalidar_consumos_especificos']

    def calcular_periodo(self, request, queryset):
        if 'apply' in request.POST:  # if user pressed 'apply' on intermediate page

            # Cool thing is that params will have the same names as in forms.py
            ano_mes_inicio = request.POST["ano_mes_inicio"]
            ano_mes_fim = request.POST["ano_mes_fim"]

            if ano_mes_inicio == '':
                self.message_user(request, 'Informar o Ano/Mês Início ou Cancelar!')
            else:
                if ano_mes_fim == '':
                    self.message_user(request, 'Informar o Ano/Mês Fim ou Cancelar!')
                else:
                    if ano_mes_fim < ano_mes_inicio:
                        self.message_user(request, 'Ano/mês Fim NÃO PODE SER MENOR que Ano/Mês Início. Favor verificar!')
                    else:
                        # Tudo ok. Vamos calcular
                        # Passando o flag para False para todos os indicadores cadastrados
                        TbConsumoEspecifico.objects.update(flag=False)
                        # Vamos passar o flag para True para os indicadores que estão na queryset
                        lista_id = queryset.values_list('id')
                        TbConsumoEspecifico.objects.filter(id__in=lista_id).update(flag=True, con_esp_status = 'A CALCULAR')
                        # Rodando o procedimento em segundo plano
                        calcular_consumo_especifico_periodo.delay(ano_mes_inicio, ano_mes_fim)

                        # Show alert that everything is cool
                        self.message_user(request, "Calculando / Atualizando em segundo plano os consumos específicos para os registros selecionados e para o periodo de " + ano_mes_inicio + ' a ' + ano_mes_fim + '. Refresh para verificar o Status')

                        # Return to previous page
                        return HttpResponseRedirect(request.get_full_path())

        if 'cancelar' in request.POST:  # usuário cancelou

            # Return to previous page
            return HttpResponseRedirect(request.get_full_path())

        # Create form and pass the data which objects were selected before triggering 'broadcast' action
        # We create an intermediate page right here
        form = CalcularPeriodoForm(initial={'_selected_action': queryset.values_list('id', flat=True)})

        # We need to create a template of intermediate page with form - but this is really easy
        return render(request, "admin/consumo_especifico_periodo.html", {'items': queryset, 'form': form})

    calcular_periodo.short_description = 'Calcular / Atualizar Por Período'

    def validar_consumos_especificos(self, request, queryset):

        consumos = queryset.values_list('id', )

        for consumo in consumos:
            tab_obj = TbConsumoEspecifico.objects.get(id=consumo[0])
            tab_obj.con_esp_validado = 1
            tab_obj.save()

    validar_consumos_especificos.short_description = 'Validar Consumos Esp. Selecionados'

    def desvalidar_consumos_especificos(self, request, queryset):

        consumos = queryset.values_list('id', )

        for consumo in consumos:
            tab_obj = TbConsumoEspecifico.objects.get(id=consumo[0])
            tab_obj.con_esp_validado = 0
            tab_obj.save()

    desvalidar_consumos_especificos.short_description = 'Desvalidar Consumos Esp. Selecionados'

    def exportar_qs_analise_excel(self, request, queryset):
        # Só exporta o cenário selecionado
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Consumo Específico Análise Áreas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Consumo Específico')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Montando cabeçalho
        ws.write(row_num, 0, 'CONSUMO ESPECÍFICO ANÁLISE ÁREAS RESPONSÁVEIS', font_style)
        row_num = row_num + 2
        ws.write(row_num, 0, 'ÁREA', font_style)
        ws.write(row_num, 1, 'ID', font_style)
        ws.write(row_num, 2, 'DESCRIÇÃO', font_style)
        ws.write(row_num, 3, 'ANO/MÊS INÍCIO', font_style)
        ws.write(row_num, 4, 'ANO/MÊS FIM', font_style)
        ws.write(row_num, 5, 'VALOR INDICADOR', font_style)
        ws.write(row_num, 6, 'VALOR SPS ATUAL (CONS. PADRÃO)', font_style)
        ws.write(row_num, 7, 'VALOR SPS ATUAL (CONS. ESPEC.)', font_style)
        ws.write(row_num, 8, 'ID CENÁRIO SPS', font_style)
        ws.write(row_num, 9, 'ANÁLISE DA ÁREA', font_style)

        # Reordenando a queryset
        queryset = sorted(queryset, key=operator.attrgetter('con_esp_area_responsavel'))

        for qs in queryset:
            row_num = row_num + 1
            ws.write(row_num, 0, qs.get_con_esp_area_responsavel_display())
            ws.write(row_num, 1, qs.id)
            ws.write(row_num, 2, qs.con_esp_descricao)
            ws.write(row_num, 3, qs.con_esp_ano_mes_inicio)
            ws.write(row_num, 4, qs.con_esp_ano_mes_fim)
            ws.write(row_num, 5, qs.con_esp_indicador)

            # Vamos calcular o valor médio do indicador no consumo padrão do SPS
            cursor = connection.cursor()
            # Expressão SQL
            # Estamos passando o id do consumo específico
            sql = "call public.valor_medio_indicador_consumo_padrao_base_id(" + str(qs.id) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            ws.write(row_num, 6, retorno)

            # Vamos calcular o valor médio do indicador no consumo específico dos equipamentos do SPS
            cursor = connection.cursor()
            # Expressão SQL
            # Estamos passando o id do consumo específico
            sql = "call public.valor_medio_indicador_consumo_especifico_equipamento_base_id(" + str(qs.id) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            ws.write(row_num, 7, retorno)

            ws.write(row_num, 8, TbCenarios.objects.get(cen_ativo=True).id)

        wb.save(response)

        return response

    exportar_qs_analise_excel.short_description = 'Exportar Análise Áreas Excel'

    def exportar_qs_pdf(self, request, queryset):

        #  exportar_pdf_qs(queryset)

        # Create a file-like buffer to receive PDF data.
        buffer = io.BytesIO()

        # Create the PDF object, using the buffer as its "file."
        p = canvas.Canvas(buffer)

        for qs in queryset:

            # Definindo padrões a serem usados no PDF
            nome_arquivo = 'Consumo Específico SPS CF'

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
            p.drawCentredString(largura_pagina / 2, altura_pagina - 59, 'Área Responsável: ' + qs.get_con_esp_area_responsavel_display())
            p.line(5, altura_pagina - 65, largura_pagina - 5, altura_pagina - 65)

            line_number = altura_pagina - 78
            p.setFont("Helvetica-Bold", 8)

            # Vamos pegar os resultados do periodo
            p.drawString(15, line_number, 'Ano/Mês Início : ' + qs.con_esp_ano_mes_inicio)
            p.drawString(117, line_number, 'Ano/Mês Fim : ' + qs.con_esp_ano_mes_fim)
            p.drawString(240, line_number, 'Qtde Produzida : ' + locale.format_string('%.2f', qs.con_esp_qtde_produzida, True))
            p.drawString(370, line_number, 'Qtde Consumo : ' + locale.format_string('%.2f', qs.con_esp_qtde_consumo, True))
            p.drawString(510, line_number, 'Indicador : ' + locale.format_string('%.4f', qs.con_esp_indicador, True))

            line_number = line_number - 5
            p.line(5, line_number, largura_pagina - 5, line_number)
            line_number = line_number - 11

            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, line_number, 'CONSUMOS PADRÕES NO SPS USANDO INDICADOR')
            p.setFont("Helvetica-Bold", 7)
            line_number = line_number - 9
            p.drawString(15, line_number, 'Descrição')
            p.drawString(105, line_number, 'Id')
            p.drawString(120, line_number, 'Cenário')
            p.drawString(150, line_number, 'From')
            p.drawString(390, line_number, 'To')
            p.drawString(530, line_number, 'Indicador Médio')
            # Vamos criar uma lista com os consumos padrões que estão usando o indicador
            lista_consumo_padrao_qs = TbFluxoConsumoPadrao.objects.filter(flu_con_pad_consumo_especifico_id=qs.id)
            p.setFont("Helvetica-Bold", 6)
            for lista_consumo_padrao in lista_consumo_padrao_qs:
                line_number = line_number - 9
                p.drawString(15, line_number, lista_consumo_padrao.flu_con_pad_descricao)
                p.drawString(105, line_number, str(lista_consumo_padrao.id))
                p.drawString(130, line_number, str(lista_consumo_padrao.tbcenarios_id))
                p.drawString(150, line_number, str(lista_consumo_padrao.flu_con_pad_from_equipamento))
                p.drawString(390, line_number, str(lista_consumo_padrao.flu_con_pad_to_equipamento))

                # Calculando o valor do indicador médio para o consumo padrão
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.valor_medio_indicador_consumo_padrao(" + str(lista_consumo_padrao.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()
                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                if retorno:
                    retorno = locale.format_string('%.4f', retorno, True)
                else:
                    print(lista_consumo_padrao.id)
                    retorno = '0,0000'
                p.drawString(550, line_number, retorno)

            line_number = line_number - 5
            p.line(5, line_number, largura_pagina - 5, line_number)
            line_number = line_number - 11
            # Vamos pegar os itens de consumo
            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE CONSUMO')
            lista_id_item_consumo_qs = TbConsumoEspecifico.con_esp_item_consumo.through.objects.filter(tbconsumoespecifico_id=qs.id)
            p.setFont("Helvetica-Bold", 6)
            for lista_id_item_consumo in lista_id_item_consumo_qs:
                # Tenho o id do item de consumo. Vamos pegar a descrição
                descricao_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
                codigo_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_codigo
                line_number = line_number - 11
                p.drawCentredString(largura_pagina / 2, line_number, descricao_item_consumo + ' / ' + codigo_item_consumo)

            line_number = line_number - 5
            p.line(5, line_number, largura_pagina - 5, line_number)

            # Vamos pegar os grupos máquinas
            p.setFont("Helvetica-Bold", 10)
            line_number = line_number - 13
            p.drawCentredString(largura_pagina / 2, line_number, 'GRUPOS MÁQUINAS')
            lista_id_grupo_maquina_qs = TbConsumoEspecifico.con_esp_grupo_maquina.through.objects.filter(tbconsumoespecifico_id=qs.id)
            p.setFont("Helvetica-Bold", 6)
            for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
                # Tenho o id do grupo máquina. Vamos pegar o nome
                nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
                codigo_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_codigo
                line_number = line_number - 11
                p.drawCentredString(largura_pagina / 2, line_number, nome_grupo_maquina + ' / ' + codigo_grupo_maquina)

            line_number = line_number - 5
            p.line(5, line_number, largura_pagina - 5, line_number)

            # Vamos pegar os itens de produção
            p.setFont("Helvetica-Bold", 10)
            line_number = line_number - 13
            p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE PRODUÇÃO')
            lista_id_item_producao_qs = TbConsumoEspecifico.con_esp_item_producao.through.objects.filter(tbconsumoespecifico_id=qs.id)
            p.setFont("Helvetica-Bold", 6)
            for lista_id_item_producao in lista_id_item_producao_qs:
                # Tenho o id do item de produção. Vamos pegar a descrição
                descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
                codigo_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_codigo
                line_number = line_number - 11
                p.drawCentredString(largura_pagina / 2, line_number, descricao_item_producao + ' / ' + codigo_item_producao)

            line_number = line_number - 5
            p.line(5, line_number, largura_pagina - 5, line_number)

            # Vamos pegar os resultados históricos
            p.setFont("Helvetica-Bold", 10)
            line_number = line_number - 13
            p.drawCentredString(largura_pagina / 2, line_number, 'RESULTADOS HISTÓRICOS')
            p.setFont("Helvetica-Bold", 4.5)
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, 'ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR')

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
                        qtde_produzida = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id, ano_mes=ano_mes_corrente).qtde_produzida
                        qtde_consumo = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id, ano_mes=ano_mes_corrente).qtde_consumo
                        indicador = TbConsumoEspecificoDaugther.objects.get(mae_id=qs.id, ano_mes=ano_mes_corrente).indicador
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

    exportar_qs_pdf.short_description = 'Exportar PDF'

    # Para formatar os campos con_esp_ano_mes_inicio e con_esp_ano_mes_fim com máscara

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    inlines = [TbConsumoEspecificoDaugther1Admin, TbConsumoEspecificoDaugtherAdmin, TbIndicadorConsumoPadraoDaugtherAdmin, TbIndicadorConsumoEspecificoDaugtherAdmin]

    # e está adicionando não mostra inlines definido acima
    def get_inline_instances(self, request, obj=None):
        return obj and super(TbConsumoEspecificoAdmin, self).get_inline_instances(request, obj) or []

    # Salvando aqui no admin. É para poder lançar mensagem ao usuário
    def save_model(self, request, obj, form, change):
        if obj.pk is None:
            # adding
            # Vamos alterar o campo con_esp_criado_por
            obj.con_esp_criado_por = str(request.user)

        else:
            # changing
            # Vamos alterar o campo con_esp_alterado_por
            obj.con_esp_alterado_por = str(request.user)

            obj.con_esp_status = 'CALCULANDO'
            obj.con_esp_qtde_produzida = 0
            obj.con_esp_qtde_desviada = 0
            obj.con_esp_qtde_consumo = 0
            obj.con_esp_indicador = 0
            messages.set_level(request, messages.ERROR)
            messages.error(request, 'Registro foi alterado com sucesso. Calculando indicadores em segundo plano. Se Status = CALCULANDO, refresh tela para atualizar.')

        super().save_model(request, obj, form, change)

        # Vamos calcular o consumo específico
        from .tasks import calcula_consumo_especifico
        #print(obj.pk)
        transaction.on_commit(lambda: calcula_consumo_especifico.delay(obj.pk))

    change_actions = ('update_consumos_padroes', 'exportar_excel', 'exportar_pdf')

    # Action
    def update_consumos_padroes(self, request, obj):
        update_indicador_consumos_padroes(obj.id)
        messages.success(request, 'Update dos consumos padrões vinculados realizado com sucesso!')

    update_consumos_padroes.label = "Update Consumos Padrões"  # optional

    def exportar_excel(self, request, obj):
        # Só exporta o cenário selecionado
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Consumo Específico.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Consumo Específico')

        # Vamos montar uma queryset com os valores das filhas
        filhas = TbConsumoEspecificoDaugther.objects.filter(mae_id=obj.id)

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos pegar a descrição do consumo específico
        nome_consumo_específico = obj.con_esp_descricao
        ws.write(row_num, 0, 'CONSUMO ESPECÍFICO: ID                  = ' + str(obj.id), font_style)
        row_num = row_num + 1
        ws.write(row_num, 0, '                                         DESCRIÇÃO = ' + nome_consumo_específico, font_style)

        # Vamos mostrar a área responsável
        row_num = row_num + 2
        ws.write(row_num, 0, 'ÁREA RESPONSÁVEL = ' + obj.get_con_esp_area_responsavel_display(), font_style)

        # Vamos pegar os itens de consumo
        row_num = row_num + 2
        ws.write(row_num, 0, 'ITENS DE CONSUMO:', font_style)
        lista_id_item_consumo_qs = TbConsumoEspecifico.con_esp_item_consumo.through.objects.filter(tbconsumoespecifico_id=obj.id)
        for lista_id_item_consumo in lista_id_item_consumo_qs:
            # Tenho o id do item de consumo. Vamos pegar a descrição
            descricao_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + descricao_item_consumo, font_style)

        # Vamos pegar os grupos máquina
        row_num = row_num + 2
        ws.write(row_num, 0, 'GRUPOS MÁQUINAS:', font_style)
        lista_id_grupo_maquina_qs = TbConsumoEspecifico.con_esp_grupo_maquina.through.objects.filter(tbconsumoespecifico_id=obj.id)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquinao. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + nome_grupo_maquina, font_style)

        # Vamos pegar os itens de prudução
        row_num = row_num + 2
        ws.write(row_num, 0, 'ITENS DE PRODUÇÃO:', font_style)
        lista_id_item_producao_qs = TbConsumoEspecifico.con_esp_item_producao.through.objects.filter(tbconsumoespecifico_id=obj.id)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de producao. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + descricao_item_producao, font_style)

        columns = ['Ano/Mês', 'Produção', 'Consumo', 'Indicador']

        row_num += 2
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('ano_mes', 'qtde_produzida', 'qtde_consumo', 'indicador').order_by('ano_mes')

        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.label = 'Exportar Excel'

    def exportar_pdf(self, request, obj):

        # Create a file-like buffer to receive PDF data.
        buffer = io.BytesIO()

        # Create the PDF object, using the buffer as its "file."
        p = canvas.Canvas(buffer)

        # Definindo padrões a serem usados no PDF
        nome_arquivo = 'Consumo Específico - ID ' + str(obj.id) + ' - PDF'

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
        p.drawCentredString(largura_pagina / 2, altura_pagina - 45, str(obj.id) + ' - ' + obj.con_esp_descricao)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 59, 'Área Responsável: ' + obj.get_con_esp_area_responsavel_display())
        p.line(5, altura_pagina - 65, largura_pagina - 5, altura_pagina - 65)

        line_number = altura_pagina - 78
        p.setFont("Helvetica-Bold", 8)

        # Vamos pegar os resultados do periodo
        p.drawString(15, line_number, 'Ano/Mês Início : ' + obj.con_esp_ano_mes_inicio)
        p.drawString(117, line_number, 'Ano/Mês Fim : ' + obj.con_esp_ano_mes_fim)
        p.drawString(240, line_number, 'Qtde Produzida : ' + locale.format_string('%.2f', obj.con_esp_qtde_produzida, True))
        p.drawString(370, line_number, 'Qtde Consumo : ' + locale.format_string('%.2f', obj.con_esp_qtde_consumo, True))
        p.drawString(510, line_number, 'Indicador : ' + locale.format_string('%.4f', obj.con_esp_indicador, True))

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)
        line_number = line_number - 11

        # Vamos pegar os itens de consumo
        p.setFont("Helvetica-Bold", 10)
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE CONSUMO')
        lista_id_item_consumo_qs = TbConsumoEspecifico.con_esp_item_consumo.through.objects.filter(tbconsumoespecifico_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_consumo in lista_id_item_consumo_qs:
            # Tenho o id do item de consumo. Vamos pegar a descrição
            descricao_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
            codigo_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_consumo + ' / ' + codigo_item_consumo)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os grupos máquinas
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'GRUPOS MÁQUINAS')
        lista_id_grupo_maquina_qs = TbConsumoEspecifico.con_esp_grupo_maquina.through.objects.filter(tbconsumoespecifico_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquina. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            codigo_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, nome_grupo_maquina + ' / ' + codigo_grupo_maquina)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os itens de produção
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE PRODUÇÃO')
        lista_id_item_producao_qs = TbConsumoEspecifico.con_esp_item_producao.through.objects.filter(tbconsumoespecifico_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de produção. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            codigo_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_producao + ' / ' + codigo_item_producao)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os resultados históricos
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'RESULTADOS HISTÓRICOS')
        p.setFont("Helvetica-Bold", 4.5)
        line_number = line_number - 11
        p.drawCentredString(largura_pagina / 2, line_number, 'ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR   |   ANO/MÈS QTDE PROD. QTDE CONS. INDICADOR')

        linha_inicial_historico = line_number

        # Vamos pegar o ano/mês máximo calculado para o consumo específico
        ano_mes_maximo_qs = TbConsumoEspecificoDaugther.objects.filter(mae_id=obj.id).order_by('-ano_mes')
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
                if TbConsumoEspecificoDaugther.objects.filter(mae_id=obj.id, ano_mes=ano_mes_corrente):
                    qtde_produzida = TbConsumoEspecificoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).qtde_produzida
                    qtde_consumo = TbConsumoEspecificoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).qtde_consumo
                    indicador = TbConsumoEspecificoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).indicador
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

        resultado_historico_qs = TbConsumoEspecificoDaugther.objects.filter(mae_id=obj.id).order_by('ano_mes')

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

    exportar_pdf.short_description = 'Exportar PDF'

admin.site.register(TbConsumoEspecifico, TbConsumoEspecificoAdmin)

class TbCustoVariavelAdicionadoDaugtherAdmin(admin.TabularInline): # Histórico
    fields = ('ano_mes', 'qtde_produzida', 'qtde_desviada', 'percentual_desvio', 'custo_variavel_adicionado_material', 'custo_variavel_adicionado_ggf', 'custo_var_adic_total', 'custo_variavel_adicionado_material_p', 'custo_variavel_adicionado_ggf_p', 'custo_var_adic_total_p', 'custo_variavel_adicionado_material_p_d', 'custo_variavel_adicionado_ggf_p_d', 'custo_var_adic_total_p_d', 'custo_variavel', 'custo_fixo_outros', 'custo_total', 'custo_variavel_ggf_direto', 'custo_variavel_desv', 'custo_fixo_outros_desv', 'custo_total_desv', 'custo_variavel_ggf_direto_desv')
    readonly_fields = ('ano_mes', 'qtde_produzida', 'qtde_desviada', 'custo_variavel_adicionado_material', 'custo_variavel_adicionado_ggf', 'custo_var_adic_total', 'custo_variavel_adicionado_material_p', 'custo_variavel_adicionado_ggf_p', 'custo_var_adic_total_p', 'custo_variavel_adicionado_material_p_d', 'custo_variavel_adicionado_ggf_p_d', 'custo_var_adic_total_p_d', 'percentual_desvio')

    model = TbCustoVariavelAdicionadoDaugther
    form = TbCustoVariavelAdicionadoDaugtherFormAdmin

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False


class TbCustoVariavelAdicionadoDaugther1Admin(admin.TabularInline): # Complemetar
    fields = ('custo_variavel_adicionado', 'custo_variavel_adicionado_atual', 'consumo_especifico', 'indicador_atual')
    readonly_fields = ('custo_variavel_adicionado_atual', 'indicador_atual')

    model = TbCustoVariavelAdicionadoDaugther1
    form = TbCustoVariavelAdicionadoDaugther1FormAdmin
    fk_name = 'mae'


# Para mostrar os custo/itens/preços que estão usando o custo variável adicionado
class TbCustoItemPrecoDaugtherAdmin(admin.TabularInline):
    fields = ('cus_ite_pre_item', 'cus_ite_pre_unidade_producao', 'valor_medio_custo_variavel_adicionado', 'valor_custo_variavel_adicionado')
    readonly_fields = ('valor_custo_variavel_adicionado', 'valor_medio_custo_variavel_adicionado')

    show_change_link = True

    model = TbCustoItemPreco

    def valor_medio_custo_variavel_adicionado(self, obj):
        # Vamos calcular o valor médio do custo variavel adicionado do custo/item/preço que está usando o indicador
        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_custo_variavel_adicionado_custo_item_preco(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    valor_medio_custo_variavel_adicionado.short_description = 'Valor Médio SPS'

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbCustoItemPrecoDaugtherAdmin, self).get_queryset(request).filter(tbcenarios_id=cen_ativo)
        # Atenção: observar que não é preciso selecionar os registros onde o custo variável adicioado foi indicado na tabela de custo/item/preço
        # Quando colocamos essa classe como tabularinline com a tabela de custo variavel adicioado, a seleção é automática pois o id do custo variavel adicionado é o link entre as duas tabelas
        # Isso me parece ser coisa do Python mesmo ou do Django.

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbCustoVariavelAdicionadoAdmin(DjangoObjectActions, admin.ModelAdmin):
    list_display = ['cus_var_adi_descricao', 'cus_var_adi_validado', 'cus_var_adi_ano_mes_inicio', 'cus_var_adi_ano_mes_fim', 'status_colored_model', 'desv_prod', 'valor_medio_custo_variavel_adicionado', 'custo_variavel_adicionado_total', 'cus_var_adi_custo_variavel_adicionado_material', 'cus_var_adi_custo_variavel_adicionado_ggf', 'cus_var_adi_qtde_produzida', 'cus_var_adi_qtde_desviada', 'sps']
    list_editable = ['cus_var_adi_validado']
    list_display_links = ['cus_var_adi_descricao']
    filter_horizontal = ('cus_var_adi_item_producao', 'cus_var_adi_grupo_maquina', 'cus_var_adi_item_consumo')
    readonly_fields = ('cus_var_adi_qtde_produzida', 'cus_var_adi_custo_variavel_adicionado_material', 'cus_var_adi_custo_variavel_adicionado_ggf', 'cus_var_adi_custo_variavel_ggf_direto', 'custo_variavel_adicionado_total', 'cus_var_adi_custo_variavel', 'cus_var_adi_custo_fixo_outros', 'cus_var_adi_custo_total',
                       'cus_var_adi_custo_variavel_desv', 'cus_var_adi_custo_fixo_outros_desv', 'cus_var_adi_custo_total_desv', 'cus_var_adi_custo_variavel_ggf_direto_desv', 'status_colored_model', 'custo_variavel_adicionado_total', 'sps', 'cus_var_adi_qtde_desviada',
                       'cus_var_adi_custo_variavel_adicionado_material_p', 'cus_var_adi_custo_variavel_adicionado_ggf_p', 'custo_variavel_adicionado_total_p',
                       'cus_var_adi_custo_variavel_adicionado_material_p_d', 'cus_var_adi_custo_variavel_adicionado_ggf_p_d', 'custo_variavel_adicionado_total_p_d', 'percentual_desvio', 'desv_prod', 'valor_medio_custo_variavel_adicionado')
    search_fields = ['cus_var_adi_descricao', ]

    def valor_medio_custo_variavel_adicionado(self, obj):
        # Vamos calcular o valor médio do custo variavel adicionado do custo/item/preço que está usando o indicador
        cursor = connection.cursor()
        # Expressão SQL
        sql = "call public.valor_medio_custo_variavel_adicionado_custo_variavel_adicionado(" + str(obj.id) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        if retorno is not None:
            retorno = locale.format_string('%.2f', retorno, True)
        else:
            retorno =''

        return retorno

    valor_medio_custo_variavel_adicionado.short_description = 'Valor Médio SPS'


    class sps_ListFilter(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'SPS'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'sps'

        def lookups(self, request, model_admin):
            # Vamos pegar registros que estão na tabela
            qs = TbCustoVariavelAdicionado.objects.filter()

            # Vamos montar a tuple na mão
            tuple = []
            tuple_aux = []
            for q in qs:
                # Vamos verificar se o indicador está ou não no SPS

                # Vamos pegar o cenário ativo (id e nome)
                cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

                if TbCustoItemPreco.objects.filter(cus_ite_pre_custo_variavel_adiconado_id=q.id, tbcenarios_id=cen_ativo).exists():
                    sps_atual = 'Sim'
                else:
                    sps_atual = 'Não'

                if sps_atual not in tuple_aux:
                    tuple_aux.append(sps_atual)
                    tuple.append((sps_atual, sps_atual))

            return tuple

        def queryset(self, request, queryset):
            lista = []
            if self.value():
                # Vamos filtrar a queryset de acordo com o sps selecionado
                # Vamos montar uma lista com os anexos que tem status de acordo com o status selecionado
                qs = TbCustoVariavelAdicionado.objects.filter()
                for q in qs:
                    # Vamos verificar se o indicador está ou não no SPS

                    # Vamos pegar o cenário ativo (id e nome)
                    cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

                    if TbCustoItemPreco.objects.filter(cus_ite_pre_custo_variavel_adiconado_id=q.id, tbcenarios_id=cen_ativo).exists():
                        sps_atual = 'Sim'
                    else:
                        sps_atual = 'Não'

                    if sps_atual== self.value():
                        lista.append(q.id)

                return queryset.filter(id__in=lista)

    list_filter = ('cus_var_adi_descricao', 'cus_var_adi_validado', 'cus_var_adi_qtde_produzida', sps_ListFilter)

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbCustoVariavelAdicionadoFormAdmin

    # Mostra campos se está ou não incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # Está incluindo
            return ('cus_var_adi_descricao', ('cus_var_adi_ano_mes_inicio', 'cus_var_adi_ano_mes_fim', 'cus_var_adi_validado', 'cus_var_adi_ajustar_periodo', 'cus_var_adi_producao_minima', 'cus_var_adi_desvio_producao', 'cus_var_adi_subproduto_producao'), 'cus_var_adi_item_producao', 'cus_var_adi_observacao',)
        else:  # Não está incluindo
            return (('cus_var_adi_descricao', 'cus_var_adi_validado', 'status_colored_model'), ('cus_var_adi_ano_mes_inicio', 'cus_var_adi_ano_mes_fim', 'cus_var_adi_ajustar_periodo', 'cus_var_adi_producao_minima'), ('cus_var_adi_qtde_produzida', 'cus_var_adi_qtde_desviada', 'percentual_desvio', 'cus_var_adi_desvio_producao', 'cus_var_adi_subproduto_producao'), ('cus_var_adi_custo_variavel_adicionado_material', 'cus_var_adi_custo_variavel_adicionado_ggf', 'custo_variavel_adicionado_total', 'sps'), ('cus_var_adi_custo_variavel_adicionado_material_p', 'cus_var_adi_custo_variavel_adicionado_ggf_p', 'custo_variavel_adicionado_total_p'), ('cus_var_adi_custo_variavel_adicionado_material_p_d', 'cus_var_adi_custo_variavel_adicionado_ggf_p_d', 'custo_variavel_adicionado_total_p_d'), ('cus_var_adi_custo_variavel', 'cus_var_adi_custo_fixo_outros', 'cus_var_adi_custo_total', 'cus_var_adi_custo_variavel_ggf_direto'), ('cus_var_adi_custo_variavel_desv', 'cus_var_adi_custo_fixo_outros_desv', 'cus_var_adi_custo_total_desv', 'cus_var_adi_custo_variavel_ggf_direto_desv'), 'cus_var_adi_item_producao', 'cus_var_adi_grupo_maquina', 'cus_var_adi_item_consumo', 'cus_var_adi_observacao')

        return self.fields

    actions = ['calcular_periodo', 'validar_custo_variavel_adicionado', 'desvalidar_custo_variavel_adicionado']

    def calcular_periodo(self, request, queryset):
        # Temos que verificar antes se na tabela de Conta Contábil e Centro de Custo existe algum registro aguardando classificação
        # con_con_cen_cus_tipo = 'A'
        if TbContaContabilCentroCusto.objects.filter(con_con_cen_cus_tipo='A').count() == 0:  # Não temos registro aguardando classificação

            if 'apply' in request.POST:  # if user pressed 'apply' on intermediate page
                # Cool thing is that params will have the same names as in forms.py
                ano_mes_inicio = request.POST["ano_mes_inicio"]
                ano_mes_fim = request.POST["ano_mes_fim"]

                if ano_mes_inicio == '':
                    self.message_user(request, 'Informar o Ano/Mês Início ou Cancelar!')
                else:
                    if ano_mes_fim == '':
                        self.message_user(request, 'Informar o Ano/Mês Fim ou Cancelar!')
                    else:
                        if ano_mes_fim < ano_mes_inicio:
                            self.message_user(request, 'Ano/mês Fim NÃO PODE SER MENOR que Ano/Mês Início. Favor verificar!')
                        else:
                            # Tudo ok. Vamos calcular
                            # Passando o flag para False para todos os indicadores cadastrados
                            TbCustoVariavelAdicionado.objects.update(flag=False)
                            # Vamos passar o flag para True para os indicadores que estão na queryset
                            lista_id = queryset.values_list('id')
                            TbCustoVariavelAdicionado.objects.filter(id__in=lista_id).update(flag=True, cus_var_adi_status = 'A CALCULAR')
                            # Rodando o procedimento em segundo plano
                            calcular_custo_variavel_adicionado_periodo.delay(ano_mes_inicio, ano_mes_fim)

                            # Show alert that everything is cool
                            self.message_user(request, "Calculando / Atualizando em segundo plano o(s) indicador(es) para os registros selecionados e para o periodo de " + ano_mes_inicio + ' a ' + ano_mes_fim + '. Refresh para verificar o Status')

                            # Return to previous page
                            return HttpResponseRedirect(request.get_full_path())

            if 'cancelar' in request.POST:  # usuário cancelou
                # Return to previous page
                return HttpResponseRedirect(request.get_full_path())

            # Create form and pass the data which objects were selected before triggering 'broadcast' action
            # We create an intermediate page right here
            form = CalcularPeriodoForm(initial={'_selected_action': queryset.values_list('id', flat=True)})

            # We need to create a template of intermediate page with form - but this is really easy
            return render(request, "admin/custo_variavel_adicionado_periodo.html", {'items': queryset, 'form': form})

        else:
            messages.set_level(request, messages.ERROR)
            messages.error(request,'TEMOS REGISTRO NA TABELA DE CONTA CONTÁBIL / CENTRO DE CUSTO AGUARDANDO CLASSIFICAÇÃO. FAVOR VERIFICAR!')

    calcular_periodo.short_description = 'Calcular / Atualizar Por Período'

    def validar_custo_variavel_adicionado(self, request, queryset):

        custos = queryset.values_list('id', )

        for custo in custos:
            tab_obj = TbCustoVariavelAdicionado.objects.get(id=custo[0])
            tab_obj.cus_var_adi_validado = 1
            tab_obj.save()

    validar_custo_variavel_adicionado.short_description = 'Validar Custos Variáveis AdicionadoS Selecionados'

    def desvalidar_custo_variavel_adicionado(self, request, queryset):

        custos = queryset.values_list('id', )

        for custo in custos:
            tab_obj = TbCustoVariavelAdicionado.objects.get(id=custo[0])
            tab_obj.cus_var_adi_validado = 0
            tab_obj.save()

    desvalidar_custo_variavel_adicionado.short_description = 'Desvalidar Custos Variáveis AdicionadoS Selecionados'

    # Para formatar os campos cus_var_adi_ano_mes_inicio e cus_var_adi_ano_mes_fim com máscara
    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    inlines = [TbCustoVariavelAdicionadoDaugther1Admin, TbCustoVariavelAdicionadoDaugtherAdmin, TbCustoItemPrecoDaugtherAdmin]

    # e está adicionando não mostra inlines definido acima
    def get_inline_instances(self, request, obj=None):
        return obj and super(TbCustoVariavelAdicionadoAdmin, self).get_inline_instances(request, obj) or []

    def save_model(self, request, obj, form, change):
        if obj.pk is None:
            # adding
            pass
        else:
            # changing
            obj.cus_var_adi_qtde_produzida = 0
            obj.cus_var_adi_qtde_desviada = 0
            obj.cus_var_adi_custo_variavel_adicionado_material = 0
            obj.cus_var_adi_custo_variavel_adicionado_ggf = 0
            obj.cus_var_adi_custo_variavel_adicionado_material_p = 0
            obj.cus_var_adi_custo_variavel_adicionado_ggf_p = 0
            obj.cus_var_adi_custo_variavel_adicionado_material_p_d = 0
            obj.cus_var_adi_custo_variavel_adicionado_ggf_p_d = 0
            obj.cus_var_adi_custo_variavel_ggf_direto = 0
            obj.cus_var_adi_custo_variavel = 0
            obj.cus_var_adi_custo_fixo_outros = 0
            obj.cus_var_adi_custo_total = 0
            obj.cus_var_adi_custo_variavel_desv = 0
            obj.cus_var_adi_custo_total_desv = 0
            obj.cus_var_adi_custo_fixo_outros_desv = 0
            obj.cus_var_adi_custo_variavel_ggf_direto_desv = 0

            #Temos que verificar antes se na tabela de Conta Contábil e Centro de Custo existe algum registro aguardando classificação
            # con_con_cen_cus_tipo = 'A'
            if TbContaContabilCentroCusto.objects.filter(con_con_cen_cus_tipo = 'A').count() == 0:  # Não temos registro aguardaando classificação
                obj.cus_var_adi_status = 'CALCULANDO'
                messages.set_level(request, messages.ERROR)
                messages.error(request, 'Registro foi alterado com sucesso. Calculando custos em segundo plano. Se Status = CALCULANDO, refresh tela para atualizar.')

                super().save_model(request, obj, form, change)

                # Vamos calcular o custo variável adicionado, que será executado após o save
                from .tasks import calcula_custo_variavel_adicionado
                #print(obj.pk)
                transaction.on_commit(lambda: calcula_custo_variavel_adicionado.delay(obj.pk))
            else:
                obj.cus_var_adi_status = 'ERRO'
                messages.set_level(request, messages.ERROR)
                messages.error(request, 'TEMOS REGISTRO NA TABELA DE CONTA CONTÁBIL / CENTRO DE CUSTO AGUARDANDO CLASSIFICAÇÃO. FAVOR VERIFICAR!')
                super().save_model(request, obj, form, change)


    change_actions = ('exportar_excel', 'exportar_pdf', 'exportar_itens_consumo_excel')

    def exportar_itens_consumo_excel(self, request, obj):
        # Só exporta o cenário selecionado
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Itens Custo Variável Total.xls"'

        produto_qs = obj.cus_var_adi_item_producao.values_list('id', flat=True) # Lista com itens de produção selecionados
        grupo_maquina_qs = obj.cus_var_adi_grupo_maquina.values_list('id', flat=True) # Grupo máquinas selecionados

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Itens Custo Variável Total')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        ws.write(row_num, 0, 'ITENS DE CONSUMO CUSTO VARIÁVEL TOTAL', font_style)

        row_num = row_num + 2
        ws.write(row_num, 0, 'PERÍODO: ' + obj.cus_var_adi_ano_mes_inicio + ' a ' + obj.cus_var_adi_ano_mes_fim, font_style)

        # Vamos pegar os itens de produção
        row_num = row_num + 2
        ws.write(row_num, 0, 'ITENS DE PRODUÇÃO:', font_style)
        lista_id_item_producao_qs = TbCustoVariavelAdicionado.cus_var_adi_item_producao.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de producao. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + descricao_item_producao, font_style)

        # Vamos pegar os grupos máquina
        row_num = row_num + 2
        ws.write(row_num, 0, 'GRUPOS MÁQUINAS:', font_style)
        lista_id_grupo_maquina_qs = TbCustoVariavelAdicionado.cus_var_adi_grupo_maquina.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquinao. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + nome_grupo_maquina, font_style)

        row_num = row_num + 2
        columns = ['Código', 'Descrição', 'Indicador', 'Custo Variável']
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Vamos selecionar os id dos itens de consumo para os produtos e grupos máquinas informados.Isso na tabela de produção mensal
        item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs, pro_men_grupo_maquina_id__in=grupo_maquina_qs)
        lista_item_consumo = []
        for item_consumo in item_consumo_qs:
            if item_consumo.pro_men_item_consumo_id not in lista_item_consumo:
                lista_item_consumo.append(item_consumo.pro_men_item_consumo_id)

        wtf = TbItensConsumo.objects.filter(id__in=lista_item_consumo)

        custo_variavel_total = 0

        for choice in wtf:

            # Vamos calcular o indicador para o item de consumo
            cursor = connection.cursor()
            # Expressão SQL
            sql = "call public.indicador_custo_variavel_adicionado_item_consumo(" + str(obj.pk) + ", " + str(choice.id) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]

            # Vamos calcular o custo variável para o item de consumo
            # Expressão SQL
            sql = "call public.custo_variavel_variavel_adicionado_item_consumo(" + str(obj.pk) + ", " + str(choice.id) + ", 0)"
            cursor.execute(sql)
            retorno1 = cursor.fetchone()[0]
            cursor.close()

            # Temos que verificar se é desvio e se é para considerar como produção ou não
            if retorno < 0: # Ou é desvio ou subproduto
                # Se for desvio e é para considerar como produção, não considera
                if choice.ite_subproduto == False and TbCustoVariavelAdicionado.objects.get(id=obj.id).cus_var_adi_desvio_producao == True:
                    retorno = 0
                    retorno1 = 0

            custo_variavel_total = custo_variavel_total + retorno1

            if retorno != 0:
                row_num = row_num + 1
                ws.write(row_num, 0 , choice.ite_con_codigo)
                ws.write(row_num, 1, choice.ite_con_descricao)
                ws.write(row_num, 2, retorno)
                ws.write(row_num, 3, retorno1)

        # Vamos pegar o Variável GGF Direto
        row_num = row_num + 1
        ws.write(row_num, 1, 'VARIÁVEL GGF DIRETO')
        if TbCustoVariavelAdicionado.objects.get(id=obj.id).cus_var_adi_desvio_producao == False:
            ws.write(row_num, 3, obj.cus_var_adi_custo_variavel_ggf_direto)
            # Vamoes mostrar o custo variável total
            custo_variavel_total = custo_variavel_total + obj.cus_var_adi_custo_variavel_ggf_direto
        else:
            ws.write(row_num, 3, obj.cus_var_adi_custo_variavel_ggf_direto_desv)
            # Vamoes mostrar o custo variável total
            custo_variavel_total = custo_variavel_total + obj.cus_var_adi_custo_variavel_ggf_direto_desv

        row_num = row_num + 1
        ws.write(row_num, 1, 'CUSTO VARIÁVEL TOTAL', font_style)
        ws.write(row_num, 3, custo_variavel_total,  font_style)

        wb.save(response)

        return response

    exportar_itens_consumo_excel.label = 'Exportar Excel Itens Custo Variável'

    def exportar_excel(self, request, obj):
        # Só exporta o cenário selecionado
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Custo Variavel Adicionado.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Custo Variavel Adicionado')

        # Vamos montar uma queryset com os valores das filhas
        filhas = TbCustoVariavelAdicionadoDaugther.objects.filter(mae_id=obj.id)

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos pegar a descrição do custo variavel adicionado
        nome_custo_variavel_adicionado = obj.cus_var_adi_descricao
        ws.write(row_num, 0, 'CUSTO VARIAVEL ADICIONADO: ID = ' + str(obj.id), font_style)
        row_num = row_num + 1
        ws.write(row_num, 0, '                                      DESCRIÇÃO = ' + nome_custo_variavel_adicionado, font_style)

        # Vamos pegar os itens de prudução
        row_num = row_num + 2
        ws.write(row_num, 0, 'ITENS DE PRODUÇÃO:', font_style)
        lista_id_item_producao_qs = TbCustoVariavelAdicionado.cus_var_adi_item_producao.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de producao. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + descricao_item_producao, font_style)

        # Vamos pegar os grupos máquina
        row_num = row_num + 2
        ws.write(row_num, 0, 'GRUPOS MÁQUINAS:', font_style)
        lista_id_grupo_maquina_qs = TbCustoVariavelAdicionado.cus_var_adi_grupo_maquina.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquinao. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + nome_grupo_maquina, font_style)

        # Vamos pegar os itens de consumo desconsiderados (que estão vindo de outros equipamentos no fluxo de produção)
        row_num = row_num + 2
        ws.write(row_num, 0, 'ITENS DE CONSUMO DESCONSIDERADOS:', font_style)
        lista_id_item_consumo_qs = TbCustoVariavelAdicionado.cus_var_adi_item_consumo.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        for lista_id_item_consumo in lista_id_item_consumo_qs:
            # Tenho o id do item de consumo. Vamos pegar a descrição
            descricao_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
            row_num = row_num + 1
            ws.write(row_num, 0, ' - ' + descricao_item_consumo, font_style)

        columns = ['Ano/Mês', 'Qtde Produzida', 'Qtde Desviada', 'Desvio (%)', 'Custo Var. Adic. Material', 'Custo Var. Adic. GGF', 'Custo Var. Adic. Total', 'Custo Variável', 'Custo Fixo/Outros', 'Custo Total', 'Custo Var. GGF Direto']

        row_num += 2
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('ano_mes', 'qtde_produzida', 'qtde_desviada', 'custo_variavel_adicionado_material', 'custo_variavel_adicionado_ggf', 'custo_variavel', 'custo_fixo_outros', 'custo_total', 'custo_variavel_ggf_direto').order_by('ano_mes')

        for row in rows:
            row_num += 1
            for col_num in range(len(row)+2):
                if col_num <= 2:
                    ws.write(row_num, col_num, row[col_num], font_style)
                if col_num == 3:
                    ws.write(row_num, col_num, row[col_num - 1] * 100 / (row[col_num - 2] + row[col_num - 1]), font_style)
                if col_num == 4 or col_num == 5:
                    ws.write(row_num, col_num, row[col_num - 1], font_style)
                if col_num == 6:
                    ws.write(row_num, col_num, row[col_num - 2] + row[col_num - 3], font_style)
                if col_num > 6:
                    ws.write(row_num, col_num, row[col_num - 2], font_style)

        wb.save(response)

        return response

    exportar_excel.label = 'Exportar Excel'

    def exportar_pdf(self, request, obj):

        # Create a file-like buffer to receive PDF data.
        buffer = io.BytesIO()

        # Create the PDF object, using the buffer as its "file."
        p = canvas.Canvas(buffer)

        # Definindo padrões a serem usados no PDF
        nome_arquivo = 'Custo Variável Adiconado - ID ' + str(obj.id) + ' - PDF'

        largura_pagina = 210 * mm  # Padrão A4
        altura_pagina = 297 * mm  # Padrão A4
        titulo_relatorio = 'Custo Variável Adicionado'

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4

        p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

        # Cabeçalho do PDF
        # Setando tamanho do título e escrevendo os títulos no topo da página
        p.setFont("Helvetica-Bold", 18)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

        p.setFont("Helvetica-Bold", 10)
        p.drawCentredString(largura_pagina / 2, altura_pagina - 45, str(obj.id) + ' - ' + obj.cus_var_adi_descricao)
        p.line(5, altura_pagina - 55, largura_pagina - 5, altura_pagina - 55)

        line_number = altura_pagina - 68
        p.setFont("Helvetica-Bold", 8)

        # Vamos pegar os resultados do periodo
        p.drawString(15, line_number, 'Ano/Mês Início : ' + obj.cus_var_adi_ano_mes_inicio)
        p.drawString(117, line_number, 'Ano/Mês Fim : ' + obj.cus_var_adi_ano_mes_fim)
        p.drawString(220, line_number, 'Qtde Produzida : ' + locale.format_string('%.2f', obj.cus_var_adi_qtde_produzida, True))
        p.drawString(330, line_number, 'Material : ' + locale.format_string('%.2f', obj.cus_var_adi_custo_variavel_adicionado_material, True))
        p.drawString(410, line_number, 'GGF : ' + locale.format_string('%.2f', obj.cus_var_adi_custo_variavel_adicionado_ggf, True))
        p.drawString(510, line_number, 'Total : ' + locale.format_string('%.2f', obj.cus_var_adi_custo_variavel_adicionado_material + obj.cus_var_adi_custo_variavel_adicionado_ggf, True))

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os itens de produção
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE PRODUÇÃO')
        lista_id_item_producao_qs = TbCustoVariavelAdicionado.cus_var_adi_item_producao.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_producao in lista_id_item_producao_qs:
            # Tenho o id do item de produção. Vamos pegar a descrição
            descricao_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_descricao
            codigo_item_producao = TbItensProducao.objects.get(id=lista_id_item_producao.tbitensproducao_id).ite_pro_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_producao + ' / ' + codigo_item_producao)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os grupos máquinas
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'GRUPOS MÁQUINAS')
        lista_id_grupo_maquina_qs = TbCustoVariavelAdicionado.cus_var_adi_grupo_maquina.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_grupo_maquina in lista_id_grupo_maquina_qs:
            # Tenho o id do grupo máquina. Vamos pegar o nome
            nome_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_nome
            codigo_grupo_maquina = TbGruposMaquinas.objects.get(id=lista_id_grupo_maquina.tbgruposmaquinas_id).gru_maq_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, nome_grupo_maquina + ' / ' + codigo_grupo_maquina)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os itens de consumo desconsiderados
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'ITENS DE CONSUMO DESCONSIDERADOS')
        lista_id_item_consumo_qs = TbCustoVariavelAdicionado.cus_var_adi_item_consumo.through.objects.filter(tbcustovariaveladicionado_id=obj.id)
        p.setFont("Helvetica-Bold", 6)
        for lista_id_item_consumo in lista_id_item_consumo_qs:
            # Tenho o id do item de consumo. Vamos pegar a descrição
            descricao_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_descricao
            codigo_item_consumo = TbItensConsumo.objects.get(id=lista_id_item_consumo.tbitensconsumo_id).ite_con_codigo
            line_number = line_number - 11
            p.drawCentredString(largura_pagina / 2, line_number, descricao_item_consumo + ' / ' + codigo_item_consumo)

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Vamos pegar os resultados históricos
        p.setFont("Helvetica-Bold", 10)
        line_number = line_number - 13
        p.drawCentredString(largura_pagina / 2, line_number, 'RESULTADOS HISTÓRICOS')
        p.setFont("Helvetica-Bold", 4.5)
        line_number = line_number - 11
        p.drawCentredString(largura_pagina / 2, line_number, 'ANO/MÈS QTDE PROD. CUSTO VAR. ADIC. TOTAL   |   ANO/MÈS QTDE PROD. CUSTO VAR. ADIC. TOTAL   |   ANO/MÈS QTDE PROD. CUSTO VAR. ADIC. TOTAL   |   ANO/MÈS QTDE PROD. CUSTO VAR. ADIC. TOTAL   |   ANO/MÈS QTDE PROD. CUSTO VAR. ADIC. TOTAL')

        linha_inicial_historico = line_number

        # Vamos pegar o ano/mês máximo calculado para o custo variável adicionado
        ano_mes_maximo_qs = TbCustoVariavelAdicionadoDaugther.objects.filter(mae_id=obj.id).order_by('-ano_mes')
        ano_mes_maximo = ''
        for ano_mes_row in ano_mes_maximo_qs:
            ano_mes_maximo = ano_mes_row.ano_mes
            break
        ano_maximo = int(ano_mes_maximo[0:4])
        ano_minimo = ano_maximo - 4
        coluna = 8
        tem_negativo = False
        for ano in range(ano_minimo, ano_maximo + 1, 1):
            line_number = linha_inicial_historico
            for mes in range(1, 13, 1):
                line_number = line_number - 11
                if mes < 10:
                    ano_mes_corrente = str(ano) + '/0' + str(mes)
                else:
                    ano_mes_corrente = str(ano) + '/' + str(mes)

                # Vamos pegar os resultados para o ano/mês corrente
                if TbCustoVariavelAdicionadoDaugther.objects.filter(mae_id=obj.id, ano_mes=ano_mes_corrente):
                    p.drawString(coluna, line_number, ano_mes_corrente)
                    qtde_produzida = TbCustoVariavelAdicionadoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).qtde_produzida
                    custo_variavel_adicionado_total = TbCustoVariavelAdicionadoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).custo_variavel_adicionado_material + TbCustoVariavelAdicionadoDaugther.objects.get(mae_id=obj.id, ano_mes=ano_mes_corrente).custo_variavel_adicionado_ggf

                    if custo_variavel_adicionado_total < 0:
                        tem_negativo = True

                    p.drawRightString(coluna + 43, line_number, locale.format_string('%.2f', qtde_produzida, True))
                    p.drawRightString(coluna + 73, line_number, locale.format_string('%.2f', custo_variavel_adicionado_total, True))

            coluna = coluna + 118

        line_number = line_number - 5
        p.line(5, line_number, largura_pagina - 5, line_number)

        # Dados para o gráfico
        data = []
        rotulo_x = []

        resultado_historico_qs = TbCustoVariavelAdicionadoDaugther.objects.filter(mae_id=obj.id).order_by('ano_mes')

        for resultado_historico in resultado_historico_qs:
            data.append(float(resultado_historico.custo_variavel_adicionado_material + resultado_historico.custo_variavel_adicionado_ggf))
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
        if not tem_negativo:
            bar.valueAxis.valueMin = 0

        data = [data, ]
        bar.data = data

        bar.categoryAxis.categoryNames = rotulo_x
        bar.bars[0].fillColor = colors.green

        drawing.add(bar, '')
        renderPDF.draw(drawing, p, x, y, showBoundary=True)

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

    exportar_pdf.short_description = 'Exportar PDF'


admin.site.register(TbCustoVariavelAdicionado, TbCustoVariavelAdicionadoAdmin)

class TbRegressaoLinearMultiplaDaugtherAdmin(admin.TabularInline):
    fields = ('item_consumo', 'classificacao', 'indicador', 'minimo', 'media', 'maximo', 'tipo_variavel', 'agrupamento', 'coeficiente', 'pt')
    readonly_fields = ['item_consumo', 'classificacao', 'coeficiente', 'pt', 'minimo', 'media', 'maximo']

    model = TbRegressaoLinearMultiplaDaugther
    form = TbRegressaoLinearMultiplaDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Mostra somente os registros com flag = True
    def get_queryset(self, request):
        return super(TbRegressaoLinearMultiplaDaugtherAdmin, self).get_queryset(request).filter(flag=True)


class TbRegressaoLinearMultiplaDaugther1Admin(admin.TabularInline):
    fields = ('ano_mes', 'variavel', 'itens_consumo', 'qtde_produzida', 'qtde_consumo', 'indicador')
    readonly_fields = ['ano_mes', 'variavel', 'qtde_produzida', 'qtde_consumo']

    model = TbRegressaoLinearMultiplaDaugther1
    form = TbRegressaoLinearMultiplaDaugther1FormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Mostra somente os registros com flag = True
    def get_queryset(self, request):
        return super(TbRegressaoLinearMultiplaDaugther1Admin, self).get_queryset(request).filter(flag=True)


class TbRegressaoLinearMultiplaAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = (('reg_lin_mul_descricao', 'reg_lin_mul_desvio_producao'), 'status_colored_model')

    # Mostra campos se está ou não incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # Está incluindo
            return self.fields + (('reg_lin_mul_ano_mes_inicio', 'reg_lin_mul_ano_mes_fim'), 'reg_lin_mul_item_producao', 'reg_lin_mul_observacao',)
        else:  # Não está incluindo
            return self.fields + (('reg_lin_mul_ano_mes_inicio', 'reg_lin_mul_ano_mes_fim', 'reg_lin_mul_qtde_produzida'), 'reg_lin_mul_equacao_regressao', 'reg_lin_mul_r2_regressao', 'reg_lin_mul_item_producao', 'reg_lin_mul_grupo_maquina', 'reg_lin_mul_sumario', 'reg_lin_mul_observacao')

    filter_horizontal = ('reg_lin_mul_grupo_maquina', 'reg_lin_mul_item_producao')
    list_display = ['reg_lin_mul_descricao', 'reg_lin_mul_ano_mes_inicio', 'reg_lin_mul_ano_mes_fim','reg_lin_mul_status']
    list_display_links = ['reg_lin_mul_descricao']
    readonly_fields = ('reg_lin_mul_qtde_produzida', 'reg_lin_mul_status', 'reg_lin_mul_equacao_regressao', 'reg_lin_mul_r2_regressao', 'reg_lin_mul_sumario', 'reg_lin_mul_status', 'status_colored_model')
    search_fields = ['reg_lin_mul_descricao',]
    list_filter = ('reg_lin_mul_descricao',)

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})}, }

    form = TbRegressaoLinearMultiplaFormAdmin

    # Para formatar os campos reg_lin_mul_ano_mes_inicio e reg_lin_mul_ano_mes_fim com máscara
    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    inlines = [TbRegressaoLinearMultiplaDaugtherAdmin, TbRegressaoLinearMultiplaDaugther1Admin]

    #  Se está adicionando não mostra inlines definido acima
    def get_inline_instances(self, request, obj=None):
        return obj and super(TbRegressaoLinearMultiplaAdmin, self).get_inline_instances(request, obj) or []

    # Salvando aqui no admin. É para poder lançar mensagem ao usuário
    def save_model(self, request, obj, form, change):
        if obj.pk is None:
            # adding
            pass
        else:
            # changing
            obj.reg_lin_mul_status = 'CALCULANDO'
            obj.reg_lin_mul_equacao_regressao = ''
            obj.reg_lin_mul_r2_regressao = 0
            obj.reg_lin_mul_sumario = ''
            messages.set_level(request, messages.ERROR)
            messages.error(request, 'Registro foi alterado com sucesso. Calculando regressão em segundo plano. Se Status = CALCULANDO, refresh tela para atualizar.')
            # Vamos calcular o consumo específico
            from .tasks import atualiza_tabela_variaveis
            transaction.on_commit(lambda: atualiza_tabela_variaveis.delay(obj.pk))
            #transaction.on_commit(lambda: atualiza_tabela_variaveis(obj.pk))
        super().save_model(request, obj, form, change)



admin.site.register(TbRegressaoLinearMultipla, TbRegressaoLinearMultiplaAdmin)


