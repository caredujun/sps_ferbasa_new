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
import json
from django.utils.translation import gettext as _

from .models import EstadoConversaAgente, TbCenarios, TbCenariosDaugther, PerfilUsuario
from tabelas.models import TbGrupoCenarios
from .contexto_usuario import eh_superuser_ou_superuser_empresa
from .tasks import remover_cenario_celery

FLUXO_CRIAR = 'criar_cenario'
FLUXO_MUDAR = 'mudar_cenario'
FLUXO_INDICADORES = 'indicadores'

# Status (TbCenarios.flag) que indicam uma operação em andamento -- não faz
# sentido mudar tipo/período de um cenário que está sendo limpo, otimizado,
# consolidado, ou tendo os fluxos atualizados nesse exato momento.
FLAGS_OPERACAO_EM_ANDAMENTO = {4: 'OTIMIZANDO', 5: 'LIMPANDO', 6: 'CONSOLIDANDO', 7: 'ATUALIZANDO FLUXOS'}

PALAVRAS_CANCELAR = ('cancelar', 'cancela', 'sair', 'parar', 'desistir')
PALAVRAS_MANTER = ('manter', 'mesmo', 'mesma', 'igual', 'não mudar', 'nao mudar')

# 🌟 NOVO: todas as etapas onde o wizard está esperando uma task do
# Celery terminar (duplicação, limpeza, otimização, consolidação,
# exclusão, exportação, envio de e-mail) -- usado por views.py pra
# sinalizar ao front-end que essa etapa pode ser SONDADA
# automaticamente (JS verificando sozinho a cada alguns segundos, sem
# precisar do usuário clicar em "Verificar"), mostrando só "Cancelar"
# enquanto isso.
ETAPAS_AGUARDANDO_CELERY = {
    'aguardando_duplicacao',
    'aguardando_limpeza',
    'aguardando_otimizacao',
    'aguardando_consolidacao',
    'aguardando_verificacao_filhas',
    'exportar_otimizacao_aguardando',
    'exportar_otimizacao_aguardando_email',
    'proc_ciclo_aguardando_limpeza',
    'proc_ciclo_aguardando_otimizacao',
    'proc_ciclo_aguardando_consolidacao',
    'proc_aguardando_limpeza',
    'proc_aguardando_otimizacao',
    'proc_aguardando_consolidacao',
    'cen_excluir_aguardando',
}

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


def etapa_atual_do_usuario(usuario):
    """
    🌟 NOVO: devolve a etapa_atual do wizard em andamento do usuário (ou
    None, se não tiver nenhum) -- usado por views.py logo depois de
    processar uma mensagem, pra decidir se essa etapa pode ser sondada
    automaticamente (ver ETAPAS_AGUARDANDO_CELERY).
    """
    estado = EstadoConversaAgente.objects.filter(usuario=usuario).first()
    return estado.etapa_atual if estado else None


def _get_estado(usuario):
    estado, _ = EstadoConversaAgente.objects.get_or_create(usuario=usuario)
    return estado


# 🌟 CORRIGIDO: substitui o padrão frágil
# "dados.get('cenario_criado_numero_sequencial', cenario_id)" usado nas
# etapas de acompanhar limpeza/otimização/consolidação -- essa chave só
# é gravada quando o cenário acabou de ser CRIADO nessa mesma conversa
# (ver iniciar_fluxo_criar_cenario); se o usuário limpar/otimizar um
# cenário que já existia de antes (ex: via "Ações Comuns", sem passar
# pelo assistente de criação), a chave nunca existe e o fallback caía
# pro id bruto do banco -- exatamente o "usar o id em vez do número
# sequencial" que já foi corrigido antes em outros lugares, mas que
# sobrevivia escondido atrás dessa variável intermediária. Busca sempre
# direto do banco -- confiável em qualquer caminho que levou até aqui.
def _numero_sequencial_por_id(cenario_id):
    if cenario_id is None:
        return None
    numero = TbCenarios.objects_real.filter(id=cenario_id).values_list('numero_sequencial', flat=True).first()
    return numero if numero is not None else cenario_id


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

    # 🌟 NOVO: mostra os últimos 10 nomes de cenário já usados PELA EMPRESA
    # do usuário, mais recentes primeiro -- ajuda a conferir nomes já
    # usados e a manter a lógica de nomenclatura da empresa (copiar um
    # nome existente e ajustar, em vez de inventar do zero).
    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    texto_nomes = ""
    if empresa_id is not None:
        # 🌟 CORRIGIDO: mostra numero_sequencial + nome (igual ao padrão
        # já usado na pergunta de "qual cenário copiar"), não só o nome
        # sozinho -- mais fácil de identificar de qual cenário se trata.
        cenarios = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('-id')[:10]
        if cenarios:
            lista = "\n".join(
                f"- {c.numero_sequencial if c.numero_sequencial is not None else c.id}: {c.cen_nome}" for c in cenarios
            )
            texto_nomes = f"Últimos cenários cadastrados (mais recente primeiro):\n{lista}\n\n"

    return (
        "Vamos criar um novo cenário! 🎬\n\n"
        f"{texto_nomes}"
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
            f"O cenário {cenario.numero_sequencial}/{cenario.cen_nome} está com uma operação em andamento "
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
            f"Vamos mudar o período do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 🔧 "
            f"(mantendo o tipo **{cenario.cen_tipo}**)\n\n"
            f"Período atual: **{cenario.cen_inicio}** a **{cenario.cen_fim}**\n\n"
            f"Qual o novo **início** do período? (formato {exemplo}, ou \"cancelar\" para desistir)"
        )

    estado.etapa_atual = 'tipo'
    estado.save()
    return (
        f"Vamos mudar o cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 🔧\n\n"
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
        # 🌟 Caso especial: cancelar durante o ACOMPANHAMENTO de uma
        # exclusão de cenário não cancela a exclusão em si (ela já está
        # rodando em segundo plano, não tem como interromper) -- só para
        # de ficar checando por aqui. Mensagem diferente pra não dar a
        # entender que a exclusão foi desfeita.
        if estado.fluxo_ativo == FLUXO_EXCLUIR_CENARIO and estado.etapa_atual == 'cen_excluir_aguardando':
            _encerrar_fluxo(estado)
            return (
                "Ok, parei de acompanhar por aqui -- mas a exclusão em si continua rodando em "
                "segundo plano normalmente (isso não cancela ela)."
            )
        # 🌟 NOVO: mesmo raciocínio da exclusão -- cancelar o acompanhamento
        # da exportação de dados de otimização não cancela a geração dos
        # relatórios em si (já está rodando em segundo plano via Celery).
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'exportar_otimizacao_aguardando':
            _encerrar_fluxo(estado)
            return (
                "Ok, parei de acompanhar por aqui -- mas a geração dos relatórios continua rodando em "
                "segundo plano normalmente (isso não cancela ela)."
            )
        # 🌟 NOVO: mesmo raciocínio -- cancelar o acompanhamento do envio
        # por e-mail não cancela o envio em si (já disparado em segundo
        # plano via Celery).
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'exportar_otimizacao_aguardando_email':
            _encerrar_fluxo(estado)
            return (
                "Ok, parei de acompanhar por aqui -- mas o envio do e-mail continua rodando em "
                "segundo plano normalmente (isso não cancela ele)."
            )
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
    elif estado.fluxo_ativo == FLUXO_EXCLUIR_CENARIO:
        return _processar_excluir_cenario(estado, texto)

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
        # 🌟 CORRIGIDO: usa o número sequencial guardado na criação (não dá
        # pra buscar fresco no banco -- o cenário já foi excluído nesse
        # ponto). Só cai pro id bruto se essa chave nunca tiver sido
        # gravada (não deveria acontecer, mas evita quebrar).
        numero_exibido = estado.dados_coletados.get('cenario_criado_numero_sequencial', cenario_id)
        _encerrar_fluxo(estado)
        return (
            f"O cenário {numero_exibido}/{cenario_nome} que eu estava acompanhando não existe "
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

    # 🌟 NOVO: mesma ideia do nome -- mostra as últimas 10 descrições já
    # usadas PELA EMPRESA do usuário, mais recente primeiro.
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    texto_descricoes = ""
    if empresa_id is not None:
        descricoes = list(
            TbCenarios.objects_real.filter(empresa_id=empresa_id).exclude(cen_descricao__isnull=True)
            .exclude(cen_descricao='').order_by('-id').values_list('cen_descricao', flat=True)[:5]
        )
        if descricoes:
            lista = "\n".join(f"- {d}" for d in descricoes)
            texto_descricoes = f"Últimas descrições cadastradas (mais recente primeiro):\n{lista}\n\n"

    return f"Nome definido: **{nome}**.\n\n{texto_descricoes}Agora, uma **descrição** pra esse cenário (pode ser breve):"


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

    # 🌟 CORRIGIDO: antes mostrava os últimos cenários de TODAS as
    # empresas do sistema (bug) -- agora escopa só à empresa efetiva do
    # usuário, mostra o numero_sequencial (o "número" que a empresa
    # reconhece, igual ao Admin) em vez do id real da tabela, e usa
    # exatamente 10 (não 8).
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    ultimos = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('-id')[:10] if empresa_id is not None else []
    lista = "\n".join(f"- {c.numero_sequencial if c.numero_sequencial is not None else c.id}: {c.cen_nome}" for c in ultimos)
    return (
        "Descrição registrada.\n\n"
        "De **qual cenário existente** você quer copiar (tipo, período e demais dados iniciais)? "
        "Pode me dizer o **número** ou o **nome**. Alguns cenários recentes:\n"
        f"{lista}"
    )


# ---------------------------------------------------------------------
# Etapa: copiar_de
# ---------------------------------------------------------------------
def _etapa_copiar_de(estado, texto):
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None

    cenario_origem = _resolver_cenario(texto, empresa_id)
    if cenario_origem is None:
        return (
            f"Não encontrei nenhum cenário correspondente a \"{texto}\". "
            "Tenta de novo com o número ou o nome exato, ou \"cancelar\"."
        )

    # 🌟 NOVO: só pode copiar de um cenário CONSOLIDADO -- copiar de um
    # cenário com dados parciais/inconsistentes (ainda otimizando,
    # limpo mas não otimizado, etc.) geraria uma cópia com dados que não
    # refletem um resultado fechado de verdade.
    numero_origem = cenario_origem.numero_sequencial if cenario_origem.numero_sequencial is not None else cenario_origem.id
    if cenario_origem.flag != 1:
        if cenario_origem.flag is None:
            status_origem = "ainda não processado (nunca foi limpo)"
        else:
            status_origem = MENSAGENS_FLAG.get(cenario_origem.flag, f"desconhecido (flag={cenario_origem.flag})")
        _encerrar_fluxo(estado)
        return (
            f"O cenário **{numero_origem}/{cenario_origem.cen_nome}** está: **{status_origem}** -- "
            "só posso copiar de um cenário **Consolidado**.\n\n"
            "Ativa esse cenário, e depois Limpa, Otimiza e Consolida ele antes de tentar copiar de novo. "
            "Cancelei a criação por aqui -- pode começar de novo depois."
        )

    dados = estado.dados_coletados
    dados['cen_copiar_de_id'] = cenario_origem.id
    dados['cen_copiar_de_nome'] = cenario_origem.cen_nome
    dados['cen_copiar_de_numero_sequencial'] = numero_origem
    estado.dados_coletados = dados
    estado.etapa_atual = 'grupo'
    estado.save()

    # 🌟 CORRIGIDO: escopado à empresa, últimos 10 por id decrescente (em
    # vez de todos os grupos do sistema em ordem arbitrária).
    grupos = TbGrupoCenarios.objects.filter(empresa_id=empresa_id).order_by('-id')[:10] if empresa_id is not None else []
    if grupos:
        lista = "\n".join(f"- {g.id}: {g}" for g in grupos)
        texto_grupos = f"Alguns grupos existentes:\n{lista}\n\n"
    else:
        texto_grupos = ""

    return (
        f"Vai copiar de **{numero_origem}/{cenario_origem.cen_nome}**.\n\n"
        f"{texto_grupos}"
        "Qual **grupo** esse cenário deve ficar? Pode me dizer o nome de um grupo "
        "existente, ou o nome de um **grupo novo** (eu crio pra você) -- todo cenário "
        "precisa estar em um grupo."
    )


def _resolver_cenario(texto, empresa_id):
    texto = texto.strip()
    if texto.isdigit():
        # 🌟 CORRIGIDO: resolve pelo numero_sequencial (o "número" que a
        # empresa reconhece, igual ao Admin) em vez do id real da tabela
        # -- e sempre escopado à empresa, já que numero_sequencial só é
        # único DENTRO de cada empresa, não no sistema inteiro.
        cenario = TbCenarios.objects_real.filter(empresa_id=empresa_id, numero_sequencial=int(texto)).first()
        if cenario is not None:
            return cenario
        # Fallback pro id real, pra não quebrar quem ainda digitar o id
        # antigo por hábito (ex: durante a transição pro numero_sequencial).
        return TbCenarios.objects_real.filter(empresa_id=empresa_id, id=int(texto)).first()
    return TbCenarios.objects_real.filter(empresa_id=empresa_id, cen_nome__iexact=texto).first() \
        or TbCenarios.objects_real.filter(empresa_id=empresa_id, cen_nome__icontains=texto).first()


# ---------------------------------------------------------------------
# Etapa: grupo
# ---------------------------------------------------------------------
def _etapa_grupo(estado, texto):
    dados = estado.dados_coletados
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None

    # 🌟 CORRIGIDO: removida a opção de deixar sem grupo -- todo cenário
    # precisa ficar em um grupo agora. Se o usuário tentar "nenhum" (ou
    # equivalente), pede um grupo de verdade em vez de aceitar.
    if texto.strip().lower() in ('nenhum', 'nao', 'não', 'sem grupo'):
        return (
            "Todo cenário precisa ficar em um grupo -- não dá mais pra deixar sem. "
            "Me diz o nome de um grupo existente, ou de um grupo novo (eu crio pra você)."
        )

    grupo = None
    if texto.strip().isdigit():
        grupo = TbGrupoCenarios.objects.filter(empresa_id=empresa_id, id=int(texto.strip())).first()
    if grupo is None:
        grupo = _buscar_grupo_por_nome(texto.strip(), empresa_id)
    if grupo is None:
        # Não encontrou -- cria um grupo novo com esse nome, já na empresa certa
        grupo = _criar_grupo(texto.strip(), empresa_id)

    dados['cen_grupo_id'] = grupo.id
    dados['cen_grupo_nome'] = str(grupo)

    estado.dados_coletados = dados
    estado.etapa_atual = 'confirmar'
    estado.save()

    resumo = _montar_resumo(dados)
    return (
        f"{resumo}\n\n"
        "Confirma a criação desse cenário? (**Sim** / **Não**)"
    )


CAMPO_NOME_GRUPO = 'gru_cen_codigo'  # confirmado com o usuário -- nome do campo em TbGrupoCenarios


def _buscar_grupo_por_nome(nome, empresa_id):
    return TbGrupoCenarios.objects.filter(empresa_id=empresa_id, **{f"{CAMPO_NOME_GRUPO}__iexact": nome}).first()


def _criar_grupo(nome, empresa_id):
    return TbGrupoCenarios.objects.create(empresa_id=empresa_id, **{CAMPO_NOME_GRUPO: nome})


# ---------------------------------------------------------------------
# Etapa: confirmar
# ---------------------------------------------------------------------
def _etapa_confirmar(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        if resposta in ('nao', 'não', 'n', 'no'):
            _encerrar_fluxo(estado)
            return "Ok, não criei o cenário. Se quiser começar de novo, é só pedir."
        return "Não entendi. Confirma a criação? (**Sim** / **Não**)"

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

    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    dados['cenario_criado_id'] = cenario.id
    dados['cenario_criado_nome'] = cenario.cen_nome
    dados['cenario_criado_numero_sequencial'] = numero_exibido
    dados['momento_criacao'] = momento_criacao
    estado.dados_coletados = dados
    # 🌟 CORRIGIDO: antes ia direto pra etapa "ativar" e já perguntava se
    # queria ativar o cenário -- só que a duplicação dos dados ainda nem
    # tinha começado de verdade, então não fazia sentido perguntar isso
    # antes dos dados do cenário existirem. Agora vai direto pro
    # acompanhamento da duplicação; a pergunta de ativar só aparece
    # DEPOIS que a duplicação terminar de verdade (dentro de
    # _checar_duplicacao).
    estado.etapa_atual = 'aguardando_duplicacao'
    estado.save()

    return (
        f"Cenário **{numero_exibido}/{cenario.cen_nome}** registrado! Agora vou duplicar os dados "
        "do cenário de origem em segundo plano -- pode levar **até alguns minutos**, dependendo da "
        "quantidade de fluxos cadastrados."
    )


# ---------------------------------------------------------------------
# Etapa: ativar -- só é perguntada DEPOIS que a duplicação já terminou
# (ver _checar_duplicacao).
# ---------------------------------------------------------------------
def _etapa_ativar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(dados.get('cenario_criado_id'))
    cenario_nome = dados.get('cenario_criado_nome')

    if resposta in ('sim', 's', 'yes', 'y'):
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=estado.usuario)
        perfil.cenario_ativo_id = dados.get('cenario_criado_id')
        perfil.save()
        mensagem_ativacao = f"Pronto, o cenário **{numero_exibido}/{cenario_nome}** agora é o seu cenário ativo.\n\n"
    else:
        mensagem_ativacao = f"Ok, o cenário **{numero_exibido}/{cenario_nome}** foi criado mas não ativado pra você.\n\n"

    estado.etapa_atual = 'confirmar_processar'
    estado.save()

    return (
        mensagem_ativacao +
        "Quer que eu já rode o ciclo completo -- **Limpar → Otimizar → Consolidar** -- "
        "e te mostre uma comparação dos resultados com o cenário de origem? (**Sim** / **Não**)"
    )


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
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(dados.get('cenario_criado_id'))
    cenario_nome = dados.get('cenario_criado_nome')
    momento_criacao = dados.get('momento_criacao')

    if not _celery_esta_ativo():
        return (
            "⚠️ Não consegui detectar nenhum worker do Celery ativo agora -- a "
            "duplicação das tabelas do cenário não vai acontecer sozinha até "
            "alguém ligar o Celery. Assim que estiver rodando,"
        )

    task = _buscar_task_duplicacao(momento_criacao)

    # 🌟 CORRIGIDO: antes, "ainda não encontrei o registro da task" tinha
    # uma mensagem própria e ambígua ("pode ser que tenha começado") --
    # agora trata igual a "ainda rodando", já que na prática o efeito pro
    # usuário é o mesmo (clicar "Verificar" de novo em instantes) e essa
    # checagem de existência já é feita aqui dentro mesmo. Também não
    # mostra mais o status técnico do Celery entre parênteses (ex:
    # "aguardando início", "PENDING") -- não ajuda o usuário e, no caso
    # de task=None, nem fazia sentido ("aguardando início" enquanto a
    # frase já diz "ainda duplicando").
    if task is None or task.status not in ('SUCCESS', 'FAILURE'):
        return (
            f"Ainda duplicando as tabelas do cenário **{numero_exibido}/{cenario_nome}**. "
        )

    if task.status == 'FAILURE':
        _encerrar_fluxo(estado)
        return (
            f"❌ A duplicação das tabelas do cenário **{numero_exibido}/{cenario_nome}** falhou "
            f"(status: {task.status}). O cenário foi criado, mas os dados não foram "
            "duplicados -- vale olhar o log do Celery pra entender o motivo. Cancelei o fluxo aqui."
        )

    # SUCCESS -- 🌟 CORRIGIDO: a mensagem de "criado! 🎉" e a pergunta de
    # ativar só aparecem AQUI agora, depois que a duplicação já terminou
    # de verdade (antes apareciam logo na confirmação, antes dos dados
    # existirem).
    estado.etapa_atual = 'ativar'
    estado.save()
    return (
        f"✅ Cenário **{numero_exibido}/{cenario_nome}** criado! 🎉 A duplicação dos dados terminou.\n\n"
        "Quer que eu já **ative esse cenário pra você**? (**Sim** / **Não**)"
    )


def _etapa_aguardando_duplicacao(estado, texto):
    return _checar_duplicacao(estado)


def _etapa_confirmar_processar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, não vou processar o cenário **{numero_exibido}/{cenario_nome}** agora. Ele já está criado, pode processar manualmente quando quiser."

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
        f"Beleza, disparei a **limpeza** do cenário **{numero_exibido}/{cenario_nome}** em segundo plano. "
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
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.get(id=cenario_id)

    if cenario.flag == 2:  # LIMPO
        return _disparar_otimizacao(estado, cenario)

    if cenario.flag == 5:  # ainda limpando
        return f"Ainda limpando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin. Cancelei o acompanhamento automático aqui."


def _disparar_otimizacao(estado, cenario):
    from django.db import connection
    from .tasks import otimizar_cenario_celery

    cenario_id = cenario.id
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
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
        f"Limpeza concluída! Disparei a **otimização** do cenário **{numero_exibido}/{cenario.cen_nome}** "
        f"({total_periodos} período(s)) em segundo plano."
    )


def _etapa_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(cenario_id)
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
            f"Ainda otimizando o cenário **{numero_exibido}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} períodos concluídos). "
        )

    cenario = TbCenarios.objects_real.get(id=cenario_id)
    if cenario.flag != 3:
        # Todos os períodos já processaram, mas o status geral do cenário
        # ainda não virou "OTIMIZADO" -- dá uma folga e confere de novo.
        return f"Períodos todos processados, aguardando o cenário fechar como OTIMIZADO."

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
        f"Otimização concluída! Disparei a **consolidação** do cenário **{numero_exibido}/{cenario_nome}** "
        "em segundo plano."
    )


def _etapa_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados.get('cenario_criado_id')
    cenario_nome = dados.get('cenario_criado_nome')
    _numero_guardado = dados.get('cenario_criado_numero_sequencial')
    numero_exibido = _numero_guardado if _numero_guardado is not None else _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.get(id=cenario_id)

    if cenario.flag == 1:  # CONSOLIDADO
        resposta = _montar_tabela_comparacao(cenario_id, dados.get('cen_copiar_de_id'))
        _encerrar_fluxo(estado)
        return (
            f"✅ Cenário **{numero_exibido}/{cenario_nome}** consolidado! Ciclo completo.\n\n{resposta}"
        )

    if cenario.flag == 6:  # ainda consolidando
        return f"Ainda consolidando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin."


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
    numero_origem = dados.get('cen_copiar_de_numero_sequencial', dados.get('cen_copiar_de_id'))
    linhas = [
        "Resumo do cenário a criar:",
        f"- Nome: {dados.get('cen_nome')}",
        f"- Descrição: {dados.get('cen_descricao')}",
        f"- Copiar de: {numero_origem}/{dados.get('cen_copiar_de_nome')}",
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
            f"O cenário {cenario.numero_sequencial}/{cenario.cen_nome} entrou em operação "
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
            f"✅ Cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** atualizado: "
            f"tipo **{dados['tipo_novo']}**, período **{dados['inicio_novo']}** a **{dados['fim_novo']}**."
        )

    dados['momento_mudanca'] = momento_mudanca
    estado.dados_coletados = dados
    estado.etapa_atual = 'aguardando_verificacao_filhas'
    estado.save()
    return (
        f"Cenário atualizado! Como o período mudou, disparei em segundo plano o ajuste "
        f"das tabelas filhas do cenário **{cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id}/{cenario.cen_nome}** (criar os "
        "períodos que faltam ou remover os que sobraram)."
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
    numero_exibido_mudar = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id

    task = _buscar_task_verifica_filhas(momento_mudanca)
    if task is None:
        return (
            "Ainda não encontrei o registro do ajuste das tabelas filhas -- pode ser que "
            "ainda não tenha começado."
        )

    if task.status == 'SUCCESS':
        _encerrar_fluxo(estado)
        return (
            f"✅ Ajuste das tabelas filhas do cenário **{numero_exibido_mudar}/{cenario.cen_nome}** concluído! "
            f"Tipo **{dados['tipo_novo']}**, período **{dados['inicio_novo']}** a **{dados['fim_novo']}**."
        )

    if task.status == 'FAILURE':
        _encerrar_fluxo(estado)
        return (
            f"❌ O ajuste das tabelas filhas do cenário {numero_exibido_mudar}/{cenario.cen_nome} falhou "
            "-- o tipo/período já foram salvos, mas vale conferir manualmente no Admin se as "
            "filhas ficaram consistentes."
        )

    # PENDING, STARTED, RETRY, etc. -- ainda rodando
    return (
        f"Ainda ajustando as tabelas filhas do cenário {numero_exibido_mudar}/{cenario.cen_nome}. "
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

def determinar_acao_indicadores(mensagem):
    """
    🌟 NOVO (Ações Comuns por empresa): extraído de dentro de
    iniciar_fluxo_indicadores pra poder ser chamado TAMBÉM pelo
    detector em agents.py, antes de decidir se dispara o fluxo -- assim
    dá pra checar "essa sub-ação específica está habilitada pra essa
    empresa?" sem duplicar a lógica de regex em dois lugares.
    """
    texto = (mensagem or '').lower()
    if re.search(r'gr[áa]fico|plot[ae]r?', texto):
        return 'grafico'
    elif re.search(r'cri[ae]r?|cadastr[ae]r?', texto):
        return 'criar'
    elif re.search(r'reajust|em massa|todos os per[ií]odos', texto):
        return 'massa'
    elif re.search(r'elimin|apag|exclu[ií]|delet|remov', texto):
        return 'eliminar'
    else:
        return 'editar'


def iniciar_fluxo_indicadores(usuario, mensagem=""):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes de mexer nos indicadores."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais. Acesse a tela de Cenários e ative outro antes de continuar."

    acao = determinar_acao_indicadores(mensagem)

    estado = _get_estado(usuario)

    # 🌟 CORRIGIDO: em vez de só avisar e encerrar, agora pergunta se o
    # usuário quer criar um indicador novo (sim/não) -- se sim,
    # reaproveita o mesmo fluxo de criação já existente (etapa
    # 'ind_criar_nome', igual à ação "criar" normal).
    if acao != 'criar':
        from tabelas.models import TbIndicadores
        if not TbIndicadores.objects.filter(tbcenarios_id=cenario.id).exists():
            estado.fluxo_ativo = FLUXO_INDICADORES
            estado.etapa_atual = 'ind_confirmar_criar_sem_dados'
            estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
            estado.save()
            return (
                f"Não tem nenhum indicador cadastrado no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** ainda -- "
                "não tem o que mudar, reajustar, eliminar ou plotar.\n\n"
                "Quer que eu **crie um indicador novo**? (**Sim** / **Não**)"
            )

    estado.fluxo_ativo = FLUXO_INDICADORES
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'acao': acao}

    if acao == 'grafico':
        estado.etapa_atual = 'ind_grafico_escolher'
        estado.save()
        return (
            f"Vamos plotar um gráfico de indicador no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer plotar? (ou \"cancelar\")"
        )
    elif acao == 'criar':
        estado.etapa_atual = 'ind_criar_nome'
        estado.save()
        return (
            f"Vamos criar um indicador novo no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            "Qual vai ser o **nome** do indicador? (ou \"cancelar\" para desistir)"
        )
    elif acao == 'massa':
        estado.etapa_atual = 'ind_massa_nome'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer reajustar? (ou \"cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'ind_eliminar_nome'
        estado.save()
        return (
            f"Vamos eliminar um indicador do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer eliminar? (ou \"cancelar\")"
        )
    else:
        estado.etapa_atual = 'ind_editar_nome'
        estado.save()
        return (
            f"Vamos mudar um valor de indicador no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
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


def _salvar_planilha_otimizacao(wb, sufixo, cenario_id):
    """
    🌟 NOVO: salva uma planilha de resultados de otimização em
    MEDIA_ROOT/relatorios_temp, devolvendo o caminho completo (pra
    anexar num e-mail, se pedido) e a URL pra baixar pelo chat -- mesmo
    padrão de pasta usado em iniciar_exportar_excel_cenario_ativo.
    """
    import os
    from django.conf import settings

    pasta = os.path.join(settings.MEDIA_ROOT, 'relatorios_temp')
    os.makedirs(pasta, exist_ok=True)
    nome_arquivo = f"otimizacao_{sufixo}_{cenario_id}.xlsx"
    caminho_completo = os.path.join(pasta, nome_arquivo)
    wb.save(caminho_completo)
    wb.close()
    return caminho_completo, f"{settings.MEDIA_URL}relatorios_temp/{nome_arquivo}", nome_arquivo


def _tipo_periodo_exibicao(cenario):
    if cenario.cen_tipo == 'Anual':
        return str(_('Ano'))
    elif cenario.cen_tipo == 'Trimestral':
        return str(_('Ano/Trimestre'))
    return str(_('Ano/Mês'))


def _exportar_dados_otimizacao_produto(cenario):
    """
    🌟 NOVO: replica TbOtimizacaoProdutoAdmin.exportar_excel
    (otimizacao/admin.py), escopado ao cenário ativo do usuário --
    TODOS os registros da tabela mãe desse cenário, sem precisar
    selecionar na tela do Admin -- com cabeçalhos traduzidos.
    """
    from otimizacao.models import TbOtimizacaoProduto, TbOtimizacaoProdutoDaugther
    from produtos.models import TbProdutos
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbOtimizacaoProduto.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbOtimizacaoProdutoDaugther.objects.filter(mae_id__in=lista_id_maes).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: PRODUTO')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Produto')), str(_('Vol. Mínimo')), str(_('Vol. Máximo')), str(_('Vendas')),
               str(_('Preço')), str(_('Custo Variável')), str(_('Margem Contribuição')), str(_('Margem (%)')),
               str(_('Margem Horária'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_nome_produto = {}
    for f in filhas:
        if f.mae_id not in cache_nome_produto:
            id_produto = TbOtimizacaoProduto.objects.get(id=f.mae_id).oti_pro_produto_id
            cache_nome_produto[f.mae_id] = TbProdutos.objects.get(id=id_produto).pro_codigo
        nome_produto = cache_nome_produto[f.mae_id]

        preco = f.dau_valor_5 or 0
        custo_variavel = f.dau_valor_6 or 0
        margem_contrib = preco - custo_variavel
        margem_pct = round(margem_contrib * 100 / preco, 2) if preco else 0

        ws.append([_dau_order_para_periodo(cenario, f.dau_order), nome_produto, f.dau_valor_1, f.dau_valor_2,
                   f.dau_valor_3, preco, custo_variavel, margem_contrib, margem_pct, f.dau_valor_7])

    return _salvar_planilha_otimizacao(wb, 'produto', cenario.id)


def _exportar_dados_otimizacao_produto_mercado(cenario):
    """🌟 NOVO: replica TbProdutoMercadoAdmin.exportar_excel."""
    from otimizacao.models import TbProdutoMercado, TbProdutoMercadoDaugther
    from produtos.models import TbProdutos
    from tabelas.models import TbMercado
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbProdutoMercado.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbProdutoMercadoDaugther.objects.filter(mae_id__in=lista_id_maes).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: PRODUTO-MERCADO')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Produto')), str(_('Mercado')), str(_('Vol. Mínimo')), str(_('Vol. Máximo')),
               str(_('Vendas')), str(_('Preço')), str(_('Custo Variável')), str(_('Margem Contribuição')),
               str(_('Margem (%)')), str(_('Margem Horária'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_nomes = {}
    for f in filhas:
        if f.mae_id not in cache_nomes:
            registro_mae = TbProdutoMercado.objects.get(id=f.mae_id)
            nome_produto = TbProdutos.objects.get(id=registro_mae.pro_mer_produto_id).pro_codigo
            nome_mercado = TbMercado.objects.get(id=registro_mae.pro_mer_mercado_id).mer_nome
            cache_nomes[f.mae_id] = (nome_produto, nome_mercado)
        nome_produto, nome_mercado = cache_nomes[f.mae_id]

        preco = f.dau_valor_5 or 0
        custo_variavel = f.dau_valor_6 or 0
        margem_contrib = preco - custo_variavel
        margem_pct = round(margem_contrib * 100 / preco, 2) if preco else 0

        ws.append([_dau_order_para_periodo(cenario, f.dau_order), nome_produto, nome_mercado, f.dau_valor_1,
                   f.dau_valor_2, f.dau_valor_3, preco, custo_variavel, margem_contrib, margem_pct, f.dau_valor_7])

    return _salvar_planilha_otimizacao(wb, 'produto_mercado', cenario.id)


def _exportar_dados_otimizacao_produto_mercado_fluxo(cenario):
    """
    🌟 NOVO: replica TbProdutoMercadoFluxoAdmin.exportar_excel -- a mais
    pesada das 6, já que a margem horária e o equipamento responsável
    por ela vêm de 2 chamadas de procedure por linha
    (gargalo_produtividade_fluxo_producao_order_valor/_equipamento).
    Guarda um cache por (id_fluxo, dau_order) pra não repetir a mesma
    chamada de procedure quando mais de uma combinação produto/mercado
    usa o mesmo fluxo no mesmo período.
    """
    from otimizacao.models import TbProdutoMercadoFluxo, TbProdutoMercadoFluxoDaugther
    from produtos.models import TbProdutos, TbProdutoMercadoPreco, TbProdutoMercadoPrecoDaugther
    from tabelas.models import TbMercado, TbEquacaoAjustePreco
    from fluxos.models import TbFluxoProducao
    from django.db import connection
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbProdutoMercadoFluxoDaugther.objects.filter(mae_id__in=lista_id_maes).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: PRODUTO-MERCADO-FLUXO DE PRODUÇÃO')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Produto')), str(_('Mercado')), str(_('Preço Comercial')), str(_('Unidade')),
               str(_('Fluxo de Produção')), str(_('Fluxo Running')), str(_('Equip. Running')), str(_('Vendas')),
               str(_('Margen Contrib.')), str(_('Margen Horária')), str(_('Equip. Margem Horária')), str(_('Preço')),
               str(_('Custo Variável')), str(_('Parcela Itens')), str(_('Parcela Inbound')),
               str(_('Parcela Outbound')), str(_('Parcela Manutenção')), str(_('Total Margem'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_mae = {}
    cache_margem_horaria = {}
    cursor = connection.cursor()
    try:
        for f in filhas:
            if f.mae_id not in cache_mae:
                registro_mae = TbProdutoMercadoFluxo.objects.get(id=f.mae_id)
                id_produto = registro_mae.pro_mer_flu_produto_id
                id_mercado = registro_mae.pro_mer_flu_mercado_id
                id_fluxo = registro_mae.pro_mer_flu_fluxo_producao_id
                nome_produto = TbProdutos.objects.get(id=id_produto).pro_codigo
                nome_mercado = TbMercado.objects.get(id=id_mercado).mer_nome
                nome_fluxo = TbFluxoProducao.objects.get(id=id_fluxo).flu_pro_descricao

                preco_cadastro = TbProdutoMercadoPreco.objects.get(pro_mer_pre_produto_id=id_produto, pro_mer_pre_mercado_id=id_mercado)
                if preco_cadastro.pro_mer_pre_equacao_id:
                    unidade_preco_comercial = TbEquacaoAjustePreco.objects.get(id=preco_cadastro.pro_mer_pre_equacao_id).equ_aju_pre_descricao
                else:
                    unidade_preco_comercial = preco_cadastro.pro_mer_pre_moeda

                cache_mae[f.mae_id] = (nome_produto, nome_mercado, nome_fluxo, id_fluxo, preco_cadastro.id, unidade_preco_comercial)
            nome_produto, nome_mercado, nome_fluxo, id_fluxo, id_preco_cadastro, unidade_preco_comercial = cache_mae[f.mae_id]

            preco_comercial = TbProdutoMercadoPrecoDaugther.objects.filter(mae_id=id_preco_cadastro, dau_order=f.dau_order).values_list('dau_valor_3', flat=True).first()

            chave_margem = (id_fluxo, f.dau_order)
            if chave_margem not in cache_margem_horaria:
                cursor.execute(f"call public.gargalo_produtividade_fluxo_producao_order_valor({id_fluxo}, {f.dau_order}, 0)")
                retorno_valor = cursor.fetchone()[0]
                cursor.execute(f"call public.gargalo_produtividade_fluxo_producao_order_equipamento({id_fluxo}, {f.dau_order}, '')")
                retorno_equipamento = cursor.fetchone()[0]
                cache_margem_horaria[chave_margem] = (retorno_valor, retorno_equipamento)
            retorno_valor, equipamento_margem_horaria = cache_margem_horaria[chave_margem]

            vendas = f.dau_valor_2 or 0
            margem_contrib = f.dau_valor_3 or 0
            margem_horaria = round(retorno_valor * margem_contrib, 2) if retorno_valor and retorno_valor > 0 else 'ND'
            total_margem = vendas * margem_contrib

            ws.append([_dau_order_para_periodo(cenario, f.dau_order), nome_produto, nome_mercado, preco_comercial,
                       unidade_preco_comercial, nome_fluxo, f.dau_valor_1, f.dau_valor_7, vendas, margem_contrib,
                       margem_horaria, equipamento_margem_horaria, f.dau_valor_4, f.dau_valor_5, f.dau_valor_8,
                       f.dau_valor_9, f.dau_valor_6, f.dau_valor_10, total_margem])
    finally:
        cursor.close()

    return _salvar_planilha_otimizacao(wb, 'produto_mercado_fluxo', cenario.id)


def _exportar_dados_otimizacao_equipamentos(cenario):
    """🌟 NOVO: replica TbOtimizacaoEquipamentosAdmin.exportar_excel."""
    from otimizacao.models import TbOtimizacaoEquipamentos, TbOtimizacaoEquipamentosDaugther
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbOtimizacaoEquipamentos.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbOtimizacaoEquipamentosDaugther.objects.filter(mae_id__in=lista_id_maes).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: EQUIPAMENTOS')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Equipamento')), str(_('Running')), str(_('Produção(volume)')),
               str(_('Produção(h)')), str(_('Loading Time(h)')), str(_('Ocupação Mínima (%)')),
               str(_('Ocupação Máxima (%)')), str(_('Ocupação(%)'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_nome_equipamento = {}
    linhas = []
    for f in filhas:
        if f.mae_id not in cache_nome_equipamento:
            cache_nome_equipamento[f.mae_id] = str(TbOtimizacaoEquipamentos.objects.get(id=f.mae_id).oti_equ_equipamento)
        nome_equipamento = cache_nome_equipamento[f.mae_id]
        linhas.append([_dau_order_para_periodo(cenario, f.dau_order), nome_equipamento, f.dau_valor_1, f.dau_valor_2,
                        f.dau_valor_6, f.dau_valor_3, f.dau_valor_5, f.dau_valor_7, f.dau_valor_4])

    # Mesma ordenação da versão original: por nome do equipamento
    for linha in sorted(linhas, key=lambda l: l[1]):
        ws.append(linha)

    return _salvar_planilha_otimizacao(wb, 'equipamentos', cenario.id)


def _exportar_dados_otimizacao_equipamentos_ordem(cenario):
    """🌟 NOVO: replica TbOtimizacaoEquipamentosOrdemAdmin.exportar_excel."""
    from otimizacao.models import TbOtimizacaoEquipamentosOrdem, TbOtimizacaoEquipamentosOrdemDaugther
    from equipamentos.models import TbEquipamentos, TbEquipamentosCadastro
    from tabelas.models import TbTipoProducao
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbOtimizacaoEquipamentosOrdem.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbOtimizacaoEquipamentosOrdemDaugther.objects.filter(mae_id__in=lista_id_maes).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: EQUIPAMENTOS - ORDEM DE PRODUÇÃO')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Equipamento Código')), str(_('Equipamento Descrição')), str(_('Tipo Produção')),
               str(_('Equipamento/Planta/Ordem de Produção')), str(_('Produção(volume)')), str(_('Produção(h)')),
               str(_('WIP(volume)')), str(_('WIP(valor)'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_mae = {}
    for f in filhas:
        if f.mae_id not in cache_mae:
            registro_mae = TbOtimizacaoEquipamentosOrdem.objects.get(id=f.mae_id)
            equipamento_id = registro_mae.oti_equ_ord_equipamento_id
            equipamento_cadastro_id = TbEquipamentos.objects.get(id=equipamento_id).equ_codigo_id
            cadastro = TbEquipamentosCadastro.objects.get(id=equipamento_cadastro_id)
            tipo_producao_id = TbEquipamentos.objects.get(id=equipamento_id).equ_tipo_producao_id
            tipo_producao = TbTipoProducao.objects.get(id=tipo_producao_id).tip_nome
            nome_equip_planta_ordem = str(registro_mae.oti_equ_ord_equipamento)
            cache_mae[f.mae_id] = (cadastro.equ_cad_codigo, cadastro.equ_cad_descricao, tipo_producao, nome_equip_planta_ordem)
        codigo_equipamento, descricao_equipamento, tipo_producao, nome_equip_planta_ordem = cache_mae[f.mae_id]

        ws.append([_dau_order_para_periodo(cenario, f.dau_order), codigo_equipamento, descricao_equipamento,
                   tipo_producao, nome_equip_planta_ordem, f.dau_valor_1, f.dau_valor_2, f.dau_valor_3, f.dau_valor_4])

    return _salvar_planilha_otimizacao(wb, 'equipamentos_ordem', cenario.id)


def _exportar_dados_otimizacao_custo_item(cenario):
    """
    🌟 NOVO: replica TbOtimizacaoCustoItemAdmin.exportar_excel -- ÚNICA
    das 6 com filtro adicional: só inclui linhas (períodos) onde
    dau_valor_4 ("Ativo") é True, por pedido explícito -- as demais 5
    trazem todos os períodos, sem esse filtro.
    """
    from otimizacao.models import TbOtimizacaoCustoItem, TbOtimizacaoCustoItemDaugther
    from tabelas.models import TbCustoItemPreco, TbCustoItem, TbUnidadeProducao
    from openpyxl import Workbook
    from openpyxl.styles import Font

    lista_id_maes = list(TbOtimizacaoCustoItem.objects.filter(tbcenarios_id=cenario.id, flag=True).values_list('id', flat=True))
    filhas = TbOtimizacaoCustoItemDaugther.objects.filter(mae_id__in=lista_id_maes, dau_valor_4=True).order_by('mae_id', 'dau_order')

    wb = Workbook()
    ws = wb.active
    ws.title = str(_('Resultados'))[:31]
    ws.append([f"{_('RESULTADOS OTIMIZAÇÃO: ITENS DE CUSTO - PLANTA DE PRODUÇÃO')} / {_('CENÁRIO')} {cenario.numero_sequencial} / {cenario.cen_nome}"])
    ws['A1'].font = Font(bold=True)

    tipo_periodo = _tipo_periodo_exibicao(cenario)
    ws.append([tipo_periodo, str(_('Item de Custo')), str(_('Unidade')), str(_('Planta de Produção')),
               str(_('Consumo Mínimo')), str(_('Consumo Máximo')), str(_('Consumo Calculado')),
               str(_('Estoque (Qtde)')), str(_('Estoque (Valor)')), str(_('Ativo'))])
    for cel in ws[2]:
        cel.font = Font(bold=True)

    cache_mae = {}
    for f in filhas:
        if f.mae_id not in cache_mae:
            id_custo_item_planta = TbOtimizacaoCustoItem.objects.get(id=f.mae_id).oti_cus_ite_item_id
            preco_planta = TbCustoItemPreco.objects.get(id=id_custo_item_planta)
            item = TbCustoItem.objects.get(id=preco_planta.cus_ite_pre_item_id)
            nome_planta = TbUnidadeProducao.objects.get(id=preco_planta.cus_ite_pre_unidade_producao_id).uni_nome
            cache_mae[f.mae_id] = (item.cus_ite_nome, item.cus_ite_unidade, nome_planta)
        nome_custo_item, unidade_custo_item, nome_planta = cache_mae[f.mae_id]

        ws.append([_dau_order_para_periodo(cenario, f.dau_order), nome_custo_item, unidade_custo_item, nome_planta,
                   f.dau_valor_1, f.dau_valor_2, f.dau_valor_3, f.dau_valor_5, f.dau_valor_6, f.dau_valor_4])

    return _salvar_planilha_otimizacao(wb, 'custo_item', cenario.id)


def iniciar_exportar_dados_otimizacao_cenario_ativo(usuario):
    """
    🌟 NOVO: dispara, em segundo plano (Celery), a geração das 6
    planilhas de resultados de otimização do cenário ativo (Produto,
    Produto-Mercado, Produto-Mercado-Fluxo, Equipamentos, Equipamentos-
    Ordem, Custo Item) -- mesma estrutura das 6 ações "Exportar Excel"
    já existentes no Admin do app otimizacao, mas escopadas ao cenário
    ativo do usuário (todos os registros da tabela mãe, sem precisar
    selecionar linha por linha) e com cabeçalhos traduzidos.

    🌟 CORRIGIDO: antes gerava tudo de forma síncrona, dentro da própria
    requisição do chat -- um dos 6 relatórios (Produto-Mercado-Fluxo)
    chama 2 procedures do banco por linha e pode demorar bastante,
    arriscando estourar o limite de 30s de processamento por requisição
    do Heroku. Agora só dispara a tarefa e devolve a resposta na hora;
    o usuário acompanha com "Verificar", igual ao limpar/otimizar/
    consolidar.
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    from .tasks import exportar_dados_otimizacao_celery

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR  # reaproveita o mesmo "namespace" de fluxo simples
    estado.etapa_atual = 'exportar_otimizacao_aguardando'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'cenario_nome': cenario.cen_nome,
        'status': 'processando',
    }
    estado.save()

    exportar_dados_otimizacao_celery.delay(cenario.id, usuario.id)

    return (
        f"Comecei a gerar os 6 relatórios de otimização do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊 "
        "em segundo plano (alguns podem demorar um pouco)."
    )


def _etapa_exportar_otimizacao_aguardando(estado, texto):
    """
    🌟 NOVO: acompanha (via "Verificar") a task exportar_dados_otimizacao_celery
    disparada por iniciar_exportar_dados_otimizacao_cenario_ativo.
    """
    dados = estado.dados_coletados or {}
    cenario_id = dados.get('cenario_id')
    cenario_nome = dados.get('cenario_nome', '')
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    status = dados.get('status')

    if status == 'processando':
        return f"Ainda gerando os relatórios do cenário **{numero_exibido}/{cenario_nome}**."

    if status == 'erro':
        _encerrar_fluxo(estado)
        return f"Não consegui gerar os relatórios: {dados.get('mensagem', 'erro desconhecido')}."

    if status != 'concluido':
        _encerrar_fluxo(estado)
        return "Não consegui identificar o status da geração dos relatórios. Cancelei o acompanhamento -- pode pedir de novo se quiser."

    arquivos = dados.get('arquivos', [])
    erros = dados.get('erros', [])

    if not arquivos:
        _encerrar_fluxo(estado)
        return "Não consegui gerar nenhum dos 6 relatórios de otimização. Detalhes: " + "; ".join(erros)

    estado.etapa_atual = 'exportar_otimizacao_confirmar_email'
    estado.dados_coletados = {'cenario_id': cenario_id, 'arquivos': arquivos}
    estado.save()

    linhas_download = "\n".join(f"- **{a['rotulo']}**: [{a['nome_arquivo']}]({a['url']})" for a in arquivos)
    resposta = f"Prontos os relatórios de otimização do cenário **{numero_exibido}/{cenario_nome}** 📊\n\n{linhas_download}\n\n"
    if erros:
        resposta += "⚠️ Não consegui gerar: " + "; ".join(erros) + "\n\n"
    resposta += "Quer que eu envie esses arquivos também por e-mail pro seu e-mail cadastrado? (sim/não)"
    return resposta


def _etapa_exportar_otimizacao_confirmar_email(estado, texto):
    """
    🌟 NOVO: trata a resposta sim/não do usuário sobre receber os 6
    relatórios de otimização por e-mail (etapa
    'exportar_otimizacao_confirmar_email' dentro de FLUXO_PROCESSAR).

    🌟 CORRIGIDO: o envio em si (6 anexos por SMTP) estava passando de
    30s, arriscando estourar o limite de processamento por requisição
    do Heroku -- agora só DISPARA o envio em segundo plano (Celery) e
    devolve a resposta na hora; o usuário acompanha com "Verificar",
    igual à geração dos relatórios.
    """
    usuario = estado.usuario
    texto = (texto or '').strip().lower()
    dados = estado.dados_coletados or {}
    arquivos = dados.get('arquivos', [])

    if re.search(r'\bsim\b|\bpode\b|\bmanda\b|\benvia\b', texto):
        email_usuario = (usuario.email or '').strip()
        if not email_usuario:
            _encerrar_fluxo(estado)
            return "Você não tem um e-mail cadastrado no seu usuário do sistema, então não consigo enviar. Os arquivos continuam disponíveis pra download nas mensagens acima."

        from .tasks import enviar_email_relatorios_otimizacao_celery

        estado.etapa_atual = 'exportar_otimizacao_aguardando_email'
        estado.dados_coletados = {**dados, 'status_email': 'enviando'}
        estado.save()

        enviar_email_relatorios_otimizacao_celery.delay(usuario.id, arquivos)

        return f"Começando a enviar os {len(arquivos)} arquivo(s) pro seu e-mail ({email_usuario}) em segundo plano."

    if re.search(r'\bnão\b|\bnao\b|\bcancel', texto):
        _encerrar_fluxo(estado)
        return "Combinado, não vou enviar por e-mail. Os arquivos continuam disponíveis pra download nas mensagens acima."

    return "Não entendi -- quer que eu envie os relatórios por e-mail? Responde **Sim** ou **Não**."


def _etapa_exportar_otimizacao_aguardando_email(estado, texto):
    """
    🌟 NOVO: acompanha (via "Verificar") a task
    enviar_email_relatorios_otimizacao_celery disparada por
    _etapa_exportar_otimizacao_confirmar_email.
    """
    dados = estado.dados_coletados or {}
    status_email = dados.get('status_email')

    if status_email == 'enviando':
        return "Ainda enviando o e-mail com os relatórios."

    _encerrar_fluxo(estado)

    if status_email == 'enviado':
        return f"Prontinho! Mandei os relatórios pro seu e-mail ({dados.get('email_usuario', '')})."
    if status_email == 'erro':
        return f"Não consegui enviar o e-mail: {dados.get('mensagem', 'erro desconhecido')}. Os arquivos continuam disponíveis pra download nas mensagens acima."
    return "Não consegui identificar o status do envio. Cancelei o acompanhamento -- os arquivos continuam disponíveis pra download nas mensagens acima."


def iniciar_exportar_excel_cenario_ativo(usuario):
    """
    🌟 NOVO: exporta o relatório de resultados do cenário ativo em Excel,
    pelo chat -- mesma estrutura de colunas e conteúdo do botão
    "Exportar Excel" já existente na tela de Cenários do Admin
    (TbCenariosAdmin.exportar_excel_cenario), só que gerando um arquivo
    salvo (com link de download) em vez de resposta HTTP direta, pra
    caber no formato de mensagem do chat -- e com os cabeçalhos
    traduzidos pro idioma ativo do usuário (o do Admin usa texto fixo em
    português).
    """
    import os
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from django.conf import settings

    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    filhas = TbCenariosDaugther.objects.filter(mae_id=cenario.id).order_by('dau_order')

    nome_empresa = cenario.empresa.emp_nome if cenario.empresa else ''

    wb = Workbook()
    ws = wb.active
    ws.title = _('Resultados')

    titulo = f"{_('RESULTADOS CENÁRIO ')}{cenario.numero_sequencial}/{cenario.cen_nome}"
    if nome_empresa:
        titulo += f" ({nome_empresa})"
    ws.append([titulo])
    ws['A1'].font = Font(bold=True)

    colunas = [
        _('Período'), _('Solução Ótima'), _('Vendas'), _('Variável'), _('Inbound'), _('Outbound'),
        _('Manut.'), _('Margem'), _('Fixo'), _('EBTIDA'), _('EBTIDA (%)'), _('D&A'), _('IR (%)'),
        _('MP'), _('WIP'), _('PF'), _('Total Est.'), _('Receber'), _('Pagar'), _('OWCR'),
        _('CAPEX'), _('OFCF'),
    ]
    ws.append(colunas)
    for cel in ws[2]:
        cel.font = Font(bold=True)

    mapa_solucao = {
        0: str(_('Não')), 1: str(_('Sim')), 2: str(_('Limpo')),
        3: str(_('Limpando')), None: str(_('Otimizando')),
    }

    for f in filhas:
        periodo = _dau_order_para_periodo(cenario, f.dau_order)
        solucao = mapa_solucao.get(f.flag, str(_('Otimizando')))
        valores = [getattr(f, f'dau_valor_{i}') for i in range(1, 21)]
        ws.append([periodo, solucao] + valores)

    pasta = os.path.join(settings.MEDIA_ROOT, 'relatorios_temp')
    os.makedirs(pasta, exist_ok=True)
    prefixo_traduzido = re.sub(r'[\\/:*?"<>|\s]+', '_', str(_('Resultados Cenário'))).strip('_')
    empresa_segura = re.sub(r'[\\/:*?"<>|\s]+', '_', nome_empresa).strip('_')
    sufixo_empresa = f"_{empresa_segura}" if empresa_segura else ""
    nome_arquivo = f"{prefixo_traduzido}{sufixo_empresa}_{cenario.id}.xlsx"
    caminho_completo = os.path.join(pasta, nome_arquivo)
    wb.save(caminho_completo)
    wb.close()
    url = f"{settings.MEDIA_URL}relatorios_temp/{nome_arquivo}"

    return (
        f"Aqui está o relatório de resultados do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}**:\n\n"
        f"[📊 Baixar relatório Excel]({url})"
    )


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

    # 🌟 CORRIGIDO (multi-empresa): exige que o relatório pertença à
    # empresa efetiva do usuário -- mesma correção de segurança feita em
    # agents.py, evitando reconhecer um arquivo de OUTRA empresa.
    empresa_id_usuario = perfil.empresa_efetiva_id()
    if empresa_id_usuario is None:
        return None

    import os
    from .models import RelatorioPDF
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True, empresa_id=empresa_id_usuario).first()
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
        "- **Manual** -- pra mudar um período de cada vez, digitando aqui\n"
        "- **Planilha** -- pra baixar uma planilha, preencher, e reenviar de uma vez\n"
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
            "pelo arquivo, não precisa repetir o nome do indicador. Ou, se mudou de ideia, "
            "manda \"cancelar\"."
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

    return "Não entendi. Digita **Manual** ou **Planilha** (ou \"cancelar\")."


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
            "mensagem já ser reconhecida certinho. Ou, se mudou de ideia, manda \"cancelar\"."
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
        "- **Manual** -- pra mudar um período de cada vez\n"
        "- **Planilha** -- pra baixar, preencher, e reenviar de uma vez\n"
        "- **Não** -- se já está bom assim"
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

    from tabelas.models import TbIndicadores
    if not TbIndicadores.objects.filter(tbcenarios_id=cenario.id).exists():
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_INDICADORES
        estado.etapa_atual = 'ind_confirmar_criar_sem_dados'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return (
            f"Não tem nenhum indicador cadastrado no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** ainda -- "
            "não tem planilha pra baixar.\n\n"
            "Quer que eu **crie um indicador novo**? (**Sim** / **Não**)"
        )

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
        "pelo arquivo, não precisa repetir o nome do indicador. Ou, se mudou de ideia, "
        "manda \"cancelar\"."
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
    # 🌟 CORRIGIDO (multi-empresa): exige que o relatório pertença à
    # empresa efetiva do usuário -- evita reconhecer/processar um
    # arquivo de OUTRA empresa.
    empresa_id_usuario = perfil.empresa_efetiva_id()
    relatorio = None
    indicador = None
    candidatos_xlsx = []
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True, empresa_id=empresa_id_usuario).first()
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


# ---------------------------------------------------------------------
# Sub-fluxo: plotar gráfico (dados reais do indicador, sem precisar da IA)
# ---------------------------------------------------------------------
def _montar_grafico_indicador(cenario, indicador, tipo_grafico):
    """
    Monta o texto de resposta com o bloco ```chart``` já pronto, usando os
    valores REAIS do indicador (mesma fonte de dados que a tela de
    edição usa) -- 100% determinístico, sem depender de nenhuma IA pra
    montar o JSON (evita qualquer risco de gráfico malformado).
    """
    from tabelas.models import TbIndicadoresDaugther
    filhas = TbIndicadoresDaugther.objects.filter(
        mae_id=indicador.id, tbcenarios_id=cenario.id
    ).order_by('dau_order')

    if not filhas:
        return f"O indicador **{indicador.ind_nome}** não tem nenhum período com valor cadastrado ainda."

    labels = [_dau_order_para_periodo(cenario, f.dau_order) for f in filhas]
    valores = [float(f.dau_valor) for f in filhas]

    config = {
        "type": tipo_grafico,
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"{indicador.ind_nome} (%)",
                "data": valores,
                "borderColor": "rgba(54, 162, 235, 1)",
                "backgroundColor": "rgba(54, 162, 235, 0.4)",
            }],
        },
        "options": {
            "plugins": {"title": {"display": True, "text": f"Indicador {indicador.ind_nome}"}},
        },
    }

    return (
        f"Aqui está o gráfico do indicador **{indicador.ind_nome}** "
        f"({labels[0]} a {labels[-1]}):\n\n"
        "```chart\n" + json.dumps(config, ensure_ascii=False) + "\n```"
    )


def _etapa_ind_grafico_escolher(estado, texto, cenario):
    indicador = _buscar_indicador(cenario.id, texto)
    if indicador is None:
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    estado.dados_coletados['indicador_id'] = indicador.id
    estado.dados_coletados['indicador_nome'] = indicador.ind_nome
    estado.etapa_atual = 'ind_grafico_tipo'
    estado.save()
    return (
        f"Indicador **{indicador.ind_nome}**.\n\n"
        "Que tipo de gráfico você quer? Escolha:\n\n"
        "- **Gráfico de Linha** -- pra ver a evolução ao longo do tempo\n"
        "- **Gráfico de Barra** -- pra comparar os valores de cada período\n\n"
        "(ou \"cancelar\")"
    )


def _etapa_ind_grafico_tipo(estado, texto, cenario):
    escolha = texto.strip().lower()
    if 'barra' in escolha:
        tipo_grafico = 'bar'
    elif 'linha' in escolha:
        tipo_grafico = 'line'
    else:
        return "Não entendi. Escolhe **Gráfico de Linha** ou **Gráfico de Barra** (ou \"cancelar\")."

    from tabelas.models import TbIndicadores
    dados = estado.dados_coletados
    indicador = TbIndicadores.objects.filter(id=dados['indicador_id']).first()
    _encerrar_fluxo(estado)
    if indicador is None:
        return "O indicador não existe mais. Cancelei o fluxo aqui."

    return _montar_grafico_indicador(cenario, indicador, tipo_grafico)


def _etapa_ind_confirmar_criar_sem_dados(estado, texto, cenario):
    """
    Trata a resposta sim/não pra pergunta "quer criar um indicador?",
    que aparece quando o usuário tenta editar/reajustar/eliminar/plotar
    um indicador mas o cenário ainda não tem nenhum cadastrado.
    """
    resposta = (texto or '').strip().lower()
    if resposta in ('sim', 's', 'yes', 'y'):
        # 🌟 Reaproveita o mesmo fluxo de criação já existente -- mesma
        # etapa e mensagem que iniciar_fluxo_indicadores usa pra
        # acao == 'criar'.
        dados = estado.dados_coletados
        dados['acao'] = 'criar'
        estado.dados_coletados = dados
        estado.etapa_atual = 'ind_criar_nome'
        estado.save()
        return (
            f"Vamos criar um indicador novo no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            "Qual vai ser o **nome** do indicador? (ou \"cancelar\" para desistir)"
        )
    elif resposta in ('nao', 'não', 'n', 'no'):
        _encerrar_fluxo(estado)
        return "Ok, não vou criar nenhum indicador agora."
    else:
        return "Não entendi -- quer que eu crie um indicador novo? Responde **Sim** ou **Não**."


_HANDLERS_INDICADORES = {
    'ind_confirmar_criar_sem_dados': _etapa_ind_confirmar_criar_sem_dados,
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
    'ind_grafico_escolher': _etapa_ind_grafico_escolher,
    'ind_grafico_tipo': _etapa_ind_grafico_tipo,
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


def determinar_acao_cambio(mensagem):
    """Mesma ideia de determinar_acao_indicadores, pra câmbio."""
    texto = (mensagem or '').lower()
    if re.search(r'gr[áa]fico|plot[ae]r?', texto):
        return 'grafico'
    elif re.search(r'cri[ae]r?|cadastr[ae]r?', texto):
        return 'criar'
    elif re.search(r'reajust|em massa|todos os per[ií]odos', texto):
        return 'massa'
    elif re.search(r'elimin|apag|exclu[ií]|delet|remov', texto):
        return 'eliminar'
    else:
        return 'editar'


def _iniciar_criacao_cambio(estado, cenario):
    """
    Monta a mensagem de início do sub-fluxo de criar uma taxa de câmbio
    nova, e atualiza o estado -- extraído numa função própria pra poder
    ser chamado tanto pela ação "criar" normal quanto pela confirmação
    sim/não que aparece quando não existe nenhuma taxa cadastrada ainda
    (ver _etapa_cam_confirmar_criar_sem_dados).
    """
    disponiveis = _moedas_disponiveis(cenario.id)
    estado.etapa_atual = 'cam_criar_moeda'
    estado.save()
    if not disponiveis:
        _encerrar_fluxo(estado)
        return "Não tem nenhuma moeda disponível pra cadastrar nesse cenário -- todas já foram usadas."
    opcoes = "\n".join(f"- {c} ({n})" for c, n in disponiveis.items())
    return (
        f"Vamos criar uma taxa de câmbio nova no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
        f"Moedas disponíveis:\n{opcoes}\n\n"
        "Qual você quer cadastrar? (ou \"cancelar\")"
    )


def iniciar_fluxo_cambio(usuario, mensagem=""):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes de mexer nas taxas de câmbio."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais. Acesse a tela de Cenários e ative outro antes de continuar."

    acao = determinar_acao_cambio(mensagem)

    estado = _get_estado(usuario)

    # 🌟 CORRIGIDO: em vez de só avisar e encerrar, agora pergunta se o
    # usuário quer criar uma taxa de câmbio nova (sim/não) -- se sim,
    # reaproveita o mesmo fluxo de criação já existente
    # (_iniciar_criacao_cambio, igual à ação "criar" normal).
    if acao != 'criar':
        from tabelas.models import TbCambio
        if not TbCambio.objects.filter(tbcenarios_id=cenario.id).exists():
            estado.fluxo_ativo = FLUXO_CAMBIO
            estado.etapa_atual = 'cam_confirmar_criar_sem_dados'
            estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
            estado.save()
            return (
                f"Não tem nenhuma taxa de câmbio cadastrada no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** ainda -- "
                "não tem o que mudar, reajustar, eliminar ou plotar.\n\n"
                "Quer que eu **crie uma taxa de câmbio nova**? (**Sim** / **Não**)"
            )

    estado.fluxo_ativo = FLUXO_CAMBIO
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'acao': acao}

    if acao == 'grafico':
        estado.etapa_atual = 'cam_grafico_escolher'
        estado.save()
        return (
            f"Vamos plotar um gráfico de câmbio no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer plotar? (ou \"cancelar\")"
        )
    elif acao == 'criar':
        return _iniciar_criacao_cambio(estado, cenario)
    elif acao == 'massa':
        estado.etapa_atual = 'cam_massa_moeda'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer reajustar? (ou \"cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'cam_eliminar_moeda'
        estado.save()
        return (
            f"Vamos eliminar uma taxa de câmbio do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual você quer eliminar? (ou \"cancelar\")"
        )
    else:
        estado.etapa_atual = 'cam_editar_moeda'
        estado.save()
        return (
            f"Vamos mudar um valor de câmbio no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
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
        "- **Manual** -- pra mudar um período de cada vez, digitando aqui\n"
        "- **Planilha** -- pra baixar uma planilha, preencher, e reenviar de uma vez\n"
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
            "pelo arquivo, não precisa repetir a moeda. Ou, se mudou de ideia, manda \"cancelar\"."
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

    return "Não entendi. Digita **Manual** ou **Planilha** (ou \"cancelar\")."


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
            "mensagem já ser reconhecida certinho. Ou, se mudou de ideia, manda \"cancelar\"."
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
        "- **Manual** -- pra mudar um período de cada vez\n"
        "- **Planilha** -- pra baixar, preencher, e reenviar de uma vez\n"
        "- **Não** -- se já está bom assim"
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
        "pelo arquivo, não precisa repetir a moeda. Ou, se mudou de ideia, manda \"cancelar\"."
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

    # 🌟 CORRIGIDO (multi-empresa): mesma correção de segurança da versão
    # de indicador.
    empresa_id_usuario = perfil.empresa_efetiva_id()
    relatorio = None
    cambio = None
    candidatos_xlsx = []
    for rid in pdf_ids:
        r = RelatorioPDF.objects.filter(id=rid, ativo=True, empresa_id=empresa_id_usuario).first()
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


# ---------------------------------------------------------------------
# Sub-fluxo: plotar gráfico de câmbio (dados reais, sem precisar da IA)
# ---------------------------------------------------------------------
def _montar_grafico_cambio(cenario, cambio, tipo_grafico):
    from tabelas.models import TbCambioDaugther
    filhas = TbCambioDaugther.objects.filter(
        mae_id=cambio.id, tbcenarios_id=cenario.id
    ).order_by('dau_order')

    if not filhas:
        return f"A taxa de câmbio **{cambio.get_cam_moeda_display()}** não tem nenhum período com valor cadastrado ainda."

    labels = [_dau_order_para_periodo(cenario, f.dau_order) for f in filhas]
    valores = [float(f.dau_valor) for f in filhas]
    nome_moeda = cambio.get_cam_moeda_display()

    config = {
        "type": tipo_grafico,
        "data": {
            "labels": labels,
            "datasets": [{
                "label": nome_moeda,
                "data": valores,
                "borderColor": "rgba(255, 159, 64, 1)",
                "backgroundColor": "rgba(255, 159, 64, 0.4)",
            }],
        },
        "options": {
            "plugins": {"title": {"display": True, "text": f"Taxa de Câmbio {nome_moeda}"}},
        },
    }

    return (
        f"Aqui está o gráfico da taxa de câmbio **{nome_moeda}** "
        f"({labels[0]} a {labels[-1]}):\n\n"
        "```chart\n" + json.dumps(config, ensure_ascii=False) + "\n```"
    )


def _etapa_cam_grafico_escolher(estado, texto, cenario):
    cambio = _buscar_cambio(cenario.id, texto)
    if cambio is None:
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"cancelar\"."

    estado.dados_coletados['cambio_id'] = cambio.id
    estado.dados_coletados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.etapa_atual = 'cam_grafico_tipo'
    estado.save()
    return (
        f"Taxa de câmbio **{cambio.get_cam_moeda_display()}**.\n\n"
        "Que tipo de gráfico você quer? Escolha:\n\n"
        "- **Gráfico de Linha** -- pra ver a evolução ao longo do tempo\n"
        "- **Gráfico de Barra** -- pra comparar os valores de cada período\n\n"
        "(ou \"cancelar\")"
    )


def _etapa_cam_grafico_tipo(estado, texto, cenario):
    escolha = texto.strip().lower()
    if 'barra' in escolha:
        tipo_grafico = 'bar'
    elif 'linha' in escolha:
        tipo_grafico = 'line'
    else:
        return "Não entendi. Escolhe **Gráfico de Linha** ou **Gráfico de Barra** (ou \"cancelar\")."

    from tabelas.models import TbCambio
    dados = estado.dados_coletados
    cambio = TbCambio.objects.filter(id=dados['cambio_id']).first()
    _encerrar_fluxo(estado)
    if cambio is None:
        return "A taxa de câmbio não existe mais. Cancelei o fluxo aqui."

    return _montar_grafico_cambio(cenario, cambio, tipo_grafico)


def _etapa_cam_confirmar_criar_sem_dados(estado, texto, cenario):
    """
    Trata a resposta sim/não pra pergunta "quer criar uma taxa de
    câmbio?", que aparece quando o usuário tenta editar/reajustar/
    eliminar/plotar uma taxa mas o cenário ainda não tem nenhuma
    cadastrada.
    """
    resposta = (texto or '').strip().lower()
    if resposta in ('sim', 's', 'yes', 'y'):
        return _iniciar_criacao_cambio(estado, cenario)
    elif resposta in ('nao', 'não', 'n', 'no'):
        _encerrar_fluxo(estado)
        return "Ok, não vou criar nenhuma taxa de câmbio agora."
    else:
        return "Não entendi -- quer que eu crie uma taxa de câmbio nova? Responde **Sim** ou **Não**."


_HANDLERS_CAMBIO = {
    'cam_confirmar_criar_sem_dados': _etapa_cam_confirmar_criar_sem_dados,
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
    'cam_grafico_escolher': _etapa_cam_grafico_escolher,
    'cam_grafico_tipo': _etapa_cam_grafico_tipo,
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
FLUXO_EXCLUIR_CENARIO = 'excluir_cenario'

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
            f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}** "
            "(operação em andamento)."
        )

    if cenario.flag == 2:  # LIMPO
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_limpeza_otimizar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **otimização**? (sim / não)"

    if cenario.flag == 3:  # OTIMIZADO
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_otimizacao_consolidar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **consolidação**? (sim / não)"

    if cenario.flag == 1 or cenario.flag is None:  # CONSOLIDADO ou nunca processado
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_consolidacao_limpar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **limpeza** (pra começar o ciclo de novo)? (sim / não)"

    return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**."


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
            f"O cenário {cenario.numero_sequencial}/{cenario.cen_nome} já está com uma operação em andamento agora "
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
        f"**{cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id}/{cenario.cen_nome}** 🔁 -- diferente do modo de ação única, aqui eu não "
        "vou perguntar \"sim/não\" a cada etapa: assim que uma etapa terminar, a próxima já dispara "
        "sozinha, e eu vou acompanhando o andamento sozinho em segundo plano -- não precisa clicar "
        "em nada, só clique em \"Cancelar\" se quiser interromper o acompanhamento."
    )


def _etapa_proc_ciclo_aguardando_limpeza(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 2:  # LIMPO -- segue direto pra otimização, sem perguntar
        return _disparar_otimizacao_ciclo(estado.usuario, cenario)

    if cenario.flag == 5:
        return f"Ainda limpando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) durante a limpeza -- melhor conferir manualmente no Admin. Cancelei o ciclo aqui."


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
        f"**{numero_exibido}/{cenario.cen_nome}** ({total_periodos} período(s)) em segundo plano. "
    )


def _etapa_proc_ciclo_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    total_periodos = dados.get('total_periodos', 0)

    total_otimizado = (
        TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=1).count()
        + TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=0).count()
    )

    if total_otimizado < total_periodos:
        return (
            f"Ainda otimizando o cenário **{numero_exibido}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} período(s) concluídos)."
        )

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag != 3:
        return "Períodos todos processados, aguardando o cenário fechar como OTIMIZADO."

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
        f"**{numero_exibido}/{cenario.cen_nome}** em segundo plano."
    )


def _etapa_proc_ciclo_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 1:  # CONSOLIDADO
        _encerrar_fluxo(estado)
        return f"✅ Ciclo completo! Cenário **{numero_exibido}/{cenario_nome}** limpo, otimizado, e consolidado."

    if cenario.flag == 6:
        return f"Ainda consolidando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) durante a consolidação -- melhor conferir manualmente no Admin."


def determinar_acao_processar(mensagem):
    """
    Mesma ideia de determinar_acao_indicadores, pra limpar/otimizar/
    consolidar. Devolve None se a mensagem não bater com nenhuma das 3.
    """
    texto = (mensagem or '').lower()
    if re.search(r'limp[ae]r?', texto):
        return 'limpar'
    elif re.search(r'otimiz[ae]r?', texto):
        return 'otimizar'
    elif re.search(r'consolid[ae]r?', texto):
        return 'consolidar'
    return None


def iniciar_fluxo_processar(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    acao = determinar_acao_processar(mensagem)
    if acao is None:
        return "Não entendi se você quer **limpar**, **otimizar**, ou **consolidar** o cenário ativo. Pode repetir dizendo qual dessas ações?"

    # 🌟 CORRIGIDO: a trava de "já tem operação em andamento" NÃO deve
    # valer pra "limpar" -- limpar é sempre permitido, independente do
    # status atual do cenário (é justamente o jeito de "resetar" um
    # cenário que ficou travado ou num estado inconsistente). Só faz
    # sentido bloquear otimizar/consolidar enquanto algo já está rodando.
    if acao != 'limpar' and cenario.flag in FLAGS_OPERACAO_EM_ANDAMENTO:
        return (
            f"O cenário {cenario.numero_sequencial}/{cenario.cen_nome} já está com uma operação em andamento agora "
            f"({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}). Espera terminar antes de disparar outra."
        )

    # 🌟 Restrições da cadeia: só otimiza se estiver LIMPO (flag=2), só
    # consolida se estiver OTIMIZADO (flag=3).
    if acao == 'otimizar' and cenario.flag != 2:
        status_atual = MENSAGENS_FLAG.get(cenario.flag, f'flag={cenario.flag}')
        return (
            f"Não dá pra otimizar o cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** agora -- ele precisa "
            f"estar **LIMPO** primeiro, e o status atual é **{status_atual}**. Limpa o cenário antes "
            "de otimizar."
        )

    if acao == 'consolidar' and cenario.flag != 3:
        status_atual = MENSAGENS_FLAG.get(cenario.flag, f'flag={cenario.flag}')
        return (
            f"Não dá pra consolidar o cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** agora -- ele precisa "
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
    numero_exibido = _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{numero_exibido}/{cenario_nome}** fica como está."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

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
        f"Disparei a **limpeza** do cenário **{numero_exibido}/{cenario.cen_nome}** em segundo plano. "
    )


def _etapa_proc_aguardando_limpeza(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 2:  # LIMPO
        estado.etapa_atual = 'proc_pos_limpeza_otimizar'
        estado.save()
        return f"✅ Cenário **{numero_exibido}/{cenario_nome}** limpo! Quer que eu já dispare a **otimização**? (sim / não)"

    if cenario.flag == 5:  # ainda limpando
        return f"Ainda limpando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin. Cancelei o acompanhamento automático aqui."


def _etapa_proc_pos_limpeza_otimizar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{numero_exibido}/{cenario_nome}** fica limpo por enquanto. É só pedir pra otimizar quando quiser."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

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
        f"Disparei a **otimização** do cenário **{numero_exibido}/{cenario.cen_nome}** "
        f"({total_periodos} período(s)) em segundo plano."
    )


def _etapa_proc_aguardando_otimizacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    total_periodos = dados.get('total_periodos', 0)

    total_otimizado = (
        TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=1).count()
        + TbCenariosDaugther.objects.filter(mae_id=cenario_id, flag=0).count()
    )

    if total_otimizado < total_periodos:
        return (
            f"Ainda otimizando o cenário **{numero_exibido}/{cenario_nome}** "
            f"({total_otimizado} de {total_periodos} período(s) concluídos). "
        )

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag != 3:
        return "Períodos todos processados, aguardando o cenário fechar como OTIMIZADO."

    estado.etapa_atual = 'proc_pos_otimizacao_consolidar'
    estado.save()
    return f"✅ Cenário **{numero_exibido}/{cenario_nome}** otimizado! Quer que eu já dispare a **consolidação**? (sim / não)"


def _etapa_proc_pos_otimizacao_consolidar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, cenário **{numero_exibido}/{cenario_nome}** fica otimizado por enquanto. É só pedir pra consolidar quando quiser."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

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
        f"Disparei a **consolidação** do cenário **{numero_exibido}/{cenario.cen_nome}** em segundo plano. "
    )


def _etapa_proc_aguardando_consolidacao(estado, texto):
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 1:  # CONSOLIDADO
        _encerrar_fluxo(estado)
        return f"✅ Cenário **{numero_exibido}/{cenario_nome}** consolidado!"

    if cenario.flag == 6:  # ainda consolidando
        return f"Ainda consolidando o cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin."


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
    'exportar_otimizacao_confirmar_email': _etapa_exportar_otimizacao_confirmar_email,
    'exportar_otimizacao_aguardando': _etapa_exportar_otimizacao_aguardando,
    'exportar_otimizacao_aguardando_email': _etapa_exportar_otimizacao_aguardando_email,
}


# =======================================================================
# Fluxo: excluir_cenario -- deixa o usuário (superusuário ou superusuário
# de empresa) escolher, entre os cenários da própria empresa, quais quer
# excluir -- sempre protegendo o cenário ATIVO de qualquer usuário e os
# cenários BASE da empresa, que nunca podem ser excluídos. Reaproveita a
# mesma regra de segurança já usada no botão "Remover Cenário(s)
# Selecionado(s)" do Admin (TbCenariosAdmin.delete_selected).
# =======================================================================

def _cenarios_elegiveis_para_exclusao(empresa_id):
    """
    Cenários da empresa que PODEM ser excluídos: não são cenário base, e
    não estão marcados como cenário ativo de nenhum usuário no momento.
    """
    ids_ativos = set(
        PerfilUsuario.objects.filter(
            cenario_ativo__isnull=False, cenario_ativo__empresa_id=empresa_id
        ).values_list('cenario_ativo_id', flat=True)
    )
    # 🌟 CORRIGIDO: ordena por numero_sequencial (o número que o usuário
    # realmente vê e usa) em vez de -id (o id bruto do banco) -- os dois
    # normalmente coincidem em ordem, mas não são garantidamente iguais
    # (ex: depois de excluir e recriar cenários no meio do caminho).
    return (
        TbCenarios.objects_real
        .filter(empresa_id=empresa_id, eh_cenario_base=False)
        .exclude(id__in=ids_ativos)
        .order_by('-numero_sequencial')
    )


def iniciar_fluxo_excluir_cenario(usuario):
    if not eh_superuser_ou_superuser_empresa(usuario):
        return "Você não tem autorização para excluir cenários. Fale com o administrador do sistema."

    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        return "Não consegui identificar sua empresa."

    elegiveis_qs = _cenarios_elegiveis_para_exclusao(empresa_id)
    total_elegiveis = elegiveis_qs.count()
    candidatos_qs = list(elegiveis_qs[:10])
    if not candidatos_qs:
        return (
            "Não tem nenhum cenário que possa ser excluído agora -- os únicos existentes são "
            "cenários base, ou estão marcados como ativos por algum usuário."
        )

    # 🌟 CORRIGIDO: guarda numero_sequencial junto com o nome -- a chave
    # do dicionário continua sendo o id real (necessário pra localizar o
    # registro certo no banco na hora de excluir), mas TUDO que é
    # mostrado ou digitado pelo usuário passa a usar numero_sequencial,
    # nunca o id bruto.
    candidatos = {
        str(c.id): {'numero': c.numero_sequencial if c.numero_sequencial is not None else c.id, 'nome': c.cen_nome}
        for c in candidatos_qs
    }

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_EXCLUIR_CENARIO
    estado.dados_coletados = {
        'empresa_id': empresa_id,
        'candidatos': candidatos,
        'selecionados': [],
        'total_elegiveis': total_elegiveis,
    }
    estado.etapa_atual = 'cen_excluir_selecionar'
    estado.save()
    return _texto_selecao_exclusao(estado.dados_coletados)


def _texto_selecao_exclusao(dados):
    candidatos = dados['candidatos']
    selecionados = set(dados['selecionados'])
    total_elegiveis = dados.get('total_elegiveis', len(candidatos))
    linhas = []
    for id_str, info in candidatos.items():
        marcador = " ✅ (marcado pra excluir)" if id_str in selecionados else ""
        linhas.append(f"- {info['numero']}: {info['nome']}{marcador}")
    lista = "\n".join(linhas)
    aviso_total = (
        f" (mostrando {len(candidatos)} dos {total_elegiveis} elegíveis -- os mais recentes)"
        if total_elegiveis > len(candidatos) else ""
    )
    return (
        "⚠️ Cenários que podem ser excluídos (o cenário ativo de qualquer usuário e os "
        f"cenários base da empresa nunca aparecem aqui){aviso_total}:\n\n"
        f"Alguns cenários recentes:\n{lista}\n\n"
        "Clique num cenário pra marcar/desmarcar pra exclusão (pode marcar mais de um). Se o "
        "que você quer excluir não está nessa lista (ela só mostra os mais recentes), digite "
        "o **número** ou o **nome** dele diretamente. Quando terminar de escolher, digite ou "
        "clique em \"concluir\". (ou \"cancelar\")"
    )


def _processar_excluir_cenario(estado, texto):
    etapa = estado.etapa_atual
    if etapa == 'cen_excluir_selecionar':
        return _etapa_cen_excluir_selecionar(estado, texto)
    elif etapa == 'cen_excluir_confirmar':
        return _etapa_cen_excluir_confirmar(estado, texto)
    elif etapa == 'cen_excluir_aguardando':
        return _etapa_cen_excluir_aguardando(estado, texto)
    _encerrar_fluxo(estado)
    return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."


def _etapa_cen_excluir_selecionar(estado, texto):
    dados = estado.dados_coletados
    candidatos = dados['candidatos']
    escolha = texto.strip().lower()

    if escolha == 'concluir':
        if not dados['selecionados']:
            return "Você ainda não marcou nenhum cenário. Clica num cenário da lista pra marcar, ou \"cancelar\"."
        estado.etapa_atual = 'cen_excluir_confirmar'
        estado.save()
        nomes = ", ".join(f"{candidatos[id_str]['numero']}/{candidatos[id_str]['nome']}" for id_str in dados['selecionados'])
        return (
            f"⚠️ Confirma a EXCLUSÃO PERMANENTE do(s) cenário(s): **{nomes}**?\n\n"
            "Essa ação não pode ser desfeita. (sim / não)"
        )

    # 🌟 CORRIGIDO: aceita clicar/digitar o NÚMERO SEQUENCIAL (o que o
    # usuário realmente vê na lista), nunca o id bruto do banco. Também
    # aceita digitar o nome do cenário -- inclusive de um cenário que
    # NÃO está entre os mostrados na lista (que só traz os mais
    # recentes, por espaço). Confere contra TODOS os elegíveis da
    # empresa, não só os pré-carregados.
    texto_limpo = texto.strip()
    id_str = None

    for candidato_id, info in candidatos.items():
        if str(info['numero']) == texto_limpo:
            id_str = candidato_id
            break

    if id_str is None:
        elegiveis = _cenarios_elegiveis_para_exclusao(dados['empresa_id'])
        cenario_digitado = None
        if texto_limpo.isdigit():
            cenario_digitado = elegiveis.filter(numero_sequencial=int(texto_limpo)).first()
        if cenario_digitado is None:
            cenario_digitado = elegiveis.filter(cen_nome__iexact=texto_limpo).first()
        if cenario_digitado is not None:
            id_str = str(cenario_digitado.id)
            numero_exibido = (
                cenario_digitado.numero_sequencial
                if cenario_digitado.numero_sequencial is not None
                else cenario_digitado.id
            )
            # 🌟 Achou um cenário elegível que ainda não estava na lista
            # mostrada -- adiciona ele aos candidatos conhecidos, pra
            # aparecer certinho no resumo e na confirmação depois.
            candidatos[id_str] = {'numero': numero_exibido, 'nome': cenario_digitado.cen_nome}
            dados['candidatos'] = candidatos

    if id_str is None:
        return (
            "Não encontrei nenhum cenário elegível com esse número/nome (lembrando: o cenário ativo "
            "de qualquer usuário e os cenários base nunca podem ser excluídos). Pode digitar o "
            "número ou nome de QUALQUER cenário elegível, mesmo que ele não esteja na lista mostrada "
            "-- ela só traz os mais recentes. Ou digite \"concluir\" quando terminar (ou \"cancelar\")."
        )

    selecionados = dados['selecionados']
    if id_str in selecionados:
        selecionados.remove(id_str)
    else:
        selecionados.append(id_str)
    dados['selecionados'] = selecionados
    estado.dados_coletados = dados
    estado.save()
    return _texto_selecao_exclusao(dados)


def _etapa_cen_excluir_confirmar(estado, texto):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    empresa_id = dados['empresa_id']

    if resposta not in ('sim', 's'):
        _encerrar_fluxo(estado)
        return "Ok, não excluí nada."

    # 🌟 Reconfere as regras de segurança bem na hora de excluir de
    # verdade (defesa em profundidade -- algo pode ter mudado entre a
    # seleção e a confirmação, tipo outro usuário ativar um desses
    # cenários nesse meio tempo).
    elegiveis_agora = set(str(c.id) for c in _cenarios_elegiveis_para_exclusao(empresa_id))
    validos = [i for i in dados['selecionados'] if i in elegiveis_agora]
    invalidos = [i for i in dados['selecionados'] if i not in elegiveis_agora]

    if not validos:
        _encerrar_fluxo(estado)
        return (
            "Nenhum dos cenários selecionados pôde ser excluído -- todos deixaram de ser "
            "elegíveis nesse meio tempo (podem ter virado o cenário ativo de alguém). "
            "Nada foi excluído."
        )

    for id_str in validos:
        remover_cenario_celery.delay(int(id_str))

    # 🌟 CORRIGIDO: exibe numero_sequencial/nome, nunca o id bruto.
    def _formatar_candidato(id_str):
        info = dados['candidatos'].get(id_str, {})
        return f"{info.get('numero', id_str)}/{info.get('nome', '')}"

    nomes_validos = ", ".join(_formatar_candidato(i) for i in validos)
    aviso_invalidos = ""
    if invalidos:
        nomes_invalidos = ", ".join(_formatar_candidato(i) for i in invalidos)
        aviso_invalidos = f"\n\n(Não excluí {nomes_invalidos} -- deixaram de ser elegíveis nesse meio tempo.)"

    # 🌟 CORRIGIDO: em vez de simplesmente encerrar o acompanhamento e
    # mandar "atualize a tela" (que não dava pro usuário conferir pelo
    # próprio chat), fica aguardando aqui -- igual ao padrão de Limpar/
    # Otimizar/Consolidar, com um botão "Verificar" que reconsulta o
    # banco de verdade pra ver se cada cenário já sumiu (foi excluído).
    estado.etapa_atual = 'cen_excluir_aguardando'
    estado.dados_coletados = {
        'ids_excluindo': validos,
        'nomes_excluindo': {i: dados['candidatos'].get(i, {}) for i in validos},
    }
    estado.save()

    return (
        f"Cenário(s) **{nomes_validos}** sendo excluído(s) em segundo plano. "
        f"{aviso_invalidos}"
    )


def _etapa_cen_excluir_aguardando(estado, texto):
    dados = estado.dados_coletados
    ids_excluindo = dados.get('ids_excluindo', [])
    nomes_excluindo = dados.get('nomes_excluindo', {})

    if 'verificar' not in texto.strip().lower():
        return (
            "Ainda estou de olho na exclusão desses cenários "
            "(ou digite \"cancelar\" pra parar de acompanhar -- a "
            "exclusão em si continua rodando em segundo plano de qualquer jeito)."
        )

    ainda_existem = set(
        str(i) for i in TbCenarios.objects_real.filter(id__in=[int(i) for i in ids_excluindo]).values_list('id', flat=True)
    )
    ja_excluidos = [i for i in ids_excluindo if i not in ainda_existem]
    pendentes = [i for i in ids_excluindo if i in ainda_existem]

    # 🌟 CORRIGIDO: exibe numero_sequencial/nome, nunca o id bruto.
    def _formatar_excluindo(id_str):
        info = nomes_excluindo.get(id_str, {})
        return f"{info.get('numero', id_str)}/{info.get('nome', '')}"

    if not pendentes:
        _encerrar_fluxo(estado)
        nomes = ", ".join(_formatar_excluindo(i) for i in ja_excluidos)
        return f"✅ Cenário(s) **{nomes}** excluído(s) com sucesso."

    nomes_pendentes = ", ".join(_formatar_excluindo(i) for i in pendentes)
    if ja_excluidos:
        nomes_prontos = ", ".join(_formatar_excluindo(i) for i in ja_excluidos)
        return (
            f"✅ Já excluído: {nomes_prontos}.\n\n"
            f"⏳ Ainda em andamento: {nomes_pendentes}."
        )

    return f"⏳ Ainda excluindo **{nomes_pendentes}**."