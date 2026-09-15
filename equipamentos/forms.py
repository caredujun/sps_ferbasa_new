from django import forms
from django.utils.translation import gettext_lazy as _
from .models import *

class TbEquipamentosCadastroOrdemDaugtherFormAdmin(forms.ModelForm):
    pass

class TbEquipamentosCadastroDaugtherFormAdmin(forms.ModelForm):

    dau_valor_2 = forms.DecimalField(localize=True)
    dau_valor_3 = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosCadastroDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if self.instance.dau_order == 1:  # Para não rodar em todos os registros da filha
            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                TbEquipamentosCadastroDaugther.display_order.short_description = _('Ano')
            elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                TbEquipamentosCadastroDaugther.display_order.short_description = _('Ano/Trimestre')
            else:
                TbEquipamentosCadastroDaugther.display_order.short_description = _('Ano/Mês')

            # Vamos pegar a moeda da manutenção para mostrar na coluna da filha
            # Vamos pegar o id do equipamento
            if self.instance.mae_id is not None:
                moeda_manutencao = TbEquipamentosCadastro.objects.get(id=self.instance.mae_id).equ_cad_moeda_manutencao
                TbEquipamentosCadastroDaugther.dau_valor_3.label = moeda_manutencao

        if self.instance.dau_order is None:
            try:
                self.fields['dau_valor_3'].label = _('Custo Manutenção (%(moeda)s/h)') % {'moeda': TbEquipamentosCadastroDaugther.dau_valor_3.label}
            except:
                self.fields['dau_valor_3'].label = _('Custo Manutenção (Moeda/h)')


class TbEquipamentosCadastroFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosCadastroFormAdmin, self).__init__(*args, **kwargs)

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['equ_cad_codigo'].widget.attrs['style'] = 'width: 120px;'
            self.fields['equ_cad_descricao'].widget.attrs['style'] = 'width: 250px;'
        except:
            pass

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
                    self.fields['equ_cad_indicador_manutencao'].queryset = TbIndicadores.objects.filter(tbcenarios_id=ativo)
                except:
                    pass


class TbEquipamentosFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosFormAdmin, self).__init__(*args, **kwargs)

        """
        if 'valor_inicial_2' in self.fields:
            valor_inicial_2 = forms.DecimalField(localize=True)
        if 'valor_inicial_3' in self.fields:
            valor_inicial_3 = forms.DecimalField(localize=True)
        """

        # Alterando a largura e altura de alguns campos no form
        try:
            self.fields['equ_codigo'].widget.attrs['style'] = 'width: 90px;'
            #self.fields['equ_observacao'].widget.attrs['style'] = 'height:60px;'
            self.fields['equ_ordem_descricao'].widget.attrs['style'] = 'width: 300px;'
        except:
            pass

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
                    self.fields['equ_codigo'].queryset = TbEquipamentosCadastro.objects.filter(tbcenarios_id=ativo)
                except:
                    pass

class TbEquipamentosConsumoEspecificoDaugtherItensFormAdmin(forms.ModelForm):
    pass

class TbEquipamentosDaugtherFormAdmin(forms.ModelForm):

    dau_valor_1 = forms.DecimalField(localize=True)
    dau_valor_2 = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if self.instance.dau_order == 1:
            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                TbEquipamentosDaugther.display_order.short_description = _('Ano')
            elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                TbEquipamentosDaugther.display_order.short_description = _('Ano/Trimestre')
            else:
                TbEquipamentosDaugther.display_order.short_description = _('Ano/Mês')


        # Vamos mudar o nome de algumas colunas na daugther
        if self.instance.dau_order == 1:  # Faço isso pois se deixar ir no último dau_order o self.instance.mae_id return None
            # Vamos pegar o id do equipamento
            id_equipamento = TbEquipamentos.objects.get(id=self.instance.mae_id).equ_codigo_id
            # Vamos pegar a unidade de produção
            TbEquipamentosDaugther.wip_volume.short_description = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_output

        # Vamos mudar a coluna de campos da daugther. Temos que deixar chegar no final do dau_order para alterar. Coisa de maluco mesmo.
        if self.instance.dau_order is None:  # Chegou no final do dau_order. Vamos mudar o label da coluna da filha
            try:
                self.fields['dau_valor_2'].label = _('Produtividade (%(unidade)s/h)') % {'unidade': TbEquipamentosDaugther.wip_volume.short_description}
            except:
                self.fields['dau_valor_2'].label = _('Produtividade')

            # Mudando o short_description
            try:
                TbEquipamentosDaugther.wip_volume.short_description = _('WIP (%(unidade)s)') % {'unidade': TbEquipamentosDaugther.wip_volume.short_description}
            except:
                pass

class TbEquipamentosConsumoEspecificoFormAdmin(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosConsumoEspecificoFormAdmin, self).__init__(*args, **kwargs)

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
                    self.fields['equ_con_esp_equipamento'].queryset = TbEquipamentos.objects.filter(tbcenarios_id=ativo)
                    self.fields['equ_con_esp_custoitempreco'].queryset = TbCustoItemPreco.objects.filter(tbcenarios_id=ativo)
                except:
                    pass

        # Alterando a altura do campo Observação
        try:
            self.fields['equ_con_esp_observacao'].widget.attrs['style'] = 'height: 60px'
        except:
            pass

class TbEquipamentosConsumoEspecificoDaugtherFormAdmin(forms.ModelForm):
    dau_valor = forms.DecimalField(localize=True)

    def __init__(self, *args, **kwargs):
        super(TbEquipamentosConsumoEspecificoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        if self.instance.dau_order == 1:  # Para não rodar em todos os registros da filha
            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                TbEquipamentosConsumoEspecificoDaugther.display_order.short_description = _('Ano')
            elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                TbEquipamentosConsumoEspecificoDaugther.display_order.short_description = _('Ano/Trimestre')
            else:
                TbEquipamentosConsumoEspecificoDaugther.display_order.short_description = _('Ano/Mês')