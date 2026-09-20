import json
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.utils.translation import gettext as _
from .agents import executar_agente_com_prompt_do_admin
from .fluxo_criar_cenario import usuario_esta_em_fluxo, MENSAGENS_FLAG, etapa_atual_do_usuario, ETAPAS_AGUARDANDO_CELERY
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from functools import wraps
from .models import RelatorioPDF, HistoricoAgente, IDIOMA_CHOICES, TbEmpresa, TbCenarios
from .contexto_usuario import eh_superuser_ou_superuser_empresa
import datetime
import re
from django.utils import timezone

# 🌟 Precisa bater EXATAMENTE (acentos, maiúsculas) com o nome do grupo criado no Django Admin
# e com a string usada no filtro de template `has_group` do base_site.html
NOME_GRUPO_AGENTE_IA = "Agente de IA"


def exige_acesso_ao_agente_ia(view_func):
    """
    Exige que o usuário esteja logado E (seja superusuário, seja
    superusuário DE EMPRESA, OU membro do grupo 'Agente de IA'). Quem
    estiver logado mas sem essa permissão recebe um 403 (Permission
    Denied) em vez de ser redirecionado pra tela de login de novo — o
    que criaria um loop, já que ele já está autenticado.

    🌟 NOVO (multi-empresa): superuser de empresa (PerfilUsuario.
    eh_superuser_empresa) ganha acesso automático, sem precisar ser
    adicionado manualmente ao grupo "Agente de IA" -- os dados que ele
    vai ver/mexer já ficam restritos à própria empresa pelo mesmo
    mecanismo de cenário ativo/empresa_efetiva_id() usado em todo o
    resto do sistema.
    """
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        tem_acesso = (
            eh_superuser_ou_superuser_empresa(request.user)
            or request.user.groups.filter(name=NOME_GRUPO_AGENTE_IA).exists()
        )
        if not tem_acesso:
            raise PermissionDenied("Você não tem permissão para acessar o Agente de IA.")
        return view_func(request, *args, **kwargs)
    return wrapper


# 🌟 NOVO: conjunto fechado de palavras-chave que aparecem como VALOR de
# botão nos fluxos do Agente IA -- sempre as mesmas palavras, em
# português, reconhecidas por casamento exato no backend. Usado pra
# gerar um rótulo traduzido só pra exibição, sem mudar o valor.
_PALAVRAS_CHAVE_BOTAO = {
    'Cancelar', 'Manter', 'Nenhum', 'Nenhuma', 'Manual', 'Planilha', 'Sim', 'Não',
    'Verificar', 'Já enviei a planilha', 'Gráfico de linha', 'Gráfico de barra',
    'Concluir',
}


def _extrair_opcoes_clicaveis(texto):
    """
    Detecta, no próprio texto que o wizard já produz, se a pergunta atual
    tem um conjunto pequeno e fechado de respostas esperadas -- e, se
    tiver, devolve essas opções pra virarem botões clicáveis no chat, sem
    precisar mudar nenhuma das dezenas de funções do wizard que já geram
    esse texto. Padrão adotado em TODOS os fluxos do Agente IA (criar
    cenário, mudar cenário, indicadores, câmbio).

    Reconhece, nessa ordem:
    0. Listas livres sob um cabeçalho EXATO e exclusivo de escolha
       ("Indicadores cadastrados:", "Taxas de câmbio cadastradas:",
       "Períodos e valores atuais:", "Alguns cenários recentes:",
       "Alguns grupos existentes:", "Moedas disponíveis:") -- só essas
       funções específicas do wizard geram esse texto, então é um sinal
       seguro (não se confunde com um resumo informativo qualquer, que
       nunca usa esse cabeçalho). Bullets com "id: nome" (cenários/
       grupos) viram o id; bullets com "nome (código)" (câmbio) viram
       só o nome; bullets com "código (nome)" (moedas disponíveis)
       viram só o código; "período: valor" vira só o período.
    1. Confirmação "(sim / não)" -- aceita também a variante em negrito
       "(**sim** / **não**)", usada no fluxo de criar cenário.
    2. Menu de bullets em negrito: "- **opção** -- descrição"
    3. Lista entre parênteses tipo "(Mensal / Trimestral / Anual...)"
    4. Mensagens de instrução sem pergunta fechada, mas que só existem
       pra sinalizar "quando terminar, volte aqui": o link de download de
       planilha, e os avisos de "processando em segundo plano, me manda
       qualquer mensagem depois" (duplicação, limpeza, otimização,
       consolidação, verificação de filhas).

    Se o texto também mencionar "manter", "cancelar" ou "nenhum" entre
    aspas (comum nas perguntas do wizard), adiciona como opção extra.
    Limita a 12 botões pra não virar uma parede em cenários com muitos
    cadastros.
    """
    if not texto:
        return []

    opcoes = []

    m_lista = re.search(
        r'(?:Indicadores cadastrados|Taxas de câmbio cadastradas|Períodos e valores atuais|'
        r'Alguns cenários recentes|Alguns grupos existentes|Moedas disponíveis):\n'
        r'((?:-\s.+\n?)+)',
        texto
    )
    if m_lista:
        for linha in m_lista.group(1).strip().split('\n'):
            linha = linha.strip()
            if not linha.startswith('-'):
                continue
            item = linha.lstrip('-').strip()
            # "DÓLAR (USD)" -> "DÓLAR" / "28: NOME DO CENÁRIO" -> "28"
            item = re.sub(r'\s*\([^)]*\)\s*$', '', item).strip()
            item = item.split(':')[0].strip()
            if item:
                opcoes.append(item)

    if not opcoes and re.search(r'\(\*{0,2}sim\*{0,2}\s*/\s*\*{0,2}n[ãa]o\*{0,2}\)', texto, re.IGNORECASE):
        opcoes = ['Sim', 'Não']

    if not opcoes:
        achados = re.findall(r'^-\s+\*\*([^*]+)\*\*\s*--', texto, re.MULTILINE)
        if achados:
            opcoes = [o.strip().capitalize() for o in achados]

    if not opcoes:
        m = re.search(r'\(([A-ZÀ-Ý][\wÀ-ÿ]*(?:\s*/\s*[A-ZÀ-Ý][\wÀ-ÿ]*){1,4})', texto)
        if m:
            opcoes = [o.strip() for o in m.group(1).split('/')]

    if not opcoes and '[📥 Baixar planilha](' in texto:
        opcoes = ['Já enviei a planilha']

    if not opcoes and re.search(r'"Verificar"', texto, re.IGNORECASE):
        opcoes = ['Verificar']

    # 🌟 CORRIGIDO: antes, "manter"/"cancelar"/"nenhum" só viravam botão se
    # OUTRA opção já tivesse sido detectada -- numa pergunta de texto livre
    # (tipo "qual o nome do cenário? (a qualquer momento, digite \"cancelar\"
    # para desistir)"), nenhum botão aparecia, nem o de cancelar. Agora
    # funcionam sozinhos também, sem depender de outra opção existir.
    if re.search(r'"manter"', texto, re.IGNORECASE) and 'Manter' not in opcoes:
        opcoes.append('Manter')
    if re.search(r'"cancelar"', texto, re.IGNORECASE) and 'Cancelar' not in opcoes:
        opcoes.append('Cancelar')
    # 🌟 CORRIGIDO: antes sempre usava o rótulo fixo "Nenhum" (masculino),
    # mesmo quando o texto dizia "nenhuma" (concordando com um substantivo
    # feminino, tipo "Alguma observação? (ou \"nenhuma\")") -- o botão
    # ficava com o gênero errado, diferente da própria pergunta. Agora
    # captura a forma exata que apareceu no texto (nenhum ou nenhuma) e
    # usa ela, capitalizada, como rótulo do botão.
    m_nenhum = re.search(r'"(nenhum[oa]?)"', texto, re.IGNORECASE)
    if m_nenhum:
        rotulo = m_nenhum.group(1).capitalize()
        if rotulo not in opcoes:
            opcoes.append(rotulo)
    if re.search(r'"concluir"', texto, re.IGNORECASE) and 'Concluir' not in opcoes:
        opcoes.append('Concluir')

    return opcoes[:12]


@login_required
def cenarios_por_empresa_json(request, empresa_id):
    """
    🌟 NOVO (multi-empresa): endpoint JSON simples, FORA do mecanismo de
    URLs do Admin (que teve problema de resolução não totalmente
    diagnosticado) -- devolve os cenários de uma empresa, usado pelo JS
    que popula o dropdown de cenario_ativo no formulário de usuário.
    Só exige estar logado (não precisa ser staff/superuser aqui, já que
    é só leitura de uma lista de nomes -- sem dado sensível).
    """
    from django.http import JsonResponse
    from .models import TbCenarios

    cenarios = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('numero_sequencial', 'id')
    dados = [
        {'id': c.id, 'texto': f"{c.numero_sequencial if c.numero_sequencial is not None else c.id}/{c.cen_nome}"}
        for c in cenarios
    ]
    return JsonResponse({'cenarios': dados})


@login_required
@require_POST
def trocar_idioma_view(request):
    """
    🌟 NOVO (multi-idioma, Fase 1): permite que QUALQUER usuário logado
    troque o próprio idioma, a qualquer momento -- sem depender de ter
    acesso ao Django Admin (que exige is_staff). Usado pelo seletor de
    idioma na tela do chat.
    """
    novo_idioma = request.POST.get('idioma', '')
    codigos_validos = {codigo for codigo, _ in IDIOMA_CHOICES}
    if novo_idioma not in codigos_validos:
        messages.error(request, 'Idioma inválido.')
        return redirect(request.META.get('HTTP_REFERER', '/'))

    perfil = getattr(request.user, 'perfilusuario', None)
    if perfil is not None:
        perfil.idioma = novo_idioma
        perfil.save()

    # 🌟 NOVO: sinaliza pro próximo carregamento do chat que deve
    # restaurar o histórico na tela -- trocar de idioma faz um
    # redirecionamento de página inteira, e sem isso o usuário perderia
    # a visão da conversa (e de qualquer fluxo em andamento aguardando
    # o Celery) só por ter mudado o idioma. Usa a sessão porque o
    # redirect já perde qualquer parâmetro de querystring/contexto direto.
    request.session['restaurar_historico_chat'] = True

    return redirect(request.META.get('HTTP_REFERER', '/'))


# 🌟 NOVO (Ações Comuns por empresa): categoria -> prefixo usado nas
# chaves do dict (ex: "ind_editar", "cam_grafico", "cen_excluir") -- pra
# casar com os nomes já usados nos <option> do chat.html.
_PREFIXO_CATEGORIA_ACAO = {'Indicadores': 'ind', 'Câmbio': 'cam', 'Cenário': 'cen'}


def _mapa_acoes_comuns_habilitadas(usuario):
    """
    🌟 NOVO (Ações Comuns por empresa): monta um dict com uma chave por
    ação habilitada (ex: {'ind_editar': True, 'cen_excluir': True, ...})
    pra empresa efetiva do usuário -- 1 consulta só (em vez de checar
    empresa_tem_acao_comum_habilitada() uma vez pra cada uma das 19
    ações), usado no template pra esconder as opções não habilitadas do
    menu "Ações Comuns". Uma chave AUSENTE do dict é tratada como "não
    habilitada" pelo {% if %} do template (não precisa pré-popular False).
    """
    mapa = {}
    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        return mapa
    combinacoes = TbEmpresa.objects.filter(id=empresa_id).values_list(
        'acoes_comuns_habilitadas__categoria', 'acoes_comuns_habilitadas__chave'
    )
    for categoria, chave in combinacoes:
        if not categoria or not chave:
            continue
        prefixo = _PREFIXO_CATEGORIA_ACAO.get(categoria)
        if prefixo:
            mapa[f'{prefixo}_{chave}'] = True
    return mapa


@exige_acesso_ao_agente_ia
def chat_view(request):
    if request.method == "POST":
        mensagem = request.POST.get("mensagem", "").strip()
        # Captura a lista de IDs dos PDFs enviados pelo front-end
        pdf_ids = request.POST.getlist("pdfs_selecionados")

        # Converte os IDs em inteiros válidos
        pdf_ids = [int(id_str) for id_str in pdf_ids if id_str.isdigit()]

        # 🌟 NOVO: o front-end manda esse campo quando a mensagem é uma
        # sondagem automática silenciosa (verificando sozinho se uma task
        # do Celery já terminou, a cada poucos segundos) -- essas não
        # devem virar entrada no histórico do usuário.
        eh_sondagem_automatica = request.POST.get("sondagem_automatica") == "1"

        # Executa o agente passando a mensagem, os arquivos escolhidos e o usuário logado
        # (usado para isolar a memória de curto prazo e o histórico salvo por conta)
        resposta, fontes, resposta_original = executar_agente_com_prompt_do_admin(
            mensagem, pdf_ids, request.user, salvar_historico=not eh_sondagem_automatica,
            eh_sondagem_automatica=eh_sondagem_automatica,
        )

        # 🌟 CORRIGIDO: extrai as opções clicáveis do texto ORIGINAL (em
        # português), não do texto já traduzido -- a extração procura
        # frases exatas em português ("Indicadores cadastrados:", "(sim /
        # não)", etc.), e um texto traduzido nunca bate com esse padrão.
        # O VALOR de cada botão continua em português de propósito (é o
        # que volta pro backend quando o usuário clica, e os fluxos
        # reconhecem esse valor por casamento de texto em português --
        # mesma lógica das "Ações Comuns" do menu lateral).
        opcoes = _extrair_opcoes_clicaveis(resposta_original)

        # 🌟 CORRIGIDO: garante o botão "Cancelar" sempre que ainda existir
        # um fluxo em andamento DE VERDADE (checando o estado, não o texto
        # da resposta) -- mais confiável do que depender da mensagem
        # mencionar "cancelar" explicitamente. Só some quando o fluxo já
        # tiver terminado (sucesso, erro, ou cancelamento).
        if usuario_esta_em_fluxo(request.user) and 'Cancelar' not in opcoes:
            opcoes.append('Cancelar')

        # 🌟 NOVO: se a etapa atual é uma das que só ficam esperando o
        # Celery terminar (duplicação, limpeza, otimização, etc.), o
        # front-end pode sondar sozinho em segundo plano, sem precisar
        # que o usuário clique em "Verificar" -- nesse caso, tira o
        # botão "Verificar" das opções (ele deixou de fazer sentido, já
        # que a checagem passa a ser automática) e deixa só "Cancelar"
        # disponível, se o usuário quiser interromper o acompanhamento.
        etapa_atual = etapa_atual_do_usuario(request.user)
        aguardando_poll = etapa_atual in ETAPAS_AGUARDANDO_CELERY
        if aguardando_poll and 'Verificar' in opcoes:
            opcoes.remove('Verificar')

        # 🌟 CORRIGIDO (generalizado): os VALORES de "Manual", "Planilha",
        # "Cancelar", "Manter", "Nenhum", "Sim", "Não", "Verificar" e "Já
        # enviei a planilha" continuam em português de propósito -- são
        # os textos que os fluxos reconhecem por casamento exato quando o
        # usuário clica. Mas mandamos também um RÓTULO traduzido pra cada
        # uma dessas palavras-chave conhecidas, que o front-end usa só
        # pra exibição, sem mudar o valor que é enviado de volta.
        rotulos_opcoes = {}
        for opcao in opcoes:
            rotulo = _(opcao) if opcao in _PALAVRAS_CHAVE_BOTAO else None
            if rotulo:
                rotulos_opcoes[opcao] = rotulo

        return JsonResponse({
            "resposta": resposta,
            "fontes": fontes,
            "opcoes": opcoes,
            "rotulos_opcoes": rotulos_opcoes,
            "aguardando_poll": aguardando_poll,
            "etapa_atual": etapa_atual,
        })

    # No GET, renderiza a página trazendo os relatórios da empresa efetiva do usuário
    # 🌟 NOVO (multi-empresa): antes trazia TODOS os relatórios (de
    # qualquer empresa) -- agora filtra igual ao resto do sistema.
    perfil = getattr(request.user, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        relatorios = RelatorioPDF.objects.none()
    else:
        relatorios = RelatorioPDF.objects.filter(ativo=True, empresa_id=empresa_id).order_by('-id')

    # 🌟 NOVO: nome da empresa efetiva e do cenário ativo, pra mostrar no
    # topo do chat -- o usuário sempre sabe em qual contexto está
    # trabalhando, sem precisar ir noutra tela conferir.
    nome_empresa = None
    nome_cenario = None
    status_cenario = None
    if empresa_id is not None:
        empresa_obj = TbEmpresa.objects.filter(id=empresa_id).first()
        nome_empresa = empresa_obj.emp_nome if empresa_obj else None
    if perfil and perfil.cenario_ativo_id:
        cenario_obj = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
        if cenario_obj:
            numero_exibido_cenario = cenario_obj.numero_sequencial if cenario_obj.numero_sequencial is not None else cenario_obj.id
            nome_cenario = f"{numero_exibido_cenario}/{cenario_obj.cen_nome}"
            if cenario_obj.flag is None:
                status_cenario = _("Ainda não processado")
            else:
                status_cenario = _(MENSAGENS_FLAG.get(cenario_obj.flag, f"Desconhecido (flag={cenario_obj.flag})"))

    # 🌟 NOVO (multi-idioma, Fase 1): manda o idioma atual e a lista de
    # opções pro seletor de idioma no template.
    # 🌟 NOVO: carrega as últimas mensagens do histórico do usuário, pra
    # reconstruir a tela do chat quando a página é recarregada (F5) --
    # antes, um refresh no meio de qualquer fluxo (esperando o Celery
    # terminar uma duplicação, por exemplo) apagava a conversa da tela
    # inteira, mesmo o fluxo continuando ativo por trás. Limitado às
    # últimas 30 trocas pra não deixar a página pesada.
    # NOTA: o histórico guarda o texto ORIGINAL em português (a tradução
    # acontece só na hora de exibir cada resposta nova) -- então mensagens
    # antigas aparecem em português mesmo se o idioma ativo for outro.
    # 🌟 CORRIGIDO: antes, TODA vez que o chat carregava (inclusive uma
    # aba nova de verdade) mostrava o histórico completo -- só devemos
    # restaurar em F5 (refresh) ou logo depois de trocar de idioma
    # (redirect de página inteira), nunca numa abertura nova. A
    # decisão "é F5 ou não" é feita no JavaScript (Navigation Timing
    # API, que sabe distinguir isso de verdade); aqui só cuidamos do
    # caso de troca de idioma, via sinalizador de sessão de uso único
    # (.pop remove logo em seguida, então só vale pra ESSE carregamento).
    forcar_restaurar_historico = request.session.pop('restaurar_historico_chat', False)

    historico_recente = list(
        HistoricoAgente.objects.filter(usuario=request.user).order_by('-data')[:30].values('comando_usuario', 'resposta_ia')
    )
    historico_recente.reverse()  # mais antiga primeiro, igual à ordem de exibição no chat

    idioma_atual = perfil.idioma_efetivo() if perfil else 'pt-br'
    return render(request, "chat.html", {
        "relatorios": relatorios,
        "idioma_atual": idioma_atual,
        "idioma_opcoes": IDIOMA_CHOICES,
        "nome_empresa": nome_empresa,
        "nome_cenario": nome_cenario,
        "status_cenario": status_cenario,
        "acoes_habilitadas": _mapa_acoes_comuns_habilitadas(request.user),
        "historico_json": json.dumps(historico_recente),
        "forcar_restaurar_historico": forcar_restaurar_historico,
    })



@exige_acesso_ao_agente_ia
@require_POST
def limpar_historico_view(request):
    """
    Remove registros do histórico DO USUÁRIO LOGADO, baseando-se estritamente
    no período ou no intervalo de datas utilizando o campo 'data' nativo do modelo.
    """
    try:
        periodo = request.POST.get("periodo", "tudo")
        agora = timezone.now()

        # 🌟 Toda consulta parte do histórico do usuário atual — nunca do histórico global
        base_queryset = HistoricoAgente.objects.filter(usuario=request.user)

        if periodo == "hoje":
            # Filtra e remove apenas as mensagens enviadas desde a meia-noite de hoje
            inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
            queryset = base_queryset.filter(data__gte=inicio_dia)

        elif periodo == "7_dias":
            # Filtra e remove mensagens dos últimos 7 dias
            sete_dias_atras = agora - datetime.timedelta(days=7)
            queryset = base_queryset.filter(data__gte=sete_dias_atras)

        elif periodo == "personalizado":
            data_inicio_str = request.POST.get("data_inicio")
            data_fim_str = request.POST.get("data_fim")

            if not data_inicio_str or not data_fim_str:
                return JsonResponse({"status": "erro", "mensagem": "Intervalo de datas incompleto."}, status=400)

            # 🌟 VALIDAÇÃO 1: mensagem amigável se a data vier malformada,
            # em vez de deixar o ValueError do strptime estourar como erro técnico genérico
            try:
                data_inicio = datetime.datetime.strptime(data_inicio_str, "%Y-%m-%d")
                data_fim = datetime.datetime.strptime(data_fim_str, "%Y-%m-%d")
            except ValueError:
                return JsonResponse({
                    "status": "erro",
                    "mensagem": "Data inválida. Selecione as datas pelo calendário no formato correto."
                }, status=400)

            # 🌟 VALIDAÇÃO 2: garante no backend que a data inicial não é posterior à final,
            # mesmo que o front-end já valide isso (evita requisição manipulada retornar
            # silenciosamente um queryset vazio)
            if data_inicio > data_fim:
                return JsonResponse({
                    "status": "erro",
                    "mensagem": "A data de início não pode ser posterior à data de fim."
                }, status=400)

            # Inclui o dia final inteiro no intervalo e torna os datetimes timezone-aware
            data_fim = data_fim + datetime.timedelta(days=1)
            data_inicio = timezone.make_aware(data_inicio)
            data_fim = timezone.make_aware(data_fim)

            queryset = base_queryset.filter(data__range=[data_inicio, data_fim])
        else:
            # 🌟 Opção "tudo" agora limpa só o histórico DO USUÁRIO LOGADO,
            # não mais a tabela inteira do sistema
            queryset = base_queryset

        # Executa a limpeza cirúrgica
        total_removido, _ = queryset.delete()

        return JsonResponse({
            "status": "sucesso",
            "mensagem": "Limpeza concluída! " + str(total_removido) + " mensagens foram removidas do histórico."
        })

    except Exception as e:
        return JsonResponse({"status": "erro", "mensagem": str(e)}, status=500)


@exige_acesso_ao_agente_ia
@require_POST
def upload_pdf_view(request):
    """
    Recebe um arquivo (PDF, TXT ou XLSX) enviado pela zona de arrastar-e-soltar
    da barra lateral e cria um novo RelatorioPDF ativo para uso imediato pelo agente.
    """
    arquivo = request.FILES.get("arquivo")

    if not arquivo:
        return JsonResponse({"status": "erro", "mensagem": "Nenhum arquivo foi enviado."}, status=400)

    # 🌟 EXTENSÕES ACEITAS: PDF, TXT e XLSX (planilha Excel)
    extensoes_aceitas = (".pdf", ".txt", ".xlsx")
    nome_arquivo = arquivo.name.lower()

    if not nome_arquivo.endswith(extensoes_aceitas):
        return JsonResponse({
            "status": "erro",
            "mensagem": "Apenas arquivos PDF, TXT ou XLSX são aceitos."
        }, status=400)

    # Limite de segurança para evitar uploads muito grandes (ajuste se necessário)
    limite_mb = 25
    if arquivo.size > limite_mb * 1024 * 1024:
        return JsonResponse({
            "status": "erro",
            "mensagem": f"Arquivo maior que {limite_mb}MB. Reduza o tamanho e tente novamente."
        }, status=400)

    # Usa o nome do arquivo (sem extensão) como título padrão
    titulo = arquivo.name.rsplit(".", 1)[0]

    try:
        novo_relatorio = RelatorioPDF.objects.create(
            titulo=titulo,
            arquivo=arquivo,
            ativo=True
        )
        return JsonResponse({
            "status": "sucesso",
            "mensagem": "Relatório enviado com sucesso!",
            "id": novo_relatorio.id,
            "titulo": novo_relatorio.titulo
        })
    except Exception as e:
        return JsonResponse({"status": "erro", "mensagem": str(e)}, status=500)