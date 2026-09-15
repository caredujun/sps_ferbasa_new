from django import forms
from django.utils.translation import gettext_lazy as _
from fluxos.models import TbFluxoProducao
from .models import *
from parameters.models import TbCenarios


class TbProdutoFluxoProducaoDaugtherFormAdmin(forms.ModelForm):
    model = TbFluxoProducao

    def __init__(self, *args, **kwargs):
        super(TbProdutoFluxoProducaoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        TbFluxoProducao.display_descricao.short_description = _('')
        # Alterando a altura de campo
        # self.fields['display_descricao'].widget.attrs['style'] = 'height: 60px'


class TbProdutosFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbProdutosFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['pro_codigo'].widget.attrs['style'] = 'width:150px;'
            self.fields['pro_codigo_interno'].widget.attrs['style'] = 'width:100px;'
            self.fields['pro_unidade_producao'].widget.attrs[
                'style'] = 'width:50px;'  # Não funciona no campo tipo foreign key. Pesquisar ....
            self.fields['pro_observacao'].widget.attrs['style'] = 'height:60px;'
        except:
            pass


class TbProdutoMercadoPrecoFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbProdutoMercadoPrecoFormAdmin, self).__init__(*args, **kwargs)

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
                    self.fields['pro_mer_pre_produto'].queryset = TbProdutos.objects.filter(tbcenarios_id=ativo)
                    self.fields['pro_mer_pre_indicador'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                    self.fields['pro_mer_pre_indicador_vol_min'].queryset = TbIndicadores.objects.filter(
                        tbcenarios_id=ativo)
                    self.fields['pro_mer_pre_indicador_vol_max'].queryset = TbIndicadores.objects.filter(
                        tbcenarios_id=ativo)
                except:
                    pass

        if 'valor_inicial_1' in self.fields:
            valor_inicial_1 = forms.DecimalField(localize=True)
        if 'valor_inicial_2' in self.fields:
            valor_inicial_2 = forms.DecimalField(localize=True)
        if 'valor_inicial_3' in self.fields:
            valor_inicial_3 = forms.DecimalField(localize=True)

        # Alterando a altura de alguns campos
        try:
            self.fields['pro_mer_pre_codigo_interno'].widget.attrs['style'] = 'width:80px;'
            self.fields['pro_mer_pre_observacao'].widget.attrs['style'] = 'height: 30px'
        except:
            pass


class TbProdutoMercadoPrecoDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(localize=True)
    dau_valor_2 = forms.DecimalField(localize=True)
    dau_valor_3 = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbProdutoMercadoPrecoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        # self.fields['preco_moeda'].widget.attrs['class'] = 'mask-moeda'

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbProdutoMercadoPrecoDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbProdutoMercadoPrecoDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbProdutoMercadoPrecoDaugther.display_order.short_description = _('Ano/Mês')

        # Vamos ver a moeda que a empresa está usando
        moeda_empresa = ''
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

        if self.instance.mae_id is not None:
            equacao = TbProdutoMercadoPreco.objects.get(id=self.instance.mae_id).pro_mer_pre_equacao
            moeda_produto = TbProdutoMercadoPreco.objects.get(id=self.instance.mae_id).pro_mer_pre_moeda
            if equacao is not None:  # Foi informado equação de preço.
                # Vamos pegar os dados na tabela de equações
                titulo_primeiro = TbEquacaoAjustePreco.objects.get(
                    equ_aju_pre_descricao=equacao).equ_aju_pre_primeiro_titulo
                titulo_segundo = TbEquacaoAjustePreco.objects.get(
                    equ_aju_pre_descricao=equacao).equ_aju_pre_segundo_titulo
                moeda_equacao = TbEquacaoAjustePreco.objects.get(equ_aju_pre_descricao=equacao).equ_aju_pre_moeda
                titulo = _('Preço (%(moeda)s%(t1)s/%(t2)s)') % {'moeda': moeda_produto, 't1': titulo_primeiro, 't2': titulo_segundo}
            else:
                titulo = _('Preço (%(moeda)s)') % {'moeda': moeda_produto}

            TbProdutoMercadoPrecoDaugther.preco_moeda.short_description = _('Preço (%(moeda)s)') % {'moeda': moeda_produto}
            TbProdutoMercadoPrecoDaugther.preco_moeda_empresa.short_description = _('Preço (%(moeda)s)') % {'moeda': moeda_empresa}


class TbMercadoOutboundFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbMercadoOutboundFormAdmin, self).__init__(*args, **kwargs)

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
                    self.fields['mer_out_produto'].queryset = TbProdutos.objects.filter(tbcenarios_id=ativo)
                except:
                    pass




class TbMercadoOutboundDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbMercadoOutboundDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbMercadoOutboundDaugther.display_order.short_description = _('Ano')
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbMercadoOutboundDaugther.display_order.short_description = _('Ano/Trimestre')
        else:
            TbMercadoOutboundDaugther.display_order.short_description = _('Ano/Mês')








