from django.shortcuts import render
from django.http import JsonResponse
from .agents import executar_agente_com_prompt_do_admin
from .fluxo_criar_cenario import usuario_esta_em_fluxo
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from functools import wraps
from .models import RelatorioPDF, HistoricoAgente
import datetime
import re
from django.utils import timezone

# 🌟 Precisa bater EXATAMENTE (acentos, maiúsculas) com o nome do grupo criado no Django Admin
# e com a string usada no filtro de template `has_group` do base_site.html
NOME_GRUPO_AGENTE_IA = "Agente de IA"


def exige_acesso_ao_agente_ia(view_func):
    """
    Exige que o usuário esteja logado E (seja superusuário OU membro do grupo
    'Agente de IA'). Quem estiver logado mas sem essa permissão recebe um 403
    (Permission Denied) em vez de ser redirecionado pra tela de login de novo
    — o que criaria um loop, já que ele já está autenticado.
    """
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        tem_acesso = request.user.is_superuser or request.user.groups.filter(name=NOME_GRUPO_AGENTE_IA).exists()
        if not tem_acesso:
            raise PermissionDenied("Você não tem permissão para acessar o Agente de IA.")
        return view_func(request, *args, **kwargs)
    return wrapper


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
       "Alguns grupos existentes:") -- só essas funções específicas do
       wizard geram esse texto, então é um sinal seguro (não se confunde
       com um resumo informativo qualquer, que nunca usa esse cabeçalho).
       Bullets com "id: nome" (cenários/grupos) viram o id; bullets com
       "nome (código)" (câmbio) viram só o nome; "período: valor" vira
       só o período.
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
        r'Alguns cenários recentes|Alguns grupos existentes):\n'
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
    if re.search(r'"nenhum[oa]?"', texto, re.IGNORECASE) and 'Nenhum' not in opcoes:
        opcoes.append('Nenhum')

    return opcoes[:12]


@exige_acesso_ao_agente_ia
def chat_view(request):
    if request.method == "POST":
        mensagem = request.POST.get("mensagem", "").strip()
        # Captura a lista de IDs dos PDFs enviados pelo front-end
        pdf_ids = request.POST.getlist("pdfs_selecionados")

        # Converte os IDs em inteiros válidos
        pdf_ids = [int(id_str) for id_str in pdf_ids if id_str.isdigit()]

        # Executa o agente passando a mensagem, os arquivos escolhidos e o usuário logado
        # (usado para isolar a memória de curto prazo e o histórico salvo por conta)
        resposta, fontes = executar_agente_com_prompt_do_admin(mensagem, pdf_ids, request.user)

        # 🌟 NOVO: extrai opções clicáveis do próprio texto da resposta,
        # pra virarem botões no chat em vez do usuário ter que digitar.
        opcoes = _extrair_opcoes_clicaveis(resposta)

        # 🌟 CORRIGIDO: garante o botão "Cancelar" sempre que ainda existir
        # um fluxo em andamento DE VERDADE (checando o estado, não o texto
        # da resposta) -- mais confiável do que depender da mensagem
        # mencionar "cancelar" explicitamente. Só some quando o fluxo já
        # tiver terminado (sucesso, erro, ou cancelamento).
        if usuario_esta_em_fluxo(request.user) and 'Cancelar' not in opcoes:
            opcoes.append('Cancelar')

        return JsonResponse({
            "resposta": resposta,
            "fontes": fontes,
            "opcoes": opcoes
        })

    # No GET, renderiza a página trazendo todos os relatórios disponíveis
    relatorios = RelatorioPDF.objects.filter(ativo=True).order_by('-id')
    return render(request, "chat.html", {"relatorios": relatorios})



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