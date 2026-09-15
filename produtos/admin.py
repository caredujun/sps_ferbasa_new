import os
from django.utils.translation import gettext_lazy as _
import boto3
import \
    xlwt  # xlwt para exportar para Excel formato .xls / xlrd para importar do Excel formato .xls. De acordo com internet é questão de segurança.
from boto3 import Session
from sps.settings import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
from xlrd.book import open_workbook_xls
from django.contrib import admin
from django.forms import Textarea
from fluxos.models import TbFluxoProducao
from .models import *
from django_object_actions import DjangoObjectActions
from django.http import HttpResponse
from django.contrib import messages
from .forms import TbProdutosFormAdmin, TbProdutoMercadoPrecoDaugtherFormAdmin, TbProdutoMercadoPrecoFormAdmin, \
    TbProdutoFluxoProducaoDaugtherFormAdmin, TbMercadoOutboundFormAdmin, TbMercadoOutboundDaugtherFormAdmin
from django.contrib.auth.models import User


# Para mostrar os fluxos que o produto está usando
class TbProdutoFluxoProducaoDaugtherAdmin(admin.TabularInline):
    # Tiramos pois quando temos muitos fluxos temos erro de timeout devido ao cálculo do custo variável médio
    #fields = ('flu_pro_descricao', 'flu_pro_ativo', 'custo_variavel_medio')
    #readonly_fields = ['custo_variavel_medio']
    fields = ('flu_pro_descricao', 'flu_pro_ativo', )

    model = TbFluxoProducao
    form = TbProdutoFluxoProducaoDaugtherFormAdmin

    show_change_link = True

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbProdutoFluxoProducaoDaugtherAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return False


class TbProdutosAdmin(admin.ModelAdmin):
    fields = (('id', 'pro_codigo', 'pro_descricao', 'pro_ativo'), ('total_mercados', 'total_fluxos_ativos', 'total_fluxos'),
              ('pro_unidade_producao', 'pro_familia'), ('pro_imagem', 'pro_imagem_tag'), 'pro_observacao')
    list_display = ['id', 'pro_codigo', 'pro_descricao', 'pro_ativo', 'pro_unidade_producao', 'pro_imagem',
                    'pro_imagem_tag', 'total_mercados', 'total_fluxos_ativos', 'total_fluxos']
    list_editable = ['pro_ativo']
    list_filter = ('pro_codigo','pro_ativo')
    list_display_links = ['id', 'pro_codigo']
    readonly_fields = ['id', 'pro_imagem_tag', 'total_mercados', 'total_fluxos_ativos', 'total_fluxos']
    search_fields = ['pro_codigo', ]

    model = TbProdutos
    form = TbProdutosFormAdmin

    actions = ['exportar_excel', 'importar_excel', 'ativar_produto', 'desativar_produto']

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('produtos.change_tbprodutos'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="produtos.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('Produtos')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome
        ws.write(row_num, 0, 'PRODUTOS / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = ['Id', 'Código', 'Descrição', 'Ativo', 'Unidade', 'Família Id', 'Família Nome', 'Observação',
                   'Cenário Id']
        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = queryset.values_list('id', 'pro_codigo', 'pro_descricao', 'pro_ativo', 'pro_unidade_producao',
                                    'pro_familia', 'pro_observacao', 'tbcenarios_id').order_by('id')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        # Nesse caso vamos incluir o nome da família do produto
        new_rows = []
        linha_num = 0
        for row in rows:
            id_produto = row[5]  # É o id da família do produto

            # Vamos pegar o nome da família do produto
            nome_familia = TbFamiliaProduto.objects.get(id=id_produto).fam_pro_codigo

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(6, nome_familia)

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows

        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        # Removemos pois no windows aparece VERDADEIRO OU FALSO. No Linux aparece 1 ou 0.
        # row_num += 3
        # ws.write(row_num, 0, 'Ativo = 1 -> Ativo')
        # row_num += 1
        # ws.write(row_num, 0, 'Ativo = 0 -> Não Ativo')

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar (Excel) Produtos Selecionados')

    def importar_excel(self, request, queryset):
        try:
            aws_id = AWS_ACCESS_KEY_ID
            aws_secret = AWS_SECRET_ACCESS_KEY
            bucket_name = 'spsferbasa'
            object_key = 'produtos.xls'

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

            sheet = wb.sheet_by_index(0)  # Abrindo o arquivo. Vamos pegar as informações na aba 'produtos', aba 0
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_arquivo = sheet.ncols

            # total_linhas_arquivo = sheet.nrows - 4 # Removemos as últimas 4 linhas pois não serão necessárias. Nesse caso foi usada para mostrar a legenda do campo Ativo
            total_linhas_arquivo = sheet.nrows

            lista_colunas = []
            for i in range(total_colunas_arquivo):
                lista_colunas.append(sheet.cell_value(1, i))  # O nome das colunas foram informadas na linha 1

            # Vamos assumir que tudo está ok no arquivo informado
            tudo_ok = True

            if lista_colunas == ['Id', 'Código', 'Descrição', 'Ativo', 'Unidade', 'Família Id', 'Família Nome',
                                 'Observação', 'Cenário Id']:
                # Tudo ok até aqui.

                # Vamos pegar o cenário ativo (id)
                cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                # Setando variáveis
                id_cenario_ok = True  # Vamos assumir que cenário a princípio está ok
                unidade_ok = True  # Vamos assumir que unidade a princípio está ok
                id_familia_ok = True  # Vamos assumir que id da família a princípio está ok

                for i in range(total_linhas_arquivo):  # A coluna do id da mãe é a de ordem 0
                    if i > 1:  # Porque linhas 0 e 1 são usadas para cabeçalhos

                        # Temos que verificar se o id do cenário informado é igual o id do cenário ativo
                        # A unidade informada está na coluna de ordem 3
                        if not sheet.cell_value(i, 8) == cen_ativo:
                            tudo_ok = False
                            id_cenario_ok = False
                            # Vamos sair fora e mostrar a mensagem de erro
                            break

                        # Temos que verificar se a unidade informada no arquivo em Excel está na lista definida do sistema
                        # A unidade informada está na coluna de ordem 4
                        if not sheet.cell_value(i, 4) in ['un', 'g', 'kg', 't', 'h', 'hh', 'm3', 'Nm3', 'L', 'kw',
                                                          'kwh', 'Mw', 'Mwh']:
                            tudo_ok = False
                            unidade_ok = False
                            # Vamos sair fora e mostrar a mensagem de erro
                            break

                        # Temos que verificar se o id da família do produto foi cadastrado
                        # O id da família do produto está na coluna de ordem 5
                        id_familia = sheet.cell_value(i, 5)
                        # Vamos ver se foi cadastrado
                        if TbFamiliaProduto.objects.filter(id=id_familia).count() == 0:
                            tudo_ok = False
                            id_familia_ok = False
                            # Vamos sair fora e mostrar a mensagem de erro
                            break

                # Mensagens de erro se existir
                if id_cenario_ok == False:
                    messages.error(request, 'Cenário Id informado na linha ' + str(
                        i) + ' da planilha não é o id do cenário ativo (' + str(cen_ativo) + '). Favor verificar!')

                if unidade_ok == False:
                    messages.error(request, 'Unidade do produto na linha ' + str(
                        i) + ' da planilha não é considerada no sistema. Favor verificar!')

                if id_familia_ok == False:
                    messages.error(request, 'Não existe no sistema o id da família do produto = ' + str(
                        sheet.cell_value(i, 5)) + ' informado na linha ' + str(
                        i) + ' do arquivo em Excel. Favor verificar!')

                if tudo_ok:
                    #  Vamos verificar se no arquivo tem o id dos produtos selecionados.
                    #  Vamos pegar o id dos produtos selecionados
                    id_produtos = queryset.values_list('id', )
                    for id_produto in id_produtos:
                        # Vamos ver se tem o id do produto informado no arquivo Excel, bem como se foi informado somente uma vez.                       sheet = wb.sheet_by_index(0)  # Abrindo o arquivo
                        conta_produtos = 0
                        for i in range(total_linhas_arquivo):  # A coluna do id da mãe é a de ordem 0
                            if i > 1:  # Porque linhas 0 e 1 são usadas para cabeçalhos

                                if sheet.cell_value(i, 0) == id_produto[0]:  # É o id produto. Soma no contador.
                                    conta_produtos = conta_produtos + 1

                        if conta_produtos == 0:
                            messages.error(request,
                                           'Na aba produto do arquivo em Excel não existe o produto com id = ' + str(
                                               id_produto[0]) + '). Favor verificar!')
                            break
                        else:
                            if conta_produtos > 1:
                                # Se tiver mais que 1 também é problema
                                messages.error(request,
                                               'Na aba produto do arquivo em Excel tem mais que 1(um)  produto com id = ' + str(
                                                   id_produto[0]) + '). Favor verificar!')
                                break
                            else:
                                # Tudo certo até aqui. Podemos continuar.
                                for i in range(total_linhas_arquivo):
                                    if i > 1 and sheet.cell_value(i, 0) == id_produto[
                                        0]:  # Porque linhas 0 e 1 são usadas para cabeçalhos
                                        tab_obj = TbProdutos.objects.get(id=sheet.cell_value(i, 0))

                                        tab_obj.pro_descricao = sheet.cell_value(i, 2)
                                        tab_obj.pro_ativo = sheet.cell_value(i, 3)
                                        tab_obj.pro_unidade_producao = sheet.cell_value(i, 4)
                                        tab_obj.pro_familia_id = int(sheet.cell_value(i, 5))
                                        tab_obj.pro_observacao = sheet.cell_value(i, 7)
                                        tab_obj.save()

                    messages.success(request, _('Tabela Produtos atualizada com sucesso!'))
                    # Deletando arquivo no AWS S3
                    s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
                    s3_client.delete_object(Bucket=bucket_name, Key=object_key)

            else:
                messages.error(request, _('Cabeçalho da aba produtos (primeira aba) não está correto. Favor verificar!'))

        except:
            messages.error(request,
                           _("Não foi encontrado o arquivo 'produtos.xls' no diretório no AWS S3. Favor verificar!"))

    importar_excel.short_description = _('Importar (Excel) Produtos Selecionados')

    def ativar_produto(self, request, queryset):

        produtos = queryset.values_list('id', )

        for produto in produtos:
            tab_obj = TbProdutos.objects.get(id=produto[0])
            tab_obj.pro_ativo = 1
            tab_obj.save()

    ativar_produto.short_description = _('Ativar Produtos Selecionados')

    def desativar_produto(self, request, queryset):

        produtos = queryset.values_list('id', )

        for produto in produtos:
            tab_obj = TbProdutos.objects.get(id=produto[0])
            tab_obj.pro_ativo = 0
            tab_obj.save()

    desativar_produto.short_description = _('Desativar Produtos Selecionados')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbProdutosAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    inlines = [TbProdutoFluxoProducaoDaugtherAdmin]


# Registrando
admin.site.register(TbProdutos, TbProdutosAdmin)


class TbProdutoMercadoPrecoDaugtherAdmin(admin.TabularInline):
    fields = (
    'display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'preco_moeda', 'preco_moeda_empresa', 'dau_valor_4')
    readonly_fields = ('display_order', 'preco_moeda', 'preco_moeda_empresa')

    model = TbProdutoMercadoPrecoDaugther
    form = TbProdutoMercadoPrecoDaugtherFormAdmin

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


class TbProdutoMercadoPrecoAdmin(admin.ModelAdmin):
    fields = (('pro_mer_pre_produto', 'pro_mer_pre_ativo', 'pro_mer_pre_validado', 'produto_imagem_tag_small', 'pro_mer_pre_codigo_interno', 'pro_mer_pre_descricao_interna'),
              ('pro_mer_pre_mercado', 'pro_mer_pre_estoque'), ('pro_mer_pre_indicador', 'pro_mer_pre_moeda'),
              ('pro_mer_pre_outbound', 'tem_outbound'), 'pro_mer_pre_equacao',
              ('pro_mer_pre_indicador_vol_min', 'pro_mer_pre_indicador_vol_max'), 'pro_mer_pre_observacao',
              'pro_mer_pre_fonte')
    list_display = ['pro_mer_pre_produto', 'produto_imagem_tag_small', 'pro_mer_pre_mercado', 'pro_mer_pre_ativo',
                    'pro_mer_pre_validado', 'pro_mer_pre_outbound', 'tem_outbound', 'pro_mer_pre_moeda', ]
    list_editable = ['pro_mer_pre_ativo', 'pro_mer_pre_outbound', 'pro_mer_pre_validado']
    readonly_fields = ('produto_imagem_tag_small', 'tem_outbound')
    list_filter = (('pro_mer_pre_produto', admin.RelatedOnlyFieldListFilter),
                   ('pro_mer_pre_mercado', admin.RelatedOnlyFieldListFilter),
                   'pro_mer_pre_ativo', 'pro_mer_pre_validado')
    search_fields = ['pro_mer_pre_produto__pro_codigo', ]
    form = TbProdutoMercadoPrecoFormAdmin

    actions = ['exportar_excel', 'importar_excel', 'validar_produto_mercado_preco', 'desvalidar_produto_mercado_preco']

    def validar_produto_mercado_preco(self, request, queryset):

        produto_mercado_precos = queryset.values_list('id', )

        for produto_mercado_preco in produto_mercado_precos:
            tab_obj = TbProdutoMercadoPreco.objects.get(id=produto_mercado_preco[0])
            tab_obj.pro_mer_pre_validado = 1
            tab_obj.save()

    validar_produto_mercado_preco.short_description = _('Validar Prod/Mercado/Preços Selecionados')

    def desvalidar_produto_mercado_preco(self, request, queryset):

        produto_mercado_precos = queryset.values_list('id', )

        for produto_mercado_preco in produto_mercado_precos:
            tab_obj = TbProdutoMercadoPreco.objects.get(id=produto_mercado_preco[0])
            tab_obj.pro_mer_pre_validado = 0
            tab_obj.save()

    desvalidar_produto_mercado_preco.short_description = _('Desvalidar Prod/Mercado/Preços Selecionados')

    # Removendo opção de importar se o usuário não tiver permissão para editar a tabela
    def get_actions(self, request):
        actions = super().get_actions(request)

        u = User.objects.get(username=request.user)
        if not u.has_perm('produtos.change_tbprodutomercadopreco'):
            if 'importar_excel' in actions:
                del actions['importar_excel']
            if 'importar_excel_new' in actions:
                del actions['importar_excel_new']

        return actions

    def exportar_excel(self, request, queryset):

        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Produto_Mercado_Preco_Mae_Filhas.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('mae')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos ver o tipo de período
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            tipo_periodo = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            tipo_periodo = 'Ano/Trimestre'
        else:
            tipo_periodo = 'Ano/Mês'

        columns = ['Id',                           # 0
                   'Produto Id',                   # 1
                   'Produto Nome',                 # 2
                   'Mercado Id',                   # 3
                   'Mercado Nome',                 # 4
                   'Estoque Dias Venda',           # 5
                   'Produto Ativo',                # 6
                   'Indicador Preço Id',           # 7
                   'Indicador Preço Nome',         # 8
                   'Indicador Vol. Mín. Id',       # 9
                   'Indicador Vol. Mín. Nome',     # 10
                   'Indicador Vol. Máx. Id',       # 11
                   'Indicador Vol. Máx. Nome',     # 12
                   'Moeda Preço',                  # 13
                   'Considerar Outbound',          # 14
                   'Equação Preço Id',             # 15
                   'Equação Preço Nome',           # 16
                   'Observação',                   # 17
                   'Cenário Id']                   # 18


        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()
        # Vamos lançar os dados numa queryset
        rows = queryset.values_list('id',
                                    'pro_mer_pre_produto_id',
                                    'pro_mer_pre_mercado_id',
                                    'pro_mer_pre_estoque',
                                    'pro_mer_pre_ativo',
                                    'pro_mer_pre_indicador_id',
                                    'pro_mer_pre_indicador_vol_min_id',
                                    'pro_mer_pre_indicador_vol_max_id',
                                    'pro_mer_pre_moeda',
                                    'pro_mer_pre_outbound',
                                    'pro_mer_pre_equacao_id',
                                    'pro_mer_pre_observacao',
                                    'tbcenarios_id').order_by('id')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            col_pro_mer_pre_produto_id = 1
            col_pro_mer_pre_mercado_id = 2
            col_pro_mer_pre_indicador_id = 5
            col_pro_mer_pre_indicador_vol_min_id = 6
            col_pro_mer_pre_indicador_vol_max_id = 7
            col_pro_mer_pre_equacao_id = 10

            # Ajustando o código dos indicadores e o id da equação de preço
            if row[col_pro_mer_pre_indicador_id] == None or row[col_pro_mer_pre_indicador_id] == '':
                indicador_1 = 0
            else:
                indicador_1 = row[col_pro_mer_pre_indicador_id]

            if row[col_pro_mer_pre_indicador_vol_min_id] == None or row[col_pro_mer_pre_indicador_vol_min_id] == '':
                indicador_2 = 0
            else:
                indicador_2 = row[col_pro_mer_pre_indicador_vol_min_id]

            if row[col_pro_mer_pre_indicador_vol_max_id] == None or row[col_pro_mer_pre_indicador_vol_max_id] == '':
                indicador_3 = 0
            else:
                indicador_3 = row[col_pro_mer_pre_indicador_vol_max_id]

            if row[col_pro_mer_pre_equacao_id] == None or row[col_pro_mer_pre_equacao_id] == '':
                equacao_id = 0
            else:
                equacao_id = row[col_pro_mer_pre_equacao_id]

            # Vamos pegar os nomes
            pro_mer_pre_produto_nome = TbProdutos.objects.get(id=row[col_pro_mer_pre_produto_id]).pro_descricao
            pro_mer_pre_mercado_nome = TbMercado.objects.get(id=row[col_pro_mer_pre_mercado_id]).mer_nome
            if indicador_1 != 0:
                if TbIndicadores.objects.filter(id=indicador_1).count() != 0:
                    pro_mer_pre_indicador_nome = TbIndicadores.objects.get(id=indicador_1).ind_nome
                else:
                    pro_mer_pre_indicador_nome = 'Não cadastrado. Verificar.'
            else:
                pro_mer_pre_indicador_nome = ''
            if indicador_2 != 0:
                if TbIndicadores.objects.filter(id=indicador_2).count() != 0:
                    pro_mer_pre_indicador_vol_min_nome = TbIndicadores.objects.get(id=indicador_2).ind_nome
                else:
                    pro_mer_pre_indicador_vol_min_nome = 'Não cadastrado. Verificar.'
            else:
                pro_mer_pre_indicador_vol_min_nome = ''
            if indicador_3 != 0:
                if TbIndicadores.objects.filter(id=indicador_3).count() != 0:
                    pro_mer_pre_indicador_vol_max_nome = TbIndicadores.objects.get(id=indicador_3).ind_nome
                else:
                    pro_mer_pre_indicador_vol_max_nome = 'Não cadastrado. Verificar.'
            else:
                pro_mer_pre_indicador_vol_max_nome = ''
            if equacao_id != 0:
                if TbEquacaoAjustePreco.objects.filter(id=equacao_id).count() != 0:
                    pro_mer_pre_equacao_nome = TbEquacaoAjustePreco.objects.get(id=equacao_id).equ_aju_pre_descricao
                else:
                    pro_mer_pre_equacao_nome = 'Não cadastrada. Verificar.'
            else:
                pro_mer_pre_equacao_nome = ''

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(2, pro_mer_pre_produto_nome)
            list_aux.insert(4, pro_mer_pre_mercado_nome)
            list_aux.insert(8, pro_mer_pre_indicador_nome)
            list_aux.insert(10, pro_mer_pre_indicador_vol_min_nome)
            list_aux.insert(12, pro_mer_pre_indicador_vol_max_nome)
            list_aux.insert(16, pro_mer_pre_equacao_nome)
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

        filhas = TbProdutoMercadoPrecoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        columns = ['Id',                       # 0  -> 0
                   'Order',                    # 1  -> 1
                   tipo_periodo,               # 2  ->
                   'Produto',                  # 3  ->
                   'Mercado',                  # 4  ->
                   'Unidade Produto',          # 5  ->
                   'Observação',               # 6  ->
                   'Produto Ativo',            # 7  ->
                   'Produto/Mercado Ativo',    # 8  ->
                   'Volume Mínimo',            # 9  -> 2
                   'Volume Máximo',            # 10 -> 3
                   'Moeda do Preço',           # 11 ->
                   'Equação do Preço',         # 12 ->
                   'Preço',                    # 13 -> 4
                   'Prazo Pagamento (dias)',   # 14 -> 5
                   'Mãe Id',                   # 15 -> 6
                   'Cenário Id']               # 16 -> 7

        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('id', 'dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'mae_id', 'tbcenarios_id').order_by('mae_id', 'dau_order')
        for row in rows:
            row_num += 1

            # Vamos agora pegar a descrição do dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + row[1])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(row[1] - 1):
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
                for j in range(row[1] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            for col_num in range(len(row) + 9): # 9 porque incluimos 9 colunas na planilha
                if col_num == 0: # id da filha
                    ws.write(row_num, col_num, row[col_num], font_style)

                if col_num == 1: # dau_order
                    ws.write(row_num, col_num, row[col_num], font_style)

                if col_num == 2: # tipo_periodo
                    ws.write(row_num, col_num, periodo_str, font_style)

                if col_num == 3: # produto
                    # Vamos a partir da mãe pegar o produto
                    nome_produto = TbProdutos.objects.get(id=TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_produto_id).pro_descricao
                    ws.write(row_num, col_num, nome_produto, font_style)

                if col_num == 4: # mercado
                    # Vamos a partir da mãe pegar o mercado
                    nome_mercado = TbMercado.objects.get(id=TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_mercado_id).mer_nome
                    ws.write(row_num, col_num, nome_mercado, font_style)

                if col_num == 5:  # unidade do produto
                    # Vamos a partir da mãe pegar a unidade do produto
                    unidade_produto = TbProdutos.objects.get(id=TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_produto_id).pro_unidade_producao
                    ws.write(row_num, col_num, unidade_produto, font_style)

                if col_num == 6:  # observação
                    # Vamos a partir da mãe pegar a observação para o produto/mercado
                    ws.write(row_num, col_num, TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_observacao, font_style)

                if col_num == 7: # produto ativo
                    # Vamos a partir da mãe ver se o produto está ativo
                    if TbProdutos.objects.get(id=TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_produto_id).pro_ativo == 1:
                        produto_ativo = 'SIM'
                    else:
                        produto_ativo = 'NÃO'
                    ws.write(row_num, col_num, produto_ativo, font_style)

                if col_num == 8: # produto/mercado ativo
                    # Vamos a partir da mãe ver se o produto/mercado está ativo
                    if TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_ativo == 1:
                        produto_mercado_ativo = 'SIM'
                    else:
                        produto_mercado_ativo = 'NÃO'
                    ws.write(row_num, col_num, produto_mercado_ativo, font_style)

                if col_num == 9: # volume mínimo
                    ws.write(row_num, col_num, row[2], font_style)

                if col_num == 10: # volume máximo
                    ws.write(row_num, col_num, row[3], font_style)

                if col_num == 11: # moeda do preço
                    # Vamos a partir da mãe pegar a moeda do preço
                    ws.write(row_num, col_num, TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_moeda, font_style)

                if col_num == 12: # equação de preço
                    # Vamos a partir da mãe pegar a equação de preço
                    if TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_equacao_id is not None:
                        equacao_descricao = TbEquacaoAjustePreco.objects.get(id=TbProdutoMercadoPreco.objects.get(id=row[6]).pro_mer_pre_equacao_id).equ_aju_pre_descricao
                    else:
                        equacao_descricao = ''
                    ws.write(row_num, col_num, equacao_descricao, font_style)

                if col_num == 13: # preço
                    ws.write(row_num, col_num, row[4], font_style)

                if col_num == 14: # prazo de pagamento
                    ws.write(row_num, col_num, row[5], font_style)

                if col_num == 15: # mãe id
                    ws.write(row_num, col_num, row[6], font_style)

                if col_num == 16: # cenário id
                    ws.write(row_num, col_num, row[7], font_style)

        wb.save(response)
        messages.success(request, _('Arquivo gerado com sucesso.'))

        return response

    exportar_excel.short_description = _('Exportar Excel')

    def importar_excel(self, request, queryset):
        #try:
        aws_id = AWS_ACCESS_KEY_ID
        aws_secret = AWS_SECRET_ACCESS_KEY
        bucket_name = 'spsferbasa'
        object_key = 'Produto_Mercado_Preco_Mae_Filhas.xls'

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

        if lista_colunas == ['Id',
                              'Produto Id',
                              'Produto Nome',
                              'Mercado Id',
                              'Mercado Nome',
                              'Estoque Dias Venda',
                              'Produto Ativo',
                              'Indicador Preço Id',
                              'Indicador Preço Nome',
                              'Indicador Vol. Mín. Id',
                              'Indicador Vol. Mín. Nome',
                              'Indicador Vol. Máx. Id',
                              'Indicador Vol. Máx. Nome',
                              'Moeda Preço',
                              'Considerar Outbound',
                              'Equação Preço Id',
                              'Equação Preço Nome',
                              'Observação',
                              'Cenário Id']:
            #  Cabeçalho da mãe está correto. Vamos agora ver a filha
            sheet = wb.sheet_by_index(1)  # Abrindo a filha
            # Vamos montar uma lista com os nomes das colunas para ver se o arquivo está correto
            total_colunas_filha = sheet.ncols
            total_linhas_filha = sheet.nrows
            lista_colunas = []
            for i in range(total_colunas_filha):
                lista_colunas.append(sheet.cell_value(0, i))


            # Vamos ver o tipo de período
            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                tipo_periodo = 'Ano'
            elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                tipo_periodo = 'Ano/Trimestre'
            else:
                tipo_periodo = 'Ano/Mês'

            if lista_colunas == ['Id',
                                 'Order',
                                 tipo_periodo,
                                 'Produto',
                                 'Mercado',
                                 'Unidade Produto',
                                 'Observação',
                                 'Produto Ativo',
                                 'Produto/Mercado Ativo',
                                 'Volume Mínimo',
                                 'Volume Máximo',
                                 'Moeda do Preço',
                                 'Equação do Preço',
                                 'Preço',
                                 'Prazo Pagamento (dias)',
                                 'Mãe Id',
                                 'Cenário Id']:

                #  Filha também com cabeçalho correto. Podemos continuar
                #  Vamos ver se o total de lançamentos na filha está de acordo com o total de mães. Vamos precisar do total de períodos do cenário ativo.
                #  Vamos obter o total de períodos para o cenário ativo
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                '''
                print('total_periodos')
                print(total_periodos)
                print(total_linhas_filha - 1)
                print(total_linhas_mae - 1)
                '''

                if (total_linhas_filha - 1) == (
                        total_linhas_mae - 1) * total_periodos:  # total de lançamentos na filha está de acordo com o total de mães
                    #  Vamos ver se o cenário informado na mãe e na filha é o cenário ativo.
                    # Cenário ativo
                    ok_cenario = True
                    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

                    sheet = wb.sheet_by_index(0)  # Abrindo a mãe
                    for i in range(total_linhas_mae):  # A coluna do cenário é a de ordem 18 na mãe
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 18) != cen_ativo:
                                ok_cenario = False
                                break

                    sheet = wb.sheet_by_index(1)  # Abrindo a filha
                    for i in range(total_linhas_filha):  # A coluna do cenário é a de ordem 16 na filha
                        if i > 0:  # Porque a linha 0 é o cabeçalho
                            if sheet.cell_value(i, 16) != cen_ativo:
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
                                    for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 15
                                        if i > 0 and sheet.cell_value(i, 15) == id_mae[
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
                                                total_linhas_filha):  # A coluna da mãe_id é a de ordem 15 e a do dau_order é o 1
                                            if i > 0:  # Porque a linha 0 é o cabeçalho

                                                if sheet.cell_value(i, 15) == id_mae[0]:  # É o id da mãe. Vamos somar no contador e comparar com o dau_order
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
                                                if i > 0 and sheet.cell_value(i, 0) == id_mae[0]:  # Porque a linha 0 é o cabeçalho
                                                    tab_obj = TbProdutoMercadoPreco.objects.get(
                                                        id=sheet.cell_value(i, 0))
                                                    tab_obj.pro_mer_pre_produto_id = int(sheet.cell_value(i, 1))
                                                    tab_obj.pro_mer_pre_mercado_id = int(sheet.cell_value(i, 3))
                                                    tab_obj.pro_mer_pre_estoque = int(sheet.cell_value(i, 5))
                                                    tab_obj.pro_mer_pre_ativo = int(sheet.cell_value(i, 6))

                                                    if sheet.cell_value(i, 7) == '':
                                                        tab_obj.pro_mer_pre_indicador_id = None
                                                    else:
                                                        tab_obj.pro_mer_pre_indicador_id = int(
                                                            sheet.cell_value(i, 7))

                                                    if sheet.cell_value(i, 9) == '':
                                                        tab_obj.pro_mer_pre_indicador_vol_min_id = None
                                                    else:
                                                        tab_obj.pro_mer_pre_indicador_vol_min_id = int(
                                                            sheet.cell_value(i, 9))

                                                    if sheet.cell_value(i, 11) == '':
                                                        tab_obj.pro_mer_pre_indicador_vol_max_id = None
                                                    else:
                                                        tab_obj.pro_mer_pre_indicador_vol_max_id = int(
                                                            sheet.cell_value(i, 11))

                                                    tab_obj.pro_mer_pre_moeda = sheet.cell_value(i, 13)
                                                    tab_obj.pro_mer_pre_outbound = sheet.cell_value(i, 14)

                                                    if sheet.cell_value(i, 15) == '':
                                                        tab_obj.pro_mer_pre_equacao_id = None
                                                    else:
                                                        tab_obj.pro_mer_pre_equacao_id = int(
                                                            sheet.cell_value(i, 15))

                                                    tab_obj.pro_mer_pre_observacao = sheet.cell_value(i, 17)
                                                    tab_obj.save()

                                            # Vamos agora atualizar as filhas
                                            sheet = wb.sheet_by_index(1)  # Abrindo as filhas
                                            for i in range(total_linhas_filha):  # A coluna da mãe_id é a de ordem 15
                                                if i > 0 and sheet.cell_value(i, 15) == id_mae[0]:  # É o id da mãe. Soma no contador.
                                                    # Temos que pegar o dau_order
                                                    dau_order_filha = sheet.cell_value(i, 1)
                                                    tab_obj = TbProdutoMercadoPrecoDaugther.objects.get(id=sheet.cell_value(i, 0), mae_id=id_mae[0], dau_order=dau_order_filha)
                                                    if sheet.cell_value(i, 9) == '':
                                                        tab_obj.dau_valor_1 = 0
                                                    else:
                                                        tab_obj.dau_valor_1 = sheet.cell_value(i, 9)
                                                    if sheet.cell_value(i, 10) == '':
                                                        tab_obj.dau_valor_2 = 0
                                                    else:
                                                        tab_obj.dau_valor_2 = sheet.cell_value(i, 10)
                                                    if sheet.cell_value(i, 13) == '':
                                                        tab_obj.dau_valor_3 = 0
                                                    else:
                                                        tab_obj.dau_valor_3 = sheet.cell_value(i, 13)
                                                    if sheet.cell_value(i, 14) == '':
                                                        tab_obj.dau_valor_4 = 0
                                                    else:
                                                        tab_obj.dau_valor_4 = sheet.cell_value(i, 14)
                                                    tab_obj.save()

                        if tudo_ok:
                            messages.success(request, _('Tabela Produtos/Volume/Preços por Mercado foi atualizada com sucesso!'))
                            # Deletando arquivo no AWS S3
                            s3_client = boto3.client('s3', aws_access_key_id=aws_id, aws_secret_access_key=aws_secret)
                            s3_client.delete_object(Bucket=bucket_name, Key=object_key)

                else:
                    messages.error(request, _('Total de lançamentos na aba mãe e/ou filhas não está correto. Favor verificar!'))

            else:
                messages.error(request, _('Cabeçalho da aba filha não está correto. Favor verificar!'))

        else:
            messages.error(request, _('Cabeçalho da aba mãe não está correto. Favor verificar!'))

        #except:
        #    messages.error(request, 'Não foi encontrado o arquivo ' + object_key + ' no AWS Amazon ou erro na rotina. Favor verificar!')

    importar_excel.short_description = _('Importar Excel')

    """
    # Removendo icons de edição, adicção e exclusão nos campos de foreignkey do form
    def get_form(self, request, obj=None, **kwargs):
        form = super(TbProdutoMercadoPrecoAdmin, self).get_form(request, obj, **kwargs)
        field = form.base_fields['pro_mer_pre_produto']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False

        field = form.base_fields['pro_mer_pre_mercado']
        field.widget.can_add_related = False
        field.widget.can_change_related = False
        field.widget.can_delete_related = False
        return form
    """

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbProdutoMercadoPrecoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + (('valor_inicial_1', 'valor_inicial_2'), ('valor_inicial_3', 'valor_inicial_4'))
        return self.fields

    inlines = [TbProdutoMercadoPrecoDaugtherAdmin]


# Registrando
admin.site.register(TbProdutoMercadoPreco, TbProdutoMercadoPrecoAdmin)


class TbMercadoOutboundDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor')
    readonly_fields = ('display_order',)

    model = TbMercadoOutboundDaugther
    form = TbMercadoOutboundDaugtherFormAdmin

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


class TbMercadoOutboundAdmin(admin.ModelAdmin):
    fields = ('mer_out_produto', 'mer_out_mercado', 'mer_out_unidade', ('mer_out_indicador', 'mer_out_moeda'),
              'mer_out_observacao')
    list_display = ['mer_out_produto', 'mer_out_mercado', 'mer_out_unidade', 'mer_out_moeda', 'mer_out_observacao']
    list_filter = (('mer_out_produto', admin.RelatedOnlyFieldListFilter),
                   ('mer_out_mercado', admin.RelatedOnlyFieldListFilter),
                   ('mer_out_unidade', admin.RelatedOnlyFieldListFilter))

    form = TbMercadoOutboundFormAdmin

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 100})},
    }

    # Removendo icons de edição, adicção e exclusão nos campos de foreignkey do form
    def get_form(self, request, obj=None, **kwargs):
        form = super(TbMercadoOutboundAdmin, self).get_form(request, obj, **kwargs)
        try:
            field = form.base_fields['mer_out_mercado']
            field.widget.can_add_related = False
            field.widget.can_change_related = False
            field.widget.can_delete_related = False
        except:
            pass

        try:
            field = form.base_fields['mer_out_unidade']
            field.widget.can_add_related = False
            field.widget.can_change_related = False
            field.widget.can_delete_related = False
        except:
            pass
        return form

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbMercadoOutboundAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    # Mostra o campo valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('valor_inicial',)
        return self.fields

    inlines = [TbMercadoOutboundDaugtherAdmin]


# Registrando
admin.site.register(TbMercadoOutbound, TbMercadoOutboundAdmin)