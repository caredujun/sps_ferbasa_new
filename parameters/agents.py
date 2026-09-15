import os
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
from .models import AgenteConfig, HistoricoAgente, RelatorioPDF

# 🌟 NOVO: wizard de criação de cenário via conversa
from .fluxo_criar_cenario import (
    usuario_esta_em_fluxo, cancelar_fluxo_ativo, iniciar_fluxo_criar_cenario, iniciar_fluxo_mudar_cenario,
    iniciar_fluxo_indicadores, iniciar_fluxo_cambio, processar_mensagem_fluxo,
    iniciar_download_planilha_indicador, iniciar_download_planilha_cambio,
    identificar_tipo_planilha_reenviada, iniciar_fluxo_processar, iniciar_consulta_status,
    iniciar_ciclo_completo,
    _processar_planilha_indicador, _processar_planilha_cambio,
)

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
    r"elimin|apag|exclu[ií]|delet|remov",
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
        HistoricoAgente.objects.create(
            usuario=usuario,
            comando_usuario=mensagem_usuario,
            resposta_ia=resposta
        )
    except Exception:
        pass


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


def executar_agente_com_prompt_do_admin(mensagem_usuario: str, pdf_ids: list, usuario) -> tuple:
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

    # 🌟 NOVO: usuário pedindo pra começar o wizard agora
    if _detectar_intencao_criar_cenario(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_criar_cenario(usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra mudar tipo/período do cenário ativo
    if _detectar_intencao_mudar_cenario(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_mudar_cenario(usuario, mensagem_usuario)
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
    if pdf_ids:
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
    if _detectar_intencao_indicadores(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_indicadores(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra mexer em taxas de câmbio (editar/criar/reajustar/eliminar)
    if _detectar_intencao_cambio(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_fluxo_cambio(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário perguntando o status/situação do cenário ativo
    if _detectar_intencao_status(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_consulta_status(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: "limpar, otimizar e consolidar" juntos -- ciclo completo
    # automático. Checado ANTES da ação única, senão cairia só no "limpar".
    if _detectar_intencao_ciclo_completo(mensagem_usuario):
        if esta_em_fluxo:
            cancelar_fluxo_ativo(usuario)
        resposta = iniciar_ciclo_completo(usuario, mensagem_usuario)
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    # 🌟 NOVO: usuário pedindo pra limpar/otimizar/consolidar o cenário
    # ativo, a qualquer momento (não só durante a criação de um cenário novo)
    if _detectar_intencao_processar(mensagem_usuario):
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
        _salvar_historico(usuario, mensagem_usuario, resposta)
        return resposta, []

    try:
        # 1. Recupera as instruções de personalidade do seu Django Admin
        config_do_admin = AgenteConfig.objects.filter(ativo=True).first()
        system_instruction = getattr(config_do_admin, 'prompt_sistema', "Você é um assistente útil.")

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

        # 7. Salva o registro real no banco para o histórico do Django Admin,
        # já vinculado ao usuário que fez a pergunta
        _salvar_historico(usuario, mensagem_usuario, resposta_final)

        return resposta_final, fontes_utilizadas

    except Exception as e:
        return f"Erro no processamento interno do servidor: {str(e)}", []