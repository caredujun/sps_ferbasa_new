from django import forms
from django.utils.translation import gettext_lazy as _
from django.db import connection

from equipamentos.models import TbEquipamentosCadastro
from otimizacao.models import TbProdutoMercadoFluxoDaugther, TbOtimizacaoEquipamentosDaugther, TbProdutoMercadoDaugther, \
    TbOtimizacaoCustoItemDaugther, TbOtimizacaoEquipamentosOrdemDaugther, TbOtimizacaoComparacaoCenariosDaugther, \
    TbOtimizacaoProdutoDaugther, TbOtimizacaoCustoItem, TbConsumoEspecificoTipoProducao, \
    TbOtimizacaoConjuntoEquipamentosDaugther, TbOtimizacaoShadow
from parameters.models import TbCenarios
from tabelas.models import TbTipoProducao, TbCustoItem, TbCustoItemPreco
from custo_ferbasa.models import TbConsumoEspecifico
import locale
locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  #  Estou usando esse pois Heroku não aceita pt_BR


class TbProdutoMercadoFluxoDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbProdutoMercadoFluxoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbProdutoMercadoFluxoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbProdutoMercadoFluxoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbProdutoMercadoFluxoDaugther.display_order.short_description = _('Ano/Mês')

class TbProdutoMercadoFluxoFormAdmin(forms.ModelForm):
    pass

class TbOtimizacaoEquipamentosFormAdmin(forms.ModelForm):
    pass

class TbOtimizacaoEquipamentosDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoEquipamentosDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoEquipamentosDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoEquipamentosDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoEquipamentosDaugther.display_order.short_description = _('Ano/Mês')


class TbOtimizacaoEquipamentosOrdemFormAdmin(forms.ModelForm):
    pass


class TbOtimizacaoEquipamentosOrdemDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoEquipamentosOrdemDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoEquipamentosOrdemDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoEquipamentosOrdemDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoEquipamentosOrdemDaugther.display_order.short_description = _('Ano/Mês')


class TbProdutoMercadoDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbProdutoMercadoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbProdutoMercadoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbProdutoMercadoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbProdutoMercadoDaugther.display_order.short_description = _('Ano/Mês')


class TbProdutoMercadoFormAdmin(forms.ModelForm):
    pass


class TbOtimizacaoProdutoDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)
    dau_valor_2 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoProdutoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoProdutoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoProdutoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoProdutoDaugther.display_order.short_description = _('Ano/Mês')


class TbOtimizacaoProdutoFormAdmin(forms.ModelForm):
    pass

class TbOtimizacaoConjuntoEquipamentosDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)
    dau_valor_2 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoConjuntoEquipamentosDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoConjuntoEquipamentosDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoConjuntoEquipamentosDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoConjuntoEquipamentosDaugther.display_order.short_description = _('Ano/Mês')

class TbOtimizacaoConjuntoEquipamentosFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoConjuntoEquipamentosFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura, altura e máscara para alguns campos no form
        try:
            self.fields['oti_con_equ_descricao'].widget.attrs['style'] = 'width: 500px;'
        except:
            pass

        '''
        # Selecionando dados somente do cenário ativo para campos tipo foreignkey / m2m no model
        # Também só mostra os equipamentos que não são gargalos
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                ativo = TbCenarios.objects.get(cen_ativo=True).id
                try:
                    self.fields['oti_con_equ_equipamentos'].queryset = TbEquipamentosCadastro.objects.filter(tbcenarios_id=ativo, equ_cad_gargalo = False)
                except:
                    pass
        '''

        # Para mostrar o código e a descrição do equipamento

        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        wtf = TbEquipamentosCadastro.objects.filter(tbcenarios_id=ativo, equ_cad_gargalo=False)

        w = self.fields['oti_con_equ_equipamentos'].widget
        choices = []
        for choice in wtf:
            descricao_equipamento = TbEquipamentosCadastro.objects.get(id=choice.id).equ_cad_descricao

            choices.append((choice.id, choice.equ_cad_codigo + ' / ' + descricao_equipamento))
        w.choices = choices

class TbOtimizacaoCustoItemFormAdmin(forms.ModelForm):
    pass


class TbConsumoEspecificoTipoProducaoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbConsumoEspecificoTipoProducaoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura, altura e máscara para alguns campos no form
        try:
            self.fields['con_esp_tip_pro_descricao'].widget.attrs['style'] = 'width: 800px;'
        except:
          pass

        # Vamos pegar os tipos de produção selecionados
        tipo_producao_qs = ()
        consumo_via_equipamento_qs = ()
        consumo_via_item_consumo_qs = ()
        if self.instance.pk is not None:
            tipo_producao_qs            = self.instance.con_esp_tip_pro_tipo_producao.values_list('id', flat=True)            # Retorna o id do tipo de produção na tabela TbTipoProducao. Independe do cenário
            consumo_via_equipamento_qs  = self.instance.con_esp_tip_pro_consumo_via_equipamento.values_list('id', flat=True)  # Retorna o id do tipo de produção na tabela TbTipoProducao. Independe do cenário
            consumo_via_item_consumo_qs = self.instance.con_esp_tip_pro_consumo_via_item_consumo.values_list('id', flat=True) # Retorna o id do item de consumo na tabela TbCustoItem. Independe do cenário

        producao = 0
        consumo_1 = 0
        consumo_2 = 0

        # Cenário ativo (usado nas 3 chamadas de stored procedure abaixo)
        cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id

        # Calculando a produção por tipo de produção para o cenário ativo
        wtf = TbTipoProducao.objects.all()

        w = self.fields['con_esp_tip_pro_tipo_producao'].widget
        choices = []
        for choice in wtf:

            # Vamos calcular a produção por tipo de produção para o cenário ativo
            cursor = connection.cursor()
            # Expressão SQL
            sql = "call public.producao_tipo_producao_cenario_ativo(" + str(cen_ativo_id) + ", " + str(choice.id) + ", 0)"  # choice.id é o id do tipo de produção
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            if retorno > 0:
                if choice.id in tipo_producao_qs:
                    producao = producao + retorno

                # Vamos formatar o valor no padrão brasileiro
                # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                retorno = locale.format_string('%.0f', retorno, True)

                choices.append((choice.id, choice.tip_nome + ' / ' + 'Produção=' + retorno))
        w.choices = choices

        if self.instance.pk is not None:  # Não está incluindo

            # Calculando o consumo via equipamento para o(s) tipo(s) de produção selecionado(s)

            wtf = TbTipoProducao.objects.all()

            w = self.fields['con_esp_tip_pro_consumo_via_equipamento'].widget
            choices = []
            for choice in wtf:
                # Vamos rodar nos tipos de produção selecionados
                retorno_total = 0
                # Vamos calcular o consumo via equipamento para os tipos de produção selecionados
                for tipo_producao in tipo_producao_qs:
                    cursor = connection.cursor()
                    # Expressão SQL
                    sql = "call public.consumo_via_equipamento_tipo_producao_cenario_ativo(" + str(cen_ativo_id) + ", " + str(choice.id) + ", " + str(tipo_producao) + ", 0)"  # choice.id é o id do tipo de produção
                    cursor.execute(sql)
                    retorno = cursor.fetchone()[0]
                    cursor.close()
                    retorno_total = retorno_total + retorno

                if retorno_total > 0:
                    if choice.id in consumo_via_equipamento_qs:
                        # Vamos verificar se foi informado ajuste no consumo
                        if self.instance.con_esp_tip_pro_ajuste1_id is not None:
                            if self.instance.con_esp_tip_pro_operacao1 == 'Multiplicação':
                                retorno_total = retorno_total * TbConsumoEspecifico.objects.get(id=self.instance.con_esp_tip_pro_ajuste1_id).con_esp_indicador
                            else:
                                retorno_total = retorno_total / TbConsumoEspecifico.objects.get(id=self.instance.con_esp_tip_pro_ajuste1_id).con_esp_indicador
                        consumo_1 = consumo_1 + retorno_total

                    # Vamos formatar o valor no padrão brasileiro
                    # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                    retorno_total = locale.format_string('%.0f', retorno_total, True)

                    choices.append((choice.id, choice.tip_nome + ' / ' + 'Consumo=' + retorno_total))
            w.choices = choices

            # Calculando o consumo via item de consumo para o(s) tipo(s) de produção selecionado(s)
            # Vamos selecionar os itens que estão na tabela TbOtimizacaoCustoItem
            qs = TbOtimizacaoCustoItem.objects.filter(tbcenarios=self.instance.tbcenarios)
            lista_id_item = []
            for q in qs:
                id_item = TbCustoItemPreco.objects.get(id=q.oti_cus_ite_item_id).cus_ite_pre_item_id
                if id_item not in lista_id_item:
                    lista_id_item.append(id_item)

            wtf = TbCustoItem.objects.filter(id__in=lista_id_item)

            w = self.fields['con_esp_tip_pro_consumo_via_item_consumo'].widget
            choices = []
            for choice in wtf:
                # Vamos rodar nos tipos de produção selecionados
                retorno_total = 0
                # Vamos calcular o consumo via item de consumo para os tipos de produção selecionados
                for tipo_producao in tipo_producao_qs:
                    cursor = connection.cursor()
                    # Expressão SQL
                    sql = "call public.consumo_via_item_consumo_tipo_producao_cenario_ativo(" + str(cen_ativo_id) + ", " + str(choice.id) + ", " + str(tipo_producao) + ", 0)"  # choice.id é o id do tipo de produção
                    cursor.execute(sql)
                    retorno = cursor.fetchone()[0]
                    cursor.close()
                    retorno_total = retorno_total + retorno

                if retorno_total > 0:
                    if choice.id in consumo_via_item_consumo_qs:
                        # Vamos verificar se foi informado ajuste no consumo
                        if self.instance.con_esp_tip_pro_ajuste2_id is not None:
                            if self.instance.con_esp_tip_pro_operacao2 == 'Multiplicação':
                                retorno_total = retorno_total * TbConsumoEspecifico.objects.get(id=self.instance.con_esp_tip_pro_ajuste2_id).con_esp_indicador
                            else:
                                retorno_total = retorno_total / TbConsumoEspecifico.objects.get(id=self.instance.con_esp_tip_pro_ajuste2_id).con_esp_indicador

                        consumo_2 = consumo_2 + retorno_total

                    # Vamos formatar o valor no padrão brasileiro
                    # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
                    retorno_total = locale.format_string('%.0f', retorno_total, True)

                    choices.append((choice.id, choice.cus_ite_nome + ' / ' + 'Consumo=' + retorno_total))
            w.choices = choices
            # Vamos salvar pra garantir
            TbConsumoEspecificoTipoProducao.objects.filter(id=self.instance.pk).update(con_esp_tip_pro_qtde_produzida=producao, con_esp_tip_pro_qtde_consumo=consumo_1+consumo_2)
            if producao > 0:
                TbConsumoEspecificoTipoProducao.objects.filter(id=self.instance.pk).update(con_esp_tip_pro_indicador=(consumo_1 + consumo_2)/producao)

        # Atualizando na tela os valores calculados
        self.instance.con_esp_tip_pro_qtde_produzida = producao
        self.instance.con_esp_tip_pro_qtde_consumo = consumo_1 + consumo_2
        if producao > 0:
            self.instance.con_esp_tip_pro_indicador = (consumo_1 + consumo_2)/producao


class TbOtimizacaoCustoItemDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)
    dau_valor_2 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)
    #dau_valor_3 = forms.DecimalField(max_digits=9, decimal_places=0, localize=True)  #  Foi eliminado pois o campo é readonly. Se deixar dá erro ao salvar. Coisa de louco mesmo.

    def __init__(self, *args, **kwargs):

        super(TbOtimizacaoCustoItemDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoCustoItemDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoCustoItemDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoCustoItemDaugther.display_order.short_description = _('Ano/Mês')


class TbOtimizacaoComparacaoCenariosDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoComparacaoCenariosDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoComparacaoCenariosDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoComparacaoCenariosDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoComparacaoCenariosDaugther.display_order.short_description = _('Ano/Mês')


class TbOtimizacaoComparacaoCenariosFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoComparacaoCenariosFormAdmin, self).__init__(*args, **kwargs)

        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        cen_tipo_query = TbCenarios.objects.get(cen_ativo=True).cen_tipo
        cen_inicio_query = TbCenarios.objects.get(cen_ativo=True).cen_inicio
        cen_fim_query = TbCenarios.objects.get(cen_ativo=True).cen_fim

        self.fields['oti_com_cenario_referencia'].queryset = TbCenarios.objects.filter(cen_tipo=cen_tipo_query, cen_inicio=cen_inicio_query, cen_fim=cen_fim_query).exclude(id=cen_ativo)


class TbOtimizacaoShadowFormAdmin(forms.ModelForm):
    pass
    '''
    def __init__(self, *args, **kwargs):
        super(TbOtimizacaoShadowFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbOtimizacaoShadow.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbOtimizacaoShadow.display_order.short_description = _('Ano/Trimestre')
        else:
            TbOtimizacaoShadow.display_order.short_description = _('Ano/Mês')
    '''