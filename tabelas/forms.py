from django import forms
from django.utils.translation import gettext_lazy as _
from tabelas.models import *
from parameters.models import TbEmpresa


class TbIndicadoresFormAdmin(forms.ModelForm):
    pass

    def __init__(self, *args, **kwargs):
        super(TbIndicadoresFormAdmin, self).__init__(*args, **kwargs)

        if 'valor_inicial' in self.fields:
            valor_inicial = forms.DecimalField(localize=True)


class TbIndicadoresDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(max_digits=6, decimal_places=4, localize=True)

    def __init__(self, *args, **kwargs):
        super(TbIndicadoresDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbIndicadoresDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbIndicadoresDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbIndicadoresDaugther.display_order.short_description = _('Ano/Mês')


class TbCambioFormAdmin(forms.ModelForm):
    pass

    def __init__(self, *args, **kwargs):
        super(TbCambioFormAdmin, self).__init__(*args, **kwargs)

        if 'valor_inicial' in self.fields:
            valor_inicial = forms.DecimalField(localize=True)


class TbCambioDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbCambioDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbCambioDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbCambioDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbCambioDaugther.display_order.short_description = _('Ano/Mês')


class TbImpostoRendaFormAdmin(forms.ModelForm):
    pass


class TbImpostoRendaDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):

        super(TbImpostoRendaDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbImpostoRendaDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbImpostoRendaDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbImpostoRendaDaugther.display_order.short_description = _('Ano/Mês')


class TbTaxaDescontoFormAdmin(forms.ModelForm):
    pass


class TbTaxaDescontoDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):

        super(TbTaxaDescontoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbTaxaDescontoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbTaxaDescontoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbTaxaDescontoDaugther.display_order.short_description = _('Ano/Mês')


class TbCustoFixoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbCustoFixoFormAdmin, self).__init__(*args, **kwargs)

        if 'valor_inicial_1' in self.fields:
            valor_inicial_1 = forms.DecimalField(localize=True)

        # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                ativo = TbCenarios.objects.get(cen_ativo=True).id
                try:
                    self.fields['fix_indicador'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                except:
                    pass


class TbCustoFixoDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbCustoFixoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbCustoFixoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbCustoFixoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbCustoFixoDaugther.display_order.short_description = _('Ano/Mês')

            # Vamos pegar a moeda do custo fixo para mostrar na coluna da filha
            if self.instance.mae_id is not None:
                moeda_custo_fixo = TbCustoFixo.objects.get(id=self.instance.mae_id).fix_moeda
                TbCustoFixoDaugther.dau_valor_1.label = moeda_custo_fixo

        if self.instance.dau_order is None:
            try:
                self.fields['dau_valor_1'].label = _('Valor (%(unidade)s)') % {'unidade': TbCustoFixoDaugther.dau_valor_1.label}
            except:
                self.fields['dau_valor_1'].label = _('Valor')


class TbDepreAmortiFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):

        super(TbDepreAmortiFormAdmin, self).__init__(*args, **kwargs)

        if 'valor_inicial' in self.fields:
            valor_inicial = forms.DecimalField(localize=True)

        # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                ativo = TbCenarios.objects.get(cen_ativo=True).id
                try:
                    self.fields['dep_indicador'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                except:
                    pass


class TbDepreAmortiDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbDepreAmortiDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbDepreAmortiDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbDepreAmortiDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbDepreAmortiDaugther.display_order.short_description = _('Ano/Mês')


class TbCapexFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbCapexFormAdmin, self).__init__(*args, **kwargs)

        if 'valor_inicial' in self.fields:
            valor_inicial = forms.DecimalField(localize=True)

        # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                ativo = TbCenarios.objects.get(cen_ativo=True).id
                try:
                    self.fields['cap_indicador'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                except:
                    pass


class TbCapexDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbCapexDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbCapexDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbCapexDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbCapexDaugther.display_order.short_description = _('Ano/Mês')


class TbMercadoFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbMercadoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['mer_observacao'].widget.attrs['style'] = 'height: 60px;'
        except:
            pass


class TbCustoItemFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbCustoItemFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['cus_ite_nome'].widget.attrs['style'] = 'width: 400px;'
            self.fields['cus_ite_codigo_interno'].widget.attrs['style'] = 'width: 90px;'
        except:
            pass


class TbCustoItemPrecoFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbCustoItemPrecoFormAdmin, self).__init__(*args, **kwargs)

        # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                try:
                    # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                    ativo = TbCenarios.objects.get(cen_ativo=True).id
                    # self.fields['cus_ite_pre_item'].queryset = TbCustoItem.objects.filter(tbcenarios_id=ativo)
                    # self.fields['cus_ite_pre_unidade_producao'].queryset = TbUnidadeProducao.objects.filter(tbcenarios_id=ativo)
                    self.fields['cus_ite_pre_indicador_preco'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                    self.fields['cus_ite_pre_indicador_inbound'].queryset = TbIndicadores.objects.filter(
                        tbcenarios_id=ativo)
                except:
                    pass


        if 'valor_inicial_1' in self.fields:
            valor_inicial_1 = forms.DecimalField(localize=True)
        if 'valor_inicial_2' in self.fields:
            valor_inicial_2 = forms.DecimalField(localize=True)


class TbCustoItemPrecoDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(localize=True)
    dau_valor_2 = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbCustoItemPrecoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbCustoItemPrecoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbCustoItemPrecoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbCustoItemPrecoDaugther.display_order.short_description = _('Ano/Mês')


class TbTipoProducaoFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbTipoProducaoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['tip_nome'].widget.attrs['style'] = 'width: 400px;'
        except:
            pass


class TbEquacaoAjustePrecoFormAdmin(forms.ModelForm):
    # Quando não tem filha, coloca o localize aqui, antes do def __init__

    equ_aju_pre_constante_a = forms.DecimalField(localize=True, label='A')
    equ_aju_pre_constante_b = forms.DecimalField(localize=True, label='B')
    equ_aju_pre_constante_c = forms.DecimalField(localize=True, label='C')
    equ_aju_pre_constante_d = forms.DecimalField(localize=True, label='D')
    equ_aju_pre_constante_e = forms.DecimalField(localize=True, label='E')
    equ_aju_pre_constante_f = forms.DecimalField(localize=True, label='F')
    equ_aju_pre_var = forms.DecimalField(localize=True, label='Valor VAR para teste')
    equ_aju_pre_valor1 = forms.DecimalField(max_digits=10, decimal_places=2, required=False, disabled=True,
                                            localize=True, label='')
    equ_aju_pre_valor2 = forms.DecimalField(max_digits=10, decimal_places=2, required=False, disabled=True,
                                            localize=True, label='')

    def __init__(self, *args, **kwargs):
        super(TbEquacaoAjustePrecoFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['equ_aju_pre_primeiro_titulo'].widget.attrs['style'] = 'width: 60px;'
            self.fields['equ_aju_pre_segundo_titulo'].widget.attrs['style'] = 'width: 40px;'
            self.fields['equ_aju_pre_var'].widget.attrs['style'] = 'width: 60px;'
            self.fields['equ_aju_pre_valor1'].widget.attrs['style'] = 'width: 80px;'
            self.fields['equ_aju_pre_valor2'].widget.attrs['style'] = 'width: 80px;'
        except:
            pass

        # Criando help_text para alguns campos
        # Se estiver adicionando
        if self.instance.pk is not None:
            if self.instance.equ_aju_pre_primeiro_titulo is None:
                self.instance.equ_aju_pre_primeiro_titulo = ''
            if self.instance.equ_aju_pre_segundo_titulo is None:
                self.instance.equ_aju_pre_segundo_titulo = ''

            try:
                self.fields['equ_aju_pre_var'].help_text = '(' + self.instance.equ_aju_pre_moeda + self.instance.equ_aju_pre_primeiro_titulo + '/' + self.instance.equ_aju_pre_segundo_titulo + ')'
                self.fields['equ_aju_pre_valor1'].help_text = '(' + self.instance.equ_aju_pre_moeda + ')'
            except:
                pass


        # Vamos ver a moeda que a empresa está usando
        # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbempresa' in all_tables:
            # Vamos ver se tem registro
            if TbEmpresa.objects.filter(id=1).count() == 1:
                moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            else:
                moeda_empresa = ''
        else:
            moeda_empresa = ''

        try:
            self.fields['equ_aju_pre_valor2'].help_text = '(' + moeda_empresa + ')'
        except:
            pass