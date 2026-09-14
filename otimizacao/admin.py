import xlwt
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.http import HttpResponse
from django.forms import Textarea
from otimizacao.forms import TbProdutoMercadoFluxoFormAdmin, TbProdutoMercadoFluxoDaugtherFormAdmin, \
    TbOtimizacaoEquipamentosFormAdmin, TbOtimizacaoEquipamentosDaugtherFormAdmin, TbProdutoMercadoDaugtherFormAdmin, \
    TbProdutoMercadoFormAdmin, TbOtimizacaoCustoItemFormAdmin, TbOtimizacaoCustoItemDaugtherFormAdmin, \
    TbOtimizacaoEquipamentosOrdemDaugtherFormAdmin, TbOtimizacaoEquipamentosOrdemFormAdmin, \
    TbOtimizacaoComparacaoCenariosDaugtherFormAdmin, TbOtimizacaoComparacaoCenariosFormAdmin, TbOtimizacaoProdutoDaugtherFormAdmin, \
    TbOtimizacaoProdutoFormAdmin, TbConsumoEspecificoTipoProducaoFormAdmin, TbOtimizacaoConjuntoEquipamentosFormAdmin, TbOtimizacaoConjuntoEquipamentosDaugtherFormAdmin, \
    TbOtimizacaoShadowFormAdmin
from otimizacao.models import *
from parameters.models import TbCenarios
from tabelas.models import TbUnidadeProducao
from django.contrib.auth.models import User, Group
from produtos.models import TbProdutoMercadoPreco, TbProdutoMercadoPrecoDaugther
from tabelas.models import TbEquacaoAjustePreco

import io
from django.http import FileResponse
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from datetime import date
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.lib import colors
from .tasks import exportar_excel_tbprodutomercadofluxo_celery
from django.utils.html import format_html

import locale
locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  #  Estou usando esse pois Heroku não aceita pt_BR

class TbOtimizacaoConjuntoEquipamentosDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3')
    readonly_fields = ('display_order', 'dau_valor_3')

    model = TbOtimizacaoConjuntoEquipamentosDaugther
    form = TbOtimizacaoConjuntoEquipamentosDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

class TbOtimizacaoConjuntoEquipamentosAdmin(admin.ModelAdmin):
    fields           = (('oti_con_equ_descricao', 'oti_con_equ_ativo'), 'nota', 'oti_con_equ_equipamentos', 'oti_con_equ_observacao')
    list_display     = ('oti_con_equ_descricao', 'oti_con_equ_ativo')
    readonly_fields  = ('nota',)
    list_editable    = ['oti_con_equ_ativo', ]
    search_fields    = ['oti_con_equ_descricao', ]

    filter_horizontal = ('oti_con_equ_equipamentos',)
    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 103})}, }

    model = TbOtimizacaoConjuntoEquipamentos
    form  = TbOtimizacaoConjuntoEquipamentosFormAdmin

    # Mostra somente os registros do cenário ativo
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoConjuntoEquipamentosAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    inlines = [TbOtimizacaoConjuntoEquipamentosDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoConjuntoEquipamentos, TbOtimizacaoConjuntoEquipamentosAdmin)

class TbOtimizacaoProdutoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'margem_cont_bruta_media', 'dau_valor_7')
    readonly_fields = ('display_order', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'margem_cont_bruta_media', 'dau_valor_7')

    model = TbOtimizacaoProdutoDaugther
    form = TbOtimizacaoProdutoDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    #Permite edição
    def has_change_permission(self, request, obj=None):
        return True
    '''

class TbOtimizacaoProdutoAdmin(admin.ModelAdmin):
    fields = (('oti_pro_produto', 'produto_imagem_tag_small', 'produto_unidade'),)
    list_display = ('oti_pro_produto', 'produto_unidade', 'produto_imagem_tag_small', 'vendas_periodo', 'preco_medio_periodo', 'custo_variavel_medio_periodo', 'margem_contribuicao_media_periodo', 'margem_percentual_periodo', 'margem_horaria_media_periodo')
    readonly_fields = ('oti_pro_produto', 'produto_imagem_tag_small', 'produto_unidade')
    list_filter = (('oti_pro_produto', admin.RelatedOnlyFieldListFilter),)
    search_fields = ['oti_pro_produto__pro_codigo', ]


    model = TbOtimizacaoProduto
    form = TbOtimizacaoProdutoFormAdmin

    actions = ['exportar_excel', 'exportar_pdf', 'exportar_excel_consolidado']

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
        nome_arquivo = 'Otimização - Produtos - PDF'

        largura_pagina = 210*mm  # Padrão A4
        altura_pagina = 297*mm  # Padrão A4
        titulo_relatorio = 'Resultados Otimização - Produtos'

        p.setPageSize((largura_pagina, altura_pagina))                  # Dimensões padrão A4

        # Vamos pegar o logo da empresa e colocar no cabeçalho do relatório
        logo_empresa = TbEmpresa.objects.get(id=1).emp_logo
        logo_empresa = mark_safe('%s' % logo_empresa.url)  # Retorna a url do logo da empresa no AWS S3 ou do computador local no caso de desenvolvimento. Descobri tentando. Não achei na internet.

        # Vamos desenhar um produto por página
        # Vamos montar uma queryset com o id dos registros selecionados
        lista_id = queryset.values_list('id', 'oti_pro_produto_id')

        for produto_id in lista_id:


            p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

            # Cabeçalho do PDF
            # Setando tamanho do título e escrevendo os títulos no topo da página
            p.setFont("Helvetica-Bold", 18)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 45, 'Cenário : ' + cen_ativo + ' / ' + cen_ativo_nome)

            # Vamos colocar o logo da empresa e passar uma linha embaixo
            p.drawImage(logo_empresa, 10, altura_pagina - 50, width=50, height=40, mask=None)
            p.line(5, altura_pagina - 55, largura_pagina - 5, altura_pagina - 55)

            # Vamos colocar as informações do registro da tabela
            line_number = altura_pagina - 68
            p.setFont("Helvetica-Bold", 10)


            # Temos o id do produto. Vamos pegar as outras informações
            produto_codigo = TbProdutos.objects.get(id=produto_id[1]).pro_codigo
            produto_descricao = TbProdutos.objects.get(id=produto_id[1]).pro_descricao
            produto_unidade = TbProdutos.objects.get(id=produto_id[1]).pro_unidade_producao
            produto_imagem = TbProdutos.objects.get(id=produto_id[1]).pro_imagem
            produto_imagem = mark_safe('%s' % produto_imagem.url)

            p.drawCentredString(largura_pagina / 2, line_number, "PRODUTO")
            line_number = line_number - 20

            p.drawString(10, line_number, 'ID: ' + str(produto_id[1]))
            p.drawString(0.08*largura_pagina, line_number, 'Código: ' + produto_codigo)
            p.drawString(0.4*largura_pagina, line_number, 'Descrição: ' + produto_descricao)
            p.drawString(0.8*largura_pagina, line_number, 'Unidade: ' + produto_unidade)
            p.drawImage(produto_imagem , 0.93 * largura_pagina, line_number - 12.5, width=30, height=30, mask=None)

            line_number = line_number - 30
            p.drawString(0.15*largura_pagina, line_number, tipo_periodo)
            p.drawString(0.30 * largura_pagina, line_number, 'Volume Mínimo')
            p.drawString(0.50 * largura_pagina, line_number, 'Volume Máximo')
            p.drawString(0.75 * largura_pagina, line_number, 'Vendas')

            #Vamos criar a filha
            filha = TbOtimizacaoProdutoDaugther.objects.filter(mae_id=produto_id[0]).order_by('dau_order')
            rows = filha.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3').order_by('dau_order')

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

                p.drawString(0.15 * largura_pagina, line_number, periodo_str)
                p.drawString(0.30 * largura_pagina, line_number, locale.format_string('%.0f', row[1], True))
                p.drawString(0.50 * largura_pagina, line_number, locale.format_string('%.0f', row[2], True))
                p.drawString(0.75 * largura_pagina, line_number, locale.format_string('%.0f', row[3], True))

                data.append(int(row[3]))

                rotulo_x.append(periodo_str)


            # Gráfico de barras
            drawing = Drawing(largura_pagina - 30, 200)

            # coordinates (from left bottom)
            x = 15
            y = 25

            bar = VerticalBarChart()
            bar.x = x + 30
            bar.y = y + 5
            bar.width = largura_pagina - 80
            bar.height = 150
            bar.valueAxis.valueMin = 0
            data = [data,]

            bar.data = data
            bar.categoryAxis.categoryNames = rotulo_x
            bar.bars[0].fillColor = colors.green
            #bar.bars[1].fillColor = PCMYKColor(23, 51, 0, 4, alpha=85)
            #bar.bars.fillColor = PCMYKColor(100, 0, 90, 50, alpha=85)
            drawing.add(bar, '')
            renderPDF.draw(drawing, p, x, y, showBoundary=True)

            p.setFont("Helvetica-Bold", 14)
            p.drawCentredString(largura_pagina / 2, 230, "Vendas (" + produto_unidade + ")")

            p.setFont("Helvetica-Oblique", 8)
            p.drawCentredString(largura_pagina / 2, 10, "Copyright © 2022 SPS Consultoria. Todos os direitos reservados.")

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

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização - Produtos.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbOtimizacaoProdutoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0, 'RESULTADOS OTIMIZAÇÃO: PRODUTO / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = [tipo_periodo, 'Produto', 'Vol. Mínimo', 'Vol. Máximo', 'Vendas', 'Preço', 'Custo Variável', 'Margem Contribuição', 'Margem (%)', 'Margem Horária']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[7]

            # Vamos pegar os id e nomes
            id_produto = TbOtimizacaoProduto.objects.get(id=id_mae).oti_pro_produto_id
            nome_produto = TbProdutos.objects.get(id=id_produto).pro_codigo

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])

            # Inserindo nome do produto
            list_aux.insert(1, nome_produto)

            # Inserindo margem de contribuição
            list_aux.insert(7, row[4] - row[5])

            if row[4] != 0:
                list_aux.insert(8, round((row[4] - row[5]) * 100 / row[4],2))
            else:
                list_aux.insert(8, 0)


            # Vamos remover o mae_id da lista. Está na posição 11 após as inclusões dos nomes.
            del (list_aux[10])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    def exportar_excel_consolidado(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização - Produtos - Consolidado.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores da mãe
        ws = wb.add_sheet('resultados')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0, 'RESULTADOS OTIMIZAÇÃO: PRODUTOS CONSOLIDADOS / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = ['Produto', 'Vendas', 'Preço Médio', 'Custo Médio', 'Margem Média', 'Margem (%)']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        # Vamos montar uma queryset com os valores da mãe
        rows = queryset.values_list('id', 'oti_pro_produto_id')

        # Vamos incluir valores na lista new_rows
        new_rows = []
        list_aux = []
        for row in rows:
            list_aux = []
            id_produto = row[1]

            # Vamos pegar o nome do produto
            nome_produto = TbProdutos.objects.get(id=id_produto).pro_codigo

            # Inserindo na lista auxiliar
            list_aux.insert(0, nome_produto)
            cursor = connection.cursor()

            # Vendas
            sql = "call public.vendas_periodo(" + str(cen_ativo) + ", 'otimizacao_TbOtimizacaoProdutoDaugther'," + str(row[0]) + ", 'dau_valor_3'" + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            list_aux.insert(1, retorno)

            # Preço Médio
            sql = "call public.vendas_periodo(" + str(cen_ativo) + ", 'otimizacao_TbOtimizacaoProdutoDaugther'," + str(row[0]) + ", 'dau_valor_3'" + ", 0)"
            cursor.execute(sql)
            retorno_vendas = cursor.fetchone()[0]

            sql = "select sum(dau_valor_2 * dau_valor_4) from otimizacao_tbprodutomercadofluxodaugther where mae_id in (select id from otimizacao_tbprodutomercadofluxo where pro_mer_flu_produto_id = " + str(id_produto) + " and tbcenarios_id = " + str(cen_ativo) + ")"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            if retorno_vendas != 0:
                list_aux.insert(2, round(retorno / retorno_vendas, 0))
            else:
                list_aux.insert(2, '')

            # Custo Médio
            sql = "call public.vendas_periodo(" + str(cen_ativo) + ", 'otimizacao_TbOtimizacaoProdutoDaugther'," + str(
                row[0]) + ", 'dau_valor_3'" + ", 0)"
            cursor.execute(sql)
            retorno_vendas = cursor.fetchone()[0]

            sql = "select sum(dau_valor_2 * dau_valor_5) from otimizacao_tbprodutomercadofluxodaugther where mae_id in (select id from otimizacao_tbprodutomercadofluxo where pro_mer_flu_produto_id = " + str(
                id_produto) + " and tbcenarios_id = " + str(cen_ativo) + ")"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            if retorno_vendas != 0:
                list_aux.insert(3, round(retorno / retorno_vendas, 0))
            else:
                list_aux.insert(3, '')

            # Margem Média
            sql = "call public.vendas_periodo(" + str(cen_ativo) + ", 'otimizacao_TbOtimizacaoProdutoDaugther'," + str(
                row[0]) + ", 'dau_valor_3'" + ", 0)"
            cursor.execute(sql)
            retorno_vendas = cursor.fetchone()[0]

            sql = "select sum(dau_valor_2 * dau_valor_3) from otimizacao_tbprodutomercadofluxodaugther where mae_id in (select id from otimizacao_tbprodutomercadofluxo where pro_mer_flu_produto_id = " + str(
                id_produto) + " and tbcenarios_id = " + str(cen_ativo) + ")"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            if retorno_vendas != 0:
                list_aux.insert(4, round(retorno / retorno_vendas, 0))
            else:
                list_aux.insert(4, '')

            # Margem Média Percentual
            sql = "select sum(dau_valor_2 * dau_valor_4) from otimizacao_tbprodutomercadofluxodaugther where mae_id in (select id from otimizacao_tbprodutomercadofluxo where pro_mer_flu_produto_id = " + str(
                id_produto) + " and tbcenarios_id = " + str(cen_ativo) + ")"
            cursor.execute(sql)
            retorno_1 = cursor.fetchone()[0]

            # Vamos montar uma expressão SQL para calcular o percentual da margem de contribuição do produto para todos os periodos
            # Expressão SQL
            sql = "select sum(dau_valor_2 * dau_valor_3) from otimizacao_tbprodutomercadofluxodaugther where mae_id in (select id from otimizacao_tbprodutomercadofluxo where pro_mer_flu_produto_id = " + str(
                id_produto) + " and tbcenarios_id = " + str(cen_ativo) + ")"
            cursor.execute(sql)
            retorno_2 = cursor.fetchone()[0]
            cursor.close()
            if retorno_1 != 0:
                list_aux.insert(5, round(retorno_2 * 100 / retorno_1, 2))
            else:
                return ''

            cursor.close()
            new_rows.append(list_aux)

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel_consolidado.short_description = 'Exportar Excel Consolidado'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbOtimizacaoProduto.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbOtimizacaoProdutoAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoProdutoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Foi feito assim para permitir a deleção em cascata quando for deletar o cenário
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbotimizacaoproduto' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return True
    '''

    inlines = [TbOtimizacaoProdutoDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoProduto, TbOtimizacaoProdutoAdmin)


class TbProdutoMercadoFluxoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_7', 'dau_valor_2', 'dau_valor_3', 'dau_valor_11', 'equipamento_gargalo', 'dau_valor_4', 'dau_valor_5', 'dau_valor_8', 'dau_valor_9', 'dau_valor_6', 'dau_valor_10')
    readonly_fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4', 'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'dau_valor_8', 'dau_valor_9', 'dau_valor_10', 'dau_valor_11', 'equipamento_gargalo')

    model = TbProdutoMercadoFluxoDaugther
    form = TbProdutoMercadoFluxoDaugtherFormAdmin

    #Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    #Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbProdutoMercadoFluxoAdmin(admin.ModelAdmin):
    fields = (('pro_mer_flu_variavel', 'pro_mer_flu_produto', 'produto_imagem_tag_small'), ('pro_mer_flu_mercado', 'pro_mer_flu_fluxo_producao'),)
    list_display = ('pro_mer_flu_variavel', 'pro_mer_flu_produto', 'produto_imagem_tag_small', 'pro_mer_flu_mercado', 'pro_mer_flu_fluxo_producao', 'periodos_fluxos_running', 'periodos_equipamentos_running'
                        , 'vendas_periodo', 'preco_medio', 'custo_medio', 'margem_media')
    list_display_links = ('pro_mer_flu_variavel', 'pro_mer_flu_produto')
    search_fields = ['pro_mer_flu_produto__pro_codigo', 'pro_mer_flu_mercado__mer_nome', 'pro_mer_flu_fluxo_producao__flu_pro_descricao',]

    # Vamos criar um campo para mostrar a qtde de periodos que o fluxo está running na tabela filha
    def periodos_fluxos_running(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select count(*) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(obj.id) + " and dau_valor_1 = true"
        cursor.execute(sql)
        retorno_running = cursor.fetchone()[0]

        sql = "select count(*) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(obj.id)
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
            color, retorno, )

        return retorno

    # Vamos ver o tipo de periodo
    # 🌟 CORRIGIDO: protege contra a tabela/coluna ainda não existir
    # (acontece durante makemigrations de uma migration ainda não aplicada).
    try:
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            periodos_fluxos_running.short_description = 'Anos Running (Fluxo)'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            periodos_fluxos_running.short_description = 'Trimestres Running (Fluxo)'
        else:
            periodos_fluxos_running.short_description = 'Meses Running (Fluxo)'
    except Exception:
        periodos_fluxos_running.short_description = 'Período Running (Fluxo)'

    # Vamos criar um campo para mostrar a qtde de periodos que os equipamentos do fluxo está running na tabela filha
    def periodos_equipamentos_running(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select count(*) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(
            obj.id) + " and dau_valor_7 = true"
        cursor.execute(sql)
        retorno_running = cursor.fetchone()[0]

        sql = "select count(*) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(obj.id)
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
            color, retorno, )

        return retorno

    # Vamos ver o tipo de periodo
    # 🌟 CORRIGIDO: protege contra a tabela/coluna ainda não existir
    # (acontece durante makemigrations de uma migration ainda não aplicada).
    try:
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            periodos_equipamentos_running.short_description = 'Anos Running (Equip.)'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            periodos_equipamentos_running.short_description = 'Trimestres Running (Equip.)'
        else:
            periodos_equipamentos_running.short_description = 'Meses Running (Equip.)'
    except Exception:
        periodos_equipamentos_running.short_description = 'Período Running (Equip.)'

    class vendas(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'Vendas'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'vendas'

        def lookups(self, request, model_admin):
            # Vamos montar a tuple na mão
            tuple = [(1, 'Sim'), (0, 'Não')]
            return tuple

        def queryset(self, request, queryset):
            if self.value() != None:
                # Vamos montar uma lista dos fluxos de produção que tiveram vendas
                id_fluxos = queryset.values_list('id', flat=True)
                lista = []
                for i in id_fluxos:
                    cursor = connection.cursor()
                    # Expressão SQL
                    sql = "select sum(dau_valor_2) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(i)
                    cursor.execute(sql)
                    retorno = cursor.fetchone()[0]
                    cursor.close()
                    if retorno > 0:
                        lista.append(i)

                if int(self.value()) == 1:
                    return queryset.filter(id__in=lista)
                else:
                    return queryset.filter().exclude(id__in=lista)
            else:
                return queryset

    class margem(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'Margem Contribuição'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'margem_contribuicao'

        def lookups(self, request, model_admin):
            # Vamos montar a tuple na mão
            tuple = [(1, 'Positiva'), (0, 'Negativa')]
            return tuple

        def queryset(self, request, queryset):
            if self.value() != None:
                # Vamos montar uma lista dos fluxos de produção que tiveram vendas
                id_fluxos = queryset.values_list('id', flat=True)
                lista = []
                for i in id_fluxos:
                    cursor = connection.cursor()
                    # Expressão SQL
                    sql = "select avg(dau_valor_3) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(i)
                    cursor.execute(sql)
                    retorno = cursor.fetchone()[0]
                    cursor.close()
                    if retorno > 0:
                        lista.append(i)

                if int(self.value()) == 1:
                    return queryset.filter(id__in=lista)
                else:
                    return queryset.filter().exclude(id__in=lista)
            else:
                return queryset

    list_filter = (('pro_mer_flu_produto', admin.RelatedOnlyFieldListFilter), ('pro_mer_flu_mercado', admin.RelatedOnlyFieldListFilter), ('pro_mer_flu_fluxo_producao', admin.RelatedOnlyFieldListFilter), vendas, margem)

    model = TbProdutoMercadoFluxo
    form = TbProdutoMercadoFluxoFormAdmin

    actions = ['exportar_excel']

    def exportar_excel(self, request, queryset):
        # Vamos montar uma lista dos dos registros na tela
        lista_id = list(queryset.values_list('id', flat=True))
        # Vamos pegar o e-mail do usuário
        user_mail = User.objects.get(username=request.user).email

        exportar_excel_tbprodutomercadofluxo_celery.delay(lista_id, user_mail)
        #exportar_excel_tbprodutomercadofluxo_celery(lista_id, user_mail)
        messages.success(request, 'Exportação para Excel sendo realizada em segundo plano. Ao terminar (5/10 minutos), será enviado o arquivo ao e-mail cadastrado do usuário no sistema.')

        '''
        # Só exporta a tabela com os valores selecionados
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização Produtos Mercados Fluxos.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbProdutoMercadoFluxoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos ver o tipo de período
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            tipo_periodo = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            tipo_periodo= 'Ano/Trimestre'
        else:
            tipo_periodo = 'Ano/Mês'

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0, 'RESULTADOS OTIMIZAÇÃO: PRODUTO-MERCADO-FLUXO DE PRODUÇÃO / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = [tipo_periodo, 'Produto', 'Mercado', 'Preço Comercial', 'Unidade', 'Fluxo de Produção', 'Fluxo Running', 'Equip. Running', 'Vendas', 'Margen Contrib.', 'Margen Horária', 'Equip. Margem Horária', 'Preço', 'Custo Variável', 'Parcela Itens', 'Parcela Inbound', 'Parcela Outbound', 'Parcela Manutenção', 'Total Margem']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_7', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4', 'dau_valor_5', 'dau_valor_8', 'dau_valor_9', 'dau_valor_6', 'dau_valor_10', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[11]

            # Vamos pegar os id e nomes
            id_produto = TbProdutoMercadoFluxo.objects.get(id=id_mae).pro_mer_flu_produto_id
            nome_produto = TbProdutos.objects.get(id=id_produto).pro_codigo

            id_mercado = TbProdutoMercadoFluxo.objects.get(id=id_mae).pro_mer_flu_mercado_id
            nome_mercado = TbMercado.objects.get(id=id_mercado).mer_nome

            # Vamos pegar o preço comercial. Está na tabela TbProdutoMercadoPrecoDaugther
            # Temos que pegar o id na tabela mãe TbProdutoMercadoPreco
            id_produto_mercado = TbProdutoMercadoPreco.objects.get(pro_mer_pre_produto_id=id_produto, pro_mer_pre_mercado_id=id_mercado).id
            produto_mercado_preco = TbProdutoMercadoPrecoDaugther.objects.get(mae_id=id_produto_mercado, dau_order=row[0]).dau_valor_3

            # Vamos pegar a unidade do preço comercial
            if TbProdutoMercadoPreco.objects.get(pro_mer_pre_produto_id=id_produto, pro_mer_pre_mercado_id=id_mercado).pro_mer_pre_equacao_id:
                # Foi informado equação de preço. Vamos pegar a unidade do preço comercial na tabela TbEquacaoAjustePreco
                unidade_preco_comercial = TbEquacaoAjustePreco.objects.get(id=TbProdutoMercadoPreco.objects.get(pro_mer_pre_produto_id=id_produto, pro_mer_pre_mercado_id=id_mercado).pro_mer_pre_equacao_id).equ_aju_pre_descricao
            else:
                # Não foi informado equação. Vamos pegar a unidade de preço comercial na tabelaTbProdutoMercadoPreco
                unidade_preco_comercial = TbProdutoMercadoPreco.objects.get(pro_mer_pre_produto_id=id_produto, pro_mer_pre_mercado_id=id_mercado).pro_mer_pre_moeda

            id_fluxo = TbProdutoMercadoFluxo.objects.get(id=id_mae).pro_mer_flu_fluxo_producao_id
            nome_fluxo = TbFluxoProducao.objects.get(id=id_fluxo).flu_pro_descricao

            # Margem Horária
            cursor = connection.cursor()
            # Expressão SQL
            sql = "call public.gargalo_produtividade_fluxo_producao_order_valor(" + str(id_fluxo) + ", " + str(row[0]) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            # Retorna o valor da produtividade no equipamento selecionado para ser considerado na margem de contribuição horária.
            # Se não foi selecionado nenhum equipamento, retorna ZERO.
            # Se multiplicarmos pela margem de contribuição bruta (dau_valor_3=row[4]) temos a margem horária.
            if retorno > 0:
                margem_horaria = round(retorno * row[4],2)
            else:
                margem_horaria = 'ND'

            # Equipamento Margem Horária
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para mostrar o equipamento selecionado para definir a margem horária
            # Expressão SQL
            sql = "call public.gargalo_produtividade_fluxo_producao_order_equipamento(" + str(id_fluxo) + ", " + str(row[0]) + ", '')"
            cursor.execute(sql)
            equipamento_margem_horaria = cursor.fetchone()[0]
            cursor.close()

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, nome_produto)
            list_aux.insert(2, nome_mercado)
            list_aux.insert(3, produto_mercado_preco)
            list_aux.insert(4, unidade_preco_comercial)
            list_aux.insert(5, nome_fluxo)
            list_aux.insert(10, margem_horaria)
            list_aux.insert(11, equipamento_margem_horaria)
            # Vamos remover o mae_id da lista. Está na posição 18 após as inclusões dos nomes.
            del(list_aux[18])
            # Vamos incluir o total da margem de contribuição
            total_margem = list_aux[8] * list_aux[9]
            list_aux.insert(18, total_margem)

            # Vamos agora alterar o dau_order para mostrar o período no formato (ano, ano/mês ou ano/trimestre)
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response
        '''

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbProdutoMercadoFluxo.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbProdutoMercadoFluxoAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbProdutoMercadoFluxoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Para permitir a deleção de cenário em cascata
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbprodutomercadofluxo' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False

    inlines = [TbProdutoMercadoFluxoDaugtherAdmin, ]

# Registrando
admin.site.register(TbProdutoMercadoFluxo, TbProdutoMercadoFluxoAdmin)

class TbOtimizacaoEquipamentosDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_6', 'dau_valor_3', 'dau_valor_5', 'dau_valor_7', 'dau_valor_4')
    readonly_fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_6', 'dau_valor_3', 'dau_valor_4')

    model = TbOtimizacaoEquipamentosDaugther
    form = TbOtimizacaoEquipamentosDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Permite edição
    def has_change_permission(self, request, obj=None):
        return True
    '''

class TbOtimizacaoEquipamentosAdmin(admin.ModelAdmin):
    fields = (('oti_equ_equipamento'), ('equipamento_imagem_tag_small', 'output_equipamento', 'gargalo_equipamento'),)
    list_display = ('oti_equ_equipamento', 'gargalo_equipamento', 'periodos_running', 'producao_periodo', 'media_ocupacao_minima_periodo', 'media_ocupacao_maxima_periodo', 'ocupacao_periodo', 'equipamento_imagem_tag_small')
    readonly_fields = ('oti_equ_equipamento', 'output_equipamento', 'equipamento_imagem_tag_small', 'gargalo_equipamento', 'producao_periodo', 'ocupacao_periodo', 'periodos_running')
    list_filter = (('oti_equ_equipamento', admin.RelatedOnlyFieldListFilter),)
    search_fields = ['oti_equ_equipamento__equ_cad_codigo',]

    model = TbOtimizacaoEquipamentos
    form = TbOtimizacaoEquipamentosFormAdmin

    # Vamos criar um campo para mostrar a qtde de periodos que está running na tabela filha
    def periodos_running(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select count(*) from equipamentos_tbequipamentoscadastrodaugther where mae_id = " + str(obj.oti_equ_equipamento_id) + " and dau_valor_1 = true"
        cursor.execute(sql)
        retorno_running = cursor.fetchone()[0]

        sql = "select count(*) from equipamentos_tbequipamentoscadastrodaugther where mae_id = " + str(obj.oti_equ_equipamento_id)
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
            color, retorno, )

        return retorno

    # Vamos ver o tipo de periodo
    # 🌟 CORRIGIDO: protege contra a tabela/coluna ainda não existir
    # (acontece durante makemigrations de uma migration ainda não aplicada).
    try:
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            periodos_running.short_description = 'Anos Running'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            periodos_running.short_description = 'Trimestres Running'
        else:
            periodos_running.short_description = 'Meses Running'
    except Exception:
        periodos_running.short_description = 'Período Running'


    actions = ['exportar_excel']

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização Equipamentos.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        #filhas = TbOtimizacaoEquipamentosDaugther.objects.filter(mae_id__in=lista_id).order_by('mae_id__oti_equ_equipamento_id__equ_cad_codigo')
        filhas = TbOtimizacaoEquipamentosDaugther.objects.filter(mae_id__in=lista_id)

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0,
                 'RESULTADOS OTIMIZAÇÃO: EQUIPAMENTOS / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome,
                 font_style)

        columns = [tipo_periodo, 'Equipamento', 'Running', 'Produção(volume)', 'Produção(h)', 'Loading Time(h)', 'Ocupação Mínima (%)', 'Ocupação Máxima (%)', 'Ocupação(%)']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_6', 'dau_valor_3', 'dau_valor_5', 'dau_valor_7', 'dau_valor_4', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[8]

            # Vamos pegar os nomes
            nome_equipamento = str(TbOtimizacaoEquipamentos.objects.get(id=id_mae).oti_equ_equipamento)  # Se tivessemos colocado oti_equ_equipamento_id teriamos o id. Como não colocou, teremos o nome do equipamento. Muito bom esse Django/Python

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, nome_equipamento)

            # Vamos remover o mae_id da lista. Está na posição 9 após as inclusões dos nomes.
            del (list_aux[9])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = sorted(new_rows, key=lambda new_rows: new_rows[1])

        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbOtimizacaoEquipamentos.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbOtimizacaoEquipamentosAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoEquipamentosAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Para permitir a deleção em cascata ao deletar cenário
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbotimizacaoequipamentos' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return True
    '''

    inlines = [TbOtimizacaoEquipamentosDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoEquipamentos, TbOtimizacaoEquipamentosAdmin)

class TbOtimizacaoEquipamentosOrdemDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'running', 'dau_valor_1', 'dau_valor_2', 'participacao', 'dau_valor_3', 'dau_valor_4')
    readonly_fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'participacao', 'dau_valor_3', 'dau_valor_4', 'running')

    model = TbOtimizacaoEquipamentosOrdemDaugther
    form = TbOtimizacaoEquipamentosOrdemDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    #Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbOtimizacaoEquipamentosOrdemAdmin(admin.ModelAdmin):
    fields = (('oti_equ_ord_equipamento'), ('equipamento_ordem_imagem_tag_small', 'output_equipamento_ordem', 'gargalo_equipamento_ordem'), 'tipo_producao')
    list_display = ('oti_equ_ord_equipamento', 'periodos_ativa', 'producao_periodo', 'percentual_periodo', 'equipamento_ordem_imagem_tag_small')
    readonly_fields = ('tipo_producao','producao_periodo', 'percentual_periodo', 'periodos_ativa')

    # Vamos montar um filtro para filtrar somente o forno
    class Forno_ListFilter(admin.SimpleListFilter):
        # Human-readable title which will be displayed in the
        # right admin sidebar just above the filter options.
        title = 'Equipamento'

        # Parameter for the filter that will be used in the URL query.
        parameter_name = 'equipamento'

        def lookups(self, request, model_admin):

            # Vamos pegar somente os equipamentos que estão na tabela. Somente do cenário ativo
            qs = TbOtimizacaoEquipamentosOrdem.objects.filter(tbcenarios_id=TbCenarios.objects.get(cen_ativo=True).id).distinct('oti_equ_ord_equipamento_id__equ_codigo_id').order_by('oti_equ_ord_equipamento_id__equ_codigo_id')
            '''
            if 'pedido' in request.GET:
                qs = qs.filter(rec_pedido_id=request.GET['pedido'])

            if 'item_consumo' in request.GET:
                qs = qs.filter(rec_item_consumo_id=request.GET['item_consumo'])

            if 'nota_fiscal' in request.GET:
                qs = qs.filter(rec_nota_fiscal=request.GET['nota_fiscal'])

            if 'transportadora' in request.GET:
                qs = qs.filter(rec_transportadora_id=request.GET['transportadora'])

            if 'lote_destino' in request.GET:
                qs = qs.filter(rec_lote_destino_id=request.GET['lote_destino'])

            if 'data_recebimento' in request.GET:
                qs = qs.filter(rec_data=request.GET['data_recebimento'])
            '''
            # Vamos montar a tuple na mão
            tuple = []
            tuple_aux = []

            for q in qs:
                # Vamos pegar o id do equipamento
                id_equipamento = TbEquipamentos.objects.get(id=q.oti_equ_ord_equipamento_id).equ_codigo_id
                # Vamos pegar o código do equipamento
                codigo_equipamento = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_codigo
                if codigo_equipamento not in tuple_aux:
                    tuple_aux.append(codigo_equipamento)
                    tuple.append((id_equipamento, codigo_equipamento))

            # Vamos ordenar a tuple pelo código do equipamento
            tuple.sort(key=lambda tup: tup[1])

            return tuple

        def queryset(self, request, queryset):
            if self.value():
                return queryset.filter(oti_equ_ord_equipamento__equ_codigo_id=self.value())
            else:
                return queryset

    list_filter = (Forno_ListFilter, ('oti_equ_ord_equipamento', admin.RelatedOnlyFieldListFilter),)

    search_fields = ['oti_equ_ord_equipamento__equ_codigo__equ_cad_codigo', 'oti_equ_ord_equipamento__equ_ordem_codigo', 'oti_equ_ord_equipamento__equ_ordem_descricao', ]

    model = TbOtimizacaoEquipamentosOrdem
    form = TbOtimizacaoEquipamentosOrdemFormAdmin

    # Vamos criar um campo para mostrar a qtde de periodos que está running na tabela filha
    def periodos_ativa(self, obj):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.oti_equ_ord_equipamento_id) + " and dau_valor_3 = true"
        cursor.execute(sql)
        retorno_ativa = cursor.fetchone()[0]

        sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(obj.oti_equ_ord_equipamento_id)
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
            color, retorno, )

        return retorno

    # Vamos ver o tipo de periodo
    # 🌟 CORRIGIDO: protege contra a tabela/coluna ainda não existir
    # (acontece durante makemigrations de uma migration ainda não aplicada).
    try:
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            periodos_ativa.short_description = 'Anos Ativa'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            periodos_ativa.short_description = 'Trimestres Ativa'
        else:
            periodos_ativa.short_description = 'Meses Ativa'
    except Exception:
        periodos_ativa.short_description = 'Período Ativa'

    actions = ['exportar_excel']

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização Equipamentos Ordem Produção.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbOtimizacaoEquipamentosOrdemDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0,
                 'RESULTADOS OTIMIZAÇÃO: EQUIPAMENTOS - ORDEM DE PRODUÇÃO / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome,
                 font_style)

        columns = [tipo_periodo, 'Equiamento Código', 'Equipamento Descrição', 'Tipo Produção', 'Equipamento/Planta/Ordem de Produção', 'Produção(volume)', 'Produção(h)', 'WIP(volume)', 'WIP(valor)']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes e valores de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[5]

            # Vamos pegar os nomes
            # Código do Equipamento
            equipamento_id = TbOtimizacaoEquipamentosOrdem.objects.get(id=id_mae).oti_equ_ord_equipamento_id
            equipamento_cadastro_id = TbEquipamentos.objects.get(id=equipamento_id).equ_codigo_id
            codigo_equipamento = TbEquipamentosCadastro.objects.get(id=equipamento_cadastro_id).equ_cad_codigo

            # Descrição do Equipamento
            descricao_equipamento = TbEquipamentosCadastro.objects.get(id=equipamento_cadastro_id).equ_cad_descricao

            # Tipo de produto/produção
            tipo_producao_id = TbEquipamentos.objects.get(id=equipamento_id).equ_tipo_producao_id
            tipo_producao = TbTipoProducao.objects.get(id=tipo_producao_id).tip_nome

            # Equipamento/Planta/Ordem
            nome_equipamento_planta_ordem_producao = str(TbOtimizacaoEquipamentosOrdem.objects.get(id=id_mae).oti_equ_ord_equipamento)

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, codigo_equipamento)
            list_aux.insert(2, descricao_equipamento)
            list_aux.insert(3, tipo_producao)
            list_aux.insert(4, nome_equipamento_planta_ordem_producao)

            # Vamos remover o mae_id da lista. Está na posição 8 após as inclusões dos nomes.
            del (list_aux[9])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbOtimizacaoEquipamentosOrdem.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbOtimizacaoEquipamentosOrdemAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoEquipamentosOrdemAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Permitir deleção em cascata ao deletar cenário
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbotimizacaoequipamentosordem' in requerente:
            retorno = False
        else:
            retorno = True

            return retorno
    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False

    inlines = [TbOtimizacaoEquipamentosOrdemDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoEquipamentosOrdem, TbOtimizacaoEquipamentosOrdemAdmin)

class TbProdutoMercadoDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'margem_cont_bruta_media', 'dau_valor_7')
    readonly_fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'margem_cont_bruta_media', 'dau_valor_7')

    model = TbProdutoMercadoDaugther
    form = TbProdutoMercadoDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    #Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbProdutoMercadoAdmin(admin.ModelAdmin):
    fields = (('pro_mer_produto', 'produto_imagem_tag_small', 'pro_mer_mercado'),)
    list_display = ('pro_mer_produto', 'produto_imagem_tag_small', 'pro_mer_mercado', 'vendas_minimo_periodo', 'vendas_maximo_periodo', 'vendas_periodo', 'preco', 'custo', 'margem_contribuicao', 'margem_contribuicao_percentual', 'margem_horaria')
    list_filter = (('pro_mer_produto', admin.RelatedOnlyFieldListFilter), ('pro_mer_mercado', admin.RelatedOnlyFieldListFilter))
    search_fields = ['pro_mer_produto__pro_codigo', 'pro_mer_mercado__mer_nome',]

    model = TbProdutoMercado
    form = TbProdutoMercadoFormAdmin

    actions = ['exportar_excel', 'exportar_pdf']

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
        nome_arquivo = 'Otimização - Produtos - Mercados - PDF'

        largura_pagina = 210 * mm  # Padrão A4
        altura_pagina = 297 * mm  # Padrão A4
        titulo_relatorio = 'Resultados Otimização - Produtos / Mercados'

        p.setPageSize((largura_pagina, altura_pagina))  # Dimensões padrão A4

        # Vamos pegar o logo da empresa e colocar no cabeçalho do relatório
        logo_empresa = TbEmpresa.objects.get(id=1).emp_logo
        logo_empresa = mark_safe('%s' % logo_empresa.url)  # Retorna a url do logo da empresa no AWS S3 ou do computador local no caso de desenvolvimento. Descobri tentando. Não achei na internet.

        # Vamos desenhar um produto/mercado por página
        # Vamos montar uma queryset com o id dos registros selecionados
        lista_id = queryset.values_list('id','pro_mer_produto_id', 'pro_mer_mercado_id')

        for produto_mercado_id in lista_id:

            p.roundRect(5, 5, largura_pagina - 10, altura_pagina - 10, 10)  # Retângulo com bordas arrendondadas

            # Cabeçalho do PDF
            # Setando tamanho do título e escrevendo os títulos no topo da página
            p.setFont("Helvetica-Bold", 18)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 30, titulo_relatorio)

            p.setFont("Helvetica-Bold", 10)
            p.drawCentredString(largura_pagina / 2, altura_pagina - 45, 'Cenário : ' + cen_ativo + ' / ' + cen_ativo_nome)

            # Vamos colocar o logo da empresa e passar uma linha embaixo
            p.drawImage(logo_empresa, 10, altura_pagina - 50, width=50, height=40, mask=None)
            p.line(5, altura_pagina - 55, largura_pagina - 5, altura_pagina - 55)

            # Vamos colocar as informações do registro da tabela
            line_number = altura_pagina - 68
            p.setFont("Helvetica-Bold", 10)

            # Temos o id do produto/mercado. Vamos pegar as outras informações
            produto_codigo = TbProdutos.objects.get(id=produto_mercado_id[1]).pro_codigo
            produto_descricao = TbProdutos.objects.get(id=produto_mercado_id[1]).pro_descricao
            produto_unidade = TbProdutos.objects.get(id=produto_mercado_id[1]).pro_unidade_producao
            produto_imagem = TbProdutos.objects.get(id=produto_mercado_id[1]).pro_imagem
            produto_imagem = mark_safe('%s' % produto_imagem.url)

            mercado_nome = TbMercado.objects.get(id=produto_mercado_id[2]).mer_nome

            p.drawCentredString(largura_pagina / 2, line_number, "PRODUTO / MERCADO")
            line_number = line_number - 20

            p.drawString(10, line_number, 'Código: ' + produto_codigo)
            p.drawString(0.30 * largura_pagina, line_number, 'Descrição: ' + produto_descricao)
            p.drawString(0.80 * largura_pagina, line_number, 'Unidade: ' + produto_unidade)
            p.drawImage(produto_imagem, 0.93 * largura_pagina, line_number - 12.5, width=30, height=30, mask=None)

            line_number = line_number - 20
            p.drawCentredString(largura_pagina / 2, line_number, 'Mercado: ' + mercado_nome)

            line_number = line_number - 30
            p.drawString(0.15 * largura_pagina, line_number, tipo_periodo)
            p.drawString(0.30 * largura_pagina, line_number, 'Volume Mínimo')
            p.drawString(0.50 * largura_pagina, line_number, 'Volume Máximo')
            p.drawString(0.75 * largura_pagina, line_number, 'Vendas')

            # Vamos criar a filha
            filha = TbProdutoMercadoDaugther.objects.filter(mae_id=produto_mercado_id[0]).order_by('dau_order')
            rows = filha.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3').order_by('dau_order')

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

                p.drawString(0.15 * largura_pagina, line_number, periodo_str)
                p.drawString(0.30 * largura_pagina, line_number, locale.format_string('%.0f', row[1], True))
                p.drawString(0.50 * largura_pagina, line_number, locale.format_string('%.0f', row[2], True))
                p.drawString(0.75 * largura_pagina, line_number, locale.format_string('%.0f', row[3], True))

                data.append(int(row[3]))
                rotulo_x.append(periodo_str)


            # Gráfico de barras
            drawing = Drawing(largura_pagina - 30, 200)

            # coordinates (from left bottom)
            x = 15
            y = 25

            bar = VerticalBarChart()
            bar.x = x + 30
            bar.y = y + 5
            bar.width = largura_pagina - 80
            bar.height = 150
            bar.valueAxis.valueMin = 0
            data = [data,]

            bar.data = data
            bar.categoryAxis.categoryNames = rotulo_x
            bar.bars[0].fillColor = colors.green
            #bar.bars[1].fillColor = PCMYKColor(23, 51, 0, 4, alpha=85)
            #bar.bars.fillColor = PCMYKColor(100, 0, 90, 50, alpha=85)
            drawing.add(bar, '')
            renderPDF.draw(drawing, p, x, y, showBoundary=True)

            p.setFont("Helvetica-Bold", 14)
            p.drawCentredString(largura_pagina / 2, 230, "Vendas (" + produto_unidade + ")")

            p.setFont("Helvetica-Oblique", 8)
            p.drawCentredString(largura_pagina / 2, 10, "Copyright © 2022 SPS Consultoria. Todos os direitos reservados.")

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

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização - Produtos - Mercados.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbProdutoMercadoDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0, 'RESULTADOS OTIMIZAÇÃO: PRODUTO-MERCADO / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = [tipo_periodo, 'Produto', 'Mercado', 'Vol. Mínimo', 'Vol. Máximo', 'Vendas', 'Preço', 'Custo Variável', 'Margem Contribuição', 'Margem (%)', 'Margem Horária']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[7]

            # Vamos pegar os id e nomes
            id_produto = TbProdutoMercado.objects.get(id=id_mae).pro_mer_produto_id
            nome_produto = TbProdutos.objects.get(id=id_produto).pro_codigo

            id_mercado = TbProdutoMercado.objects.get(id=id_mae).pro_mer_mercado_id
            nome_mercado = TbMercado.objects.get(id=id_mercado).mer_nome

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, nome_produto)
            list_aux.insert(2, nome_mercado)

            # Inserindo margem de contribuição
            list_aux.insert(8, row[4] - row[5])

            # Inserindo margem de contribuição (%)
            if row[4] != 0:
                list_aux.insert(9, round((row[4] - row[5]) * 100 / row[4], 2))
            else:
                list_aux.insert(9, 0)


            # Vamos remover o mae_id da lista. Está na posição 11 após as inclusões dos nomes.
            del (list_aux[11])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbProdutoMercado.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbProdutoMercadoAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbProdutoMercadoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Permitir deleção em cascara ao deletar cenário
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbprodutomercado' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False

    inlines = [TbProdutoMercadoDaugtherAdmin, ]

# Registrando
admin.site.register(TbProdutoMercado, TbProdutoMercadoAdmin)

class TbOtimizacaoCustoItemDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'dau_valor_4')
    readonly_fields = ('display_order', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6')

    model = TbOtimizacaoCustoItemDaugther
    form = TbOtimizacaoCustoItemDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    '''
    # Removemos pois a permissão ficará a nível de grupo de usuários
    # Permite edição
    def has_change_permission(self, request, obj=None):
        return True
    '''

class TbOtimizacaoCustoItemAdmin(admin.ModelAdmin):
    fields = (('oti_cus_ite_item', 'unidade_item', 'custo_item_imagem_tag_small'),)
    list_display = ('oti_cus_ite_item', 'unidade_item', 'custo_item_imagem_tag_small')
    readonly_fields = ('oti_cus_ite_item', 'unidade_item', 'custo_item_imagem_tag_small',)
    list_filter = (('oti_cus_ite_item', admin.RelatedOnlyFieldListFilter),('oti_cus_ite_item__cus_ite_pre_item', admin.RelatedOnlyFieldListFilter), ('oti_cus_ite_item__cus_ite_pre_unidade_producao', admin.RelatedOnlyFieldListFilter),)
    search_fields = ['oti_cus_ite_item__cus_ite_pre_item__cus_ite_nome', 'oti_cus_ite_item__cus_ite_pre_unidade_producao__uni_nome']

    model = TbOtimizacaoCustoItem
    form = TbOtimizacaoCustoItemFormAdmin

    actions = ['exportar_excel']

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados.
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização Itens Custo Planta.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbOtimizacaoCustoItemDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0,
                 'RESULTADOS OTIMIZAÇÃO: ITENS DE CUSTO - PLANTA DE PRODUÇÃO / CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome,
                 font_style)

        columns = [tipo_periodo, 'Item de Custo', 'Unidade', 'Planta de Produção', 'Consumo Mínimo', 'Consumo Máximo', 'Consumo Calculado', 'Estoque (Qtde)', 'Estoque (Valor)', 'Ativo']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_5', 'dau_valor_6', 'dau_valor_4', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[7]

            # Vamos pegar os nomes
            # Pegando id do item de custo.
            id_custo_item_planta = TbOtimizacaoCustoItem.objects.get(id=id_mae).oti_cus_ite_item_id
            id_custo_item = TbCustoItemPreco.objects.get(id=id_custo_item_planta).cus_ite_pre_item_id
            nome_custo_item = TbCustoItem.objects.get(id=id_custo_item).cus_ite_nome
            unidade_custo_item = TbCustoItem.objects.get(id=id_custo_item).cus_ite_unidade
            id_planta = TbCustoItemPreco.objects.get(id=id_custo_item_planta).cus_ite_pre_unidade_producao_id
            nome_planta = TbUnidadeProducao.objects.get(id=id_planta).uni_nome

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, nome_custo_item)
            list_aux.insert(2, unidade_custo_item)
            list_aux.insert(3, nome_planta)

            # Vamos remover o mae_id da lista. Está na posição 10 após as inclusões dos nomes.
            del (list_aux[10])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel e exportar_pdf sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and (request.POST['action'] == 'exportar_excel' or request.POST['action'] == 'exportar_pdf'):
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbOtimizacaoCustoItem.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbOtimizacaoCustoItemAdmin, self).changelist_view(request, extra_context)

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoCustoItemAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbotimizacaocustoitem' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    def has_add_permission(self, request, obj=None):
        return False

    inlines = [TbOtimizacaoCustoItemDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoCustoItem, TbOtimizacaoCustoItemAdmin)

class TbConsumoEspecificoTipoProducaoAdmin(admin.ModelAdmin):
    fields            = (('con_esp_tip_pro_descricao', 'con_esp_tip_pro_indicador_referencia'), ('con_esp_tip_pro_qtde_produzida', 'con_esp_tip_pro_qtde_consumo', 'con_esp_tip_pro_indicador', 'valor_indicador_referencia'), 'con_esp_tip_pro_tipo_producao', ('con_esp_tip_pro_consumo_via_equipamento', 'con_esp_tip_pro_ajuste1', 'valor_indicador1', 'con_esp_tip_pro_operacao1'), ('con_esp_tip_pro_consumo_via_item_consumo', 'con_esp_tip_pro_ajuste2', 'valor_indicador2', 'con_esp_tip_pro_operacao2'), 'con_esp_tip_pro_observacao')
    list_display      = ('con_esp_tip_pro_descricao', 'con_esp_tip_pro_qtde_produzida', 'con_esp_tip_pro_qtde_consumo', 'con_esp_tip_pro_indicador', 'valor_indicador_referencia')
    readonly_fields   = ('con_esp_tip_pro_qtde_produzida', 'con_esp_tip_pro_qtde_consumo', 'con_esp_tip_pro_indicador', 'valor_indicador1', 'valor_indicador2', 'valor_indicador_referencia')
    filter_horizontal = ('con_esp_tip_pro_tipo_producao', 'con_esp_tip_pro_consumo_via_equipamento', 'con_esp_tip_pro_consumo_via_item_consumo')
    #list_filter = (('oti_cus_ite_item', admin.RelatedOnlyFieldListFilter),('oti_cus_ite_item__cus_ite_pre_item', admin.RelatedOnlyFieldListFilter), ('oti_cus_ite_item__cus_ite_pre_unidade_producao', admin.RelatedOnlyFieldListFilter),)
    search_fields = ['con_esp_tip_pro_descricao']

    formfield_overrides = {models.TextField: {'widget': Textarea(attrs={'rows': 3, 'cols': 105})}, }

    model = TbConsumoEspecificoTipoProducao
    form = TbConsumoEspecificoTipoProducaoFormAdmin

    def valor_indicador1(self, object):
        if object.con_esp_tip_pro_ajuste1_id is not None:
            return TbConsumoEspecifico.objects.get(id=object.con_esp_tip_pro_ajuste1_id).con_esp_indicador
        else:
            return ''
    valor_indicador1.short_description = 'Valor Indicador'

    def valor_indicador_referencia(self, object):
        if object.con_esp_tip_pro_indicador_referencia_id is not None:
            return TbConsumoEspecifico.objects.get(id=object.con_esp_tip_pro_indicador_referencia_id).con_esp_indicador
        else:
            return ''
    valor_indicador_referencia.short_description = 'Referência'

    def valor_indicador2(self, object):
        if object.con_esp_tip_pro_ajuste2_id is not None:
            return TbConsumoEspecifico.objects.get(id=object.con_esp_tip_pro_ajuste2_id).con_esp_indicador
        else:
            return ''
    valor_indicador2.short_description = 'Valor Indicador'

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbConsumoEspecificoTipoProducaoAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

# Registrando
admin.site.register(TbConsumoEspecificoTipoProducao, TbConsumoEspecificoTipoProducaoAdmin)

class TbOtimizacaoComparacaoCenariosDaugtherAdmin(admin.TabularInline):
    fields          = ('display_order', 'ofcf_referencia', 'ofcf_ativo', 'delta_ofcf', 'wacc', 'delta_ofcf_descontado', 'vendas_referencia', 'vendas_ativo', 'delta_vendas', 'variavel_referencia', 'variavel_ativo', 'delta_variavel', 'inbound_referencia', 'inbound_ativo', 'delta_inbound', 'outbound_referencia', 'outbound_ativo', 'delta_outbound', 'manutencao_referencia', 'manutencao_ativo', 'delta_manutencao', 'margem_contribuicao_referencia', 'margem_contribuicao_ativo', 'delta_margem_contribuicao', 'custo_fixo_referencia', 'custo_fixo_ativo', 'delta_custo_fixo', 'EBITDA_referencia', 'EBITDA_ativo', 'delta_EBITDA', 'EBITDA_PERC_referencia', 'EBITDA_PERC_ativo', 'delta_EBITDA_PERC', 'da_referencia', 'da_ativo', 'delta_da', 'IR_PERC_referencia', 'IR_PERC_ativo', 'delta_IR_PERC', 'mp_referencia', 'mp_ativo', 'delta_mp', 'wip_referencia', 'wip_ativo', 'delta_wip', 'pf_referencia', 'pf_ativo', 'delta_pf', 'estoque_referencia', 'estoque_ativo', 'delta_estoque', 'receber_referencia', 'receber_ativo', 'delta_receber', 'pagar_referencia', 'pagar_ativo', 'delta_pagar', 'owcr_referencia', 'owcr_ativo', 'delta_owcr', 'capex_referencia', 'capex_ativo', 'delta_capex')
    readonly_fields = ('display_order', 'ofcf_referencia', 'ofcf_ativo', 'delta_ofcf', 'wacc', 'delta_ofcf_descontado', 'vendas_referencia', 'vendas_ativo', 'delta_vendas', 'variavel_referencia', 'variavel_ativo', 'delta_variavel', 'inbound_referencia', 'inbound_ativo', 'delta_inbound', 'outbound_referencia', 'outbound_ativo', 'delta_outbound', 'manutencao_referencia', 'manutencao_ativo', 'delta_manutencao', 'margem_contribuicao_referencia', 'margem_contribuicao_ativo', 'delta_margem_contribuicao', 'custo_fixo_referencia', 'custo_fixo_ativo', 'delta_custo_fixo', 'EBITDA_referencia', 'EBITDA_ativo', 'delta_EBITDA', 'EBITDA_PERC_referencia', 'EBITDA_PERC_ativo', 'delta_EBITDA_PERC', 'da_referencia', 'da_ativo', 'delta_da', 'IR_PERC_referencia', 'IR_PERC_ativo', 'delta_IR_PERC', 'mp_referencia', 'mp_ativo', 'delta_mp', 'wip_referencia', 'wip_ativo', 'delta_wip', 'pf_referencia', 'pf_ativo', 'delta_pf', 'estoque_referencia', 'estoque_ativo', 'delta_estoque', 'receber_referencia', 'receber_ativo', 'delta_receber', 'pagar_referencia', 'pagar_ativo', 'delta_pagar', 'owcr_referencia', 'owcr_ativo', 'delta_owcr', 'capex_referencia', 'capex_ativo', 'delta_capex')

    model = TbOtimizacaoComparacaoCenariosDaugther
    form = TbOtimizacaoComparacaoCenariosDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    #Não permite edição
    def has_change_permission(self, request, obj=None):
        return False

class TbOtimizacaoComparacaoCenariosAdmin(admin.ModelAdmin):
    fields = ('oti_com_cenario_referencia', ('vpl', 'payback_descontado', 'tir'))
    list_display = ('oti_com_cenario_referencia', 'vpl', 'payback_descontado', 'tir')
    readonly_fields = ('vpl', 'payback_descontado', 'tir')

    model = TbOtimizacaoComparacaoCenarios
    form = TbOtimizacaoComparacaoCenariosFormAdmin

    # Mostra somente os registros do cenário ativo
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoComparacaoCenariosAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    actions = ['exportar_excel']

    def exportar_excel(self, request, queryset):
        # Só exporta os registros selecionados. Se nenhum for selecionado, exporta todos
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="Otimização Comparação Cenários.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores das filhas
        ws = wb.add_sheet('resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id',) # Temos aqui o id da mãe

        filhas = TbOtimizacaoComparacaoCenariosDaugther.objects.filter(mae_id__in=lista_id).order_by('id')
        # Só vem o id, dau_order, mae_id e tbcenarios_id (nessa filha só tem esses campos)

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

        # Vamos pegar o cenário ativo (id e nome)
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_ativo_nome = TbCenarios.objects.get(cen_ativo=True).cen_nome

        ws.write(row_num, 0, 'RESULTADOS OTIMIZAÇÃO: COMPARAÇÕES DO CENÁRIO ' + str(cen_ativo) + ' / ' + cen_ativo_nome, font_style)

        columns = [tipo_periodo, 'ID CENÁRIO REFERÊNCIA', 'NOME CENÁRIO REFERÊNCIA', 'VPL', 'PAYBACK DESCONTADO', 'TIR(%)', 'OFCF REFERÊNCIA', 'OFCF ATIVO', 'DELTA OFCF', 'WACC (%)', 'DELTA OFCF DESCONTADO', 'VENDAS REFERÊNCIA', 'VENDAS ATIVO', 'DELTA VENDAS', 'VARIÁVEL REFERÊNCIA', 'VARIÁVEL ATIVO', 'DELTA VARIÁVEL', 'INBOUND REFERÊNCIA', 'INBOUND ATIVO', 'DELTA INBOUND', 'OUTBOUND REFERÊNCIA', 'OUTBOUND ATIVO', 'DELTA OUTBOUND', 'MANUTENÇÃO REFERÊNCIA', 'MANUTENÇÃO ATIVO', 'DELTA MANUTENÇÃO', 'MARGEM CONT. REFERÊNCIA','MARGEM CONT. ATIVO', 'DELTA MARGEM CONT.', 'CUSTO FIXO REFERÊNCIA', 'CUSTO FIXO ATIVO', 'DELTA CUSTO FIXO', 'EBITDA REFERÊNCIA', 'EBITDA ATIVO', 'DELTA EBITDA', 'EBITDA (%) REFERÊNCIA', 'EBITDA (%) ATIVO', 'DELTA EBITDA (%)', 'D&A REFERÊNCIA', 'D&A ATIVO', 'DELTA D&A', 'IR (%) REFERÊNCIA', 'IR (%) ATIVO', 'DELTA IR (%)', 'MP REFERÊNCIA', 'MP ATIVO', 'DELTA MP', 'WIP REFERÊNCIA', 'WIP ATIVO', 'DELTA WIP', 'PF REFERÊNCIA', 'PF ATIVO', 'DELTA PF', 'ESTOQUE REFERÊNCIA', 'ESTOQUE ATIVO', 'DELTA ESTOQUE', 'RECEBER REFERÊNCIA', 'RECEBER ATIVO', 'DELTA RECEBER', 'PAGAR REFERÊNCIA', 'PAGAR ATIVO', 'DELTA PAGAR', 'OWCR REFERÊNCIA', 'OWCR ATIVO', 'DELTA OWCR', 'CAPEX REFERÊNCIA', 'CAPEX ATIVO', 'DELTA CAPEX']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'mae_id').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes e valores de algumas colunas.
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[1]

            # Vamos pegar o cenário referência (id e nome)
            cen_referencia = TbOtimizacaoComparacaoCenarios.objects.get(id=id_mae).oti_com_cenario_referencia_id
            cen_referencia_nome = TbCenarios.objects.get(id=cen_referencia).cen_nome

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, cen_referencia)
            list_aux.insert(2, cen_referencia_nome)

            # Vamos obter o total de períodos para o cenário ativo
            cursor = connection.cursor()
            sql = "select conta_periodos(" + str(cen_ativo) + ")"
            cursor.execute(sql)
            total_periodos = cursor.fetchone()[0]
            cursor.close()

            # Calculando e inserindo o VPL
            vpl_acumulado = 0
            for j in range(total_periodos):
                retorno_a = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=j + 1).dau_valor_20

                retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=j + 1).dau_valor_20

                retorno = retorno_b - retorno_a
                wacc_acumulado = 1
                # Temos que calcular o WACC acumulado até j
                for i in range(j + 1):
                    wacc_acumulado = wacc_acumulado * (
                                1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo, dau_order=i + 1).dau_valor / 100)

                retorno = retorno / wacc_acumulado
                vpl_acumulado = vpl_acumulado + retorno

            list_aux.insert(3, vpl_acumulado)

            # Calculando e inserindo o Payback Descontado
            vpl_acumulado = 0
            pay_back = 0
            for j in range(total_periodos):
                retorno_a = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=j + 1).dau_valor_20

                retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=j + 1).dau_valor_20

                retorno = retorno_b - retorno_a
                wacc_acumulado = 1
                # Temos que calcular o WACC acumulado até j
                for i in range(j + 1):
                    wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo, dau_order=i + 1).dau_valor / 100)

                retorno = retorno / wacc_acumulado
                vpl_acumulado = vpl_acumulado + retorno
                if vpl_acumulado < 0:
                    pay_back = pay_back + 1
                else:  # Ficou positivo. Vamos calcular a parte fracionária
                    if (abs(vpl_acumulado) + retorno) != 0:
                        pay_back = pay_back + abs(vpl_acumulado) / (abs(vpl_acumulado) + retorno)
                    else:
                        pay_back = 0
                    break

            if pay_back == total_periodos:  # Significa que chegou no final e não ficou positivo
                pay_back_descontado = 999
            else:
                pay_back_descontado = pay_back

            list_aux.insert(4, pay_back_descontado)

            # Calculando e inserindo a TIR(%)
            lista_tir = []
            for j in range(total_periodos):
                retorno_a = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=j + 1).dau_valor_20

                retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=j + 1).dau_valor_20

                retorno = retorno_b - retorno_a
                wacc_acumulado = 1
                # Temos que calcular o WACC acumulado até j
                for i in range(j + 1):
                    wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo, dau_order=i + 1).dau_valor / 100)

                lista_tir.append(retorno / wacc_acumulado)

            tir = npf.irr(lista_tir) * 100

            list_aux.insert(5, tir)

            ofcf_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_20
            ofcf_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_20

            list_aux.insert(6, ofcf_referencia)
            list_aux.insert(7, ofcf_ativo)
            list_aux.insert(8, ofcf_ativo-ofcf_referencia)

            wacc = TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo, dau_order=row[0]).dau_valor
            list_aux.insert(9, wacc)

            # Calculando OFCF Descontado
            retorno_a = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_20
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_20

            retorno = retorno_b - retorno_a
            wacc_acumulado = 1
            # Temos que calcular o WACC acumulado até o dau_order
            for i in range(row[0]):
                wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo, dau_order=i + 1).dau_valor / 100)

            ofcf_descontado = retorno / wacc_acumulado
            list_aux.insert(10, ofcf_descontado)

            vendas_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_1
            vendas_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_1

            list_aux.insert(11, vendas_referencia)
            list_aux.insert(12, vendas_ativo)
            list_aux.insert(13, vendas_ativo - vendas_referencia)

            variavel_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_2
            variavel_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_2

            list_aux.insert(14, variavel_referencia)
            list_aux.insert(15, variavel_ativo)
            list_aux.insert(16, variavel_ativo - variavel_referencia)

            inbound_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_3
            inbound_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_3

            list_aux.insert(17, inbound_referencia)
            list_aux.insert(18, inbound_ativo)
            list_aux.insert(19, inbound_ativo - inbound_referencia)

            outbound_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_4
            outbound_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_4

            list_aux.insert(20, outbound_referencia)
            list_aux.insert(21, outbound_ativo)
            list_aux.insert(22, outbound_ativo - outbound_referencia)

            manutencao_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_5
            manutencao_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_5

            list_aux.insert(23, manutencao_referencia)
            list_aux.insert(24, manutencao_ativo)
            list_aux.insert(25, manutencao_ativo - manutencao_referencia)

            margem_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_6
            margem_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_6

            list_aux.insert(26, margem_referencia)
            list_aux.insert(27, margem_ativo)
            list_aux.insert(28, margem_ativo - margem_referencia)

            custo_fixo_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_7
            custo_fixo_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_7

            list_aux.insert(29, custo_fixo_referencia)
            list_aux.insert(30, custo_fixo_ativo)
            list_aux.insert(31, custo_fixo_ativo - custo_fixo_referencia)

            ebitda_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_8
            ebitda_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_8

            list_aux.insert(32, ebitda_referencia)
            list_aux.insert(33, ebitda_ativo)
            list_aux.insert(34, ebitda_ativo - ebitda_referencia)

            ebitda_perc_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_9
            ebitda_perc_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_9

            list_aux.insert(35, ebitda_perc_referencia)
            list_aux.insert(36, ebitda_perc_ativo)
            list_aux.insert(37, ebitda_perc_ativo - ebitda_perc_referencia)

            da_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_10
            da_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_10

            list_aux.insert(38, da_referencia)
            list_aux.insert(39, da_ativo)
            list_aux.insert(40, da_ativo - da_referencia)

            ir_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_11
            ir_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_11

            list_aux.insert(41, ir_referencia)
            list_aux.insert(42, ir_ativo)
            list_aux.insert(43, ir_ativo - ir_referencia)

            mp_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_12
            mp_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_12

            list_aux.insert(44, mp_referencia)
            list_aux.insert(45, mp_ativo)
            list_aux.insert(46, mp_ativo - mp_referencia)

            wip_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_13
            wip_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_13

            list_aux.insert(47, wip_referencia)
            list_aux.insert(48, wip_ativo)
            list_aux.insert(49, wip_ativo - wip_referencia)

            pf_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_14
            pf_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_14

            list_aux.insert(50, pf_referencia)
            list_aux.insert(51, pf_ativo)
            list_aux.insert(52, pf_ativo - pf_referencia)

            estoque_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_15
            estoque_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_15

            list_aux.insert(53, estoque_referencia)
            list_aux.insert(54, estoque_ativo)
            list_aux.insert(55, estoque_ativo - estoque_referencia)

            receber_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_16
            receber_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_16

            list_aux.insert(56, receber_referencia)
            list_aux.insert(57, receber_ativo)
            list_aux.insert(58, receber_ativo - receber_referencia)

            pagar_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_17
            pagar_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_17

            list_aux.insert(59, pagar_referencia)
            list_aux.insert(60, pagar_ativo)
            list_aux.insert(61, pagar_ativo - pagar_referencia)

            owcr_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_18
            owcr_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_18

            list_aux.insert(62, owcr_referencia)
            list_aux.insert(63, owcr_ativo)
            list_aux.insert(64, owcr_ativo - owcr_referencia)

            capex_referencia = TbCenariosDaugther.objects.get(mae_id=cen_referencia, dau_order=row[0]).dau_valor_19
            capex_ativo = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=row[0]).dau_valor_19

            list_aux.insert(65, capex_referencia)
            list_aux.insert(66, capex_ativo)
            list_aux.insert(67, capex_ativo - capex_referencia)

            # Vamos remover o mae_id da lista. Está na posição 68 após as inclusões dos nomes.
            del (list_aux[68])

            # Vamos agora alterar o dau_order
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
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
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel'

    # Para permitir rodar exportar_excel sem selecionar nenhum registro
    def changelist_view(self, request, extra_context=None):
        if 'action' in request.POST and request.POST['action'] == 'exportar_excel':
            if not request.POST.getlist(ACTION_CHECKBOX_NAME):
                post = request.POST.copy()
                for u in TbOtimizacaoCustoItem.objects.all():
                    post.update({ACTION_CHECKBOX_NAME: str(u.id)})
                request._set_post(post)
        return super(TbOtimizacaoComparacaoCenariosAdmin, self).changelist_view(request, extra_context)

    inlines = [TbOtimizacaoComparacaoCenariosDaugtherAdmin, ]

# Registrando
admin.site.register(TbOtimizacaoComparacaoCenarios, TbOtimizacaoComparacaoCenariosAdmin)

'''
class TbOtimizacaoShadowDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4')
    readonly_fields = ('display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4')

    model = TbOtimizacaoShadowDaugther
    form = TbOtimizacaoShadowDaugtherFormAdmin

    # Permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    #Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    #Não permite edição
    def has_change_permission(self, request, obj=None):
        return False
'''

class TbOtimizacaoShadowAdmin(admin.ModelAdmin):
    fields = (('oti_shadow_restricao', 'oti_shadow_tipo', 'display_order'), ('oti_shadow_id_1', 'oti_shadow_id_2'), ('dau_valor_1', 'dau_valor_2'), ('dau_valor_5', 'dau_valor_7', 'dau_valor_6'), ('dau_valor_8', 'dau_valor_9'), ('dau_valor_10', 'dau_valor_11'), 'dau_texto_1', 'dau_texto_2', 'dau_texto_3')
    list_display = ('oti_shadow_restricao', 'oti_shadow_tipo', 'display_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_5', 'dau_valor_7', 'dau_valor_6', 'dau_valor_8', 'dau_valor_9', 'dau_valor_10', 'dau_valor_11', 'dau_texto_1', 'dau_texto_2', 'dau_texto_3')
    list_filter = ('oti_shadow_restricao', 'oti_shadow_tipo', 'dau_order')
    search_fields = ['oti_shadow_restricao', ]

    model = TbOtimizacaoShadow
    form  = TbOtimizacaoShadowFormAdmin

    # Removendo a opção de deletar registros do actions
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    # Mostra somente os registros do cenário ativo e que flag seja igual a 1
    # Faço isso somente na mãe. Não precisa fazer na filha.
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbOtimizacaoShadowAdmin, self).get_queryset(request).filter(tbcenarios=cen_ativo, flag=1)

    # Permite delete
    # Permitir deleção em cascara ao deletar cenário
    def has_delete_permission(self, request, obj=None):
        retorno = True
        requerente = str(request)
        if 'adminotimizacao/tbotimizacaoShadow' in requerente:
            retorno = False
        else:
            retorno = True

        return retorno

    # Naõ permite adição.
    #def has_add_permission(self, request, obj=None):
    #    return False

    # Não permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        return False


# Registrando
admin.site.register(TbOtimizacaoShadow, TbOtimizacaoShadowAdmin)