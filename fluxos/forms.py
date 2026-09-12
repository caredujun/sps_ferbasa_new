from django import forms
from .models import *


class TbFluxoConsumoPadraoFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbFluxoConsumoPadraoFormAdmin, self).__init__(*args, **kwargs)

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
                    self.fields['flu_con_pad_from_equipamento'].queryset = TbEquipamentos.objects.filter(
                        tbcenarios_id=ativo)
                    self.fields['flu_con_pad_to_equipamento'].queryset = TbEquipamentos.objects.filter(
                        tbcenarios_id=ativo)
                except:
                    pass

        try:
            # Alterando a altura do campo Observação
            self.fields['flu_con_pad_observacao'].widget.attrs['style'] = 'height: 60px'

            # Alterando a largura do campo descrição
            self.fields['flu_con_pad_descricao'].widget.attrs['style'] = 'width: 510px;'
        except:
            pass


class TbFluxoConsumoPadraoDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbFluxoConsumoPadraoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbFluxoConsumoPadraoDaugther.display_order.short_description = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbFluxoConsumoPadraoDaugther.display_order.short_description = 'Ano/Trimestre'
        else:
            TbFluxoConsumoPadraoDaugther.display_order.short_description = 'Ano/Mês'


class TbFluxoProducaoFormAdmin(forms.ModelForm):

    # Mostra o campo flu_pro_copiar_de somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None, *args, **kwargs):
        if not obj:  # editing an existing object. Se estiver adicionando no indicador, irá aparecer o campo para informar o valor inicial
            return self.fields + ('flu_pro_copiar_de',)
        return self.fields

    def __init__(self, *args, **kwargs):
        super(TbFluxoProducaoFormAdmin, self).__init__(*args, **kwargs)

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
                    self.fields['flu_pro_produto'].queryset = TbProdutos.objects.filter(tbcenarios_id=ativo)
                except:
                    pass

        if 'flu_pro_copiar_de' in self.fields:
            # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
            # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
            all_tables = connection.introspection.table_names()
            if 'parameters_tbcenarios' in all_tables:

                # Existe. Vamos ver se tem registro
                qtde = TbCenarios.objects.filter(cen_ativo=True).count()
                if qtde == 1:
                    # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                    ativo = TbCenarios.objects.get(cen_ativo=True).id
                    self.fields['flu_pro_copiar_de'].queryset = TbFluxoProducao.objects.filter(tbcenarios_id=ativo)

        try:
            # Alterando a altura do campo Observação
            self.fields['flu_pro_observacao'].widget.attrs['style'] = 'height: 30px'
            # Alterando a comprimento do campo Descrição
            self.fields['flu_pro_descricao'].widget.attrs['style'] = 'width: 500px;'
        except:
            pass


class TbFluxoProducaoDaugtherFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbFluxoProducaoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        # Selecionando dados somente do cenário ativo para campos tipo foreignkey no model
        # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
        all_tables = connection.introspection.table_names()
        if 'parameters_tbcenarios' in all_tables:
            # Existe. Vamos ver se tem registro
            qtde = TbCenarios.objects.filter(cen_ativo=True).count()
            if qtde == 1:
                # Vamos pegar o cenário ativo e colocar o filtro nos foreignkeys do model
                ativo = TbCenarios.objects.get(cen_ativo=True).id
                if 'flu_pro_dau_consumo_padrao' in self.fields:
                    self.fields['flu_pro_dau_consumo_padrao'].queryset = TbFluxoConsumoPadrao.objects.filter(
                        tbcenarios_id=ativo)


class TbFluxoProducaoDaugther01FormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbFluxoProducaoDaugther01FormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbFluxoProducaoDaugther01.display_order.short_description = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbFluxoProducaoDaugther01.display_order.short_description = 'Ano/Trimestre'
        else:
            TbFluxoProducaoDaugther01.display_order.short_description = 'Ano/Mês'


class TbFluxoProducaoInputOutputFormAdmin(forms.ModelForm):
    pass


class TbFluxoProducaoInputOutputDaugtherFormAdmin(forms.ModelForm):
    dau_valor_1 = forms.DecimalField(localize=True)
    dau_valor_2 = forms.DecimalField(localize=True)
    dau_valor_3 = forms.DecimalField(localize=True)
    dau_valor_4 = forms.DecimalField(localize=True)
    produtividade = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbFluxoProducaoInputOutputDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            TbFluxoProducaoInputOutputDaugther.display_order.short_description = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            TbFluxoProducaoInputOutputDaugther.display_order.short_description = 'Ano/Trimestre'
        else:
            TbFluxoProducaoInputOutputDaugther.display_order.short_description = 'Ano/Mês'

        TbFluxoProducaoInputOutputDaugther.indfun.short_description = 'IF(%)'

class AtualizarFluxoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    #ano_mes_inicio = forms.CharField(max_length=7, required=False, label='Ano/Mês Início')
    #ano_mes_fim = forms.CharField(max_length=7, required=False, label='Ano/Mês Fim')
    def __init__(self, *args, **kwargs):
        super(AtualizarFluxoForm, self).__init__(*args, **kwargs)

