from django import forms
from django.utils.translation import gettext_lazy as _

from custo_ferbasa.models import TbProducaoMensal, TbGruposMaquinas, TbItensConsumo, TbItensProducao, TbConsumoEspecifico

from tabelas.models import *

class TbItensProducaoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbItensProducaoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['ite_pro_codigo'].widget.attrs['style'] = 'width: 90px;'
            self.fields['ite_pro_descricao'].widget.attrs['style'] = 'width: 460px;'
            self.fields['ite_pro_unidade'].widget.attrs['style'] = 'width: 30px;'
        except:
            pass


class TbEstabelecimentosFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbEstabelecimentosFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['est_codigo'].widget.attrs['style'] = 'width: 30px;'
            self.fields['est_nome'].widget.attrs['style'] = 'width: 520px;'
        except:
            pass


class TbGruposMaquinasFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbGruposMaquinasFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['gru_maq_codigo'].widget.attrs['style'] = 'width: 100px;'
            self.fields['gru_maq_nome'].widget.attrs['style'] = 'width: 450px;'
        except:
            pass


class TbItensConsumoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbItensConsumoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['ite_con_codigo'].widget.attrs['style'] = 'width: 90px;'
            self.fields['ite_con_descricao'].widget.attrs['style'] = 'width: 450px;'
            self.fields['ite_con_unidade'].widget.attrs['style'] = 'width: 30px;'
            self.fields['ite_con_tipo'].widget.attrs['style'] = 'width: 30px;'  # Não funciona. Verificar
        except:
            pass


class TbContaContabilFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbContaContabilFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['con_con_codigo'].widget.attrs['style'] = 'width: 60px;'
            self.fields['con_con_descricao'].widget.attrs['style'] = 'width: 490px;'
        except:
            pass


class TbCentroCustoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbCentroCustoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['cen_cus_codigo'].widget.attrs['style'] = 'width: 60px;'
            self.fields['cen_cus_descricao'].widget.attrs['style'] = 'width: 490px;'
            self.fields['cen_cus_mod'].widget.attrs['style'] = 'width: 40px;'
            self.fields['cen_cus_moi'].widget.attrs['style'] = 'width: 40px;'
        except:
            pass


class TbContaContabilCentroCustoAdminFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbContaContabilCentroCustoAdminFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['con_con_cen_cus_descricao'].widget.attrs['style'] = 'width: 490px;'
        except:
            pass


class TbProducaoMensalFormAdmin(forms.ModelForm):
    # Na tabela mãe tenho que colocar o localize aqui, antes do __init__
    # Depois do __init__ altero o labe. Vde Abaixo
    pro_men_qtde_produzida = forms.DecimalField(localize=True)
    pro_men_qtde_consumo = forms.DecimalField(localize=True)
    pro_men_valor_ultima_entrada = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbProducaoMensalFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form e máscara do ano/mês
        try:
            self.fields['pro_men_ano_mes'].widget.attrs['style'] = 'width: 60px;'
            self.fields['pro_men_ordem_producao'].widget.attrs['style'] = 'width: 70px;'
            self.fields['pro_men_ano_mes'].widget.attrs['class'] = 'mask-cenario-2'
            self.fields['pro_men_qtde_produzida'].label = _('Qtde Produzida')
            self.fields['pro_men_qtde_consumo'].label = _('Qtde Consumo')
            self.fields['pro_men_valor_material'].widget.attrs['style'] = 'width: 80px;'
            self.fields['pro_men_valor_material'].label = _('Valor Material')
            self.fields['pro_men_valor_ggf'].widget.attrs['style'] = 'width: 80px;'
            self.fields['pro_men_valor_ggf'].label = _('Valor GGF')
            self.fields['pro_men_valor_ultima_entrada'].widget.attrs['style'] = 'width: 80px;'
            self.fields['pro_men_valor_ultima_entrada'].label = _('Valor Última Entrada')
        except:
            pass


class TbDistribuicaoGGFMensalFormAdmin(forms.ModelForm):
    # Na tabela mãe tenho que colocar o localize aqui, antes do __init__
    # Depois do __init__ altero o labe. Vde Abaixo
    dis_ggf_men_qtde_produzida = forms.DecimalField(localize=True)
    dis_ggf_men_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbDistribuicaoGGFMensalFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form e máscara do ano/mês
        try:
            self.fields['dis_ggf_men_ano_mes'].widget.attrs['style'] = 'width: 60px;'
            self.fields['dis_ggf_men_ordem_producao'].widget.attrs['style'] = 'width: 70px;'
            self.fields['dis_ggf_men_ano_mes'].widget.attrs['class'] = 'mask-cenario-2'
            self.fields['dis_ggf_men_qtde_produzida'].widget.attrs['class'] = 'mask-moeda'
            self.fields['dis_ggf_men_qtde_produzida'].label = _('Qtde Produzida')
            self.fields['dis_ggf_men_valor'].label = _('Valor')
        except:
            pass


class TbConsumoEspecificoDaugtherFormAdmin(forms.ModelForm):
    qtde_produzida = forms.DecimalField(max_digits=10, decimal_places=2, localize=True)
    qtde_consumo = forms.DecimalField(max_digits=10, decimal_places=2, localize=True)
    indicador = forms.DecimalField(max_digits=12, decimal_places=4, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbConsumoEspecificoDaugtherFormAdmin, self).__init__(*args, **kwargs)


class TbConsumoEspecificoDaugther1FormAdmin(forms.ModelForm):
    class Meta:
        # Alterar a largura do campo. Não pode ser foreigner
        widgets = {'codigo': forms.TextInput(attrs={'size': 5}),
                   }


class TbConsumoEspecificoFormAdmin(forms.ModelForm):

    con_esp_producao_minima = forms.DecimalField(max_digits=10, decimal_places=0, localize=True, label=_('Prod. Minima'))

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None) # Para ter acesso ao user
        super(TbConsumoEspecificoFormAdmin, self).__init__(*args, **kwargs)


        if self.instance.pk is not None:  # Não está incluindo

            # Calculando a produção total dos itens de produção para o periodo informado para mostrar na lista choice do manytomany
            wtf = TbItensProducao.objects.all()

            w = self.fields['con_esp_item_producao'].widget
            choices = []
            for choice in wtf:

                # Vamos calcular a produção do item de produção para o  periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.producao_consumo_especifico_item_producao(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                #if retorno > 0:
                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.2f', retorno, True)
                choices.append((choice.id, choice.ite_pro_descricao + ' / ' + choice.ite_pro_codigo + ' / ' + 'Prod. Periodo=' + retorno))

            w.choices = choices

            produto_qs = self.instance.con_esp_item_producao.values_list('id', flat=True)

            # Vamos selecionar os id dos grupos máquinas que tiveram como produção os itens de produção informados.Isso na tabela de produção mensal
            grupo_maquina_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs)
            lista_grupo_maquina = []
            for grupo_maquina in grupo_maquina_qs:
                if grupo_maquina.pro_men_grupo_maquina_id not in lista_grupo_maquina:
                    lista_grupo_maquina.append(grupo_maquina.pro_men_grupo_maquina_id)

            wtf = TbGruposMaquinas.objects.filter(id__in=lista_grupo_maquina)

            w = self.fields['con_esp_grupo_maquina'].widget
            choices = []
            for choice in wtf:
                # Vamos calcular a produção do grupo máquina para os produtos e periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.producao_consumo_especifico_grupo_maquina(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.2f', retorno, True)

                choices.append((choice.id, choice.gru_maq_nome + ' / ' + choice.gru_maq_codigo + ' / ' + 'Produção=' + retorno))
            w.choices = choices

            grupo_maquina_qs = self.instance.con_esp_grupo_maquina.values_list('id', flat=True)

            '''
            # Vamos selecionar os id dos itens de consumo para os produtos e grupos máquinas informados.Isso na tabela de produção mensal
            periodo_inicio = TbConsumoEspecifico.objects.get(id=self.instance.pk).con_esp_ano_mes_inicio
            periodo_fim = TbConsumoEspecifico.objects.get(id=self.instance.pk).con_esp_ano_mes_fim
            item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs, pro_men_grupo_maquina_id__in=grupo_maquina_qs, pro_men_ano_mes__gte=periodo_inicio, pro_men_ano_mes__lte=periodo_fim).values_list('pro_men_item_consumo_id', flat=True).order_by('pro_men_item_consumo_id').distinct()
            '''

            # Vamos selecionar os id dos itens de consumo para os produtos e grupos máquinas informados.Isso na tabela de produção mensal
            item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs, pro_men_grupo_maquina_id__in=grupo_maquina_qs).values_list('pro_men_item_consumo_id', flat=True).order_by('pro_men_item_consumo_id').distinct()

            wtf = TbItensConsumo.objects.filter(id__in=item_consumo_qs)

            w = self.fields['con_esp_item_consumo'].widget
            choices = []
            for choice in wtf:
                # Vamos calcular o indicador para o item de consumo
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.indicador_consumo_especifico_item_consumo(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retornostr = locale.format_string('%.4f', retorno, True)
                if retorno >= 0:
                    choices.append((choice.id, choice.ite_con_descricao + ' / ' + choice.ite_con_codigo + ' / ' + 'Indicador=' + retornostr))
                else:
                    # Vamos ver se é subproduto ou desvio
                    subproduto = TbItensConsumo.objects.get(id=choice.id).ite_subproduto
                    if subproduto:
                        choices.append((choice.id, '(-) SUBPRODUTO / ' + choice.ite_con_descricao + ' / ' + choice.ite_con_codigo + ' / ' + 'Indicador=' + retornostr))
                    else:
                        choices.append((choice.id, '(-) DESVIO / ' + choice.ite_con_descricao + ' / ' + choice.ite_con_codigo + ' / ' + 'Indicador=' + retornostr))

            # Vamos ordenar para aparecer as bonificações no topo
            def takedescricao(elem):
                return elem[1]

            choices.sort(key=takedescricao)
            w.choices = choices

        else:
            pass

        # Alterando a largura, altura e máscara para alguns campos no form
        #try:
        self.fields['con_esp_descricao'].widget.attrs['style'] = 'width: 600px;'
        self.fields['con_esp_ano_mes_inicio'].widget.attrs['style'] = 'width: 60px;'
        self.fields['con_esp_ano_mes_fim'].widget.attrs['style'] = 'width: 60px;'
        self.fields['con_esp_producao_minima'].widget.attrs['style'] = 'width: 60px;'
        if self.instance.pk:
            self.fields['con_esp_equacao'].widget.attrs['style'] = 'width: 775px;'

        # Se o campo é readonly, dá erro. Por isso comentamos
        #self.fields['con_esp_criado_por'].widget.attrs['style'] = 'width: 80px;'
        #self.fields['con_esp_alterado_por'].widget.attrs['style'] = 'width: 80px;'

        self.fields['con_esp_ano_mes_inicio'].widget.attrs['class'] = 'mask-cenario-2'
        self.fields['con_esp_ano_mes_fim'].widget.attrs['class'] = 'mask-cenario-2'

        #except:
        #    pass

class TbCustoVariavelAdicionadoDaugtherFormAdmin(forms.ModelForm):
    qtde_produzida = forms.DecimalField(max_digits=10, decimal_places=2, localize=True)
    custo_variavel_adiconado_material = forms.DecimalField(max_digits=10, decimal_places=2, localize=True)
    custo_variavel_adiconado_ggf = forms.DecimalField(max_digits=10, decimal_places=2, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbCustoVariavelAdicionadoDaugtherFormAdmin, self).__init__(*args, **kwargs)


class TbCustoVariavelAdicionadoDaugther1FormAdmin(forms.ModelForm):
    class Meta:
        # Alterar a largura do campo. Não pode ser foreigner
        widgets = {'codigo': forms.TextInput(attrs={'size': 5}),
                   }


class TbCustoVariavelAdicionadoFormAdmin(forms.ModelForm):

    cus_var_adi_producao_minima = forms.DecimalField(max_digits=10, decimal_places=0, localize=True, label=_('Prod. Minima'))

    def __init__(self, *args, **kwargs):
        super(TbCustoVariavelAdicionadoFormAdmin, self).__init__(*args, **kwargs)

        if self.instance.pk is not None:

            # Calculando a produção total dos itens de produção para o periodo informado para mostrar na lista choice do manytomany
            wtf = TbItensProducao.objects.all()

            w = self.fields['cus_var_adi_item_producao'].widget
            choices = []
            for choice in wtf:
                # Vamos calcular a produção do item de produção para o  periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                # O período início e fim é pego pelo stored procedure a partir do id do custo variável salvo (self.instance.pk)
                sql = "call public.producao_custo_variavel_adicionado_item_producao(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.2f', retorno, True)

                choices.append((choice.id, choice.ite_pro_descricao + ' / ' + choice.ite_pro_codigo + ' / ' + 'Produção Periodo=' + retorno))
            w.choices = choices

            produto_qs = self.instance.cus_var_adi_item_producao.values_list('id', flat=True)

            # Vamos selecionar os id dos grupos máquinas que tiveram como produção os itens de produção informados.Isso na tabela de produção mensal
            grupo_maquina_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs)
            lista_grupo_maquina = []
            for grupo_maquina in grupo_maquina_qs:
                if grupo_maquina.pro_men_grupo_maquina_id not in lista_grupo_maquina:
                    lista_grupo_maquina.append(grupo_maquina.pro_men_grupo_maquina_id)

            wtf = TbGruposMaquinas.objects.filter(id__in=lista_grupo_maquina)

            w = self.fields['cus_var_adi_grupo_maquina'].widget
            choices = []
            for choice in wtf:
                # Vamos calcular a produção do grupo máquina para os produtos e periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.producao_custo_variavel_adicionado_grupo_maquina(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.2f', retorno, True)

                choices.append((choice.id, choice.gru_maq_nome + ' / ' + choice.gru_maq_codigo + ' / ' + 'Produção=' + retorno))
            w.choices = choices

            grupo_maquina_qs = self.instance.cus_var_adi_grupo_maquina.values_list('id', flat=True)

            '''
            # Vamos selecionar os id dos itens de consumo para os produtos e grupos máquinas informados. Isso na tabela de produção mensal
            periodo_inicio = TbCustoVariavelAdicionado.objects.get(id=self.instance.pk).cus_var_adi_ano_mes_inicio
            periodo_fim = TbCustoVariavelAdicionado.objects.get(id=self.instance.pk).cus_var_adi_ano_mes_fim
            item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs, pro_men_grupo_maquina_id__in=grupo_maquina_qs, pro_men_ano_mes__gte=periodo_inicio, pro_men_ano_mes__lte=periodo_fim).values_list('pro_men_item_consumo_id', flat=True).order_by('pro_men_item_consumo_id').distinct()
            '''
            item_consumo_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs, pro_men_grupo_maquina_id__in=grupo_maquina_qs).values_list('pro_men_item_consumo_id', flat=True).order_by('pro_men_item_consumo_id').distinct()

            wtf = TbItensConsumo.objects.filter(id__in=item_consumo_qs)

            w = self.fields['cus_var_adi_item_consumo'].widget
            choices = []
            for choice in wtf:
                # Vamos calcular o indicador para o item de consumo
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.indicador_custo_variavel_adicionado_item_consumo(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]


                # Vamos calcular o custo variável para o item de consumo
                # Expressão SQL
                sql = "call public.custo_variavel_variavel_adicionado_item_consumo(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno1 = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                retornostr = locale.format_string('%.4f', retorno, True)
                retornostr1 = locale.format_string('%.2f', retorno1, True)
                if retorno >= 0:
                    choices.append((choice.id, choice.ite_con_descricao + ' / ' + 'Indicador=' + retornostr + ' / ' + 'Custo Variável =' + retornostr1 + ' / ' + choice.ite_con_codigo + ' / ' + choice.ite_con_tipo))
                else:
                    # Vamos ver se é subproduto ou desvio
                    subproduto = TbItensConsumo.objects.get(id=choice.id).ite_subproduto
                    if subproduto:
                        choices.append((choice.id,
                                        '(-) SUBPRODUTO / ' + choice.ite_con_descricao + ' / ' + choice.ite_con_codigo + ' / ' + 'Indicador=' + retornostr))
                    else:
                        choices.append((choice.id,
                                        '(-) DESVIO / ' + choice.ite_con_descricao + ' / ' + choice.ite_con_codigo + ' / ' + 'Indicador=' + retornostr))

            # Vamos ordenar para aparecer as bonificações no topo
            def takedescricao(elem):
                return elem[1]

            choices.sort(key=takedescricao)
            w.choices = choices

        else:
            pass

        # Alterando a largura, altura e máscara para alguns campos no form
        try:
            self.fields['cus_var_adi_descricao'].widget.attrs['style'] = 'width: 600px;'
            self.fields['cus_var_adi_ano_mes_inicio'].widget.attrs['style'] = 'width: 60px;'
            self.fields['cus_var_adi_ano_mes_fim'].widget.attrs['style'] = 'width: 60px;'
            self.fields['cus_var_adi_producao_minima'].widget.attrs['style'] = 'width: 60px;'
            self.fields['cus_var_adi_ano_mes_inicio'].widget.attrs['class'] = 'mask-cenario-2'
            self.fields['cus_var_adi_ano_mes_fim'].widget.attrs['class'] = 'mask-cenario-2'

        except:
            pass


class TbRegressaoLinearMultiplaDaugtherFormAdmin(forms.ModelForm):
    indicador = forms.DecimalField(max_digits=12, decimal_places=4, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbRegressaoLinearMultiplaDaugtherFormAdmin, self).__init__(*args, **kwargs)


class TbRegressaoLinearMultiplaDaugther1FormAdmin(forms.ModelForm):
    indicador = forms.DecimalField(max_digits=12, decimal_places=4, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbRegressaoLinearMultiplaDaugther1FormAdmin, self).__init__(*args, **kwargs)


class TbRegressaoLinearMultiplaFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):

        super(TbRegressaoLinearMultiplaFormAdmin, self).__init__(*args, **kwargs)

        if self.instance.pk is not None:  # Não está incluindo

            # Calculando a produção total dos itens de produção para o periodo informado para mostrar na lista choice do manytomany
            wtf = TbItensProducao.objects.all()

            w = self.fields['reg_lin_mul_item_producao'].widget
            choices = []
            for choice in wtf:

                # Vamos calcular a produção do item de produção para o periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.producao_rlm_item_producao(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                if retorno > 0:
                    # Vamos formatar o valor no padrão brasileiro
                    # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                    retorno = locale.format_string('%.2f', retorno, True)

                    choices.append((choice.id, choice.ite_pro_descricao + ' / ' + choice.ite_pro_codigo + ' / ' + 'Produção Periodo=' + retorno))
            w.choices = choices

            produto_qs = self.instance.reg_lin_mul_item_producao.values_list('id', flat=True)

            # Vamos selecionar os id dos grupos máquinas que tiveram como produção os itens de produção informados.Isso na tabela de produção mensal
            grupo_maquina_qs = TbProducaoMensal.objects.filter(pro_men_item_producao_id__in=produto_qs)
            lista_grupo_maquina = []
            for grupo_maquina in grupo_maquina_qs:
                if grupo_maquina.pro_men_grupo_maquina_id not in lista_grupo_maquina:
                    # Só vamos considerar se o código do grupo máquina for diferente de 'VAZIO'
                    codigo_grupo_maquina = TbGruposMaquinas.objects.get(id=grupo_maquina.pro_men_grupo_maquina_id).gru_maq_codigo
                    if codigo_grupo_maquina != 'VAZIO':
                        lista_grupo_maquina.append(grupo_maquina.pro_men_grupo_maquina_id)

            wtf = TbGruposMaquinas.objects.filter(id__in=lista_grupo_maquina)

            w = self.fields['reg_lin_mul_grupo_maquina'].widget
            choices = []
            for choice in wtf:
                # if choice.gru_maq_codigo != 'VAZIO':
                # Vamos calcular a produção do grupo máquina para os produtos e periodo informado
                cursor = connection.cursor()
                # Expressão SQL
                sql = "call public.producao_rlm_grupo_maquina(" + str(self.instance.pk) + ", " + str(choice.id) + ", 0)"
                cursor.execute(sql)
                retorno = cursor.fetchone()[0]
                cursor.close()

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.2f', retorno, True)

                choices.append((choice.id, choice.gru_maq_nome + ' / ' + choice.gru_maq_codigo + ' / ' + 'Produção=' + retorno))
            w.choices = choices

        else:
            pass

        # Alterando a largura, altura e máscara para alguns campos no form
        try:
            self.fields['reg_lin_mul_descricao'].widget.attrs['style'] = 'width: 775px;'
            self.fields['reg_lin_mul_ano_mes_inicio'].widget.attrs['style'] = 'width: 60px;'
            self.fields['reg_lin_mul_ano_mes_fim'].widget.attrs['style'] = 'width: 60px;'
            self.fields['reg_lin_mul_ano_mes_inicio'].widget.attrs['class'] = 'mask-cenario-2'
            self.fields['reg_lin_mul_ano_mes_fim'].widget.attrs['class'] = 'mask-cenario-2'
        except:
            pass

    # Tiramos pois colocamos o campo como readonly
    # reg_lin_mul_qtde_produzida = forms.DecimalField(localize=True)



class CalcularPeriodoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    ano_mes_inicio = forms.CharField(max_length=7, required=False, label=_('Ano/Mês Início'))
    ano_mes_fim = forms.CharField(max_length=7, required=False, label=_('Ano/Mês Fim'))
    def __init__(self, *args, **kwargs):
        super(CalcularPeriodoForm, self).__init__(*args, **kwargs)
        self.fields['ano_mes_inicio'].widget.attrs['class'] = 'mask-cenario-2'
        self.fields['ano_mes_fim'].widget.attrs['class'] = 'mask-cenario-2'

        self.fields['ano_mes_inicio'].widget.attrs['style'] = 'width: 70px;'
        self.fields['ano_mes_fim'].widget.attrs['style'] = 'width: 70px;'