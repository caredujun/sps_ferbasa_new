"""
🌟 NOVO: reseta TbCenarios.flag pra 0 (e zera os valores calculados de
TbCenariosDaugther) sempre que uma tabela que ALIMENTA o cálculo do
cenário for alterada -- sinaliza pro usuário que o resultado consolidado
que ele está vendo ficou desatualizado, e precisa ser reprocessado.

ESCOPO ATUAL (a pedido): tabelas dos apps tabelas/produtos/equipamentos/
fluxos. NÃO inclui custo_ferbasa -- mudanças lá acabam refletindo numa
das tabelas abaixo de qualquer forma (ex: Consumo Específico ->
TbFluxoConsumoPadrao/TbEquipamentosConsumoEspecifico), disparando o
sinal por tabela interposta. NÃO cobre as tasks de atualização em massa
(usam .update(), que não dispara sinal do Django -- são só acionadas por
comando explícito do usuário, então ficam de fora por enquanto).
"""
from django.apps import apps
from django.db.models.signals import post_save, post_delete
from django.urls import reverse, NoReverseMatch
from django.utils import timezone

from .contexto_usuario import get_usuario_atual
from .models import TbCenarios, TbCenariosDaugther

# 🌟 CORRIGIDO: TbCenariosDaugther usa 8 (não 0) pra sinalizar
# "desatualizado" -- 0 já tinha um significado próprio nessa tabela
# (solucao_otima() == "Não", i.e. "essa linha não é a solução ótima"),
# então usar 0 aqui colidiria com isso. Em TbCenarios (a tabela mãe), 0
# não tinha nenhum uso antes, então lá sim usamos 0 mesmo (ver
# status_cenario() em models.py).
_FLAG_ALTERADO_CENARIOS_DAUGTHER = 8

# 🌟 Nomes dos campos de valor calculado em TbCenariosDaugther
# (dau_valor_1 até dau_valor_20) -- zerados junto com o flag.
_CAMPOS_VALOR_CENARIOS_DAUGTHER = {f'dau_valor_{i}': 0 for i in range(1, 21)}


def _nome_tabela_para_historico(sender, instance):
    """
    🌟 NOVO: nome da tabela pro histórico -- se for uma tabela "filha"
    (tem campo mae, aponta pra uma tabela "mãe"), mostra "Mãe/Filha" em
    vez de só o nome genérico da filha sozinho (que na maioria dessas
    tabelas é só "Detalhe", pouco informativo por si só).
    """
    nome_proprio = str(sender._meta.verbose_name) or sender.__name__
    mae_id = getattr(instance, 'mae_id', None)
    if mae_id is None:
        return nome_proprio
    try:
        mae = instance.mae
    except Exception:
        return nome_proprio
    if mae is None:
        return nome_proprio
    return f"{mae._meta.verbose_name}/{nome_proprio}"


def _montar_linha_historico(sender, instance, **kwargs):
    """
    🌟 CORRIGIDO: monta uma linha do histórico de "Últimas Alterações" --
    data, hora, usuário, app, tabela e (quando dá pra saber) o campo,
    seguido de um link pro registro alterado (igual a "Ações recentes"
    do Django Admin).

    O nome do app usa o verbose_name do AppConfig (apps.py de cada app)
    -- o mesmo nome que já aparece no menu lateral do Admin -- em vez do
    app_label cru (minúsculo, sem espaço).

    O link: tenta primeiro a URL de edição do PRÓPRIO registro alterado.
    A maioria das tabelas "filha" (*Daugther), porém, só é editável via
    INLINE dentro da tela da tabela MÃE -- não têm URL de edição própria
    no Admin, então essa 1ª tentativa falha (NoReverseMatch) pra quase
    todas elas. Nesse caso, cai pra URL de edição da MÃE (instance.mae),
    que é onde a edição de verdade acontece. Só omite o link de vez
    quando nem uma coisa nem outra tem página no Admin, ou quando o
    registro foi excluído (não tem mais o que abrir).
    """
    agora = timezone.now().strftime('%d/%m/%Y %H:%M:%S')

    usuario = get_usuario_atual()
    nome_usuario = usuario.get_username() if usuario is not None else 'Sistema'

    try:
        nome_app = str(apps.get_app_config(sender._meta.app_label).verbose_name).strip()
    except LookupError:
        nome_app = sender._meta.app_label
    nome_tabela = _nome_tabela_para_historico(sender, instance)

    eh_delete = kwargs.get('signal') is post_delete
    if eh_delete:
        detalhe_campo = 'registro excluído'
    else:
        update_fields = kwargs.get('update_fields')
        detalhe_campo = ', '.join(sorted(update_fields)) if update_fields else None

    linha = f"{agora} - {nome_usuario} - {nome_app} - {nome_tabela}"
    if detalhe_campo:
        linha += f" - {detalhe_campo}"

    if not eh_delete:
        url = None
        try:
            url = reverse(f'admin:{sender._meta.app_label}_{sender._meta.model_name}_change', args=[instance.pk])
        except NoReverseMatch:
            mae_id = getattr(instance, 'mae_id', None)
            if mae_id is not None:
                try:
                    mae_model = instance.mae._meta.model
                    url = reverse(
                        f'admin:{mae_model._meta.app_label}_{mae_model._meta.model_name}_change',
                        args=[mae_id],
                    )
                except Exception:
                    url = None
        if url:
            linha += f" | {url}"

    return linha


def _resetar_flag_cenario_se_necessario(sender, instance, **kwargs):
    """
    Handler compartilhado por todas as tabelas conectadas (ver
    conectar_sinais, abaixo). Cada uma delas tem um campo tbcenarios_id
    direto -- não precisa saber qual tabela disparou, só de qual cenário.

    🌟 CORRIGIDO: agora SEMPRE registra uma linha nova em
    TbCenarios.ultimas_alteracoes, mesmo depois da primeira vez -- só o
    reset do flag (TbCenarios.flag=0 e os campos calculados de
    TbCenariosDaugther) é que continua acontecendo só na PRIMEIRA
    alteração (quando o cenário ainda não estava "ALTERADO"). A partir
    da segunda alteração em diante, o flag não muda de novo (já está 0),
    mas cada alteração seguinte ainda vira uma linha nova no histórico.
    """
    cenario_id = getattr(instance, 'tbcenarios_id', None)
    if cenario_id is None:
        return

    cenario = TbCenarios.objects_real.filter(id=cenario_id).only('id', 'flag', 'ultimas_alteracoes').first()
    if cenario is None:
        return

    linha_nova = _montar_linha_historico(sender, instance, **kwargs)
    historico_atual = cenario.ultimas_alteracoes or ''
    historico_novo = f"{historico_atual}\n{linha_nova}" if historico_atual else linha_nova

    if cenario.flag == 0:
        # 🌟 Já estava "ALTERADO" -- só acrescenta mais uma linha no
        # histórico, sem mexer no flag nem nos valores calculados de
        # novo (evita updates repetidos à toa nesses dois).
        TbCenarios.objects_real.filter(id=cenario_id).update(ultimas_alteracoes=historico_novo)
        return

    TbCenarios.objects_real.filter(id=cenario_id).update(flag=0, ultimas_alteracoes=historico_novo)
    TbCenariosDaugther.objects.filter(mae_id=cenario_id).update(
        flag=_FLAG_ALTERADO_CENARIOS_DAUGTHER, **_CAMPOS_VALOR_CENARIOS_DAUGTHER
    )


def conectar_sinais():
    """
    Chamada uma vez, em ParametersConfig.ready() -- conecta o handler
    acima em post_save E post_delete de cada tabela relevante (post_save
    cobre criar/editar um registro; post_delete cobre remover um
    registro que existia -- os dois invalidam o resultado calculado
    igual). Import tardio (dentro da função, não no topo do arquivo) --
    evita import circular, já que apps.py chama isso de dentro de
    ready(), quando os apps ainda podem não estar 100% carregados se
    fosse import direto no topo.
    """
    from tabelas.models import (
        TbIndicadoresDaugther, TbCambioDaugther, TbImpostoRendaDaugther,
        TbTaxaDescontoDaugther, TbCustoFixo, TbCustoFixoDaugther,
        TbDepreAmorti, TbDepreAmortiDaugther, TbCapex, TbCapexDaugther,
        TbCustoItemPrecoDaugther,
    )
    from produtos.models import TbProdutoMercadoPrecoDaugther, TbMercadoOutboundDaugther
    from equipamentos.models import (
        TbEquipamentosCadastroDaugther, TbEquipamentosDaugther,
        TbEquipamentosConsumoEspecificoDaugther,
    )
    from fluxos.models import (
        TbFluxoConsumoPadraoDaugther, TbFluxoProducaoDaugther,
        TbFluxoProducaoDaugther01, TbFluxoProducaoInputOutputDaugther,
    )
    # 🌟 NOVO: tabelas do app otimizacao -- só as "mãe" genuinamente
    # editáveis (confirmadas via admin_otimizacao.py) -- E TAMBÉM as
    # tabelas "filha" (*Daugther) desse app, que ao contrário do que eu
    # tinha assumido antes, PODEM sim ter campos genuinamente editáveis
    # (ex: TbOtimizacaoProdutoDaugther.dau_valor_1/2 -- Volume Mínimo/
    # Máximo -- não estão em readonly_fields). O "Limpar" roda como
    # stored procedure SQL pura (cursor.execute("call
    # public.limpa_cenario(...)")), completamente por fora do ORM do
    # Django -- então NUNCA dispara sinal do Django em nenhuma tabela,
    # mãe ou filha. Conectar o sinal nas filhas é seguro: só vai
    # disparar numa edição manual de verdade (via Admin), nunca durante
    # o Limpar.
    #
    # 🌟 CORRIGIDO (confirmado pelo usuário, que conhece o sistema
    # melhor do que dá pra inferir só olhando readonly_fields no
    # admin_otimizacao.py):
    # - TbOtimizacaoProduto (mãe): NÃO editável -- só a filha
    # - TbProdutoMercadoFluxo + Daugther: NENHUMA das duas é editável
    # - TbProdutoMercado + Daugther: NENHUMA das duas é editável
    # - TbOtimizacaoEquipamentos (mãe): NÃO editável -- só a filha
    # - TbOtimizacaoEquipamentosOrdem + Daugther: NENHUMA das duas é editável
    # - TbOtimizacaoConjuntoEquipamentos + Daugther: as DUAS são editáveis
    # - TbOtimizacaoCustoItem (mãe): NÃO editável -- só a filha
    # - TbConsumoEspecificoTipoProducao: fora -- é atualizada DEPOIS do
    #   cenário já estar consolidado (mesmo motivo de
    #   TbOtimizacaoComparacaoCenarios: não retroalimenta o cálculo)
    # Também ficam de fora: TbOtimizacaoShadow(Daugther) --
    # has_change_permission=False, nunca é editável por ninguém -- e
    # TbOtimizacaoComparacaoCenarios(Daugther) -- é uma comparação ENTRE
    # cenários já prontos, não retroalimenta o cálculo do cenário em si
    # (decisão explícita).
    from otimizacao.models import (
        TbOtimizacaoProdutoDaugther,
        TbOtimizacaoEquipamentosDaugther,
        TbOtimizacaoConjuntoEquipamentos, TbOtimizacaoConjuntoEquipamentosDaugther,
        TbOtimizacaoCustoItemDaugther,
    )

    modelos = [
        # tabelas -- mãe (têm valor_inicial* usado direto no cálculo)
        TbCustoFixo, TbDepreAmorti, TbCapex,
        # tabelas -- filha
        TbIndicadoresDaugther, TbCambioDaugther, TbImpostoRendaDaugther,
        TbTaxaDescontoDaugther, TbCustoFixoDaugther, TbDepreAmortiDaugther,
        TbCapexDaugther, TbCustoItemPrecoDaugther,
        # otimizacao -- só as confirmadas como editáveis (ver comentário acima)
        TbOtimizacaoProdutoDaugther,
        TbOtimizacaoEquipamentosDaugther,
        TbOtimizacaoConjuntoEquipamentos, TbOtimizacaoConjuntoEquipamentosDaugther,
        TbOtimizacaoCustoItemDaugther,
        # produtos
        TbProdutoMercadoPrecoDaugther, TbMercadoOutboundDaugther,
        # equipamentos
        TbEquipamentosCadastroDaugther, TbEquipamentosDaugther,
        TbEquipamentosConsumoEspecificoDaugther,
        # fluxos
        TbFluxoConsumoPadraoDaugther, TbFluxoProducaoDaugther,
        TbFluxoProducaoDaugther01, TbFluxoProducaoInputOutputDaugther,
    ]

    for modelo in modelos:
        # dispatch_uid evita conectar em dobro se ready() rodar mais de
        # uma vez no mesmo processo (o autoreloader do runserver, por
        # exemplo, chama ready() 2x -- ver comentário em apps.py).
        post_save.connect(
            _resetar_flag_cenario_se_necessario, sender=modelo,
            dispatch_uid=f'resetar_flag_cenario_save_{modelo.__name__}',
        )
        post_delete.connect(
            _resetar_flag_cenario_se_necessario, sender=modelo,
            dispatch_uid=f'resetar_flag_cenario_delete_{modelo.__name__}',
        )