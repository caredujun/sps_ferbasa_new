import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from pypdf import PdfReader  # Leitura dinâmica universal direta do arquivo físico PDF
import openpyxl  # Leitura de planilhas XLSX
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage

# Seus modelos do Django Admin
from .models import AgenteConfig, HistoricoAgente, RelatorioPDF, IDIOMA_CHOICES, TbCenarios

# 🌟 NOVO: wizard de criação de cenário via conversa
from .fluxo_criar_cenario import (
    usuario_esta_em_fluxo, cancelar_fluxo_ativo, iniciar_fluxo_criar_cenario, iniciar_fluxo_mudar_cenario,
    iniciar_fluxo_indicadores, iniciar_fluxo_cambio, processar_mensagem_fluxo,
    iniciar_download_planilha_indicador, iniciar_download_planilha_cambio,
    identificar_tipo_planilha_reenviada, iniciar_fluxo_processar, iniciar_consulta_status,
    iniciar_ciclo_completo, iniciar_fluxo_excluir_cenario, iniciar_exportar_excel_cenario_ativo,
    etapa_atual_do_usuario, ETAPAS_AGUARDANDO_CELERY,
    iniciar_exportar_dados_otimizacao_cenario_ativo,
    _processar_planilha_indicador, _processar_planilha_cambio,
    _buscar_indicador, _lista_indicadores, _lista_periodos_indicador,
    _buscar_cambio, _lista_cambios, _lista_periodos_cambio,
    determinar_acao_indicadores, determinar_acao_cambio, determinar_acao_processar,
)
from .contexto_usuario import empresa_tem_acao_comum_habilitada

# Carrega as variáveis de ambiente do arquivo .env localizado na raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Filtros de mapeamento para capturar linhas com alta densidade financeira e dados numéricos
# (usado igualmente para PDF, TXT e XLSX, mantendo o mesmo critério de relevância)
#
# 🌟 GENERALIZADO: removidos os termos fixos de trimestre (2t26/2t25) e ampliado o
# vocabulário para cobrir demonstrações financeiras de qualquer empresa/período, não
# só Vale/Ferbasa do 2T26. Uma linha é considerada relevante se tiver (a) um termo
# contábil OU (b) uma referência a trimestre/período OU (c) um símbolo monetário/percentual
# — E, em qualquer um dos três casos, também contiver pelo menos um número.
TERMOS_FINANCEIROS = re.compile(
    r"("
    r"receita|lucro|preju[ií]zo|ebitda|ebit\b|capex|custo|despesa|margem|"
    r"vendas|faturamento|caixa|l[ií]quid[ao]|d[ií]vida|endividamento|alavancagem|"
    r"proforma|frete|all-in|\bc1\b|"
    r"ativo|passivo|patrim[oô]nio|resultado|imposto|tribut[ao]|juros|"
    r"amortiza[cç][aã]o|deprecia[cç][aã]o|investimento|provis[aã]o|conting[eê]ncia|"
    r"dividendo|guidance|volume|produ[cç][aã]o|pre[cç]o|cota[cç][aã]o|varia[cç][aã]o|"
    r"roic|roe\b|opex|net income|revenue|cash flow"
    r")",
    re.IGNORECASE
)

# Referência a trimestre/período em qualquer ano: 1T24, 2T25, 3T26, Q1, Q2...
PADRAO_TRIMESTRE = re.compile(r"\b[1-4]T\d{2}\b|\bQ[1-4]\b", re.IGNORECASE)

# Símbolos monetários/percentuais que por si só já sinalizam relevância financeira
PADRAO_MOEDA_PERCENTUAL = re.compile(r"(r\$|us\$|\$|%)", re.IGNORECASE)

CONTEM_NUMERO = re.compile(r"\d+")

# Limite de caracteres do contexto injetado no prompt (proteção de cota da Groq)
LIMITE_CARACTERES_CONTEXTO = 5000

# 🌟 NOVO: liga/desliga a busca na internet. True usa o SDK puro da Groq
# com a ferramenta nativa "Web Search" (openai/gpt-oss-20b e -120b têm
# suporte nativo a isso). False volta pro pipeline ORIGINAL via LangChain,
# sem busca -- se a ferramenta nova não se comportar bem, é só trocar essa
# flag pra False; nenhum outro código precisa mudar pra reverter.
USAR_BUSCA_WEB = True


def _gerar_resposta_com_busca_web(system_instruction, mensagem_usuario, historico_langchain):
    """
    Chama o SDK puro da Groq (não o LangChain) com a ferramenta nativa
    "Browser Search" habilitada -- permite responder perguntas sobre
    informações atuais, além do conhecimento de treinamento do modelo.
    Só openai/gpt-oss-20b, openai/gpt-oss-120b e openai/gpt-oss-safeguard-20b
    têm suporte a essa ferramenta ("Web Search" é uma ferramenta diferente,
    exclusiva dos sistemas groq/compound e groq/compound-mini). O
    langchain_groq.ChatGroq não tem um jeito documentado de passar esse
    parâmetro específico -- daí a chamada direta aqui, por fora do LangChain.

    tool_choice não é definido (fica "auto" por padrão) -- o modelo só usa
    a busca quando achar que precisa, não em toda pergunta.
    """
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    mensagens = [{"role": "system", "content": system_instruction}]
    for msg in historico_langchain:
        papel = "user" if isinstance(msg, HumanMessage) else "assistant"
        mensagens.append({"role": papel, "content": msg.content})
    mensagens.append({"role": "user", "content": mensagem_usuario})

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=mensagens,
        temperature=0.1,  # Baixa variação para blindar a precisão de relatórios contábeis
        timeout=60,
        # 🌟 CORRIGIDO: "web_search" só funciona com os sistemas
        # groq/compound e groq/compound-mini -- com openai/gpt-oss-120b
        # (o modelo que já validamos pra análise financeira) a ferramenta
        # certa é "browser_search". É mais lenta que web_search (navega os
        # sites de forma mais completa, em vez de só recuperar trechos),
        # mas é a que existe pra esse modelo específico.
        tools=[{"type": "browser_search"}],
        # 🌟 NOVO: recomendação oficial da Groq para Browser Search -- sem
        # isso, o modelo pode navegar várias páginas e gastar muito mais
        # tokens do que o necessário pra a maioria das perguntas, o que
        # esgota mais rápido o limite diário do plano gratuito (200.000
        # tokens/dia, o mesmo tanto pro gpt-oss-120b quanto pro -20b).
        reasoning_effort="low",
    )

    # 🌟 NOVO (diagnóstico): registra no log do servidor se a busca na
    # internet foi REALMENTE usada nessa resposta -- sem isso, só dá pra
    # desconfiar pelo texto que o modelo escreveu, sem confirmação de
    # verdade. Se não aparecer nenhuma ferramenta usada bem quando o
    # usuário perguntar algo atual, é sinal de que a Groq não está
    # honrando a ferramenta pra essa conta (cota do dia estourada, tier
    # sem acesso, etc.) -- não é bug daqui. Não sabemos de antemão o nome
    # exato do campo nessa versão do SDK, então imprimimos a mensagem
    # inteira -- fica visível de um jeito ou de outro.
    try:
        mensagem_bruta = response.choices[0].message.model_dump()
    except Exception:
        mensagem_bruta = str(response.choices[0].message)
    print(f"[agente_ia][busca_web] finish_reason={response.choices[0].finish_reason!r}")
    print(f"[agente_ia][busca_web] mensagem_completa={mensagem_bruta!r}")

    conteudo = response.choices[0].message.content
    # 🌟 NOVO: a Browser Search injeta marcações de citação bruta no texto
    # (tipo "【2†L6-L10】"), sem virar link clicável de verdade -- melhor
    # remover do que mostrar uma citação que não funciona.
    conteudo = re.sub(r'【\d+†[^】]*】', '', conteudo)
    return conteudo


def _gerar_resposta_original_langchain(system_instruction, mensagem_usuario, historico_langchain):
    """
    Pipeline ORIGINAL, via LangChain -- sem acesso à internet. Preservado
    tal como estava antes da busca web, pra servir de fallback (ver
    USAR_BUSCA_WEB acima).
    """
    llm = ChatGroq(
        model="openai/gpt-oss-120b",
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.1,  # Baixa variação para blindar a precisão de relatórios contábeis
        timeout=60
    )

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}")
    ])

    chain = prompt_template | llm | StrOutputParser()
    resposta_bruta = chain.invoke({
        "input": mensagem_usuario,
        "chat_history": historico_langchain
    })
    return str(resposta_bruta)

# 🌟 NOVO: frases que disparam o wizard de criar cenário. Checagem simples
# por palavra-chave -- não é NLU sofisticado, mas cobre as formas mais
# naturais de pedir isso numa conversa em português.
PADRAO_INICIAR_CRIAR_CENARIO = re.compile(
    r"(criar|cria|novo|nova)\s+.{0,15}\bcen[aá]rio\b|\bcen[aá]rio\s+(novo|nova)\b",
    re.IGNORECASE
)


def _detectar_intencao_criar_cenario(mensagem):
    return bool(PADRAO_INICIAR_CRIAR_CENARIO.search(mensagem or ""))


# 🌟 CORRIGIDO: a versão anterior exigia uma ORDEM específica das palavras
# ("mudar tipo do cenário" ou "cenário, mudar tipo") -- mas uma frase
# natural como "mudar o cenário, tipo e período" tem uma 3ª ordem possível
# (mudar -> cenário -> tipo/período) que não era coberta, e caía direto na
# análise financeira. Agora só checa se as 3 palavras aparecem em
# qualquer lugar da frase, em qualquer ordem -- bem mais robusto.
PADRAO_MUDAR = re.compile(r"mud[ae]r?|alter[ae]r?|troc[ae]r?|atualiz[ae]r?", re.IGNORECASE)
PADRAO_CENARIO = re.compile(r"cen[aá]rio", re.IGNORECASE)
PADRAO_EXCLUIR_ACAO = re.compile(r"elimin|apag|exclu[ií]|delet|remov", re.IGNORECASE)


def _detectar_intencao_excluir_cenario(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_EXCLUIR_ACAO.search(texto) and PADRAO_CENARIO.search(texto))


PADRAO_EXPORTAR = re.compile(r"export", re.IGNORECASE)


def _detectar_intencao_exportar_cenario(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_EXPORTAR.search(texto) and PADRAO_CENARIO.search(texto))


# 🌟 NOVO: mais específica que _detectar_intencao_exportar_cenario -- exige
# também menção a "otimização" pra distinguir do export financeiro comum.
def _detectar_intencao_exportar_dados_otimizacao(mensagem):
    texto = mensagem or ""
    return bool(
        PADRAO_EXPORTAR.search(texto)
        and PADRAO_CENARIO.search(texto)
        and re.search(r'otimiza', texto, re.IGNORECASE)
    )
PADRAO_TIPO_OU_PERIODO = re.compile(r"\btipo\b|per[ií]odo", re.IGNORECASE)


def _detectar_intencao_mudar_cenario(mensagem):
    texto = mensagem or ""
    return bool(
        PADRAO_MUDAR.search(texto)
        and PADRAO_CENARIO.search(texto)
        and PADRAO_TIPO_OU_PERIODO.search(texto)
    )


# 🌟 NOVO: frases que disparam o wizard de indicadores (editar valor, criar
# indicador, ou reajuste em massa). Só precisa mencionar "indicador" junto
# de algum verbo de ação -- o próprio wizard decide qual dos 3 sub-fluxos
# usar, olhando a mesma mensagem de novo.
PADRAO_INDICADOR = re.compile(r"indicador", re.IGNORECASE)
PADRAO_ACAO_INDICADOR = re.compile(
    r"mud[ae]r?|alter[ae]r?|troc[ae]r?|edit[ae]r?|atualiz[ae]r?|cri[ae]r?|cadastr[ae]r?|reajust|"
    r"elimin|apag|exclu[ií]|delet|remov|gr[áa]fico|plot[ae]r?",
    re.IGNORECASE
)


def _detectar_intencao_indicadores(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_INDICADOR.search(texto) and PADRAO_ACAO_INDICADOR.search(texto))


# 🌟 NOVO: mesma lógica do fluxo de indicadores, mas pra taxas de câmbio --
# reaproveita o mesmo PADRAO_ACAO_INDICADOR (é só um conjunto de verbos de
# ação, não específico de indicador).
PADRAO_CAMBIO = re.compile(r"c[âa]mbio", re.IGNORECASE)


def _detectar_intencao_cambio(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_CAMBIO.search(texto) and PADRAO_ACAO_INDICADOR.search(texto))


# 🌟 NOVO: "limpar/otimizar/consolidar o cenário [ativo]" -- ação avulsa,
# disponível a qualquer momento, não só durante a criação de um cenário
# novo. Palavras de ação diferentes das de mudar/criar cenário, então não
# tem risco de conflito entre os detectores.
PADRAO_PROCESSAR_ACAO = re.compile(r'limp[ae]r?|otimiz[ae]r?|consolid[ae]r?', re.IGNORECASE)


def _detectar_intencao_processar(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_CENARIO.search(texto) and PADRAO_PROCESSAR_ACAO.search(texto))


# 🌟 NOVO: "limpar, otimizar e consolidar o cenário" -- as 3 ações juntas
# na mesma mensagem disparam o ciclo completo automático (sem perguntar
# confirmação entre as etapas), em vez do fluxo de ação única (que
# pergunta a cada passo). Precisa ser checado ANTES do detector de ação
# única, senão essa mensagem cairia só no "limpar".
def _detectar_intencao_ciclo_completo(mensagem):
    texto = (mensagem or '').lower()
    return bool(
        PADRAO_CENARIO.search(texto)
        and re.search(r'limp[ae]r?', texto)
        and re.search(r'otimiz[ae]r?', texto)
        and re.search(r'consolid[ae]r?', texto)
    )


# 🌟 NOVO: "qual o status/situação do cenário [ativo]" -- consulta
# informativa, mostra o status atual (e já pergunta se quer seguir pro
# próximo passo, se fizer sentido).
PADRAO_STATUS = re.compile(r'status|situa[cç][ãa]o', re.IGNORECASE)


def _detectar_intencao_status(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_CENARIO.search(texto) and PADRAO_STATUS.search(texto))


# 🌟 NOVO: "baixar planilha do indicador/câmbio X" -- gera um template
# pra download, fora do wizard passo-a-passo (resolve tudo numa mensagem só).
PADRAO_BAIXAR_PLANILHA = re.compile(r"baix[ae]r?|download", re.IGNORECASE)
PADRAO_PLANILHA = re.compile(r"planilha", re.IGNORECASE)


def _detectar_download_planilha_indicador(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_INDICADOR.search(texto) and PADRAO_PLANILHA.search(texto) and PADRAO_BAIXAR_PLANILHA.search(texto))


def _detectar_download_planilha_cambio(mensagem):
    texto = mensagem or ""
    return bool(PADRAO_CAMBIO.search(texto) and PADRAO_PLANILHA.search(texto) and PADRAO_BAIXAR_PLANILHA.search(texto))


# 🌟 NOVO: detecta uma resposta vazia ou "quebrada" (linha repetida em loop,
# padrão observado em falhas momentâneas do provedor de IA) -- antes de
# salvar no histórico, porque a próxima pergunta usa a última troca como
# memória de curto prazo, e uma resposta ruim ali contamina a pergunta
# seguinte também (foi o que causou uma resposta sem nexo, sobre "não
# recebi uma pergunta específica", numa sessão real).
LIMITE_REPETICOES_LINHA = 5


def _resposta_suspeita(resposta):
    texto = (resposta or '').strip()
    if not texto:
        return True

    linhas = [l.strip() for l in texto.split('\n') if l.strip()]
    if not linhas:
        return True

    contagem = {}
    for linha in linhas:
        # Só considera linhas com conteúdo substancial (evita falso positivo
        # em coisas curtas e genuinamente repetidas, tipo separadores de
        # tabela "---" ou células vazias "-").
        if len(linha) >= 15:
            contagem[linha] = contagem.get(linha, 0) + 1

    return any(qtd >= LIMITE_REPETICOES_LINHA for qtd in contagem.values())


def _salvar_historico(usuario, mensagem_usuario, resposta):
    try:
        # 🌟 NOVO (multi-empresa): grava a empresa EFETIVA do usuário no
        # momento dessa interação -- mesmo critério usado em
        # RelatorioPDFAdmin (empresa_ativa pra superusuário real, empresa
        # fixa pra usuário comum/superusuário de empresa). Guardar isso
        # direto no registro (em vez de só derivar depois via
        # usuario__perfilusuario__empresa_id) preserva qual empresa
        # estava envolvida DE VERDADE naquele momento, mesmo que o
        # superusuário troque de "empresa ativa" depois.
        perfil = getattr(usuario, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        HistoricoAgente.objects.create(
            usuario=usuario,
            comando_usuario=mensagem_usuario,
            resposta_ia=resposta,
            empresa_id=empresa_id,
        )
    except Exception:
        pass


def _avisar_superusers_falha_resposta(usuario, mensagem_usuario, resposta_final, codigo_idioma=None):
    """
    🌟 NOVO: manda um e-mail pra todos os superusuários (com e-mail
    cadastrado) sempre que o próprio modelo sinaliza que não conseguiu
    ajudar de verdade o usuário -- dá visibilidade de conversas que
    precisam de atenção humana, sem precisar ninguém ficar lendo o
    histórico inteiro procurando por elas. Falha silenciosamente (nunca
    quebra a resposta pro usuário) se o envio der qualquer problema --
    é um alerta, não uma parte essencial do fluxo.

    🌟 CORRIGIDO: assunto e textos fixos do e-mail agora traduzem pro
    idioma ativo do usuário que fez a pergunta (igual ao resto da
    resposta) -- antes ficavam sempre em português, mesmo com o chat já
    traduzido. `resposta_final` também deve vir JÁ TRADUZIDA (quem
    chama essa função é responsável por isso), pra bater com o que o
    usuário realmente viu na tela.
    """
    try:
        from django.contrib.auth import get_user_model
        from django.core.mail import send_mail
        from django.utils import translation
        from django.utils.translation import gettext as _t

        UserModel = get_user_model()
        emails = list(
            UserModel.objects.filter(is_superuser=True, is_active=True)
            .exclude(email='').exclude(email__isnull=True)
            .values_list('email', flat=True)
        )
        if not emails:
            return

        nome_usuario = usuario.get_full_name() or usuario.username

        with translation.override(codigo_idioma or 'pt-br'):
            assunto = _t("Mensagem Agente IA")
            intro = _t('Não foi possível atender a mensagem abaixo do usuário "%(nome)s (%(username)s)".') % {
                'nome': nome_usuario, 'username': usuario.username,
            }
            rotulo_mensagem = _t("Mensagem do usuário:")
            rotulo_resposta = _t("Resposta do agente:")

        corpo = f"{intro}\n\n{rotulo_mensagem}\n{mensagem_usuario}\n\n{rotulo_resposta}\n{resposta_final}"
        send_mail(
            subject=assunto,
            message=corpo,
            from_email=None,  # usa DEFAULT_FROM_EMAIL do settings.py
            recipient_list=emails,
            fail_silently=True,
        )
    except Exception as erro_email:
        print(f"[agente_ia][aviso_email] não consegui avisar os superusuários: {erro_email}")


def _calcular_relevancia(linha_texto):
    """
    Conta quantos dos três critérios financeiros a linha atende (0 a 3).
    Usado para priorizar as linhas mais relevantes quando o contexto
    precisa ser cortado para caber no limite de caracteres.
    """
    pontos = 0
    if TERMOS_FINANCEIROS.search(linha_texto):
        pontos += 1
    if PADRAO_TRIMESTRE.search(linha_texto):
        pontos += 1
    if PADRAO_MOEDA_PERCENTUAL.search(linha_texto):
        pontos += 1
    return pontos


def _montar_contexto_priorizado(linhas_com_relevancia, limite_caracteres):
    """
    Recebe uma lista de tuplas (relevancia, linha_marcada), ordena da mais
    para a menos relevante (mantendo a ordem original entre empates, já que
    sort() do Python é estável — preserva a sequência de leitura dentro de
    cada arquivo/página) e monta o texto final respeitando o limite de
    caracteres SEM cortar uma linha no meio (evita truncar um valor numérico
    pela metade, como acontecia com o corte cego [:5000] anterior).
    """
    linhas_ordenadas = sorted(linhas_com_relevancia, key=lambda item: item[0], reverse=True)

    partes = []
    total_caracteres = 0
    for _, linha_marcada in linhas_ordenadas:
        tamanho_com_quebra = len(linha_marcada) + (1 if partes else 0)
        if total_caracteres + tamanho_com_quebra > limite_caracteres:
            continue  # essa linha não cabe mais, mas outra menor (mais abaixo) pode caber
        partes.append(linha_marcada)
        total_caracteres += tamanho_com_quebra

    return "\n".join(partes)


def _extrair_linhas_pdf(caminho_fisico):
    """Varre todas as páginas de um PDF e retorna [(origem, linha_texto), ...]."""
    linhas = []
    reader = PdfReader(caminho_fisico)
    for idx, pagina in enumerate(reader.pages):
        texto_pagina = pagina.extract_text()
        if texto_pagina:
            for linha in texto_pagina.split('\n'):
                linha_limpa = linha.strip()
                if linha_limpa:
                    linhas.append((f"Pag {idx + 1}", linha_limpa))
    return linhas


def _extrair_linhas_txt(caminho_fisico):
    """Lê um arquivo TXT (com fallback de encoding) e retorna [(origem, linha_texto), ...]."""
    linhas = []
    conteudo = None
    for encoding in ("utf-8", "latin-1"):
        try:
            with open(caminho_fisico, "r", encoding=encoding) as f:
                conteudo = f.read()
            break
        except UnicodeDecodeError:
            continue

    if conteudo is None:
        return linhas

    for idx, linha in enumerate(conteudo.split('\n'), start=1):
        linha_limpa = linha.strip()
        if linha_limpa:
            linhas.append((f"Linha {idx}", linha_limpa))
    return linhas


def _extrair_linhas_xlsx(caminho_fisico):
    """Varre todas as abas de uma planilha XLSX e retorna [(origem, linha_texto), ...]."""
    linhas = []
    workbook = openpyxl.load_workbook(caminho_fisico, data_only=True, read_only=True)
    try:
        for aba in workbook.worksheets:
            for idx, row in enumerate(aba.iter_rows(values_only=True), start=1):
                valores = [str(v).strip() for v in row if v is not None and str(v).strip() != ""]
                if valores:
                    linha_texto = " | ".join(valores)
                    linhas.append((f"{aba.title} - Linha {idx}", linha_texto))
    finally:
        workbook.close()
    return linhas


def _extrair_linhas_do_arquivo(caminho_fisico, nome_arquivo):
    """
    Escolhe o extrator correto com base na extensão do arquivo e retorna
    uma lista de tuplas (origem, linha_texto) prontas para o filtro financeiro.
    """
    extensao = os.path.splitext(nome_arquivo)[1].lower()
    if extensao == ".pdf":
        return _extrair_linhas_pdf(caminho_fisico)
    elif extensao == ".txt":
        return _extrair_linhas_txt(caminho_fisico)
    elif extensao == ".xlsx":
        return _extrair_linhas_xlsx(caminho_fisico)
    return []


_PALAVRAS_CHAVE_ENTRE_ASPAS = {
    # 🌟 CORRIGIDO: as traduções aqui tinham letra minúscula ('cancel',
    # 'keep', 'none'), mas o BOTÃO correspondente (rotulos_opcoes em
    # views.py, via gettext/lote6.py) sempre mostra com inicial
    # maiúscula ("Cancel", "Keep", "None", "Check", etc.) -- mesma
    # palavra, capitalização diferente entre a frase e o botão. Ajustado
    # pra bater exatamente com o que o botão mostra, em toda linha.
    # Também inclui agora 'pt-br' -- antes a função pulava totalmente o
    # português, então uma frase com "cancelar" minúsculo (o jeito como
    # o texto-fonte dos fluxos é escrito) nunca virava "Cancelar" pra
    # bater com o botão quando o idioma ativo já era português.
    'cancelar': {'pt-br': 'Cancelar', 'en': 'Cancel', 'es': 'Cancelar', 'fr': 'Annuler', 'de': 'Abbrechen', 'it': 'Annulla', 'zh-hans': '取消'},
    'manter': {'pt-br': 'Manter', 'en': 'Keep', 'es': 'Mantener', 'fr': 'Conserver', 'de': 'Beibehalten', 'it': 'Mantieni', 'zh-hans': '保留'},
    'nenhum': {'pt-br': 'Nenhum', 'en': 'None', 'es': 'Ninguno', 'fr': 'Aucun', 'de': 'Keine', 'it': 'Nessuno', 'zh-hans': '无'},
    # 🌟 CORRIGIDO: "nenhuma" (feminino, usado quando concorda com um
    # substantivo feminino, tipo "Alguma observação? (ou \"nenhuma\")")
    # não tinha entrada própria -- caía fora desse mecanismo de tradução/
    # capitalização por completo, só "nenhum" (masculino) era reconhecido.
    'nenhuma': {'pt-br': 'Nenhuma', 'en': 'None', 'es': 'Ninguna', 'fr': 'Aucune', 'de': 'Keine', 'it': 'Nessuna', 'zh-hans': '无'},
    'verificar': {'pt-br': 'Verificar', 'en': 'Check', 'es': 'Verificar', 'fr': 'Vérifier', 'de': 'Prüfen', 'it': 'Verifica', 'zh-hans': '检查'},
}


def _forcar_traducao_palavras_chave(texto: str, codigo_idioma: str) -> str:
    """
    🌟 NOVO: os fluxos determinísticos (criar/editar cenário, indicadores,
    câmbio) sempre usam as mesmas 4 palavras entre aspas como instrução
    pro usuário -- "cancelar", "manter", "nenhum", "verificar" (ex: 'ou
    "cancelar" pra desistir'), quase sempre em minúsculo no texto-fonte.
    A tradução via IA (chamada em `_traduzir_resposta_se_necessario`) é
    boa mas não 100% consistente pra esses casos -- às vezes esquece de
    traduzir uma dessas palavras (visto em alemão: "cancelar" ficou sem
    traduzir enquanto o resto do texto traduziu certo), e mesmo quando
    traduz não necessariamente usa a MESMA capitalização do botão
    correspondente. Como sabemos exatamente quais palavras são e o
    padrão exato (sempre entre aspas), garantimos a tradução E a
    capitalização delas por substituição direta, sem depender da IA.

    🌟 CORRIGIDO: agora roda também pra pt-br (antes pulava totalmente)
    -- precisa rodar mesmo em português pra corrigir a CAPITALIZAÇÃO
    (frase minúscula "cancelar" -> "Cancelar", igual ao botão), não só
    a tradução pra outro idioma.

    Roda DEPOIS da tradução via IA: se a IA já tiver traduzido/
    capitalizado certo (não vai mais achar a palavra original entre
    aspas), não faz nada; se sobrou a palavra errada, corrige.
    """
    if not codigo_idioma or not texto:
        return texto

    for palavra_pt, formas in _PALAVRAS_CHAVE_ENTRE_ASPAS.items():
        forma_certa = formas.get(codigo_idioma)
        if not forma_certa:
            continue
        # Cobre aspas retas (") e curvas (" ") ao redor da palavra
        for aspa_abre, aspa_fecha in [('"', '"'), ('\u201c', '\u201d')]:
            padrao_original = f'{aspa_abre}{palavra_pt}{aspa_fecha}'
            if padrao_original.lower() in texto.lower():
                texto = re.sub(
                    re.escape(padrao_original), f'{aspa_abre}{forma_certa}{aspa_fecha}',
                    texto, flags=re.IGNORECASE
                )
    return texto


def _traduzir_resposta_se_necessario(texto: str, codigo_idioma: str) -> str:
    """
    🌟 NOVO: traduz a resposta FINAL do Agente IA pro idioma ativo do
    usuário, com uma chamada separada e dedicada só pra tradução --
    não depende do sistema de locale/gettext (que só funciona pra
    textos fixos da interface, não pra conteúdo gerado dinamicamente
    pela IA). Isso resolve o problema de o modelo "ignorar" a instrução
    de idioma quando o contexto (relatórios, etc.) está em português.

    Se o idioma já é português (o idioma "nativo" das respostas, dado
    que os dados-fonte são em português) ou se algo falhar na tradução,
    devolve o texto original sem quebrar o fluxo -- pior caso, o
    usuário recebe a resposta em português como sempre foi.
    """
    if not codigo_idioma or codigo_idioma == 'pt-br' or not texto:
        return texto

    nome_idioma = dict(IDIOMA_CHOICES).get(codigo_idioma, codigo_idioma)

    # 🌟 NOVO: protege os blocos ```chart``` (JSON) ANTES de mandar pra
    # tradução, trocando cada um por um placeholder que não parece texto
    # traduzível -- e recoloca o bloco original, intacto, depois. Não
    # depende da IA de tradução "obedecer" a instrução de não mexer no
    # JSON (já vimos ela falhar em instruções parecidas, tipo a palavra
    # "cancelar" -- aqui o risco é maior, JSON quebrado derruba o
    # gráfico inteiro).
    blocos_grafico = re.findall(r'```chart\s*\n.*?\n```', texto, re.DOTALL)
    texto_protegido = texto
    for indice, bloco in enumerate(blocos_grafico):
        texto_protegido = texto_protegido.replace(bloco, f'[[[GRAFICO_{indice}]]]', 1)

    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Você é um tradutor técnico. Traduza o texto do usuário para "
                        f"{nome_idioma} (código de idioma: {codigo_idioma}). "
                        "Preserve EXATAMENTE todos os números, valores monetários, "
                        "percentuais, nomes próprios, siglas técnicas (como EBITDA, "
                        "CAPEX, WIP) e a formatação (markdown, listas, quebras de "
                        "linha) do texto original. Se aparecer um marcador como "
                        "[[[GRAFICO_0]]], copie ele EXATAMENTE igual, sem traduzir "
                        "nem alterar nada dentro dos colchetes. Responda APENAS com "
                        "o texto traduzido, sem nenhum comentário adicional, sem "
                        "repetir o texto original e sem explicar o que você fez."
                    ),
                },
                {"role": "user", "content": texto_protegido},
            ],
            temperature=0.1,
            timeout=30,
        )
        traduzido = response.choices[0].message.content
        traduzido = traduzido.strip() if traduzido else texto_protegido

        # Recoloca os blocos de gráfico originais, intactos, no lugar dos
        # placeholders -- não importa o que a IA de tradução tenha feito
        # com o marcador (traduziu, manteve, alterou), o resultado final
        # sempre tem o JSON original, sem risco de quebrar.
        for indice, bloco in enumerate(blocos_grafico):
            traduzido = traduzido.replace(f'[[[GRAFICO_{indice}]]]', bloco, 1)

        return traduzido
    except Exception as erro_traducao:
        print(f"[agente_ia][traducao] falhou, devolvendo original: {erro_traducao}")
        return texto


# 🌟 NOVO: ferramentas que o Agente IA pode acionar sozinho pra consultar
# dados REAIS do cenário ativo do usuário (não só o que estiver nos
# relatórios anexados). Cada ferramenta é restrita ao cenário ativo --
# nunca dá pra consultar dados de outro cenário ou de outra empresa.
_FERRAMENTAS_CONSULTA_DADOS = [
    {
        "type": "function",
        "function": {
            "name": "listar_indicadores",
            "description": "Lista os nomes de todos os indicadores cadastrados no cenário ativo do usuário.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_indicador",
            "description": (
                "Retorna os valores reais, período a período, de um indicador "
                "específico do cenário ativo do usuário (ex: INPC, IPCA)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Nome do indicador, ex: \"INPC\""},
                },
                "required": ["nome"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_cambios",
            "description": "Lista as moedas/taxas de câmbio cadastradas no cenário ativo do usuário.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_cambio",
            "description": (
                "Retorna os valores reais, período a período, de uma taxa de "
                "câmbio específica do cenário ativo do usuário (ex: USD, EUR)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "moeda": {"type": "string", "description": "Código ou nome da moeda, ex: \"USD\" ou \"Dólar\""},
                },
                "required": ["moeda"],
            },
        },
    },
]


def _executar_ferramenta_consulta(nome_ferramenta, argumentos, cenario):
    """Executa uma ferramenta de consulta e devolve o resultado como texto."""
    if nome_ferramenta == 'listar_indicadores':
        return _lista_indicadores(cenario.id)

    if nome_ferramenta == 'consultar_indicador':
        nome = str(argumentos.get('nome', '')).strip()
        indicador = _buscar_indicador(cenario.id, nome) if nome else None
        if indicador is None:
            return f"Não encontrei nenhum indicador chamado \"{nome}\" nesse cenário.\n\n" + _lista_indicadores(cenario.id)
        return f"Indicador {indicador.ind_nome}:\n" + _lista_periodos_indicador(cenario, indicador)

    if nome_ferramenta == 'listar_cambios':
        return _lista_cambios(cenario.id)

    if nome_ferramenta == 'consultar_cambio':
        moeda = str(argumentos.get('moeda', '')).strip()
        cambio = _buscar_cambio(cenario.id, moeda) if moeda else None
        if cambio is None:
            return f"Não encontrei nenhuma taxa de câmbio \"{moeda}\" nesse cenário.\n\n" + _lista_cambios(cenario.id)
        return f"Câmbio {cambio.get_cam_moeda_display()} ({cambio.cam_moeda}):\n" + _lista_periodos_cambio(cenario, cambio)

    return ""


def _consultar_dados_cenario_se_necessario(mensagem_usuario: str, usuario) -> str:
    """
    🌟 NOVO: antes de gerar a resposta final, deixa o modelo decidir (numa
    chamada separada, rápida, só com essas 4 ferramentas) se a pergunta do
    usuário precisa de dados REAIS do cenário ativo -- indicadores e
    taxas de câmbio -- pra ser respondida bem (isso é comum quando o
    usuário pede um gráfico ou uma análise numérica que não está em
    nenhum relatório anexado). Se precisar, executa a(s) ferramenta(s)
    chamada(s) e devolve um texto pronto pra ser injetado na instrução de
    sistema principal -- mesmo mecanismo já usado pros dados extraídos de
    PDF.

    Roda numa chamada SEPARADA da principal (que já usa a ferramenta
    nativa de busca na internet) de propósito -- evita misturar dois
    tipos de "tool calling" diferentes numa única chamada, o que nem
    sempre é bem suportado.

    Sempre restrito ao cenário ATIVO do usuário -- nunca consulta outro
    cenário ou dados de outra empresa. Se der qualquer erro, devolve
    string vazia (a resposta principal simplesmente segue sem esses
    dados, como já era antes dessa funcionalidade existir).
    """
    perfil = getattr(usuario, 'perfilusuario', None)
    if perfil is None or perfil.cenario_ativo_id is None:
        return ""

    cenario = TbCenarios.objects_real.filter(id=perfil.cenario_ativo_id).first()
    if cenario is None:
        return ""

    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        resposta = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Decida se a pergunta do usuário precisa de dados REAIS do "
                        "cenário ativo dele (indicadores ou taxas de câmbio cadastrados) "
                        "pra ser respondida bem -- isso é comum quando ele pede um "
                        "gráfico, uma análise numérica, ou os valores de algo. Se "
                        "precisar, chame a ferramenta certa (pode chamar mais de uma se "
                        "precisar). Se a pergunta não tiver nada a ver com indicadores "
                        "ou câmbio do cenário (ex: só sobre um relatório anexado, ou uma "
                        "pergunta genérica), não chame nenhuma ferramenta."
                    ),
                },
                {"role": "user", "content": mensagem_usuario},
            ],
            tools=_FERRAMENTAS_CONSULTA_DADOS,
            tool_choice="auto",
            temperature=0.1,
            timeout=20,
        )

        chamadas = resposta.choices[0].message.tool_calls
        if not chamadas:
            return ""

        partes = []
        for chamada in chamadas[:5]:  # limite de segurança, evita loop maluco
            try:
                argumentos = json.loads(chamada.function.arguments or "{}")
            except Exception:
                argumentos = {}
            resultado = _executar_ferramenta_consulta(chamada.function.name, argumentos, cenario)
            if resultado:
                partes.append(resultado)

        if not partes:
            return ""

        return (
            "\n\n[DADOS REAIS DO CENÁRIO ATIVO -- consultados agora no banco de dados, "
            f"cenário {cenario.numero_sequencial}/{cenario.cen_nome}]:\n"
            + "\n".join(partes)
            + "\n\nUse esses dados com prioridade máxima pra responder (inclusive pra "
            "montar gráficos, se fizer sentido) -- são valores reais e atuais, não "
            "precisa pedir confirmação nem alegar falta de dados."
        )
    except Exception as erro_consulta:
        print(f"[agente_ia][consulta_dados] falhou, seguindo sem esses dados: {erro_consulta}")
        return ""


def _executar_agente_interno(mensagem_usuario: str, pdf_ids: list, usuario, _sinalizador_falha=None, salvar_historico=True) -> tuple:
    """
    Executa o agente fazendo RAG dinâmico sobre PDF, TXT ou XLSX (conforme a
    extensão de cada arquivo selecionado), capturando linhas numéricas
    contábeis de qualquer seção para evitar pontos cegos.

    'usuario' é o request.user autenticado: a memória de curto prazo (última
    interação) e o registro salvo no histórico ficam isolados por usuário.

    🌟 NOVO: antes de qualquer coisa, checa se o usuário está no meio do
    wizard de criar cenário (resposta a uma etapa) ou está pedindo pra
    começar um agora -- nesses dois casos, a mensagem nunca chega no LLM.
    """
    # 🌟 CORRIGIDO: os detectores de comando (abaixo) agora são checados
    # ANTES de "usuario_esta_em_fluxo" -- antes, um comando reconhecido
    # (tipo clicar "criar um cenário novo" nas Ações Comuns) enquanto
    # outro fluxo já estava em andamento (tipo o wizard de indicadores
    # esperando um nome) era engolido como se fosse resposta à pergunta
    # antiga, em vez de começar o comando novo. Agora, cada detector que
    # bater cancela o fluxo velho primeiro (se houver) e começa o novo.
    esta_em_fluxo = usuario_esta_em_fluxo(usuario)

    # 🌟 NOVO: "cancelar" digitado (ou clicado) fora de qualquer fluxo
    # com estado registrado -- acontece na etapa de baixar planilha
    # (indicador/câmbio), que não grava estado de fluxo no banco (é uma
    # resposta única, aguardando o usuário reenviar a planilha depois).
    # Sem esse tratamento, "cancelar" nesse ponto caía direto na IA, sem
    # fazer sentido nenhum. Só entra em ação quando NÃO há fluxo ativo --
    # dentro de um fluxo, o cancelamento já é tratado internamente por
    # cada etapa (processar_mensagem_fluxo).
    if not esta_em_fluxo and mensagem_usuario.strip().lower() == 'cancelar':
        resposta = "Ok, cancelado. Não tem mais nada pendente aqui -- me diz o que você precisa."
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra começar o wizard agora
    if _detectar_intencao_criar_cenario(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'criar'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_criar_cenario(usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra mudar tipo/período do cenário ativo
    if _detectar_intencao_mudar_cenario(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'mudar'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_mudar_cenario(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra excluir um ou mais cenários (fora do
    # ativo, e não sendo cenário base -- essa checagem em si é feita
    # dentro do fluxo, aqui só detecta a intenção e começa o wizard)
    if _detectar_intencao_excluir_cenario(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'excluir'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_excluir_cenario(usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra exportar os dados de otimização (Produto,
    # Produto-Mercado, Produto-Mercado-Fluxo, Equipamentos, Equipamentos-
    # Ordem, Custo Item) do cenário ativo -- checado ANTES do export
    # financeiro genérico (mais específico primeiro, senão cairia sempre
    # no financeiro por causa da palavra "exportar" em comum).
    if _detectar_intencao_exportar_dados_otimizacao(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'exportar_dados_otimizacao'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_exportar_dados_otimizacao_cenario_ativo(usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra exportar o relatório Excel do cenário ativo
    if _detectar_intencao_exportar_cenario(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'exportar_excel'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_exportar_excel_cenario_ativo(usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra baixar a planilha-modelo de um indicador
    if _detectar_download_planilha_indicador(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_download_planilha_indicador(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra baixar a planilha-modelo de um câmbio
    if _detectar_download_planilha_cambio(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_download_planilha_cambio(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário reenviou uma planilha preenchida (marcada nos
    # Relatórios) mencionando um indicador ou câmbio -- checa ANTES dos
    # wizards normais de editar, senão "atualizar indicador X com essa
    # planilha" cairia no fluxo de editar comum (que também reconhece
    # "atualizar" + "indicador").
    # 🌟 CORRIGIDO: se o usuário está EXATAMENTE na etapa de confirmar
    # uma planilha (respondendo sim/não/cancelar pra "Confirma a
    # aplicação?"), a mensagem tem que ir direto pro processamento do
    # fluxo abaixo -- nunca passar pela checagem de "planilha reenviada"
    # a seguir. Sem isso: como a caixinha do arquivo na área de
    # Relatórios continua marcada na tela depois do upload, pdf_ids
    # chegava preenchido de novo no clique de "Sim"/"Não"/"Cancelar", e
    # o código reprocessava o arquivo do zero (cancelando o fluxo de
    # confirmação em andamento) em vez de aplicar a resposta -- dando a
    # impressão de "nada acontece" (a mesma pergunta de confirmação
    # reaparecia, idêntica, em loop, e nunca era realmente respondida).
    etapa_atual_usuario = etapa_atual_do_usuario(usuario) if esta_em_fluxo else None
    if pdf_ids and etapa_atual_usuario not in ('ind_planilha_confirmar', 'cam_planilha_confirmar'):
        # 🌟 CORRIGIDO: identifica pelo nome do arquivo primeiro (não exige
        # que a mensagem mencione "indicador"/"câmbio" -- o nome do
        # arquivo, gerado por nós, já basta). O plano B (palavra-chave) só
        # entra em ação se a mensagem mencionar "planilha" explicitamente
        # -- sem essa exigência, qualquer "alterar câmbio X" com QUALQUER
        # arquivo marcado (mesmo um PDF de relatório sem relação nenhuma)
        # cairia aqui por engano, atropelando o wizard normal.
        tipo_planilha = identificar_tipo_planilha_reenviada(usuario, pdf_ids)
        menciona_planilha = PADRAO_PLANILHA.search(mensagem_usuario or "")

        if tipo_planilha == 'ind' or (tipo_planilha is None and menciona_planilha and PADRAO_INDICADOR.search(mensagem_usuario or "")):
            if esta_em_fluxo:
                cancelar_fluxo_ativo(usuario)
            resposta = _processar_planilha_indicador(usuario, mensagem_usuario, pdf_ids)
            _salvar_historico(usuario, mensagem_usuario, resposta)
            return resposta, []

        if tipo_planilha == 'cam' or (tipo_planilha is None and menciona_planilha and PADRAO_CAMBIO.search(mensagem_usuario or "")):
            if esta_em_fluxo:
                cancelar_fluxo_ativo(usuario)
            resposta = _processar_planilha_cambio(usuario, mensagem_usuario, pdf_ids)
            _salvar_historico(usuario, mensagem_usuario, resposta)
            return resposta, []

    # 🌟 NOVO: usuário pedindo pra mexer em indicadores (editar/criar/reajustar)
    if _detectar_intencao_indicadores(mensagem_usuario) and empresa_tem_acao_comum_habilitada(
            usuario, 'Indicadores', determinar_acao_indicadores(mensagem_usuario)):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_indicadores(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra mexer em taxas de câmbio (editar/criar/reajustar/eliminar)
    if _detectar_intencao_cambio(mensagem_usuario) and empresa_tem_acao_comum_habilitada(
            usuario, 'Câmbio', determinar_acao_cambio(mensagem_usuario)):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_cambio(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário perguntando o status/situação do cenário ativo
    if _detectar_intencao_status(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'status'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_consulta_status(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: "limpar, otimizar e consolidar" juntos -- ciclo completo
    # automático. Checado ANTES da ação única, senão cairia só no "limpar".
    if _detectar_intencao_ciclo_completo(mensagem_usuario) and empresa_tem_acao_comum_habilitada(usuario, 'Cenário', 'ciclo_completo'):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_ciclo_completo(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra limpar/otimizar/consolidar o cenário
    # ativo, a qualquer momento (não só durante a criação de um cenário novo)
    if _detectar_intencao_processar(mensagem_usuario) and empresa_tem_acao_comum_habilitada(
            usuario, 'Cenário', determinar_acao_processar(mensagem_usuario)):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_processar(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 Só chega aqui se NENHUM comando reconhecido bateu -- se o usuário
    # estava mesmo no meio de um fluxo, trata a mensagem como resposta à
    # etapa atual.
    if esta_em_fluxo:
        resposta = processar_mensagem_fluxo(usuario, mensagem_usuario)
        # 🌟 NOVO: sondagens automáticas silenciosas (front-end verificando
        # sozinho se uma task do Celery já terminou, a cada poucos
        # segundos) não devem virar entrada no histórico -- só a mensagem
        # de verdade que o usuário mandou (ou a que efetivamente muda de
        # estado) importa aqui.
        if salvar_historico:
            _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    try:
        # 1. Recupera as instruções de personalidade do seu Django Admin
        # 🌟 CORRIGIDO (multi-empresa): agora cada empresa tem o próprio
        # conjunto de comportamentos, com um "ativo" independente por
        # empresa -- sem filtrar por empresa aqui, essa consulta podia
        # pegar QUALQUER registro "ativo=True" do sistema, de uma
        # empresa completamente diferente da do usuário conversando.
        perfil_para_config = getattr(usuario, 'perfilusuario', None)
        empresa_id_config = perfil_para_config.empresa_efetiva_id() if perfil_para_config else None
        config_do_admin = AgenteConfig.objects.filter(ativo=True, empresa_id=empresa_id_config).first()
        system_instruction = getattr(config_do_admin, 'prompt_sistema', "Você é um assistente útil.")

        # 🌟 NOVO: injeta o idioma ativo do usuário (o mesmo escolhido no
        # seletor da interface) na instrução de sistema, pra o Agente
        # responder sempre nesse idioma -- independente do que a
        # instrução configurada no Admin diga (ou não diga) sobre
        # idioma. Isso vem DEPOIS do prompt do Admin, então tem
        # prioridade sobre qualquer instrução de idioma anterior nele.
        perfil_para_idioma = getattr(usuario, 'perfilusuario', None)
        if perfil_para_idioma is not None:
            codigo_idioma = perfil_para_idioma.idioma_efetivo()
            nome_idioma = dict(IDIOMA_CHOICES).get(codigo_idioma, codigo_idioma)
            system_instruction += (
                f"\n\nIMPORTANTE: Responda sempre em {nome_idioma} "
                f"(código de idioma: {codigo_idioma}), independentemente "
                f"do idioma usado na pergunta ou em qualquer outra "
                f"instrução acima."
            )

        # 🌟 NOVO: ensina o modelo a desenhar gráficos de verdade no chat,
        # quando isso ajudar mais que texto/tabela (evolução no tempo,
        # comparação entre categorias, proporção de um total, etc.). O
        # front-end (chat.html) sabe reconhecer um bloco ```chart``` com
        # essa estrutura JSON e desenha com a biblioteca Chart.js.
        system_instruction += (
            "\n\nVocê também pode desenhar um GRÁFICO DE VERDADE no chat, além de "
            "texto/tabelas, quando isso ajudar a visualizar os dados (evolução no "
            "tempo, comparação entre itens, proporção de um total, etc.). Pra isso, "
            "inclua na sua resposta um bloco separado assim, com JSON válido do "
            "formato do Chart.js (versão 4):\n\n"
            "```chart\n"
            "{\"type\": \"line\", \"data\": {\"labels\": [\"Jan\", \"Fev\", \"Mar\"], "
            "\"datasets\": [{\"label\": \"Receita\", \"data\": [100, 120, 90]}]}, "
            "\"options\": {\"plugins\": {\"title\": {\"display\": true, \"text\": \"Receita Mensal\"}}}}\n"
            "```\n\n"
            "Regras: escolha o \"type\" mais adequado (\"line\", \"bar\", \"pie\", "
            "\"doughnut\", etc.) pro que estiver mostrando. O JSON precisa ser "
            "válido (aspas duplas, sem comentários, sem vírgula sobrando). Pode "
            "incluir mais de um gráfico na mesma resposta se fizer sentido. NÃO "
            "invente números -- só desenhe gráfico quando tiver dados reais "
            "extraídos dos relatórios, fornecidos na conversa, ou consultados do "
            "cenário ativo (indicadores/câmbio); se não tiver dados suficientes, "
            "explique isso em texto em vez de inventar valores pro gráfico. O "
            "texto normal ao redor do bloco continua podendo explicar o que o "
            "gráfico mostra."
        )

        # 🌟 NOVO: marcador especial pra sinalizar quando você genuinamente
        # NÃO conseguiu ajudar o usuário (pergunta fora do escopo do
        # sistema, falta de dado que você não tem como obter, pergunta que
        # não faz sentido no contexto, etc.) -- isso dispara um e-mail de
        # alerta pros superusuários do sistema revisarem a conversa. NÃO é
        # pra casos onde você deu uma resposta parcial, incerta, ou pediu
        # mais detalhes -- é só pra quando você realmente não tem como
        # ajudar de jeito nenhum com essa mensagem.
        system_instruction += (
            "\n\nSe você REALMENTE não conseguir ajudar com a mensagem do usuário "
            "(pergunta totalmente fora do escopo desse sistema, falta alguma "
            "informação que você não tem como obter de jeito nenhum, ou a "
            "pergunta simplesmente não faz sentido no contexto), comece sua "
            "resposta com a marca exata \"[AGENTE_NAO_CONSEGUIU_RESPONDER]\" "
            "(sem nada antes dela), seguida do resto da sua resposta normal "
            "explicando o motivo pro usuário. NÃO use essa marca em respostas "
            "parciais, incertas, ou quando só está pedindo mais detalhes -- só "
            "quando genuinamente não há nada que você possa fazer."
        )

        # 🌟 NOVO: consulta dados REAIS do cenário ativo (indicadores, câmbio)
        # quando a pergunta parecer precisar deles -- não só o que estiver
        # nos relatórios anexados. Roda numa chamada separada e rápida, só
        # de tool-calling; se não achar nada relevante pra consultar, ou se
        # der qualquer erro, não muda nada (devolve string vazia).
        system_instruction += _consultar_dados_cenario_se_necessario(mensagem_usuario, usuario)

        # 2. 🌟 EXTRAÇÃO CIRÚRGICA DE TODOS OS ARQUIVOS SELECIONADOS (PDF, TXT ou XLSX):
        # 🌟 CORRIGIDO (multi-empresa): antes filtrava só por id__in e
        # ativo=True -- sem checar a empresa, alguém poderia mandar o id
        # de um relatório de OUTRA empresa manipulando a requisição
        # diretamente (a tela já filtra a listagem, mas isso sozinho não
        # impede um id "de fora" sendo enviado no POST). Agora exige que
        # o relatório pertença à empresa efetiva de quem está pedindo.
        perfil_usuario = getattr(usuario, 'perfilusuario', None)
        empresa_id_usuario = perfil_usuario.empresa_efetiva_id() if perfil_usuario else None
        if empresa_id_usuario is None:
            relatorios_selecionados = RelatorioPDF.objects.none()
        else:
            relatorios_selecionados = RelatorioPDF.objects.filter(id__in=pdf_ids, ativo=True, empresa_id=empresa_id_usuario)
        linhas_com_relevancia = []  # lista de (relevancia, linha_marcada)
        linhas_ja_vistas = set()    # evita duplicatas sem custo O(n) por checagem
        fontes_utilizadas = []

        for relatorio in relatorios_selecionados:
            if relatorio.arquivo:
                try:
                    caminho_fisico = relatorio.arquivo.path
                    if os.path.exists(caminho_fisico):
                        # 🌟 Escolhe o extrator certo conforme a extensão do arquivo
                        linhas_do_arquivo = _extrair_linhas_do_arquivo(caminho_fisico, relatorio.arquivo.name)

                        # Mantém o mesmo comportamento original: a fonte é registrada
                        # assim que o arquivo é aberto/lido com sucesso, independente
                        # de alguma linha ter batido no filtro financeiro
                        fontes_utilizadas.append(relatorio.titulo)

                        for origem, linha_limpa in linhas_do_arquivo:
                            # 🌟 Relevante se tiver termo contábil, referência de trimestre
                            # ou símbolo monetário/percentual — sempre combinado com um número
                            relevancia = _calcular_relevancia(linha_limpa)
                            if relevancia > 0 and CONTEM_NUMERO.search(linha_limpa):
                                # Monta uma estrutura identificando a origem para facilitar a leitura da IA
                                linha_marcada = f"[{origem}] {linha_limpa}"
                                if linha_marcada not in linhas_ja_vistas:
                                    linhas_ja_vistas.add(linha_marcada)
                                    linhas_com_relevancia.append((relevancia, linha_marcada))
                except Exception as arquivo_err:
                    print(f"⚠️ Erro ao ler arquivo do disco: {arquivo_err}")

        # 🌟 Ordena por relevância (mais critérios batidos primeiro) e monta o
        # contexto final sem cortar linhas no meio, priorizando as mais relevantes
        # quando o limite de caracteres não permite incluir tudo
        texto_contexto_pdf = _montar_contexto_priorizado(linhas_com_relevancia, LIMITE_CARACTERES_CONTEXTO)

        if texto_contexto_pdf:
            system_instruction += (
                f"\n\n[DADOS DINÂMICOS EXTRAÍDOS DOS RELATÓRIOS ANEXADOS]:\n{texto_contexto_pdf}\n\n"
                "ATENÇÃO: Os dados numéricos e as linhas acima contêm as tabelas estruturadas e demonstrações "
                "financeiras extraídas de todos os arquivos ativos selecionados (PDF, TXT ou planilha XLSX). "
                "Utilize essas informações contábeis com prioridade máxima para realizar a análise detalhada "
                "(calculando variações trimestrais, YoY, EBITDA, CAPEX, margens e endividamento) solicitada "
                "pelo usuário, interpretando os indicadores sem alegar falta de dados."
            )

        # 3. MEMÓRIA: Mantém a última interação DESTE usuário para poupar espaço no
        # pacote de tokens (🌟 antes pegava a última mensagem de QUALQUER usuário
        # no sistema — vazamento de contexto entre contas corrigido aqui)
        ultimos_logs = HistoricoAgente.objects.filter(usuario=usuario).order_by('-id')[:1]
        historico_langchain = []
        for log in reversed(ultimos_logs):
            historico_langchain.append(HumanMessage(content=log.comando_usuario))
            historico_langchain.append(AIMessage(content=log.resposta_ia))

        # 🌟 NOVO: tenta até 2 vezes -- se a primeira resposta vier vazia ou
        # com padrão de repetição em loop (falha momentânea observada do
        # lado do provedor de IA), tenta de novo automaticamente antes de
        # desistir, sem o usuário precisar perceber nada.
        resposta_final = ""
        for tentativa in range(2):
            if USAR_BUSCA_WEB:
                resposta_bruta = _gerar_resposta_com_busca_web(system_instruction, mensagem_usuario, historico_langchain)
            else:
                resposta_bruta = _gerar_resposta_original_langchain(system_instruction, mensagem_usuario, historico_langchain)
            resposta_final = str(resposta_bruta).strip()
            if not _resposta_suspeita(resposta_final):
                break

        if _resposta_suspeita(resposta_final):
            # Mesmo depois da segunda tentativa a resposta ainda veio ruim
            # -- NÃO salva no histórico (evita contaminar a memória da
            # próxima pergunta) e avisa o usuário com transparência.
            return (
                "Tive um problema para gerar uma resposta completa agora (a IA retornou "
                "algo vazio ou com repetição estranha, mesmo depois de tentar de novo). "
                "Isso costuma ser uma instabilidade momentânea -- tenta reformular ou "
                "repetir a pergunta em instantes."
            ), fontes_utilizadas

        # 🌟 NOVO: se o modelo sinalizou que não conseguiu ajudar de
        # verdade, tira a marca da resposta (o usuário não precisa ver
        # esse detalhe interno) e SINALIZA a falha pra camada de fora
        # (executar_agente_com_prompt_do_admin) -- o e-mail em si só é
        # disparado LÁ, depois da tradução, pra usar a resposta já no
        # idioma certo (esse ponto aqui ainda trabalha só com o texto
        # original em português).
        MARCA_NAO_RESPONDIDO = "[AGENTE_NAO_CONSEGUIU_RESPONDER]"
        if resposta_final.startswith(MARCA_NAO_RESPONDIDO):
            resposta_final = resposta_final[len(MARCA_NAO_RESPONDIDO):].strip()
            if _sinalizador_falha is not None:
                _sinalizador_falha['falhou'] = True

        # 7. Salva o registro real no banco para o histórico do Django Admin,
        # já vinculado ao usuário que fez a pergunta
        _salvar_historico(usuario, mensagem_usuario, resposta_final)

        return resposta_final, fontes_utilizadas

    except Exception as e:
        return f"Erro no processamento interno do servidor: {str(e)}", []


def executar_agente_com_prompt_do_admin(mensagem_usuario: str, pdf_ids: list, usuario, salvar_historico=True, eh_sondagem_automatica=False) -> tuple:
    """
    🌟 NOVO: camada fina por cima de `_executar_agente_interno` -- essa é a
    função que a view chama de verdade agora. Existe só pra garantir que a
    tradução pro idioma ativo do usuário aconteça em CIMA de qualquer
    caminho de resposta (LLM, fluxos determinísticos tipo "criar cenário",
    planilhas reenviadas, etc.), sem precisar duplicar a chamada de
    tradução em cada um dos vários "return" espalhados lá dentro -- alguns
    desses fluxos (as "Ações Comuns" do menu lateral, por exemplo) nunca
    passam pelo LLM, e por isso nunca passavam pela tradução antes.

    O histórico salvo no banco continua com o texto ORIGINAL (em
    português) -- só o que é devolvido pra tela nessa resposta é
    traduzido.

    🌟 CORRIGIDO: devolve TAMBÉM o texto original (sem tradução), como
    terceiro item da tupla -- `_extrair_opcoes_clicaveis` (em views.py)
    reconhece os botões procurando frases EXATAS em português no texto
    ("Indicadores cadastrados:", "(sim / não)", etc.); se ela recebesse o
    texto já traduzido, parava de reconhecer qualquer botão. Quem chama
    essa função precisa usar o texto ORIGINAL pra extrair as opções, e o
    texto TRADUZIDO só pra mostrar na tela.
    """
    sinalizador_falha = {}
    resposta_original, fontes = _executar_agente_interno(
        mensagem_usuario, pdf_ids, usuario, _sinalizador_falha=sinalizador_falha, salvar_historico=salvar_historico
    )

    # 🌟 NOVO: sondagens automáticas silenciosas, enquanto a etapa AINDA
    # está esperando o Celery (ou seja, a resposta é só mais um "ainda
    # processando" repetido), pulam a tradução por IA -- ela é uma
    # chamada de rede a um modelo externo, cara e completamente
    # desperdiçada aqui, já que a mesma checagem se repete a cada poucos
    # segundos até o processo terminar (dezenas de chamadas idênticas
    # numa tarefa de 10 minutos). A resposta final, quando a etapa
    # termina de esperar, sempre traduz normalmente -- só o "ainda
    # rodando" intermediário fica sem tradução (a barra de progresso no
    # front-end mostra esse texto em português nesse meio-tempo).
    if eh_sondagem_automatica and etapa_atual_do_usuario(usuario) in ETAPAS_AGUARDANDO_CELERY:
        return resposta_original, fontes, resposta_original

    perfil_para_idioma = getattr(usuario, 'perfilusuario', None)
    codigo_idioma_resposta = perfil_para_idioma.idioma_efetivo() if perfil_para_idioma else None
    resposta_traduzida = _traduzir_resposta_se_necessario(resposta_original, codigo_idioma_resposta)
    resposta_traduzida = _forcar_traducao_palavras_chave(resposta_traduzida, codigo_idioma_resposta)

    if sinalizador_falha.get('falhou'):
        _avisar_superusers_falha_resposta(usuario, mensagem_usuario, resposta_traduzida, codigo_idioma_resposta)

    return resposta_traduzida, fontes, resposta_original