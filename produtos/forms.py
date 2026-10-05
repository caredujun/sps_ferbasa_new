import json

from django import forms
from django.apps import apps
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.db.models import Q
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from fluxos.models import TbFluxoProducao
from .models import *
from parameters.models import TbCenarios
from parameters.contexto_usuario import limit_choices_to_cenario_ativo


class TbProdutoFluxoProducaoDaugtherFormAdmin(forms.ModelForm):
    model = TbFluxoProducao

    def __init__(self, *args, **kwargs):
        super(TbProdutoFluxoProducaoDaugtherFormAdmin, self).__init__(*args, **kwargs)

        TbFluxoProducao.display_descricao.short_description = ''
        # Alterando a altura de campo
        # self.fields['display_descricao'].widget.attrs['style'] = 'height: 60px'


_SCRIPT_PINTAR_SEM_FLUXO = """
<script>
(function () {
  var campoId = __CAMPO_ID__;
  var semFluxo = new Set(__IDS__);   // ids que NÃO aparecem em nenhum fluxo do produto

  function pintar() {
    var escolhidos = document.getElementById(campoId + '_to');     // caixa dos ESCOLHIDOS
    var disponiveis = document.getElementById(campoId + '_from');  // caixa dos disponíveis
    [disponiveis, escolhidos].forEach(function (caixa) {
      if (!caixa) return;
      Array.prototype.forEach.call(caixa.options, function (opcao) {
        // Só fica vermelho o que está ESCOLHIDO e não aparece em fluxo nenhum.
        var vermelho = (caixa === escolhidos) && semFluxo.has(opcao.value);
        // setProperty(..., 'important'): o tema do Admin define a cor das opções com
        // !important, e um estilo comum no elemento perde para isso (o negrito aparecia,
        // a cor não). Com !important no próprio elemento, ele passa a ganhar.
        // -webkit-text-fill-color: no Chrome ela, se definida pelo tema, vale mais que color.
        if (vermelho) {
          opcao.style.setProperty('color', '#c00', 'important');
          opcao.style.setProperty('-webkit-text-fill-color', '#c00', 'important');
          opcao.style.setProperty('font-weight', 'bold', 'important');
        } else {
          opcao.style.removeProperty('color');
          opcao.style.removeProperty('-webkit-text-fill-color');
          opcao.style.removeProperty('font-weight');
        }
        opcao.title = vermelho
          ? opcao.text + ' -- escolhido, mas não aparece em nenhum fluxo de produção deste produto'
          : opcao.text;
      });
    });
  }

  function iniciar() {
    var escolhidos = document.getElementById(campoId + '_to');
    var disponiveis = document.getElementById(campoId + '_from');
    if (!escolhidos || !disponiveis) return false;
    // O seletor do Admin RECRIA as opções a cada movimentação (SelectBox.redisplay),
    // perdendo o estilo -- por isso repinta sempre que a lista mudar.
    var observador = new MutationObserver(pintar);
    // subtree: o seletor pode agrupar as opções dentro de <optgroup>.
    observador.observe(escolhidos, { childList: true, subtree: true });
    observador.observe(disponiveis, { childList: true, subtree: true });
    pintar();
    return true;
  }

  // O SelectFilter2 do Admin monta as duas caixas no evento "load".
  window.addEventListener('load', function () {
    if (!iniciar()) { setTimeout(iniciar, 500); }
  });
})();
</script>
"""


class _WidgetEquipamentosProducao(FilteredSelectMultiple):
    """
    Seletor de duas colunas do Admin + um script que pinta de VERMELHO, na
    caixa dos escolhidos, os equipamentos que não aparecem em nenhum fluxo
    de produção do produto. Sem ids_sem_fluxo (produto novo, ou sem nada a
    destacar) não emite script nenhum e se comporta como o widget padrão.
    """
    ids_sem_fluxo = ()

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        if not self.ids_sem_fluxo:
            return html
        campo_id = (attrs or {}).get('id') or self.attrs.get('id') or f'id_{name}'
        script = (
            _SCRIPT_PINTAR_SEM_FLUXO
            .replace('__CAMPO_ID__', json.dumps(campo_id))
            .replace('__IDS__', json.dumps(sorted(str(i) for i in self.ids_sem_fluxo)))
        )
        return mark_safe(html + script)


class TbProdutosFormAdmin(forms.ModelForm):
    # 🌟 NOVO: equipamentos/ordens de produção que compõem a produção do
    # produto -- visão, pelo lado do PRODUTO, do mesmo vínculo que
    # TbEquipamentos.equ_produtos já guarda pelo lado do equipamento.
    #
    # NÃO é uma segunda tabela nem um segundo campo no banco: lê e grava
    # a MESMA tabela de ligação (acesso reverso do ManyToMany). Por isso
    # qualquer alteração feita em um dos lados já aparece no outro, sem
    # sinal nem sincronização, e os vínculos que já estavam cadastrados no
    # equipamento aparecem aqui sem nenhuma migração de dados.
    #
    # As opções são limitadas ao CENÁRIO do produto (e, como o cenário
    # pertence a uma empresa, à empresa também). O queryset é definido no
    # __init__, porque depende do produto sendo editado.
    equipamentos_producao = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        label=_('Equipamentos/Ordens Que Compõem a Produção'),
        widget=_WidgetEquipamentosProducao(_('Equipamentos/Ordens'), is_stacked=False),
        help_text=_(
            'Em vermelho: escolhido, mas não aparece em nenhum fluxo de produção do produto. '
            'Compare com o painel "Equipamentos Usados nos Fluxos" abaixo.'
        ),
    )

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

        # Fora do try acima de propósito: aquele bloco falha na linha de
        # pro_codigo_interno (campo que não existe em TbProdutos) e o
        # except engole -- qualquer coisa colocada lá dentro depois dessa
        # linha nunca executaria.
        self._configurar_equipamentos_producao()

    # apps.get_model em vez de import direto: equipamentos.models importa
    # produtos.models, então importar de volta aqui criaria um ciclo.
    @staticmethod
    def _modelo_equipamentos():
        return apps.get_model('equipamentos', 'TbEquipamentos')

    @classmethod
    def _nome_acessor_reverso(cls):
        # Nome do acesso reverso de equ_produtos visto de TbProdutos
        # (hoje "tbequipamentos_set"). Resolvido pelo Django em vez de
        # fixo no código, pra continuar certo se alguém definir um
        # related_name no campo depois.
        campo = cls._modelo_equipamentos()._meta.get_field('equ_produtos')
        return campo.remote_field.get_accessor_name()

    def _configurar_equipamentos_producao(self):
        campo = self.fields.get('equipamentos_producao')
        if campo is None:
            return

        TbEquipamentos = self._modelo_equipamentos()

        if self.instance.pk is not None:
            # Produto já gravado: o escopo é o cenário DELE.
            filtro = Q(tbcenarios_id=self.instance.tbcenarios_id)
        else:
            # Produto novo ainda não tem cenário gravado (ele é definido no
            # save, a partir do cenário ativo) -- usa o MESMO filtro de
            # cenário ativo que o equ_produtos usa do lado do equipamento.
            try:
                limite = limit_choices_to_cenario_ativo()
                filtro = limite if isinstance(limite, Q) else Q(**limite)
            except Exception:
                # Sem contexto de usuário (ex: shell) não há como saber o
                # cenário ativo -- melhor não oferecer nada do que oferecer
                # equipamentos de qualquer cenário.
                filtro = Q(pk__in=[])

        campo.queryset = (
            TbEquipamentos.objects.filter(filtro)
            .select_related('equ_codigo')
            .order_by('equ_codigo__equ_cad_codigo', 'equ_ordem_codigo')
        )

        if self.instance.pk is not None:
            acessor = getattr(self.instance, self._nome_acessor_reverso())
            self.initial['equipamentos_producao'] = list(acessor.values_list('pk', flat=True))

            # Equipamentos do cenário que NÃO aparecem em nenhum fluxo do
            # produto. O script do widget só pinta de vermelho os que
            # estiverem na caixa dos ESCOLHIDOS naquele momento (inclusive
            # os que o usuário acabou de mover na tela).
            usados_nos_fluxos = self.instance._ids_equipamentos_usados()
            todos_do_cenario = set(campo.queryset.values_list('pk', flat=True))
            campo.widget.ids_sem_fluxo = todos_do_cenario - usados_nos_fluxos

    def _save_m2m(self):
        # O Admin chama isso depois de gravar o produto (e o produto novo
        # já tem pk nesse ponto), nos dois caminhos do ModelForm
        # (commit=True e commit=False + save_m2m()).
        super()._save_m2m()

        escolhidos = self.cleaned_data.get('equipamentos_producao')
        if escolhidos is None:
            # Campo não veio no formulário: não mexe em nada (em vez de
            # tratar como "esvaziou tudo").
            return

        acessor = getattr(self.instance, self._nome_acessor_reverso())

        # Só adiciona/remove dentro do escopo do cenário (as opções do
        # campo). Um vínculo antigo com equipamento de OUTRO cenário não
        # aparece na tela, então também não pode ser apagado por ela.
        escopo = set(self.fields['equipamentos_producao'].queryset.values_list('pk', flat=True))
        atuais = set(acessor.values_list('pk', flat=True)) & escopo
        novos = {equipamento.pk for equipamento in escolhidos}

        acessor.remove(*(atuais - novos))
        acessor.add(*(novos - atuais))


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