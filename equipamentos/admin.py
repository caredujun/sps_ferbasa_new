import boto3
import xlwt
from boto3 import Session
from django.contrib import admin, messages
from django.forms import Textarea
from django.http import HttpResponse
from xlrd import open_workbook_xls

from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from .forms import *
from parameters.models import TbCenarios
from django_object_actions import DjangoObjectActions  # Para permitir criar botão de action
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import User
from .tasks import update_indicador
from django_object_actions import DjangoObjectActions
from django.utils.html import format_html

# Para mostrar as ordens de produção cadastradas para o equipamento
class TbEquipamentosCadastroOrdemDaugtherAdmin(admin.TabularInline):
    fields = ('equ_ordem_codigo', 'equ_ordem_descricao')

    model = TbEquipamentos
    form = TbEquipamentosCadastroOrdemDaugtherFormAdmin

    show_change_link = True

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosCadastroOrdemDaugtherAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbEquipamentosCadastroDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3')
    readonly_fields = ('display_order',)

    model = TbEquipamentosCadastroDaugther
    form = TbEquipamentosCadastroDaugtherFormAdmin

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosCadastroDaugtherAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

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

class TbEquipamentosCadastroAdmin(admin.ModelAdmin):
    fields = (('equ_cad_codigo', 'equ_cad_descricao', 'equ_cad_gargalo', 'periodos_running', 'equ_cad_expedicao'),
              ('equ_cad_output', 'equ_cad_unidade_producao'),
              ('equ_cad_indicador_manutencao', 'equ_cad_moeda_manutencao'), ('equ_cad_imagem', 'equ_cad_imagem_tag'),
              'equ_cad_observacao', 'total_ordens', 'equ_cad_fonte')
    list_filter = ('equ_cad_codigo',)
    list_display = ['id', 'equ_cad_codigo', 'equ_cad_descricao', 'equ_cad_imagem', 'equ_cad_imagem_tag_small',
                    'equ_cad_gargalo', 'periodos_running', 'equ_cad_expedicao', 'total_ordens']
    list_editable = ['equ_cad_gargalo', 'equ_cad_expedicao']
    readonly_fields = ['equ_cad_imagem_tag', 'total_ordens', 'periodos_running']
    list_display_links = ['id', 'equ_cad_codigo']
    search_fields = ['equ_cad_codigo', ]
    form = TbEquipamentosCadastroFormAdmin

    # Vamos criar um campo para mostrar a qtde de periodos que está running na tabela filha
    def periodos_running(self, obj):
        if obj.id is not None:
            cursor = connection.cursor()
            # Expressão SQL
            sql = "select count(*) from equipamentos_tbequipamentoscadastrodaugther where mae_id = " + str(obj.id) + " and dau_valor_1 = true"
            cursor.execute(sql)
            retorno_running = cursor.fetchone()[0]

            sql = "select count(*) from equipamentos_tbequipamentoscadastrodaugther where mae_id = " + str(obj.id)
            cursor.execute(sql)
            retorno_total = cursor.fetchone()[0]
            cursor.close()

            if retorno_running < retorno_total:
                color = 'red'
            else:
                color = 'green'

            retorno = str(retorno_running) + '/' + str(retorno_total)
            retorno = format_html(
                '<b style="color:{};">{}</b>',
                color, retorno,)

            return retorno
        else:
            return ''

    # Vamos ver o tipo de periodo
    if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
        periodos_running.short_description = 'Anos Running'
    elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
        periodos_running.short_description = 'Trimestres Running'
    else:
        periodos_running.short_description = 'Meses Running'

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosCadastroAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    def equ_cad_imagem_tag(self, obj):
        return obj.equ_cad_imagem_tag

    def equ_cad_imagem_tag_small(self, obj):
        return obj.equ_cad_imagem_tag_small

    equ_cad_imagem_tag.short_description = ''
    equ_cad_imagem_tag.allow_tags = True

    equ_cad_imagem_tag_small.short_description = ''
    equ_cad_imagem_tag_small.allow_tags = True

    # Mostra os campos valor_inicial_1, valor_inicial_2 e valor_inicial_3 somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial_1', 'valor_inicial_2', 'valor_inicial_3')
        return self.fields

    inlines = [TbEquipamentosCadastroDaugtherAdmin, TbEquipamentosCadastroOrdemDaugtherAdmin]


# Registrando
admin.site.register(TbEquipamentosCadastro, TbEquipamentosCadastroAdmin)

# Para mostrar os itens de custo variável cadastrados para o equipamento/ordem
class TbEquipamentosConsumoEspecificoDaugtherItensAdmin(admin.TabularInline):
    fields = ('equ_con_esp_custoitempreco',)

    model = TbEquipamentosConsumoEspecifico
    form = TbEquipamentosConsumoEspecificoDaugtherItensFormAdmin

    show_change_link = True

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosConsumoEspecificoDaugtherItensAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)


    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbEquipamentosDaugtherAdmin(admin.TabularInline):
    fields = (
    'display_order', 'dau_valor_3', 'dau_valor_1', 'dau_valor_2', 'wip_volume', 'custo_variavel', 'custo_variavel_item',
    'custo_variavel_inbound', 'custo_variavel_manutencao')
    readonly_fields = ('display_order', 'wip_volume', 'custo_variavel', 'custo_variavel_item', 'custo_variavel_inbound',
                       'custo_variavel_manutencao')

    model = TbEquipamentosDaugther
    form = TbEquipamentosDaugtherFormAdmin

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

class TbEquipamentosAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = []

    # Mostra os campos dos valores_iniciais somente se estiver incluindo novo registro, e também campo equ_wip (mostra se não for expedição)
    def get_fields(self, request, obj=None):

        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            # Esta adicionando. Vamos mostrar os campos de valores iniciais
            self.fields = [('equ_codigo', 'equipamento_imagem_tag_small', 'gargalo', 'expedicao'), ('equ_ordem_codigo', 'equ_ordem_descricao', 'total_itens'), ('equ_tipo_producao', 'equ_wip'), 'equ_observacao', 'equ_fonte', 'valor_inicial_3', 'valor_inicial_1', 'valor_inicial_2']
        else:
            # Vamos ver se é expedição. Se sim, tira o campo equ_fields
            expedicao = TbEquipamentosCadastro.objects.get(id=obj.equ_codigo_id).equ_cad_expedicao
            if expedicao:
                self.fields = [('equ_codigo', 'equipamento_imagem_tag_small', 'gargalo', 'expedicao'),
                               ('equ_ordem_codigo', 'equ_ordem_descricao', 'periodos_ativa', 'total_itens'), 'equ_tipo_producao',
                               'equ_observacao', 'equ_fonte']
            else:
                self.fields = [('equ_codigo', 'equipamento_imagem_tag_small', 'gargalo', 'expedicao'),
                               ('equ_ordem_codigo', 'equ_ordem_descricao', 'periodos_ativa', 'total_itens'),
                               ('equ_tipo_producao', 'equ_wip'), 'equ_observacao', 'equ_fonte']

        return self.fields

    list_display = ['id', 'equ_codigo', 'equ_ordem_codigo', 'equipamento_imagem_tag_small', 'equ_ordem_descricao', 'periodos_ativa',
                    'equ_tipo_producao', 'media_paradas_np', 'media_prod', 'media_custo_var_adi', 'total_itens']

    readonly_fields = ('equipamento_imagem_tag_small', 'total_itens', 'gargalo', 'expedicao', 'media_paradas_np', 'media_prod', 'media_custo_var_adi', 'periodos_ativa')
    list_display_links = ['id', 'equ_codigo']

    list_filter = (('equ_codigo', admin.RelatedOnlyFieldListFilter),('equ_tipo_producao', admin.RelatedOnlyFieldListFilter),)
    list_per_page = 10
    search_fields = ['equ_codigo__equ_cad_codigo', ]

    actions = ['exportar_excel', 'importar_excel']

    # Vamos criar um campo para mostrar a qtde de periodos que está running na tabela filha
    def periodos_ativa(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.id) + " and dau_valor_3 = true"
        cursor.execute(sql)
        retorno_ativa = cursor.fetchone()[0]

        sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.id)
        cursor.execute(sql)
        retorno_total = cursor.fetchone()[0]
        cursor.close()

        if retorno_ativa < retorno_total:
            color = 'blue'
        else:
            color = 'green'

        retorno = str(retorno_ativa) + '/' + str(retorno_total)
        retorno = format_html(
            '<b style="color:{};">{}</b>',
            color, retorno,)

        return retorno

    # Vamos ver o tipo de periodo
    if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
        periodos_ativa.short_description = 'Anos Ativa'
    elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
        periodos_ativa.short_description = 'Trimestres Ativa'
    else:
        periodos_ativa.short_description = 'Meses Ativa'

    # Vamos criar um campo para mostrar a média das paradas não programadas previstas
    def media_paradas_np(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select avg(dau_valor_1) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()
        return locale.format_string('%.2f', retorno, True)

    media_paradas_np.short_description = 'Paradas NP (%)'

    # Vamos criar um campo para mostrar a média das produtividades previstas
    def media_prod(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select avg(dau_valor_2) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()
        return locale.format_string('%.2f', retorno, True)

    media_prod.short_description = 'Produtividade'

    # Vamos criar um campo para mostrar a média do custo variável adicionado
    def media_custo_var_adi(self, obj):
        cursor = connection.cursor()

        sql = "CALL public.atualiza_custo_var_adic_equipamento(" + str(obj.tbcenarios_id) + ", " + str(obj.id) + ", 0)"

        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR')
        retorno = locale.format_string('%.2f', retorno, True)

        # Vamos ver a moeda da empresa
        moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
        if moeda_empresa == 'BRL':
            retorno = 'R$ ' + retorno
        if moeda_empresa == 'USD':
            retorno = 'US$ ' + retorno
        if moeda_empresa == 'EUR':
            retorno = '€ ' + retorno

        return retorno

    media_custo_var_adi.short_description = 'Custo Var. Adic.'

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('equipamentos.change_tbequipamentos'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def importar_excel(self, request, queryset):
        try:
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'equipamentos_ordem_producao_mae_filhas.xls'

            '''
            # Removemos pois não funciona no Heroku. Vamos fazer usando executável externo (excel_to_s3.exe)
            # Vamos salvar o arquivo Excel no AWS S3
            session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

            s3 = session.resource('s3')

            object = s3.Object(bucket_name, object_key)

            os.chdir(r'c:\sps\excel')
            result = object.put(Body=open(object_key, 'rb'))

            # res = result.get('ResponseMetadata')

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

            sheet = wb.sheet_by_index(0)  # Abrindo a mãe
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_mae = sheet.ncols
            total_linhas_mae = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_mae):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id',
                                 'equ_codigo_id',
                                 'equ_codigo_descricao',
                                 'equ_ordem_codigo',
                                 'equ_ordem_descricao',
                                 'equ_tipo_producao_id',
                                 'equ_tipo_producao_nome',
                                 'equ_wip',
                                 'equ_observacao',
                                 'valor_inicial_1',
                                 'valor_inicial_2',
                                 'valor_inicial_3',
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

                if lista_colunas == ['id',
                                     'dau_order',
                                     'dau_valor_1',
                                     'dau_valor_2',
                                     'dau_valor_3',
                                     'mae_id',
                                     'cenarios_id']:

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
                        for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 13 na mãe
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if sheet.cell_value(i, 13) != cen_ativo:
                                    ok_cenario = False
                                    break

                        sheet = wb.sheet_by_index(1)  # Abrindo a filha
                        for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 6 na filha
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if sheet.cell_value(i, 6) != cen_ativo:
                                    ok_cenario = False
                                    break
                        if not ok_cenario:
                            messages.error(request,
                                           'Cenário informado na planilha (aba mãe ou filhas) não é o ativo. Favor verificar!')
                        else:
                            #  Tudo ok até aqui. Vamos verificar se no arquivo tem as mães selecionadas. E se tiver, vamos ver se tem as filhas no total do período do cenário.
                            #  Vamos pegar o id das mães selecionadas.
                            id_maes = queryset.values_list('id', )
                            tudo_ok = True
                            messages_error = ''
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
                                    messages_error = 'Na aba mãe do arquivo em Excel não existe o id = ' + str(
                                        id_mae[0]) + '. Favor verificar!'
                                    break
                                else:
                                    if conta_mae > 1:
                                        # Se tiver mais que 1 também é problema
                                        tudo_ok = False
                                        messages_error = 'Na aba mãe do arquivo em Excel tem mais que 1(um) id = ' + str(
                                            id_mae[0]) + '. Favor verificar!'
                                        break
                                    else:
                                        #  Tudo certo até aqui. Podemos continuar.
                                        #  Vamos ver se na tabela filhas existe um total de filhas igual ao total de periodos
                                        sheet = wb.sheet_by_index(1)  # Abrindo a filhas
                                        conta_filha = 0
                                        for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 5
                                            if i > 0 and sheet.cell_value(i, 5) == id_mae[
                                                0]:  # É o id da mãe. Soma no contador.
                                                conta_filha = conta_filha + 1

                                        if conta_filha != total_periodos:
                                            tudo_ok = False
                                            messages_error = 'Total de filhas para a mãe id = ' + str(
                                                id_mae[0]) + 'é igual a ' + str(conta_filha) + ', diferente de ' + str(
                                                total_periodos) + '. Favor verificar!'
                                            break
                                        else:
                                            # Vamos ver agora se o dau_order nas filhas estão na sequência certa
                                            conta_dau_order = 0
                                            ok_dau_order = True
                                            for i in range(
                                                    total_linhas_filha):  # A coluna da mãe_id é a de ordem 5 e a do dau_order é o 1
                                                if i > 0:  # Porque a linha 0 é o cabeçalho

                                                    if sheet.cell_value(i, 5) == id_mae[
                                                        0]:  # É o id da mãe. Vamos somar no contador e comparar com o dau_order
                                                        conta_dau_order = conta_dau_order + 1
                                                        if conta_dau_order != sheet.cell_value(i, 1):
                                                            ok_dau_order = False
                                                            break

                                            if not ok_dau_order:
                                                tudo_ok = False
                                                messages_error = 'Sequencial dau_order para a mãe id = ' + str(
                                                    id_mae[0]) + ' está fora da sequencia na filha. Favor verificar!'
                                                break
                                            else:
                                                #  Tudo ok até aqui. Podemos continuar
                                                #  Vamos agora atualizar a mãe e as filhas pois tudo está Ok com o arquivo Excel
                                                sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                                                #  id_mae[0] é o id da mãe que foi selecionado
                                                for i in range(
                                                        total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                                    if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                                        0]:  # Porque a linha 0 é o cabeçalho
                                                        tab_obj = TbEquipamentos.objects.get(id=sheet.cell_value(i, 0))
                                                        tab_obj.equ_codigo_id = int(sheet.cell_value(i, 1))
                                                        tab_obj.equ_ordem_codigo = sheet.cell_value(i, 3)
                                                        tab_obj.equ_ordem_descricao = sheet.cell_value(i, 4)
                                                        tab_obj.equ_tipo_producao_id = sheet.cell_value(i, 5)
                                                        tab_obj.equ_wip = sheet.cell_value(i, 7)
                                                        tab_obj.equ_observacao = sheet.cell_value(i, 8)
                                                        '''
                                                        # Não é necessário atualizar os valores iniciais
                                                        tab_obj.valor_inicial_1 = sheet.cell_value(i, 9)
                                                        tab_obj.valor_inicial_2 = sheet.cell_value(i, 10)
                                                        tab_obj.valor_inicial_3 = int(sheet.cell_value(i, 11))
                                                        '''
                                                        tab_obj.save()

                                                # Vamos agora atualizar as filhas
                                                sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                                for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 5
                                                    if i > 0 and sheet.cell_value(i, 5) == id_mae[
                                                        0]:  # É o id da mãe. Soma no contador.
                                                        # Temos que pegar o dau_order
                                                        dau_order_filha = sheet.cell_value(i, 1)
                                                        tab_obj = TbEquipamentosDaugther.objects.get(
                                                            id=sheet.cell_value(i, 0), mae_id=id_mae[0],
                                                            dau_order=dau_order_filha)
                                                        tab_obj.dau_valor_1 = sheet.cell_value(i, 2)
                                                        tab_obj.dau_valor_2 = sheet.cell_value(i, 3)
                                                        if sheet.cell_value(i, 4) == 1:
                                                            tab_obj.dau_valor_3 = True
                                                        else:
                                                            tab_obj.dau_valor_3 = False
                                                        tab_obj.save()

                            if tudo_ok:
                                messages.success(request, 'Tabela Equipamentos foi atualizada com sucesso!')

                                # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
                                try:
                                    bucket_object.delete()
                                    messages.success(request,
                                                     'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
                                except:
                                    messages.error(request,
                                                   'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

                            else:
                                messages.error(request, messages_error)

                    else:
                        messages.error(request,
                                       'Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!')

                else:
                    messages.error(request, 'Cabeçalho da aba filha não está correto. Favor verificar!')

            else:
                messages.error(request, 'Cabeçalho da aba mãe não está correto. Favor verificar!')
        except:
            messages.error(request,
                           'Não foi encontrado o arquivo ' + object_key + ' no AWS S3 Bucket spsferbasa. Favor verificar!')

    importar_excel.short_description = 'Importar Excel'

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="equipamentos_ordem_producao_mae_filhas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id',
                   'equ_codigo_id',
                   'equ_codigo_descricao',
                   'equ_ordem_codigo',
                   'equ_ordem_descricao',
                   'equ_tipo_producao_id',
                   'equ_tipo_producao_nome',
                   'equ_wip',
                   'equ_observacao',
                   'valor_inicial_1',
                   'valor_inicial_2',
                   'valor_inicial_3',
                   'id_origem',
                   'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id',
                                    'equ_codigo_id',
                                    'equ_ordem_codigo',
                                    'equ_ordem_descricao',
                                    'equ_tipo_producao_id',
                                    'equ_wip',
                                    'equ_observacao',
                                    'valor_inicial_1',
                                    'valor_inicial_2',
                                    'valor_inicial_3',
                                    'id_origem',
                                    'tbcenarios_id').order_by('id')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            col_equ_codigo_id = 1
            col_equ_tipo_producao_id = 4
            # Vamos pegar os nomes
            equ_codigo_descricao = TbEquipamentosCadastro.objects.get(id=row[col_equ_codigo_id]).equ_cad_codigo
            equ_tipo_producao_nome = TbTipoProducao.objects.get(
                id=row[col_equ_tipo_producao_id]).tip_nome

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(col_equ_codigo_id + 1, equ_codigo_descricao)
            list_aux.insert(col_equ_tipo_producao_id + 2, equ_tipo_producao_nome)
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

        filhas = TbEquipamentosDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id',
                   'dau_order',
                   'dau_valor_1',
                   'dau_valor_2',
                   'dau_valor_3',
                   'mae_id',
                   'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id',
                                  'dau_order',
                                  'dau_valor_1',
                                  'dau_valor_2',
                                  'dau_valor_3',
                                  'mae_id',
                                  'tbcenarios_id').order_by('mae_id', 'dau_order')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel'

    form = TbEquipamentosFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)



    inlines = [TbEquipamentosDaugtherAdmin, TbEquipamentosConsumoEspecificoDaugtherItensAdmin]

# Registrando
admin.site.register(TbEquipamentos, TbEquipamentosAdmin)

class TbEquipamentosConsumoEspecificoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor', 'custo_item_preco', 'custo_item_inbound', 'custo_item_total')
    readonly_fields = ('display_order', 'custo_item_preco', 'custo_item_inbound', 'custo_item_total')

    model = TbEquipamentosConsumoEspecificoDaugther
    form = TbEquipamentosConsumoEspecificoDaugtherFormAdmin

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

"""
# Removi porque não precisou. Usei list_filter = (('equ_con_esp_equipamento', admin.RelatedOnlyFieldListFilter),)
class FiltraEquipamento(SimpleListFilter):  # Criamos essa subclasse para filtrar somente os equipamentos do cenário ativo
    title = 'Equipamento/Tipo de Produto/Planta'
    parameter_name = 'equipamento'

    def lookups(self, request, model_admin):
        qs = model_admin.get_queryset(request)  # A queryset já foi filtrada para ter somente dados do cenário ativo
        types = qs.values_list('equ_con_esp_equipamento_id', 'equ_con_esp_equipamento')
        return list(types.order_by('-equ_con_esp_equipamento').distinct())

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        return queryset.filter(equ_con_esp_equipamento_id=self.value())
"""

class TbEquipamentosConsumoEspecificoAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = (('equ_con_esp_equipamento', 'equipamento_imagem_tag_small'), ('equ_con_esp_custoitempreco', 'cus_ite_imagem_tag_small'), ('equ_con_consumo_especifico', 'periodo_inicio', 'periodo_fim', 'valor_indicador'), 'equ_con_esp_observacao')

    readonly_fields = ('equipamento_imagem_tag_small', 'cus_ite_imagem_tag_small', 'periodo_inicio', 'periodo_fim', 'valor_indicador')

    list_display = ['equ_con_esp_equipamento', 'equipamento_imagem_tag_small', 'equ_con_esp_custoitempreco', 'equ_con_consumo_especifico',
                    'cus_ite_imagem_tag_small', 'equ_con_esp_observacao']
    list_filter = (('equ_con_esp_equipamento', admin.RelatedOnlyFieldListFilter),
                   ('equ_con_esp_custoitempreco', admin.RelatedOnlyFieldListFilter),
                   ('equ_con_consumo_especifico', admin.RelatedOnlyFieldListFilter))

    search_fields = ['equ_con_esp_equipamento__equ_codigo__equ_cad_codigo', 'equ_con_esp_equipamento__equ_ordem_descricao', 'equ_con_esp_custoitempreco__cus_ite_pre_item__cus_ite_nome']

    list_per_page = 20

    actions = ['exportar_excel', 'importar_excel', 'importar_excel_new', 'update_indicador_geral']

    def update_indicador_geral(self, request, queryset): # Atualiza os indicadores de consumo específico indicado
                                                         # SÓ ATUALIZA SE FOR INDICADO CONSUMO ESPECÍFICO CALCULADO NO MÓDULO CF
        consumos = queryset.values_list('id', )
        for consumo in consumos:
            if TbEquipamentosConsumoEspecifico.objects.get(id=consumo[0]).equ_con_consumo_especifico: # Se foi indicado consumo específico calculado no módulo CF
               update_indicador(consumo[0])

        messages.success(request, 'Update do indicador dos consumos especvíficos realizado com sucesso!')

    update_indicador_geral.short_description = "Update Indicador Consumos Específicos Selecionados"

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('equipamentos.change_tbequipamentosconsumoespecifico'):
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
        object_key = 'equipamentosconsumoespecifico_new.xls'

        ''' Removemos pois não funciona no Heroku. Só localmente. Fizemos um .exe para transferir
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

        if lista_colunas == ['id', 'equipamento_id', 'equipamento_nome/planta/ordem', 'custoitempreco_id',
                             'custoitempreco_nome/planta', 'observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id']:
            #  Cabeçalho da mãe está correto.
            #  Vamos ver se o cenário informado na mãe é o cenário ativo.
            # Cenário ativo
            ok_cenario = True
            cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

            sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba
            for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 8
                if i > 0:  # Porque a linha 0 é o cabeçalho
                    if sheet.cell_value(i, 8) != cen_ativo:
                        ok_cenario = False
                        break

            if not ok_cenario:
                messages.error(request, 'Cenário informado na planilha não é o ativo. Favor verificar!')
            else:
                # Tudo ok até aqui.
                # Vamos ver se a informação é nova.
                # Nesse caso temos que ver se o id do equipamento/ordem (equ_con_esp_equipamento_id/coluna order 1) e o id do item de custo/preçounidade de produção (equ_con_esp_custoitempreco_id/coluna order 3) são novos.
                existe = False
                for i in range(total_linhas_mae):
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        if TbEquipamentosConsumoEspecifico.objects.filter(
                                equ_con_esp_equipamento_id=sheet.cell_value(i, 1),
                                equ_con_esp_custoitempreco_id=sheet.cell_value(i, 3),
                                tbcenarios_id=cen_ativo).count() > 0:
                            existe = True
                            break
                if not existe:
                    # Tudo ok até aqui. Podemos continuar.
                    # Vamos ver se id do equipamento/ordem informado existe.
                    existe = True
                    for i in range(total_linhas_mae):
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if TbEquipamentos.objects.filter(id=sheet.cell_value(i, 1)).count() == 0:
                                existe = False
                                break
                    if existe:
                        # Tudo ok até aqui. Podemos continuar.
                        # Vamos ver se id do item de custo/preço informado existe.
                        existe = True
                        for i in range(total_linhas_mae):
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if TbCustoItemPreco.objects.filter(id=sheet.cell_value(i, 3)).count() == 0:
                                    existe = False
                                    break
                        if existe:
                            # Tudo ok até aqui. Podemos continuar.
                            # Vamos ver se o equipamento/ordem e item de custo/preço são da mesma unidade de produção.
                            existe = True
                            for i in range(total_linhas_mae):
                                if i > 0:  # Porque a linha 0 é o cabeçalho
                                    id_equipamento = TbEquipamentos.objects.get(id=sheet.cell_value(i, 1)).equ_codigo_id
                                    id_unidade_equipamento_ordem = TbEquipamentosCadastro.objects.get(
                                        id=id_equipamento).equ_cad_unidade_producao_id
                                    id_unidade_item_custo_preco = TbCustoItemPreco.objects.get(
                                        id=sheet.cell_value(i, 3)).cus_ite_pre_unidade_producao_id
                                    if id_unidade_equipamento_ordem != id_unidade_item_custo_preco:
                                        existe = False
                                        break
                            if existe:
                                # Vamos agora incluir o registro e a filhas a partir dos valores iniciais informados.
                                # Se foi informado algum valor na coluna id nós desconsideramos.
                                for i in range(total_linhas_mae):
                                    if i > 0:  # Porque a linha 0 é o cabeçalho
                                        TbEquipamentosConsumoEspecifico.objects.create(
                                            equ_con_esp_equipamento_id=sheet.cell_value(i, 1),
                                            equ_con_esp_custoitempreco_id=sheet.cell_value(i, 3),
                                            equ_con_esp_observacao=sheet.cell_value(i, 5),
                                            valor_inicial=sheet.cell_value(i, 6),
                                            tbcenarios_id=cen_ativo)

                                        # Temos que pegar o id da mãe para lançar na filha
                                        id_mae = TbEquipamentosConsumoEspecifico.objects.get(
                                            equ_con_esp_equipamento_id=sheet.cell_value(i, 1),
                                            equ_con_esp_custoitempreco_id=sheet.cell_value(i, 3)).id

                                        # Vamos atualizar a filha usando o Stored Procedure verifica_filha_2_com_indicador
                                        cursor = connection.cursor()
                                        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_2_com_indicador

                                        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
                                        sql = "call public.verifica_filha('equipamentos_" + "tbequipamentosconsumoespecifico" + "daugther', " + str(
                                            id_mae) + ", " + str(cen_ativo) + ", " + str(sheet.cell_value(i, 6)) + ")"

                                        cursor.execute(sql)
                                        cursor.close()

                                messages.success(request,
                                                 'Novos equipamentos/ordem e item de custo/preço foram adicionados com sucesso.')

                            else:
                                messages.error(request, 'UNIDADE DE PRODUÇÃO do equipamento/ordem(' + str(
                                    id_equipamento) + '/' + str(
                                    id_unidade_equipamento_ordem) + ') e item de custo/preço(' + str(
                                    sheet.cell_value(i, 3)) + '/' + str(
                                    id_unidade_item_custo_preco) + ') na linha ' + str(
                                    i + 1) + ' são diferentes. Favor verificar!')

                        else:
                            messages.error(request, 'Não existe o item de custo/preço id = ' + str(
                                sheet.cell_value(i, 2)) + ' cadastrado. Favor verificar!')

                    else:
                        messages.error(request, 'Não existe o equipamento/ordem id = ' + str(
                            sheet.cell_value(i, 1)) + ' cadastrado. Favor verificar!')

                else:
                    messages.error(request, 'Já existe o equipamento/order id = ' + str(
                        int(sheet.cell_value(i, 1))) + ' e o item de custo/preço id = ' + str(
                        int(sheet.cell_value(i, 2))) + ' já cadastrados para esse cenário. Favor verificar!')

        else:
            messages.error(request, 'Cabeçalho do arquivo Excel não está correto. Favor verificar!')

    importar_excel_new.short_description = 'Importar Excel (Novos)'

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel_new':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbEquipamentosConsumoEspecifico.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbEquipamentosConsumoEspecificoAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        # try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY
        bucket_name = 'spsferbasa'
        object_key = 'equipamentosconsumoespecifico_mae_filhas.xls'

        ## Vamos salvar o arquivo Excel no AWS S3
        # session = boto3.Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

        # s3 = session.resource('s3')

        # object = s3.Object(bucket_name, object_key)

        # os.chdir(r'c:\sps\excel')
        # result = object.put(Body=open(object_key, 'rb'))

        # res = result.get('ResponseMetadata')

        # if res.get('HTTPStatusCode') == 200:
        #    print('File Uploaded Successfully')
        # else:
        #    print('File Not Uploaded')

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

        if lista_colunas == ['id', 'equipamento_id',
                             'equipamento_nome/planta/ordem',
                             'custoitempreco_id',
                             'custoitempreco_nome/planta',
                             'observacao',
                             'valor_inicial',
                             'id_origem',
                             'tbcenarios_id']:
            # Cabeçalho da mãe está correto. Vamos agora ver a filha
            sheet = wb.sheet_by_index(1)  # Abrindo a filha
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_filha = sheet.ncols
            total_linhas_filha = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_filha):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id', 'dau_order', 'dau_valor', 'mae_id', 'tbcenarios_id']:
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
                    for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 8 na mãe
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 8) != cen_ativo:
                                ok_cenario = False
                                break

                    sheet = wb.sheet_by_index(1)  # Abrindo a filha
                    for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 4 na filha
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 4) != cen_ativo:
                                ok_cenario = False
                                break
                    if not ok_cenario:
                        messages.error(request,
                                       'Cenário informado na planilha (aba mãe ou filhas) não é o ativo. Favor verificar!')
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
                                    sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                    conta_filha = 0
                                    for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                        if i > 0 and sheet.cell_value(i, 3) == id_mae[
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
                                                total_linhas_filha):  # A coluna da mãe_id é a de ordem 3 e a do dau_order é o 1
                                            if i > 0:  # Porque a linha 0 é o cabeçalho

                                                if sheet.cell_value(i, 3) == id_mae[
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
                                            for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0
                                                if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                                    0]:  # Porque a linha 0 é o cabeçalho
                                                    tab_obj = TbEquipamentosConsumoEspecifico.objects.get(
                                                        id=sheet.cell_value(i, 0))
                                                    tab_obj.equ_con_esp_equipamento_id = sheet.cell_value(i, 1)
                                                    tab_obj.equ_con_esp_custoitempreco_id = sheet.cell_value(i, 3)
                                                    tab_obj.equ_con_esp_observacao = sheet.cell_value(i, 5)
                                                    tab_obj.valor_inicial = sheet.cell_value(i, 6)
                                                    tab_obj.save()

                                            # Vamos agora atualizar as filhas
                                            sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                            for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                                if i > 0 and sheet.cell_value(i, 3) == id_mae[
                                                    0]:  # É o id da mãe. Soma no contador.
                                                    # Temos que pegar o dau_order
                                                    dau_order_filha = sheet.cell_value(i, 1)
                                                    tab_obj = TbEquipamentosConsumoEspecificoDaugther.objects.get(
                                                        id=sheet.cell_value(i, 0), mae_id=id_mae[0],
                                                        dau_order=dau_order_filha)
                                                    tab_obj.dau_valor = sheet.cell_value(i, 2)
                                                    tab_obj.save()

                        if tudo_ok:
                            messages.success(request,
                                             'Tabela Equipamentos/Consumos Específicos foi atualizada com sucesso!')

                else:
                    messages.error(request,
                                   'Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!')

            else:
                messages.error(request, 'Cabeçalho da aba filha não está correto. Favor verificar!')

        else:
            messages.error(request, 'Cabeçalho da aba mãe não está correto. Favor verificar!')

    # except:
    #    messages.error(request, 'Não foi encontrado o arquivo ' + object_key + ' no diretório c:\sps\excel. Favor verificar!')

    importar_excel.short_description = 'Importar Excel'

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="equipamentosconsumoespecifico_mae_filhas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id', 'equipamento_id', 'equipamento_nome/planta/ordem', 'custoitempreco_id',
                   'custoitempreco_nome/planta', 'observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id', 'equ_con_esp_equipamento_id', 'equ_con_esp_custoitempreco_id',
                                    'equ_con_esp_observacao', 'valor_inicial', 'id_origem', 'tbcenarios_id').order_by(
            'id')

        # Vamos incluir o nome do equipamento e item de custo nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            col_id_equipamento = 1
            col_id_custoitem = 2
            # Vamos pegar o nome do equipamento
            id_equipamento = TbEquipamentos.objects.get(id=row[col_id_equipamento]).equ_codigo_id
            ordem_equipamento = TbEquipamentos.objects.get(id=row[col_id_equipamento]).equ_ordem_codigo
            ordem_descricao = TbEquipamentos.objects.get(id=row[col_id_equipamento]).equ_ordem_descricao
            nome_equipamento = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_codigo
            id_planta = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_unidade_producao_id
            nome_planta = TbUnidadeProducao.objects.get(id=id_planta).uni_nome
            nome_equipamento = nome_equipamento + '/' + nome_planta + '/' + str(
                ordem_equipamento) + '/' + ordem_descricao

            # Vamos pegar o nome do item de custo
            id_custoitem = TbCustoItemPreco.objects.get(id=row[col_id_custoitem]).cus_ite_pre_item_id
            id_planta = TbCustoItemPreco.objects.get(id=row[col_id_custoitem]).cus_ite_pre_unidade_producao_id
            nome_planta = TbUnidadeProducao.objects.get(id=id_planta).uni_nome
            nome_custoitem = TbCustoItem.objects.get(id=id_custoitem).cus_ite_nome + '/' + nome_planta

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(2, nome_equipamento)
            list_aux.insert(4, nome_custoitem)
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

        filhas = TbEquipamentosConsumoEspecificoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id', 'dau_order', 'dau_valor', 'mae_id', 'tbcenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id', 'dau_order', 'dau_valor', 'mae_id', 'tbcenarios_id').order_by('mae_id',
                                                                                                      'dau_order')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, 'Arquivo gerado com sucesso.')

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Action
    def update_indicador_consumo_especifico(self, request, obj):
        update_indicador(obj.id)
        messages.success(request, 'Update do indicador de consumo específico realizado com sucesso!')

    update_indicador_consumo_especifico.label = "Update Indicador"  # optional

    change_actions = ('update_indicador_consumo_especifico',)

    form = TbEquipamentosConsumoEspecificoFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }


    # Action
    def update_indicador_consumo_especifico(self, request, obj):
        update_indicador(obj.id)
        messages.success(request, 'Update do indicador de consumo específico realizado com sucesso!')

    update_indicador_consumo_especifico.label = "Update Indicador"  # optional

    change_actions = ('update_indicador_consumo_especifico',)


    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbEquipamentosConsumoEspecificoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo ind_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbEquipamentosConsumoEspecificoDaugtherAdmin]


# Registrando
admin.site.register(TbEquipamentosConsumoEspecifico, TbEquipamentosConsumoEspecificoAdmin)