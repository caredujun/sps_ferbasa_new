from django import forms
from django.contrib import messages

from .models import *

class TbCenariosDaugtherFormAdmin(forms.ModelForm):
    '''
    dau_valor_1 = forms.DecimalField(localize=True)
    dau_valor_2 = forms.DecimalField(localize=True)
    dau_valor_3 = forms.DecimalField(localize=True)
    dau_valor_4 = forms.DecimalField(localize=True)
    dau_valor_5 = forms.DecimalField(localize=True)
    dau_valor_6 = forms.DecimalField(localize=True)
    dau_valor_7 = forms.DecimalField(localize=True)
    dau_valor_8 = forms.DecimalField(localize=True)
    dau_valor_9 = forms.DecimalField(localize=True)
    dau_valor_10 = forms.DecimalField(localize=True)
    dau_valor_11 = forms.DecimalField(localize=True)
    dau_valor_12 = forms.DecimalField(localize=True)
    dau_valor_13 = forms.DecimalField(localize=True)
    dau_valor_14 = forms.DecimalField(localize=True)
    dau_valor_15 = forms.DecimalField(localize=True)
    dau_valor_16 = forms.DecimalField(localize=True)
    dau_valor_17 = forms.DecimalField(localize=True)
    dau_valor_18 = forms.DecimalField(localize=True)
    dau_valor_19 = forms.DecimalField(localize=True)
    dau_valor_20 = forms.DecimalField(localize=True)
    '''

    def __init__(self, *args, **kwargs):
        super(TbCenariosDaugtherFormAdmin, self).__init__(*args, **kwargs)
        if self.instance.dau_order == 1:
            if TbCenarios.objects.get(id=self.instance.mae_id).cen_tipo == 'Anual':
                TbCenariosDaugther.display_order.short_description = 'Ano'
            elif TbCenarios.objects.get(id=self.instance.mae_id).cen_tipo == 'Trimestral':
                TbCenariosDaugther.display_order.short_description = 'Ano/Trimestre'
            else:
                TbCenariosDaugther.display_order.short_description = 'Ano/Mês'

class TbCenariosFormAdmin(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(TbCenariosFormAdmin, self).__init__(*args, **kwargs)
        if self.instance.cen_tipo == 'Anual':
            try:
                self.fields['cen_inicio'].widget.attrs['class'] = 'mask-cenario-1'
                self.fields['cen_fim'].widget.attrs['class'] = 'mask-cenario-1'
                self.fields['cen_inicio'].label = 'Ano Início'
                self.fields['cen_fim'].label = 'Ano Fim'
            except:
                pass

        elif self.instance.cen_tipo == 'Mensal' or self.instance.cen_tipo == 'Trimestral':
            try:
                self.fields['cen_inicio'].widget.attrs['class'] = 'mask-cenario-2'
                self.fields['cen_fim'].widget.attrs['class'] = 'mask-cenario-2'
                if self.instance.cen_tipo == 'Mensal':
                    self.fields['cen_inicio'].label = 'Ano/Mês Início'
                    self.fields['cen_fim'].label = 'Ano/Mês Fim'
                else:
                    self.fields['cen_inicio'].label = 'Ano/Trimestre Início'
                    self.fields['cen_fim'].label = 'Ano/Trimestre Fim'
            except:
                pass

            # Alterando a largura e altura de alguns campos no form
            try:
                self.fields['cen_nome'].widget.attrs['style'] = 'width: 300px;'
            except:
                pass

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Custom save message!")
        return response

class TbCenariosProdMercFluxFormAdmin(forms.ModelForm):
    pass

class TbCenariosProdMercFluxDaughterFormAdmin(forms.ModelForm):
    pass