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

Em qualquer etapa, o usuário pode digitar "Cancelar" para abortar o wizard
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

Em qualquer etapa, de qualquer fluxo, o usuário pode digitar "Cancelar" para
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

PALAVRAS_CANCELAR = ('cancelar', 'cancela', 'sair', 'parar', 'desistir', 'encerrar ação', 'encerrar acao')
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
    'proc_aguardando_atualizar_fluxos',
    'proc_aguardando_otimizacao',
    'proc_aguardando_consolidacao',
    'cen_excluir_aguardando',
    'cf_processando_producao',
    'cf_processando_ggf',
    'cf_processando_conta_cc_tipo',
    'cf_processando_genealogia',
    'ce_processando_consumo_especifico',
    'ce_processando_custo_variavel',
    'ce_processando_indicador_fluxo',
    'ce_processando_indicador_equipamentos',
    'ce_processando_custo_item_preco',
    # 🌟 NOVO: "Criar/Atualizar Fluxo no Editor" (categoria Fluxos de
    # Produção) -- até aqui, disparava a task e esquecia (sem barra de
    # progresso, sem "Verificar", sem aviso de conclusão) -- diferente
    # de toda ação semelhante no resto do chat. Agora segue o mesmo
    # padrão: barra de progresso, sondagem automática, e "Cancelar" só
    # encerra o acompanhamento (a montagem em si continua em segundo
    # plano).
    'fp_criar_no_editor_aguardando',
    # 🌟 NOVO: "Fluxos de Produção - Criar Fluxos de Produção dos
    # Produtos" -- enquanto gerar_fluxos_produto_celery roda em segundo
    # plano, mesmo padrão de sondagem automática + barra de progresso.
    'fp_criar_fluxos_produto_aguardando',
    # 🌟 NOVO: "Equipamentos - Criar Novo Equipamento": gera os fluxos de cada produto afetado, um de cada vez.
    'equ_criar_fluxos_aguardando',
    # 🌟 NOVO: "Atualizar Fluxos de Produção por Produto": cálculo da situação e execução, produto a produto.
    'fp_sit_calculando', 'fp_lote_aguardando', 'fp_io_aguardando',
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
    'proc_aguardando_limpeza', 'proc_aguardando_atualizar_fluxos', 'proc_aguardando_otimizacao', 'proc_aguardando_consolidacao',
    'proc_ciclo_aguardando_limpeza', 'proc_ciclo_aguardando_otimizacao', 'proc_ciclo_aguardando_consolidacao',
    # 🌟 NOVO: esperar o usuário preparar/subir um arquivo pode levar bem
    # mais que 15 minutos (procurar o relatório certo, exportar do
    # sistema de origem, etc.) -- sem risco de confusão, já que qualquer
    # mensagem enviada aqui sem arquivo marcado só reforça o lembrete de
    # subir o arquivo, nunca dispara uma ação por engano.
    'cf_aguardando_producao', 'cf_aguardando_ggf',
    'cf_processando_producao', 'cf_processando_ggf',
    'cf_aguardando_conta_cc_tipo', 'cf_processando_conta_cc_tipo',
    'cf_processando_genealogia',
    'ce_processando_consumo_especifico', 'ce_processando_custo_variavel',
    'ce_processando_indicador_fluxo', 'ce_processando_indicador_equipamentos', 'ce_processando_custo_item_preco',
    'equ_criar_fluxos_aguardando',
    'fp_sit_calculando', 'fp_lote_aguardando', 'fp_io_aguardando',
}


def usuario_esta_em_fluxo(usuario):
    """
    True se o usuário tem QUALQUER um dos wizards em andamento agora.

    🌟 CORRIGIDO: antes só conferia SE tinha um fluxo marcado, sem
    checar se ele já tinha EXPIRADO (mais de MINUTOS_EXPIRACAO_FLUXO
    parado, numa etapa que não é isenta -- ver ETAPAS_SEM_EXPIRACAO).
    Isso fazia a PRIMEIRA mensagem depois de um fluxo velho (esquecido,
    ou interrompido por um restart do servidor no meio de um teste) ser
    ENGOLIDA: o sistema só limpava o estado e devolvia um aviso de
    "expirou", obrigando a mandar a mesma mensagem de novo pra ela ser
    processada de verdade. Agora essa limpeza acontece bem aqui,
    silenciosamente, ANTES do resto do sistema decidir como rotear a
    mensagem -- assim, uma mensagem que chega logo depois de um fluxo
    expirado já é tratada como se não houvesse fluxo nenhum, e
    processada normalmente na mesma tacada, sem aviso nenhum e sem
    precisar reenviar.
    """
    estado = EstadoConversaAgente.objects.filter(usuario=usuario).exclude(fluxo_ativo__isnull=True).first()
    if estado is None:
        return False
    if (
        estado.etapa_atual not in ETAPAS_SEM_EXPIRACAO
        and (timezone.now() - estado.atualizado_em) > timedelta(minutes=MINUTOS_EXPIRACAO_FLUXO)
    ):
        _encerrar_fluxo(estado)
        return False
    return True


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
        "\"Cancelar\" para desistir)"
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
            f"Qual o novo **início** do período? (formato {exemplo}, ou \"Cancelar\" para desistir)"
        )

    estado.etapa_atual = 'tipo'
    estado.save()
    return (
        f"Vamos mudar o cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 🔧\n\n"
        f"Tipo atual: **{cenario.cen_tipo}**\n"
        f"Período atual: **{cenario.cen_inicio}** a **{cenario.cen_fim}**\n\n"
        "Para qual **tipo** você quer mudar? (Mensal / Trimestral / Anual, "
        "\"Manter\" para deixar como está, ou \"Cancelar\" para desistir)"
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
        # 🌟 NOVO: mesmo raciocínio -- cancelar o acompanhamento da
        # importação de Produção Mensal/Distribuição GGF Mensal/correção
        # de Tipo não cancela o processamento em si (já disparado em
        # segundo plano via Celery).
        if estado.fluxo_ativo == FLUXO_IMPORTAR_CF and estado.etapa_atual in ('cf_processando_producao', 'cf_processando_ggf', 'cf_processando_conta_cc_tipo'):
            _encerrar_fluxo(estado)
            return (
                "Ok, parei de acompanhar por aqui -- mas o processamento em si continua rodando em "
                "segundo plano normalmente (isso não cancela ele)."
            )
        # 🌟 NOVO: a Genealogia é a etapa mais demorada de todo o fluxo --
        # aqui o botão mostrado ao usuário é "Encerrar Ação" (em vez de
        # "Cancelar"), justamente pra não dar a entender que a Genealogia
        # em si vai ser interrompida. Mesmo assim, se o usuário digitar
        # "Cancelar" (ou qualquer outro sinônimo) em vez de clicar no
        # botão, o comportamento e o aviso são os mesmos.
        if estado.fluxo_ativo == FLUXO_IMPORTAR_CF and estado.etapa_atual == 'cf_processando_genealogia':
            _encerrar_fluxo(estado)
            return (
                "Ok, encerrei o acompanhamento por aqui -- mas isso **não interrompe** a atualização "
                "da Genealogia dos Produtos, que continua rodando em segundo plano normalmente até "
                "terminar."
            )
        # 🌟 CORRIGIDO: a primeira etapa (Consumo Específico) usa a
        # mensagem padrão de "cancelei" -- cancelar aqui interrompe a
        # SEQUÊNCIA (o Custo Variável Adicionado não dispara mais
        # automaticamente depois), que é o que importa pro usuário,
        # mesmo o cálculo já disparado no banco não podendo ser desfeito
        # de verdade. Só a ÚLTIMA etapa (Custo Variável Adicionado, sem
        # nada encadeado depois) usa a mensagem de "Encerrar Ação",
        # deixando claro que só para de acompanhar.
        if estado.fluxo_ativo == FLUXO_CONSUMO_ESPECIFICO and estado.etapa_atual == 'ce_processando_custo_variavel':
            _encerrar_fluxo(estado)
            return (
                "Ok, encerrei o acompanhamento por aqui -- mas isso **não interrompe** o cálculo, "
                "que continua rodando em segundo plano normalmente até terminar."
            )
        if estado.fluxo_ativo == FLUXO_CONSUMO_ESPECIFICO and estado.etapa_atual == 'ce_processando_consumo_especifico':
            _encerrar_fluxo(estado)
            return (
                "Ok, cancelei a sequência -- o Custo Variável Adicionado não vai disparar "
                "automaticamente. O cálculo do Consumo Específico já disparado no banco continua "
                "rodando normalmente até terminar por conta própria."
            )
        # 🌟 NOVO: mesmo raciocínio -- cancelar o acompanhamento de
        # "Criar/Atualizar Fluxo no Editor" não cancela a montagem em si
        # (já disparada em segundo plano via Celery, fluxo por fluxo).
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'fp_criar_no_editor_aguardando':
            _encerrar_fluxo(estado)
            return (
                "Ok, parei de acompanhar por aqui -- mas a montagem do fluxo no editor continua "
                "rodando em segundo plano normalmente (isso não cancela ela)."
            )
        # 🌟 NOVO: o equipamento novo (cadastro, ordens, itens e consumos padrão) JÁ foi gravado nessa etapa --
        # cancelar aqui só para de acompanhar/encadear os próximos produtos, não desfaz nada.
        if estado.fluxo_ativo == FLUXO_EQUIPAMENTOS and estado.etapa_atual == 'equ_criar_fluxos_aguardando':
            dados_cancelar = estado.dados_coletados or {}
            atual = dados_cancelar.get('produto_codigo', '')
            pendentes = [p['codigo'] for p in dados_cancelar.get('fila') or []]
            _encerrar_fluxo(estado)
            texto_cancelar = (
                "Ok, parei de acompanhar por aqui. O equipamento novo, as ordens e os consumos padrão **já foram "
                f"criados** (isso não é desfeito), e a geração dos fluxos do produto **{atual}** continua em segundo plano."
            )
            if pendentes:
                texto_cancelar += (
                    " Os fluxos destes produtos **não** serão gerados: " + ", ".join(pendentes)
                    + ". Pede depois em Fluxos de Produção > Atualizar Fluxos de Produção por Produto."
                )
            return texto_cancelar
        # 🌟 NOVO: "Atualizar Fluxos por Produto" já em execução: o que foi atualizado não é desfeito; cancelar só para
        # de acompanhar e de encadear os próximos produtos.
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'fp_lote_aguardando':
            d_cancelar = estado.dados_coletados or {}
            atual = d_cancelar.get('produto_codigo', '')
            pendentes = [p['codigo'] for p in d_cancelar.get('fila') or []]
            _encerrar_fluxo(estado)
            texto_cancelar = (
                "Ok, parei de acompanhar por aqui. O que já foi atualizado **não é desfeito**, e a atualização do "
                f"produto **{atual}** continua em segundo plano."
            )
            if pendentes:
                texto_cancelar += (" Estes produtos **não** serão atualizados: " + ", ".join(pendentes)
                                   + ". Pede de novo nesta mesma ação quando quiser.")
            return texto_cancelar
        # 🌟 NOVO: depois da atualização dos produtos o genérico ("nada foi alterado") seria falso.
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'fp_io_confirmar':
            _encerrar_fluxo(estado)
            return _FP_IO_TEXTO_NAO
        if estado.fluxo_ativo == FLUXO_PROCESSAR and estado.etapa_atual == 'fp_io_aguardando':
            _encerrar_fluxo(estado)
            return ("Ok, parei de acompanhar por aqui. A atualização do I/O continua em segundo plano e **não é "
                    "desfeita**.")
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
    elif estado.fluxo_ativo == FLUXO_IMPORTAR_CF:
        return _processar_importar_cf(estado, texto)
    elif estado.fluxo_ativo == FLUXO_CONSUMO_ESPECIFICO:
        return _processar_consumo_especifico(estado, texto)
    elif estado.fluxo_ativo == FLUXO_EQUIPAMENTOS:
        return _processar_equipamentos(estado, texto)
    elif estado.fluxo_ativo == FLUXO_TOOL_CALL:
        return _processar_tool_call(estado, texto)

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
            "ou clique em \"Cancelar\" pra desistir."
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
            "Tenta de novo com o número ou o nome exato, ou \"Cancelar\"."
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

    from fluxos.models import TbFluxoProducaoDaugther01

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        _encerrar_fluxo(estado)
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    resposta_fluxos = _perguntar_atualizar_fluxos_ou_encaminhar(
        estado.usuario, cenario, 'confirmar_processar',
        dados_extra={**dados, 'cenario_criado_id': cenario_id, 'cenario_criado_nome': cenario_nome, 'cenario_criado_numero_sequencial': numero_exibido},
    )
    if resposta_fluxos is not None:
        return resposta_fluxos

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        _encerrar_fluxo(estado)
        return "Tem fluxo(s) de produção ativo(s) com custo variável zerado. Verifica isso no Admin antes de tentar de novo."

    return _dispatch_limpeza_pos_criacao(estado.usuario, cenario_id, cenario_nome, numero_exibido, dados_base=dados)


def _dispatch_limpeza_pos_criacao(usuario, cenario_id, cenario_nome, numero_exibido, dados_base=None):
    """
    🌟 NOVO: extraído de _etapa_confirmar_processar -- despacha a
    limpeza logo após criar um cenário novo (flag=5 + limpar_cenario_
    celery.delay + etapa 'aguardando_limpeza' do FLUXO_CRIAR).
    Também chamada por _continuar_apos_atualizar_fluxos, quando o
    usuário pediu pra atualizar os fluxos antes de continuar.
    """
    from django.db import connection
    from .tasks import limpar_cenario_celery

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 5 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    limpar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CRIAR
    estado.etapa_atual = 'aguardando_limpeza'
    # 🌟 CORRIGIDO: preserva TODOS os dados que o fluxo já tinha (em
    # especial cen_copiar_de_id, usado no final pra comparar o cenário
    # novo com o de origem). Antes substituía por só 3 chaves e a
    # comparação final rodava contra "nenhum cenário".
    novos_dados = dict(dados_base if dados_base is not None else (estado.dados_coletados or {}))
    for chave_temporaria in ('continuar_com', 'cenario_id', 'cenario_nome', 'task_ids', 'disparado_em'):
        novos_dados.pop(chave_temporaria, None)
    novos_dados.update({
        'cenario_criado_id': cenario_id,
        'cenario_criado_nome': cenario_nome,
        'cenario_criado_numero_sequencial': numero_exibido,
    })
    estado.dados_coletados = novos_dados
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

    if not cenario_origem_id:
        return "Não consegui identificar de qual cenário este foi copiado, então não fiz a comparação."

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

    # 🌟 NOVO: a comparação é feita mesmo com grupos diferentes, mas o
    # usuário precisa saber -- a tela "Comparação de Cenários" do Admin
    # só permite comparar cenários do mesmo grupo.
    novo = TbCenarios.objects_real.filter(id=cenario_novo_id).select_related('cen_grupo').first()
    origem = TbCenarios.objects_real.filter(id=cenario_origem_id).select_related('cen_grupo').first()
    if novo is not None and origem is not None and novo.cen_grupo_id != origem.cen_grupo_id:
        grupo_novo = novo.cen_grupo.gru_cen_codigo if novo.cen_grupo_id else 'nenhum'
        grupo_origem = origem.cen_grupo.gru_cen_codigo if origem.cen_grupo_id else 'nenhum'
        linhas.append(
            f"\n⚠️ Atenção: os dois cenários estão em **grupos diferentes** (novo: {grupo_novo}; origem: {grupo_origem}). "
            "A comparação acima foi feita normalmente, mas a tela de Comparação de Cenários do Admin só "
            "permite comparar cenários do mesmo grupo."
        )

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
            return "Não entendi. Escolhe um: Mensal, Trimestral ou Anual (ou \"Manter\" pra deixar como está)."

    dados['tipo_novo'] = tipo_novo
    estado.dados_coletados = dados
    estado.etapa_atual = 'periodo_inicio'
    estado.save()

    exemplo = {'Mensal': '2026/06', 'Trimestral': '2026/02', 'Anual': '2026'}[tipo_novo]
    manter_periodo = " (ou \"Manter\" pra deixar como está)" if tipo_novo == dados['tipo_atual'] else ""
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
    manter_fim = " (ou \"Manter\" pra deixar como está)" if tipo_novo == dados['tipo_atual'] else ""
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
        "Confirma a mudança? (Sim / Não)"
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
            "Qual **indicador** você quer plotar? (ou \"Cancelar\")"
        )
    elif acao == 'criar':
        estado.etapa_atual = 'ind_criar_nome'
        estado.save()
        return (
            f"Vamos criar um indicador novo no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            "Qual vai ser o **nome** do indicador? (ou \"Cancelar\" para desistir)"
        )
    elif acao == 'massa':
        estado.etapa_atual = 'ind_massa_nome'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer reajustar? (ou \"Cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'ind_eliminar_nome'
        estado.save()
        return (
            f"Vamos eliminar um indicador do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer eliminar? (ou \"Cancelar\")"
        )
    else:
        estado.etapa_atual = 'ind_editar_nome'
        estado.save()
        return (
            f"Vamos mudar um valor de indicador no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 📊\n\n"
            f"{_lista_indicadores(cenario.id)}"
            "Qual **indicador** você quer mudar? (ou \"Cancelar\")"
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


# 🌟 NOVO: "Fluxos de Produção - Criar/Atualizar Fluxo no Editor" -- monta o
# editor visual (flu_pro_dados_fluxo) de TODOS os fluxos de produção do
# cenário ativo a partir da respectiva tabela filha (coluna/linha), em
# segundo plano. Mesma ação do botão em massa no Admin, só que
# disparada pelo chat e já escopada ao cenário ativo (não precisa
# selecionar fluxo por fluxo).
# ⚠️ Decisão assumida: como um cenário pode ter vários fluxos (um por
# produto) e a Ação Comum, aqui, não pergunta QUAL fluxo, ela aplica a
# TODOS os fluxos do cenário ativo de uma vez -- mesmo espírito das
# outras Ações Comuns da categoria "Cenário" (Limpar/Otimizar/
# Consolidar), que também operam sobre o cenário inteiro. Se o
# comportamento esperado for outro (ex: perguntar qual fluxo), é aqui
# que se ajusta.
def iniciar_criar_fluxo_no_editor_cenario_ativo(usuario):
    """
    🌟 CORRIGIDO: até aqui, disparava a task e devolvia a resposta na
    hora, sem nenhum acompanhamento -- diferente de toda ação parecida
    no resto do chat (limpar, otimizar, consolidar, exportar
    otimização, etc.), que mostra barra de progresso e permite
    "Cancelar" (o acompanhamento; a task em si nunca é interrompida).
    Agora segue o mesmo padrão: registra a etapa
    'fp_criar_no_editor_aguardando' (ver ETAPAS_AGUARDANDO_CELERY) e o
    front-end passa a sondar sozinho até a task escrever o resultado de
    volta em EstadoConversaAgente (ver criar_fluxo_no_editor_lista_celery
    em fluxos/tasks.py).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    from fluxos.models import TbFluxoProducao
    from fluxos.tasks import criar_fluxo_no_editor_lista_celery

    lista_id = list(TbFluxoProducao.objects.filter(tbcenarios_id=cenario.id, flu_pro_auto_editor_aut=True).values_list('id', flat=True))
    if not lista_id:
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** não tem nenhum fluxo de produção com atualização automática habilitada."

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR  # reaproveita o mesmo "namespace" de fluxo simples
    estado.etapa_atual = 'fp_criar_no_editor_aguardando'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'cenario_nome': cenario.cen_nome,
        'total_fluxos': len(lista_id),
        'status': 'processando',
    }
    estado.save()

    criar_fluxo_no_editor_lista_celery.delay(lista_id, usuario.id)

    return (
        f"Criação/atualização do fluxo no editor sendo feita em segundo plano pra {len(lista_id)} "
        f"fluxo(s) do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}**. "
        "⚠️ Isso substitui qualquer arranjo manual que já estivesse no editor de cada fluxo se o campo "
        "\"Permite Atualização Automática\" no registro do fluxo estiver MARCADO."
    )


def _etapa_fp_criar_no_editor_aguardando(estado, texto):
    """
    🌟 NOVO: acompanha (via "Verificar", sondado automaticamente pelo
    JS -- ver ETAPAS_AGUARDANDO_CELERY) a task
    criar_fluxo_no_editor_lista_celery, disparada por
    iniciar_criar_fluxo_no_editor_cenario_ativo.
    """
    dados = estado.dados_coletados or {}
    cenario_nome = dados.get('cenario_nome', '')
    numero_exibido = _numero_sequencial_por_id(dados.get('cenario_id'))
    total_fluxos = dados.get('total_fluxos', 0)
    status = dados.get('status')

    if status == 'processando':
        return f"Ainda montando/atualizando o fluxo no editor pros {total_fluxos} fluxo(s) do cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)

    if status == 'erro':
        return f"Não consegui montar o fluxo no editor: {dados.get('mensagem', 'erro desconhecido')}."

    if status != 'concluido':
        return "Não consegui identificar o status da montagem. Cancelei o acompanhamento -- pode pedir de novo se quiser."

    total_processados = dados.get('total_processados', total_fluxos)
    erros = dados.get('erros', [])
    resposta = (
        f"Fluxo montado/atualizado no editor pra {total_processados} de {total_fluxos} fluxo(s) do cenário "
        f"**{numero_exibido}/{cenario_nome}**. ✅"
    )
    if erros:
        resposta += "\n\n⚠️ Alguns fluxos tiveram problema: " + "; ".join(erros)
    return resposta


# =======================================================================
# 🌟 NOVO: "Fluxos de Produção - Criar Fluxos de Produção dos Produtos" --
# lista os produtos do cenário ativo (com a qtde atual de fluxos entre
# parênteses), deixa o usuário escolher um, calcula os fluxos novos e
# compara com os existentes. Se já existir algo e for diferente, pergunta
# se quer substituir; se for igual, avisa que não vale a pena; se não
# existir nada ainda, avisa que essa parte ainda não foi desenvolvida.
# =======================================================================

def iniciar_fluxo_criar_fluxos_produto(usuario):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    from produtos.models import TbProdutos
    import uuid
    from datetime import datetime, timezone as _tz

    produtos = list(TbProdutos.objects.filter(tbcenarios_id=cenario.id).order_by('pro_codigo'))
    if not produtos:
        return f"Não encontrei nenhum produto cadastrado no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}**."

    # 🌟 NOVO: antes de listar os produtos, CALCULA (em segundo plano, no Celery) a situação dos fluxos de cada um --
    # cadastrados, corretos, a gerar e a remover --, pra o usuário poder marcar o que quer atualizar. A comparação
    # leva alguns segundos por produto (mais nos produtos com muitos fluxos), então não dá pra fazer dentro do clique.
    calculo_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'fp_sit_calculando'
    estado.dados_coletados = {
        'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'calculo_id': calculo_id,
        'produtos': [{'id': p.id, 'codigo': p.pro_codigo} for p in produtos], 'situacao': {},
        'task_ids': [task_id], 'disparado_em': datetime.now(_tz.utc).isoformat(),
    }
    estado.save()

    from fluxos.tasks import calcular_situacao_fluxos_celery
    produto_ids, usuario_id = [p.id for p in produtos], estado.usuario_id
    # on_commit: a task só começa depois de o estado acima estar gravado (ela confere o calculo_id no estado).
    transaction.on_commit(lambda: calcular_situacao_fluxos_celery.apply_async(
        (produto_ids, usuario_id, calculo_id), task_id=task_id))

    return (
        f"Vou conferir a situação dos fluxos de cada produto do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** "
        "pra você escolher o que atualizar. Pode levar alguns instantes; acompanho por aqui.\n\n"
        + _fp_texto_calculo(estado.dados_coletados)
    )


def _fp_texto_calculo(dados):
    return (f"Calculando a situação dos fluxos... {len(dados.get('situacao') or {})} de "
            f"{len(dados.get('produtos') or [])} produto(s).")


def _etapa_fp_sit_calculando(estado, texto):
    dados = estado.dados_coletados or {}
    total = len(dados.get('produtos') or [])
    feitos = len(dados.get('situacao') or {})
    if feitos >= total:
        return _fp_montar_selecao(estado, dados)
    if _equ_geracao_morreu(dados):
        _encerrar_fluxo(estado)
        return (
            f"O cálculo parou antes de terminar ({feitos} de {total} produtos) -- o processo do Celery foi encerrado "
            "(provavelmente por falta de memória). Não mexi em nenhum fluxo. Pode pedir de novo."
        )
    return _fp_texto_calculo(dados)


def _fp_montar_selecao(estado, dados):
    situacao = dados.get('situacao') or {}
    itens = []
    for produto in dados.get('produtos') or []:
        s = situacao.get(str(produto['id'])) or {}
        if s.get('erro'):
            status, motivo = 'indisponivel', s['erro']
        # Produto SEM nenhum fluxo cadastrado (ex.: o equipamento excluído estava em todos os fluxos dele) entra na
        # mesma regra dos demais: se faltam fluxos ("A gerar" > 0), dá pra marcar e gerar a partir do zero.
        elif not s.get('qtd_a_gerar') and not s.get('qtd_a_remover'):
            status, motivo = 'ok', ''
        else:
            status, motivo = 'ajustar', ''
        itens.append({
            'id': produto['id'], 'codigo': produto['codigo'], 'status': status, 'motivo': motivo,
            'qtd_existente': s.get('qtd_existente', 0), 'qtd_corretos': s.get('qtd_corretos', 0),
            'qtd_a_gerar': s.get('qtd_a_gerar', 0), 'qtd_a_remover': s.get('qtd_a_remover', 0),
        })

    dados = {**dados, 'itens': itens}
    precisam = sum(1 for i in itens if i['status'] == 'ajustar')
    texto = (
        f"Esta é a situação dos fluxos de cada produto do cenário **{dados.get('cenario_nome', '')}**: "
        "**Cadastrados** são os fluxos que o produto tem hoje, **Corretos** são os cadastrados que batem com o que eu "
        "calcularia agora, **A gerar** são os que faltam e **A remover** são os cadastrados que estão errados ou sobrando.\n\n"
    )
    if precisam == 0:
        estado.dados_coletados = dados
        _encerrar_fluxo(estado)
        return (texto + "Nenhum produto precisa de ajuste agora (ou não dá pra atualizar). Não há o que fazer.\n\n"
                + _equ_marcador('SELECAO_PRODUTOS', {'produtos': itens}))
    estado.etapa_atual = 'fp_sit_selecionar'
    estado.dados_coletados = dados
    estado.save()
    return (texto + f"**{precisam}** produto(s) precisam de ajuste. Marque os que você quer atualizar.\n\n"
            + _equ_marcador('SELECAO_PRODUTOS', {'produtos': itens}))


def _etapa_fp_sit_selecionar(estado, texto):
    dados = estado.dados_coletados or {}
    itens = {i['id']: i for i in dados.get('itens') or []}
    reapresentar = lambda: _equ_marcador('SELECAO_PRODUTOS', {'produtos': list(itens.values())})

    entrada = _equ_json(texto)
    if entrada is not None and isinstance(entrada.get('produtos'), list):
        ids = []
        for valor in entrada['produtos']:
            try:
                ids.append(int(valor))
            except (TypeError, ValueError):
                pass
    else:
        # Código digitado (um produto só), como era antes.
        achado = next((i for i in itens.values() if i['codigo'].lower() == texto.strip().lower()), None)
        if achado is None:
            return ("Não encontrei esse produto. Marque os produtos na lista, digite o código exatamente como aparece, "
                    "ou \"Cancelar\" pra desistir.\n\n" + reapresentar())
        ids = [achado['id']]
    ids = list(dict.fromkeys(ids))      # sem repetir, mantendo a ordem

    if not ids:
        return "Marque pelo menos um produto na lista.\n\n" + reapresentar()
    problemas = []
    for produto_id in ids:
        item = itens.get(produto_id)
        if item is None:
            problemas.append("- Um dos produtos marcados não existe nessa lista.")
        elif item['status'] != 'ajustar':
            problemas.append(f"- **{item['codigo']}**: " + (item['motivo'] or "já está tudo certo, não há o que atualizar."))
    if problemas:
        return ("Não dá pra atualizar estes:\n" + "\n".join(problemas)
                + "\n\nDesmarque-os e tente de novo.\n\n" + reapresentar())

    selecionados = [{
        'id': i['id'], 'codigo': i['codigo'], 'qtd_a_gerar': i['qtd_a_gerar'], 'qtd_a_remover': i['qtd_a_remover'],
    } for i in (itens[p] for p in ids)]
    estado.etapa_atual = 'fp_sit_confirmar'
    estado.dados_coletados = {**dados, 'selecionados': selecionados}
    estado.save()

    linhas = [
        f"- **{i['codigo']}**: criar {i['qtd_a_gerar']}, remover {i['qtd_a_remover']} "
        f"(hoje {itens[i['id']]['qtd_existente']} cadastrados, {itens[i['id']]['qtd_corretos']} corretos)"
        for i in selecionados[:30]
    ]
    if len(selecionados) > 30:
        linhas.append(f"- ... e mais {len(selecionados) - 30}")
    return (
        f"Vou atualizar **{len(selecionados)}** produto(s), um de cada vez, mexendo só no que está errado ou faltando:\n"
        + "\n".join(linhas)
        + f"\n\nTotal: criar **{sum(i['qtd_a_gerar'] for i in selecionados)}** e remover "
          f"**{sum(i['qtd_a_remover'] for i in selecionados)}** fluxo(s). O resto não é tocado.\n\n"
        "Quer que eu faça isso? (Sim / Não)"
    )


def _etapa_fp_sit_confirmar(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra atualizar ou \"Não\" pra deixar como está. (Sim / Não)"
    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return "Ok, não mexi em nada -- o banco continua como estava."

    dados = estado.dados_coletados or {}
    selecionados = dados.get('selecionados') or []
    estado.dados_coletados = {
        'cenario_id': dados.get('cenario_id'), 'cenario_nome': dados.get('cenario_nome'),
        'fila': [{'id': s['id'], 'codigo': s['codigo']} for s in selecionados],
        'total_produtos': len(selecionados), 'concluidos': 0, 'resultados': [],
    }
    _fp_lote_iniciar_proximo(estado)
    return (f"Atualizando os fluxos de **{len(selecionados)}** produto(s), um de cada vez, só mexendo no que está "
            "errado ou faltando. Pode demorar; acompanho por aqui.\n\n" + _fp_texto_lote(estado.dados_coletados))


# ---------------------------------------------------------------------
# Execução encadeada: um produto de cada vez (mesma ideia do clone de equipamento)
# ---------------------------------------------------------------------
def _fp_texto_lote(dados):
    return (f"Atualizando os fluxos do produto **{dados.get('produto_codigo', '')}** "
            f"({dados.get('concluidos', 0) + 1} de {dados.get('total_produtos', 0)}).")


def _fp_lote_iniciar_proximo(estado):
    """Tira o próximo produto da fila e dispara a task que já existe (gerar_fluxos_produto_celery)."""
    import uuid
    from datetime import datetime, timezone as _tz
    from fluxos.models import TbFluxoProducao
    dados = dict(estado.dados_coletados or {})
    dados['fila'] = list(dados['fila'])
    atual = dados['fila'].pop(0)
    for chave in ('status', 'mensagem', 'total_criados', 'total_removidos'):
        dados.pop(chave, None)
    task_id = str(uuid.uuid4())
    dados.update({
        'produto_id': atual['id'], 'produto_codigo': atual['codigo'], 'status': 'processando',
        'produto_antes': TbFluxoProducao.objects.filter(flu_pro_produto_id=atual['id']).count(),
        'task_ids': [task_id], 'disparado_em': datetime.now(_tz.utc).isoformat(),
    })
    estado.etapa_atual = 'fp_lote_aguardando'
    estado.dados_coletados = dados
    estado.save()

    from fluxos.tasks import gerar_fluxos_produto_celery
    produto_id, usuario_id = atual['id'], estado.usuario_id
    transaction.on_commit(lambda: gerar_fluxos_produto_celery.apply_async((produto_id, usuario_id), task_id=task_id))


def _etapa_fp_lote_aguardando(estado, texto):
    from fluxos.models import TbFluxoProducao
    dados = dict(estado.dados_coletados or {})
    status = dados.get('status')
    if status == 'processando':
        if not _equ_geracao_morreu(dados):
            return _fp_texto_lote(dados)
        status = 'erro'
        dados['mensagem'] = ("a atualização parou antes de terminar -- o processo do Celery foi encerrado "
                             "(provavelmente por falta de memória)")

    depois = TbFluxoProducao.objects.filter(flu_pro_produto_id=dados.get('produto_id')).count()
    resultado = {
        'codigo': dados.get('produto_codigo', ''), 'antes': dados.get('produto_antes', 0), 'depois': depois,
        'criados': dados.get('total_criados', 0), 'removidos': dados.get('total_removidos', 0), 'erro': None,
    }
    if status == 'erro':
        resultado['erro'] = dados.get('mensagem', 'erro desconhecido')
    elif status != 'concluido':
        resultado['erro'] = 'não recebi a resposta da atualização dos fluxos'
    dados['resultados'] = list(dados.get('resultados') or []) + [resultado]
    dados['concluidos'] = dados.get('concluidos', 0) + 1
    estado.dados_coletados = dados

    if dados.get('fila'):
        _fp_lote_iniciar_proximo(estado)
        return _fp_texto_lote(estado.dados_coletados)

    resultados = dados['resultados']
    linhas = []
    for r in resultados:
        if r['erro']:
            linhas.append(f"- **{r['codigo']}**: ⚠️ não consegui atualizar ({r['erro']})")
        else:
            linhas.append(f"- **{r['codigo']}**: {r['antes']} → {r['depois']} fluxo(s) "
                          f"(criados {r['criados']}, removidos {r['removidos']})")
    texto_final = "✅ Atualização concluída.\n\nFluxos por produto:\n" + "\n".join(linhas)
    if any(r['erro'] for r in resultados):
        texto_final += ("\n\nOs produtos com ⚠️ podem ser refeitos depois nesta mesma ação (Atualizar Fluxos de "
                        "Produção por Produto).")
    if any(not r['erro'] and r['criados'] for r in resultados):
        return _fp_io_perguntar_ou_encerrar(estado, dados.get('cenario_id'), dados.get('cenario_nome'), texto_final)
    _encerrar_fluxo(estado)
    return texto_final


def _fp_io_perguntar_ou_encerrar(estado, cenario_id, cenario_nome, texto_final):
    """
    Fim de uma geração de fluxos que CRIOU fluxos (Atualizar Fluxos por Produto e Clonar Equipamento): os fluxos novos
    nascem com o I/O desatualizado, então, em vez de só avisar, PERGUNTA se o usuário quer atualizar agora (todos os
    fluxos ativos do cenário com flu_pro_input_output_atualizado=False). Sem nenhum desatualizado: só encerra.
    """
    from fluxos.models import TbFluxoProducao
    qtd = TbFluxoProducao.objects.filter(
        tbcenarios_id=cenario_id, flu_pro_input_output_atualizado=False, flu_pro_ativo=True).count()
    if qtd == 0:
        _encerrar_fluxo(estado)
        return texto_final
    if not cenario_nome:
        cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
        cenario_nome = cenario.cen_nome if cenario is not None else ''
    # fluxo_ativo = processar: as etapas "fp_io_*" são desse fluxo (vale também quando a pergunta vem do clone).
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'fp_io_confirmar'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario_nome, 'qtd_desatualizados': qtd}
    estado.save()
    return (texto_final + f"\n\nExistem **{qtd}** fluxo(s) ativo(s) com I/O desatualizado no cenário "
            f"**{cenario_nome}** (os novos entram nessa conta). Quer que eu atualize o I/O "
            "(input/output/custos) deles agora? (Sim / Não)")


def _etapa_fp_io_confirmar(estado, texto):
    """Resposta a "quer atualizar o I/O dos fluxos desatualizados agora?". Sim = mesma rotina da ação do Admin
    "Atualizar Input/Output/Custos dos Fluxos Selecionados" (atualizar_fluxo_celery), pra TODOS os fluxos ativos do
    cenário com flu_pro_input_output_atualizado=False."""
    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra atualizar o I/O ou \"Não\" pra deixar como está. (Sim / Não)"
    dados = estado.dados_coletados or {}
    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return _FP_IO_TEXTO_NAO

    import uuid
    from datetime import datetime, timezone as _tz
    from fluxos.models import TbFluxoProducao
    # A lista é refeita AGORA (a da pergunta pode estar velha). Um cenário só: a task descobre o cenário pelo 1º fluxo.
    ids = list(TbFluxoProducao.objects.filter(
        tbcenarios_id=dados['cenario_id'], flu_pro_input_output_atualizado=False, flu_pro_ativo=True,
    ).values_list('id', flat=True))
    if not ids:
        _encerrar_fluxo(estado)
        return "Não há mais nenhum fluxo ativo com I/O desatualizado nesse cenário -- não precisei fazer nada."

    task_id = str(uuid.uuid4())
    estado.etapa_atual = 'fp_io_aguardando'
    estado.dados_coletados = {**dados, 'total': len(ids), 'task_ids': [task_id],
                              'disparado_em': datetime.now(_tz.utc).isoformat()}
    estado.save()

    from fluxos.tasks import atualizar_fluxo_celery
    transaction.on_commit(lambda: atualizar_fluxo_celery.apply_async((ids,), task_id=task_id))
    return (f"Disparei a atualização do I/O (input/output/custos) de **{len(ids)}** fluxo(s) em segundo plano. "
            "Acompanho por aqui.\n\n" + _fp_io_texto_andamento(estado.dados_coletados, len(ids)))


_FP_IO_TEXTO_NAO = ("Ok, não atualizei o I/O. Os fluxos novos continuam com o I/O desatualizado -- roda \"Atualizar "
                    "Fluxos\" (menu Cenário) antes de limpar o cenário.")


def _fp_io_texto_andamento(dados, restantes):
    total = dados.get('total', 0)
    return f"Atualizando o I/O dos fluxos... {min(max(total - restantes, 0), total)} de {total} já atualizados."


def _fp_io_status_tarefa(task_ids):
    try:
        from django_celery_results.models import TaskResult
        return TaskResult.objects.filter(task_id=task_ids[0]).values_list('status', flat=True).first() if task_ids else None
    except Exception:
        return None


def _etapa_fp_io_aguardando(estado, texto):
    from fluxos.models import TbFluxoProducao
    dados = estado.dados_coletados or {}
    nome = dados.get('cenario_nome', '')
    restantes = TbFluxoProducao.objects.filter(
        tbcenarios_id=dados.get('cenario_id'), flu_pro_input_output_atualizado=False, flu_pro_ativo=True).count()

    if _fp_io_status_tarefa(dados.get('task_ids')) == 'SUCCESS':
        _encerrar_fluxo(estado)
        resposta = f"✅ I/O (input/output/custos) atualizado em **{dados.get('total', 0)}** fluxo(s) do cenário **{nome}**."
        if restantes:
            resposta += (f"\n\nAinda há **{restantes}** fluxo(s) ativo(s) com I/O marcado como desatualizado -- "
                         "confere no Admin.")
        return resposta
    if _equ_geracao_morreu(dados):
        _encerrar_fluxo(estado)
        return (f"A atualização do I/O parou antes de terminar -- o processo do Celery foi encerrado (provavelmente por "
                f"falta de memória). Faltam **{restantes}** fluxo(s) ativo(s) com I/O desatualizado no cenário "
                f"**{nome}**; pode pedir de novo em \"Atualizar Fluxos\" (menu Cenário).")
    return _fp_io_texto_andamento(dados, restantes)


# ---------------------------------------------------------------------
# 🌟 NOVO: "Fluxos de Produção - Comparar Fluxos de Produção" (só consulta: não altera nada)
#
# Escolhe o produto, depois acha o 1º e o 2º fluxo digitando parte da descrição (ou o id) -- um produto tem milhares de
# fluxos --, e mostra as LIGAÇÕES (consumos padrão from -> to) que diferem entre os dois, com o indicador cadastrado
# (período a período) e o custo variável adicionado de cada ligação. A lógica fica em fluxos/comparar_fluxos.py.
# ---------------------------------------------------------------------
def _cmp_produtos_com_fluxos(cenario_id, excluir_fluxo_id=None):
    """[(produto, qtd_de_fluxos)] dos produtos do cenário que têm pelo menos 1 fluxo -- sem contar o fluxo já escolhido
    (um produto cujo ÚNICO fluxo é o já escolhido não tem com o que ser comparado)."""
    from produtos.models import TbProdutos
    from fluxos.models import TbFluxoProducao
    from django.db.models import Count
    fluxos = TbFluxoProducao.objects.filter(tbcenarios_id=cenario_id)
    if excluir_fluxo_id is not None:
        fluxos = fluxos.exclude(id=excluir_fluxo_id)
    contagem = {i['flu_pro_produto_id']: i['qtd'] for i in
                fluxos.order_by().values('flu_pro_produto_id').annotate(qtd=Count('id'))}
    return [(p, contagem[p.id]) for p in TbProdutos.objects.filter(tbcenarios_id=cenario_id).order_by('pro_codigo')
            if contagem.get(p.id, 0) >= 1]


def _cmp_texto_pedir_produto(dados):
    produtos = _cmp_produtos_com_fluxos(dados['cenario_id'], dados.get('fluxo_a_id') if dados.get('alvo') == 'b' else None)
    itens_marcador = "|".join(f"{p.pro_codigo}={qtd}" for p, qtd in produtos)
    if dados.get('alvo') == 'b':
        cabecalho = (f"Primeiro fluxo: **id {dados['fluxo_a_id']}** (produto {dados['fluxo_a_produto']}) — "
                     f"{dados['fluxo_a_descricao']}\n\nDe qual produto é o **segundo** fluxo? Pode ser o mesmo produto ou "
                     "outro. ")
    else:
        cabecalho = ("De qual produto é o **primeiro** fluxo? A comparação pode ser entre fluxos do mesmo produto ou de "
                     "produtos diferentes. ")
    return (cabecalho + "O número ao lado do código é a quantidade de fluxos do produto (só aparecem produtos que "
            "têm fluxos).\n\n" + f"[LISTA_PRODUTOS:{itens_marcador}]")


def iniciar_fluxo_comparar_fluxos(usuario):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."
    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    produtos = _cmp_produtos_com_fluxos(cenario.id)
    if sum(qtd for _, qtd in produtos) < 2:
        return (f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** tem menos de 2 fluxos de produção, "
                "então não há o que comparar.")

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'cmp_produto'
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'alvo': 'a'}
    estado.save()
    return _cmp_texto_pedir_produto(estado.dados_coletados)


def _cmp_texto_lista_fluxos(dados, filtro='', aviso=''):
    """A lista de fluxos do produto escolhido: a TELA mostra id e descrição, em ordem alfabética, com rolagem e um filtro."""
    from fluxos.comparar_fluxos import listar_fluxos, LIMITE_LISTA_FLUXOS
    excluir = dados.get('fluxo_a_id') if dados.get('alvo') == 'b' else None
    itens, total = listar_fluxos(dados['cenario_id'], dados['produto_id'], filtro, excluir_id=excluir)
    produto = dados.get('produto_codigo', '')
    if dados.get('alvo', 'a') == 'a':
        cabecalho = f"Escolha o **primeiro** fluxo do produto **{produto}** na lista abaixo (em ordem alfabética da descrição). "
    else:
        cabecalho = (f"Primeiro fluxo: **id {dados['fluxo_a_id']}** (produto {dados['fluxo_a_produto']}) — {dados['fluxo_a_descricao']}\n\n"
                     f"Agora escolha o **segundo** fluxo, do produto **{produto}**, na lista abaixo (em ordem alfabética da descrição). ")
    dica = ("Clique no fluxo. Se a lista for grande, use o campo de filtro (palavras da descrição, por exemplo os códigos de equipamento "
            "que ele usa) ou digite o id do fluxo. Se quiser desistir, digite \"cancelar\".")
    return aviso + cabecalho + dica + "\n\n" + _equ_marcador('LISTA_FLUXOS', {
        'titulo': f"Fluxos do produto {produto}", 'alvo': dados.get('alvo', 'a'), 'fluxos': itens, 'total': total,
        'mostrando': len(itens), 'filtro': filtro.strip(), 'limite': LIMITE_LISTA_FLUXOS})


def _etapa_cmp_produto(estado, texto):
    dados = estado.dados_coletados or {}
    excluir = dados.get('fluxo_a_id') if dados.get('alvo') == 'b' else None
    validos = {p.pro_codigo.lower(): p for p, _ in _cmp_produtos_com_fluxos(dados.get('cenario_id'), excluir)}
    produto = validos.get(texto.strip().lower())
    if produto is None:
        return ("Não encontrei esse produto entre os que têm fluxos disponíveis. Clique em um dos produtos da lista, "
                "ou digite o código exatamente como aparece (ou \"cancelar\" pra desistir).")
    estado.etapa_atual = 'cmp_fluxo'
    estado.dados_coletados = {**dados, 'produto_id': produto.id, 'produto_codigo': produto.pro_codigo}
    estado.save()
    return _cmp_texto_lista_fluxos(estado.dados_coletados)


def _etapa_cmp_fluxo(estado, texto):
    """Clicar num fluxo da lista envia o id dele. Qualquer outro texto é um FILTRO (palavras da descrição)."""
    from fluxos.models import TbFluxoProducao
    dados = estado.dados_coletados or {}
    excluir = dados.get('fluxo_a_id') if dados.get('alvo') == 'b' else None
    t = texto.strip()
    if t.isdigit():
        fluxo = (TbFluxoProducao.objects.filter(id=int(t), tbcenarios_id=dados['cenario_id'], flu_pro_produto_id=dados['produto_id'])
                 .exclude(id=excluir or 0).first())
        if fluxo is not None:
            return _cmp_fluxo_escolhido(estado, dados, {'id': fluxo.id, 'descricao': fluxo.flu_pro_descricao})
        return _cmp_texto_lista_fluxos(dados, aviso=f"Não há fluxo com o id **{t}** entre os fluxos desse produto. ")
    from fluxos.comparar_fluxos import listar_fluxos
    _itens, total = listar_fluxos(dados['cenario_id'], dados['produto_id'], t, excluir_id=excluir)
    if total == 0:
        return _cmp_texto_lista_fluxos(dados, aviso=f"Não achei nenhum fluxo com **{t}**; mostrei a lista completa de novo. ")
    return _cmp_texto_lista_fluxos(dados, filtro=t)


def _cmp_fluxo_escolhido(estado, dados, item):
    if dados.get('alvo') == 'a':
        estado.etapa_atual = 'cmp_produto'
        estado.dados_coletados = {**{k: v for k, v in dados.items() if k not in ('candidatos', 'produto_id', 'produto_codigo')},
                                  'alvo': 'b', 'fluxo_a_id': item['id'], 'fluxo_a_descricao': item['descricao'],
                                  'fluxo_a_produto': dados.get('produto_codigo', '')}
        estado.save()
        return _cmp_texto_pedir_produto(estado.dados_coletados)
    return _cmp_executar(estado, dados, item)


def _cmp_moeda(estado):
    try:
        from parameters.models import TbEmpresa
        perfil = getattr(estado.usuario, 'perfilusuario', None)
        empresa = TbEmpresa.objects.filter(id=perfil.empresa_efetiva_id()).first() if perfil else None
        return {'BRL': 'R$ ', 'USD': 'US$ ', 'EUR': '€ '}.get(getattr(empresa, 'emp_moeda', None), '')
    except Exception:
        return ''


def _cmp_executar(estado, dados, fluxo_b):
    from fluxos.comparar_fluxos import comparar_custo_fluxos, dados_para_tabela_custo, ComparacaoError
    cenario = TbCenarios.objects_real.filter(id=dados['cenario_id']).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return "O cenário dessa comparação não existe mais."
    try:
        resultado = comparar_custo_fluxos(dados['fluxo_a_id'], fluxo_b['id'], cenario)
    except ComparacaoError as erro:
        _encerrar_fluxo(estado)
        return f"Não consegui comparar: {erro}"
    moeda = _cmp_moeda(estado)
    _encerrar_fluxo(estado)
    # 🌟 NOVO: a comparação explica a diferença de CUSTO do fluxo (que sai do I/O: output real x custo variável adicionado da
    # ordem), do último equipamento até o primeiro. A tela desenha a tabela (com o botão Detalhar) a partir do marcador abaixo;
    # o texto fica curto: título, contagens e o custo total de cada fluxo.
    tabela = dados_para_tabela_custo(resultado, moeda)
    r, t = tabela['resumo'], tabela['total']
    texto = (f"**{tabela['titulo']}**\n\nEquipamentos: **{r['equipamentos']}** · iguais: **{r['iguais']}** · "
             f"com ordem diferente: **{r['ordem_diferente']}** · com custo diferente: **{r['custo_diferente']}** · "
             f"só no A: **{r['so_a']}** · só no B: **{r['so_b']}**")
    if t['delta'] is not None:
        texto += (f"\n\nCusto do fluxo (média dos períodos): A **{moeda}{t['a']}** · B **{moeda}{t['b']}** · "
                  f"diferença (B − A) **{t['delta']}**")
    if r['iguais'] == r['equipamentos']:
        texto += "\n\nOs dois fluxos usam as mesmas ordens e têm o mesmo custo em todos os equipamentos."
    return texto + "\n\n" + _equ_marcador('COMPARACAO_FLUXOS', tabela)


def _etapa_fp_criar_fluxos_produto_escolher(estado, texto):
    dados = estado.dados_coletados or {}
    cenario_id = dados.get('cenario_id')

    from produtos.models import TbProdutos
    produto = TbProdutos.objects.filter(tbcenarios_id=cenario_id, pro_codigo__iexact=texto.strip()).first()
    if produto is None:
        return (
            "Não encontrei esse produto no cenário ativo. Digita o código exatamente como apareceu na "
            "lista, ou \"Cancelar\" pra desistir."
        )

    from fluxos.gerar_fluxos_por_produto import comparar_fluxos_existentes, GeracaoFluxoError

    try:
        comparacao = comparar_fluxos_existentes(produto.id)
    except GeracaoFluxoError as erro:
        _encerrar_fluxo(estado)
        return f"Não consegui calcular os fluxos do produto **{produto.pro_codigo}**: {erro}"

    if comparacao['qtd_existente'] == 0:
        _encerrar_fluxo(estado)
        return (
            f"O produto **{produto.pro_codigo}** ainda não tem nenhum fluxo de produção cadastrado. "
            "Gerar do zero (sem nada existente pra comparar) é uma etapa que ainda vamos desenvolver "
            "depois -- por enquanto, não fiz nada."
        )

    if not comparacao['mudou']:
        _encerrar_fluxo(estado)
        return (
            f"O produto **{produto.pro_codigo}** já tem **{comparacao['qtd_existente']}** fluxo(s), e são "
            "exatamente os mesmos que eu geraria agora -- não vale a pena mexer em nada. Não fiz nada."
        )

    # 🌟 NOVO: em vez de "apagar tudo e gravar tudo", agora só mexe na
    # DIFERENÇA -- o que está sobrando/errado é removido, o que está
    # faltando é criado, o resto (já certo) fica intocado.
    qtd_remover = len(comparacao['mae_ids_a_remover'])
    qtd_criar = len(comparacao['planos_a_criar'])

    estado.etapa_atual = 'fp_criar_fluxos_produto_confirmar'
    estado.dados_coletados = {**dados, 'produto_id': produto.id, 'produto_codigo': produto.pro_codigo}
    estado.save()

    return (
        f"O produto **{produto.pro_codigo}** já tem **{comparacao['qtd_existente']}** fluxo(s) cadastrado(s), "
        f"dos quais **{comparacao['qtd_iguais']}** já batem certinho com o que eu calcularia agora.\n\n"
        f"Pra deixar tudo certo, eu precisaria **remover {qtd_remover}** fluxo(s) que estão errados/sobrando "
        f"e **criar {qtd_criar}** que estão faltando -- o resto não seria tocado.\n\n"
        "Quer que eu faça isso? (Sim / Não)"
    )


def _etapa_fp_criar_fluxos_produto_confirmar(estado, texto):
    dados = estado.dados_coletados or {}
    produto_id = dados.get('produto_id')
    produto_codigo = dados.get('produto_codigo', '')

    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra substituir ou \"Não\" pra deixar como está."

    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return "Ok, não mexi em nada -- o banco continua como estava."

    from fluxos.tasks import gerar_fluxos_produto_celery

    estado.etapa_atual = 'fp_criar_fluxos_produto_aguardando'
    estado.dados_coletados = {**dados, 'status': 'processando'}
    estado.save()

    gerar_fluxos_produto_celery.delay(produto_id, estado.usuario_id)

    return (
        f"Sincronizando os fluxos do produto **{produto_codigo}** em segundo plano -- só mexendo no que "
        "está errado/faltando, o resto fica como está."
    )


def _etapa_fp_criar_fluxos_produto_aguardando(estado, texto):
    dados = estado.dados_coletados or {}
    produto_codigo = dados.get('produto_codigo', '')
    status = dados.get('status')

    if status == 'processando':
        return f"Ainda sincronizando os fluxos do produto **{produto_codigo}**."

    _encerrar_fluxo(estado)

    if status == 'erro':
        return f"Não consegui sincronizar os fluxos do produto **{produto_codigo}**: {dados.get('mensagem', 'erro desconhecido')}."

    if status != 'concluido':
        return "Não consegui identificar o status da sincronização. Cancelei o acompanhamento -- pode pedir de novo se quiser."

    total_removidos = dados.get('total_removidos', 0)
    total_criados = dados.get('total_criados', 0)
    return (
        f"✅ Pronto -- produto **{produto_codigo}**: **{total_removidos}** fluxo(s) removido(s) e "
        f"**{total_criados}** criado(s). O resto não foi tocado."
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


def existe_arquivo_atualizacao(usuario):
    """
    🌟 NOVO: diz se existe um arquivo no espaço único de atualização
    (ArquivoAtualizacaoAgente) pra empresa efetiva do usuário -- usado em
    agents.py como "porta de entrada" pras checagens de planilha
    reenviada/Custo Ferbasa, evitando rodar essas checagens (e suas
    consultas ao banco) em toda mensagem à toa quando não há nenhum
    arquivo esperando ser processado.
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None:
        return False
    empresa_id = perfil.empresa_efetiva_id()
    if empresa_id is None:
        return False
    from .models import ArquivoAtualizacaoAgente
    return ArquivoAtualizacaoAgente.objects.filter(empresa_id=empresa_id).exists()


def identificar_tipo_planilha_reenviada(usuario):
    """
    Olha o arquivo no espaço ÚNICO de atualização (ArquivoAtualizacaoAgente)
    e devolve 'ind' ou 'cam' se o nome bater com nosso padrão, pro cenário
    ativo do usuário. Usado no roteamento em agents.py -- permite
    reconhecer o reenvio mesmo se a mensagem do usuário não mencionar
    explicitamente "indicador"/"câmbio" (o nome do arquivo já basta).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return None

    empresa_id_usuario = perfil.empresa_efetiva_id()
    if empresa_id_usuario is None:
        return None

    import os
    from .models import ArquivoAtualizacaoAgente
    arquivo = ArquivoAtualizacaoAgente.objects.filter(empresa_id=empresa_id_usuario).order_by('-id').first()
    if not arquivo or not arquivo.arquivo or not arquivo.arquivo.name.lower().endswith('.xlsx'):
        return None
    nome_arquivo = os.path.basename(arquivo.arquivo.name)
    if _identificar_planilha_por_nome(nome_arquivo, 'ind', perfil.cenario_ativo_id):
        return 'ind'
    if _identificar_planilha_por_nome(nome_arquivo, 'cam', perfil.cenario_ativo_id):
        return 'cam'
    return None


def _descartar_arquivo_atualizacao(arquivo_id):
    """
    Remove o registro do espaço único de atualização
    (ArquivoAtualizacaoAgente) e o arquivo em disco -- chamado sempre que
    um arquivo JÁ FOI LIDO/PROCESSADO, independente do resultado
    (aplicado, recusado, sem mudanças, ou erro de leitura). Não faz
    sentido deixá-lo ocupando o espaço depois -- ele já cumpriu seu papel.
    """
    try:
        from .models import ArquivoAtualizacaoAgente
        import os
        arquivo = ArquivoAtualizacaoAgente.objects.filter(id=arquivo_id).first()
        if arquivo and arquivo.arquivo:
            caminho = arquivo.arquivo.path
            arquivo.delete()
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
    """
    🌟 CORRIGIDO: removido o marcador [LISTA_PERIODOS:...] que eu tinha
    adicionado -- ele estava DUPLICANDO os botões de período, porque o
    reconhecedor genérico de opções clicáveis (views.py,
    _extrair_opcoes_clicaveis) já transforma esse formato de texto
    ("Períodos e valores atuais:\\n- X: Y") em botões sozinho, sem
    precisar de nenhum marcador extra.
    """
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
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        "(ou \"Cancelar\")"
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
            "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
            "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
            "pelo arquivo, não precisa repetir o nome do indicador. Ou, se mudou de ideia, "
            "manda \"Cancelar\"."
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
            f"Clica no período que quer mudar, ou digita (formato {_exemplo_periodo(cenario)})."
        )

    return "Não entendi. Digita **Manual** ou **Planilha** (ou \"Cancelar\")."


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
            "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
            "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente pelo "
            "arquivo, não precisa repetir o nome do indicador. Encerrei esse fluxo aqui, pra sua próxima "
            "mensagem já ser reconhecida certinho. Ou, se mudou de ideia, manda \"Cancelar\"."
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
        return f"O período {periodo} está fora do intervalo do cenário. Informa outro período, ou \"Cancelar\"."

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
        return "Não entendi o valor. Informa um número (ex: 5.5), ou \"Cancelar\"."

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
        "Confirma a mudança? (Sim / Não)"
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
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        "Isso apaga o indicador E todos os valores dele, em todos os períodos -- não tem como desfazer. (Sim / Não)"
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
        return f"Já existe um indicador chamado **{nome}** nesse cenário. Escolhe outro nome, ou \"Cancelar\"."

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
        return "Não entendi o valor. Informa um número (ex: 5.5), ou \"Cancelar\"."

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
        "Confirma a criação? (Sim / Não)"
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
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        return "Não entendi o percentual. Informa um número (ex: 5 ou -3.5), ou \"Cancelar\"."

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
        "Confirma a aplicação? (Sim / Não)"
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
        "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
        "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
        "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
        "pelo arquivo, não precisa repetir o nome do indicador. Ou, se mudou de ideia, "
        "manda \"Cancelar\"."
    )


def _processar_planilha_indicador(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    import os
    from .models import ArquivoAtualizacaoAgente
    from tabelas.models import TbIndicadores

    # 🌟 CORRIGIDO: agora lê do espaço ÚNICO de atualização
    # (ArquivoAtualizacaoAgente), não mais da lista de Relatórios com
    # pdf_ids marcados -- em vez de exigir que o usuário repita o nome
    # do indicador na mensagem, identifica automaticamente pelo nome do
    # arquivo (que já carrega o id do indicador, gravado por
    # _gerar_planilha_periodos). Só cai pra busca por nome na mensagem
    # se o arquivo não tiver esse padrão (por exemplo, foi renomeado).
    empresa_id_usuario = perfil.empresa_efetiva_id()
    arquivo = ArquivoAtualizacaoAgente.objects.filter(empresa_id=empresa_id_usuario).order_by('-id').first()
    indicador = None
    if arquivo and arquivo.arquivo and arquivo.arquivo.name.lower().endswith('.xlsx'):
        nome_arquivo = os.path.basename(arquivo.arquivo.name)
        indicador_id = _identificar_planilha_por_nome(nome_arquivo, 'ind', cenario.id)
        if indicador_id:
            indicador = TbIndicadores.objects.filter(id=indicador_id, tbcenarios_id=cenario.id).first()

    if indicador is None:
        indicador = _buscar_indicador_na_mensagem(cenario.id, mensagem)

    if indicador is None:
        return f"Não consegui identificar qual indicador essa planilha é. Me diz o nome dele na mensagem.\n\n{_lista_indicadores(cenario.id)}"
    if arquivo is None:
        return "Não encontrei nenhum arquivo no espaço de atualização. Solta a planilha ali e tenta de novo."

    linhas = _ler_planilha_periodos(arquivo.arquivo.path)
    if not linhas:
        _descartar_arquivo_atualizacao(arquivo.id)
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
        _descartar_arquivo_atualizacao(arquivo.id)
        return f"Não encontrei nenhuma mudança de valor nessa planilha, comparado ao que já está salvo.{aviso_erros}"

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_INDICADORES
    estado.etapa_atual = 'ind_planilha_confirmar'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'indicador_id': indicador.id,
        'indicador_nome': indicador.ind_nome,
        'relatorio_id': arquivo.id,
        'mudancas': mudancas,
    }
    estado.save()

    linhas_resumo = "\n".join(f"- {p}: {antigo} → {novo}" for _, p, antigo, novo in mudancas)
    return (
        f"Encontrei {len(mudancas)} mudança(s) pro indicador **{indicador.ind_nome}**:\n"
        f"{linhas_resumo}"
        f"{aviso_erros}\n\n"
        "Confirma a aplicação? (Sim / Não)"
    )


def _etapa_ind_planilha_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    if resposta not in ('sim', 's', 'yes', 'y'):
        _descartar_arquivo_atualizacao(dados['relatorio_id'])
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

    # 🌟 CORRIGIDO: move a planilha enviada pro campo "Fonte" do
    # indicador, e apaga o registro do espaço único de atualização
    # (ArquivoAtualizacaoAgente) junto com o arquivo em disco -- ela não
    # precisa mais ocupar esse espaço, agora vive oficialmente dentro do
    # próprio indicador.
    aviso_fonte = ""
    try:
        from .models import ArquivoAtualizacaoAgente
        arquivo = ArquivoAtualizacaoAgente.objects.filter(id=dados['relatorio_id']).first()
        indicador = TbIndicadores.objects.filter(id=dados['indicador_id']).first()
        if arquivo and indicador and arquivo.arquivo:
            from django.core.files.base import ContentFile
            import os
            arquivo.arquivo.open('rb')
            conteudo = arquivo.arquivo.read()
            arquivo.arquivo.close()
            nome_arquivo = arquivo.arquivo.name.split('/')[-1]
            indicador.ind_fonte.save(nome_arquivo, ContentFile(conteudo), save=True)

            caminho_antigo = arquivo.arquivo.path
            arquivo.delete()
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
        return f"Não encontrei nenhum indicador chamado \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

    estado.dados_coletados['indicador_id'] = indicador.id
    estado.dados_coletados['indicador_nome'] = indicador.ind_nome
    estado.etapa_atual = 'ind_grafico_tipo'
    estado.save()
    return (
        f"Indicador **{indicador.ind_nome}**.\n\n"
        "Que tipo de gráfico você quer? Escolha:\n\n"
        "- **Gráfico de Linha** -- pra ver a evolução ao longo do tempo\n"
        "- **Gráfico de Barra** -- pra comparar os valores de cada período\n\n"
        "(ou \"Cancelar\")"
    )


def _etapa_ind_grafico_tipo(estado, texto, cenario):
    escolha = texto.strip().lower()
    if 'barra' in escolha:
        tipo_grafico = 'bar'
    elif 'linha' in escolha:
        tipo_grafico = 'line'
    else:
        return "Não entendi. Escolhe **Gráfico de Linha** ou **Gráfico de Barra** (ou \"Cancelar\")."

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
            "Qual vai ser o **nome** do indicador? (ou \"Cancelar\" para desistir)"
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
        "Qual você quer cadastrar? (ou \"Cancelar\")"
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
            "Qual **taxa de câmbio** você quer plotar? (ou \"Cancelar\")"
        )
    elif acao == 'criar':
        return _iniciar_criacao_cambio(estado, cenario)
    elif acao == 'massa':
        estado.etapa_atual = 'cam_massa_moeda'
        estado.save()
        return (
            f"Vamos aplicar um reajuste em massa no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer reajustar? (ou \"Cancelar\")"
        )
    elif acao == 'eliminar':
        estado.etapa_atual = 'cam_eliminar_moeda'
        estado.save()
        return (
            f"Vamos eliminar uma taxa de câmbio do cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual você quer eliminar? (ou \"Cancelar\")"
        )
    else:
        estado.etapa_atual = 'cam_editar_moeda'
        estado.save()
        return (
            f"Vamos mudar um valor de câmbio no cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** 💱\n\n"
            f"{_lista_cambios(cenario.id)}"
            "Qual **taxa de câmbio** você quer mudar? (ou \"Cancelar\")"
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


# 🌟 NOVO: apelidos em português (informal, sem exigir a sigla) pras
# moedas mais comuns -- "dólar", "euro", "real" e variações (plural,
# sem acento) além do código técnico (USD/EUR/BRL) que já funcionava.
# Sem isso, só quem digitasse a sigla exata era reconhecido.
_APELIDOS_MOEDA = {
    'USD': ('dolar', 'dolares', 'dolar americano'),
    'EUR': ('euro', 'euros'),
    'BRL': ('real', 'reais'),
}


def _codigo_moeda_por_apelido(texto):
    """
    Devolve o código (USD/EUR/BRL) se o texto contiver um apelido comum
    em português dessa moeda -- ignora acentos (compara sem eles) pra
    aceitar tanto "dólar" quanto "dolar". Devolve None se não achar.
    """
    import unicodedata
    texto_sem_acento = ''.join(
        c for c in unicodedata.normalize('NFD', texto.lower()) if unicodedata.category(c) != 'Mn'
    )
    for codigo, apelidos in _APELIDOS_MOEDA.items():
        for apelido in apelidos:
            if apelido in texto_sem_acento:
                return codigo
    return None


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
    # 🌟 NOVO: tenta pelo apelido em português (dólar, euro, real)
    codigo_apelido = _codigo_moeda_por_apelido(texto)
    if codigo_apelido:
        for c in cambios:
            if c.cam_moeda == codigo_apelido:
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
    if candidatos:
        candidatos.sort(key=lambda c: len(c.get_cam_moeda_display()), reverse=True)
        return candidatos[0]
    # 🌟 NOVO: nada bateu pelo código/nome cadastrado -- tenta pelo
    # apelido em português (dólar, euro, real) mencionado na mensagem.
    codigo_apelido = _codigo_moeda_por_apelido(mensagem or "")
    if codigo_apelido:
        return TbCambio.objects.filter(tbcenarios_id=cenario_id, cam_moeda=codigo_apelido).first()
    return None


def _moedas_disponiveis(cenario_id):
    """Moedas que o model permite (cam_moeda_choice, calculado a partir da
    moeda da empresa) e que AINDA não foram cadastradas nesse cenário."""
    from tabelas.models import TbCambio
    todas = dict(TbCambio._meta.get_field('cam_moeda').choices or [])
    usadas = set(TbCambio.objects.filter(tbcenarios_id=cenario_id).values_list('cam_moeda', flat=True))
    return {codigo: nome for codigo, nome in todas.items() if codigo not in usadas}


def _lista_periodos_cambio(cenario, cambio):
    """🌟 CORRIGIDO: mesma reversão de _lista_periodos_indicador (marcador era redundante)."""
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
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        "(ou \"Cancelar\")"
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
            "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
            "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
            "pelo arquivo, não precisa repetir a moeda. Ou, se mudou de ideia, manda \"Cancelar\"."
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
            f"Clica no período que quer mudar, ou digita (formato {_exemplo_periodo(cenario)})."
        )

    return "Não entendi. Digita **Manual** ou **Planilha** (ou \"Cancelar\")."


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
            "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
            "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
            "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente pelo "
            "arquivo, não precisa repetir a moeda. Encerrei esse fluxo aqui, pra sua próxima "
            "mensagem já ser reconhecida certinho. Ou, se mudou de ideia, manda \"Cancelar\"."
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
        return f"O período {periodo} está fora do intervalo do cenário. Informa outro período, ou \"Cancelar\"."

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
        return "Não entendi o valor. Informa um número (ex: 5.20), ou \"Cancelar\"."

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
        "Confirma a mudança? (Sim / Não)"
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
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        "Isso apaga o câmbio E todos os valores dele, em todos os períodos -- não tem como desfazer. (Sim / Não)"
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
        return f"Não entendi. Escolhe uma dessas: {opcoes}, ou \"Cancelar\"."

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
        return "Não entendi o valor. Informa um número (ex: 5.20), ou \"Cancelar\"."

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
        "Confirma a criação? (Sim / Não)"
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
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

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
        return "Não entendi o percentual. Informa um número (ex: 5 ou -3.5), ou \"Cancelar\"."

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
        "Confirma a aplicação? (Sim / Não)"
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
        "Edita os valores que quiser (sem mudar a coluna Período), salva, e solta no espaço de "
        "atualização (arrastar-e-soltar, logo abaixo dos Relatórios). Clica no botão abaixo "
        "quando terminar (ou manda qualquer mensagem) -- eu identifico automaticamente "
        "pelo arquivo, não precisa repetir a moeda. Ou, se mudou de ideia, manda \"Cancelar\"."
    )


def _processar_planilha_cambio(usuario, mensagem):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    import os
    from .models import ArquivoAtualizacaoAgente
    from tabelas.models import TbCambio

    # 🌟 CORRIGIDO: agora lê do espaço ÚNICO de atualização
    # (ArquivoAtualizacaoAgente), mesma mudança feita na versão de
    # indicador.
    empresa_id_usuario = perfil.empresa_efetiva_id()
    arquivo = ArquivoAtualizacaoAgente.objects.filter(empresa_id=empresa_id_usuario).order_by('-id').first()
    cambio = None
    if arquivo and arquivo.arquivo and arquivo.arquivo.name.lower().endswith('.xlsx'):
        nome_arquivo = os.path.basename(arquivo.arquivo.name)
        cambio_id = _identificar_planilha_por_nome(nome_arquivo, 'cam', cenario.id)
        if cambio_id:
            cambio = TbCambio.objects.filter(id=cambio_id, tbcenarios_id=cenario.id).first()

    if cambio is None:
        cambio = _buscar_cambio_na_mensagem(cenario.id, mensagem)

    if cambio is None:
        return f"Não consegui identificar qual taxa de câmbio essa planilha é. Me diz a moeda na mensagem.\n\n{_lista_cambios(cenario.id)}"
    if arquivo is None:
        return "Não encontrei nenhum arquivo no espaço de atualização. Solta a planilha ali e tenta de novo."

    linhas = _ler_planilha_periodos(arquivo.arquivo.path)
    if not linhas:
        _descartar_arquivo_atualizacao(arquivo.id)
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
        _descartar_arquivo_atualizacao(arquivo.id)
        return f"Não encontrei nenhuma mudança de valor nessa planilha, comparado ao que já está salvo.{aviso_erros}"

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CAMBIO
    estado.etapa_atual = 'cam_planilha_confirmar'
    estado.dados_coletados = {
        'cenario_id': cenario.id,
        'cambio_id': cambio.id,
        'cambio_nome': cambio.get_cam_moeda_display(),
        'relatorio_id': arquivo.id,
        'mudancas': mudancas,
    }
    estado.save()

    linhas_resumo = "\n".join(f"- {p}: {antigo} → {novo}" for _, p, antigo, novo in mudancas)
    return (
        f"Encontrei {len(mudancas)} mudança(s) pra taxa de câmbio **{cambio.get_cam_moeda_display()}**:\n"
        f"{linhas_resumo}"
        f"{aviso_erros}\n\n"
        "Confirma a aplicação? (Sim / Não)"
    )


def _etapa_cam_planilha_confirmar(estado, texto, cenario):
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    if resposta not in ('sim', 's', 'yes', 'y'):
        _descartar_arquivo_atualizacao(dados['relatorio_id'])
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
        from .models import ArquivoAtualizacaoAgente
        arquivo = ArquivoAtualizacaoAgente.objects.filter(id=dados['relatorio_id']).first()
        cambio = TbCambio.objects.filter(id=dados['cambio_id']).first()
        if arquivo and cambio and arquivo.arquivo:
            from django.core.files.base import ContentFile
            import os
            arquivo.arquivo.open('rb')
            conteudo = arquivo.arquivo.read()
            arquivo.arquivo.close()
            nome_arquivo = arquivo.arquivo.name.split('/')[-1]
            cambio.cam_fonte.save(nome_arquivo, ContentFile(conteudo), save=True)

            caminho_antigo = arquivo.arquivo.path
            arquivo.delete()
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
        return f"Não encontrei nenhuma taxa de câmbio \"{texto}\" nesse cenário. Tenta de novo, ou \"Cancelar\"."

    estado.dados_coletados['cambio_id'] = cambio.id
    estado.dados_coletados['cambio_nome'] = cambio.get_cam_moeda_display()
    estado.etapa_atual = 'cam_grafico_tipo'
    estado.save()
    return (
        f"Taxa de câmbio **{cambio.get_cam_moeda_display()}**.\n\n"
        "Que tipo de gráfico você quer? Escolha:\n\n"
        "- **Gráfico de Linha** -- pra ver a evolução ao longo do tempo\n"
        "- **Gráfico de Barra** -- pra comparar os valores de cada período\n\n"
        "(ou \"Cancelar\")"
    )


def _etapa_cam_grafico_tipo(estado, texto, cenario):
    escolha = texto.strip().lower()
    if 'barra' in escolha:
        tipo_grafico = 'bar'
    elif 'linha' in escolha:
        tipo_grafico = 'line'
    else:
        return "Não entendi. Escolhe **Gráfico de Linha** ou **Gráfico de Barra** (ou \"Cancelar\")."

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
    # 🌟 NOVO: 0 sinaliza que uma tabela que alimenta o cálculo mudou
    # depois do último processamento (ver parameters/signals.py) --
    # antes caía em "Desconhecido (flag=0)" por não estar nesse dict.
    0: 'ALTERADO',
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
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **otimização**? (Sim / Não)"

    if cenario.flag == 3:  # OTIMIZADO
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_otimizacao_consolidar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **consolidação**? (Sim / Não)"

    if cenario.flag == 1 or cenario.flag is None:  # CONSOLIDADO ou nunca processado
        estado = _get_estado(usuario)
        estado.fluxo_ativo = FLUXO_PROCESSAR
        estado.etapa_atual = 'proc_pos_consolidacao_limpar'
        estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
        estado.save()
        return f"O cenário **{cenario.numero_sequencial}/{cenario.cen_nome}** está: **{status_atual}**. Quer que eu já dispare a **limpeza** (pra começar o ciclo de novo)? (Sim / Não)"

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


def _disparar_ciclo_completo(usuario, cenario, sem_checar_em_andamento=False):
    from fluxos.models import TbFluxoProducaoDaugther01
    from django.db import connection
    from .tasks import limpar_cenario_celery

    cenario_id = cenario.id

    if not sem_checar_em_andamento:
        pergunta_interromper = _perguntar_interromper_ou_encaminhar(usuario, cenario, 'ciclo_completo')
        if pergunta_interromper is not None:
            return pergunta_interromper

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    resposta_fluxos = _perguntar_atualizar_fluxos_ou_encaminhar(usuario, cenario, 'ciclo_completo')
    if resposta_fluxos is not None:
        return resposta_fluxos

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
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
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
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
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
    consolidar/atualizar fluxos. Devolve None se a mensagem não bater
    com nenhuma das 4.
    """
    texto = (mensagem or '').lower()
    # 🌟 NOVO: precisa checar "atualizar fluxo" ANTES de "limpar" -- não
    # tem sobreposição de palavra entre os dois, mas mantém a ordem
    # consistente com a do menu (Atualizar Fluxos aparece antes de
    # Limpar). Exige a palavra "fluxo" junto com "atualizar", pra não
    # confundir com outras ações de "atualizar" do sistema (atualizar
    # indicador, atualizar consumo específico, etc.).
    if re.search(r'atualiz[ae]r?.*fluxo', texto):
        return 'atualizar_fluxos'
    elif re.search(r'limp[ae]r?', texto):
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
        return "Não entendi se você quer **atualizar fluxos**, **limpar**, **otimizar**, ou **consolidar** o cenário ativo. Pode repetir dizendo qual dessas ações?"

    # 🌟 CORRIGIDO: a trava de "já tem operação em andamento" NÃO deve
    # valer pra "limpar" -- limpar é sempre permitido, independente do
    # status atual do cenário (é justamente o jeito de "resetar" um
    # cenário que ficou travado ou num estado inconsistente). Só faz
    # sentido bloquear otimizar/consolidar/atualizar_fluxos enquanto algo
    # já está rodando (inclusive uma OUTRA atualização de fluxos já em
    # andamento -- flag=7 já está em FLAGS_OPERACAO_EM_ANDAMENTO).
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

    if acao == 'atualizar_fluxos':
        return _disparar_atualizar_fluxos_standalone(usuario, cenario)
    elif acao == 'limpar':
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


# ---------------------------------------------------------------------
# 🌟 NOVO: interromper operações do Celery em andamento antes de limpar
# ---------------------------------------------------------------------
# nome da task -> posição do id do cenário nos argumentos dela
_TASKS_DO_CICLO = {
    'limpar_cenario_celery': 0,
    'consolidar_cenario_celery': 0,
    'atualizar_fluxos_celery': 0,
    'otimizar_cenario_celery': 1,   # (periodo, cen_ativo, total_variaveis)
}
_ROTULO_TASK = {
    'limpar_cenario_celery': 'limpeza',
    'consolidar_cenario_celery': 'consolidação',
    'atualizar_fluxos_celery': 'atualização de fluxos',
    'otimizar_cenario_celery': 'otimização',
}


def _valor_como_estrutura(valor):
    """A inspeção do Celery devolve args/kwargs ora como lista/dict, ora como texto."""
    import ast
    if isinstance(valor, (list, tuple, dict)):
        return valor
    if isinstance(valor, str):
        try:
            return ast.literal_eval(valor)
        except Exception:
            return None
    return None


def _encontrar_tasks_do_cenario(cenario_id):
    """
    Lista as tasks do ciclo (limpar/otimizar/consolidar/atualizar fluxos)
    deste cenário que estão rodando, reservadas por um worker ou agendadas.
    Identifica pelo nome da task + id do cenário nos argumentos -- pega
    também as disparadas pelos botões do Admin, cujo id não fica guardado.
    Limitação: task ainda na fila do broker (não entregue a nenhum worker)
    é invisível pra essa inspeção. Devolve [] se não achou nada ou se
    nenhum worker respondeu.
    """
    try:
        from celery import current_app
        inspecao = current_app.control.inspect(timeout=2.0)
        ativas = inspecao.active() or {}
        reservadas = inspecao.reserved() or {}
        agendadas = inspecao.scheduled() or {}
    except Exception:
        return []

    achadas = []

    def _considerar(t, situacao):
        nome = (t.get('name') or '').rsplit('.', 1)[-1]
        if nome not in _TASKS_DO_CICLO:
            return
        args = _valor_como_estrutura(t.get('args')) or []
        kwargs = _valor_como_estrutura(t.get('kwargs')) or {}
        pos = _TASKS_DO_CICLO[nome]
        valor = args[pos] if isinstance(args, (list, tuple)) and len(args) > pos else None
        if valor is None and isinstance(kwargs, dict):
            valor = kwargs.get('id_cenario', kwargs.get('cen_ativo'))
        try:
            bate = int(valor) == int(cenario_id)
        except (TypeError, ValueError):
            bate = False
        if bate and t.get('id'):
            achadas.append({'id': t['id'], 'nome': nome, 'situacao': situacao})

    for grupo, situacao in ((ativas, 'rodando'), (reservadas, 'na fila de um worker')):
        for tarefas in grupo.values():
            for t in tarefas:
                _considerar(t, situacao)
    for tarefas in agendadas.values():
        for t in tarefas:
            _considerar(t.get('request') or {}, 'agendada')
    return achadas


def _cancelar_consultas_do_cenario(cenario_id):
    """
    Matar a task do Celery NÃO mata a consulta que ela já mandou pro
    Postgres -- uma procedure longa pode continuar rodando órfã e
    concorrer com a limpeza. Cancela (pg_cancel_backend) as consultas
    ATIVAS dessas procedures que mencionam este cenário. Devolve quantas
    foram canceladas.
    """
    import re
    from django.db import connection
    n = str(int(cenario_id))
    padroes = [
        rf'limpa_cenario\(\s*{n}\s*\)',
        rf'consolida_resultados\(\s*{n}\s*\)',
        rf'atualiza_input_output_geral\(\s*{n}\s*\)',
        rf'salva_resultados_order\(\s*{n}\s*,',
        rf'calcula_media_preco_custo_margem_horaria\(\s*{n}\s*,',
        rf'media_preco_custo_margem_horaria_venda_zerada\(\s*{n}\s*\)',
        rf'oti_\w+\(\s*\d+\s*,\s*{n}\s*\)',
    ]
    canceladas = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select pid, query from pg_stat_activity "
                "where state = 'active' and datname = current_database() and pid <> pg_backend_pid()"
            )
            for pid, consulta in cursor.fetchall():
                if any(re.search(pad, consulta or '', re.IGNORECASE) for pad in padroes):
                    cursor.execute("select pg_cancel_backend(%s)", [pid])
                    canceladas += 1
    except Exception:
        pass
    return canceladas


def _perguntar_interromper_ou_encaminhar(usuario, cenario, continuar_com):
    """
    Se o cenário tem operação(ões) do Celery em andamento, avisa o que
    está rodando e pergunta se pode interromper antes de limpar. Devolve
    a pergunta (texto), ou None se não há nada em andamento (o chamador
    segue normalmente). Só inspeciona o Celery quando o status do cenário
    já indica operação em andamento -- evita 6s de espera à toa em toda
    limpeza normal.
    """
    if cenario.flag not in FLAGS_OPERACAO_EM_ANDAMENTO:
        return None
    tasks = _encontrar_tasks_do_cenario(cenario.id)
    if not tasks:
        return None

    from collections import Counter
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    por_tipo = {}
    for t in tasks:
        por_tipo.setdefault(t['nome'], Counter())[t['situacao']] += 1
    linhas = []
    for nome, contagem in por_tipo.items():
        detalhe = ", ".join(f"{qtd} {sit}" for sit, qtd in contagem.items())
        linhas.append(f"- {_ROTULO_TASK[nome]}: {sum(contagem.values())} task(s) ({detalhe})")

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_confirmar_interromper'
    estado.dados_coletados = {
        'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome, 'continuar_com': continuar_com,
    }
    estado.save()
    return (
        f"O cenário **{numero_exibido}/{cenario.cen_nome}** tem operação em andamento agora "
        f"({FLAGS_OPERACAO_EM_ANDAMENTO[cenario.flag]}):\n" + "\n".join(linhas) + "\n\n"
        "Quer que eu **interrompa tudo isso** e faça a limpeza em seguida? (Sim / Não)\n"
        "A limpeza refaz o cenário do zero, então interromper no meio não deixa nada inconsistente. "
        "Se essa operação foi disparada por outra pessoa, ela também será interrompida."
    )


def _etapa_proc_confirmar_interromper(estado, texto):
    import time
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, não interrompi nada. O cenário **{numero_exibido}/{cenario_nome}** não foi limpo."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei."

    tasks = _encontrar_tasks_do_cenario(cenario_id)
    if tasks:
        from celery import current_app
        current_app.control.revoke([t['id'] for t in tasks], terminate=True, signal='SIGTERM')
    canceladas = _cancelar_consultas_do_cenario(cenario_id)
    time.sleep(2)  # dá tempo dos processos morrerem e soltarem os bloqueios

    aviso = f"Interrompi {len(tasks)} operação(ões) em andamento"
    if canceladas:
        aviso += f" e cancelei {canceladas} consulta(s) ainda rodando no banco"
    aviso += "."

    if dados.get('continuar_com') == 'ciclo_completo':
        proxima = _disparar_ciclo_completo(estado.usuario, cenario, sem_checar_em_andamento=True)
    else:
        proxima = _disparar_limpeza_standalone(estado.usuario, cenario, sem_checar_em_andamento=True)
    return f"{aviso}\n\n{proxima}"


def _todas_tasks_mortas(task_ids, disparado_em=None):
    """
    🌟 CORRIGIDO: detecta operação (limpar/otimizar/consolidar/atualizar
    fluxos) que travou de verdade, MAS de forma bem conservadora. A
    versão anterior olhava só as tasks "rodando agora" (active) e
    concluía "morreu" pra qualquer task que ainda estivesse na fila ou
    reservada por um worker ocupado -- zerando o cenário e cancelando o
    acompanhamento enquanto a task ainda ia rodar.

    Agora só devolve True quando TODAS estas condições valem:
      1. passaram pelo menos 10 minutos desde o disparo (task na fila do
         broker é invisível pra inspeção do Celery, então precisa dar
         tempo dela ser pega por um worker);
      2. nenhuma das tasks aparece em active, reserved nem scheduled em
         nenhum worker;
      3. o broker respondeu à inspeção (se não respondeu, não afirma nada).
    Sem 'disparado_em' guardado (estado antigo), nunca declara morta.
    Devolve None quando não dá pra afirmar, False se achou alguma viva.
    """
    if not task_ids or not disparado_em:
        return None
    from datetime import datetime, timezone as _tz
    try:
        disparo = datetime.fromisoformat(disparado_em)
        if disparo.tzinfo is None:
            disparo = disparo.replace(tzinfo=_tz.utc)
        if (datetime.now(_tz.utc) - disparo).total_seconds() < 600:
            return None  # cedo demais pra afirmar qualquer coisa
    except Exception:
        return None
    try:
        from celery import current_app
        inspecao = current_app.control.inspect(timeout=3.0)
        ativas = inspecao.active()
        reservadas = inspecao.reserved()
        agendadas = inspecao.scheduled()
        if ativas is None or reservadas is None or agendadas is None:
            return None  # broker/worker não respondeu -- não afirma nada
        vivas = set()
        for grupo in (ativas, reservadas):
            for tarefas in grupo.values():
                vivas.update(t.get('id') for t in tarefas)
        for tarefas in agendadas.values():
            vivas.update((t.get('request') or {}).get('id') for t in tarefas)
        if any(tid in vivas for tid in task_ids):
            return False
        return True
    except Exception:
        return None


def _perguntar_atualizar_fluxos_ou_encaminhar(usuario, cenario, continuar_com, dados_extra=None):
    """
    🌟 NOVO: checagem reutilizável de "tem fluxo de produção ativo com
    I/O desatualizado?", usada pelos 3 lugares que precisam dela antes
    de limpar um cenário (_disparar_limpeza_standalone,
    _disparar_ciclo_completo, _etapa_confirmar_processar). Se tiver
    fluxo desatualizado, pergunta se quer que o Agente atualize (mesma
    rotina do botão "Atualizar Fluxos" do Admin) antes de continuar --
    guardando em 'continuar_com' QUAL fluxo retomar depois que a
    atualização terminar (ver _continuar_apos_atualizar_fluxos). Se não
    tiver nenhum desatualizado, devolve None -- o chamador segue com a
    própria lógica normalmente, sem nenhuma mudança de comportamento.
    """
    from fluxos.models import TbFluxoProducao

    cenario_id = cenario.id
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id

    qtd_desatualizados = TbFluxoProducao.objects.filter(
        flu_pro_input_output_atualizado=False, tbcenarios_id=cenario_id, flu_pro_ativo=True
    ).count()
    if qtd_desatualizados == 0:
        return None

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_confirmar_atualizar_fluxos'
    estado.dados_coletados = {
        'cenario_id': cenario_id,
        'cenario_nome': cenario.cen_nome,
        'continuar_com': continuar_com,
        **(dados_extra or {}),
    }
    estado.save()
    plural = 's' if qtd_desatualizados > 1 else ''
    return (
        f"Existem **{qtd_desatualizados} fluxo(s)** de produção ativo{plural} com I/O desatualizado no "
        f"cenário **{numero_exibido}/{cenario.cen_nome}**. Quer que eu atualize os fluxos agora, antes de "
        "continuar? (Sim / Não)"
    )


def _continuar_apos_atualizar_fluxos(estado, cenario, dados):
    """
    🌟 NOVO: retoma o fluxo certo depois que os I/O de produção
    terminaram de ser atualizados -- qual retomar vem de
    dados['continuar_com'], guardado por
    _perguntar_atualizar_fluxos_ou_encaminhar no momento em que a
    pergunta foi feita.
    """
    continuar_com = dados.get('continuar_com')

    if continuar_com == 'limpeza_standalone':
        return _disparar_limpeza_standalone(estado.usuario, cenario)

    if continuar_com == 'ciclo_completo':
        return _disparar_ciclo_completo(estado.usuario, cenario)

    if continuar_com == 'confirmar_processar':
        numero_exibido = dados.get('cenario_criado_numero_sequencial')
        return _dispatch_limpeza_pos_criacao(
            estado.usuario, cenario.id, cenario.cen_nome,
            numero_exibido if numero_exibido is not None else cenario.numero_sequencial,
            dados_base=dados,
        )

    if continuar_com == 'standalone':
        # 🌟 NOVO: ação "Atualizar Fluxos de Produção" avulsa -- não
        # encadeia em nada depois, só confirma que terminou.
        numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
        _encerrar_fluxo(estado)
        return f"✅ Fluxos de produção do cenário **{numero_exibido}/{cenario.cen_nome}** atualizados com sucesso."

    _encerrar_fluxo(estado)
    return "Os fluxos foram atualizados, mas não consegui identificar o que fazer em seguida. Tenta de novo."


def _disparar_atualizar_fluxos_standalone(usuario, cenario):
    """
    🌟 NOVO: Ação Comum "Atualizar Fluxos de Produção" -- checa se
    existe fluxo de produção ativo com I/O desatualizado no cenário
    ativo. Se não tiver nenhum, avisa e encerra (nada a fazer). Se
    tiver, informa a quantidade e pergunta se quer atualizar (reaproveita
    _perguntar_atualizar_fluxos_ou_encaminhar, a mesma pergunta usada
    quando "Limpar" encontra fluxo desatualizado no meio do caminho).
    """
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id

    from fluxos.models import TbFluxoProducao
    qtd_desatualizados = TbFluxoProducao.objects.filter(
        flu_pro_input_output_atualizado=False, tbcenarios_id=cenario.id, flu_pro_ativo=True
    ).count()
    if qtd_desatualizados == 0:
        return f"Não existem fluxos de produção desatualizados no cenário **{numero_exibido}/{cenario.cen_nome}**."

    return _perguntar_atualizar_fluxos_ou_encaminhar(usuario, cenario, 'standalone')


def _disparar_limpeza_standalone(usuario, cenario, sem_checar_em_andamento=False):
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    from fluxos.models import TbFluxoProducaoDaugther01
    from django.db import connection
    from .tasks import limpar_cenario_celery

    cenario_id = cenario.id

    if not sem_checar_em_andamento:
        pergunta_interromper = _perguntar_interromper_ou_encaminhar(usuario, cenario, 'limpeza_standalone')
        if pergunta_interromper is not None:
            return pergunta_interromper

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) sem o cálculo dos custos variáveis. Não posso limpar automaticamente -- verifica isso no Admin primeiro."

    resposta_fluxos = _perguntar_atualizar_fluxos_ou_encaminhar(usuario, cenario, 'limpeza_standalone')
    if resposta_fluxos is not None:
        return resposta_fluxos

    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=cenario_id, mae_id__flu_pro_ativo=True).count() > 0:
        return "Tem fluxo(s) de produção ativo(s) com custo variável zerado. Verifica isso no Admin antes de tentar de novo."

    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 5 where id = " + str(cenario_id)
    cursor.execute(sql)
    sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    resultado_async = limpar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_limpeza'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome, 'task_ids': [resultado_async.id], 'disparado_em': __import__('django.utils.timezone', fromlist=['now']).now().isoformat()}
    estado.save()

    return (
        f"Disparei a **limpeza** do cenário **{numero_exibido}/{cenario.cen_nome}** em segundo plano. "
    )


def _etapa_proc_confirmar_atualizar_fluxos(estado, texto):
    """
    🌟 NOVO: resposta ao "quer que eu atualize os fluxos desatualizados
    antes de continuar?" (ver _perguntar_atualizar_fluxos_ou_encaminhar).
    Se sim, dispara atualizar_fluxos_celery (mesma rotina do botão
    "Atualizar Fluxos" do Admin) e acompanha via polling; quando
    terminar, retoma o fluxo certo (ver _continuar_apos_atualizar_fluxos).
    """
    resposta = texto.strip().lower()
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return f"Ok, não atualizei os fluxos. Cenário **{numero_exibido}/{cenario_nome}** não foi processado -- resolve isso no Admin quando quiser tentar de novo."

    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    from .tasks import atualizar_fluxos_celery
    resultado_async = atualizar_fluxos_celery.delay(cenario_id)

    estado.etapa_atual = 'proc_aguardando_atualizar_fluxos'
    dados['task_ids'] = [resultado_async.id]
    dados['disparado_em'] = __import__('django.utils.timezone', fromlist=['now']).now().isoformat()
    estado.dados_coletados = dados  # preserva continuar_com (e qualquer dado extra) pra depois
    estado.save()

    return f"Disparei a **atualização dos fluxos** do cenário **{numero_exibido}/{cenario_nome}** em segundo plano."


def _etapa_proc_aguardando_atualizar_fluxos(estado, texto):
    from django.db import connection
    dados = estado.dados_coletados
    cenario_id = dados['cenario_id']
    cenario_nome = dados['cenario_nome']
    numero_exibido = _numero_sequencial_por_id(cenario_id)
    cenario = TbCenarios.objects_real.filter(id=cenario_id).first()
    if cenario is None:
        _encerrar_fluxo(estado)
        return f"O cenário {numero_exibido}/{cenario_nome} não existe mais. Cancelei o acompanhamento."

    if cenario.flag == 8:  # FLUXOS ATUALIZADOS
        return _continuar_apos_atualizar_fluxos(estado, cenario, dados)

    if cenario.flag == 7:  # ainda atualizando
        if _todas_tasks_mortas(dados.get('task_ids'), dados.get('disparado_em')) is True:
            cursor = connection.cursor()
            sql = "update parameters_tbcenarios set flag = 0 where id = " + str(cenario_id)
            cursor.execute(sql)
            cursor.close()
            _encerrar_fluxo(estado)
            return (
                f"A atualização de fluxos do cenário **{numero_exibido}/{cenario_nome}** parece ter "
                "travado (nenhum processo ainda está rodando, mas ela não terminou). Marquei o "
                "cenário como ALTERADO -- pode tentar de novo."
            )
        return f"Ainda atualizando os fluxos do cenário **{numero_exibido}/{cenario_nome}**."

    _encerrar_fluxo(estado)
    return f"O status do cenário **{numero_exibido}/{cenario_nome}** mudou pra algo inesperado (flag={cenario.flag}) -- melhor conferir manualmente no Admin. Cancelei o acompanhamento automático aqui."


def _etapa_proc_aguardando_limpeza(estado, texto):
    from django.db import connection
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
        return f"✅ Cenário **{numero_exibido}/{cenario_nome}** limpo! Quer que eu já dispare a **otimização**? (Sim / Não)"

    if cenario.flag == 5:  # ainda limpando
        # 🌟 NOVO: se nenhuma task ainda está rodando de verdade (worker
        # morreu no meio, por exemplo), o flag=5 nunca ia mudar sozinho
        # -- detecta isso e recupera, em vez de esperar pra sempre.
        if _todas_tasks_mortas(dados.get('task_ids'), dados.get('disparado_em')) is True:
            cursor = connection.cursor()
            sql = "update parameters_tbcenarios set flag = 0 where id = " + str(cenario_id)
            cursor.execute(sql)
            cursor.close()
            _encerrar_fluxo(estado)
            return (
                f"A limpeza do cenário **{numero_exibido}/{cenario_nome}** parece ter travado (nenhum "
                "processo ainda está rodando, mas ela não terminou). Marquei o cenário como ALTERADO "
                "-- pode tentar limpar de novo."
            )
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
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
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

    task_ids = []
    for i in range(total_periodos):
        if TbCenariosDaugther.objects.get(mae_id=cenario_id, dau_order=i + 1).otimizar:
            resultado_async = otimizar_cenario_celery.delay(i, cenario_id, total_variaveis)
            task_ids.append(resultado_async.id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_otimizacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome, 'total_periodos': total_periodos, 'task_ids': task_ids, 'disparado_em': __import__('django.utils.timezone', fromlist=['now']).now().isoformat()}
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
        if _todas_tasks_mortas(dados.get('task_ids'), dados.get('disparado_em')) is True:
            from django.db import connection
            cursor = connection.cursor()
            sql = "update parameters_tbcenarios set flag = 0 where id = " + str(cenario_id)
            cursor.execute(sql)
            cursor.close()
            _encerrar_fluxo(estado)
            return (
                f"A otimização do cenário **{numero_exibido}/{cenario_nome}** parece ter travado "
                f"({total_otimizado} de {total_periodos} período(s) concluídos, mas nenhum processo "
                "ainda está rodando). Marquei o cenário como ALTERADO -- pode tentar otimizar de novo."
            )
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
    return f"✅ Cenário **{numero_exibido}/{cenario_nome}** otimizado! Quer que eu já dispare a **consolidação**? (Sim / Não)"


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
    numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    from django.db import connection
    from .tasks import consolidar_cenario_celery

    cenario_id = cenario.id
    cursor = connection.cursor()
    sql = "update parameters_tbcenarios set flag = 6 where id = " + str(cenario_id)
    cursor.execute(sql)
    cursor.close()
    resultado_async = consolidar_cenario_celery.delay(cenario_id)

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_PROCESSAR
    estado.etapa_atual = 'proc_aguardando_consolidacao'
    estado.dados_coletados = {'cenario_id': cenario_id, 'cenario_nome': cenario.cen_nome, 'task_ids': [resultado_async.id], 'disparado_em': __import__('django.utils.timezone', fromlist=['now']).now().isoformat()}
    estado.save()

    return (
        f"Disparei a **consolidação** do cenário **{numero_exibido}/{cenario.cen_nome}** em segundo plano. "
    )


def _etapa_proc_aguardando_consolidacao(estado, texto):
    from django.db import connection
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
        if _todas_tasks_mortas(dados.get('task_ids'), dados.get('disparado_em')) is True:
            cursor = connection.cursor()
            sql = "update parameters_tbcenarios set flag = 0 where id = " + str(cenario_id)
            cursor.execute(sql)
            cursor.close()
            _encerrar_fluxo(estado)
            return (
                f"A consolidação do cenário **{numero_exibido}/{cenario_nome}** parece ter travado "
                "(nenhum processo ainda está rodando, mas ela não terminou). Marquei o cenário como "
                "ALTERADO -- pode tentar consolidar de novo."
            )
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
    'proc_confirmar_atualizar_fluxos': _etapa_proc_confirmar_atualizar_fluxos,
    'proc_confirmar_interromper': _etapa_proc_confirmar_interromper,
    'proc_aguardando_atualizar_fluxos': _etapa_proc_aguardando_atualizar_fluxos,
    'proc_pos_limpeza_otimizar': _etapa_proc_pos_limpeza_otimizar,
    'proc_aguardando_otimizacao': _etapa_proc_aguardando_otimizacao,
    'proc_pos_otimizacao_consolidar': _etapa_proc_pos_otimizacao_consolidar,
    'proc_aguardando_consolidacao': _etapa_proc_aguardando_consolidacao,
    'exportar_otimizacao_confirmar_email': _etapa_exportar_otimizacao_confirmar_email,
    'exportar_otimizacao_aguardando': _etapa_exportar_otimizacao_aguardando,
    'exportar_otimizacao_aguardando_email': _etapa_exportar_otimizacao_aguardando_email,
    'fp_criar_no_editor_aguardando': _etapa_fp_criar_no_editor_aguardando,
    'fp_criar_fluxos_produto_escolher': _etapa_fp_criar_fluxos_produto_escolher,
    'fp_criar_fluxos_produto_confirmar': _etapa_fp_criar_fluxos_produto_confirmar,
    'fp_criar_fluxos_produto_aguardando': _etapa_fp_criar_fluxos_produto_aguardando,
    # 🌟 NOVO: "Atualizar Fluxos de Produção por Produto" com situação por produto e seleção múltipla.
    'fp_sit_calculando': _etapa_fp_sit_calculando,
    'fp_sit_selecionar': _etapa_fp_sit_selecionar,
    'fp_sit_confirmar': _etapa_fp_sit_confirmar,
    'fp_lote_aguardando': _etapa_fp_lote_aguardando,
    'fp_io_confirmar': _etapa_fp_io_confirmar,
    'fp_io_aguardando': _etapa_fp_io_aguardando,
    # 🌟 NOVO: "Comparar Fluxos de Produção" (só consulta)
    'cmp_produto': _etapa_cmp_produto,
    'cmp_fluxo': _etapa_cmp_fluxo,
    'cmp_busca': _etapa_cmp_fluxo,        # nomes antigos: uma conversa em andamento antes da atualização continua funcionando
    'cmp_escolher': _etapa_cmp_fluxo,
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
        "clique em \"Concluir\". (ou \"Cancelar\")"
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
            return "Você ainda não marcou nenhum cenário. Clica num cenário da lista pra marcar, ou \"Cancelar\"."
        estado.etapa_atual = 'cen_excluir_confirmar'
        estado.save()
        nomes = ", ".join(f"{candidatos[id_str]['numero']}/{candidatos[id_str]['nome']}" for id_str in dados['selecionados'])
        return (
            f"⚠️ Confirma a EXCLUSÃO PERMANENTE do(s) cenário(s): **{nomes}**?\n\n"
            "Essa ação não pode ser desfeita. (Sim / Não)"
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
            "-- ela só traz os mais recentes. Ou digite \"Concluir\" quando terminar (ou \"Cancelar\")."
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
            "(ou digite \"Cancelar\" pra parar de acompanhar -- a "
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


# ---------------------------------------------------------------------
# Fluxo: importar_custo_ferbasa -- Produção Mensal + Distribuição GGF
# Mensal a partir de arquivo enviado pelo usuário (Ações Comuns:
# "Atualizar Produção e GGF Mensal", app custo_ferbasa). Reaproveita a
# MESMA regra de negócio (validação de cabeçalho, upsert de cadastros
# auxiliares) já usada na importação a partir da AWS -- só a origem do
# arquivo muda (upload em Relatórios, em vez de um arquivo fixo no S3),
# e agora aceita QUALQUER nome de arquivo, em Excel OU CSV.
# ---------------------------------------------------------------------
FLUXO_IMPORTAR_CF = 'importar_custo_ferbasa'


def iniciar_fluxo_importar_custo_ferbasa(usuario, mensagem=""):
    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_IMPORTAR_CF
    estado.etapa_atual = 'cf_aguardando_producao'
    estado.dados_coletados = {}
    estado.save()
    return (
        "Vamos atualizar as tabelas de **Produção Mensal** e **Distribuição GGF Mensal** 🏭\n\n"
        "Primeiro a **Produção Mensal**: solta o arquivo (Excel ou CSV, qualquer nome) no espaço "
        "de atualização (logo abaixo dos Relatórios) e manda qualquer mensagem (ou clica em "
        "\"Já enviei o arquivo\") quando terminar. Se quiser atualizar só a Distribuição GGF Mensal, "
        "clica em \"Pular\" pra ir direto pra ela. (ou \"Cancelar\")"
    )


def _processar_arquivo_custo_ferbasa(usuario, etapa_atual):
    """
    Chamado direto pelo agents.py (fora do despacho normal de fluxo,
    igual ao mecanismo já usado pra planilha de indicador/câmbio) quando
    o usuário está esperando o arquivo de Produção Mensal, de
    Distribuição GGF Mensal, ou de correção de Tipo (Conta Contábil x
    Centro de Custo), e há um arquivo no espaço único de atualização
    (ArquivoAtualizacaoAgente).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None:
        return "Não consegui identificar sua empresa."
    empresa_id = perfil.empresa_efetiva_id()

    from .models import ArquivoAtualizacaoAgente
    arquivo = ArquivoAtualizacaoAgente.objects.filter(empresa_id=empresa_id).order_by('-id').first()
    if arquivo is None or not arquivo.arquivo or not arquivo.arquivo.name.lower().endswith(('.xlsx', '.xls', '.csv')):
        nomes_etapa = {
            'cf_aguardando_producao': 'Produção Mensal',
            'cf_aguardando_ggf': 'Distribuição GGF Mensal',
            'cf_aguardando_conta_cc_tipo': 'correção de Tipo',
        }
        etapa_nome = nomes_etapa.get(etapa_atual, 'arquivo')
        return (
            f"Não encontrei nenhum arquivo Excel ou CSV no espaço de atualização. Solta o "
            f"arquivo de **{etapa_nome}** ali e manda de novo."
        )

    from custo_ferbasa.tasks import (
        importar_producao_mensal_de_relatorio_celery,
        importar_distribuicao_ggf_mensal_de_relatorio_celery,
        importar_conta_cc_tipo_de_relatorio_celery,
    )

    estado = _get_estado(usuario)

    if etapa_atual == 'cf_aguardando_producao':
        resultado_async = importar_producao_mensal_de_relatorio_celery.delay(arquivo.id, usuario.id)
        estado.etapa_atual = 'cf_processando_producao'
        # 🌟 NOVO: guarda o id da task -- permite depois confirmar direto
        # com o Celery se ela ainda está sendo processada por algum
        # worker vivo, em vez de só assumir que sim.
        estado.dados_coletados = {'celery_task_id': resultado_async.id}
        estado.save()
        return "Arquivo recebido! Processando a **Produção Mensal** em segundo plano."

    elif etapa_atual == 'cf_aguardando_ggf':
        resultado_async = importar_distribuicao_ggf_mensal_de_relatorio_celery.delay(arquivo.id, usuario.id)
        estado.etapa_atual = 'cf_processando_ggf'
        estado.dados_coletados = {'celery_task_id': resultado_async.id}
        estado.save()
        return "Arquivo recebido! Processando a **Distribuição GGF Mensal** em segundo plano."

    else:  # cf_aguardando_conta_cc_tipo
        resultado_async = importar_conta_cc_tipo_de_relatorio_celery.delay(arquivo.id, usuario.id)
        estado.etapa_atual = 'cf_processando_conta_cc_tipo'
        estado.dados_coletados = {'celery_task_id': resultado_async.id}
        estado.save()
        return "Arquivo recebido! Processando a correção de Tipo em segundo plano."


def _texto_iniciar_conta_cc_tipo():
    """
    🌟 NOVO: mensagem que abre a etapa de correção de Tipo (F/V/O) dos
    registros de Conta Contábil x Centro de Custo ainda com Tipo = "A"
    (indefinido) -- gera a planilha na hora e devolve o link de download
    junto com as instruções. Reaproveitada tanto na sequência normal
    (depois da Distribuição GGF Mensal terminar) quanto no atalho
    "Pular" na etapa de GGF.
    """
    from custo_ferbasa.tasks import gerar_planilha_conta_cc_tipo_a
    url = gerar_planilha_conta_cc_tipo_a()
    return (
        f"[📥 Baixar planilha]({url})\n\n"
        "Alguns registros da tabela Conta Contábil x Centro de Custo estão com o campo Tipo "
        "igual a Aguardando (A). Edita a coluna **Tipo** da planilha acima disponibilizada para "
        "download pra **F** (Custo Fixo), **V** (Custo Variável) ou **O** (Outro), salva, e "
        "solta o arquivo no espaço de atualização. Manda qualquer mensagem (ou clica em \"Já "
        "enviei o arquivo\") quando terminar. (ou \"Cancelar\")"
    )


def _iniciar_genealogia(estado):
    """
    🌟 NOVO: último passo do fluxo -- atualiza a Genealogia dos Produtos
    da empresa (procedure public.atualizar_genealogia_v2, versão
    otimizada). Antes de disparar, confere que NENHUM registro de Conta
    Contábil x Centro de Custo está com Tipo fora de F, V ou O -- essa
    procedure usa esse campo pra classificar o GGF em variável/fixo, e
    rodar com dados inconsistentes gera números errados sem avisar.
    """
    from custo_ferbasa.tasks import existem_contas_cc_tipo_invalido, atualizar_genealogia_de_chat_celery
    if existem_contas_cc_tipo_invalido():
        _encerrar_fluxo(estado)
        return (
            "❌ A tabela Conta Contábil x Centro de Custo tem registro(s) com Tipo diferente de "
            "F, V ou O. Essa tabela precisa ser corrigida antes de permitir a atualização da "
            "Genealogia dos Produtos da empresa."
        )
    atualizar_genealogia_de_chat_celery.delay(estado.usuario_id)
    estado.etapa_atual = 'cf_processando_genealogia'
    estado.dados_coletados = {}
    estado.save()
    return (
        "⏳ Atualizando a Genealogia dos Produtos da empresa em segundo plano... Essa é a etapa "
        "mais demorada. Se quiser, pode clicar em \"Encerrar Ação\" -- isso só para de acompanhar "
        "por aqui, a atualização em si **continua rodando normalmente** até terminar."
    )


def _avancar_apos_ggf(estado):
    """
    🌟 NOVO: depois da Distribuição GGF Mensal terminar (ou ser pulada),
    decide o próximo passo -- se existir algum registro de Conta
    Contábil x Centro de Custo ainda com Tipo = "A", abre essa etapa
    extra; senão, já parte direto pra atualização da Genealogia dos
    Produtos (último passo do fluxo).
    """
    from custo_ferbasa.tasks import existem_contas_cc_tipo_a
    if existem_contas_cc_tipo_a():
        estado.etapa_atual = 'cf_aguardando_conta_cc_tipo'
        estado.dados_coletados = {}
        estado.save()
        return _texto_iniciar_conta_cc_tipo()
    return _iniciar_genealogia(estado)


def _processar_importar_cf(estado, texto):
    if estado.etapa_atual == 'cf_aguardando_producao':
        # 🌟 NOVO: permite pular direto pra Distribuição GGF Mensal, pro
        # usuário que só quer atualizar essa e não a Produção Mensal.
        if (texto or '').strip().lower() in ('pular', 'pula'):
            estado.etapa_atual = 'cf_aguardando_ggf'
            estado.dados_coletados = {}
            estado.save()
            return (
                "Ok, pulando a Produção Mensal. Agora a **Distribuição GGF Mensal**: solta o "
                "arquivo (Excel ou CSV, qualquer nome) no espaço de atualização e manda qualquer "
                "mensagem (ou clica em \"Já enviei o arquivo\") quando terminar. Se quiser pular "
                "essa também, clica em \"Pular\". (ou \"Cancelar\")"
            )
        return (
            "Ainda esperando o arquivo de **Produção Mensal**. Solta ele (Excel ou CSV) no espaço "
            "de atualização e manda de novo (ou clica em \"Já enviei o arquivo\"). Se quiser "
            "atualizar só a Distribuição GGF Mensal, clica em \"Pular\". (ou \"Cancelar\")"
        )
    elif estado.etapa_atual == 'cf_aguardando_ggf':
        # 🌟 NOVO: permite pular direto pra próxima etapa que fizer
        # sentido (correção de Tipo, se houver algo pra corrigir, ou a
        # Genealogia dos Produtos direto).
        if (texto or '').strip().lower() in ('pular', 'pula'):
            mensagem = _avancar_apos_ggf(estado)
            return "Ok, pulando a Distribuição GGF Mensal.\n\n" + mensagem
        return (
            "Ainda esperando o arquivo de **Distribuição GGF Mensal**. Solta ele (Excel ou CSV) no "
            "espaço de atualização e manda de novo (ou clica em \"Já enviei o arquivo\"). Se quiser "
            "pular essa etapa, clica em \"Pular\". (ou \"Cancelar\")"
        )
    elif estado.etapa_atual == 'cf_aguardando_conta_cc_tipo':
        return (
            "Ainda esperando o arquivo de correção de Tipo. Solta ele (Excel ou CSV) no espaço de "
            "atualização e manda de novo (ou clica em \"Já enviei o arquivo\") (ou \"Cancelar\")."
        )
    elif estado.etapa_atual == 'cf_processando_producao':
        return _etapa_cf_processando_producao(estado, texto)
    elif estado.etapa_atual == 'cf_processando_ggf':
        return _etapa_cf_processando_ggf(estado, texto)
    elif estado.etapa_atual == 'cf_processando_conta_cc_tipo':
        return _etapa_cf_processando_conta_cc_tipo(estado, texto)
    elif estado.etapa_atual == 'cf_processando_genealogia':
        return _etapa_cf_processando_genealogia(estado, texto)

    _encerrar_fluxo(estado)
    return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."


def _formatar_resultado_importacao_cf(resultado):
    if not resultado:
        return ""
    if 'total_criadas' in resultado:
        criadas = resultado.get('total_criadas', 0)
        atualizadas = resultado.get('total_atualizadas', 0)
        return f" ({criadas} linha(s) nova(s), {atualizadas} atualizada(s))"
    return f" ({resultado.get('total_atualizadas', 0)} linha(s) atualizada(s))"


def _checar_travamento_celery(estado, nome_display, etapa_aguardando):
    """
    🌟 NOVO: quando ainda não temos status de conclusão/erro, confirma
    ATIVAMENTE com o Celery se a task ainda está sendo processada por
    algum worker vivo -- em vez de simplesmente assumir que sim (e ficar
    "⏳ Ainda processando" pra sempre). Detecta o caso real já visto em
    produção: o worker que processava a task morreu no meio (ex:
    reinício do Celery), deixando a task perdida, sem nunca reportar
    conclusão nem erro.

    Devolve uma mensagem de aviso (já resetando a etapa pra aguardar um
    novo arquivo) se confirmar que não tem nada rodando, ou None se
    ainda está confirmadamente ativa OU se não deu pra confirmar (nesse
    caso, melhor não afirmar nada e continuar esperando normalmente).
    """
    from custo_ferbasa.tasks import celery_task_ainda_ativa
    dados = estado.dados_coletados or {}
    task_id = dados.get('celery_task_id')
    ainda_ativa = celery_task_ainda_ativa(task_id)
    if ainda_ativa is not False:
        return None
    estado.etapa_atual = etapa_aguardando
    estado.dados_coletados = {}
    estado.save()
    return (
        f"⚠️ Não encontrei mais nenhum processamento em andamento pra {nome_display}, mas ele "
        "também não chegou a terminar -- provavelmente foi interrompido (por exemplo, o servidor "
        "ou o worker do Celery reiniciou no meio). Solta o arquivo de novo no espaço de "
        "atualização e tenta outra vez."
    )


def _checar_travamento_genealogia(estado):
    """Mesma ideia de _checar_travamento_celery, mas checando direto no PostgreSQL."""
    from custo_ferbasa.tasks import genealogia_ainda_rodando_no_banco
    ainda_rodando = genealogia_ainda_rodando_no_banco()
    if ainda_rodando is not False:
        return None
    _encerrar_fluxo(estado)
    return (
        "⚠️ Não encontrei mais a atualização da Genealogia rodando no banco, mas ela também não "
        "chegou a terminar -- provavelmente foi interrompida (por exemplo, o servidor reiniciou "
        "no meio, ou a query foi cancelada diretamente no banco). Se quiser, pode iniciar a "
        "atualização de novo."
    )


def _etapa_cf_processando_producao(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        # 🌟 CORRIGIDO: volta pra etapa de espera (em vez de encerrar o
        # fluxo por completo) -- o arquivo com problema já foi apagado
        # pela task, então o espaço está livre pro usuário salvar a
        # versão corrigida direto, sem precisar reabrir a ação do zero.
        estado.etapa_atual = 'cf_aguardando_producao'
        estado.dados_coletados = {}
        estado.save()
        return (
            f"❌ Deu erro ao processar a Produção Mensal: {mensagem_erro}\n\n"
            "Corrige o arquivo e solta ele de novo no espaço de atualização. Manda qualquer "
            "mensagem (ou clica em \"Já enviei o arquivo\") quando terminar. Se quiser pular "
            "essa etapa, clica em \"Pular\". (ou \"Cancelar\")"
        )

    if status == 'concluido':
        resumo = _formatar_resultado_importacao_cf(dados.get('resultado_importacao_cf'))
        estado.etapa_atual = 'cf_aguardando_ggf'
        estado.dados_coletados = {}
        estado.save()
        return (
            f"✅ **Produção Mensal** atualizada{resumo}!\n\n"
            "Agora a **Distribuição GGF Mensal**: solta o arquivo (Excel ou CSV, qualquer nome) no "
            "espaço de atualização e manda qualquer mensagem (ou clica em \"Já enviei o arquivo\") "
            "quando terminar. Se quiser pular essa etapa, clica em \"Pular\". (ou \"Cancelar\")"
        )

    aviso = _checar_travamento_celery(estado, 'Produção Mensal', 'cf_aguardando_producao')
    if aviso:
        return aviso
    return "⏳ Ainda processando a **Produção Mensal**."


def _etapa_cf_processando_ggf(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        estado.etapa_atual = 'cf_aguardando_ggf'
        estado.dados_coletados = {}
        estado.save()
        return (
            f"❌ Deu erro ao processar a Distribuição GGF Mensal: {mensagem_erro}\n\n"
            "Corrige o arquivo e solta ele de novo no espaço de atualização. Manda qualquer "
            "mensagem (ou clica em \"Já enviei o arquivo\") quando terminar. Se quiser pular "
            "essa etapa, clica em \"Pular\". (ou \"Cancelar\")"
        )

    if status == 'concluido':
        resumo = _formatar_resultado_importacao_cf(dados.get('resultado_importacao_cf'))
        mensagem = _avancar_apos_ggf(estado)
        return f"✅ **Distribuição GGF Mensal** atualizada{resumo}!\n\n" + mensagem

    aviso = _checar_travamento_celery(estado, 'Distribuição GGF Mensal', 'cf_aguardando_ggf')
    if aviso:
        return aviso
    return "⏳ Ainda processando a **Distribuição GGF Mensal**."


def _etapa_cf_processando_conta_cc_tipo(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        estado.etapa_atual = 'cf_aguardando_conta_cc_tipo'
        estado.dados_coletados = {}
        estado.save()
        return (
            f"❌ Deu erro ao processar a correção de Tipo: {mensagem_erro}\n\n"
            "Corrige o arquivo e solta ele de novo no espaço de atualização. Manda qualquer "
            "mensagem (ou clica em \"Já enviei o arquivo\") quando terminar. (ou \"Cancelar\")"
        )

    if status == 'concluido':
        resumo = _formatar_resultado_importacao_cf(dados.get('resultado_importacao_cf'))
        mensagem = _iniciar_genealogia(estado)
        return f"✅ Tipo corrigido{resumo}!\n\n" + mensagem

    aviso = _checar_travamento_celery(estado, 'correção de Tipo', 'cf_aguardando_conta_cc_tipo')
    if aviso:
        return aviso
    return "⏳ Ainda processando a correção de Tipo."


def _etapa_cf_processando_genealogia(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return f"❌ Deu erro ao atualizar a Genealogia dos Produtos: {mensagem_erro}"

    if status == 'concluido':
        _encerrar_fluxo(estado)
        return "✅ Genealogia dos Produtos da empresa atualizada com sucesso! Todas as etapas foram concluídas."

    aviso = _checar_travamento_genealogia(estado)
    if aviso:
        return aviso
    return (
        "⏳ Ainda atualizando a Genealogia dos Produtos da empresa. Se quiser, pode clicar em "
        "\"Encerrar Ação\" -- isso só para de acompanhar por aqui, a atualização em si continua "
        "rodando normalmente até terminar."
    )


# ---------------------------------------------------------------------
# Fluxo: consumo_especifico_custo_variavel -- Consumo Específico seguido
# de Custo Variável Adicionado, por período (app custo_ferbasa, Ações
# Comuns: "Atualizar Consumo Específico e Custo Variável").
# Reaproveita as MESMAS procedures de banco já usadas pelas ações
# "Calcular / Atualizar Por Período" do Admin (TbConsumoEspecificoAdmin
# e TbCustoVariavelAdicionadoAdmin) -- só que dessa vez atualizando
# TODOS os registros da tabela, não só os selecionados, e encadeando as
# duas etapas automaticamente (a segunda só começa depois que a
# primeira termina).
# ---------------------------------------------------------------------
FLUXO_CONSUMO_ESPECIFICO = 'consumo_especifico_cf'


def iniciar_fluxo_consumo_especifico(usuario, mensagem=""):
    """
    🌟 CORRIGIDO: sugere o período com base no mínimo/máximo de Ano/Mês
    cadastrados em Produção Mensal -- mas agora o usuário PODE digitar
    um período diferente (respeitando os limites), não só confirmar o
    sugerido.
    """
    from django.db.models import Min, Max
    from custo_ferbasa.models import TbProducaoMensal

    limites = TbProducaoMensal.objects.aggregate(minimo=Min('pro_men_ano_mes'), maximo=Max('pro_men_ano_mes'))
    ano_mes_minimo = limites['minimo']
    ano_mes_maximo = limites['maximo']

    if ano_mes_minimo is None or ano_mes_maximo is None:
        return (
            "Não encontrei nenhum registro em Produção Mensal -- não dá pra sugerir um período "
            "sem isso. Atualize a Produção Mensal antes de rodar essa ação."
        )

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_CONSUMO_ESPECIFICO
    estado.etapa_atual = 'ce_confirmar_periodo'
    estado.dados_coletados = {'ano_mes_minimo': ano_mes_minimo, 'ano_mes_maximo': ano_mes_maximo}
    estado.save()
    # 🌟 NOVO: [FORM_PERIODO:min:max] é um marcador que a tela do chat
    # reconhece e transforma em dois campos de digitação (Início/Fim) já
    # preenchidos com os valores sugeridos, com um botão "Confirmar" --
    # o usuário edita direto na tela, em vez de digitar um período no
    # meio da conversa. O marcador nunca aparece pro usuário (é
    # extraído do texto antes de exibir).
    return (
        "Vamos atualizar o **Consumo Específico** e, na sequência, o **Custo Variável "
        "Adicionado** 🏭\n\n"
        f"Com base no que já está cadastrado em Produção Mensal, o período sugerido é de "
        f"**{ano_mes_minimo}** até **{ano_mes_maximo}** -- ajuste se precisar e confirme "
        "abaixo, ou clica em \"Cancelar\".\n\n"
        f"[FORM_PERIODO:{ano_mes_minimo}:{ano_mes_maximo}]"
    )


def _processar_consumo_especifico(estado, texto):
    if estado.etapa_atual == 'ce_confirmar_periodo':
        return _etapa_ce_confirmar_periodo(estado, texto)
    elif estado.etapa_atual == 'ce_processando_consumo_especifico':
        return _etapa_ce_processando_consumo_especifico(estado, texto)
    elif estado.etapa_atual == 'ce_processando_custo_variavel':
        return _etapa_ce_processando_custo_variavel(estado, texto)
    elif estado.etapa_atual == 'ce_confirmar_indicadores':
        return _etapa_ce_confirmar_indicadores(estado, texto)
    elif estado.etapa_atual == 'ce_processando_indicador_fluxo':
        return _etapa_ce_processando_indicador_fluxo(estado, texto)
    elif estado.etapa_atual == 'ce_processando_indicador_equipamentos':
        return _etapa_ce_processando_indicador_equipamentos(estado, texto)
    elif estado.etapa_atual == 'ce_processando_custo_item_preco':
        return _etapa_ce_processando_custo_item_preco(estado, texto)

    _encerrar_fluxo(estado)
    return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."


def _etapa_ce_confirmar_periodo(estado, texto):
    texto_bruto = (texto or '').strip()
    resposta = texto_bruto.lower()
    dados = estado.dados_coletados or {}
    ano_mes_minimo = dados.get('ano_mes_minimo')
    ano_mes_maximo = dados.get('ano_mes_maximo')

    if resposta in ('não', 'nao', 'n', 'no'):
        _encerrar_fluxo(estado)
        return "Ok, não disparei nada."

    if resposta in ('sim', 's', 'yes', 'y'):
        ano_mes_inicio = ano_mes_minimo
        ano_mes_fim = ano_mes_maximo
    else:
        # 🌟 NOVO: tenta extrair dois períodos no formato AAAA/MM do
        # texto digitado -- aceita variações como "2020/01,2025/12",
        # "2020/01 a 2025/12", "de 2020/01 até 2025/12", etc. Isso cobre
        # tanto quem usa o formulário na tela (que manda exatamente
        # nesse formato ao confirmar) quanto quem prefere digitar livre.
        periodos_encontrados = re.findall(r'\d{4}/\d{2}', texto_bruto)
        if len(periodos_encontrados) != 2:
            return (
                "Não consegui entender esse período. Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                f"[FORM_PERIODO:{ano_mes_minimo}:{ano_mes_maximo}]"
            )
        ano_mes_inicio, ano_mes_fim = periodos_encontrados

        if ano_mes_fim < ano_mes_inicio:
            return (
                f"O fim ({ano_mes_fim}) não pode ser antes do início ({ano_mes_inicio}). Ajuste "
                "abaixo, ou clica em \"Cancelar\".\n\n"
                f"[FORM_PERIODO:{ano_mes_inicio}:{ano_mes_fim}]"
            )
        if ano_mes_inicio < ano_mes_minimo:
            return (
                f"O início ({ano_mes_inicio}) não pode ser antes de {ano_mes_minimo} (não tem "
                "Produção Mensal cadastrada antes disso). Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                f"[FORM_PERIODO:{ano_mes_inicio}:{ano_mes_fim}]"
            )
        if ano_mes_fim > ano_mes_maximo:
            return (
                f"O fim ({ano_mes_fim}) não pode ser depois de {ano_mes_maximo} (não tem "
                "Produção Mensal cadastrada depois disso). Ajuste abaixo, ou clica em \"Cancelar\".\n\n"
                f"[FORM_PERIODO:{ano_mes_inicio}:{ano_mes_fim}]"
            )

    from custo_ferbasa.models import TbConsumoEspecifico
    from custo_ferbasa.tasks import calcular_consumo_especifico_de_chat_celery

    # 🌟 Mesma preparação da ação do Admin (zera o flag geral, depois
    # marca os alvos como "A CALCULAR") -- só que aqui os alvos são
    # TODOS os registros da tabela, não só os selecionados.
    TbConsumoEspecifico.objects.update(flag=False)
    TbConsumoEspecifico.objects.update(flag=True, con_esp_status='A CALCULAR')

    calcular_consumo_especifico_de_chat_celery.delay(ano_mes_inicio, ano_mes_fim, estado.usuario_id)
    estado.etapa_atual = 'ce_processando_consumo_especifico'
    estado.dados_coletados = {'ano_mes_inicio': ano_mes_inicio, 'ano_mes_fim': ano_mes_fim}
    estado.save()
    return (
        f"⏳ Calculando o **Consumo Específico** de **{ano_mes_inicio}** a **{ano_mes_fim}** em "
        "segundo plano..."
    )


def _etapa_ce_processando_consumo_especifico(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return f"❌ Deu erro ao calcular o Consumo Específico: {mensagem_erro}"

    if status == 'concluido':
        return _iniciar_custo_variavel_adicionado(estado, dados.get('ano_mes_inicio'), dados.get('ano_mes_fim'))

    from custo_ferbasa.tasks import procedure_ainda_rodando_no_banco
    ainda_rodando = procedure_ainda_rodando_no_banco('consumo_especifico_periodo')
    if ainda_rodando is False:
        _encerrar_fluxo(estado)
        return (
            "⚠️ Não encontrei mais o cálculo do Consumo Específico rodando no banco, mas ele "
            "também não chegou a terminar -- provavelmente foi interrompido (por exemplo, o "
            "servidor ou o worker do Celery reiniciou no meio). Se quiser, pode iniciar de novo."
        )
    return (
        "⏳ Ainda calculando o **Consumo Específico**. Se quiser, pode clicar em \"Cancelar\" -- "
        "isso interrompe a sequência (o Custo Variável Adicionado não vai disparar automaticamente "
        "depois), mas o cálculo do Consumo Específico já disparado no banco continua rodando "
        "normalmente até terminar."
    )


def _iniciar_custo_variavel_adicionado(estado, ano_mes_inicio, ano_mes_fim):
    """
    🌟 NOVO: segunda etapa -- só dispara depois que o Consumo Específico
    terminar, reaproveitando o MESMO período da primeira etapa. Antes de
    disparar, confere que não existe nenhum registro de Conta Contábil x
    Centro de Custo com Tipo = "A" -- mesma checagem que a ação
    equivalente do Admin (TbCustoVariavelAdicionadoAdmin) já fazia.
    """
    from custo_ferbasa.tasks import existem_contas_cc_tipo_a, calcular_custo_variavel_adicionado_de_chat_celery
    if existem_contas_cc_tipo_a():
        _encerrar_fluxo(estado)
        return (
            "❌ Temos registro(s) na tabela Conta Contábil x Centro de Custo aguardando "
            "classificação (Tipo = \"A\"). Corrija essa tabela antes de calcular o Custo Variável "
            "Adicionado."
        )

    calcular_custo_variavel_adicionado_de_chat_celery.delay(ano_mes_inicio, ano_mes_fim, estado.usuario_id)
    estado.etapa_atual = 'ce_processando_custo_variavel'
    estado.dados_coletados = {'ano_mes_inicio': ano_mes_inicio, 'ano_mes_fim': ano_mes_fim}
    estado.save()
    return (
        "✅ Consumo Específico calculado!\n\n"
        f"Agora calculando o **Custo Variável Adicionado** de **{ano_mes_inicio}** a "
        f"**{ano_mes_fim}** em segundo plano..."
    )


def _etapa_ce_processando_custo_variavel(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return f"❌ Deu erro ao calcular o Custo Variável Adicionado: {mensagem_erro}"

    if status == 'concluido':
        # 🌟 NOVO: em vez de encerrar direto, pergunta se quer também
        # atualizar os indicadores de Consumo Específico no SPS (ação
        # equivalente a TbFluxoConsumoPadraoAdmin.update_indicador_geral,
        # só que pra TODOS os registros, não só os selecionados).
        estado.etapa_atual = 'ce_confirmar_indicadores'
        estado.dados_coletados = {}
        estado.save()
        return (
            "✅ Custo Variável Adicionado calculado com sucesso!\n\n"
            "Quer que eu também atualize os **indicadores de Consumo Específico** (Consumo Padrão "
            "nos Fluxos de Produção e Consumo Específico nos Equipamentos) e **Custo Variável "
            "Adicionado** (Equipamentos) no SPS? (Sim / Não)"
        )

    from custo_ferbasa.tasks import procedure_ainda_rodando_no_banco
    ainda_rodando = procedure_ainda_rodando_no_banco('custo_variavel_adicionado_periodo')
    if ainda_rodando is False:
        _encerrar_fluxo(estado)
        return (
            "⚠️ Não encontrei mais o cálculo do Custo Variável Adicionado rodando no banco, mas "
            "ele também não chegou a terminar -- provavelmente foi interrompido (por exemplo, o "
            "servidor ou o worker do Celery reiniciou no meio). Se quiser, pode iniciar de novo."
        )
    return (
        "⏳ Ainda calculando o **Custo Variável Adicionado**. Se quiser, pode clicar em "
        "\"Encerrar Ação\" -- isso só para de acompanhar por aqui, o cálculo em si continua "
        "rodando normalmente até terminar."
    )


def _etapa_ce_confirmar_indicadores(estado, texto):
    """
    🌟 NOVO: trata a resposta sim/não pra pergunta de atualizar os
    indicadores de Consumo Específico no SPS, feita logo depois que o
    Custo Variável Adicionado termina. Dispara a primeira das 3 etapas
    em sequência (Fluxo Consumo Padrão -> Equipamentos Consumo
    Específico -> Custo Item Preço), cada uma só começando depois que a
    anterior termina.
    """
    resposta = (texto or '').strip().lower()

    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não atualizei os indicadores."

    from custo_ferbasa.tasks import atualizar_indicadores_consumo_padrao_de_chat_celery
    resultado_async = atualizar_indicadores_consumo_padrao_de_chat_celery.delay(estado.usuario_id)
    estado.etapa_atual = 'ce_processando_indicador_fluxo'
    estado.dados_coletados = {'celery_task_id': resultado_async.id}
    estado.save()
    return "⏳ Atualizando os indicadores de Consumo Específico (Fluxo Consumo Padrão) em segundo plano..."


def _etapa_ce_processando_indicador_fluxo(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return f"❌ Deu erro ao atualizar os indicadores de Consumo Específico (Fluxo Consumo Padrão): {mensagem_erro}"

    if status == 'concluido':
        from custo_ferbasa.tasks import atualizar_indicadores_equipamentos_consumo_especifico_de_chat_celery
        resultado_async = atualizar_indicadores_equipamentos_consumo_especifico_de_chat_celery.delay(estado.usuario_id)
        estado.etapa_atual = 'ce_processando_indicador_equipamentos'
        estado.dados_coletados = {'celery_task_id': resultado_async.id}
        estado.save()
        return (
            "✅ Indicadores de Consumo Específico (Fluxo Consumo Padrão) atualizados!\n\n"
            "⏳ Agora atualizando a **Atualização Indicador de Consumo Específico nos Equipamentos "
            "do SPS** em segundo plano..."
        )

    from custo_ferbasa.tasks import celery_task_ainda_ativa
    ainda_ativa = celery_task_ainda_ativa(dados.get('celery_task_id'))
    if ainda_ativa is False:
        _encerrar_fluxo(estado)
        return (
            "⚠️ Não encontrei mais nenhum processamento em andamento pra atualização dos "
            "indicadores (Fluxo Consumo Padrão), mas ela também não chegou a terminar -- "
            "provavelmente foi interrompida (por exemplo, o servidor ou o worker do Celery "
            "reiniciou no meio). Se quiser, pode iniciar de novo."
        )
    return (
        "⏳ Ainda atualizando os indicadores de Consumo Específico (Fluxo Consumo Padrão). Se "
        "quiser, pode clicar em \"Encerrar Ação\" -- isso só para de acompanhar por aqui, a "
        "atualização em si continua rodando normalmente até terminar."
    )


def _etapa_ce_processando_indicador_equipamentos(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return (
            "❌ Deu erro na **Atualização Indicador de Consumo Específico nos Equipamentos do "
            f"SPS**: {mensagem_erro}"
        )

    if status == 'concluido':
        from custo_ferbasa.tasks import atualizar_custo_variavel_adicionado_item_preco_de_chat_celery
        resultado_async = atualizar_custo_variavel_adicionado_item_preco_de_chat_celery.delay(estado.usuario_id)
        estado.etapa_atual = 'ce_processando_custo_item_preco'
        estado.dados_coletados = {'celery_task_id': resultado_async.id}
        estado.save()
        return (
            "✅ **Atualização Indicador de Consumo Específico nos Equipamentos do SPS** concluída!\n\n"
            "⏳ Agora a **Atualização do Custo Variável Adicionado nos Equipamentos do SPS** em "
            "segundo plano..."
        )

    from custo_ferbasa.tasks import celery_task_ainda_ativa
    ainda_ativa = celery_task_ainda_ativa(dados.get('celery_task_id'))
    if ainda_ativa is False:
        _encerrar_fluxo(estado)
        return (
            "⚠️ Não encontrei mais nenhum processamento em andamento pra **Atualização Indicador "
            "de Consumo Específico nos Equipamentos do SPS**, mas ela também não chegou a terminar "
            "-- provavelmente foi interrompida (por exemplo, o servidor ou o worker do Celery "
            "reiniciou no meio). Se quiser, pode iniciar de novo."
        )
    return (
        "⏳ Ainda na **Atualização Indicador de Consumo Específico nos Equipamentos do SPS**. Se "
        "quiser, pode clicar em \"Encerrar Ação\" -- isso só para de acompanhar por aqui, a "
        "atualização em si continua rodando normalmente até terminar."
    )


def _etapa_ce_processando_custo_item_preco(estado, texto):
    dados = estado.dados_coletados or {}
    status = dados.get('status_importacao_cf')

    if status == 'erro':
        mensagem_erro = dados.get('mensagem_importacao_cf', 'motivo não especificado')
        _encerrar_fluxo(estado)
        return f"❌ Deu erro na **Atualização do Custo Variável Adicionado nos Equipamentos do SPS**: {mensagem_erro}"

    if status == 'concluido':
        _encerrar_fluxo(estado)
        return (
            "✅ **Atualização do Custo Variável Adicionado nos Equipamentos do SPS** concluída com "
            "sucesso! Todas as etapas foram concluídas."
        )

    from custo_ferbasa.tasks import celery_task_ainda_ativa
    ainda_ativa = celery_task_ainda_ativa(dados.get('celery_task_id'))
    if ainda_ativa is False:
        _encerrar_fluxo(estado)
        return (
            "⚠️ Não encontrei mais nenhum processamento em andamento pra **Atualização do Custo "
            "Variável Adicionado nos Equipamentos do SPS**, mas ela também não chegou a terminar "
            "-- provavelmente foi interrompida (por exemplo, o servidor ou o worker do Celery "
            "reiniciou no meio). Se quiser, pode iniciar de novo."
        )
    return (
        "⏳ Ainda na **Atualização do Custo Variável Adicionado nos Equipamentos do SPS**. Se "
        "quiser, pode clicar em \"Encerrar Ação\" -- isso só para de acompanhar por aqui, a "
        "atualização em si continua rodando normalmente até terminar."
    )


# =======================================================================
# 🌟 NOVO: fluxo GENÉRICO de confirmação pra "ferramentas" (tool calling
# -- ver FERRAMENTAS em agents.py). Diferente de todos os fluxos acima
# (cada um escrito à mão, com suas próprias etapas), esse aqui serve
# pra QUALQUER ferramenta nova que precise de confirmação antes de
# gravar -- não precisa escrever uma etapa nova pra cada ferramenta,
# só registrar a ferramenta em FERRAMENTAS (agents.py) com uma função
# "preparar" (valida e monta a mensagem de confirmação) e uma "executar"
# (grava de verdade, só chamada depois do "Sim").
# =======================================================================
FLUXO_TOOL_CALL = 'tool_call_generico'


def iniciar_fluxo_tool_call(usuario, nome_ferramenta, dados, mensagem_confirmacao):
    """
    Guarda qual ferramenta foi escolhida pelo LLM e os dados já
    validados (preparados pela própria ferramenta, em agents.py), e
    espera sim/não antes de executar de verdade.
    """
    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_TOOL_CALL
    estado.etapa_atual = 'tc_confirmar'
    estado.dados_coletados = {'nome_ferramenta': nome_ferramenta, 'dados': dados}
    estado.save()
    return mensagem_confirmacao


def _processar_tool_call(estado, texto):
    if estado.etapa_atual == 'tc_confirmar':
        return _etapa_tc_confirmar(estado, texto)
    _encerrar_fluxo(estado)
    return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."


def _etapa_tc_confirmar(estado, texto):
    resposta = (texto or '').strip().lower()
    if resposta not in ('sim', 's', 'yes', 'y'):
        _encerrar_fluxo(estado)
        return "Ok, não fiz a alteração."

    dados_estado = estado.dados_coletados or {}
    nome_ferramenta = dados_estado.get('nome_ferramenta')
    dados = dados_estado.get('dados')
    _encerrar_fluxo(estado)

    # 🌟 Import tardio (dentro da função, não no topo do arquivo) --
    # evita import circular, já que agents.py importa várias funções
    # DESTE arquivo lá no topo dele.
    from .agents import FERRAMENTAS
    ferramenta = FERRAMENTAS.get(nome_ferramenta)
    if ferramenta is None or 'executar' not in ferramenta:
        return "Não consegui encontrar a ferramenta pra executar essa ação. Cancelei."
    try:
        return ferramenta['executar'](dados)
    except Exception as e:
        return f"Deu erro ao aplicar a mudança: {e}."


# =======================================================================
# Fluxo: Equipamentos -- "Criar novo equipamento" (1ª etapa, pra validar)
#
# 1) pergunta se o usuário quer criar o novo equipamento a partir do CLONE
#    de um equipamento que já existe no cenário ativo (Sim / Não);
# 2) Sim -> mostra uma grade de cartões (imagem, código, descrição e, no
#    canto, a qtde de ordens de produção) -- o marcador [LISTA_EQUIPAMENTOS]
#    faz o chat_view buscar os dados e o front-end desenhar a grade; o
#    círculo de cada cartão abre uma janela com os detalhes das ordens;
# 3) o usuário clica num cartão -> esta etapa registra a escolha e PÁRA.
#
# Os próximos passos (o "Não", e o que fazer com o clone escolhido) ainda
# serão desenvolvidos -- por enquanto não cria nada no banco.
# =======================================================================
FLUXO_EQUIPAMENTOS = 'equipamentos'


def iniciar_fluxo_criar_equipamento(usuario):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_EQUIPAMENTOS
    estado.etapa_atual = 'equ_criar_clone_pergunta'
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
    estado.save()

    numero = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    return (
        "Você quer criar o novo equipamento a partir do **clone** de um equipamento que já existe "
        f"no cenário **{numero}/{cenario.cen_nome}**? (Sim / Não)"
    )


def _processar_equipamentos(estado, texto):
    handler = _HANDLERS_EQUIPAMENTOS.get(estado.etapa_atual)
    if handler is None:
        # Estado inconsistente (não deveria acontecer) -- encerra por segurança
        _encerrar_fluxo(estado)
        return "Não consegui identificar em qual etapa estávamos. Cancelei o fluxo -- pode começar de novo se quiser."
    return handler(estado, texto)


def _etapa_equ_criar_clone_pergunta(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return (
            "Não entendi -- responde \"Sim\" pra criar a partir do clone de um equipamento que já existe, "
            "ou \"Não\" pra criar sem clone. (Sim / Não)"
        )

    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return (
            "Ok, sem clone. Criar um equipamento do zero (a partir do cadastro de equipamentos) é uma etapa "
            "que ainda vamos desenvolver depois -- por enquanto, não fiz nada."
        )

    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    if not TbEquipamentosCadastro.objects.filter(tbcenarios_id=dados.get('cenario_id')).exists():
        _encerrar_fluxo(estado)
        return "Não encontrei nenhum equipamento cadastrado no cenário ativo, então não há o que clonar. Não fiz nada."

    estado.etapa_atual = 'equ_criar_clone_escolher'
    estado.save()
    return (
        "Qual equipamento você quer clonar? O número no canto superior direito de cada cartão é a quantidade "
        "de ordens de produção que ele já tem -- clica nesse número pra ver os detalhes das ordens.\n\n"
        "[LISTA_EQUIPAMENTOS]"
    )


def _etapa_equ_criar_clone_escolher(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos
    dados = estado.dados_coletados or {}
    cadastro = TbEquipamentosCadastro.objects.filter(
        tbcenarios_id=dados.get('cenario_id'), equ_cad_codigo__iexact=texto.strip()
    ).first()
    if cadastro is None:
        return (
            "Não encontrei esse equipamento no cenário ativo. Clica num dos cartões, digita o código "
            "exatamente como aparece, ou \"Cancelar\" pra desistir.\n\n"
            "[LISTA_EQUIPAMENTOS]"
        )

    qtd_ordens = TbEquipamentos.objects.filter(equ_codigo_id=cadastro.id, tbcenarios_id=cadastro.tbcenarios_id).count()
    estado.etapa_atual = 'equ_criar_dados'
    estado.dados_coletados = {
        **dados, 'clone_cadastro_id': cadastro.id, 'clone_codigo': cadastro.equ_cad_codigo,
        'clone_descricao': cadastro.equ_cad_descricao, 'clone_qtd_ordens': qtd_ordens,
    }
    estado.save()
    return (
        f"Você escolheu clonar o equipamento **{cadastro.equ_cad_codigo}** ({cadastro.equ_cad_descricao}), "
        f"que tem **{qtd_ordens}** ordem(ns) de produção.\n\n"
        "Agora informa o **código** e a **descrição** do novo equipamento. Já vêm preenchidos com os do "
        "equipamento clonado -- é só ajustar. O código precisa ser diferente de qualquer outro do cenário.\n\n"
        + _equ_form_equipamento(cadastro.equ_cad_codigo, cadastro.equ_cad_descricao)
    )


# ---------------------------------------------------------------------
# Auxiliares do clone
# ---------------------------------------------------------------------
def _equ_marcador(nome, payload):
    """Marcador [NOME:<json em base64 url-safe>] que o chat_view transforma em formulário na tela."""
    import base64
    bruto = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    return f"[{nome}:{base64.urlsafe_b64encode(bruto).decode('ascii')}]"


def _equ_form_equipamento(codigo, descricao):
    from equipamentos.models import TbEquipamentosCadastro
    return _equ_marcador('FORM_EQUIPAMENTO', {
        'codigo': codigo, 'descricao': descricao,
        'max_codigo': TbEquipamentosCadastro._meta.get_field('equ_cad_codigo').max_length,
        'max_descricao': TbEquipamentosCadastro._meta.get_field('equ_cad_descricao').max_length,
    })


def _equ_json(texto):
    """O formulário do chat manda um JSON. Devolve dict, ou None se a mensagem não for isso."""
    try:
        valor = json.loads(texto)
    except (TypeError, ValueError):
        return None
    return valor if isinstance(valor, dict) else None


def _equ_tipos_producao_permitidos():
    """Os mesmos Tipos de Produção que o dropdown da tela de Ordens oferece: o limit_choices_to do próprio campo."""
    from equipamentos.models import TbEquipamentos
    campo = TbEquipamentos._meta.get_field('equ_tipo_producao')
    consulta = campo.remote_field.model._default_manager.all()
    limite = campo.get_limit_choices_to()
    if limite:
        consulta = consulta.complex_filter(limite)
    return [{'id': t.pk, 'nome': str(t)} for t in consulta]


def _equ_url_novo_tipo(usuario):
    """Endereço do "+" do Admin pra cadastrar um Tipo de Produção novo. None se o usuário não pode (ou não existe)."""
    from equipamentos.models import TbEquipamentos
    try:
        from django.urls import reverse
        meta = TbEquipamentos._meta.get_field('equ_tipo_producao').remote_field.model._meta
        if not usuario.has_perm(f'{meta.app_label}.add_{meta.model_name}'):
            return None
        return reverse(f'admin:{meta.app_label}_{meta.model_name}_add')
    except Exception:
        return None


def _equ_form_ordens(estado, ordens_form):
    """ordens_form: [{'ordem': n, 'descricao': str, 'tipo_id': id|None}]"""
    from equipamentos.models import TbEquipamentos
    return _equ_marcador('FORM_ORDENS', {
        'ordens': ordens_form,
        'tipos': _equ_tipos_producao_permitidos(),
        'url_novo_tipo': _equ_url_novo_tipo(estado.usuario),
        'max_descricao': TbEquipamentos._meta.get_field('equ_ordem_descricao').max_length,
    })


def _equ_ordens_do_clone(dados):
    from equipamentos.models import TbEquipamentos
    return list(TbEquipamentos.objects.filter(
        equ_codigo_id=dados.get('clone_cadastro_id'), tbcenarios_id=dados.get('cenario_id')
    ).order_by('equ_ordem_codigo'))


def _equ_copiar_campos(origem, **sobrescrever):
    """
    Cria (SEM salvar) um registro novo igual ao `origem`, campo a campo, trocando só o que vier em `sobrescrever`
    (chaves no formato attname: 'campo_id' pra chave estrangeira). Genérico de propósito: se um dia o model ganhar
    um campo novo, ele já é copiado sozinho. Chave primária não é copiada, `id_origem` (usado só na duplicação de
    cenário) fica vazio, e arquivos/imagens apontam pro mesmo arquivo.
    """
    from django.db.models import FileField
    dados = {}
    for campo in origem._meta.concrete_fields:
        if campo.primary_key or campo.attname in sobrescrever:
            continue
        if campo.name == 'id_origem':
            dados[campo.attname] = None
            continue
        valor = getattr(origem, campo.attname)
        if isinstance(campo, FileField):
            valor = (valor.name if valor else None)
            if valor is None and not campo.null:
                valor = ''
        dados[campo.attname] = valor
    dados.update(sobrescrever)
    return origem.__class__(**dados)


def _equ_clonar_filhas(ModeloFilha, pai_origem, pai_novo):
    """
    Salvar o pai novo faz o banco criar as linhas filhas sozinho (procedure, com o valor inicial em todos os
    períodos). Aqui elas são trocadas por CÓPIAS EXATAS das filhas do pai de origem (valor por período), sem chamar
    o save() das filhas (que reexecutaria as procedures). Se a origem não tiver filha, deixa o que o banco criou.
    """
    copias = [
        _equ_copiar_campos(filha, mae_id=pai_novo.pk, tbcenarios_id=pai_novo.tbcenarios_id)
        for filha in ModeloFilha.objects.filter(mae_id=pai_origem.pk).order_by('dau_order')
    ]
    if not copias:
        return 0
    ModeloFilha.objects.filter(mae_id=pai_novo.pk).delete()
    ModeloFilha.objects.bulk_create(copias)
    return len(copias)


def _equ_descricao_consumo_padrao(equip_from, equip_to):
    """Mesma montagem do clean() do TbFluxoConsumoPadrao: CÓDIGO/ordem --> CÓDIGO/ordem."""
    return (str(equip_from.equ_codigo) + '/' + str(equip_from.equ_ordem_codigo) + ' --> '
            + str(equip_to.equ_codigo) + '/' + str(equip_to.equ_ordem_codigo))


def _equ_resumo_previo(dados, ordens):
    """Contagens e produtos afetados, pro resumo antes do "Sim"."""
    from django.db.models import Q
    from equipamentos.models import TbEquipamentosConsumoEspecifico
    from fluxos.models import TbFluxoConsumoPadrao
    ids = [o.id for o in ordens]
    itens = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id__in=ids).count()
    consumos = TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=dados.get('cenario_id')).filter(
        Q(flu_con_pad_from_equipamento_id__in=ids) | Q(flu_con_pad_to_equipamento_id__in=ids)).count()
    produtos = set()
    for ordem in ordens:
        produtos |= ordem._ids_produtos_usados()
    from produtos.models import TbProdutos
    codigos = sorted(TbProdutos.objects.filter(id__in=produtos).values_list('id', 'pro_codigo'), key=lambda x: x[1])
    return {'itens': itens, 'consumos': consumos, 'produtos': [{'id': i, 'codigo': c} for i, c in codigos]}


def _equ_texto_resumo(dados, resumo):
    plano = dados.get('plano_ordens') or []
    linhas = [
        f"Vou criar, a partir do clone de **{dados['clone_codigo']}**:",
        f"- o equipamento **{dados['novo_codigo']}** ({dados['nova_descricao']}), com a mesma configuração e os "
        "mesmos valores por período;",
    ]
    if plano:
        linhas.append(f"- **{len(plano)}** ordem(ns) de produção: " + "; ".join(
            f"{p['ordem']} - {p['descricao']} ({p['tipo_nome']})" for p in plano) + ";")
        linhas.append(f"- **{resumo['itens']}** item(ns) de consumo específico das ordens e **{resumo['consumos']}** "
                      "consumo(s) padrão (cópias dos que usam as ordens clonadas);")
        if resumo['produtos']:
            codigos = ", ".join(p['codigo'] for p in resumo['produtos'][:8])
            mais = f" e mais {len(resumo['produtos']) - 8}" if len(resumo['produtos']) > 8 else ""
            linhas.append(f"- e depois os fluxos de **{len(resumo['produtos'])}** produto(s): {codigos}{mais} "
                          "(a quantidade de fluxos de cada um vai aumentar).")
        else:
            linhas.append("- nenhum produto usa as ordens clonadas nos fluxos, então não há fluxos a gerar.")
    return "\n".join(linhas) + "\n\nConfirma? (Sim / Não)"


# ---------------------------------------------------------------------
# Etapa: código e descrição do novo equipamento
# ---------------------------------------------------------------------
def _etapa_equ_criar_dados(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    entrada = _equ_json(texto)
    if entrada is None or not isinstance(entrada.get('codigo'), str) or not isinstance(entrada.get('descricao'), str):
        return (
            "Não entendi -- preenche o código e a descrição no formulário abaixo e clica em Confirmar.\n\n"
            + _equ_form_equipamento(dados.get('clone_codigo', ''), dados.get('clone_descricao', ''))
        )

    # Mesma intenção do clean() do cadastro: código e descrição em maiúsculas e "/" trocado por "-" na descrição.
    codigo = entrada['codigo'].strip().upper()
    descricao = entrada['descricao'].strip().upper().replace('/', '-')
    max_codigo = TbEquipamentosCadastro._meta.get_field('equ_cad_codigo').max_length
    max_descricao = TbEquipamentosCadastro._meta.get_field('equ_cad_descricao').max_length

    erro = None
    if not codigo:
        erro = "O código não pode ficar vazio."
    elif len(codigo) > max_codigo:
        erro = f"O código pode ter no máximo {max_codigo} caracteres (o seu tem {len(codigo)})."
    elif not descricao:
        erro = "A descrição não pode ficar vazia."
    elif len(descricao) > max_descricao:
        erro = f"A descrição pode ter no máximo {max_descricao} caracteres (a sua tem {len(descricao)})."
    elif TbEquipamentosCadastro.objects.filter(tbcenarios_id=dados.get('cenario_id'), equ_cad_codigo__iexact=codigo).exists():
        erro = f"Já existe o equipamento **{codigo}** nesse cenário. Escolhe outro código."
    if erro:
        return erro + "\n\n" + _equ_form_equipamento(entrada['codigo'].strip(), entrada['descricao'].strip())

    dados = {**dados, 'novo_codigo': codigo, 'nova_descricao': descricao}
    ordens = _equ_ordens_do_clone(dados)
    if not ordens:
        # Equipamento sem nenhuma ordem: só o cadastro (e a filha dele) é clonado.
        dados['plano_ordens'] = []
        estado.etapa_atual = 'equ_criar_confirmar'
        estado.dados_coletados = dados
        estado.save()
        return _equ_texto_resumo(dados, {'itens': 0, 'consumos': 0, 'produtos': []})

    estado.etapa_atual = 'equ_criar_ordens'
    estado.dados_coletados = dados
    estado.save()
    return (
        f"Agora as **{len(ordens)}** ordem(ns) de produção do novo equipamento **{codigo}**. Pra cada uma, ajusta a "
        "descrição e escolhe o Tipo de Produção (já vêm os da ordem original). Se o tipo que você precisa ainda não "
        "existe, o botão **+** cadastra um novo.\n\n"
        + _equ_form_ordens(estado, [
            {'ordem': o.equ_ordem_codigo, 'descricao': o.equ_ordem_descricao, 'tipo_id': o.equ_tipo_producao_id}
            for o in ordens])
    )


# ---------------------------------------------------------------------
# Etapa: descrição e Tipo de Produção de cada ordem nova
# ---------------------------------------------------------------------
def _etapa_equ_criar_ordens(estado, texto):
    from equipamentos.models import TbEquipamentos
    dados = estado.dados_coletados or {}
    originais = _equ_ordens_do_clone(dados)
    por_ordem = {o.equ_ordem_codigo: o for o in originais}
    padrao = [{'ordem': o.equ_ordem_codigo, 'descricao': o.equ_ordem_descricao, 'tipo_id': o.equ_tipo_producao_id}
              for o in originais]

    entrada = _equ_json(texto)
    lista = entrada.get('ordens') if entrada else None
    if not isinstance(lista, list):
        return ("Não entendi -- preenche as ordens no formulário abaixo e clica em Confirmar.\n\n"
                + _equ_form_ordens(estado, padrao))

    tipos = {t['id']: t['nome'] for t in _equ_tipos_producao_permitidos()}
    max_descricao = TbEquipamentos._meta.get_field('equ_ordem_descricao').max_length
    digitado, plano, erros, vistos = [], [], [], set()
    for item in lista:
        if not isinstance(item, dict):
            continue
        try:
            ordem = int(item.get('ordem'))
        except (TypeError, ValueError):
            continue
        descricao = str(item.get('descricao') or '').strip()
        try:
            tipo_id = int(item.get('tipo_id'))
        except (TypeError, ValueError):
            tipo_id = None
        digitado.append({'ordem': ordem, 'descricao': descricao, 'tipo_id': tipo_id})
        if ordem not in por_ordem or ordem in vistos:
            erros.append(f"A ordem {ordem} não existe no equipamento clonado.")
            continue
        vistos.add(ordem)
        if not descricao:
            erros.append(f"A descrição da ordem {ordem} não pode ficar vazia.")
        elif len(descricao) > max_descricao:
            erros.append(f"A descrição da ordem {ordem} pode ter no máximo {max_descricao} caracteres.")
        if tipo_id not in tipos:
            erros.append(f"Escolhe um Tipo de Produção válido para a ordem {ordem}.")
        plano.append({'origem_id': por_ordem[ordem].id, 'ordem': ordem, 'descricao': descricao,
                      'tipo_id': tipo_id, 'tipo_nome': tipos.get(tipo_id, '')})
    faltando = sorted(set(por_ordem) - vistos)
    if faltando:
        erros.append("Faltou preencher a(s) ordem(ns): " + ", ".join(str(n) for n in faltando) + ".")
    if erros:
        reapresentar = sorted(digitado, key=lambda x: x['ordem']) if not faltando and digitado else padrao
        return "\n".join(f"- {e}" for e in erros) + "\n\n" + _equ_form_ordens(estado, reapresentar)

    plano.sort(key=lambda p: p['ordem'])
    dados = {**dados, 'plano_ordens': plano}
    resumo = _equ_resumo_previo(dados, originais)
    estado.etapa_atual = 'equ_criar_confirmar'
    estado.dados_coletados = {**dados, 'produtos_afetados': resumo['produtos']}
    estado.save()
    return _equ_texto_resumo(dados, resumo)


# ---------------------------------------------------------------------
# Etapa: confirmação e gravação
# ---------------------------------------------------------------------
def _equ_executar_clone(estado):
    """
    Grava tudo (cadastro + filha, ordens + filhas + itens + filhas, vínculo com os produtos e consumos padrão +
    filhas). Chamada dentro de transaction.atomic(): qualquer erro desfaz tudo. Devolve um dicionário com as contagens.
    """
    from django.db.models import Q
    from equipamentos.models import (
        TbEquipamentosCadastro, TbEquipamentos, TbEquipamentosCadastroDaugther, TbEquipamentosDaugther,
        TbEquipamentosConsumoEspecifico, TbEquipamentosConsumoEspecificoDaugther,
    )
    from fluxos.models import TbFluxoConsumoPadrao, TbFluxoConsumoPadraoDaugther

    dados = estado.dados_coletados or {}
    cenario_id = dados['cenario_id']
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id != cenario_id:
        raise ValueError("o cenário ativo mudou desde que você começou. Começa de novo no cenário certo")

    origem = TbEquipamentosCadastro.objects.get(id=dados['clone_cadastro_id'], tbcenarios_id=cenario_id)
    if TbEquipamentosCadastro.objects.filter(tbcenarios_id=cenario_id, equ_cad_codigo__iexact=dados['novo_codigo']).exists():
        raise ValueError(f"já existe o equipamento {dados['novo_codigo']} nesse cenário")

    # 1) cadastro + filha (valores por período)
    novo_cadastro = _equ_copiar_campos(origem, equ_cad_codigo=dados['novo_codigo'], equ_cad_descricao=dados['nova_descricao'])
    novo_cadastro.save()
    _equ_clonar_filhas(TbEquipamentosCadastroDaugther, origem, novo_cadastro)

    # 2) ordens (+ filhas, + itens de consumo específico e as filhas deles) e vínculo com os produtos
    ordens_origem = {o.id: o for o in _equ_ordens_do_clone(dados)}
    mapa = {}                 # id da ordem antiga -> ordem nova
    produtos_afetados = set()
    qtd_itens = 0
    for item in dados.get('plano_ordens') or []:
        antiga = ordens_origem[item['origem_id']]
        # equ_e_clone_de NUNCA aponta pra ordem antiga: o gerador de fluxos trataria as duas como a mesma alternativa.
        nova = _equ_copiar_campos(
            antiga, equ_codigo_id=novo_cadastro.pk, equ_ordem_descricao=item['descricao'],
            equ_tipo_producao_id=item['tipo_id'], equ_e_clone_de_id=None)
        nova.save()
        nova.equ_codigo = novo_cadastro      # evita nova consulta ao montar a descrição dos consumos padrão
        _equ_clonar_filhas(TbEquipamentosDaugther, antiga, nova)
        for it in TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=antiga.pk).order_by('id'):
            novo_item = _equ_copiar_campos(it, equ_con_esp_equipamento_id=nova.pk)
            novo_item.save()
            _equ_clonar_filhas(TbEquipamentosConsumoEspecificoDaugther, it, novo_item)
            qtd_itens += 1
        usados = antiga._ids_produtos_usados()
        # O gerador de fluxos só enxerga as ordens vinculadas ao produto (equ_produtos): sem isso as ordens novas
        # ficariam de fora. Também é o que mantém o "Escolhidos Igual Fluxos" em Sim depois.
        nova.equ_produtos.set(usados)
        produtos_afetados |= usados
        mapa[antiga.pk] = nova

    # "É Clone Da Ordem": só dentro do equipamento novo (a ordem 3 clone da 1 continua clone da 1, agora a nova).
    for antiga_id, nova in mapa.items():
        alvo = ordens_origem[antiga_id].equ_e_clone_de_id
        if alvo in mapa:
            TbEquipamentos.objects.filter(pk=nova.pk).update(equ_e_clone_de_id=mapa[alvo].pk)

    # 3) consumos padrão que usam as ordens antigas -> cópias que usam as novas (+ filhas)
    qtd_consumos = 0
    if mapa:
        consumos = (
            TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=cenario_id)
            .filter(Q(flu_con_pad_from_equipamento_id__in=list(mapa)) | Q(flu_con_pad_to_equipamento_id__in=list(mapa)))
            .select_related('flu_con_pad_from_equipamento__equ_codigo', 'flu_con_pad_to_equipamento__equ_codigo')
            .order_by('id')
        )
        for cp in consumos:
            de = mapa.get(cp.flu_con_pad_from_equipamento_id) or cp.flu_con_pad_from_equipamento
            para = mapa.get(cp.flu_con_pad_to_equipamento_id) or cp.flu_con_pad_to_equipamento
            descricao = _equ_descricao_consumo_padrao(de, para)
            if TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=cenario_id, flu_con_pad_descricao=descricao).exists():
                raise ValueError(f"já existe o consumo padrão {descricao}")
            novo_cp = _equ_copiar_campos(
                cp, flu_con_pad_from_equipamento_id=de.pk, flu_con_pad_to_equipamento_id=para.pk,
                flu_con_pad_descricao=descricao)
            novo_cp.save()
            _equ_clonar_filhas(TbFluxoConsumoPadraoDaugther, cp, novo_cp)
            qtd_consumos += 1

    return {'cadastro_id': novo_cadastro.pk, 'ordens': len(mapa), 'itens': qtd_itens, 'consumos': qtd_consumos,
            'produtos': produtos_afetados}


def _etapa_equ_criar_confirmar(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra criar ou \"Não\" pra desistir. (Sim / Não)"
    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return "Ok, não criei nada."

    dados = estado.dados_coletados or {}
    try:
        with transaction.atomic():
            feito = _equ_executar_clone(estado)
    except Exception as erro:
        _encerrar_fluxo(estado)
        return f"Não criei nada -- deu erro e eu desfiz tudo: {erro}."

    from produtos.models import TbProdutos
    fila = [{'id': pid, 'codigo': codigo} for pid, codigo in sorted(
        TbProdutos.objects.filter(id__in=feito['produtos']).values_list('id', 'pro_codigo'), key=lambda x: x[1])]
    criado = (
        f"✅ Equipamento **{dados['novo_codigo']}** criado a partir do clone de **{dados['clone_codigo']}**: "
        f"**{feito['ordens']}** ordem(ns) de produção, **{feito['itens']}** item(ns) de consumo específico e "
        f"**{feito['consumos']}** consumo(s) padrão."
    )
    if not fila:
        _encerrar_fluxo(estado)
        return criado + "\n\nNenhum produto usa as ordens clonadas nos fluxos, então não há fluxos a gerar."

    total_produtos = len(fila)
    estado.dados_coletados = {
        'cenario_id': dados['cenario_id'], 'novo_codigo': dados['novo_codigo'], 'clone_codigo': dados['clone_codigo'],
        'resumo_criado': criado, 'fila': fila, 'total_produtos': total_produtos, 'concluidos': 0, 'resultados': [],
    }
    _equ_iniciar_proximo_produto(estado)
    return (
        criado + f"\n\nAgora estou gerando os fluxos dos **{total_produtos}** produto(s) que usam as ordens clonadas, um de cada "
        "vez. Pode demorar; acompanho por aqui.\n\n" + _equ_texto_andamento(estado.dados_coletados)
    )


# ---------------------------------------------------------------------
# Etapa: gera os fluxos de cada produto afetado, um de cada vez
# ---------------------------------------------------------------------
def _equ_texto_andamento(dados):
    return (f"Gerando os fluxos do produto **{dados.get('produto_codigo', '')}** "
            f"({dados.get('concluidos', 0) + 1} de {dados.get('total_produtos', 0)}).")


def _equ_iniciar_proximo_produto(estado):
    """Tira o próximo produto da fila e dispara a task que já existe (a mesma de "Atualizar Fluxos por Produto")."""
    from fluxos.models import TbFluxoProducao
    import uuid
    from datetime import datetime, timezone as _tz
    dados = dict(estado.dados_coletados or {})
    dados['fila'] = list(dados['fila'])      # cópia: quem chamou ainda usa a lista original (ex.: pra contar os produtos)
    atual = dados['fila'].pop(0)
    for chave in ('status', 'mensagem', 'total_criados', 'total_removidos'):
        dados.pop(chave, None)
    task_id = str(uuid.uuid4())              # id escolhido aqui: é por ele que dá pra saber depois se a task morreu
    dados.update({
        'produto_id': atual['id'], 'produto_codigo': atual['codigo'], 'status': 'processando',
        'produto_antes': TbFluxoProducao.objects.filter(flu_pro_produto_id=atual['id']).count(),
        'task_ids': [task_id], 'disparado_em': datetime.now(_tz.utc).isoformat(),
    })
    estado.etapa_atual = 'equ_criar_fluxos_aguardando'
    estado.dados_coletados = dados
    estado.save()

    from fluxos.tasks import gerar_fluxos_produto_celery
    produto_id, usuario_id = atual['id'], estado.usuario_id
    # on_commit: a task só começa depois de o estado acima estar gravado (ela confere o produto_id no estado).
    transaction.on_commit(lambda: gerar_fluxos_produto_celery.apply_async((produto_id, usuario_id), task_id=task_id))


def _equ_geracao_morreu(dados):
    """
    True se a task do produto atual morreu SEM avisar o chat (a task grava o resultado no estado no fim; se o worker
    for encerrado no meio -- por exemplo pelo sistema operacional, por falta de memória -- ela nunca grava e o chat
    ficaria em "Gerando..." pra sempre). Duas fontes: a falha que o próprio Celery registra na tabela de resultados
    (aparece na hora) e o detector conservador que os outros fluxos já usam (>= 10 min sem a task em nenhum worker).
    """
    task_ids = dados.get('task_ids') or []
    if not task_ids:
        return False
    try:
        from django_celery_results.models import TaskResult
        if TaskResult.objects.filter(task_id__in=task_ids, status__in=('FAILURE', 'REVOKED')).exists():
            return True
    except Exception:
        pass
    return _todas_tasks_mortas(task_ids, dados.get('disparado_em')) is True


def _etapa_equ_criar_fluxos_aguardando(estado, texto):
    from fluxos.models import TbFluxoProducao
    dados = dict(estado.dados_coletados or {})
    status = dados.get('status')
    if status == 'processando':
        if not _equ_geracao_morreu(dados):
            return _equ_texto_andamento(dados)
        # A task morreu sem avisar: conta como erro desse produto e o chat segue pro próximo (ou encerra).
        status = 'erro'
        dados['mensagem'] = ("a geração parou antes de terminar -- o processo do Celery foi encerrado "
                             "(provavelmente por falta de memória)")

    # O produto atual terminou (com sucesso, com erro, ou sem resposta): registra e segue pro próximo.
    depois = TbFluxoProducao.objects.filter(flu_pro_produto_id=dados.get('produto_id')).count()
    resultado = {
        'codigo': dados.get('produto_codigo', ''), 'antes': dados.get('produto_antes', 0), 'depois': depois,
        'criados': dados.get('total_criados', 0), 'removidos': dados.get('total_removidos', 0), 'erro': None,
    }
    if status == 'erro':
        resultado['erro'] = dados.get('mensagem', 'erro desconhecido')
    elif status != 'concluido':
        resultado['erro'] = 'não recebi a resposta da geração dos fluxos'
    dados['resultados'] = list(dados.get('resultados') or []) + [resultado]
    dados['concluidos'] = dados.get('concluidos', 0) + 1
    estado.dados_coletados = dados

    if dados.get('fila'):
        _equ_iniciar_proximo_produto(estado)
        return _equ_texto_andamento(estado.dados_coletados)

    resumo = dados['resumo_criado']
    resultados = dados['resultados']
    linhas = []
    for r in resultados:
        if r['erro']:
            linhas.append(f"- **{r['codigo']}**: ⚠️ não consegui gerar ({r['erro']})")
        else:
            linhas.append(f"- **{r['codigo']}**: {r['antes']} → {r['depois']} fluxo(s) ({r['depois'] - r['antes']:+d})")
    total = sum(r['depois'] - r['antes'] for r in resultados if not r['erro'])
    texto_final = resumo + "\n\nFluxos por produto:\n" + "\n".join(linhas) + f"\n\nTotal: **{total:+d}** fluxo(s)."
    if any(r['erro'] for r in resultados):
        texto_final += ("\n\nOs produtos com ⚠️ podem ser refeitos depois em Fluxos de Produção > Atualizar Fluxos de "
                        "Produção por Produto.")
    if any(not r['erro'] and r['criados'] for r in resultados):
        return _fp_io_perguntar_ou_encerrar(estado, dados.get('cenario_id'), None, texto_final)
    _encerrar_fluxo(estado)
    return texto_final


# ---------------------------------------------------------------------
# 🌟 NOVO: "Equipamentos - Criar Nova Ordem de Produção" (clone de equipamento/ordem: CRIA uma ordem nova ou ALTERA uma
# existente, assumindo as informações da ordem clonada).
#
# Sequência: equipamento de destino -> ordem (as existentes + a sugerida; repetir um número existente = ALTERAR, com
# aviso) -> equipamento a clonar -> ordem a clonar -> tipo de produção e descrição -> produtos que a ordem atende ->
# consumos padrão (os que saem da ordem clonada: clonar ou não, com o valor inicial do indicador) -> resumo e "Sim".
# NADA é gravado antes do "Sim" final: aí vai tudo numa transação só (tudo-ou-nada). Depois, a mesma geração de fluxos
# por produto do clone de equipamento (etapa equ_criar_fluxos_aguardando) e a pergunta do I/O.
#
# CRIANDO: copia a ordem inteira (campos + filhas por período) e os itens de consumo específico (+ filhas).
# ALTERANDO: muda só o tipo de produção, a descrição, os produtos e os consumos específicos (os atuais são APAGADOS e
# substituídos pelos da ordem clonada). WIP, valores iniciais, observação e valores por período da ordem não mudam.
# "É Clone Da Ordem" nunca é preenchido (marca "o mesmo recurso físico de outra ordem", caso raro).
# ---------------------------------------------------------------------
def _ord_fmt_valor(valor):
    from decimal import Decimal
    return f"{Decimal(str(valor)):.4f}".replace('.', ',')


def _ord_parse_valor(texto):
    """'2,5' / '2.5' / '1.234,5000' -> Decimal; None se não for um número válido pro campo (11 dígitos, 4 decimais, >= 0)."""
    from decimal import Decimal, InvalidOperation
    t = str(texto if texto is not None else '').strip().replace(' ', '')
    if not t:
        return None
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d.as_tuple().exponent < -4 or len(d.as_tuple().digits) + d.as_tuple().exponent > 7:
        return None
    return d


def _ord_ordens(cadastro_id, cenario_id):
    from equipamentos.models import TbEquipamentos
    return list(TbEquipamentos.objects.filter(equ_codigo_id=cadastro_id, tbcenarios_id=cenario_id)
                .select_related('equ_tipo_producao').order_by('equ_ordem_codigo'))


def _ord_botao_ordem(codigo, ordem):
    return {'valor': str(ordem.equ_ordem_codigo), 'tipo': 'existente',
            'rotulo': f"{codigo}/{ordem.equ_ordem_codigo} — {ordem.equ_ordem_descricao} ({ordem.equ_tipo_producao})"}


def iniciar_fluxo_criar_ordem(usuario):
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return "Você ainda não tem um cenário ativo escolhido. Acesse a tela de Cenários e ative um antes."
    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return "O cenário que estava ativo pra você não existe mais."

    from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos
    numero = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
    if not TbEquipamentosCadastro.objects.filter(tbcenarios_id=cenario.id).exists() or \
            not TbEquipamentos.objects.filter(tbcenarios_id=cenario.id).exists():
        return (f"Não encontrei equipamentos com ordens de produção no cenário **{numero}/{cenario.cen_nome}**, então "
                "não há de onde clonar. Não fiz nada.")

    estado = _get_estado(usuario)
    estado.fluxo_ativo = FLUXO_EQUIPAMENTOS
    estado.etapa_atual = 'ord_destino_equipamento'
    estado.dados_coletados = {'cenario_id': cenario.id, 'cenario_nome': cenario.cen_nome}
    estado.save()
    return (
        f"Vou criar uma nova ordem de produção (ou alterar uma que já existe) no cenário **{numero}/{cenario.cen_nome}**, "
        "clonando outra ordem. Primeiro: em qual equipamento fica a ordem que você quer criar ou alterar? O número no "
        "canto superior direito de cada cartão é a quantidade de ordens de produção dele.\n\n[LISTA_EQUIPAMENTOS]"
    )


def _ord_pedir_ordem_destino(estado, dados, cadastro):
    ordens = _ord_ordens(cadastro.id, dados['cenario_id'])
    proximo = (max(o.equ_ordem_codigo for o in ordens) + 1) if ordens else 1
    estado.etapa_atual = 'ord_destino_ordem'
    estado.dados_coletados = {**dados, 'destino_cadastro_id': cadastro.id, 'destino_codigo': cadastro.equ_cad_codigo,
                              'proximo_numero': proximo}
    estado.save()
    botoes = [_ord_botao_ordem(cadastro.equ_cad_codigo, o) for o in ordens]
    botoes.append({'valor': str(proximo), 'tipo': 'nova', 'rotulo': f"Nova ordem {cadastro.equ_cad_codigo}/{proximo}"})
    if ordens:
        corpo = (f"O equipamento **{cadastro.equ_cad_codigo}** — {cadastro.equ_cad_descricao} já tem {len(ordens)} ordem(ns). "
                 f"Escolha uma **ordem existente** pra ALTERAR, ou a **nova ordem sugerida ({proximo})**. Também pode "
                 "digitar outro número: se ele já existir, aquela ordem será alterada.")
    else:
        corpo = (f"O equipamento **{cadastro.equ_cad_codigo}** — {cadastro.equ_cad_descricao} ainda não tem nenhuma ordem. "
                 f"A primeira será a **{proximo}** (ou digite outro número).")
    return corpo + " Se quiser desistir, digite \"cancelar\".\n\n" + _equ_marcador('BOTOES', {'botoes': botoes})


def _etapa_ord_destino_equipamento(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    cadastro = TbEquipamentosCadastro.objects.filter(
        tbcenarios_id=dados.get('cenario_id'), equ_cad_codigo__iexact=texto.strip()).first()
    if cadastro is None:
        return ("Não encontrei esse equipamento no cenário ativo. Clica num dos cartões, digita o código exatamente "
                "como aparece, ou \"cancelar\" pra desistir.\n\n[LISTA_EQUIPAMENTOS]")
    return _ord_pedir_ordem_destino(estado, dados, cadastro)


def _etapa_ord_destino_ordem(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    cadastro = TbEquipamentosCadastro.objects.get(id=dados['destino_cadastro_id'])
    try:
        numero = int(texto.strip())
    except ValueError:
        numero = None
    if numero is None or numero < 1 or numero > 2147483647:
        return ("Não entendi -- clique em um dos botões ou digite o número da ordem (um inteiro maior que zero).\n\n"
                + _ord_pedir_ordem_destino(estado, dados, cadastro))
    existente = next((o for o in _ord_ordens(cadastro.id, dados['cenario_id']) if o.equ_ordem_codigo == numero), None)
    if existente is None:
        estado.dados_coletados = {**dados, 'numero': numero, 'existente': False, 'destino_ordem_id': None}
        estado.save()
        return _ord_pedir_origem_equipamento(estado, estado.dados_coletados)

    from equipamentos.models import TbEquipamentosConsumoEspecifico
    itens = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=existente.id).count()
    estado.etapa_atual = 'ord_alterar_confirmar'
    estado.dados_coletados = {**dados, 'numero': numero, 'existente': True, 'destino_ordem_id': existente.id}
    estado.save()
    return (
        f"Atenção: a ordem **{cadastro.equ_cad_codigo}/{numero}** já existe — {existente.equ_ordem_descricao} "
        f"({existente.equ_tipo_producao}). Se você seguir, eu vou **ALTERAR** essa ordem: o tipo de produção e a "
        "descrição (você poderá ajustar), os produtos que ela atende e os **consumos específicos** — os atuais "
        f"({itens} item(ns)) serão **apagados** e substituídos pelos da ordem que você vai clonar. O restante da ordem "
        "(WIP, valores iniciais, valores por período) não muda, e os consumos padrão que já saem dela e não existem na "
        "ordem clonada continuam como estão. Nada é gravado até a confirmação final. Quer continuar? (Sim / Não)"
    )


def _etapa_ord_alterar_confirmar(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    resposta = texto.strip().lower()
    dados = estado.dados_coletados or {}
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra alterar essa ordem ou \"Não\" pra escolher outra. (Sim / Não)"
    if resposta == 'sim':
        return _ord_pedir_origem_equipamento(estado, dados)
    return _ord_pedir_ordem_destino(estado, dados, TbEquipamentosCadastro.objects.get(id=dados['destino_cadastro_id']))


def _ord_pedir_origem_equipamento(estado, dados):
    estado.etapa_atual = 'ord_origem_equipamento'
    estado.dados_coletados = dados
    estado.save()
    alvo = f"{dados['destino_codigo']}/{dados['numero']}"
    acao = "alterada" if dados.get('existente') else "criada"
    return (f"Ordem **{alvo}** será {acao}. Agora escolha o equipamento que será **clonado** (a ordem dele serve de "
            "modelo).\n\n[LISTA_EQUIPAMENTOS]")


def _etapa_ord_origem_equipamento(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    cadastro = TbEquipamentosCadastro.objects.filter(
        tbcenarios_id=dados.get('cenario_id'), equ_cad_codigo__iexact=texto.strip()).first()
    if cadastro is None:
        return ("Não encontrei esse equipamento no cenário ativo. Clica num dos cartões, digita o código exatamente "
                "como aparece, ou \"cancelar\" pra desistir.\n\n[LISTA_EQUIPAMENTOS]")
    return _ord_pedir_ordem_origem(estado, dados, cadastro)


def _ord_pedir_ordem_origem(estado, dados, cadastro, aviso=''):
    ordens = _ord_ordens(cadastro.id, dados['cenario_id'])
    if not ordens:
        return (f"O equipamento **{cadastro.equ_cad_codigo}** não tem nenhuma ordem de produção, então não há o que "
                "clonar. Escolhe outro.\n\n[LISTA_EQUIPAMENTOS]")
    estado.etapa_atual = 'ord_origem_ordem'
    estado.dados_coletados = {**dados, 'origem_cadastro_id': cadastro.id, 'origem_codigo': cadastro.equ_cad_codigo}
    estado.save()
    return (aviso + f"Qual ordem do equipamento **{cadastro.equ_cad_codigo}** será clonada? Clique no botão (ou "
            "\"cancelar\" pra desistir).\n\n"
            + _equ_marcador('BOTOES', {'botoes': [_ord_botao_ordem(cadastro.equ_cad_codigo, o) for o in ordens]}))


def _etapa_ord_origem_ordem(estado, texto):
    from equipamentos.models import TbEquipamentosCadastro
    dados = estado.dados_coletados or {}
    cadastro = TbEquipamentosCadastro.objects.get(id=dados['origem_cadastro_id'])
    try:
        numero = int(texto.strip())
    except ValueError:
        numero = None
    ordem = next((o for o in _ord_ordens(cadastro.id, dados['cenario_id']) if o.equ_ordem_codigo == numero), None)
    if ordem is None:
        return _ord_pedir_ordem_origem(estado, dados, cadastro, "Não encontrei essa ordem. Escolha uma da lista.\n\n")
    if dados.get('existente') and ordem.id == dados.get('destino_ordem_id'):
        return _ord_pedir_ordem_origem(
            estado, dados, cadastro,
            f"A ordem **{cadastro.equ_cad_codigo}/{numero}** é a própria ordem que você quer alterar -- não dá pra clonar "
            "ela nela mesma. Escolha outra.\n\n")
    estado.dados_coletados = {**dados, 'origem_ordem_id': ordem.id, 'origem_ordem_numero': ordem.equ_ordem_codigo}
    estado.save()
    return _ord_pedir_dados(estado, estado.dados_coletados)


def _ord_pedir_dados(estado, dados):
    from equipamentos.models import TbEquipamentos
    base = TbEquipamentos.objects.get(id=dados['destino_ordem_id'] if dados.get('existente') else dados['origem_ordem_id'])
    linha = {'ordem': dados['numero'], 'descricao': base.equ_ordem_descricao, 'tipo_id': base.equ_tipo_producao_id}
    estado.etapa_atual = 'ord_dados'
    estado.dados_coletados = dados
    estado.save()
    de_onde = ("os valores ATUAIS da ordem" if dados.get('existente') else
               f"os da ordem clonada ({dados['origem_codigo']}/{dados['origem_ordem_numero']})")
    return (f"Agora o **tipo de produção** e a **descrição** da ordem **{dados['destino_codigo']}/{dados['numero']}**. Já "
            f"vêm preenchidos com {de_onde} -- é só ajustar. Se o tipo que você precisa não existe, use o + pra "
            "cadastrar um novo.\n\n" + _equ_form_ordens(estado, [linha]))


def _etapa_ord_dados(estado, texto):
    from equipamentos.models import TbEquipamentos
    dados = estado.dados_coletados or {}
    base = TbEquipamentos.objects.get(id=dados['destino_ordem_id'] if dados.get('existente') else dados['origem_ordem_id'])
    padrao = [{'ordem': dados['numero'], 'descricao': base.equ_ordem_descricao, 'tipo_id': base.equ_tipo_producao_id}]

    entrada = _equ_json(texto)
    lista = entrada.get('ordens') if entrada else None
    if not isinstance(lista, list):
        return ("Não entendi -- preenche a ordem no formulário abaixo e clica em Confirmar.\n\n"
                + _equ_form_ordens(estado, padrao))
    tipos = {t['id']: t['nome'] for t in _equ_tipos_producao_permitidos()}
    max_descricao = TbEquipamentos._meta.get_field('equ_ordem_descricao').max_length
    item = next((i for i in lista if isinstance(i, dict) and str(i.get('ordem')) == str(dados['numero'])), None)
    if item is None:
        return "Não encontrei os dados da ordem no formulário.\n\n" + _equ_form_ordens(estado, padrao)
    descricao = str(item.get('descricao') or '').strip()
    try:
        tipo_id = int(item.get('tipo_id'))
    except (TypeError, ValueError):
        tipo_id = None
    erros = []
    if not descricao:
        erros.append("A descrição da ordem não pode ficar vazia.")
    elif len(descricao) > max_descricao:
        erros.append(f"A descrição pode ter no máximo {max_descricao} caracteres.")
    if tipo_id not in tipos:
        erros.append("Escolhe um Tipo de Produção válido.")
    if erros:
        digitado = [{'ordem': dados['numero'], 'descricao': descricao, 'tipo_id': tipo_id}]
        return "\n".join(f"- {e}" for e in erros) + "\n\n" + _equ_form_ordens(estado, digitado)
    return _ord_pedir_produtos(estado, {**dados, 'descricao': descricao, 'tipo_id': tipo_id, 'tipo_nome': tipos[tipo_id]})


def _ord_pedir_produtos(estado, dados):
    from equipamentos.models import TbEquipamentos
    from produtos.models import TbProdutos
    ordem_ref = TbEquipamentos.objects.get(id=dados['destino_ordem_id'] if dados.get('existente') else dados['origem_ordem_id'])
    marcados = set(ordem_ref.equ_produtos.values_list('id', flat=True))
    produtos = list(TbProdutos.objects.filter(tbcenarios_id=dados['cenario_id']).order_by('pro_codigo'))
    estado.etapa_atual = 'ord_produtos'
    estado.dados_coletados = dados
    estado.save()
    if not produtos:
        return _ord_produtos_escolhidos(estado, dados, [])
    de_onde = "os que a ordem atende hoje" if dados.get('existente') else "os da ordem clonada"
    itens = [{'id': p.id, 'codigo': p.pro_codigo, 'status': 'escolha', 'motivo': '', 'marcado': p.id in marcados,
              'titulo': p.pro_descricao or ''} for p in produtos]
    return (f"Quais produtos a ordem **{dados['destino_codigo']}/{dados['numero']}** vai atender? Já deixei marcados {de_onde}: "
            "marque ou desmarque e confirme. Se ela não atende nenhum produto, digite \"nenhum\" (ou \"cancelar\" pra "
            "desistir).\n\n" + _equ_marcador('SELECAO_PRODUTOS', {
                'produtos': itens, 'rotulo_confirmar': 'Confirmar produtos', 'rotulo_marcar': 'Marcar todos'}))


def _etapa_ord_produtos(estado, texto):
    from produtos.models import TbProdutos
    dados = estado.dados_coletados or {}
    validos = set(TbProdutos.objects.filter(tbcenarios_id=dados['cenario_id']).values_list('id', flat=True))
    if texto.strip().lower() in ('nenhum', 'nenhuma'):
        return _ord_produtos_escolhidos(estado, dados, [])
    entrada = _equ_json(texto)
    if entrada is None or not isinstance(entrada.get('produtos'), list):
        return ("Não entendi -- marque os produtos na lista e clique em \"Confirmar produtos\" (ou digite \"nenhum\").\n\n"
                + _ord_pedir_produtos(estado, dados))
    ids = []
    for valor in entrada['produtos']:
        try:
            ids.append(int(valor))
        except (TypeError, ValueError):
            pass
    ids = list(dict.fromkeys(ids))
    if any(i not in validos for i in ids):
        return "Um dos produtos marcados não existe nesse cenário.\n\n" + _ord_pedir_produtos(estado, dados)
    return _ord_produtos_escolhidos(estado, dados, ids)


def _ord_produtos_escolhidos(estado, dados, ids):
    dados = {**dados, 'produtos_ids': ids}
    return _ord_pedir_consumos(estado, dados)


def _ord_consumos_da_origem(dados):
    """Consumos padrão que têm a ordem clonada como FROM, com o que seria o novo (from = ordem de destino)."""
    from fluxos.models import TbFluxoConsumoPadrao
    linhas, ignorados = [], []
    consumos = (TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=dados['cenario_id'], flu_con_pad_from_equipamento_id=dados['origem_ordem_id'])
                .select_related('flu_con_pad_from_equipamento__equ_codigo', 'flu_con_pad_to_equipamento__equ_codigo').order_by('id'))
    alvo = f"{dados['destino_codigo']}/{dados['numero']}"
    for cp in consumos:
        para = cp.flu_con_pad_to_equipamento
        para_rotulo = f"{para.equ_codigo}/{para.equ_ordem_codigo}"
        if dados.get('existente') and para.id == dados.get('destino_ordem_id'):
            ignorados.append(f"{cp.flu_con_pad_from_equipamento.equ_codigo}/{cp.flu_con_pad_from_equipamento.equ_ordem_codigo} --> {para_rotulo}")
            continue          # a nova ligação seria da ordem pra ela mesma
        nova_desc = f"{alvo} --> {para_rotulo}"
        ja = TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=dados['cenario_id'], flu_con_pad_descricao=nova_desc).first()
        linhas.append({'cp': cp, 'para': para_rotulo, 'nova_desc': nova_desc, 'existente': ja})
    return linhas, ignorados


def _ord_pedir_consumos(estado, dados):
    linhas, ignorados = _ord_consumos_da_origem(dados)
    estado.etapa_atual = 'ord_consumos'
    estado.dados_coletados = dados
    estado.save()
    if not linhas:
        extra = (f" (ignorei {len(ignorados)} cujo destino é a própria ordem alterada)" if ignorados else "")
        return _ord_resumo_ou_confirmar(estado, {**dados, 'consumos_decididos': []},
                                        f"A ordem clonada não tem consumos padrão de saída para clonar{extra}.\n\n")
    de = f"{dados['origem_codigo']}/{dados['origem_ordem_numero']}"
    alvo = f"{dados['destino_codigo']}/{dados['numero']}"
    rows = []
    for l in linhas:
        rows.append({
            'cp_id': l['cp'].id, 'de': de, 'para': l['para'], 'novo_de': alvo,
            'valor': _ord_fmt_valor((l['existente'] or l['cp']).valor_inicial),
            'existe': l['existente'] is not None,
            'valor_atual': _ord_fmt_valor(l['existente'].valor_inicial) if l['existente'] else None,
        })
    nota = (f" Ignorei {len(ignorados)} consumo(s) padrão cujo destino é a própria ordem alterada." if ignorados else "")
    return (
        f"A ordem clonada (**{de}**) envia produção para {len(rows)} ordem(ns) (consumos padrão com ela como origem). "
        f"Marque os que devem ser **clonados** para **{alvo}** e confira o **valor inicial do indicador** de cada um: já vem "
        "o valor do consumo padrão clonado. Os que já existem aparecem com o valor atual -- confirme ou ajuste (se mudar, "
        "o novo valor passa a valer em todos os períodos)." + nota + " Se quiser desistir, digite \"cancelar\".\n\n"
        + _equ_marcador('FORM_CONSUMOS', {'consumos': rows, 'max_inteiros': 7, 'casas': 4}))


def _etapa_ord_consumos(estado, texto):
    dados = estado.dados_coletados or {}
    linhas, _ignorados = _ord_consumos_da_origem(dados)
    por_id = {l['cp'].id: l for l in linhas}
    entrada = _equ_json(texto)
    lista = entrada.get('consumos') if entrada else None
    if not isinstance(lista, list):
        return ("Não entendi -- confira os consumos padrão no formulário e clique em Confirmar.\n\n"
                + _ord_pedir_consumos(estado, dados))
    enviados = {}
    for item in lista:
        if isinstance(item, dict):
            try:
                enviados[int(item.get('cp_id'))] = item
            except (TypeError, ValueError):
                pass
    erros, decididos = [], []
    for cp_id, l in por_id.items():
        item = enviados.get(cp_id)
        rotulo = f"{dados['origem_codigo']}/{dados['origem_ordem_numero']} --> {l['para']}"
        if item is None:
            erros.append(f"Faltou o consumo padrão {rotulo}.")
            continue
        existe = l['existente'] is not None
        clonar = bool(item.get('clonar'))
        if not existe and not clonar:
            decididos.append({'cp_id': cp_id, 'acao': 'ignorar', 'valor': None})
            continue
        valor = _ord_parse_valor(item.get('valor'))
        if valor is None:
            erros.append(f"O valor do consumo padrão {l['nova_desc']} é inválido -- use um número (até 4 casas decimais, "
                         "até 7 dígitos antes da vírgula).")
            continue
        if existe:
            acao = 'manter' if valor == l['existente'].valor_inicial else 'atualizar'
        else:
            acao = 'criar'
        decididos.append({'cp_id': cp_id, 'acao': acao, 'valor': _ord_fmt_valor(valor)})
    if erros:
        return "\n".join(f"- {e}" for e in erros) + "\n\n" + _ord_pedir_consumos(estado, dados)
    return _ord_resumo_ou_confirmar(estado, {**dados, 'consumos_decididos': decididos}, "")


def _ord_resumo_ou_confirmar(estado, dados, prefixo):
    from equipamentos.models import TbEquipamentos, TbEquipamentosConsumoEspecifico
    from produtos.models import TbProdutos
    origem = TbEquipamentos.objects.get(id=dados['origem_ordem_id'])
    anteriores = set()
    itens_apagar = 0
    if dados.get('existente'):
        alvo = TbEquipamentos.objects.get(id=dados['destino_ordem_id'])
        anteriores = set(alvo.equ_produtos.values_list('id', flat=True))
        itens_apagar = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=alvo.id).count()
    itens_copiar = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=origem.id).count()
    afetados = sorted(anteriores | set(dados['produtos_ids']))
    codigos = dict(TbProdutos.objects.filter(id__in=afetados).values_list('id', 'pro_codigo'))
    escolhidos = sorted(codigos[i] for i in dados['produtos_ids'])
    dec = dados['consumos_decididos']
    cont = {a: sum(1 for d in dec if d['acao'] == a) for a in ('criar', 'atualizar', 'manter', 'ignorar')}
    estado.etapa_atual = 'ord_confirmar'
    estado.dados_coletados = {**dados, 'afetados': [{'id': i, 'codigo': codigos[i]} for i in afetados],
                              'itens_apagar': itens_apagar}
    estado.save()

    nome = f"{dados['destino_codigo']}/{dados['numero']}"
    de = f"{dados['origem_codigo']}/{dados['origem_ordem_numero']}"
    L = [f"Vou {'ALTERAR a ordem' if dados.get('existente') else 'criar a ordem'} **{nome}** a partir do clone da ordem **{de}**:",
         f"- tipo de produção **{dados['tipo_nome']}** e descrição **{dados['descricao']}**;",
         f"- produtos que ela atende ({len(escolhidos)}): " + (", ".join(escolhidos[:10]) + (f" e mais {len(escolhidos) - 10}" if len(escolhidos) > 10 else "") if escolhidos else "nenhum") + ";"]
    if dados.get('existente'):
        L.append(f"- consumos específicos: **apagar o(s) {itens_apagar} item(ns) atual(is)** e copiar o(s) **{itens_copiar}** da ordem {de};")
    else:
        L.append(f"- consumos específicos: copiar os **{itens_copiar}** item(ns) da ordem {de} (com os valores por período);")
    partes = []
    if cont['criar']:
        partes.append(f"**{cont['criar']}** clonado(s)")
    if cont['atualizar']:
        partes.append(f"**{cont['atualizar']}** com o valor ajustado")
    if cont['manter']:
        partes.append(f"**{cont['manter']}** já existente(s) mantido(s)")
    if cont['ignorar']:
        partes.append(f"**{cont['ignorar']}** não clonado(s)")
    L.append("- consumos padrão (origem " + nome + "): " + (", ".join(partes) if partes else "nenhum") + ";")
    if afetados:
        L.append(f"- e depois os fluxos de **{len(afetados)}** produto(s): "
                 + ", ".join(codigos[i] for i in afetados[:8]) + (f" e mais {len(afetados) - 8}" if len(afetados) > 8 else "")
                 + " (a quantidade de fluxos de cada um pode mudar).")
    else:
        L.append("- nenhum produto usa essa ordem, então não há fluxos a gerar.")
    return prefixo + "\n".join(L) + "\n\nConfirma? (Sim / Não)"


def _ord_filhas_com_valor(ModeloFilha, pai_origem, pai_novo, valor):
    """Filhas (valor por período) do consumo padrão novo: as do original, só que com o MESMO valor em todos os períodos."""
    copias = [_equ_copiar_campos(f, mae_id=pai_novo.pk, tbcenarios_id=pai_novo.tbcenarios_id, dau_valor=valor)
              for f in ModeloFilha.objects.filter(mae_id=pai_origem.pk).order_by('dau_order')]
    if copias:
        ModeloFilha.objects.filter(mae_id=pai_novo.pk).delete()
        ModeloFilha.objects.bulk_create(copias)


def _ord_executar(estado):
    """Grava tudo numa transação só (a chamada fica dentro de transaction.atomic()): qualquer erro desfaz tudo."""
    from decimal import Decimal
    from equipamentos.models import (
        TbEquipamentosCadastro, TbEquipamentos, TbEquipamentosDaugther,
        TbEquipamentosConsumoEspecifico, TbEquipamentosConsumoEspecificoDaugther,
    )
    from fluxos.models import (TbFluxoConsumoPadrao, TbFluxoConsumoPadraoDaugther, TbFluxoProducao, TbFluxoProducaoDaugther)

    dados = estado.dados_coletados or {}
    cenario_id = dados['cenario_id']
    perfil = getattr(estado.usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id != cenario_id:
        raise ValueError("o cenário ativo mudou desde que você começou. Começa de novo no cenário certo")

    origem = TbEquipamentos.objects.select_related('equ_codigo').get(id=dados['origem_ordem_id'], tbcenarios_id=cenario_id)
    destino = TbEquipamentosCadastro.objects.get(id=dados['destino_cadastro_id'], tbcenarios_id=cenario_id)
    numero = dados['numero']
    existente = TbEquipamentos.objects.filter(equ_codigo_id=destino.pk, equ_ordem_codigo=numero, tbcenarios_id=cenario_id).first()

    anteriores, itens_apagados = set(), 0
    if dados.get('existente'):
        if existente is None or existente.id != dados['destino_ordem_id']:
            raise ValueError(f"a ordem {destino.equ_cad_codigo}/{numero} não existe mais")
        alvo = existente
        anteriores = set(alvo.equ_produtos.values_list('id', flat=True))
        alvo.equ_ordem_descricao = dados['descricao']
        alvo.equ_tipo_producao_id = dados['tipo_id']
        alvo.save()
        itens_apagados = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=alvo.pk).count()
        TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=alvo.pk).delete()    # cascata: as filhas
    else:
        if existente is not None:
            raise ValueError(f"a ordem {destino.equ_cad_codigo}/{numero} já existe")
        # equ_e_clone_de NUNCA aponta pra origem: o gerador de fluxos trataria as duas como UMA alternativa.
        alvo = _equ_copiar_campos(origem, equ_codigo_id=destino.pk, equ_ordem_codigo=numero,
                                  equ_ordem_descricao=dados['descricao'], equ_tipo_producao_id=dados['tipo_id'],
                                  equ_e_clone_de_id=None)
        alvo.save()
        _equ_clonar_filhas(TbEquipamentosDaugther, origem, alvo)
    alvo.equ_codigo = destino                      # evita nova consulta ao montar a descrição dos consumos padrão
    alvo.equ_produtos.set(dados['produtos_ids'])   # sem isso o gerador de fluxos não enxerga a ordem

    qtd_itens = 0
    for it in TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=origem.pk).order_by('id'):
        novo_item = _equ_copiar_campos(it, equ_con_esp_equipamento_id=alvo.pk)
        novo_item.save()
        _equ_clonar_filhas(TbEquipamentosConsumoEspecificoDaugther, it, novo_item)
        qtd_itens += 1

    contagem = {'criar': 0, 'atualizar': 0, 'manter': 0, 'ignorar': 0}
    for d in dados.get('consumos_decididos') or []:
        contagem[d['acao']] += 1
        if d['acao'] in ('ignorar', 'manter'):
            continue
        cp = TbFluxoConsumoPadrao.objects.select_related('flu_con_pad_to_equipamento__equ_codigo').get(
            id=d['cp_id'], tbcenarios_id=cenario_id, flu_con_pad_from_equipamento_id=origem.pk)
        valor = Decimal(d['valor'].replace(',', '.'))
        descricao = _equ_descricao_consumo_padrao(alvo, cp.flu_con_pad_to_equipamento)
        ja = TbFluxoConsumoPadrao.objects.filter(tbcenarios_id=cenario_id, flu_con_pad_descricao=descricao).first()
        if d['acao'] == 'criar':
            if ja is not None:
                raise ValueError(f"já existe o consumo padrão {descricao}")
            novo = _equ_copiar_campos(cp, flu_con_pad_from_equipamento_id=alvo.pk, flu_con_pad_descricao=descricao,
                                      valor_inicial=valor)
            novo.save()
            if valor == cp.valor_inicial:
                _equ_clonar_filhas(TbFluxoConsumoPadraoDaugther, cp, novo)      # cópia exata (valores por período)
            else:
                _ord_filhas_com_valor(TbFluxoConsumoPadraoDaugther, cp, novo, valor)
        else:                                                                    # atualizar um que já existe
            if ja is None:
                raise ValueError(f"o consumo padrão {descricao} deixou de existir")
            TbFluxoConsumoPadrao.objects.filter(pk=ja.pk).update(valor_inicial=valor)
            TbFluxoConsumoPadraoDaugther.objects.filter(mae_id=ja.pk).update(dau_valor=valor)
            # O I/O dos fluxos que usam esse consumo padrão ficou desatualizado: entra na pergunta do I/O no fim.
            TbFluxoProducao.objects.filter(
                id__in=TbFluxoProducaoDaugther.objects.filter(flu_pro_dau_consumo_padrao_id=ja.pk).values('mae_id')
            ).update(flu_pro_input_output_atualizado=False)
    return {'ordem_id': alvo.pk, 'alterada': bool(dados.get('existente')), 'itens_apagados': itens_apagados,
            'itens': qtd_itens, 'consumos': contagem, 'produtos': anteriores | set(dados['produtos_ids'])}


def _etapa_ord_confirmar(estado, texto):
    resposta = texto.strip().lower()
    if resposta not in ('sim', 'não', 'nao'):
        return "Não entendi -- responde \"Sim\" pra gravar ou \"Não\" pra desistir. (Sim / Não)"
    if resposta != 'sim':
        _encerrar_fluxo(estado)
        return "Ok, não gravei nada."

    dados = estado.dados_coletados or {}
    try:
        with transaction.atomic():
            feito = _ord_executar(estado)
    except Exception as erro:
        _encerrar_fluxo(estado)
        return f"Não gravei nada -- deu erro e eu desfiz tudo: {erro}."

    nome = f"{dados['destino_codigo']}/{dados['numero']}"
    de = f"{dados['origem_codigo']}/{dados['origem_ordem_numero']}"
    c = feito['consumos']
    partes = [f"**{feito['itens']}** item(ns) de consumo específico"]
    if feito['alterada']:
        partes.insert(0, f"**{feito['itens_apagados']}** item(ns) antigo(s) apagado(s)")
    partes.append(f"consumos padrão: {c['criar']} clonado(s), {c['atualizar']} ajustado(s), {c['manter']} mantido(s)")
    criado = (f"✅ Ordem **{nome}** {'alterada' if feito['alterada'] else 'criada'} a partir do clone da ordem **{de}**: "
              + "; ".join(partes) + ".")
    fila = list(dados.get('afetados') or [])
    if not fila:
        _encerrar_fluxo(estado)
        return criado + "\n\nNenhum produto usa essa ordem, então não há fluxos a gerar."

    estado.dados_coletados = {
        'cenario_id': dados['cenario_id'], 'resumo_criado': criado, 'fila': fila, 'total_produtos': len(fila),
        'concluidos': 0, 'resultados': [],
    }
    _equ_iniciar_proximo_produto(estado)
    return (criado + f"\n\nAgora estou gerando os fluxos dos **{len(fila)}** produto(s) afetados, um de cada vez. Pode "
            "demorar; acompanho por aqui.\n\n" + _equ_texto_andamento(estado.dados_coletados))


_HANDLERS_EQUIPAMENTOS = {
    'equ_criar_clone_pergunta': _etapa_equ_criar_clone_pergunta,
    'equ_criar_clone_escolher': _etapa_equ_criar_clone_escolher,
    'equ_criar_dados': _etapa_equ_criar_dados,
    'equ_criar_ordens': _etapa_equ_criar_ordens,
    'equ_criar_confirmar': _etapa_equ_criar_confirmar,
    'equ_criar_fluxos_aguardando': _etapa_equ_criar_fluxos_aguardando,
    # 🌟 NOVO: "Criar Nova Ordem de Produção" (clone de equipamento/ordem; cria ou altera)
    'ord_destino_equipamento': _etapa_ord_destino_equipamento,
    'ord_destino_ordem': _etapa_ord_destino_ordem,
    'ord_alterar_confirmar': _etapa_ord_alterar_confirmar,
    'ord_origem_equipamento': _etapa_ord_origem_equipamento,
    'ord_origem_ordem': _etapa_ord_origem_ordem,
    'ord_dados': _etapa_ord_dados,
    'ord_produtos': _etapa_ord_produtos,
    'ord_consumos': _etapa_ord_consumos,
    'ord_confirmar': _etapa_ord_confirmar,
}