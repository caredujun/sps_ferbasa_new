"""
Widget compartilhado: seletor de duas colunas do Admin (FilteredSelectMultiple)
que pinta de VERMELHO, na caixa dos ESCOLHIDOS, os itens que não aparecem em
nenhum fluxo de produção.

Usado nas duas pontas do mesmo vínculo (TbEquipamentos.equ_produtos):
  - no produto:      equipamentos/ordens escolhidos que nenhum fluxo do produto usa;
  - no equipamento:  produtos escolhidos que nenhum fluxo usa com este equipamento/ordem.

Como usar:
    widget = WidgetEscolhidosSemFluxo(verbose_name, is_stacked)
    widget.ids_sem_fluxo = {ids (pk) das opções que NÃO aparecem em fluxo nenhum}
    widget.dica_vermelho = 'texto do tooltip do item vermelho'   # opcional

Sem ids_sem_fluxo (cadastro novo, ou nada a destacar) NÃO emite script nenhum e
se comporta exatamente como o FilteredSelectMultiple padrão.

Por que o script existe (e por que é assim):
  - O seletor do Admin RECRIA as opções a cada movimentação (SelectBox.redisplay),
    perdendo qualquer estilo -- por isso um MutationObserver repinta sempre que a
    lista mudar. Só fica vermelho o que está ESCOLHIDO naquele momento (inclusive
    o que o usuário acabou de mover na tela).
  - A cor é aplicada com setProperty(..., 'important'): o tema do Admin define a
    cor das opções com !important, e um estilo comum no elemento perde para isso
    (o negrito aparecia, a cor não). Foi o defeito encontrado em uso real.
"""
import json

from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils.safestring import mark_safe

_SCRIPT_PINTAR_SEM_FLUXO = """
<script>
(function () {
  var campoId = __CAMPO_ID__;
  var semFluxo = new Set(__IDS__);   // ids que NÃO aparecem em nenhum fluxo
  var dica = __DICA__;

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
        opcao.title = vermelho ? (opcao.text + ' -- ' + dica) : opcao.text;
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


def _js(valor):
    """Valor Python -> literal JavaScript seguro para ir dentro de um <script>."""
    return json.dumps(valor).replace('</', '<\\/')


class WidgetEscolhidosSemFluxo(FilteredSelectMultiple):
    ids_sem_fluxo = ()
    dica_vermelho = 'escolhido, mas não aparece em nenhum fluxo de produção'

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        if not self.ids_sem_fluxo:
            return html
        campo_id = (attrs or {}).get('id') or self.attrs.get('id') or f'id_{name}'
        script = (
            _SCRIPT_PINTAR_SEM_FLUXO
            .replace('__CAMPO_ID__', _js(campo_id))
            .replace('__IDS__', _js(sorted(str(i) for i in self.ids_sem_fluxo)))
            .replace('__DICA__', _js(str(self.dica_vermelho)))
        )
        return mark_safe(html + script)