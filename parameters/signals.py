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


# ---------------------------------------------------------------------------------------------------------------------
# 🌟 CORRIGIDO (30/10/2026): exclusão em CASCATA travava o sistema. O Django faz a cascata em Python e dispara o
# post_delete para CADA registro filho excluído -- milhares. Antes, cada um deles: buscava o registro mãe no banco (para o
# nome da tabela), lia o texto inteiro de ultimas_alteracoes, acrescentava uma linha e regravava o texto inteiro (que só
# crescia: milhares de linhas regravadas milhares de vezes) -- na prática não terminava.
# Agora as alterações ficam guardadas em memória durante a transação e são gravadas UMA vez, no fim (on_commit):
#   - um único UPDATE por cenário (flag = 0 e o histórico);
#   - exclusões: UMA linha por tabela com a quantidade ("1.234 registros excluídos"), sem consultar o registro mãe;
#   - inclusões/alterações: uma linha por registro (com o link), como antes; mais de MAX_LINHAS_POR_TABELA na mesma
#     tabela viram uma linha só com a quantidade.
# Fora de transação (salvar um registro na tela), grava na hora, como antes.
# ---------------------------------------------------------------------------------------------------------------------
import threading
from collections import OrderedDict

from django.db import transaction

MAX_LINHAS_POR_TABELA = 20
_pendente = threading.local()


def _nome_tabela_sem_consulta(sender):
    """Nome da tabela para o histórico sem ir ao banco: "Mãe/Filha" pelo modelo do campo mae (se existir)."""
    nome_proprio = str(sender._meta.verbose_name) or sender.__name__
    try:
        campo_mae = sender._meta.get_field('mae')
        return f"{campo_mae.related_model._meta.verbose_name}/{nome_proprio}"
    except Exception:
        return nome_proprio


def _nome_app(sender):
    try:
        return str(apps.get_app_config(sender._meta.app_label).verbose_name).strip()
    except LookupError:
        return sender._meta.app_label


def _gravar(cenario_id, linhas_novas):
    """Acrescenta as linhas no histórico do cenário e, se ele ainda não estava ALTERADO, reseta o flag (uma vez só)."""
    if not linhas_novas:
        return
    cenario = TbCenarios.objects_real.filter(id=cenario_id).only('id', 'flag', 'ultimas_alteracoes').first()
    if cenario is None:
        return
    historico_atual = cenario.ultimas_alteracoes or ''
    novo = '\n'.join(linhas_novas)
    historico_novo = f"{historico_atual}\n{novo}" if historico_atual else novo
    if cenario.flag == 0:
        TbCenarios.objects_real.filter(id=cenario_id).update(ultimas_alteracoes=historico_novo)
        return
    TbCenarios.objects_real.filter(id=cenario_id).update(flag=0, ultimas_alteracoes=historico_novo)
    TbCenariosDaugther.objects.filter(mae_id=cenario_id).update(
        flag=_FLAG_ALTERADO_CENARIOS_DAUGTHER, **_CAMPOS_VALOR_CENARIOS_DAUGTHER
    )


def _descarregar():
    """No fim da transação: monta as linhas (exclusões resumidas por tabela) e grava uma vez por cenário."""
    buffer = getattr(_pendente, 'buffer', None)
    _pendente.buffer = None
    if not buffer:
        return
    agora = timezone.now().strftime('%d/%m/%Y %H:%M:%S')
    for cenario_id, por_tabela in buffer.items():
        linhas = []
        for (usuario, app, tabela, eh_delete), itens in por_tabela.items():
            if eh_delete:
                q = len(itens)
                linhas.append(f"{agora} - {usuario} - {app} - {tabela} - "
                              + ('registro excluído' if q == 1 else f"{q:,} registros excluídos".replace(',', '.')))
            elif len(itens) > MAX_LINHAS_POR_TABELA:
                linhas.append(f"{agora} - {usuario} - {app} - {tabela} - "
                              + f"{len(itens):,} registros incluídos/alterados".replace(',', '.'))
            else:
                linhas.extend(itens)
        try:
            _gravar(cenario_id, linhas)
        except Exception as erro:          # o histórico nunca pode derrubar a exclusão/gravação
            print(f"[signals] não consegui gravar o histórico do cenário {cenario_id}: {erro}")


def _resetar_flag_cenario_se_necessario(sender, instance, **kwargs):
    """
    Handler compartilhado por todas as tabelas conectadas (ver conectar_sinais, abaixo). Cada uma delas tem um campo
    tbcenarios_id direto. Registra a alteração no histórico (TbCenarios.ultimas_alteracoes) e deixa o cenário ALTERADO
    (flag = 0, com os valores calculados de TbCenariosDaugther zerados) -- dentro de uma transação, só no fim dela.
    """
    cenario_id = getattr(instance, 'tbcenarios_id', None)
    if cenario_id is None:
        return
    eh_delete = kwargs.get('signal') is post_delete

    conexao = transaction.get_connection()
    if not conexao.in_atomic_block:
        # fora de transação (ex.: salvar um registro): grava na hora, uma linha, como antes
        try:
            _gravar(cenario_id, [_montar_linha_historico(sender, instance, **kwargs)])
        except Exception as erro:
            print(f"[signals] não consegui gravar o histórico do cenário {cenario_id}: {erro}")
        return

    buffer = getattr(_pendente, 'buffer', None)
    # transação anterior desfeita (rollback): o on_commit dela sumiu da fila -- o que estava guardado não vale mais
    agendado = any(len(item) > 1 and item[1] is _descarregar for item in getattr(conexao, 'run_on_commit', []))
    if buffer is None or not agendado:
        buffer = _pendente.buffer = OrderedDict()
        transaction.on_commit(_descarregar)
    usuario = get_usuario_atual()
    nome_usuario = usuario.get_username() if usuario is not None else 'Sistema'
    if eh_delete:
        chave = (nome_usuario, _nome_app(sender), _nome_tabela_sem_consulta(sender), True)
        buffer.setdefault(cenario_id, OrderedDict()).setdefault(chave, []).append(None)
    else:
        chave = (nome_usuario, _nome_app(sender), _nome_tabela_sem_consulta(sender), False)
        lista = buffer.setdefault(cenario_id, OrderedDict()).setdefault(chave, [])
        if len(lista) < MAX_LINHAS_POR_TABELA:
            lista.append(_montar_linha_historico(sender, instance, **kwargs))
        else:
            lista.append(None)          # só conta: vai virar a linha resumida


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
    # 🌟 30/10/2026: confirmação de exclusão do Admin só com o resumo (ver parameters/exclusao_resumo.py)
    try:
        from .exclusao_resumo import instalar as _instalar_resumo_exclusao
        _instalar_resumo_exclusao()
    except Exception as erro:
        print(f"[signals] resumo de exclusão não instalado: {erro}")
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