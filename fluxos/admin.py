import openpyxl  # Para ler Excel xlsx
from django.utils.translation import gettext_lazy as _
import io, subprocess
import xlwt
from boto3 import Session
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.forms import TextInput, Textarea
from django.http import HttpResponse, HttpResponseRedirect
from xlrd import open_workbook_xls
from fluxos.forms import TbFluxoConsumoPadraoDaugtherFormAdmin, TbFluxoConsumoPadraoFormAdmin, TbFluxoProducaoFormAdmin, \
    TbFluxoProducaoDaugtherFormAdmin, TbFluxoProducaoInputOutputFormAdmin, TbFluxoProducaoInputOutputDaugtherFormAdmin, \
    TbFluxoProducaoDaugther01FormAdmin, AtualizarFluxoForm
from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from .models import *
from parameters.models import TbCenarios
import nested_admin
from django_object_actions import DjangoObjectActions
from .tasks import update_fluxo_celery, update_indicador, importar_excel_fluxo_producao_celery, update_fluxo_lista, \
    excluir_output_real_zerado_lista, importar_excel_new_segundo_celery, atualizar_fluxo_celery, \
    atualiza_input_output_celery, \
    exportar_excel_fluxo_producao_celery, importar_excel_xlsx_new_segundo_celery, atualizar_custos_celery, remover_fluxo
from django.contrib.auth.models import User
from django.shortcuts import render


# **********************************************************************************************************

# **********************************************************************************************************
class TbFluxoConsumoPadraoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor', 'custo_variavel')
    readonly_fields = ['display_order', 'custo_variavel']

    model = TbFluxoConsumoPadraoDaugther
    form = TbFluxoConsumoPadraoDaugtherFormAdmin

    '''
    # Tiramos a mensagem pois removemos o método
    def save_model(self, request, obj, form, change):
        # add an additional message
        messages.info(request, _("Atualização dos fluxos de produção está sendo feita em segundo plano."))
        super(TbFluxoConsumoPadraoDaugtherAdmin, self).save_model(request, obj, form, change)
    '''

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


class TbFluxoConsumoPadraoAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = (('flu_con_pad_descricao', 'flu_con_pad_validado', 'fluxo_input_output_atualizado_desatualizado'),
              'flu_con_pad_from_equipamento', 'flu_con_pad_to_equipamento',
              ('flu_con_pad_consumo_especifico', 'periodo_inicio', 'periodo_fim', 'valor_indicador'), 'media_consumo',
              'flu_con_pad_observacao')
    list_display = ['id', 'flu_con_pad_descricao', 'flu_con_pad_validado',
                    'fluxo_input_output_atualizado_desatualizado', 'flu_con_pad_from_equipamento',
                    'flu_con_pad_to_equipamento', 'media_consumo', 'flu_con_pad_consumo_especifico']
    list_editable = ['flu_con_pad_validado']
    search_fields = ['flu_con_pad_descricao', 'flu_con_pad_from_equipamento__equ_ordem_descricao',
                     'flu_con_pad_to_equipamento__equ_ordem_descricao']
    list_display_links = ('id', 'flu_con_pad_descricao',)
    readonly_fields = ('periodo_inicio', 'periodo_fim', 'valor_indicador', 'media_consumo',
                       'fluxo_input_output_atualizado_desatualizado')

    '''
    # Tiramos a mensagem pois removemos o método
    def save_model(self, request, obj, form, change):
        # add an additional message
        messages.info(request, _("Atualização dos fluxos de produção está sendo feita em segundo plano."))
        super(TbFluxoConsumoPadraoAdmin, self).save_model(request, obj, form, change)
    '''

    list_per_page = 50

    class input_output_desatualizado(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'I/O Atualizado'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'i_o_desatualizado'

        def lookups(self, request, model_admin):
            # Vamos montar a tuple na mão
            tuple = [(1, 'Sim'), (0, 'Não')]
            return tuple

        def queryset(self, request, queryset):
            if self.value() != None:
                # Vamos montar uma lista dos consumos padrões que tem fluxo de produção com input/output desatualizado
                id_consumo_padrao = queryset.values_list('id', flat=True)
                lista = []
                id_cenario_ativo = TbCenarios.objects.get(cen_ativo=1).id
                for i in id_consumo_padrao:
                    id_fluxo = TbFluxoProducaoDaugther.objects.filter(flu_pro_dau_consumo_padrao_id=i, mae_id__in=(
                        TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False,
                                                       tbcenarios_id=id_cenario_ativo))).values_list('mae_id',
                                                                                                     flat=True)
                    if len(id_fluxo) > 0 and i not in lista:
                        lista.append(i)

                if int(self.value()) == 0:
                    return queryset.filter(id__in=lista)
                else:
                    return queryset.filter().exclude(id__in=lista)
            else:
                return queryset

    list_filter = ('flu_con_pad_descricao', ('flu_con_pad_from_equipamento', admin.RelatedOnlyFieldListFilter),
                   ('flu_con_pad_to_equipamento', admin.RelatedOnlyFieldListFilter), 'flu_con_pad_consumo_especifico',
                   'flu_con_pad_validado', input_output_desatualizado)

    # Vamos criar um campo para mostrar a média do consumo padrão previsto
    def media_consumo(self, obj):
        if obj.pk is not None:
            cursor = connection.cursor()
            # Expressão SQL
            sql = "select avg(dau_valor) from fluxos_tbfluxoconsumopadraodaugther where mae_id = " + str(obj.id)
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            if retorno is not None:
                return locale.format_string('%.4f', retorno, True)
            else:
                return ''

    media_consumo.short_description = _('Média Consumo')

    # Vamos criar um campo para mostrar se temos fluxos de produção com input/output desatualizado
    def fluxo_input_output_atualizado_desatualizado(self, obj):
        if obj.pk != None:
            cursor = connection.cursor()
            # Expressão SQL
            sql = "select count(*) from fluxos_tbfluxoproducao where id in (select mae_id from fluxos_tbfluxoproducaodaugther where flu_pro_dau_consumo_padrao_id = " + str(
                obj.id) + ")"
            cursor.execute(sql)
            total = cursor.fetchone()[0]
            sql = "select count(*) from fluxos_tbfluxoproducao where flu_pro_input_output_atualizado = true and id in (select mae_id from fluxos_tbfluxoproducaodaugther where flu_pro_dau_consumo_padrao_id = " + str(
                obj.id) + ")"
            cursor.execute(sql)
            total_sim = cursor.fetchone()[0]
            cursor.close()
            if total_sim == total:
                if total == 0:
                    return 'Não Tem Fluxo'
                else:
                    return 'Sim ' + ' / ' + locale.format_string('%.0f', total_sim, True)
            else:
                return 'Não ' + ' / ' + locale.format_string('%.0f', total - total_sim, True)
        else:
            return ''

    fluxo_input_output_atualizado_desatualizado.short_description = _('Fluxos I/O Atualizados')

    actions = ['exportar_excel', 'importar_excel', 'importar_excel_new', 'validar_consumo_padrao',
               'desvalidar_consumo_padrao', 'atualiza_input_output_fluxos', 'update_indicador_geral']

    def atualiza_input_output_fluxos(self, request, queryset):

        # Vamos montar uma lista dos id dos fluxos de produção que estão usando o consumos padrões selecionados e que estão desatualizados
        id_consumo_padrao = queryset.values_list('id', flat=True)
        lista = []
        id_cenario_ativo = TbCenarios.objects.get(cen_ativo=1).id
        for i in id_consumo_padrao:
            id_fluxo = TbFluxoProducaoDaugther.objects.filter(flu_pro_dau_consumo_padrao_id=i, mae_id__in=(
                TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False,
                                               tbcenarios_id=id_cenario_ativo))).values_list('mae_id', flat=True)
            for j in id_fluxo:
                if j not in lista:
                    lista.append(j)

        if len(lista) == 0:
            self.message_user(request, 'NÃO TEMOS NENHUM FLUXO DE PRODUÇÃO COM INPUT/OUTPUT DESATUALIZADO!')

        else:

            if 'apply' in request.POST:  # if user pressed 'apply' on intermediate page
                # Cool thing is that params will have the same names as in forms.py

                # Tudo ok. Vamos atualizar

                atualizar_fluxo_celery.delay(lista)

                # Show alert that everything is cool
                self.message_user(request, 'Atualização Fluxos (Input/Output) sendo realizada em segundo plano!')

                # Return to previous page
                return HttpResponseRedirect(request.get_full_path())

            if 'cancelar' in request.POST:  # usuário cancelou
                # Return to previous page
                return HttpResponseRedirect(request.get_full_path())

            # Create form and pass the data which objects were selected before triggering 'broadcast' action
            # We create an intermediate page right here
            form = AtualizarFluxoForm(initial={'_selected_action': queryset.values_list('id', flat=True)})

            # We need to create a template of intermediate page with form - but this is really easy
            return render(request, "admin/fluxos_producao_atualizar_input_output.html",
                          {'items': TbFluxoProducao.objects.filter(id__in=lista), 'form': form})

    atualiza_input_output_fluxos.short_description = _("Atualizar Fluxos (Input/Output) Itens Selecionados")

    def update_indicador_geral(self, request,
                               queryset):  # Atualiza os indicadores do consumo padrão de acordo com o consumo específico indicado
        # SÓ ATUALIZA SE FOR INDICADO CONSUMO ESPECÍFICO PARA O CONSUMO PADRÃO
        consumos = queryset.values_list('id', )
        for consumo in consumos:
            if TbFluxoConsumoPadrao.objects.get(
                    id=consumo[0]).flu_con_pad_consumo_especifico:  # Se foi indicado consumo específico
                update_indicador(consumo[0])

        messages.success(request, _('Update do indicador dos consumos padrões realizado com sucesso!'))

    update_indicador_geral.short_description = _("Update Indicador Consumos Padrões Selecionados")

    def validar_consumo_padrao(self, request, queryset):

        consumos = queryset.values_list('id', )

        for consumo in consumos:
            tab_obj = TbFluxoConsumoPadrao.objects.get(id=consumo[0])
            tab_obj.flu_con_pad_validado = 1
            tab_obj.save()

    validar_consumo_padrao.short_description = _('Validar Consumos Padrões Selecionados')

    def desvalidar_consumo_padrao(self, request, queryset):

        consumos = queryset.values_list('id', )

        for consumo in consumos:
            tab_obj = TbFluxoConsumoPadrao.objects.get(id=consumo[0])
            tab_obj.flu_con_pad_validado = 0
            tab_obj.save()

    desvalidar_consumo_padrao.short_description = _('Desvalidar Consumos Padrões Selecionados')

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('fluxos.change_tbfluxoconsumopadrao'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def importar_excel_new(self, request, queryset):
        # try:
        # Para permitit acesso ao AWS S3
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY
        bucket_name = 'spsferbasa'
        object_key = 'fluxoconsumopadrao_new.xls'

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
                             'flu_con_pad_descricao',
                             'flu_con_pad_from_equipamento_id',
                             'flu_con_pad_from_equipamento_nome_ordem',
                             'flu_con_pad_to_equipamento_id',
                             'flu_con_pad_to_equipamento_nome_ordem',
                             'flu_con_pad_consumo_especifico_id',
                             'flu_con_pad_observacao',
                             'valor_inicial',
                             'id_origem',
                             'cenarios_id']:

            #  Cabeçalho da mãe está correto.
            #  Vamos ver se o cenário informado na mãe é o cenário ativo.
            # Cenário ativo
            ok_cenario = True
            cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

            sheet = wb.sheet_by_index(0)  # Abrindo a primeira aba
            for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 10
                if i > 0:  # Porque a linha 0 é o cabeçalho
                    if sheet.cell_value(i, 10) != cen_ativo:
                        ok_cenario = False
                        break

            if not ok_cenario:
                # Na realidade poderiamos desconsiderar essa consistência e assumir o cenário ativo.
                # Estamos fazendo dessa forma para forçar que na coluna de ordem 5 seja informado o cenário ativo.
                messages.error(request, _('Cenário informado na planilha não é o ativo. Favor verificar!'))
            else:
                # Tudo ok até aqui.
                # Vamos ver se a informação é nova.
                # Nesse caso temos que ver se o equipamento from e to são novos. Informações estão nas colunas de ordem 2 e 4
                existe = False
                for i in range(total_linhas_mae):
                    if i > 0:  # Porque a linha 0 é o cabeçalho
                        if TbFluxoConsumoPadrao.objects.filter(flu_con_pad_from_equipamento_id=sheet.cell_value(i, 2),
                                                               flu_con_pad_to_equipamento_id=sheet.cell_value(i, 4),
                                                               tbcenarios_id=cen_ativo).count() > 0:
                            existe = True
                            break

                if not existe:
                    # Tudo ok até aqui. Podemos continuar

                    # Temos que verificar se os Ids informados para os equipamentos from e to estão cadastrados
                    # Verificando Id Equipamento From
                    existe_equipamento_from = True
                    for i in range(total_linhas_mae):
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if TbEquipamentos.objects.filter(id=sheet.cell_value(i, 2),
                                                             tbcenarios_id=cen_ativo).count() == 0:  # Não existe
                                existe_equipamento_from = False
                                break  # Podemos sair fora

                    if existe_equipamento_from:
                        # Verificando Id Equipamento To
                        existe_equipamento_to = True
                        for i in range(total_linhas_mae):
                            if i > 0:  # Porque a linha 0 é o cabeçalho
                                if TbEquipamentos.objects.filter(id=sheet.cell_value(i, 4),
                                                                 tbcenarios_id=cen_ativo).count() == 0:  # Não existe
                                    existe_equipamento_to = False
                                    break  # Podemos sair fora

                        if existe_equipamento_to:
                            # Vamos agora incluir o registro e as filhas a partir do(s) valor(es) inicial(ais) informado(s).
                            # Se foi informado algum valor na coluna id nós desconsideramos.
                            for i in range(total_linhas_mae):
                                if i > 0:  # Porque a linha 0 é o cabeçalho

                                    # Vamos ajustar a descrição do fluxo de produção
                                    codigo_from = TbEquipamentos.objects.get(id=sheet.cell_value(i, 2)).equ_codigo
                                    ordem_from = TbEquipamentos.objects.get(id=sheet.cell_value(i, 2)).equ_ordem_codigo
                                    codigo_to = TbEquipamentos.objects.get(id=sheet.cell_value(i, 4)).equ_codigo
                                    ordem_to = TbEquipamentos.objects.get(id=sheet.cell_value(i, 4)).equ_ordem_codigo
                                    descricao = str(codigo_from) + '/' + str(ordem_from) + ' --> ' + str(
                                        codigo_to) + '/' + str(ordem_to)

                                    TbFluxoConsumoPadrao.objects.create(flu_con_pad_descricao=descricao,
                                                                        flu_con_pad_from_equipamento_id=sheet.cell_value(
                                                                            i, 2),
                                                                        flu_con_pad_to_equipamento_id=sheet.cell_value(
                                                                            i, 4),
                                                                        flu_con_pad_consumo_especifico_id=sheet.cell_value(
                                                                            i, 6),
                                                                        flu_con_pad_observacao=sheet.cell_value(i, 7),
                                                                        valor_inicial=sheet.cell_value(i, 8),
                                                                        tbcenarios_id=cen_ativo)

                                    '''
                                    # Removemos pois no pós save da tabela já verifica a filha. Esse python é muito bom
                                    # Temos que pegar o id da mãe para lançar na filha
                                    id_mae = TbFluxoConsumoPadrao.objects.get(
                                        flu_con_pad_from_equipamento_id=sheet.cell_value(i, 2),
                                        flu_con_pad_to_equipamento_id=sheet.cell_value(i, 4)).id
                                    # Vamos atualizar a filha usando o Stored Procedure verifica_filha
                                    cursor = connection.cursor()
                                    # Montando a expressão sql para rodar o Stored Procedure verifica_filha
                                    sql = "call public.verifica_filha('fluxos_" + 'tbfluxoconsumopadrao' + "daugther', " + str(
                                        id_mae) + ", " + str(cen_ativo) + ", " + str(sheet.cell_value(i, 8)) + ")"
                                    cursor.execute(sql)
                                    cursor.close()
                                    '''

                            # Se chegou até aqui tudo ok. Vamos dar a mensagem de sucesso
                            messages.success(request,
                                             _('Novo(s) Consumo(s) Padrão(ões) foi(ram) adicionado(s) com sucesso.'))
                            # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
                            try:
                                bucket_object.delete()
                                messages.success(request,
                                                 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
                            except:
                                messages.error(request,
                                               'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

                        else:
                            messages.error(request, 'Equipamento To/Ordem de Produção com Id ' + str(
                                int(sheet.cell_value(i, 4))) + ' não cadastrado para esse cenário. Favor verificar!')

                    else:
                        messages.error(request, 'Equipamento From/Ordem de Produção com Id ' + str(
                            int(sheet.cell_value(i, 2))) + ' não cadastrado para esse cenário. Favor verificar!')

                else:
                    messages.error(request, 'Já existe o Consumo Padrão com Ids ' + str(
                        int(sheet.cell_value(i, 2))) + ' --> ' + str(
                        int(sheet.cell_value(i, 4))) + ' cadastrado para esse cenário. Favor verificar!')

        else:
            messages.error(request, _('Cabeçalho do arquivo Excel não está correto. Favor verificar!'))

    # except:
    #    messages.error(request, 'Não foi encontrado o arquivo ' + object_key + ' no AWS S3 Bucket spsferbasa. Favor verificar!')

    importar_excel_new.short_description = _('Importar Excel (Novos)')

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'importar_excel_new':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbFluxoConsumoPadrao.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbFluxoConsumoPadraoAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        # try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY
        bucket_name = 'spsferbasa'
        object_key = 'fluxoconsumopadrao_mae_filhas.xls'

        # Vamos abrir o arquivo Excel que foi gravado no AWS S3

        # messages.success(request, 'Verificando se temos o arquivo ' + object_key + ' no AWS S3 Bucket spsferbasa!')

        s3_session = Session(aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)

        bucket_object = s3_session.resource('s3').Bucket(bucket_name).Object(object_key)

        # messages.success(request, 'Encontrado o arquivo ' + object_key + ' no AWS S3 Bucket spsferbasa. Lendo arquivo!')

        content = bucket_object.get()['Body'].read()
        # messages.success(request, 'Arquivo ' + object_key + ' foi lido no AWS S3 Bucket spsferbasa. Abrindo planilha Excel!')

        wb = open_workbook_xls(file_contents=content)

        # messages.success(request, 'Planilha ' + object_key + ' aberta com sucesso. Iniciado procedimento!')

        sheet = wb.sheet_by_index(0)  # Abrindo a mãe
        # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
        total_colunas_mae = sheet.ncols
        total_linhas_mae = sheet.nrows
        lista_colunas = []
        for i in range(total_colunas_mae):
            lista_colunas.append(sheet.cell_value(0, i))

        if lista_colunas == ['id',
                             'flu_con_pad_descricao',
                             'flu_con_pad_from_equipamento_id',
                             'flu_con_pad_from_equipamento_nome_ordem',
                             'flu_con_pad_to_equipamento_id',
                             'flu_con_pad_to_equipamento_nome_ordem',
                             'flu_con_pad_consumo_especifico_id',
                             'flu_con_pad_observacao',
                             'valor_inicial',
                             'id_origem',
                             'cenarios_id']:
            #  Cabeçalho da mãe está correto. Vamos agora ver a filha
            # messages.success(request, _('Cabeçalho da aba mãe correto!'))

            sheet = wb.sheet_by_index(1)  # Abrindo a filha
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_filha = sheet.ncols
            total_linhas_filha = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_filha):
                lista_colunas.append(sheet.cell_value(0, i))

            if lista_colunas == ['id',
                                 'dau_order',
                                 'dau_valor',
                                 'mae_id',
                                 'cenarios_id']:

                #  Filha também com cabeçalho correto. Podemos continuar
                # messages.success(request, _('Cabeçalho da aba filha está correto!'))

                #  Vamos ver se o total de lançamentos na filha está de acordo com o total de mães. Vamos precisar do total de períodos do cenário ativo.
                #  Vamos obter o total de períodos para o cenário ativo
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                if (total_linhas_filha - 1) == (total_linhas_mae - 1) * total_periodos:
                    # messages.success(request, _('Total de lançamentos na aba filha está correto!'))

                    #  Vamos ver se o cenário informado na mãe e na filha é o cenário ativo.
                    # Cenário ativo
                    ok_cenario = True
                    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                    # messages.success(request, _('Abrindo aba mãe!'))
                    sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                    for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 10 na mãe
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 10) != cen_ativo:
                                ok_cenario = False
                                break

                    # messages.success(request, _('Abrindo aba filha!'))
                    sheet = wb.sheet_by_index(1)  # Abrindo a filha
                    for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 4 na filha
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 4) != cen_ativo:
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
                                    for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                        if i > 0 and sheet.cell_value(i, 3) == id_mae[
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
                                            messages_error = 'Sequencial dau_order para a mãe id = ' + str(
                                                id_mae[0]) + ' está fora da sequencia na filha. Favor verificar!'
                                            break
                                        else:
                                            #  Tudo ok até aqui. Podemos continuar
                                            #  Vamos agora atualizar a mãe e as filhas pois tudo está Ok com o arquivo Excel
                                            sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                                            for i in range(total_linhas_mae):  # A coluna do id da mãe é a de ordem 0

                                                if i > 0 and sheet.cell_value(i, 0) == id_mae[
                                                    0]:  # Porque a linha 0 é o cabeçalho
                                                    # Vamos ver se foi informado o id do consumo específico
                                                    if str(sheet.cell_value(i,
                                                                            6)) == '':  # Não foi informado o id do consumo específico no sistema de custo
                                                        id_consumo_especifico = '0'  # Assumimos zero para passar um integer para o stored procedure
                                                    else:
                                                        id_consumo_especifico = str(int(sheet.cell_value(i, 6)))
                                                    cursor = connection.cursor()

                                                    # Montando a descrição do consumo padrão
                                                    codigo_from = TbEquipamentos.objects.get(
                                                        id=sheet.cell_value(i, 2)).equ_codigo
                                                    ordem_from = TbEquipamentos.objects.get(
                                                        id=sheet.cell_value(i, 2)).equ_ordem_codigo
                                                    codigo_to = TbEquipamentos.objects.get(
                                                        id=sheet.cell_value(i, 4)).equ_codigo
                                                    ordem_to = TbEquipamentos.objects.get(
                                                        id=sheet.cell_value(i, 4)).equ_ordem_codigo
                                                    descricao = str(codigo_from) + '/' + str(
                                                        ordem_from) + ' --> ' + str(codigo_to) + '/' + str(ordem_to)

                                                    # Montando a expressão sql para rodar o Stored Procedure
                                                    sql = "call public.atualiza_consumo_padrao_mae(" + \
                                                          str(id_mae[0]) + ",'" + \
                                                          descricao + "'," + \
                                                          str(int(sheet.cell_value(i, 2))) + "," + \
                                                          str(int(sheet.cell_value(i, 4))) + "," + \
                                                          id_consumo_especifico + ",'" + \
                                                          sheet.cell_value(i, 7) + "')"
                                                    cursor.execute(sql)
                                                    cursor.close()

                                            # messages.success(request, _('Aba mãe atualizada com sucesso!'))

                                            # Vamos agora atualizar as filhas
                                            sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                            for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 3
                                                if i > 0 and sheet.cell_value(i, 3) == id_mae[0]:  # É o id da mãe.

                                                    cursor = connection.cursor()
                                                    # Montando a expressão sql para rodar o Stored Procedure
                                                    sql = "call public.atualiza_consumo_padrao_filha(" + \
                                                          str(int(sheet.cell_value(i, 0))) + "," + \
                                                          str(sheet.cell_value(i, 2)) + \
                                                          ")"
                                                    # print(sql)
                                                    cursor.execute(sql)
                                                    cursor.close()

                                            # messages.success(request, _('Aba filha atualizada com sucesso!'))
                        if tudo_ok:
                            messages.success(request, _('Tabela Consumos Padrões foi atualizada com sucesso!'))

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
                                   _('Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!'))

            else:
                messages.error(request, _('Cabeçalho da aba filha não está correto. Favor verificar!'))

        else:
            messages.error(request, _('Cabeçalho da aba mãe não está correto. Favor verificar!'))
        # except:
        #    messages.error(request,
        #                   'Não foi encontrado o arquivo ' + object_key + ' no AWS S3 Bucket spsferbasa. Favor verificar!')

    importar_excel.short_description = _('Importar Excel')

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="fluxoconsumopadrao_mae_filhas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id',
                   'flu_con_pad_descricao',
                   'flu_con_pad_from_equipamento_id',
                   'flu_con_pad_from_equipamento_nome_ordem',
                   'flu_con_pad_to_equipamento_id',
                   'flu_con_pad_to_equipamento_nome_ordem',
                   'flu_con_pad_consumo_especifico_id',
                   'flu_con_pad_observacao',
                   'valor_inicial',
                   'id_origem',
                   'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id',
                                    'flu_con_pad_descricao',
                                    'flu_con_pad_from_equipamento_id',
                                    'flu_con_pad_to_equipamento_id',
                                    'flu_con_pad_consumo_especifico_id',
                                    'flu_con_pad_observacao',
                                    'valor_inicial',
                                    'id_origem',
                                    'tbcenarios_id').order_by('id')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            col_flu_con_pad_from_equipamento_id = 2
            col_flu_con_pad_to_equipamento_id = 3

            # Vamos pegar os nomes e ordem de produção dos equipamentos
            # Equipamento from
            equipamento_from_id = TbEquipamentos.objects.get(id=row[col_flu_con_pad_from_equipamento_id]).equ_codigo_id
            ordem = TbEquipamentos.objects.get(id=row[col_flu_con_pad_from_equipamento_id]).equ_ordem_codigo
            flu_con_pad_from_equipamento_nome_ordem = TbEquipamentosCadastro.objects.get(
                id=equipamento_from_id).equ_cad_codigo + '/' + str(ordem)
            # Equipamento to
            equipamento_to_id = TbEquipamentos.objects.get(id=row[col_flu_con_pad_to_equipamento_id]).equ_codigo_id
            ordem = TbEquipamentos.objects.get(id=row[col_flu_con_pad_to_equipamento_id]).equ_ordem_codigo
            flu_con_pad_to_equipamento_nome_ordem = TbEquipamentosCadastro.objects.get(
                id=equipamento_to_id).equ_cad_codigo + '/' + str(ordem)

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(col_flu_con_pad_from_equipamento_id + 1, flu_con_pad_from_equipamento_nome_ordem)
            list_aux.insert(col_flu_con_pad_to_equipamento_id + 2, flu_con_pad_to_equipamento_nome_ordem)
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

        filhas = TbFluxoConsumoPadraoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['id',
                   'dau_order',
                   'dau_valor',
                   'mae_id',
                   'cenarios_id']

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id',
                                  'dau_order',
                                  'dau_valor',
                                  'mae_id',
                                  'tbcenarios_id').order_by('mae_id', 'dau_order')
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar Excel')

    # Action
    def update_indicador_consumo_especifico(self, request, obj):
        update_indicador(obj.id)
        # current_datetime(request)
        messages.success(request, _('Update do indicador de consumo específico realizado com sucesso!'))

    update_indicador_consumo_especifico.label = "Update Indicador"  # optional

    change_actions = ('update_indicador_consumo_especifico',)

    form = TbFluxoConsumoPadraoFormAdmin

    # save_on_top = True

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbFluxoConsumoPadraoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo ind_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbFluxoConsumoPadraoDaugtherAdmin]


# Registrando
admin.site.register(TbFluxoConsumoPadrao, TbFluxoConsumoPadraoAdmin)


class TbFluxoProducaoInputOutputDaugtherAdmin(nested_admin.NestedTabularInline):
    # fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3',  'dau_valor_4', 'running', 'produtividade', 'indfun', 'custo_ate_equipamento', 'produtividade_equivalente')
    fields = ('display_order', 'dau_valor_3', 'dau_valor_4', 'running', 'produtividade', 'indfun',
              'custo_ate_equipamento', 'produtividade_equivalente')

    readonly_fields = ('display_order', 'running', 'produtividade', 'indfun', 'custo_ate_equipamento',
                       'produtividade_equivalente')

    extra = 0

    model = TbFluxoProducaoInputOutputDaugther
    form = TbFluxoProducaoInputOutputDaugtherFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Não permite exclusão.
    def has_delete_permission(self, request, obj=None):
        return False

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False


class TbFluxoProducaoInputOutputAdmin(nested_admin.NestedTabularInline):
    fields = ('mae', 'flu_pro_inp_out_coluna', 'flu_pro_inp_out_linha', 'flu_pro_inp_out_equipamento',
              'flu_pro_inp_out_envia_para')
    extra = 0

    model = TbFluxoProducaoInputOutput
    form = TbFluxoProducaoInputOutputFormAdmin

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros com flag = 1
    def get_queryset(self, request):
        return super(TbFluxoProducaoInputOutputAdmin, self).get_queryset(request).filter(flag=1)

    # Não permite exclusão.
    def has_delete_permission(self, request, obj=None):
        return False

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False

    inlines = [TbFluxoProducaoInputOutputDaugtherAdmin, ]


class TbFluxoProducaoDaugther01Admin(admin.TabularInline):
    fields = ('display_order', 'dau_valor', 'custo_variavel', 'custo_variavel_item', 'custo_variavel_inbound',
              'custo_variavel_manutencao', 'gargalo_produtividade', 'producao_maxima')
    readonly_fields = ('display_order', 'custo_variavel', 'custo_variavel_item', 'custo_variavel_inbound',
                       'custo_variavel_manutencao', 'gargalo_produtividade', 'producao_maxima')

    model = TbFluxoProducaoDaugther01
    form = TbFluxoProducaoDaugther01FormAdmin

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


class TbFluxoProducaoDaugtherAdmin(admin.TabularInline):
    # fields = ('flu_pro_dau_coluna', 'flu_pro_dau_linha', 'flu_pro_dau_consumo_padrao', 'descricao_consumo_padrao', 'flu_pro_dau_margem_horaria')
    fields = ('flu_pro_dau_coluna', 'flu_pro_dau_linha', 'flu_pro_dau_consumo_padrao', 'descricao_consumo_padrao')

    extra = 0  # Para não mostrar linha em branco no final
    readonly_fields = ('descricao_consumo_padrao',)

    model = TbFluxoProducaoDaugther
    form = TbFluxoProducaoDaugtherFormAdmin

    def save_model(self, request, obj, form, change):
        # add an additional message
        messages.info(request, _("Atualização do fluxo de produção está sendo feita em segundo plano."))
        super(TbFluxoProducaoDaugtherAdmin, self).save_model(request, obj, form, change)

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Vamos ver se usuário é superuser. Se sim, permite exclusão.
    def has_delete_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Vamos ver se usuário é superuser. Se sim, permite adição.
    def has_add_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser
    '''


class TbFluxoProducao01Admin(nested_admin.NestedModelAdmin):
    fields = (('flu_pro_descricao', 'flu_pro_produto', 'flu_pro_custo_variavel_medio', 'flu_pro_ativo'),)
    list_display = ['id', 'flu_pro_descricao', 'flu_pro_produto', 'flu_pro_custo_variavel_medio', 'flu_pro_ativo']
    list_filter = (('flu_pro_produto', admin.RelatedOnlyFieldListFilter), 'flu_pro_ativo')
    list_display_links = ['id', 'flu_pro_descricao', 'flu_pro_produto']
    search_fields = ['flu_pro_descricao']
    list_per_page = 15

    def has_delete_permission(self, request, obj=None):
        return False

    # Vamos ver se usuário é superuser. Se sim, permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '20'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 105})},
    }

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbFluxoProducao01Admin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    inlines = [TbFluxoProducaoInputOutputAdmin]


# Registrando
admin.site.register(TbFluxoProducao01, TbFluxoProducao01Admin)


class TbFluxoProducaoAdmin(DjangoObjectActions, admin.ModelAdmin):
    fieldsets = (
        ('Informações Básicas', {
            'fields': (('id', 'flu_pro_descricao', 'flu_pro_ativo', 'flu_pro_input_output_atualizado'),
                       ('flu_pro_produto', 'flu_pro_custo_variavel_medio'), 'flu_pro_pdf_file', 'flu_pro_observacao',
                       'flu_pro_erro')
        }),
        ('Visualização', {
            'fields': ('visualizar_fluxo',),
        }),
        ('Metadados', {
            'fields': ('flu_pro_dados_fluxo', 'flu_pro_data_criacao', 'flu_pro_data_modificacao'),
            'classes': ('collapse',)
        }),
    )

    def get_fieldsets(self, request, obj=None):
        if not obj:  # Se for uma adição (obj=None)
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    '''
    def get_fields(self, request, obj=None):
        if obj:  # Significa que está editando
            self.fields = (
            ('id', 'flu_pro_descricao', 'flu_pro_ativo', 'flu_pro_input_output_atualizado'), ('flu_pro_produto', 'flu_pro_custo_variavel_medio'), 'flu_pro_pdf_file', 'flu_pro_observacao','flu_pro_erro', 'visualizar_fluxo')
        else:
            self.fields = (
            ('flu_pro_descricao', 'flu_pro_ativo'), 'flu_pro_produto', 'flu_pro_observacao', 'flu_pro_erro',
            'flu_pro_copiar_de')
        return self.fields
    '''

    list_display = ['id', 'flu_pro_produto', 'flu_pro_descricao', 'flu_pro_ativo', 'flu_pro_input_output_atualizado',
                    'flu_pro_erro', 'flu_pro_custo_variavel_medio', 'flu_pro_pdf_file', 'visualizar_fluxo']
    list_editable = ['flu_pro_ativo', ]

    list_display_links = ['id', 'flu_pro_produto', 'flu_pro_descricao']

    readonly_fields = ('id', 'flu_pro_erro', 'flu_pro_custo_variavel_medio', 'flu_pro_data_criacao',
                       'flu_pro_data_modificacao', 'visualizar_fluxo')

    class output_real_zerado(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'Output Real Zero'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'output_real_zero'

        def lookups(self, request, model_admin):
            # Vamos montar a tuple na mão
            tuple = [(1, 'Sim'), (0, 'Não')]
            return tuple

        def queryset(self, request, queryset):
            if self.value() != None:
                # Vamos montar uma lista do fluxos que estão na queryset
                lista_in = list(queryset.values_list('id', flat=True))

                # Vamos passar essa lista para a procedure que verifica se o fluxo tem output zerado
                # Vamos montar uma lista das sequência que estão com o output real = 0 através de procedure
                cursor = connection.cursor()
                sql = "call public.output_real_zero(array" + str(lista_in) + ", null)"
                cursor.execute(sql)
                lista = cursor.fetchone()[0]
                cursor.close()

                if lista == None:
                    lista = []

                if int(self.value()) == 1:
                    return queryset.filter(id__in=lista)
                else:
                    return queryset.filter().exclude(id__in=lista)

            else:
                return queryset

    class custo_variavel_zerado(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'Custo Variável Zero'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'custo_variavel_zero'

        def lookups(self, request, model_admin):
            # Vamos montar a tuple na mão
            tuple = [(1, 'Sim'), (0, 'Não')]
            return tuple

        def queryset(self, request, queryset):
            if self.value() != None:
                # Vamos montar uma lista do fluxos que estão na queryset
                lista_in = list(queryset.values_list('id', flat=True))

                # Vamos passar essa lista para a procedure que verifica se o fluxo tem custo variavel zerado
                cursor = connection.cursor()
                sql = "call public.custo_variavel_zero(array" + str(lista_in) + ", null)"
                cursor.execute(sql)
                lista = cursor.fetchone()[0]
                cursor.close()

                if lista == None:
                    lista = []

                if int(self.value()) == 1:
                    return queryset.filter(id__in=lista)
                else:
                    return queryset.filter().exclude(id__in=lista)

            else:
                return queryset

    list_filter = (('flu_pro_produto', admin.RelatedOnlyFieldListFilter), 'flu_pro_ativo',
                   'flu_pro_input_output_atualizado', output_real_zerado, custo_variavel_zerado, 'flu_pro_erro')
    search_fields = ['flu_pro_produto__pro_codigo', 'flu_pro_descricao', ]
    list_per_page = 15

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('fluxos.change_tbfluxoproducao'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def has_delete_permission(self, request, obj=None):
        return False

    actions = ['delete_selected', 'exportar_excel', 'importar_excel', 'importar_excel_new',
               'importar_excel_new_segundo', 'importar_excel_xlsx_new', 'importar_excel_xlsx_new_segundo',
               'atualizar_fluxo_admin', 'atualizar_custo_admin', 'atualizar_fluxo_pdf', 'ativar_fluxo',
               'desativar_fluxo', 'excluir_output_real_zerado', 'verificar_erro_fluxo']

    def delete_selected(modeladmin, request, queryset):
        # Vamos ver se é superusuário.
        # Se sim, remove fluxos selecionados.
        # Também o cenário ativo não poderá ser removido
        if request.user.is_superuser:

            lista = list(queryset.values_list('id', flat=True))
            # Vamos excluir os fluxos selecionados
            remover_fluxo.delay(lista)
            messages.success(request,
                             _('Fluxo(s) selecionado(s) sendo excluidos em segundo plano. Para verificar o status da exclusão, refresh a tela.'))

        else:
            messages.error(request,
                           _("Você não tem autorização para excluir fluxo(s). Favor entrar em contato com administrador do sistema!"))

    delete_selected.short_description = _('Remover Fluxo(s) Selecionado(s)')

    def excluir_output_real_zerado(self, request, queryset):

        # Vamos transformar o id da queryset numa lista para passar para a função celery no segundo plano. Não aceita passar a queryset
        lista = []
        id_lista = queryset.values_list('id', flat=True)
        for i in id_lista:
            lista.append(i)

        excluir_output_real_zerado_lista.delay(lista)
        messages.success(request, _('Sequências zeradas dos fluxos selecionados sendo removidas em segundo plano!'))

    excluir_output_real_zerado.short_description = _('Remover Sequências Output Real Zerado Fluxos Selecionados')

    def ativar_fluxo(self, request, queryset):
        fluxos = queryset.values_list('id', flat=True)

        for fluxo in fluxos:
            tab_obj = TbFluxoProducao.objects.get(id=fluxo)
            tab_obj.flu_pro_ativo = 1
            tab_obj.save()

    ativar_fluxo.short_description = _('Ativar Fluxos Selecionados')

    def verificar_erro_fluxo(self, request, queryset):
        fluxos = queryset.values_list('id', flat=True)

        for fluxo in fluxos:
            #  Vamos verificar se esse fluxo tem erro
            cursor = connection.cursor()
            sql = "call public.verifica_fluxo(" + str(fluxo) + ")"
            cursor.execute(sql)
            cursor.close()

    verificar_erro_fluxo.short_description = _('Verificar Erros Fluxos Selecionados')

    def desativar_fluxo(self, request, queryset):
        fluxos = queryset.values_list('id', flat=True)

        for fluxo in fluxos:
            tab_obj = TbFluxoProducao.objects.get(id=fluxo)
            tab_obj.flu_pro_ativo = 0
            tab_obj.save()

    desativar_fluxo.short_description = _('Desativar Itens Selecionados')

    def atualizar_fluxo_pdf(self, request, queryset):
        # Vamos transformar o id da queryset numa lista para passar para a função celery no segundo plano. Não aceita passar a queryset
        lista = []
        id_lista = queryset.values_list('id', flat=True)
        for i in id_lista:
            lista.append(i)

        update_fluxo_lista.delay(lista)
        messages.success(request, _('Fluxos PDF sendo atualizados em segundo plano!'))

    atualizar_fluxo_pdf.short_description = _('Update Fluxo PDF Itens Selecionados')

    def atualizar_fluxo_admin(self, request, queryset):
        # Vamos transformar o id da queryset numa lista para passar para a função celery no segundo plano. Não aceita passar a queryset
        lista = []
        id_lista = queryset.values_list('id', flat=True)
        for i in id_lista:
            lista.append(i)

        # print(lista)
        atualizar_fluxo_celery.delay(lista)
        messages.success(request, _('Fluxos (input/output) sendo atualizados em segundo plano!'))

    atualizar_fluxo_admin.short_description = _('Atualizar Input/Output/Custos dos Fluxos Selecionados')

    def atualizar_custo_admin(self, request, queryset):
        # Vamos transformar o id da queryset numa lista para passar para a função celery no segundo plano. Não aceita passar a queryset
        lista = []
        id_lista = queryset.values_list('id', flat=True)
        for i in id_lista:
            lista.append(i)

        atualizar_custos_celery.delay(lista)
        messages.success(request, _('Custos sendo atualizados em segundo plano!'))

    atualizar_custo_admin.short_description = _('Atualizar Custos dos Fluxos Selecionados')

    def importar_excel_new(self, request, queryset):

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
                                messages.error(request, 'Não existe o consumo padrão com id = ' + str(
                                    sheet.cell_value(i, 5)) + ' cadastrado. Favor verificar!')
                                break  # Pula fora do for ...
                        else:
                            dados_ok = False
                            messages.error(request, 'Não existe o produto com id = ' + str(
                                sheet.cell_value(i, 1)) + ' cadastrado. Favor verificar!')
                            break  # Pula fora do for ...
                    else:
                        dados_ok = False
                        messages.error(request, 'Já existe o fluxo de produção = ' + sheet.cell_value(i,
                                                                                                      0) + '. Favor verificar!')
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
                            id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo,
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
                messages.success(request, _('Novo(s) Fluxo(s) de Produção foi(ram) adicionado(s) com sucesso.'))
                # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
                try:
                    bucket_object.delete()
                    messages.success(request, 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
                except:
                    messages.error(request,
                                   'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

        else:
            # print(lista_colunas)
            messages.error(request, _('Cabeçalho do arquivo Excel não está correto. Favor verificar!'))

    importar_excel_new.short_description = _('Importar Excel/xls 1º Plano (Novos)')

    def importar_excel_new_segundo(self, request, queryset):

        importar_excel_new_segundo_celery.delay()
        messages.success(request, _('Importação Novos Fluxos de Produção sendo realizada em segundo plano!'))

    importar_excel_new_segundo.short_description = _('Importar Excel/xls 2º Plano (Novos)')

    def importar_excel_xlsx_new(self, request, queryset):

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
                    if TbFluxoProducao.objects.filter(flu_pro_descricao=sheet.cell(i, 1).value,
                                                      tbcenarios_id=cen_ativo).count() == 0:
                        # Não existe. Podemos contonuar.
                        # Vamos ver se o id do produto informado existe.
                        if TbProdutos.objects.filter(id=sheet.cell(i, 2).value).count() > 0:
                            # Existe. Podemos continuar.
                            # Vamos ver se id do consumo padrão informado existe.
                            if TbFluxoConsumoPadrao.objects.filter(id=sheet.cell(i, 6).value).count() > 0:
                                # Existe. Podemos continuar.
                                pass
                            else:
                                dados_ok = False
                                messages.error(request, 'Não existe o consumo padrão com id = ' + str(
                                    sheet.cell(i, 6)) + ' cadastrado. Favor verificar!')
                                break  # Pula fora do for ...
                        else:
                            dados_ok = False
                            messages.error(request, 'Não existe o produto com id = ' + str(
                                sheet.cell(i, 2).value) + ' cadastrado. Favor verificar!')
                            break  # Pula fora do for ...
                    else:
                        dados_ok = False
                        messages.error(request, 'Já existe o fluxo de produção = ' + sheet.cell(i,
                                                                                                1).value + '. Favor verificar!')
                        break  # Pula fora do for ...

            if dados_ok:
                # Tudo ok com os dados. Podemos atualizar
                for i in range(1, total_linhas_mae + 1):
                    if i > 1:  # Porque a linha 1 é o cabeçalho
                        descricao_fluxo = sheet.cell(i, 1).value[:150]
                        # Vamos ver se existe o fluxo cadastrado. Se não cadastra.
                        if TbFluxoProducao.objects.filter(flu_pro_descricao=descricao_fluxo,
                                                          tbcenarios_id=cen_ativo).count() == 0:
                            # Vamos cadastrar

                            TbFluxoProducao.objects.create(
                                flu_pro_descricao=sheet.cell(i, 1).value[:150],
                                flu_pro_produto_id=int(sheet.cell(i, 2).value),
                                flu_pro_ativo=True,
                                tbcenarios_id=cen_ativo)

                            # Temos que pegar o id da mãe que foi criado para lançar na filha
                            id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo,
                                                                 tbcenarios_id=cen_ativo).id

                            # Vamos atualizar a filha usando o Stored Procedure verifica_filha_boolean (só tem um campo e é boolean)
                            cursor = connection.cursor()
                            # Montando a expressão sql para rodar o Stored Procedure
                            sql = "call public.verifica_filha_boolean('fluxos_tbfluxoproducaodaugther01', " + str(
                                id_mae) + ", " + str(cen_ativo) + ", true)"
                            cursor.execute(sql)
                            cursor.close()
                        else:
                            # Fluxo de produção foi cadastrado anteriormente. Vamos pegar o id da mãe.
                            id_mae = TbFluxoProducao.objects.get(flu_pro_descricao=descricao_fluxo,
                                                                 tbcenarios_id=cen_ativo).id

                        # Vamos cadastrar o consumo padrão na tabela de sequenciamento da produção
                        TbFluxoProducaoDaugther.objects.create(
                            flu_pro_dau_coluna=int(sheet.cell(i, 4).value),
                            flu_pro_dau_linha=int(sheet.cell(i, 5).value),
                            flu_pro_dau_consumo_padrao_id=int(sheet.cell(i, 6).value),
                            mae_id=id_mae,
                            tbcenarios_id=cen_ativo)

                # Vamos informar que o arquivo foi atualizado com sucesso e eliminar o mesmo do AWS S3
                # Se chegou até aqui tudo ok. Vamos dar a mensagem de sucesso
                messages.success(request, _('Novo(s) Fluxo(s) de Produção foi(ram) adicionado(s) com sucesso.'))
                # Vamos deletar o arquivo no AWS S3. Isso é para evitar reutilização do mesmo
                try:
                    bucket_object.delete()
                    messages.success(request, 'Arquivo ' + object_key + ' foi removido do AWS S3 Bucket spsferbasa!')
                except:
                    messages.error(request,
                                   'Arquivo ' + object_key + ' não foi removido do AWS S3 Bucket spsferbasa. Favor remover manualmente.')

        else:
            messages.error(request, _('Cabeçalho do arquivo Excel no AWS não está correto. Favor verificar!'))

    importar_excel_xlsx_new.short_description = _('Importar Excel/xlsx 1º Plano (Novos)')

    def importar_excel_xlsx_new_segundo(self, request, queryset):

        importar_excel_xlsx_new_segundo_celery.delay()
        messages.success(request, _('Importação Novos Fluxos de Produção sendo realizada em segundo plano!'))

    importar_excel_xlsx_new_segundo.short_description = _('Importar Excel/xlsx 2º Plano (Novos)')

    # Para permitir rodar importar_excel_new sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'importar_excel_new' or request.POST[
            'action'] == 'importar_excel_new_segundo' or request.POST['action'] == 'importar_excel_xlsx_new' or
                                         request.POST['action'] == 'importar_excel_xlsx_new_segundo' or request.POST[
                                             'action'] == 'desenhar_fluxo'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbFluxoProducao.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbFluxoProducaoAdmin, self).changelist_view(request, extra_context)

    def importar_excel(self, request, queryset):
        # Vamos transformar o id da queryset numa lista para passar para a função celery no segundo plano. Não aceita passar a queryset
        lista = []
        id_lista = queryset.values_list('id')
        for i in id_lista:
            lista.append(i)

        importar_excel_fluxo_producao_celery.delay(lista)
        messages.success(request, _('Tabela Fluxo de Produção sendo atualizada em segundo plano!'))

    importar_excel.short_description = _('Importar Excel')

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os registros selecionados. O relatório será enviado via e-mail ao usuário ao término do processo.

        # Vamos montar uma lista dos id registros na queryset
        lista_id = list(queryset.values_list('id', flat=True))
        # Vamos pegar o e-mail do usuário
        user_mail = User.objects.get(username=request.user).email
        exportar_excel_fluxo_producao_celery.delay(lista_id, user_mail)
        messages.success(request,
                         'Exportação para Excel sendo realizada em segundo plano. Ao terminar o processo o arquivo será enviado ao e-mail ' + user_mail + '. Favor aguardar por volta de 01 minuto e verificar seu e-mail.')

    exportar_excel.short_description = _('Exportar Excel')

    # Action
    def atualizar_fluxo(self, request, obj):

        # Vamos atualizar o flag do Input/Output (I/O)
        TbFluxoProducao.objects.filter(id=obj.id).update(flu_pro_input_output_atualizado=False)

        atualiza_input_output_celery.delay(obj.id)
        messages.success(request,
                         _('Atualização do Fluxo (Input/Output) sendo realizada em segundo plano. Favor aguardar!'))

    atualizar_fluxo.label = "Atualizar Fluxo (Input/Output)"

    def update_fluxo(self, request, obj):
        update_fluxo_celery.delay(obj.id)
        # update_fluxo_celery(obj.id)
        # current_datetime(request)
        messages.success(request, _('Update do arquivo PDF do fluxo sendo realizado em segundo plano. Favor aguardar!'))

    update_fluxo.label = "Update Fluxo (PDF)"

    change_actions = ('atualizar_fluxo', 'update_fluxo',)
    form = TbFluxoProducaoFormAdmin

    save_on_top = False

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '20'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 105})},
    }

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbFluxoProducaoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    def save_model(self, request, obj, form, change):
        if not change:  # Se for uma adição
            obj.flu_pro_dados_fluxo = {}  # Inicializa com um dicionário vazio
        super().save_model(request, obj, form, change)

    def visualizar_fluxo(self, obj):
        if obj.id:
            from django.utils.html import format_html
            return format_html(
                _('<a href="/fluxo_producao/editor/{0}/" class="button" target="_blank">Visualizar / Editar Fluxo</a>'),
                obj.id)
        return "Salve o fluxo primeiro para visualizá-lo"

    visualizar_fluxo.short_description = _("Visualizar Fluxo")

    inlines = [TbFluxoProducaoDaugtherAdmin, TbFluxoProducaoDaugther01Admin]


# Registrando
admin.site.register(TbFluxoProducao, TbFluxoProducaoAdmin)