"""
Fluxo guiado (wizard) para criar um TbCenarios através de conversa no chat
do Agente IA, em vez de preencher o formulário do Admin.

Uso esperado, a partir de agents.py:

    from .fluxo_criar_cenario import (
        usuario_esta_em_fluxo, iniciar_fluxo_criar_cenario, processar_mensagem_fluxo
    )

    if usuario_esta_em_fluxo(request.user):
        resposta = processar_mensagem_fluxo(request.user, mensagem_usuario)
    elif <mensagem indica intenção de criar cenário>:
        resposta = iniciar_fluxo_criar_cenario(request.user)
    else:
        resposta = <fluxo normal do LLM>

Em qualquer etapa, o usuário pode digitar "cancelar" para abortar o wizard
sem criar nada.
"""

"""
Fluxos guiados (wizards) através de conversa no chat do Agente IA, em vez de
usar o formulário do Admin. Hoje tem dois:

  - 'criar_cenario': cria um TbCenarios novo, acompanha a duplicação e
    opcionalmente roda Limpar -> Otimizar -> Consolidar, com comparação
    final dos resultados.
  - 'mudar_cenario': muda o tipo (Mensal/Trimestral/Anual) e/ou o período
    do cenário ATIVO do usuário.

Uso esperado, a partir de agents.py:

    from .fluxo_criar_cenario import (
        usuario_esta_em_fluxo, iniciar_fluxo_criar_cenario,
        iniciar_fluxo_mudar_cenario, processar_mensagem_fluxo
    )

    if usuario_esta_em_fluxo(request.user):
        resposta = processar_mensagem_fluxo(request.user, mensagem_usuario)
    elif <mensagem indica intenção de criar cenário>:
        resposta = iniciar_fluxo_criar_cenario(request.user)
    elif <mensagem indica intenção de mudar tipo/período>:
        resposta = iniciar_fluxo_mudar_cenario(request.user)
    else:
        resposta = <fluxo normal do LLM>

Em qualquer etapa, de qualquer fluxo, o usuário pode digitar "cancelar" para
abortar sem aplicar nada.
"""

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import re

from .models import EstadoConversaAgente, TbCenarios, TbCenariosDaugther, PerfilUsuario
from tabelas.models import TbGrupoCenarios

FLUXO_CRIAR = 'criar_cenario'
FLUXO_MUDAR = 'mudar_cenario'
FLUXO_INDICADORES = 'indicadores'

# Status (TbCenarios.flag) que indicam uma operação em andamento -- não faz
# sentido mudar tipo/período de um cenário que está sendo limpo, otimizado,
# consolidado, ou tendo os fluxos atualizados nesse exato momento.
FLAGS_OPERACAO_EM_ANDAMENTO = {4: 'OTIMIZANDO', 5: 'LIMPANDO', 6: 'CONSOLIDANDO', 7: 'ATUALIZANDO FLUXOS'}

PALAVRAS_CANCELAR = ('cancelar', 'cancela', 'sair', 'parar', 'desistir')
PALAVRAS_MANTER = ('manter', 'mesmo', 'mesma', 'igual', 'não mudar', 'nao mudar')

# 🌟 NOVO: se um fluxo ficar parado por mais que isso, sem nenhuma
# mensagem, a próxima mensagem recebida encerra ele automaticamente em
# vez de tratá-la como resposta a uma etapa antiga.
MINUTOS_EXPIRACAO_FLUXO = 15

# 🌟 NOVO: etapas de "aguardando" (checagem de status de task em segundo
# plano) ficam ISENTAS da expiração acima. Diferente de uma etapa que
# espera um dado específico (nome, período, valor -- onde uma mensagem
# velha e sem relação poderia ser mal-interpretada como resposta), essas
# etapas só reagem a QUALQUER mensagem checando o status de novo -- não
# tem risco de confusão, então não faz sentido elas expirarem rápido.
# Sem isso, um ciclo completo (limpar->otimizar->consolidar) que demora
# mais de 15 minutos entre uma checagem e outra "esquece" que devia
# continuar sozinho pra próxima etapa.
ETAPAS_SEM_EXPIRACAO = {
    'aguardando_duplicacao', 'aguardando_limpeza', 'aguardando_otimizacao',
    'aguardando_consolidacao', 'aguardando_verificacao_filhas',
    'proc_aguardando_limpeza', 'proc_aguardando_otimizacao', 'proc_aguardando_consolidacao',
    'proc_ciclo_aguardando_limpeza', 'proc_ciclo_aguardando_otimizacao', 'proc_ciclo_aguardando_consolidacao',
}


def usuario_esta_em_fluxo(usuario):
    """True se o usuário tem QUALQUER um dos wizards em andamento agora."""
    return EstadoConversaAgente.objects.filter(usuario=usuario).exclude(fluxo_ativo__isnull=True).exists()


def _get_estado(usuario):
    estado, _ = EstadoConversaAgente.objects.get_or_create(usuario=usuario)
    return estado


def _encerrar_fluxo(estado):
    estado.fluxo_ativo = None
    estado.etapa_atual = None
    estado.dados_coletados = {}
    estado.save()


def cancelar_fluxo_ativo(usuario):
    """
    Encerra qualquer fluxo em andamento do usuário, se houver. Usado em
    agents.py quando uma mensagem nova bate com um comando reconhecido
    (criar cenário, mexer em indicador, etc.) MESMO com outro fluxo já em
    andamento -- em vez de a mensagem nova ser engolida como se fosse
    resposta à pergunta antiga (ex: clicar "criar um cenário novo" enquanto
    o wizard de indicadores ainda esperava um nome), o comando reconhecido
    tem prioridade e cancela o fluxo velho primeiro.
    """
    estado = _get_estado(usuario)
    if estado.fluxo_ativo:
        _encerrar_fluxo(estado)


def iniciar_fluxo_criar_cenario(usuario):
    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CRIAR
    estado.etapa_atual = 'nome'
    estado.dados_coletados = {}
    estado.save()
    return (
        "Vamos criar um novo cenário! 🎬\n\n"
        "Qual vai ser o **nome** do cenário? (a qualquer momento, clique em "
        "\"cancelar\" para desistir)"
    )


def iniciar_fluxo_mudar_cenario(usuario, mensagem=""):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes de tentar mudar o tipo/período."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais. Acesse a tela de Cenários e ative outro antes de continuar."

    if cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        return (
            f"O cenário {cenario.id}/{cenario.cen_nome} está com uma operação em andamento "
            f"agora ({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}) -- espera terminar antes de "
            "mudar o tipo ou o período dele."
        )

    # 🌟 NOVO: se a mensagem que disparou o fluxo menciona só "período" (sem
    # "tipo"), pula direto pra pergunta de período, mantendo o tipo atual --
    # evita perguntar uma coisa que a pessoa deixou claro que não quer mudar.
    tem_tipo = bool(re.search(r'\btipo\b', mensagem, re.IGNORECASE))
    tem_periodo = bool(re.search(r'per[ií]odo', mensagem, re.IGNORECASE))
    pular_tipo = tem_periodo and not tem_tipo

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_MUDAR
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'cenario_nome': cenario.cen_nome,
        'tipo_atual': cenario.cen_tipo,
        'inicio_atual': cenario.cen_inicio,
        'fim_atual': cenario.cen_fim,
    }

    if pular_tipo:
        dados = estado.dados_coletados
        dados['tipo_novo'] = cenario.cen_tipo
        estado.dados_coletados = dados
        estado.etapa_atual = 'periodo_inicio'
        estado.save()
        exemplo = {'Mensal': '2026/06', 'Trimestral': '2026/02', 'Anual': '2026'}[cenario.cen_tipo]
        return (
            f"Vamos mudar o período do cenário **{cenario.id}/{cenario.cen_nome}** 🔧 "
            f"(mantendo o tipo **{cenario.cen_tipo}**)\n\n"
            f"Período atual: **{cenario.cen_inicio}** a **{cenario.cen_fim}**\n\n"
            f"Qual o novo **início** do período? (formato {exemplo}, ou \"cancelar\" para desistir)"
        )

    estado.etapa_atual = 'tipo'
    estado.save()
    return (
        f"Vamos mudar o cenário **{cenario.id}/{cenario.cen_nome}** 🔧\n\n"
        f"Tipo atual: **{cenario.cen_tipo}**\n"
        f"Período atual: **{cenario.cen_inicio}** a **{cenario.cen_fim}**\n\n"
        "Para qual **tipo** você quer mudar? (Mensal / Trimestral / Anual, "
        "\"manter\" para deixar como está, ou \"cancelar\" para desistir)"
    )


def processar_mensagem_fluxo(usuario, mensagem):
    # 🌟 select_for_update() + transaction.atomic() -- se duas mensagens do
    # mesmo usuário chegarem quase ao mesmo tempo (duplo clique, reenvio, ou
    # qualquer outra concorrência), a segunda fica esperando a primeira
    # terminar de processar e salvar, em vez de ler o estado "no meio" da
    # transição e se atropelarem.
    with transaction.atomic():
        estado, _ = EstadoConversaAgente.objects.select_for_update().get_or_create(usuario=usuario)
        return _processar_mensagem_fluxo_com_lock(estado, mensagem)


def _processar_mensagem_fluxo_com_lock(estado, mensagem):
    texto = (mensagem or '').strip()

    # 🌟 NOVO: se o fluxo ficou parado por muito tempo sem nenhuma
    # mensagem (esquecido no meio de uma etapa de espera, por exemplo),
    # encerra sozinho em vez de interpretar a mensagem nova como resposta
    # a uma pergunta antiga -- evita o "estado preso" que já causou
    # respostas sem nexo antes (ex: uma pergunta sobre um indicador sendo
    # respondida como se fosse confirmação de uma mudança de cenário de
    # dias atrás).
    if (
        estado.fluxo_ativo
        and estado.etapa_atual not in ETAPAS_SEM_EXPIRACAO
        and (timezone.now() - estado.atualizado_em) > timedelta(minutes=MINUTOS_EXPIRACAO_FLUXO)
    ):
        _encerrar_fluxo(estado)
        return (
            "O fluxo anterior ficou parado por um tempo e expirou automaticamente, pra não misturar "
            "com o que você está pedindo agora. Pode repetir o pedido, começando do zero."
        )

    if texto.lower() in PALAVRAS_CANCELAR:
        _encerrar_fluxo(estado)
        return "Ok, cancelei. Nada foi alterado."

    if estado.fluxo_ativo == FLUXO_CRIAR:
        return _processar_criar_cenario(estado, texto)
    elif estado.fluxo_ativo == FLUXO_MUDAR:
        return _processar_mudar_cenario(estado, texto)
    elif estado.fluxo_ativo == FLUXO_INDICADORES:
        return _processar_indicadores(estado, texto)
    elif estado.fluxo_ativo == FLUXO_CAMBIO:
        return _processar_cambio(estado, texto)
    elif estado.fluxo_ativo == FLUXO_PROCESSAR:
        return _processar_fluxo_processar(estado, texto)

    # Estado inconsistente (não deveria acontecer) -- encerra por segurança
    _encerrar_fluxo(estado)
    return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."


def _processar_criar_cenario(estado, texto):
    # 🌟 se essa etapa já trabalha em cima de um cenário criado (etapas
    # depois da confirmação), confere se ele ainda existe de verdade antes
    # de continuar. Se alguém excluir o cenário no meio do acompanhamento
    # (duplicação/limpeza/otimização/consolidação), o fluxo ficaria preso
    # pra sempre nessa etapa, interpretando qualquer mensagem nova
    # (inclusive um pedido de criar outro cenário) como resposta a uma
    # pergunta sobre um cenário que não existe mais.
    cenario_id = estado.dados_coletados.get('cenario_criado_id')
    if cenario_id is not None and not TbCenarios.objects_real.filter(id=cenario_id).exists():
        cenario_nome = estado.dados_coletados.get('cenario_criado_nome', '')
        _encerrar_fluxo(estado)
        return (
            f"O cenário {cenario_id}/{cenario_nome} que eu estava acompanhando não existe "
            "mais (foi excluído). Cancelei o acompanhamento automático aqui. Se quiser "
            "criar um cenário novo, é só pedir de novo."
        )

    etapa = estado.etapa_atual
    handler = _HANDLERS_CRIAR.get(etapa)
    if handler is None:
        # Estado inconsistente (não deveria acontecer) -- encerra por segurança
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."

    return handler(estado, texto)


# ---------------------------------------------------------------------
# Etapa: nome
# ---------------------------------------------------------------------
def _etapa_nome(estado, texto):
    nome = texto.strip().upper()
    if not nome:
        return "Preciso de um nome pra continuar. Qual vai ser o nome do cenário?"

    if TbCenarios.objects.filter(cen_nome=nome).exists():
        return (
            f"Já existe um cenário chamado **{nome}**. Escolhe outro nome, "
            "ou clique em \"cancelar\" pra desistir."
        )

    dados = estado.dados_coletados
    dados['cen_nome'] = nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'descricao'
    estado.save()
    return f"Nome definido: **{nome}**.\n\nAgora, uma **descrição** pra esse cenário (pode ser breve):"


# ---------------------------------------------------------------------
# Etapa: descrição
# ---------------------------------------------------------------------
def _etapa_descricao(estado, texto):
    if not texto:
        return "Preciso de uma descrição, mesmo que curta. Qual seria?"

    dados = estado.dados_coletados
    dados['cen_descricao'] = texto
    estado.dados_coletados = dados
    estado.etapa_atual = 'copiar_de'
    estado.save()

    ultimos = TbCenarios.objects_real.order_by('-id')[:8]
    lista = "\n".join(f"- {c.id}: {c.cen_nome}" for c in ultimos)
    return (
        "Descrição registrada.\n\n"
        "De **qual cenário existente** você quer copiar (tipo, período e demais dados iniciais)? "
        "Pode me dizer o **id** ou o **nome**. Alguns cenários recentes:\n"
        f"{lista}"
    )


# ---------------------------------------------------------------------
# Etapa: copiar_de
# ---------------------------------------------------------------------
def _etapa_copiar_de(estado, texto):
    cenario_origem = _resolver_cenario(texto)
    if cenario_origem is None:
        return (
            f"Não encontrei nenhum cenário correspondente a \"{texto}\". "
            "Tenta de novo com o id ou o nome exato, ou \"cancelar\"."
        )

    dados = estado.dados_coletados
    dados['cen_copiar_de_id'] = cenario_origem.id
    dados['cen_copiar_de_nome'] = cenario_origem.cen_nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'grupo'
    estado.save()

    grupos = TbGrupoCenarios.objects.all()[:8]
    if grupos:
        lista = "\n".join(f"- {g.id}: {g}" for g in grupos)
        texto_grupos = f"Alguns grupos existentes:\n{lista}\n\n"
    else:
        texto_grupos = ""

    return (
        f"Vai copiar de **{cenario_origem.id}/{cenario_origem.cen_nome}**.\n\n"
        f"{texto_grupos}"
        "Qual **grupo** esse cenário deve ficar? Pode me dizer o nome de um grupo "
        "existente, o nome de um **grupo novo** (eu crio pra você), ou \"nenhum\" "
        "pra deixar sem grupo."
    )


def _resolver_cenario(texto):
    texto = texto.strip()
    if texto.isdigit():
        return TbCenarios.objects_real.filter(id=int(texto)).first()
    return TbCenarios.objects_real.filter(cen_nome__iexact=texto).first() \
        or TbCenarios.objects_real.filter(cen_nome__icontains=texto).first()


# ---------------------------------------------------------------------
# Etapa: grupo
# ---------------------------------------------------------------------
def _etapa_grupo(estado, texto):
    dados = estado.dados_coletados

    if texto.strip().lower() in ('nenhum', 'nao', 'não', 'sem grupo'):
        dados['cen_grupo_id'] = None
        dados['cen_grupo_nome'] = None
    else:
        grupo = None
        if texto.strip().isdigit():
            grupo = TbGrupoCenarios.objects.filter(id=int(texto.strip())).first()
        if grupo is None:
            grupo = _buscar_grupo_por_nome(texto.strip())
        if grupo is None:
            # Não encontrou -- cria um grupo novo com esse nome
            grupo = _criar_grupo(texto.strip())

        dados['cen_grupo_id'] = grupo.id
        dados['cen_grupo_nome'] = str(grupo)

    estado.dados_coletados = dados
    estado.etapa_atual = 'confirmar'
    estado.save()

    resumo = _montar_resumo(dados)
    return (
        f"{resumo}\n\n"
        "Confirma a criação desse cenário? (**sim** / **não**)"
    )


CAMPO_NOME_GRUPO = 'gru_cen_codigo'  # confirmado com o usuário -- nome do campo em TbGrupoCenarios


def _buscar_grupo_por_nome(nome):
    return TbGrupoCenarios.objects.filter(**{f"{CAMPO_NOME_GRUPO}__iexact": nome}).first()


def _criar_grupo(nome):
    return TbGrupoCenarios.objects.create(**{CAMPO_NOME_GRUPO: nome})


# ---------------------------------------------------------------------
# Etapa: confirmar
# ---------------------------------------------------------------------
def _etapa_confirmar(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        if resposta in ('nao', 'não', 'n', 'no'):
            _encerrar_fluxo(estado)
            return "Ok, não criei o cenário. Se quiser começar de novo, é só pedir."
        return "Não entendi. Confirma a criação? (**sim** / **não**)"

    dados = estado.dados_coletados
    try:
        cenario = TbCenarios(
            cen_nome=dados['cen_nome'],
            cen_descricao=dados['cen_descricao'],
            cen_copiar_de_id=dados['cen_copiar_de_id'],
            cen_grupo_id=dados.get('cen_grupo_id'),
        )
        cenario.full_clean(exclude=['cen_tipo', 'cen_inicio', 'cen_fim', 'flag'])
        # 🌟 CORRIGIDO: captura o momento ANTES do save(), não depois --
        # o save() já dispara a duplicação internamente (via on_commit, que
        # roda de forma praticamente imediata), então capturar o timestamp
        # DEPOIS do save() podia ficar um instante à frente do TaskResult
        # de verdade, fazendo a busca "desde este momento" excluir a task
        # que já tinha sido criada um instante antes.
        momento_criacao = timezone.now().isoformat()
        cenario.save()
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao criar o cenário: {e}. Cancelei o fluxo -- pode tentar de novo."

    dados['cenario_criado_id'] = cenario.id
    dados['cenario_criado_nome'] = cenario.cen_nome
    dados['momento_criacao'] = momento_criacao
    estado.dados_coletados = dados
    estado.etapa_atual = 'ativar'
    estado.save()

    return (
        f"Cenário **{cenario.id}/{cenario.cen_nome}** criado! 🎉 A duplicação dos dados "
        "está rodando em segundo plano, pode levar alguns instantes.\n\n"
        "Quer que eu já **ative esse cenário pra você**? (**sim** / **não**)"
    )


# ---------------------------------------------------------------------
# Etapa: ativar
# ---------------------------------------------------------------------
def _etapa_ativar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')

    if resposta in ('sim', 's', 'yes', 'y'):
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=estado.usuario)
        perfil.cenario_ativo_id = cenario_id
        perfil.save()
        mensagem_ativacao = f"Pronto, o cenário **{cenario_id}/{cenario_nome}** agora é o seu cenário ativo.\n\n"
    else:
        mensagem_ativacao = f"Ok, o cenário **{cenario_id}/{cenario_nome}** foi criado mas não ativado pra você.\n\n"

    estado.etapa_atual = 'aguardando_duplicacao'
    estado.save()

    return mensagem_ativacao + _checar_duplicacao(estado)


# ---------------------------------------------------------------------
# Etapa: aguardando_duplicacao -- checa se a duplicação das tabelas (que
# roda em background via Celery, disparada de dentro do TbCenarios.save())
# já terminou. Qualquer mensagem do usuário nessa etapa dispara uma nova
# checagem -- não é uma pergunta de verdade, é só "toca aqui pra eu olhar
# de novo".
# ---------------------------------------------------------------------
def _celery_esta_ativo():
    try:
        from celery import current_app
        respostas = current_app.control.ping(timeout=1.0)
        return bool(respostas)
    except Exception:
        return False


def _buscar_task_duplicacao(desde_iso):
    try:
        from django_celery_results.models import TaskResult
        from django.utils.dateparse import parse_datetime
        import datetime
    except Exception:
        return None

    desde = parse_datetime(desde_iso)
    if desde is not None:
        # 🌟 Margem de segurança de 2s pra absorver qualquer diferença
        # residual de precisão entre o timezone.now() capturado no wizard
        # e o date_created gravado pelo Celery/django-celery-results.
        desde = desde - datetime.timedelta(seconds=2)
    else:
        desde = desde_iso

    return TaskResult.objects.filter(
        task_name__icontains='duplica_tabela_celery',
        date_created__gte=desde,
    ).order_by('date_created').first()


def _checar_duplicacao(estado):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    momento_criacao = dados.get('momento_criacao')

    if not _celery_esta_ativo():
        return (
            "⚠️ Não consegui detectar nenhum worker do Celery ativo agora -- a "
            "duplicação das tabelas do cenário não vai acontecer sozinha até "
            "alguém ligar o Celery. Assim que estiver rodando, clica em \"Verificar\" que eu confiro de novo."
        )

    task = _buscar_task_duplicacao(momento_criacao)
    if task is None:
        return (
            "O Celery está ativo, mas ainda não encontrei o registro da "
            "duplicação desse cenário -- pode ser que tenha começado e está duplicando as tabelas. "
            "Clica em \"Verificar\" em alguns segundos que eu confiro de novo."
        )

    if task.status == 'SUCCESS':
        estado.etapa_atual = 'confirmar_processar'
        estado.save()
        return (
            f"✅ Duplicação das tabelas do cenário **{cenario_id}/{cenario_nome}** concluída!\n\n"
            "Quer que eu já rode o ciclo completo -- **Limpar → Otimizar → Consolidar** -- "
            "e te mostre uma comparação dos resultados com o cenário de origem? (**sim** / **não**)"
        )

    if task.status == 'FAILURE':
        _encerrar_fluxo(estado)
        return (
            f"❌ A duplicação das tabelas do cenário **{cenario_id}/{cenario_nome}** falhou "
            f"(status: {task.status}). O cenário foi criado, mas os dados não foram "
            "duplicados -- vale olhar o log do Celery pra entender o motivo. Cancelei o fluxo aqui."
        )

    # PENDING, STARTED, RETRY, etc. -- ainda rodando
    return (
        f"Ainda duplicando as tabelas do cenário **{cenario_id}/{cenario_nome}** "
        f"(status atual: {task.status}). Clica em \"Verificar\" daqui a pouco "
        "que eu confiro de novo."
    )


def _etapa_aguardando_duplicacao(estado, texto):
    return _checar_duplicacao(estado)


def _etapa_confirmar_processar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, não vou processar o cenário **{cenario_id}/{cenario_nome}** agora. Ele já está criado, pode processar manualmente quando quiser."

    from fluxos.models import TbFluxoProducaoDaugther01, TbFluxoProducao
    from django.db import connection

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        _encerrar_fluxo(estado)
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=cenario_id, flu_pro_ativo=True).count() > 0:
        _encerrar_fluxo(estado)
        return "Tem fluxo(s) de produção ativo(s) com I/O desatualizado. Usa \"Atualizar Fluxos\" no Admin antes de tentar de novo."

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        _encerrar_fluxo(estado)
        return "Tem fluxo(s) de produção ativo(s) com custo variável zerado. Verifica isso no Admin antes de tentar de novo."

    from .tasks import limpar_cenario_celery
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 5 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    limpar_cenario_celery.delay(cenario_id)

    estado.etapa_atual = 'aguardando_limpeza'
    estado.save()
    return (
        f"Beleza, disparei a **limpeza** do cenário **{cenario_id}/{cenario_nome}** em segundo plano. "
        "Clica em \"Verificar\" daqui a pouco que eu confiro se já terminou."
    )


# ---------------------------------------------------------------------
# Etapas de espera do ciclo Limpar -> Otimizar -> Consolidar. Cada uma
# funciona igual à de espera da duplicação: qualquer mensagem do usuário
# dispara uma nova checagem do status atual (via TbCenarios.flag).
# ---------------------------------------------------------------------
def _etapa_aguardando_limpeza(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    cenario = TbCenarios.objects_real.get(id=cenario_id)

    if cenario.flag == 2:  # LIMPO
        return _disparar_otimizacao(estado, cenario)

    if cenario.flag == 5:  # ainda limpando
        return f"Ainda limpando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin. Cancelei o acompanhamento automático aqui."


def _disparar_otimizacao(estado, cenario):
    from django.db import connection
    from .tasks import otimizar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 4 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 4 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)

    sql = "select conta_periodos(" + str(cenario_id) + ")"
    cursor.execute(sql)
    total_periodos = cursor.fetchone()[0]
    cursor.close()

    from otimizacao.models import TbProdutoMercadoFluxo
    total_variaveis = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cenario_id, flag=True).count()

    for i in range(total_periodos):
        if TbCenariosDaugther.objects.get(mae_id=cenario_id, dau_order=i + 1).otimizar:
            otimizar_cenario_celery.delay(i, cenario_id, total_variaveis)

    dados = estado.dados_coletados
    dados['total_periodos'] = total_periodos
    estado.dados_coletados = dados
    estado.etapa_atual = 'aguardando_otimizacao'
    estado.save()

    return (
        f"Limpeza concluída! Disparei a **otimização** do cenário **{cenario_id}/{cenario.cen_nome}** "
        f"({total_periodos} período(s)) em segundo plano. Clica em \"Verificar\" daqui a pouco "
        "que eu confiro o progresso."
    )


def _etapa_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    total_periodos = dados.get('total_periodos', 0)

    # Mesma lógica do botão "Status Otimização" já usado no Admin: conta
    # quantos períodos já têm resultado (flag 1 ou 0), não confia só no
    # flag do TbCenarios pra saber se todo mundo terminou.
    total_otimizado = (
        TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=1).count()
        + TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=0).count()
    )

    if total_otimizado < total_periodos:
        return (
            f"Ainda otimizando o cenário **{cenario_id}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} períodos concluídos). "
            "Clica em \"Verificar\" daqui a pouco."
        )

    cenario = TbCenarios.objects_real.get(id=cenario_id)
    if cenario.flag != 3:
        # Todos os períodos já processaram, mas o status geral do cenário
        # ainda não virou "OTIMIZADO" -- dá uma folga e confere de novo.
        return f"Períodos todos processados, aguardando o cenário fechar como OTIMIZADO. Clica em \"Verificar\" em instantes."

    from django.db import connection
    from .tasks import consolidar_cenario_celery

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 6 where id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    consolidar_cenario_celery.delay(cenario_id)

    estado.etapa_atual = 'aguardando_consolidacao'
    estado.save()
    return (
        f"Otimização concluída! Disparei a **consolidação** do cenário **{cenario_id}/{cenario_nome}** "
        "em segundo plano. Clica em \"Verificar\" daqui a pouco."
    )


def _etapa_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    cenario = TbCenarios.objects_real.get(id=cenario_id)

    if cenario.flag == 1:  # CONSOLIDADO
        resposta = _montar_tabela_comparacao(cenario_id, dados.get('cen_copiar_de_id'))
        _encerrar_fluxo(estado)
        return (
            f"✅ Cenário **{cenario_id}/{cenario_nome}** consolidado! Ciclo completo.\n\n{resposta}"
        )

    if cenario.flag == 6:  # ainda consolidando
        return f"Ainda consolidando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin."


# ---------------------------------------------------------------------
# Comparação final: dau_valor_1..20 por dau_order, cenário novo vs origem
# ---------------------------------------------------------------------
NOMES_CAMPOS = {
    1: 'Vendas', 2: 'Variável', 3: 'Inbound', 4: 'Outbound', 5: 'Manut.',
    6: 'Margem', 7: 'Fixo', 8: 'EBTIDA', 9: 'EBTIDA (%)', 10: 'D&A',
    11: 'IR (%)', 12: 'MP', 13: 'WIP', 14: 'PF', 15: 'Total Est.',
    16: 'Receber', 17: 'Pagar', 18: 'OWCR', 19: 'CAPEX', 20: 'OFCF',
}


def _montar_tabela_comparacao(cenario_novo_id, cenario_origem_id):
    filhas_novo = {f.dau_order: f for f in TbCenariosDaugther.objects.filter(mae_id=cenario_novo_id)}
    filhas_origem = {f.dau_order: f for f in TbCenariosDaugther.objects.filter(mae_id=cenario_origem_id)}

    ordens = sorted(set(filhas_novo.keys()) & set(filhas_origem.keys()))
    if not ordens:
        return "Não encontrei períodos em comum entre os dois cenários pra comparar."

    linhas = ["Comparação (novo vs. origem) -- diferenças por período:\n"]
    algo_diferente = False
    for order in ordens:
        fn = filhas_novo[order]
        fo = filhas_origem[order]
        diffs = []
        for campo_num, nome in NOMES_CAMPOS.items():
            valor_novo = getattr(fn, f'dau_valor_{campo_num}')
            valor_origem = getattr(fo, f'dau_valor_{campo_num}')
            if valor_novo != valor_origem:
                diffs.append(f"{nome}: {valor_origem} → {valor_novo}")
        if diffs:
            algo_diferente = True
            linhas.append(f"**Período {order}**: " + "; ".join(diffs))

    if not algo_diferente:
        linhas.append("Nenhuma diferença encontrada -- os valores ficaram idênticos ao cenário de origem, em todos os períodos.")

    return "\n".join(linhas)


def _montar_resumo(dados):
    linhas = [
        "Resumo do cenário a criar:",
        f"- Nome: {dados.get('cen_nome')}",
        f"- Descrição: {dados.get('cen_descricao')}",
        f"- Copiar de: {dados.get('cen_copiar_de_id')}/{dados.get('cen_copiar_de_nome')}",
        f"- Grupo: {dados.get('cen_grupo_nome') or 'nenhum'}",
    ]
    return "\n".join(linhas)


_HANDLERS_CRIAR = {
    'nome': _etapa_nome,
    'descricao': _etapa_descricao,
    'copiar_de': _etapa_copiar_de,
    'grupo': _etapa_grupo,
    'confirmar': _etapa_confirmar,
    'ativar': _etapa_ativar,
    'aguardando_duplicacao': _etapa_aguardando_duplicacao,
    'confirmar_processar': _etapa_confirmar_processar,
    'aguardando_limpeza': _etapa_aguardando_limpeza,
    'aguardando_otimizacao': _etapa_aguardando_otimizacao,
    'aguardando_consolidacao': _etapa_aguardando_consolidacao,
}


# =======================================================================
# Fluxo: mudar_cenario -- mudar tipo (Mensal/Trimestral/Anual) e/ou
# período do cenário ATIVO do usuário. A lógica de ajustar as filhas
# (apagar dau_order sobrando, criar as que faltam copiando do último) já
# existe em TbCenarios.save() -- aqui só coletamos os dados novos,
# validamos o formato/faixa (mesma regra do clean() do model, checada
# aqui também só pra dar erro amigável na hora, sem precisar bater no
# banco) e chamamos save() normalmente.
# =======================================================================

def _processar_mudar_cenario(estado, texto):
    cenario_id = estado.dados_coletados.get('cenario_id')
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first() if cenario_id else None
    if cenario is None:
        _encerrar_fluxo(estado)
        return "O cenário que eu estava alterando não existe mais. Cancelei o fluxo aqui."

    # 🌟 Trava: não deixa mudar tipo/período se o cenário entrou em
    # operação (limpando/otimizando/consolidando/atualizando fluxos)
    # enquanto o usuário respondia as perguntas.
    if cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        _encerrar_fluxo(estado)
        return (
            f"O cenário {cenario.id}/{cenario.cen_nome} entrou em operação "
            f"({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}) enquanto conversávamos -- "
            "cancelei a mudança de tipo/período aqui, por segurança."
        )

    etapa = estado.etapa_atual
    handler = _HANDLERS_MUDAR.get(etapa)
    if handler is None:
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."

    return handler(estado, texto, cenario)


def _validar_periodo(tipo, texto):
    """
    Retorna (periodo_formatado, mensagem_erro). Se mensagem_erro is None,
    a validação passou e periodo_formatado é a string pronta pra gravar
    em cen_inicio/cen_fim (sempre 7 caracteres, formato AAAA/NN) -- mesma
    faixa de valores que TbCenarios.clean() já valida, checada aqui só
    pra dar um erro amigável na hora, sem precisar bater no banco.
    """
    texto = texto.strip()

    if tipo == 'Anual':
        m = re.fullmatch(r'\d{4}', texto)
        if not m:
            return None, "Informa só o ano, no formato AAAA (ex: 2026)."
        return f"{texto}/01", None

    m = re.fullmatch(r'(\d{4})/(\d{1,2})', texto)
    if not m:
        exemplo = "2026/06" if tipo == 'Mensal' else "2026/02"
        return None, f"Formato inválido. Usa AAAA/NN (ex: {exemplo})."

    ano, num = m.group(1), int(m.group(2))
    limite = 12 if tipo == 'Mensal' else 4
    unidade = 'mês' if tipo == 'Mensal' else 'trimestre'
    if num < 1 or num > limite:
        return None, f"O {unidade} deve ficar entre 01 e {limite:02d}."

    return f"{ano}/{num:02d}", None


def _etapa_mudar_tipo(estado, texto, cenario):
    dados = estado.dados_coletados
    texto_norm = texto.strip().lower()

    if texto_norm in PALAVRAS_MANTER:
        tipo_novo = dados['tipo_atual']
    else:
        mapa = {'mensal': 'Mensal', 'trimestral': 'Trimestral', 'anual': 'Anual'}
        tipo_novo = mapa.get(texto_norm)
        if tipo_novo is None:
            return "Não entendi. Escolhe um: Mensal, Trimestral ou Anual (ou \"manter\" pra deixar como está)."

    dados['tipo_novo'] = tipo_novo
    estado.dados_coletados = dados
    estado.etapa_atual = 'periodo_inicio'
    estado.save()

    exemplo = {'Mensal': '2026/06', 'Trimestral': '2026/02', 'Anual': '2026'}[tipo_novo]
    manter_periodo = " (ou \"manter\" pra deixar como está)" if tipo_novo == dados['tipo_atual'] else ""
    return f"Tipo definido: **{tipo_novo}**.\n\nQual o novo **início** do período? (formato {exemplo}{manter_periodo})"


def _etapa_mudar_periodo_inicio(estado, texto, cenario):
    dados = estado.dados_coletados
    texto_norm = texto.strip().lower()

    if texto_norm in PALAVRAS_MANTER:
        if dados['tipo_novo'] != dados['tipo_atual']:
            return (
                f"Não dá pra manter o início {dados['inicio_atual']} já que o tipo vai mudar "
                f"pra {dados['tipo_novo']} -- o formato do período muda junto. Informa o novo início."
            )
        periodo = dados['inicio_atual']
    else:
        periodo, erro = _validar_periodo(dados['tipo_novo'], texto)
        if erro:
            return erro

    dados['inicio_novo'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'periodo_fim'
    estado.save()

    tipo_novo = dados['tipo_novo']
    exemplo = {'Mensal': '2026/12', 'Trimestral': '2026/04', 'Anual': '2027'}[tipo_novo]
    manter_fim = " (ou \"manter\" pra deixar como está)" if tipo_novo == dados['tipo_atual'] else ""
    return f"Início definido: **{periodo}**.\n\nQual o novo **fim** do período? (formato {exemplo}{manter_fim})"


def _etapa_mudar_periodo_fim(estado, texto, cenario):
    dados = estado.dados_coletados
    texto_norm = texto.strip().lower()

    if texto_norm in PALAVRAS_MANTER:
        if dados['tipo_novo'] != dados['tipo_atual']:
            return (
                f"Não dá pra manter o fim {dados['fim_atual']} já que o tipo vai mudar "
                f"pra {dados['tipo_novo']} -- o formato do período muda junto. Informa o novo fim."
            )
        periodo = dados['fim_atual']
    else:
        periodo, erro = _validar_periodo(dados['tipo_novo'], texto)
        if erro:
            return erro

    if periodo < dados['inicio_novo']:
        return f"O fim ({periodo}) não pode ser menor que o início ({dados['inicio_novo']}). Informa o fim de novo."

    dados['fim_novo'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'confirmar_mudanca'
    estado.save()

    return (
        "Resumo da mudança:\n"
        f"- Cenário: {dados['cenario_id']}/{dados['cenario_nome']}\n"
        f"- Tipo: {dados['tipo_atual']} → {dados['tipo_novo']}\n"
        f"- Período: {dados['inicio_atual']} a {dados['fim_atual']} → {dados['inicio_novo']} a {periodo}\n\n"
        "⚠️ Se o novo período tiver MENOS períodos que o atual, os dados dos períodos "
        "excedentes serão apagados. Se tiver MAIS, os novos períodos serão criados "
        "copiando os dados do último período existente.\n\n"
        "Confirma a mudança? (sim / não)"
    )


def _etapa_mudar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não mudei nada."

    dados = estado.dados_coletados

    # 🌟 NOVO: verifica_filhas só é disparada (via TbCenarios.save()) quando
    # o período de fato muda -- se só o tipo mudou e o período foi mantido
    # igual, não tem processamento em segundo plano nenhum pra esperar.
    periodo_vai_mudar = (
        dados['inicio_novo'] != dados['inicio_atual']
        or dados['fim_novo'] != dados['fim_atual']
    )

    cenario.cen_tipo = dados['tipo_novo']
    cenario.cen_inicio = dados['inicio_novo']
    cenario.cen_fim = dados['fim_novo']
    try:
        cenario.full_clean()
        # Captura o momento ANTES do save() -- mesmo motivo do ajuste que já
        # fizemos pra duplicação: o save() dispara a task via on_commit, que
        # roda de forma praticamente imediata, então capturar o timestamp
        # DEPOIS do save() pode ficar um instante à frente do TaskResult
        # de verdade.
        momento_mudanca = timezone.now().isoformat()
        cenario.save()
    except ValidationError as e:
        _encerrar_fluxo(estado)
        return f"Não consegui aplicar a mudança: {'; '.join(e.messages)}. Cancelei o fluxo -- pode tentar de novo."

    if not periodo_vai_mudar:
        _encerrar_fluxo(estado)
        return (
            f"✅ Cenário **{cenario.id}/{cenario.cen_nome}** atualizado: "
            f"tipo **{dados['tipo_novo']}**, período **{dados['inicio_novo']}** a **{dados['fim_novo']}**."
        )

    dados['momento_mudanca'] = momento_mudanca
    estado.dados_coletados = dados
    estado.etapa_atual = 'aguardando_verificacao_filhas'
    estado.save()
    return (
        f"Cenário atualizado! Como o período mudou, disparei em segundo plano o ajuste "
        f"das tabelas filhas do cenário **{cenario.id}/{cenario.cen_nome}** (criar os "
        "períodos que faltam ou remover os que sobraram). Clica em \"Verificar\" "
        "daqui a pouco que eu confiro se já terminou."
    )


def _buscar_task_verifica_filhas(desde_iso):
    try:
        from django_celery_results.models import TaskResult
        from django.utils.dateparse import parse_datetime
        import datetime
    except Exception:
        return None

    desde = parse_datetime(desde_iso)
    if desde is not None:
        desde = desde - datetime.timedelta(seconds=2)
    else:
        desde = desde_iso

    return TaskResult.objects.filter(
        task_name__icontains='verifica_filhas',
        date_created__gte=desde,
    ).order_by('date_created').first()


def _etapa_aguardando_verificacao_filhas(estado, texto, cenario):
    dados = estado.dados_coletados
    momento_mudanca = dados.get('momento_mudanca')

    task = _buscar_task_verifica_filhas(momento_mudanca)
    if task is None:
        return (
            "Ainda não encontrei o registro do ajuste das tabelas filhas -- pode ser que "
            "ainda não tenha começado. Clica em \"Verificar\" em alguns segundos que "
            "eu confiro de novo."
        )

    if task.status == 'SUCCESS':
        _encerrar_fluxo(estado)
        return (
            f"✅ Ajuste das tabelas filhas do cenário **{cenario.id}/{cenario.cen_nome}** concluído! "
            f"Tipo **{dados['tipo_novo']}**, período **{dados['inicio_novo']}** a **{dados['fim_novo']}**."
        )

    if task.status == 'FAILURE':
        _encerrar_fluxo(estado)
        return (
            f"❌ O ajuste das tabelas filhas do cenário {cenario.id}/{cenario.cen_nome} falhou "
            "-- o tipo/período já foram salvos, mas vale conferir manualmente no Admin se as "
            "filhas ficaram consistentes."
        )

    # PENDING, STARTED, RETRY, etc. -- ainda rodando
    return (
        f"Ainda ajustando as tabelas filhas do cenário {cenario.id}/{cenario.cen_nome} "
        f"(status atual: {task.status}). Clica em \"Verificar\" daqui a pouco."
    )


_HANDLERS_MUDAR = {
    'tipo': _etapa_mudar_tipo,
    'periodo_inicio': _etapa_mudar_periodo_inicio,
    'periodo_fim': _etapa_mudar_periodo_fim,
    'confirmar_mudanca': _etapa_mudar_confirmar,
    'aguardando_verificacao_filhas': _etapa_aguardando_verificacao_filhas,
}



# =======================================================================
# Fluxo: indicadores -- editar valor existente, criar indicador novo, ou
# aplicar reajuste em massa num intervalo de períodos. Tudo dentro do
# cenário ATIVO do usuário.
# =======================================================================

def iniciar_fluxo_indicadores(usuario, mensagem=""):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes de mexer nos indicadores."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais. Acesse a tela de Cenários e ative outro antes de continuar."

    texto = (mensagem or '').lower()
    if re.search(r'cri[ae]r?|cadastr[ae]r?', texto):
        acao = 'criar'
    elif re.search(r'reajust|em massa|todos os per[ií]odos', texto):
        acao = 'massa'
    elif re.search(r'elimin|apag|exclu[ií]|delet|remov', texto):
        acao = 'eliminar'
    else:
        acao = 'editar'

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_INDICADORES
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'acao': acao}

    if acao == 'criar':
        estado.etapa_atual = 'ind_criar_nome'
        estado.save()
        return (
            f"Vamos criar um indicador novo no cenário **{cenario.id}/{cenario.cen_nome}** 📊\n\n"
            "Qual vai ser o **nome** do indicador? (ou \"cancelar\" para desistir)"
        )
    elif acao == 'massa':
        estado.etapa_atual = 'ind_massa_nome'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.id}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer reajustar? (ou \"cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'ind_eliminar_nome'
        estado.save()
        return (
            f"Vamos eliminar um indicador do cenário **{cenario.id}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer eliminar? (ou \"cancelar\")"
        )
    else:
        estado.etapa_atual = 'ind_editar_nome'
        estado.save()
        return (
            f"Vamos mudar um valor de indicador no cenário **{cenario.id}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer mudar? (ou \"cancelar\")"
        )


def _processar_indicadores(estado, texto):
    cenario_id = estado.dados_coletados.get('cenario_id')
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return "O cenário que eu estava usando não existe mais. Cancelei o fluxo aqui."

    etapa = estado.etapa_atual
    handler = _HANDLERS_INDICADORES.get(etapa)
    if handler is None:
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."

    return handler(estado, texto, cenario)


def _buscar_indicador(cenario_id, texto):
    from tabelas.models import TbIndicadores
    nome = texto.strip().upper()
    return (
        TbIndicadores.objects.filter(tbcenarios_id=cenario_id, ind_nome=nome).first()
        or TbIndicadores.objects.filter(tbcenarios_id=cenario_id, ind_nome__icontains=nome).first()
    )


def _buscar_indicador_na_mensagem(cenario_id, mensagem):
    """
    Diferente de _buscar_indicador (que espera receber só o nome, como
    quando o usuário responde diretamente a "qual indicador?") -- aqui a
    MENSAGEM INTEIRA é passada (ex: "atualizar indicador TESTE com essa
    planilha"), e precisamos achar qual indicador cadastrado é MENCIONADO
    dentro dela, não o contrário.
    """
    from tabelas.models import TbIndicadores
    texto_upper = (mensagem or "").upper()
    candidatos = [
        i for i in TbIndicadores.objects.filter(tbcenarios_id=cenario_id)
        if i.ind_nome and i.ind_nome in texto_upper
    ]
    if not candidatos:
        return None
    # Se mais de um nome aparecer mencionado, prioriza o mais longo/específico
    # (evita um nome curto "bater" só por coincidência dentro de outro maior).
    candidatos.sort(key=lambda i: len(i.ind_nome), reverse=True)
    return candidatos[0]


def _periodo_para_dau_order(cenario, periodo_normalizado):
    """
    Converte um período normalizado ('AAAA/NN', ou 'AAAA/01' pra Anual)
    pro dau_order correspondente -- é o inverso da lógica usada em
    TbIndicadoresDaugther.display_order().
    """
    ano_inicio = int(cenario.cen_inicio[:4])
    ano_periodo = int(periodo_normalizado[:4])

    if cenario.cen_tipo == 'Anual':
        return ano_periodo - ano_inicio + 1

    num_inicio = int(cenario.cen_inicio[5:])
    num_periodo = int(periodo_normalizado[5:])
    unidades_por_ano = 12 if cenario.cen_tipo == 'Mensal' else 4

    return (ano_periodo - ano_inicio) * unidades_por_ano + (num_periodo - num_inicio) + 1


def _dau_order_para_periodo(cenario, dau_order):
    """
    Converte um dau_order pro período normalizado correspondente ('AAAA/NN',
    ou só o ano pra Anual) -- é o inverso de _periodo_para_dau_order().
    """
    ano_inicio = int(cenario.cen_inicio[:4])

    if cenario.cen_tipo == 'Anual':
        return str(ano_inicio - 1 + dau_order)

    num_inicio = int(cenario.cen_inicio[5:])
    unidades_por_ano = 12 if cenario.cen_tipo == 'Mensal' else 4

    total = (num_inicio - 1) + (dau_order - 1)
    ano = ano_inicio + total // unidades_por_ano
    num = (total % unidades_por_ano) + 1
    return f"{ano}/{num:02d}"


def _gerar_planilha_periodos(cenario, tabela_mae_id, tabela_daugther_model, tipo_planilha, nome_exibicao):
    """
    Gera uma planilha simples (Período | Valor) com os períodos e valores
    ATUAIS de um indicador ou câmbio, salva em MEDIA_ROOT/planilhas_temp,
    e devolve a URL pra baixar. Formato propositalmente simples (nosso
    próprio template, não tentando ler layouts variados de terceiros) --
    evita qualquer ambiguidade de interpretação na hora de reenviar.

    O nome do arquivo começa com um prefixo fixo e legível por máquina
    ("planilha_ind_<id>_<cenario>_" ou "planilha_cam_<id>_<cenario>_") --
    isso permite, no reenvio, identificar automaticamente a QUAL
    indicador/câmbio a planilha pertence, sem exigir que o usuário repita
    o nome na mensagem (só o nome do arquivo já basta).
    """
    import os
    from openpyxl import Workbook
    from django.conf import settings

    filhas = tabela_daugther_model.objects.filter(mae_id=tabela_mae_id, tbcenarios_id=cenario.id).order_by('dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = "Períodos"
    ws.append(['Período', 'Valor'])
    for f in filhas:
        periodo = _dau_order_para_periodo(cenario, f.dau_order)
        ws.append([periodo, float(f.dau_valor)])

    pasta = os.path.join(settings.MEDIA_ROOT, 'planilhas_temp')
    os.makedirs(pasta, exist_ok=True)
    nome_exibicao_seguro = re.sub(r'[^A-Za-z0-9]+', '', nome_exibicao)[:20]
    nome_arquivo = f"planilha_{tipo_planilha}_{tabela_mae_id}_{cenario.id}_{nome_exibicao_seguro}.xlsx"
    caminho_completo = os.path.join(pasta, nome_arquivo)
    wb.save(caminho_completo)
    wb.close()

    return f"{settings.MEDIA_URL}planilhas_temp/{nome_arquivo}"


def _identificar_planilha_por_nome(nome_arquivo, tipo_planilha, cenario_id):
    """
    Extrai o id da mãe (indicador ou câmbio) do nome do arquivo, no
    padrão 'planilha_<tipo>_<id_mae>_<id_cenario>_...' gerado por
    _gerar_planilha_periodos(). Retorna None se o nome não bater o
    padrão esperado, ou se pertencer a outro cenário (arquivo antigo,
    de outro cenário que o usuário já usou antes).
    """
    m = re.match(rf'planilha_{tipo_planilha}_(\d+)_(\d+)_', nome_arquivo)
    if not m:
        return None
    id_mae, id_cenario_arquivo = int(m.group(1)), int(m.group(2))
    if id_cenario_arquivo != cenario_id:
        return None
    return id_mae


def identificar_tipo_planilha_reenviada(usuario, pdf_ids):
    """
    Escaneia os arquivos XLSX marcados nos Relatórios e devolve 'ind' ou
    'cam' se algum bater com nosso padrão de nome, pro cenário ativo do
    usuário. Usado no roteamento em agents.py -- permite reconhecer o
    reenvio mesmo se a mensagem do usuário não mencionar explicitamente
    "indicador"/"câmbio" (o nome do arquivo já basta).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return None

    import os
    from .models import RelatorioPDF
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True).first()
        if not r or not r.arquivo or not r.arquivo.name.lower().endswith('.xlsx'):
            continue
        nome_arquivo = os.path.basename(r.arquivo.name)
        if _identificar_planilha_por_nome(nome_arquivo, 'ind', perfil.cenario_ativo_id):
            return 'ind'
        if _identificar_planilha_por_nome(nome_arquivo, 'cam', perfil.cenario_ativo_id):
            return 'cam'
    return None


def _descartar_planilha(relatorio_id):
    """
    Remove o registro temporário de upload (RelatorioPDF) e o arquivo em
    disco -- chamado sempre que uma planilha JÁ FOI LIDA/PROCESSADA,
    independente do resultado (aplicada, recusada, sem mudanças, ou erro
    de leitura). Não faz sentido deixá-la disponível pra seleção depois
    -- ela já cumpriu seu papel.
    """
    try:
        from .models import RelatorioPDF
        import os
        relatorio = RelatorioPDF.objects.filter(id=relatorio_id).first()
        if relatorio and relatorio.arquivo:
            caminho = relatorio.arquivo.path
            relatorio.delete()
            if os.path.exists(caminho):
                os.remove(caminho)
    except Exception:
        pass


def _ler_planilha_periodos(caminho_arquivo):
    """
    Lê uma planilha no formato do NOSSO template (coluna A = Período,
    coluna B = Valor, cabeçalho na linha 1). Retorna lista de tuplas
    (periodo_texto, valor), na ordem em que aparecem -- ignora linhas com
    qualquer uma das duas colunas vazia.
    """
    import openpyxl
    wb = openpyxl.load_workbook(caminho_arquivo, data_only=True)
    ws = wb.active
    linhas = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None or row[1] is None:
            continue
        periodo_texto = str(row[0]).strip()
        valor = row[1]
        linhas.append((periodo_texto, valor))
    wb.close()
    return linhas


def _lista_indicadores(cenario_id):
    from tabelas.models import TbIndicadores
    indicadores = TbIndicadores.objects.filter(tbcenarios_id=cenario_id).order_by('ind_nome')
    if not indicadores:
        return "Não tem nenhum indicador cadastrado nesse cenário ainda.\n\n"
    lista = "\n".join(f"- {i.ind_nome}" for i in indicadores)
    return f"Indicadores cadastrados:\n{lista}\n\n"


def _lista_periodos_indicador(cenario, indicador):
    from tabelas.models import TbIndicadoresDaugther
    filhas = TbIndicadoresDaugther.objects.filter(mae_id=indicador.id, tbcenarios_id=cenario.id).order_by('dau_order')
    if not filhas:
        return ""
    linhas = [f"- {_dau_order_para_periodo(cenario, f.dau_order)}: {f.dau_valor}%" for f in filhas]
    return "Períodos e valores atuais:\n" + "\n".join(linhas) + "\n\n"


def _total_periodos(cenario):
    from django.db import connection
    cursor = connection.cursor()
    cursor.execute("select conta_periodos(%s)", [cenario.id])
    total = cursor.fetchone()[0]
    cursor.close()
    return total


def _exemplo_periodo(cenario, deslocamento=0):
    mapa = {'Mensal': '2026/06', 'Trimestral': '2026/02', 'Anual': '2026'}
    return mapa[cenario.cen_tipo] if deslocamento == 0 else {'Mensal': '2026/12', 'Trimestral': '2026/04', 'Anual': '2027'}[cenario.cen_tipo]


# ---------------------------------------------------------------------
# Sub-fluxo: editar valor existente
# ---------------------------------------------------------------------
def _etapa_ind_editar_nome(estado, texto, cenario):
    indicador = _buscar_indicador(cenario.id, texto)
    if indicador is None:
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['indicador_id'] = indicador.id
    dados['indicador_nome'] = indicador.ind_nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_editar_modo'
    estado.save()

    return (
        f"Indicador: **{indicador.ind_nome}**.\n\n"
        "Como você quer ajustar os valores? Digite:\n"
        "- **manual** -- pra mudar um período de cada vez, digitando aqui\n"
        "- **planilha** -- pra baixar uma planilha, preencher, e reenviar de uma vez\n"
        "(ou \"cancelar\")"
    )


def _etapa_ind_editar_modo(estado, texto, cenario):
    escolha = texto.strip().lower()
    dados = estado.dados_coletados

    if escolha in ('não', 'nao', 'n'):
        _encerrar_fluxo(estado)
        return "Combinado, deixei como está."

    if escolha in ('planilha', 'excel', 'xlsx'):
        from tabelas.models import TbIndicadoresDaugther
        url = _gerar_planilha_periodos(cenario, dados['indicador_id'], TbIndicadoresDaugther, 'ind', dados['indicador_nome'])
        _encerrar_fluxo(estado)
        return (
            f"[📥 Baixar planilha]({url})\n\n"
            "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
            "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
            "pelo arquivo, não precisa repetir o nome do indicador."
        )

    if escolha in ('manual', 'digitar', 'digitando', 'm'):
        from tabelas.models import TbIndicadores
        indicador = TbIndicadores.objects.filter(id=dados['indicador_id']).first()
        if indicador is None:
            _encerrar_fluxo(estado)
            return "O indicador não existe mais. Cancelei o fluxo."
        estado.etapa_atual = 'ind_editar_periodo'
        estado.save()
        return (
            f"{_lista_periodos_indicador(cenario, indicador)}"
            f"Qual **período** você quer mudar? (formato {_exemplo_periodo(cenario)})"
        )

    return "Não entendi. Digita **manual** ou **planilha** (ou \"cancelar\")."


def _etapa_ind_editar_periodo(estado, texto, cenario):
    # 🌟 Se ainda assim o usuário digitar "planilha" aqui (mesmo já tendo
    # passado pela escolha de modo), atende do mesmo jeito -- rede de
    # segurança, não é mais o caminho principal.
    if 'planilha' in texto.strip().lower():
        from tabelas.models import TbIndicadoresDaugther
        dados = estado.dados_coletados
        url = _gerar_planilha_periodos(cenario, dados['indicador_id'], TbIndicadoresDaugther, 'ind', dados['indicador_nome'])
        _encerrar_fluxo(estado)
        return (
            f"[📥 Baixar planilha]({url})\n\n"
            "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
            "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente pelo "
            "arquivo, não precisa repetir o nome do indicador. Encerrei esse fluxo aqui, pra sua próxima "
            "mensagem já ser reconhecida certinho."
        )

    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dau_order = _periodo_para_dau_order(cenario, periodo)
    from tabelas.models import TbIndicadoresDaugther
    filha = TbIndicadoresDaugther.objects.filter(
        mae_id=estado.dados_coletados['indicador_id'], tbcenarios_id=cenario.id, dau_order=dau_order
    ).first()
    if filha is None:
        return f"O período {periodo} está fora do intervalo do cenário. Informa outro período, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['periodo'] = periodo
    dados['dau_order'] = dau_order
    dados['valor_atual'] = str(filha.dau_valor)
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_editar_valor'
    estado.save()

    return f"Valor atual em {periodo}: **{filha.dau_valor}%**.\n\nQual o **novo valor** (%)?"


def _etapa_ind_editar_valor(estado, texto, cenario):
    try:
        valor_novo = Decimal(texto.strip().replace(',', '.').replace('%', ''))
    except (InvalidOperation, ValueError):
        return "Não entendi o valor. Informa um número (ex: 5.5), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['valor_novo'] = str(valor_novo)
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_editar_confirmar'
    estado.save()

    return (
        "Resumo:\n"
        f"- Indicador: {dados['indicador_nome']}\n"
        f"- Período: {dados['periodo']}\n"
        f"- Valor: {dados['valor_atual']}% → {valor_novo}%\n\n"
        "Confirma a mudança? (sim / não)"
    )


def _etapa_ind_editar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não mudei nada."

    dados = estado.dados_coletados
    from tabelas.models import TbIndicadoresDaugther
    try:
        filha = TbIndicadoresDaugther.objects.get(
            mae_id=dados['indicador_id'], tbcenarios_id=cenario.id, dau_order=dados['dau_order']
        )
        filha.dau_valor = Decimal(dados['valor_novo'])
        filha.save()
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao salvar: {e}. Cancelei o fluxo -- pode tentar de novo."

    _encerrar_fluxo(estado)
    return f"✅ Indicador **{dados['indicador_nome']}**, período **{dados['periodo']}**, atualizado para **{dados['valor_novo']}%**."


# ---------------------------------------------------------------------
# Sub-fluxo: eliminar indicador (remove o indicador e TODOS os períodos
# dele -- a filha tem on_delete=CASCADE, então um único .delete() na mãe
# já apaga tudo)
# ---------------------------------------------------------------------
def _indicador_em_uso(indicador):
    """
    Descobre, via introspecção do próprio Django (não uma lista fixa de
    tabelas, pra não correr risco de esquecer alguma), se esse indicador
    está referenciado por algum OUTRO registro do sistema -- não importa
    o on_delete configurado (CASCADE apagaria em cascata sem avisar;
    PROTECT o banco já recusa sozinho, mas é melhor avisar antes, com uma
    mensagem clara, do que deixar estourar um erro técnico do Postgres).

    Exclui TbIndicadoresDaugther da checagem -- são os próprios períodos
    do indicador (a mesma coisa que _etapa_ind_eliminar_confirmar já
    apaga de propósito), não "uso externo".
    """
    usos = []
    for related in indicador._meta.related_objects:
        if related.related_model._meta.model_name == 'tbindicadoresdaugther':
            continue
        accessor = related.get_accessor_name()
        qtd = getattr(indicador, accessor).count()
        if qtd > 0:
            nome_modelo = related.related_model._meta.verbose_name
            usos.append(f"{nome_modelo} ({qtd} registro(s))")
    return usos


def _etapa_ind_eliminar_nome(estado, texto, cenario):
    indicador = _buscar_indicador(cenario.id, texto)
    if indicador is None:
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    # 🌟 NOVO: bloqueia a eliminação se esse indicador estiver referenciado
    # por qualquer outra tabela do sistema -- eliminar apagaria esses
    # registros em cascata também, silenciosamente, se não checássemos aqui.
    usos = _indicador_em_uso(indicador)
    if usos:
        _encerrar_fluxo(estado)
        lista_usos = "\n".join(f"- {u}" for u in usos)
        return (
            f"⚠️ Não posso eliminar o indicador **{indicador.ind_nome}** -- ele está em uso em:\n"
            f"{lista_usos}\n\n"
            "Remove essas referências primeiro (pelo Admin, trocando ou limpando o indicador "
            "usado nesses registros), antes de tentar eliminar esse."
        )

    dados = estado.dados_coletados
    dados['indicador_id'] = indicador.id
    dados['indicador_nome'] = indicador.ind_nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_eliminar_confirmar'
    estado.save()

    return (
        f"⚠️ Confirma que quer **eliminar** o indicador **{indicador.ind_nome}**? "
        "Isso apaga o indicador E todos os valores dele, em todos os períodos -- não tem como desfazer. (sim / não)"
    )


def _etapa_ind_eliminar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não eliminei nada."

    dados = estado.dados_coletados
    from tabelas.models import TbIndicadores
    try:
        indicador = TbIndicadores.objects.filter(id=dados['indicador_id'], tbcenarios_id=cenario.id).first()
        if indicador is None:
            _encerrar_fluxo(estado)
            return f"O indicador {dados['indicador_nome']} já não existe mais. Nada a fazer."

        # 🌟 Re-checa bem antes de apagar de verdade -- defesa extra, caso
        # algum registro tenha passado a usar esse indicador enquanto o
        # usuário respondia a pergunta de confirmação.
        usos = _indicador_em_uso(indicador)
        if usos:
            _encerrar_fluxo(estado)
            lista_usos = "\n".join(f"- {u}" for u in usos)
            return (
                f"⚠️ O indicador {dados['indicador_nome']} passou a estar em uso enquanto conversávamos:\n"
                f"{lista_usos}\n\nCancelei a eliminação, por segurança."
            )

        nome = indicador.ind_nome
        indicador.delete()
    except ProtectedError:
        _encerrar_fluxo(estado)
        return (
            f"⚠️ Não consegui eliminar o indicador {dados['indicador_nome']} -- o banco recusou porque ele "
            "ainda está em uso em algum lugar. Confere no Admin onde esse indicador está sendo usado antes "
            "de tentar de novo."
        )
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao eliminar: {e}. Cancelei o fluxo -- pode tentar de novo."

    _encerrar_fluxo(estado)
    return f"✅ Indicador **{nome}** eliminado, junto com todos os valores dele."


# ---------------------------------------------------------------------
# Sub-fluxo: criar indicador novo
# ---------------------------------------------------------------------
def _etapa_ind_criar_nome(estado, texto, cenario):
    nome = texto.strip().upper()
    if not nome:
        return "Preciso de um nome. Qual vai ser o nome do indicador?"

    from tabelas.models import TbIndicadores
    if TbIndicadores.objects.filter(tbcenarios_id=cenario.id, ind_nome=nome).exists():
        return f"Já existe um indicador chamado **{nome}** nesse cenário. Escolhe outro nome, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['nome'] = nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_criar_valor_inicial'
    estado.save()
    return f"Nome: **{nome}**.\n\nQual o **valor inicial** (%) desse indicador? (esse valor é aplicado a todos os períodos, de início)"


def _etapa_ind_criar_valor_inicial(estado, texto, cenario):
    try:
        valor_inicial = Decimal(texto.strip().replace(',', '.').replace('%', ''))
    except (InvalidOperation, ValueError):
        return "Não entendi o valor. Informa um número (ex: 5.5), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['valor_inicial'] = str(valor_inicial)
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_criar_observacao'
    estado.save()
    return f"Valor inicial: **{valor_inicial}%**.\n\nAlguma **observação**? (ou \"nenhuma\")"


def _etapa_ind_criar_observacao(estado, texto, cenario):
    observacao = '' if texto.strip().lower() in ('nenhuma', 'nao', 'não', 'n/a', 'na') else texto.strip()

    dados = estado.dados_coletados
    dados['observacao'] = observacao
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_criar_confirmar'
    estado.save()

    return (
        "Resumo do indicador a criar:\n"
        f"- Nome: {dados['nome']}\n"
        f"- Valor inicial: {dados['valor_inicial']}%\n"
        f"- Observação: {observacao or 'nenhuma'}\n\n"
        "Confirma a criação? (sim / não)"
    )


def _etapa_ind_criar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não criei o indicador."

    dados = estado.dados_coletados
    from tabelas.models import TbIndicadores
    try:
        indicador = TbIndicadores(
            ind_nome=dados['nome'],
            valor_inicial=Decimal(dados['valor_inicial']),
            ind_observacao=dados['observacao'] or None,
        )
        # 🌟 tbcenarios é preenchido sozinho pelo sinal pre_save
        # (atualiza_cenario) -- por isso excluído da validação aqui, senão
        # o full_clean() reclamaria de campo obrigatório vazio.
        indicador.full_clean(exclude=['tbcenarios'])
        indicador.save()
    except ValidationError as e:
        _encerrar_fluxo(estado)
        return f"Não consegui criar o indicador: {'; '.join(e.messages)}. Cancelei o fluxo -- pode tentar de novo."
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao criar o indicador: {e}. Cancelei o fluxo -- pode tentar de novo."

    # 🌟 NOVO: em vez de encerrar aqui, oferece já ajustar valores de
    # períodos específicos -- reaproveita a mesma etapa 'ind_editar_modo'
    # usada no fluxo de editar.
    dados['indicador_id'] = indicador.id
    dados['indicador_nome'] = indicador.ind_nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_editar_modo'
    estado.save()

    return (
        f"✅ Indicador **{indicador.ind_nome}** criado, com valor inicial de **{dados['valor_inicial']}%** em todos os períodos.\n\n"
        "Quer ajustar algum período específico agora? Digite:\n"
        "- **manual** -- pra mudar um período de cada vez\n"
        "- **planilha** -- pra baixar, preencher, e reenviar de uma vez\n"
        "- **não** -- se já está bom assim"
    )


# ---------------------------------------------------------------------
# Sub-fluxo: ajuste em massa (reajuste percentual num intervalo)
# ---------------------------------------------------------------------
def _etapa_ind_massa_nome(estado, texto, cenario):
    indicador = _buscar_indicador(cenario.id, texto)
    if indicador is None:
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['indicador_id'] = indicador.id
    dados['indicador_nome'] = indicador.ind_nome
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_massa_periodo_inicio'
    estado.save()

    return (
        f"Indicador: **{indicador.ind_nome}**.\n\n"
        f"Qual o **período inicial** do reajuste? (formato {_exemplo_periodo(cenario)}, "
        "ou \"todos\" pra aplicar em todo o cenário)"
    )


def _etapa_ind_massa_periodo_inicio(estado, texto, cenario):
    dados = estado.dados_coletados

    if texto.strip().lower() in ('todos', 'tudo', 'todo'):
        total = _total_periodos(cenario)
        dados['dau_order_inicio'] = 1
        dados['dau_order_fim'] = total
        dados['periodo_inicio_texto'] = 'início'
        dados['periodo_fim_texto'] = 'fim'
        estado.dados_coletados = dados
        estado.etapa_atual = 'ind_massa_percentual'
        estado.save()
        return f"Vai aplicar em **todos os {total} períodos** do cenário.\n\nQual o **percentual de reajuste**? (ex: 5, ou -3.5)"

    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dados['dau_order_inicio'] = _periodo_para_dau_order(cenario, periodo)
    dados['periodo_inicio_texto'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_massa_periodo_fim'
    estado.save()

    return f"Início: **{periodo}**.\n\nQual o **período final** do reajuste? (formato {_exemplo_periodo(cenario, 1)})"


def _etapa_ind_massa_periodo_fim(estado, texto, cenario):
    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dau_order_fim = _periodo_para_dau_order(cenario, periodo)
    dados = estado.dados_coletados
    if dau_order_fim < dados['dau_order_inicio']:
        return f"O fim ({periodo}) não pode ser antes do início ({dados['periodo_inicio_texto']}). Informa outro período final."

    dados['dau_order_fim'] = dau_order_fim
    dados['periodo_fim_texto'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_massa_percentual'
    estado.save()
    return f"Fim: **{periodo}**.\n\nQual o **percentual de reajuste**? (ex: 5, ou -3.5)"


def _etapa_ind_massa_percentual(estado, texto, cenario):
    try:
        percentual = Decimal(texto.strip().replace(',', '.').replace('%', ''))
    except (InvalidOperation, ValueError):
        return "Não entendi o percentual. Informa um número (ex: 5 ou -3.5), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['percentual'] = str(percentual)
    estado.dados_coletados = dados
    estado.etapa_atual = 'ind_massa_confirmar'
    estado.save()

    from tabelas.models import TbIndicadoresDaugther
    qtd = TbIndicadoresDaugther.objects.filter(
        mae_id=dados['indicador_id'], tbcenarios_id=cenario.id,
        dau_order__gte=dados['dau_order_inicio'], dau_order__lte=dados['dau_order_fim'],
    ).count()

    sinal = '+' if percentual >= 0 else ''
    return (
        "Resumo do reajuste:\n"
        f"- Indicador: {dados['indicador_nome']}\n"
        f"- Período: {dados['periodo_inicio_texto']} a {dados['periodo_fim_texto']} ({qtd} período(s))\n"
        f"- Reajuste: {sinal}{percentual}%\n\n"
        f"⚠️ Isso vai salvar {qtd} registro(s), um de cada vez -- pode levar um instante a mais que uma edição única.\n\n"
        "Confirma a aplicação? (sim / não)"
    )


def _etapa_ind_massa_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não apliquei o reajuste."

    dados = estado.dados_coletados
    percentual = Decimal(dados['percentual'])
    fator = Decimal('1') + (percentual / Decimal('100'))

    from tabelas.models import TbIndicadoresDaugther
    filhas = TbIndicadoresDaugther.objects.filter(
        mae_id=dados['indicador_id'], tbcenarios_id=cenario.id,
        dau_order__gte=dados['dau_order_inicio'], dau_order__lte=dados['dau_order_fim'],
    )

    qtd = 0
    try:
        for filha in filhas:
            filha.dau_valor = (filha.dau_valor * fator).quantize(Decimal('0.0001'))
            filha.save()
            qtd += 1
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro no meio do reajuste (parei em {qtd} período(s) já atualizados): {e}."

    _encerrar_fluxo(estado)
    return f"✅ Reajuste de **{dados['percentual']}%** aplicado em **{qtd} período(s)** do indicador **{dados['indicador_nome']}**."


# ---------------------------------------------------------------------
# Sub-fluxo: planilha (baixar template, reenviar preenchida, confirmar)
# ---------------------------------------------------------------------
def iniciar_download_planilha_indicador(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    indicador = _buscar_indicador_na_mensagem(cenario.id, mensagem)
    if indicador is None:
        return (
            f"Não consegui identificar qual indicador -- me diz o nome dele na mesma mensagem "
            f"(ex: \"baixar planilha do indicador INPC\").\n\n{_lista_indicadores(cenario.id)}"
        )

    from tabelas.models import TbIndicadoresDaugther
    url = _gerar_planilha_periodos(cenario, indicador.id, TbIndicadoresDaugther, 'ind', indicador.ind_nome)
    return (
        f"Aqui está a planilha do indicador **{indicador.ind_nome}**, com os períodos e valores atuais:\n\n"
        f"[📥 Baixar planilha]({url})\n\n"
        "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
        "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
        "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
        "pelo arquivo, não precisa repetir o nome do indicador."
    )


def _processar_planilha_indicador(usuario, mensagem, pdf_ids):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    import os
    from .models import RelatorioPDF
    from tabelas.models import TbIndicadores

    # 🌟 CORRIGIDO: em vez de exigir que o usuário repita o nome do
    # indicador na mensagem, identifica automaticamente pelo nome do
    # arquivo (que já carrega o id do indicador, gravado por
    # _gerar_planilha_periodos). Só cai pra busca por nome na mensagem se
    # o arquivo não tiver esse padrão (por exemplo, foi renomeado).
    relatorio = None
    indicador = None
    candidatos_xlsx = []
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True).first()
        if r and r.arquivo and r.arquivo.name.lower().endswith('.xlsx'):
            candidatos_xlsx.append(r)
            nome_arquivo = os.path.basename(r.arquivo.name)
            indicador_id = _identificar_planilha_por_nome(nome_arquivo, 'ind', cenario.id)
            if indicador_id:
                achado = TbIndicadores.objects.filter(id=indicador_id, tbcenarios_id=cenario.id).first()
                if achado:
                    relatorio = r
                    indicador = achado
                    break

    if indicador is None:
        indicador = _buscar_indicador_na_mensagem(cenario.id, mensagem)
        if indicador is not None and candidatos_xlsx:
            relatorio = candidatos_xlsx[0]

    if indicador is None:
        return f"Não consegui identificar qual indicador essa planilha é. Me diz o nome dele na mensagem.\n\n{_lista_indicadores(cenario.id)}"
    if relatorio is None:
        return "Não encontrei nenhuma planilha XLSX marcada na lista de Relatórios. Marca a caixinha do arquivo que você subiu."

    linhas = _ler_planilha_periodos(relatorio.arquivo.path)
    if not linhas:
        _descartar_planilha(relatorio.id)
        return "Não consegui ler nenhuma linha válida dessa planilha. Confere se ela tem as colunas Período e Valor."

    from tabelas.models import TbIndicadoresDaugther
    mudancas = []
    erros = []
    for periodo_texto, valor in linhas:
        try:
            dau_order = _periodo_para_dau_order(cenario, periodo_texto)
        except (ValueError, IndexError):
            erros.append(f"período \"{periodo_texto}\" com formato inválido")
            continue
        filha = TbIndicadoresDaugther.objects.filter(mae_id=indicador.id, tbcenarios_id=cenario.id, dau_order=dau_order).first()
        if filha is None:
            erros.append(f"período {periodo_texto} não existe nesse cenário")
            continue
        try:
            valor_novo = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            erros.append(f"valor inválido em {periodo_texto}: {valor}")
            continue
        if valor_novo != filha.dau_valor:
            mudancas.append((filha.id, periodo_texto, str(filha.dau_valor), str(valor_novo)))

    aviso_erros = ("\n\n⚠️ Alguns problemas encontrados:\n" + "\n".join(f"- {e}" for e in erros)) if erros else ""

    if not mudancas:
        _descartar_planilha(relatorio.id)
        return f"Não encontrei nenhuma mudança de valor nessa planilha, comparado ao que já está salvo.{aviso_erros}"

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_INDICADORES
    estado.etapa_atual = 'ind_planilha_confirmar'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'indicador_id': indicador.id,
        'indicador_nome': indicador.ind_nome,
        'relatorio_id': relatorio.id,
        'mudancas': mudancas,
    }
    estado.save()

    linhas_resumo = "\n".join(f"- {p}: {antigo} → {novo}" for _, p, antigo, novo in mudancas)
    return (
        f"Encontrei {len(mudancas)} mudança(s) pro indicador **{indicador.ind_nome}**:\n"
        f"{linhas_resumo}"
        f"{aviso_erros}\n\n"
        "Confirma a aplicação? (sim / não)"
    )


def _etapa_ind_planilha_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    if resposta not in ('sim', 's', 'yes', 'y'):
        _descartar_planilha(dados['relatorio_id'])
        _encerrar_fluxo(estado)
        return "Ok, não apliquei nada da planilha."

    from tabelas.models import TbIndicadoresDaugther, TbIndicadores

    qtd = 0
    try:
        for filha_id, periodo, antigo, novo in dados['mudancas']:
            filha = TbIndicadoresDaugther.objects.get(id=filha_id)
            filha.dau_valor = Decimal(novo)
            filha.save()
            qtd += 1
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro no meio da aplicação (parei em {qtd} período(s)): {e}."

    # 🌟 Move a planilha enviada pro campo "Fonte" do indicador, e apaga o
    # registro temporário de upload (RelatorioPDF) junto com o arquivo em
    # disco -- ela não precisa mais existir como "relatório selecionável",
    # agora vive oficialmente dentro do próprio indicador.
    aviso_fonte = ""
    try:
        from .models import RelatorioPDF
        relatorio = RelatorioPDF.objects.filter(id=dados['relatorio_id']).first()
        indicador = TbIndicadores.objects.filter(id=dados['indicador_id']).first()
        if relatorio and indicador and relatorio.arquivo:
            from django.core.files.base import ContentFile
            import os
            relatorio.arquivo.open('rb')
            conteudo = relatorio.arquivo.read()
            relatorio.arquivo.close()
            nome_arquivo = relatorio.arquivo.name.split('/')[-1]
            indicador.ind_fonte.save(nome_arquivo, ContentFile(conteudo), save=True)

            caminho_antigo = relatorio.arquivo.path
            relatorio.delete()
            if os.path.exists(caminho_antigo):
                os.remove(caminho_antigo)
    except Exception as e:
        aviso_fonte = f"\n\n(A planilha não pôde ser movida pra Fonte automaticamente: {e})"

    _encerrar_fluxo(estado)
    return f"✅ {qtd} período(s) do indicador **{dados['indicador_nome']}** atualizados a partir da planilha.{aviso_fonte}"


_HANDLERS_INDICADORES = {
    'ind_editar_nome': _etapa_ind_editar_nome,
    'ind_editar_modo': _etapa_ind_editar_modo,
    'ind_editar_periodo': _etapa_ind_editar_periodo,
    'ind_editar_valor': _etapa_ind_editar_valor,
    'ind_editar_confirmar': _etapa_ind_editar_confirmar,
    'ind_planilha_confirmar': _etapa_ind_planilha_confirmar,
    'ind_eliminar_nome': _etapa_ind_eliminar_nome,
    'ind_eliminar_confirmar': _etapa_ind_eliminar_confirmar,
    'ind_criar_nome': _etapa_ind_criar_nome,
    'ind_criar_valor_inicial': _etapa_ind_criar_valor_inicial,
    'ind_criar_observacao': _etapa_ind_criar_observacao,
    'ind_criar_confirmar': _etapa_ind_criar_confirmar,
    'ind_massa_nome': _etapa_ind_massa_nome,
    'ind_massa_periodo_inicio': _etapa_ind_massa_periodo_inicio,
    'ind_massa_periodo_fim': _etapa_ind_massa_periodo_fim,
    'ind_massa_percentual': _etapa_ind_massa_percentual,
    'ind_massa_confirmar': _etapa_ind_massa_confirmar,
}


# =======================================================================
# Fluxo: cambio -- editar valor existente, criar taxa de câmbio nova (só
# entre as moedas ainda disponíveis pro cenário), reajuste em massa, ou
# eliminar. Mesma estrutura do fluxo de indicadores, adaptada pras
# diferenças reais do TbCambio:
#   - "nome" não é livre -- cam_moeda é um campo de opções fixas (a
#     escolha depende da moeda da própria empresa, calculada em
#     TbCambio.cam_moeda_choice)
#   - não existe propagação automática (TbCambio/TbCambioDaugther.save()
#     não chamam nenhuma procedure tipo o atualiza_indicador)
# =======================================================================

FLUXO_CAMBIO = 'cambio'


def iniciar_fluxo_cambio(usuario, mensagem=""):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes de mexer nas taxas de câmbio."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais. Acesse a tela de Cenários e ative outro antes de continuar."

    texto = (mensagem or '').lower()
    if re.search(r'cri[ae]r?|cadastr[ae]r?', texto):
        acao = 'criar'
    elif re.search(r'reajust|em massa|todos os per[ií]odos', texto):
        acao = 'massa'
    elif re.search(r'elimin|apag|exclu[ií]|delet|remov', texto):
        acao = 'eliminar'
    else:
        acao = 'editar'

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CAMBIO
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'acao': acao}

    if acao == 'criar':
        disponiveis = _moedas_disponiveis(cenario.id)
        estado.etapa_atual = 'cam_criar_moeda'
        estado.save()
        if not disponiveis:
            _encerrar_fluxo(estado)
            return "Não tem nenhuma moeda disponível pra cadastrar nesse cenário -- todas já foram usadas."
        opcoes = "\n".join(f"- {c} ({n})" for c, n in disponiveis.items())
        return (
            f"Vamos criar uma taxa de câmbio nova no cenário **{cenario.id}/{cenario.cen_nome}** 💱\n\n"
            f"Moedas disponíveis:\n{opcoes}\n\n"
            "Qual você quer cadastrar? (ou \"cancelar\")"
        )
    elif acao == 'massa':
        estado.etapa_atual = 'cam_massa_moeda'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.id}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer reajustar? (ou \"cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'cam_eliminar_moeda'
        estado.save()
        return (
            f"Vamos eliminar uma taxa de câmbio do cenário **{cenario.id}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual você quer eliminar? (ou \"cancelar\")"
        )
    else:
        estado.etapa_atual = 'cam_editar_moeda'
        estado.save()
        return (
            f"Vamos mudar um valor de câmbio no cenário **{cenario.id}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer mudar? (ou \"cancelar\")"
        )


def _processar_cambio(estado, texto):
    cenario_id = estado.dados_coletados.get('cenario_id')
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return "O cenário que eu estava usando não existe mais. Cancelei o fluxo aqui."

    etapa = estado.etapa_atual
    handler = _HANDLERS_CAMBIO.get(etapa)
    if handler is None:
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."

    return handler(estado, texto, cenario)


def _lista_cambios(cenario_id):
    from tabelas.models import TbCambio
    cambios = TbCambio.objects.filter(tbcenarios_id=cenario_id).order_by('cam_moeda')
    if not cambios:
        return "Não tem nenhuma taxa de câmbio cadastrada nesse cenário ainda.\n\n"
    lista = "\n".join(f"- {c.get_cam_moeda_display()} ({c.cam_moeda})" for c in cambios)
    return f"Taxas de câmbio cadastradas:\n{lista}\n\n"


def _buscar_cambio(cenario_id, texto):
    from tabelas.models import TbCambio
    texto_norm = texto.strip().upper()
    cambios = TbCambio.objects.filter(tbcenarios_id=cenario_id)
    for c in cambios:
        if c.cam_moeda == texto_norm or c.get_cam_moeda_display().upper() == texto_norm:
            return c
    for c in cambios:
        if texto_norm in c.get_cam_moeda_display().upper():
            return c
    return None


def _buscar_cambio_na_mensagem(cenario_id, mensagem):
    """Mesma ideia de _buscar_indicador_na_mensagem, pra câmbio: procura
    qual moeda cadastrada é mencionada dentro da mensagem inteira."""
    from tabelas.models import TbCambio
    texto_upper = (mensagem or "").upper()
    candidatos = []
    for c in TbCambio.objects.filter(tbcenarios_id=cenario_id):
        nome_display = c.get_cam_moeda_display().upper()
        if c.cam_moeda in texto_upper or nome_display in texto_upper:
            candidatos.append(c)
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: len(c.get_cam_moeda_display()), reverse=True)
    return candidatos[0]


def _moedas_disponiveis(cenario_id):
    """Moedas que o model permite (cam_moeda_choice, calculado a partir da
    moeda da empresa) e que AINDA não foram cadastradas nesse cenário."""
    from tabelas.models import TbCambio
    todas = dict(TbCambio._meta.get_field('cam_moeda').choices or [])
    usadas = set(TbCambio.objects.filter(tbcenarios_id=cenario_id).values_list('cam_moeda', flat=True))
    return {codigo: nome for codigo, nome in todas.items() if codigo not in usadas}


def _lista_periodos_cambio(cenario, cambio):
    from tabelas.models import TbCambioDaugther
    filhas = TbCambioDaugther.objects.filter(mae_id=cambio.id, tbcenarios_id=cenario.id).order_by('dau_order')
    if not filhas:
        return ""
    linhas = [f"- {_dau_order_para_periodo(cenario, f.dau_order)}: {f.dau_valor}" for f in filhas]
    return "Períodos e valores atuais:\n" + "\n".join(linhas) + "\n\n"


def _cambio_em_uso(cambio):
    """Mesma lógica de _indicador_em_uso -- descobre via introspecção se
    esse câmbio está referenciado em algum outro lugar do sistema."""
    usos = []
    for related in cambio._meta.related_objects:
        if related.related_model._meta.model_name == 'tbcambiodaugther':
            continue
        accessor = related.get_accessor_name()
        qtd = getattr(cambio, accessor).count()
        if qtd > 0:
            nome_modelo = related.related_model._meta.verbose_name
            usos.append(f"{nome_modelo} ({qtd} registro(s))")
    return usos


# ---------------------------------------------------------------------
# Sub-fluxo: editar valor existente
# ---------------------------------------------------------------------
def _etapa_cam_editar_moeda(estado, texto, cenario):
    cambio = _buscar_cambio(cenario.id, texto)
    if cambio is None:
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['cambio_id'] = cambio.id
    dados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_editar_modo'
    estado.save()

    return (
        f"Câmbio: **{cambio.get_cam_moeda_display()}**.\n\n"
        "Como você quer ajustar os valores? Digite:\n"
        "- **manual** -- pra mudar um período de cada vez, digitando aqui\n"
        "- **planilha** -- pra baixar uma planilha, preencher, e reenviar de uma vez\n"
        "(ou \"cancelar\")"
    )


def _etapa_cam_editar_modo(estado, texto, cenario):
    escolha = texto.strip().lower()
    dados = estado.dados_coletados

    if escolha in ('não', 'nao', 'n'):
        _encerrar_fluxo(estado)
        return "Combinado, deixei como está."

    if escolha in ('planilha', 'excel', 'xlsx'):
        from tabelas.models import TbCambioDaugther
        url = _gerar_planilha_periodos(cenario, dados['cambio_id'], TbCambioDaugther, 'cam', dados['cambio_nome'])
        _encerrar_fluxo(estado)
        return (
            f"[📥 Baixar planilha]({url})\n\n"
            "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
            "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
            "pelo arquivo, não precisa repetir a moeda."
        )

    if escolha in ('manual', 'digitar', 'digitando', 'm'):
        from tabelas.models import TbCambio
        cambio = TbCambio.objects.filter(id=dados['cambio_id']).first()
        if cambio is None:
            _encerrar_fluxo(estado)
            return "A taxa de câmbio não existe mais. Cancelei o fluxo."
        estado.etapa_atual = 'cam_editar_periodo'
        estado.save()
        return (
            f"{_lista_periodos_cambio(cenario, cambio)}"
            f"Qual **período** você quer mudar? (formato {_exemplo_periodo(cenario)})"
        )

    return "Não entendi. Digita **manual** ou **planilha** (ou \"cancelar\")."


def _etapa_cam_editar_periodo(estado, texto, cenario):
    # 🌟 Se ainda assim o usuário digitar "planilha" aqui (mesmo já tendo
    # passado pela escolha de modo), atende do mesmo jeito -- rede de
    # segurança, não é mais o caminho principal.
    if 'planilha' in texto.strip().lower():
        from tabelas.models import TbCambioDaugther
        dados = estado.dados_coletados
        url = _gerar_planilha_periodos(cenario, dados['cambio_id'], TbCambioDaugther, 'cam', dados['cambio_nome'])
        _encerrar_fluxo(estado)
        return (
            f"[📥 Baixar planilha]({url})\n\n"
            "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
            "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente pelo "
            "arquivo, não precisa repetir a moeda. Encerrei esse fluxo aqui, pra sua próxima "
            "mensagem já ser reconhecida certinho."
        )

    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dau_order = _periodo_para_dau_order(cenario, periodo)
    from tabelas.models import TbCambioDaugther
    filha = TbCambioDaugther.objects.filter(
        mae_id=estado.dados_coletados['cambio_id'], tbcenarios_id=cenario.id, dau_order=dau_order
    ).first()
    if filha is None:
        return f"O período {periodo} está fora do intervalo do cenário. Informa outro período, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['periodo'] = periodo
    dados['dau_order'] = dau_order
    dados['valor_atual'] = str(filha.dau_valor)
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_editar_valor'
    estado.save()

    return f"Valor atual em {periodo}: **{filha.dau_valor}**.\n\nQual o **novo valor**?"


def _etapa_cam_editar_valor(estado, texto, cenario):
    try:
        valor_novo = Decimal(texto.strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return "Não entendi o valor. Informa um número (ex: 5.20), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['valor_novo'] = str(valor_novo)
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_editar_confirmar'
    estado.save()

    return (
        "Resumo:\n"
        f"- Câmbio: {dados['cambio_nome']}\n"
        f"- Período: {dados['periodo']}\n"
        f"- Valor: {dados['valor_atual']} → {valor_novo}\n\n"
        "Confirma a mudança? (sim / não)"
    )


def _etapa_cam_editar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não mudei nada."

    dados = estado.dados_coletados
    from tabelas.models import TbCambioDaugther
    try:
        filha = TbCambioDaugther.objects.get(
            mae_id=dados['cambio_id'], tbcenarios_id=cenario.id, dau_order=dados['dau_order']
        )
        filha.dau_valor = Decimal(dados['valor_novo'])
        filha.save()
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao salvar: {e}. Cancelei o fluxo -- pode tentar de novo."

    _encerrar_fluxo(estado)
    return f"✅ Câmbio **{dados['cambio_nome']}**, período **{dados['periodo']}**, atualizado para **{dados['valor_novo']}**."


# ---------------------------------------------------------------------
# Sub-fluxo: eliminar câmbio
# ---------------------------------------------------------------------
def _etapa_cam_eliminar_moeda(estado, texto, cenario):
    cambio = _buscar_cambio(cenario.id, texto)
    if cambio is None:
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    usos = _cambio_em_uso(cambio)
    if usos:
        _encerrar_fluxo(estado)
        lista_usos = "\n".join(f"- {u}" for u in usos)
        return (
            f"⚠️ Não posso eliminar a taxa de câmbio **{cambio.get_cam_moeda_display()}** -- ela está em uso em:\n"
            f"{lista_usos}\n\nRemove essas referências primeiro (pelo Admin) antes de tentar eliminar essa."
        )

    dados = estado.dados_coletados
    dados['cambio_id'] = cambio.id
    dados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_eliminar_confirmar'
    estado.save()

    return (
        f"⚠️ Confirma que quer **eliminar** a taxa de câmbio **{cambio.get_cam_moeda_display()}**? "
        "Isso apaga o câmbio E todos os valores dele, em todos os períodos -- não tem como desfazer. (sim / não)"
    )


def _etapa_cam_eliminar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não eliminei nada."

    dados = estado.dados_coletados
    from tabelas.models import TbCambio
    try:
        cambio = TbCambio.objects.filter(id=dados['cambio_id'], tbcenarios_id=cenario.id).first()
        if cambio is None:
            _encerrar_fluxo(estado)
            return f"A taxa de câmbio {dados['cambio_nome']} já não existe mais. Nada a fazer."

        usos = _cambio_em_uso(cambio)
        if usos:
            _encerrar_fluxo(estado)
            lista_usos = "\n".join(f"- {u}" for u in usos)
            return (
                f"⚠️ A taxa de câmbio {dados['cambio_nome']} passou a estar em uso enquanto conversávamos:\n"
                f"{lista_usos}\n\nCancelei a eliminação, por segurança."
            )

        nome = cambio.get_cam_moeda_display()
        cambio.delete()
    except ProtectedError:
        _encerrar_fluxo(estado)
        return (
            f"⚠️ Não consegui eliminar a taxa de câmbio {dados['cambio_nome']} -- o banco recusou porque ela "
            "ainda está em uso em algum lugar."
        )
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao eliminar: {e}. Cancelei o fluxo -- pode tentar de novo."

    _encerrar_fluxo(estado)
    return f"✅ Taxa de câmbio **{nome}** eliminada, junto com todos os valores dela."


# ---------------------------------------------------------------------
# Sub-fluxo: criar taxa de câmbio nova
# ---------------------------------------------------------------------
def _etapa_cam_criar_moeda(estado, texto, cenario):
    disponiveis = _moedas_disponiveis(cenario.id)
    texto_norm = texto.strip().upper()

    codigo_escolhido = None
    if texto_norm in disponiveis:
        codigo_escolhido = texto_norm
    else:
        for codigo, nome in disponiveis.items():
            if nome.upper() == texto_norm:
                codigo_escolhido = codigo
                break

    if codigo_escolhido is None:
        if not disponiveis:
            _encerrar_fluxo(estado)
            return "Não tem mais nenhuma moeda disponível pra cadastrar nesse cenário -- todas já foram usadas."
        opcoes = ", ".join(f"{c} ({n})" for c, n in disponiveis.items())
        return f"Não entendi. Escolhe uma dessas: {opcoes}, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['cam_moeda'] = codigo_escolhido
    dados['cam_moeda_nome'] = disponiveis[codigo_escolhido]
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_criar_valor_inicial'
    estado.save()
    return f"Moeda: **{disponiveis[codigo_escolhido]} ({codigo_escolhido})**.\n\nQual o **valor inicial** dessa taxa de câmbio?"


def _etapa_cam_criar_valor_inicial(estado, texto, cenario):
    try:
        valor_inicial = Decimal(texto.strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return "Não entendi o valor. Informa um número (ex: 5.20), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['valor_inicial'] = str(valor_inicial)
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_criar_observacao'
    estado.save()
    return f"Valor inicial: **{valor_inicial}**.\n\nAlguma **observação**? (ou \"nenhuma\")"


def _etapa_cam_criar_observacao(estado, texto, cenario):
    observacao = '' if texto.strip().lower() in ('nenhuma', 'nao', 'não', 'n/a', 'na') else texto.strip()

    dados = estado.dados_coletados
    dados['observacao'] = observacao
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_criar_confirmar'
    estado.save()

    return (
        "Resumo da taxa de câmbio a criar:\n"
        f"- Moeda: {dados['cam_moeda_nome']} ({dados['cam_moeda']})\n"
        f"- Valor inicial: {dados['valor_inicial']}\n"
        f"- Observação: {observacao or 'nenhuma'}\n\n"
        "Confirma a criação? (sim / não)"
    )


def _etapa_cam_criar_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não criei a taxa de câmbio."

    dados = estado.dados_coletados
    from tabelas.models import TbCambio
    try:
        cambio = TbCambio(
            cam_moeda=dados['cam_moeda'],
            valor_inicial=Decimal(dados['valor_inicial']),
            cam_observacao=dados['observacao'] or None,
        )
        # 🌟 tbcenarios é preenchido sozinho pelo sinal pre_save
        # (atualiza_cenario) -- excluído da validação aqui, senão o
        # full_clean() reclamaria de campo obrigatório vazio.
        cambio.full_clean(exclude=['tbcenarios'])
        cambio.save()
    except ValidationError as e:
        _encerrar_fluxo(estado)
        return f"Não consegui criar a taxa de câmbio: {'; '.join(e.messages)}. Cancelei o fluxo -- pode tentar de novo."
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro ao criar: {e}. Cancelei o fluxo -- pode tentar de novo."

    # 🌟 NOVO: em vez de encerrar aqui, oferece já ajustar valores de
    # períodos específicos -- reaproveita a mesma etapa 'cam_editar_modo'
    # usada no fluxo de editar.
    dados['cambio_id'] = cambio.id
    dados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_editar_modo'
    estado.save()

    return (
        f"✅ Taxa de câmbio **{cambio.get_cam_moeda_display()}** criada, com valor inicial de **{dados['valor_inicial']}** em todos os períodos.\n\n"
        "Quer ajustar algum período específico agora? Digite:\n"
        "- **manual** -- pra mudar um período de cada vez\n"
        "- **planilha** -- pra baixar, preencher, e reenviar de uma vez\n"
        "- **não** -- se já está bom assim"
    )


# ---------------------------------------------------------------------
# Sub-fluxo: ajuste em massa
# ---------------------------------------------------------------------
def _etapa_cam_massa_moeda(estado, texto, cenario):
    cambio = _buscar_cambio(cenario.id, texto)
    if cambio is None:
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['cambio_id'] = cambio.id
    dados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_massa_periodo_inicio'
    estado.save()

    return (
        f"Câmbio: **{cambio.get_cam_moeda_display()}**.\n\n"
        f"Qual o **período inicial** do reajuste? (formato {_exemplo_periodo(cenario)}, "
        "ou \"todos\" pra aplicar em todo o cenário)"
    )


def _etapa_cam_massa_periodo_inicio(estado, texto, cenario):
    dados = estado.dados_coletados

    if texto.strip().lower() in ('todos', 'tudo', 'todo'):
        total = _total_periodos(cenario)
        dados['dau_order_inicio'] = 1
        dados['dau_order_fim'] = total
        dados['periodo_inicio_texto'] = 'início'
        dados['periodo_fim_texto'] = 'fim'
        estado.dados_coletados = dados
        estado.etapa_atual = 'cam_massa_percentual'
        estado.save()
        return f"Vai aplicar em **todos os {total} períodos** do cenário.\n\nQual o **percentual de reajuste**? (ex: 5, ou -3.5)"

    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dados['dau_order_inicio'] = _periodo_para_dau_order(cenario, periodo)
    dados['periodo_inicio_texto'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_massa_periodo_fim'
    estado.save()

    return f"Início: **{periodo}**.\n\nQual o **período final** do reajuste? (formato {_exemplo_periodo(cenario, 1)})"


def _etapa_cam_massa_periodo_fim(estado, texto, cenario):
    periodo, erro = _validar_periodo(cenario.cen_tipo, texto)
    if erro:
        return erro

    dau_order_fim = _periodo_para_dau_order(cenario, periodo)
    dados = estado.dados_coletados
    if dau_order_fim < dados['dau_order_inicio']:
        return f"O fim ({periodo}) não pode ser antes do início ({dados['periodo_inicio_texto']}). Informa outro período final."

    dados['dau_order_fim'] = dau_order_fim
    dados['periodo_fim_texto'] = periodo
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_massa_percentual'
    estado.save()
    return f"Fim: **{periodo}**.\n\nQual o **percentual de reajuste**? (ex: 5, ou -3.5)"


def _etapa_cam_massa_percentual(estado, texto, cenario):
    try:
        percentual = Decimal(texto.strip().replace(',', '.').replace('%', ''))
    except (InvalidOperation, ValueError):
        return "Não entendi o percentual. Informa um número (ex: 5 ou -3.5), ou \"cancelar\"."

    dados = estado.dados_coletados
    dados['percentual'] = str(percentual)
    estado.dados_coletados = dados
    estado.etapa_atual = 'cam_massa_confirmar'
    estado.save()

    from tabelas.models import TbCambioDaugther
    qtd = TbCambioDaugther.objects.filter(
        mae_id=dados['cambio_id'], tbcenarios_id=cenario.id,
        dau_order__gte=dados['dau_order_inicio'], dau_order__lte=dados['dau_order_fim'],
    ).count()

    sinal = '+' if percentual >= 0 else ''
    return (
        "Resumo do reajuste:\n"
        f"- Câmbio: {dados['cambio_nome']}\n"
        f"- Período: {dados['periodo_inicio_texto']} a {dados['periodo_fim_texto']} ({qtd} período(s))\n"
        f"- Reajuste: {sinal}{percentual}%\n\n"
        f"⚠️ Isso vai salvar {qtd} registro(s), um de cada vez -- pode levar um instante a mais que uma edição única.\n\n"
        "Confirma a aplicação? (sim / não)"
    )


def _etapa_cam_massa_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não apliquei o reajuste."

    dados = estado.dados_coletados
    percentual = Decimal(dados['percentual'])
    fator = Decimal('1') + (percentual / Decimal('100'))

    from tabelas.models import TbCambioDaugther
    filhas = TbCambioDaugther.objects.filter(
        mae_id=dados['cambio_id'], tbcenarios_id=cenario.id,
        dau_order__gte=dados['dau_order_inicio'], dau_order__lte=dados['dau_order_fim'],
    )

    qtd = 0
    try:
        for filha in filhas:
            filha.dau_valor = (filha.dau_valor * fator).quantize(Decimal('0.0001'))
            filha.save()
            qtd += 1
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro no meio do reajuste (parei em {qtd} período(s) já atualizados): {e}."

    _encerrar_fluxo(estado)
    return f"✅ Reajuste de **{dados['percentual']}%** aplicado em **{qtd} período(s)** da taxa de câmbio **{dados['cambio_nome']}**."


# ---------------------------------------------------------------------
# Sub-fluxo: planilha (baixar template, reenviar preenchida, confirmar)
# ---------------------------------------------------------------------
def iniciar_download_planilha_cambio(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    cambio = _buscar_cambio_na_mensagem(cenario.id, mensagem)
    if cambio is None:
        return (
            f"Não consegui identificar qual taxa de câmbio -- me diz a moeda na mesma mensagem "
            f"(ex: \"baixar planilha do câmbio dólar\").\n\n{_lista_cambios(cenario.id)}"
        )

    from tabelas.models import TbCambioDaugther
    url = _gerar_planilha_periodos(cenario, cambio.id, TbCambioDaugther, 'cam', cambio.cam_moeda)
    return (
        f"Aqui está a planilha da taxa de câmbio **{cambio.get_cam_moeda_display()}**, com os períodos e valores atuais:\n\n"
        f"[📥 Baixar planilha]({url})\n\n"
        "Edita os valores que quiser (sem mudar a coluna Período), salva, sobe de volta na área de "
        "Relatórios (arrastar-e-soltar), marca a caixinha dela, e clica no botão abaixo "
        "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
        "pelo arquivo, não precisa repetir a moeda."
    )


def _processar_planilha_cambio(usuario, mensagem, pdf_ids):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    import os
    from .models import RelatorioPDF
    from tabelas.models import TbCambio

    relatorio = None
    cambio = None
    candidatos_xlsx = []
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True).first()
        if r and r.arquivo and r.arquivo.name.lower().endswith('.xlsx'):
            candidatos_xlsx.append(r)
            nome_arquivo = os.path.basename(r.arquivo.name)
            cambio_id = _identificar_planilha_por_nome(nome_arquivo, 'cam', cenario.id)
            if cambio_id:
                achado = TbCambio.objects.filter(id=cambio_id, tbcenarios_id=cenario.id).first()
                if achado:
                    relatorio = r
                    cambio = achado
                    break

    if cambio is None:
        cambio = _buscar_cambio_na_mensagem(cenario.id, mensagem)
        if cambio is not None and candidatos_xlsx:
            relatorio = candidatos_xlsx[0]

    if cambio is None:
        return f"Não consegui identificar qual taxa de câmbio essa planilha é. Me diz a moeda na mensagem.\n\n{_lista_cambios(cenario.id)}"
    if relatorio is None:
        return "Não encontrei nenhuma planilha XLSX marcada na lista de Relatórios. Marca a caixinha do arquivo que você subiu."

    linhas = _ler_planilha_periodos(relatorio.arquivo.path)
    if not linhas:
        _descartar_planilha(relatorio.id)
        return "Não consegui ler nenhuma linha válida dessa planilha. Confere se ela tem as colunas Período e Valor."

    from tabelas.models import TbCambioDaugther
    mudancas = []
    erros = []
    for periodo_texto, valor in linhas:
        try:
            dau_order = _periodo_para_dau_order(cenario, periodo_texto)
        except (ValueError, IndexError):
            erros.append(f"período \"{periodo_texto}\" com formato inválido")
            continue
        filha = TbCambioDaugther.objects.filter(mae_id=cambio.id, tbcenarios_id=cenario.id, dau_order=dau_order).first()
        if filha is None:
            erros.append(f"período {periodo_texto} não existe nesse cenário")
            continue
        try:
            valor_novo = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            erros.append(f"valor inválido em {periodo_texto}: {valor}")
            continue
        if valor_novo != filha.dau_valor:
            mudancas.append((filha.id, periodo_texto, str(filha.dau_valor), str(valor_novo)))

    aviso_erros = ("\n\n⚠️ Alguns problemas encontrados:\n" + "\n".join(f"- {e}" for e in erros)) if erros else ""

    if not mudancas:
        _descartar_planilha(relatorio.id)
        return f"Não encontrei nenhuma mudança de valor nessa planilha, comparado ao que já está salvo.{aviso_erros}"

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CAMBIO
    estado.etapa_atual = 'cam_planilha_confirmar'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'cambio_id': cambio.id,
        'cambio_nome': cambio.get_cam_moeda_display(),
        'relatorio_id': relatorio.id,
        'mudancas': mudancas,
    }
    estado.save()

    linhas_resumo = "\n".join(f"- {p}: {antigo} → {novo}" for _, p, antigo, novo in mudancas)
    return (
        f"Encontrei {len(mudancas)} mudança(s) pra taxa de câmbio **{cambio.get_cam_moeda_display()}**:\n"
        f"{linhas_resumo}"
        f"{aviso_erros}\n\n"
        "Confirma a aplicação? (sim / não)"
    )


def _etapa_cam_planilha_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    if resposta not in ('sim', 's', 'yes', 'y'):
        _descartar_planilha(dados['relatorio_id'])
        _encerrar_fluxo(estado)
        return "Ok, não apliquei nada da planilha."

    from tabelas.models import TbCambioDaugther, TbCambio

    qtd = 0
    try:
        for filha_id, periodo, antigo, novo in dados['mudancas']:
            filha = TbCambioDaugther.objects.get(id=filha_id)
            filha.dau_valor = Decimal(novo)
            filha.save()
            qtd += 1
    except Exception as e:
        _encerrar_fluxo(estado)
        return f"Deu erro no meio da aplicação (parei em {qtd} período(s)): {e}."

    aviso_fonte = ""
    try:
        from .models import RelatorioPDF
        relatorio = RelatorioPDF.objects.filter(id=dados['relatorio_id']).first()
        cambio = TbCambio.objects.filter(id=dados['cambio_id']).first()
        if relatorio and cambio and relatorio.arquivo:
            from django.core.files.base import ContentFile
            import os
            relatorio.arquivo.open('rb')
            conteudo = relatorio.arquivo.read()
            relatorio.arquivo.close()
            nome_arquivo = relatorio.arquivo.name.split('/')[-1]
            cambio.cam_fonte.save(nome_arquivo, ContentFile(conteudo), save=True)

            caminho_antigo = relatorio.arquivo.path
            relatorio.delete()
            if os.path.exists(caminho_antigo):
                os.remove(caminho_antigo)
    except Exception as e:
        aviso_fonte = f"\n\n(A planilha não pôde ser movida pra Fonte automaticamente: {e})"

    _encerrar_fluxo(estado)
    return f"✅ {qtd} período(s) da taxa de câmbio **{dados['cambio_nome']}** atualizados a partir da planilha.{aviso_fonte}"


_HANDLERS_CAMBIO = {
    'cam_editar_moeda': _etapa_cam_editar_moeda,
    'cam_editar_modo': _etapa_cam_editar_modo,
    'cam_editar_periodo': _etapa_cam_editar_periodo,
    'cam_editar_valor': _etapa_cam_editar_valor,
    'cam_editar_confirmar': _etapa_cam_editar_confirmar,
    'cam_planilha_confirmar': _etapa_cam_planilha_confirmar,
    'cam_eliminar_moeda': _etapa_cam_eliminar_moeda,
    'cam_eliminar_confirmar': _etapa_cam_eliminar_confirmar,
    'cam_criar_moeda': _etapa_cam_criar_moeda,
    'cam_criar_valor_inicial': _etapa_cam_criar_valor_inicial,
    'cam_criar_observacao': _etapa_cam_criar_observacao,
    'cam_criar_confirmar': _etapa_cam_criar_confirmar,
    'cam_massa_moeda': _etapa_cam_massa_moeda,
    'cam_massa_periodo_inicio': _etapa_cam_massa_periodo_inicio,
    'cam_massa_periodo_fim': _etapa_cam_massa_periodo_fim,
    'cam_massa_percentual': _etapa_cam_massa_percentual,
    'cam_massa_confirmar': _etapa_cam_massa_confirmar,
}


# =======================================================================
# Fluxo: processar_cenario -- limpar, otimizar ou consolidar o cenário
# ATIVO, a qualquer momento (não só durante a criação de um cenário novo).
# Reaproveita a mesma lógica de disparo/acompanhamento já usada no ciclo
# de criação, mas cada ação aqui é INDEPENDENTE: dispara só aquela etapa
# e para, sem encadear automaticamente pra próxima -- o usuário decide se
# quer seguir pra próxima etapa depois.
# =======================================================================

FLUXO_PROCESSAR = 'processar_cenario'

MENSAGENS_FLAG = {
    1: 'CONSOLIDADO', 2: 'LIMPO', 3: 'OTIMIZADO',
    4: 'OTIMIZANDO', 5: 'LIMPANDO', 6: 'CONSOLIDANDO', 7: 'ATUALIZANDO FLUXOS',
}


def iniciar_consulta_status(usuario, mensagem=""):
    """
    Consulta somente informativa (não é bem um "fluxo") -- mostra o status
    atual do cenário ativo. Se ele estiver LIMPO ou OTIMIZADO, já pergunta
    se quer seguir pro próximo passo, reaproveitando as mesmas etapas de
    encadeamento do fluxo de processar (proc_pos_limpeza_otimizar /
    proc_pos_otimizacao_consolidar).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    if cenario.flag is None:
        status_atual = "ainda não processado (nunca foi limpo)"
    else:
        status_atual = MENSAGENS_FLAG.get(cenario.flag, f"desconhecido (flag={cenario.flag})")

    if cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        return (
            f"O cenário **{cenario.id}/{cenario.cen_nome}** está: **{status_atual}** "
            "(operação em andamento). Clica em \"Verificar\" daqui a pouco pra conferir se já terminou."
        )

    if cenario.flag == 2:  # LIMPO
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_limpeza_otimizar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.id}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **otimização**? (sim / não)"

    if cenario.flag == 3:  # OTIMIZADO
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_otimizacao_consolidar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.id}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **consolidação**? (sim / não)"

    if cenario.flag == 1 or cenario.flag is None:  # CONSOLIDADO ou nunca processado
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_consolidacao_limpar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.id}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **limpeza** (pra começar o ciclo de novo)? (sim / não)"

    return f"O cenário **{cenario.id}/{cenario.cen_nome}** está: **{status_atual}**."


def iniciar_ciclo_completo(usuario, mensagem):
    """
    "Limpar, otimizar e consolidar o cenário" numa tacada só -- dispara
    limpeza, e ao terminar segue AUTOMATICAMENTE pra otimização, e ao
    terminar segue automaticamente pra consolidação, sem perguntar
    confirmação no meio (diferente do fluxo de ação única, que pergunta
    a cada passo). O usuário só acompanha via "Verificar".
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    if cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        return (
            f"O cenário {cenario.id}/{cenario.cen_nome} já está com uma operação em andamento agora "
            f"({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}). Espera terminar antes de disparar o ciclo completo."
        )

    return _disparar_ciclo_completo(usuario, cenario)


def _disparar_ciclo_completo(usuario, cenario):
    from fluxos.models import TbFluxoProducaoDaugther01, TbFluxoProducao
    from django.db import connection
    from .tasks import limpar_cenario_celery

    cenario_id = cenario.id

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=cenario_id, flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) com I/O desatualizado. Usa \"Atualizar Fluxos\" no Admin antes de tentar de novo."

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) com custo variável zerado. Verifica isso no Admin antes de tentar de novo."

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 5 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    limpar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_ciclo_aguardando_limpeza'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome}
    estado.save()

    return (
        f"Disparei o **ciclo completo** (limpar → otimizar → consolidar) do cenário "
        f"**{cenario_id}/{cenario.cen_nome}** 🔁 -- vou seguir automaticamente de uma etapa pra outra, "
        "sem perguntar no meio. Clica em \"Verificar\" quando quiser conferir o andamento."
    )


def _etapa_proc_ciclo_aguardando_limpeza(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 2:  # LIMPO -- segue direto pra otimização, sem perguntar
        return _disparar_otimizacao_ciclo(estado.usuario, cenario)

    if cenario.flag == 5:
        return f"Ainda limpando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) durante a limpeza -- melhor conferir manualmente no Admin. Cancelei o ciclo aqui."


def _disparar_otimizacao_ciclo(usuario, cenario):
    from django.db import connection
    from .tasks import otimizar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 4 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 4 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)

    sql = "select conta_periodos(" + str(cenario_id) + ")"
    cursor.execute(sql)
    total_periodos = cursor.fetchone()[0]
    cursor.close()

    from otimizacao.models import TbProdutoMercadoFluxo
    total_variaveis = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cenario_id, flag=True).count()

    for i in range(total_periodos):
        if TbCenariosDaugther.objects.get(mae_id=cenario_id, dau_order=i + 1).otimizar:
            otimizar_cenario_celery.delay(i, cenario_id, total_variaveis)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_ciclo_aguardando_otimizacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome, 'total_periodos': total_periodos}
    estado.save()

    return (
        f"Limpeza concluída! Disparei a **otimização** do cenário "
        f"**{cenario_id}/{cenario.cen_nome}** ({total_periodos} período(s)) em segundo plano. "
        "Clica em \"Verificar\" quando quiser conferir o progresso."
    )


def _etapa_proc_ciclo_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    total_periodos = dados.get('total_periodos', 0)

    total_otimizado = (
        TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=1).count()
        + TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=0).count()
    )

    if total_otimizado < total_periodos:
        return (
            f"Ainda otimizando o cenário **{cenario_id}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} período(s) concluídos). Clica em \"Verificar\" daqui a pouco."
        )

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag != 3:
        return "Períodos todos processados, aguardando o cenário fechar como OTIMIZADO. Clica em \"Verificar\" em instantes."

    return _disparar_consolidacao_ciclo(estado.usuario, cenario)


def _disparar_consolidacao_ciclo(usuario, cenario):
    from django.db import connection
    from .tasks import consolidar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 6 where id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    consolidar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_ciclo_aguardando_consolidacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome}
    estado.save()

    return (
        f"Otimização concluída! Disparei a **consolidação** do cenário "
        f"**{cenario_id}/{cenario.cen_nome}** em segundo plano. Clica em \"Verificar\" quando quiser conferir se já terminou."
    )


def _etapa_proc_ciclo_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 1:  # CONSOLIDADO
        _encerrar_fluxo(estado)
        return f"✅ Ciclo completo! Cenário **{cenario_id}/{cenario_nome}** limpo, otimizado, e consolidado."

    if cenario.flag == 6:
        return f"Ainda consolidando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) durante a consolidação -- melhor conferir manualmente no Admin."


def iniciar_fluxo_processar(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    texto = (mensagem or '').lower()
    if re.search(r'limp[ae]r?', texto):
        acao = 'limpar'
    elif re.search(r'otimiz[ae]r?', texto):
        acao = 'otimizar'
    elif re.search(r'consolid[ae]r?', texto):
        acao = 'consolidar'
    else:
        return "Não entendi se você quer **limpar**, **otimizar**, ou **consolidar** o cenário ativo. Pode repetir dizendo qual dessas ações?"

    # 🌟 Trava comum: não dá pra disparar nada novo se já tem uma operação
    # em andamento nesse cenário agora (mesma checagem usada em mudar_cenario).
    if cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        return (
            f"O cenário {cenario.id}/{cenario.cen_nome} já está com uma operação em andamento agora "
            f"({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}). Espera terminar antes de disparar outra."
        )

    # 🌟 Restrições da cadeia: só otimiza se estiver LIMPO (flag=2), só
    # consolida se estiver OTIMIZADO (flag=3).
    if acao == 'otimizar' and cenario.flag != 2:
        status_atual = MENSAGENS_FLAG.get(cenario.flag, f'flag={cenario.flag}')
        return (
            f"Não dá pra otimizar o cenário **{cenario.id}/{cenario.cen_nome}** agora -- ele precisa "
            f"estar **LIMPO** primeiro, e o status atual é **{status_atual}**. Limpa o cenário antes "
            "de otimizar."
        )

    if acao == 'consolidar' and cenario.flag != 3:
        status_atual = MENSAGENS_FLAG.get(cenario.flag, f'flag={cenario.flag}')
        return (
            f"Não dá pra consolidar o cenário **{cenario.id}/{cenario.cen_nome}** agora -- ele precisa "
            f"estar **OTIMIZADO** primeiro, e o status atual é **{status_atual}**. Otimiza o cenário "
            "antes de consolidar."
        )

    if acao == 'limpar':
        return _disparar_limpeza_standalone(usuario, cenario)
    elif acao == 'otimizar':
        return _disparar_otimizacao_standalone(usuario, cenario)
    else:
        return _disparar_consolidacao_standalone(usuario, cenario)


def _etapa_proc_pos_consolidacao_limpar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{cenario_id}/{cenario_nome}** fica como está."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    return _disparar_limpeza_standalone(estado.usuario, cenario)


def _disparar_limpeza_standalone(usuario, cenario):
    from fluxos.models import TbFluxoProducaoDaugther01, TbFluxoProducao
    from django.db import connection
    from .tasks import limpar_cenario_celery

    cenario_id = cenario.id

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=cenario_id, flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) com I/O desatualizado. Usa \"Atualizar Fluxos\" no Admin antes de tentar de novo."

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) com custo variável zerado. Verifica isso no Admin antes de tentar de novo."

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 5 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    limpar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_limpeza'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome}
    estado.save()

    return (
        f"Disparei a **limpeza** do cenário **{cenario_id}/{cenario.cen_nome}** em segundo plano. "
        "Clica em \"Verificar\" quando quiser conferir se já terminou."
    )


def _etapa_proc_aguardando_limpeza(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 2:  # LIMPO
        estado.etapa_atual = 'proc_pos_limpeza_otimizar'
        estado.save()
        return f"✅ Cenário **{cenario_id}/{cenario_nome}** limpo! Quer que eu já dispare a **otimização**? (sim / não)"

    if cenario.flag == 5:  # ainda limpando
        return f"Ainda limpando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin. Cancelei o acompanhamento automático aqui."


def _etapa_proc_pos_limpeza_otimizar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{cenario_id}/{cenario_nome}** fica limpo por enquanto. É só pedir pra otimizar quando quiser."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    return _disparar_otimizacao_standalone(estado.usuario, cenario)


def _disparar_otimizacao_standalone(usuario, cenario):
    from django.db import connection
    from .tasks import otimizar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 4 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 4 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)

    sql = "select conta_periodos(" + str(cenario_id) + ")"
    cursor.execute(sql)
    total_periodos = cursor.fetchone()[0]
    cursor.close()

    from otimizacao.models import TbProdutoMercadoFluxo
    total_variaveis = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cenario_id, flag=True).count()

    for i in range(total_periodos):
        if TbCenariosDaugther.objects.get(mae_id=cenario_id, dau_order=i + 1).otimizar:
            otimizar_cenario_celery.delay(i, cenario_id, total_variaveis)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_otimizacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome, 'total_periodos': total_periodos}
    estado.save()

    return (
        f"Disparei a **otimização** do cenário **{cenario_id}/{cenario.cen_nome}** "
        f"({total_periodos} período(s)) em segundo plano. Clica em \"Verificar\" quando quiser conferir o progresso."
    )


def _etapa_proc_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    total_periodos = dados.get('total_periodos', 0)

    total_otimizado = (
        TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=1).count()
        + TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=0).count()
    )

    if total_otimizado < total_periodos:
        return (
            f"Ainda otimizando o cenário **{cenario_id}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} período(s) concluídos). "
            "Clica em \"Verificar\" daqui a pouco."
        )

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag != 3:
        return "Períodos todos processados, aguardando o cenário fechar como OTIMIZADO. Clica em \"Verificar\" em instantes."

    estado.etapa_atual = 'proc_pos_otimizacao_consolidar'
    estado.save()
    return f"✅ Cenário **{cenario_id}/{cenario_nome}** otimizado! Quer que eu já dispare a **consolidação**? (sim / não)"


def _etapa_proc_pos_otimizacao_consolidar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{cenario_id}/{cenario_nome}** fica otimizado por enquanto. É só pedir pra consolidar quando quiser."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    return _disparar_consolidacao_standalone(estado.usuario, cenario)


def _disparar_consolidacao_standalone(usuario, cenario):
    from django.db import connection
    from .tasks import consolidar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 6 where id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    consolidar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_consolidacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome}
    estado.save()

    return (
        f"Disparei a **consolidação** do cenário **{cenario_id}/{cenario.cen_nome}** em segundo plano. "
        "Clica em \"Verificar\" quando quiser conferir se já terminou."
    )


def _etapa_proc_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {cenario_id}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 1:  # CONSOLIDADO
        _encerrar_fluxo(estado)
        return f"✅ Cenário **{cenario_id}/{cenario_nome}** consolidado!"

    if cenario.flag == 6:  # ainda consolidando
        return f"Ainda consolidando o cenário **{cenario_id}/{cenario_nome}**. Clica em \"Verificar\" daqui a pouco."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{cenario_id}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin."


def _processar_fluxo_processar(estado, texto):
    handler = _HANDLERS_PROCESSAR.get(estado.etapa_atual)
    if handler is None:
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."
    return handler(estado, texto)


_HANDLERS_PROCESSAR = {
    'proc_ciclo_aguardando_limpeza': _etapa_proc_ciclo_aguardando_limpeza,
    'proc_ciclo_aguardando_otimizacao': _etapa_proc_ciclo_aguardando_otimizacao,
    'proc_ciclo_aguardando_consolidacao': _etapa_proc_ciclo_aguardando_consolidacao,
    'proc_pos_consolidacao_limpar': _etapa_proc_pos_consolidacao_limpar,
    'proc_aguardando_limpeza': _etapa_proc_aguardando_limpeza,
    'proc_pos_limpeza_otimizar': _etapa_proc_pos_limpeza_otimizar,
    'proc_aguardando_otimizacao': _etapa_proc_aguardando_otimizacao,
    'proc_pos_otimizacao_consolidar': _etapa_proc_pos_otimizacao_consolidar,
    'proc_aguardando_consolidacao': _etapa_proc_aguardando_consolidacao,
}