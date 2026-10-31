"""
🌟 NOVO (24/10/2026): o Agente IA analisa PLANILHAS anexadas nos "Relatórios Agente IA" (XLSX ou CSV) com o arquivo
INTEIRO, do jeito que um analista faria: em vez de colar linhas no pedido (o que estoura o limite de tamanho da IA e
obrigava a cortar o arquivo em 5.000 caracteres), a IA recebe só a ESTRUTURA de cada aba (colunas, quantidade de linhas,
exemplos) e FERRAMENTAS de consulta -- filtrar, agrupar, somar, média, média ponderada, dividir uma coluna por outra,
listar valores. O servidor executa cada consulta sobre o arquivo inteiro e devolve só o resultado (pequeno); a IA pode
pedir várias consultas seguidas até ter a resposta.

Segurança: a IA NÃO escreve nem executa código -- só escolhe parâmetros das ferramentas abaixo, que o servidor valida.
"""
import csv
import io
import json
import os
import re
import time
import unicodedata
from collections import OrderedDict

import openpyxl

MAX_RODADAS = 10                 # consultas seguidas que a IA pode pedir numa pergunta
MAX_LINHAS_RESULTADO = 300      # linhas devolvidas por consulta
MAX_CARACTERES_RESULTADO = 3500 # tamanho máximo do texto de um resultado (o resto é avisado como cortado)
MAX_TOKENS_RESPOSTA = None      # 🌟 24/10/2026: não limitado (o provedor pode somar o limite ao pedido nos tokens por minuto)
MAX_INSTRUCAO_BASE = 1200       # caracteres do prompt do Admin/idioma/SPS usados na análise de planilha
MAX_ESPERA_LIMITE_S = 180        # espera total quando a IA pede para aguardar (limite de uso por minuto)
EXTENSOES_PLANILHA = ('.xlsx', '.xlsm', '.csv')

_CACHE = OrderedDict()          # (caminho, mtime) -> planilha carregada (as últimas 4)


# ---------------------------------------------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------------------------------------------
def eh_planilha(nome_arquivo):
    return os.path.splitext(nome_arquivo or '')[1].lower() in EXTENSOES_PLANILHA


def _norm(t):
    t = unicodedata.normalize('NFD', str(t if t is not None else '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', t).strip().lower()


def _num(v):
    """Número da célula (float) ou None. Aceita '1.234,56', '1234.56', '12,5%'."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace('R$', '').replace('US$', '').replace('%', '').strip()
    if not t or not re.match(r'^[-+]?[\d.,\s]+$', t):
        return None
    t = t.replace(' ', '')
    if ',' in t and '.' in t:
        t = t.replace('.', '').replace(',', '.') if t.rfind(',') > t.rfind('.') else t.replace(',', '')
    elif ',' in t:
        t = t.replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return None


def _detectar_cabecalho(linhas):
    """Linha do cabeçalho: entre as 25 primeiras, a que tem MAIS células de texto preenchidas (com pelo menos 2), e
    abaixo dela pelo menos uma linha com número. As linhas acima viram 'títulos' da aba."""
    melhor, idx_melhor = 1, None
    for i, l in enumerate(linhas[:25]):
        textos = [c for c in l if c is not None and str(c).strip() != '' and _num(c) is None]
        if len(textos) > melhor and any(any(_num(c) is not None for c in x) for x in linhas[i + 1:i + 6]):
            melhor, idx_melhor = len(textos), i
    return idx_melhor


def _montar_aba(nome, linhas):
    linhas = [list(l) for l in linhas if l is not None]
    while linhas and all(c is None or str(c).strip() == '' for c in linhas[-1]):
        linhas.pop()
    if not linhas:
        return None
    h = _detectar_cabecalho(linhas)
    if h is None:                         # sem cabeçalho reconhecível: colunas numeradas
        n = max(len(l) for l in linhas)
        colunas, dados, titulos = [f'Coluna {k + 1}' for k in range(n)], linhas, []
    else:
        cab = linhas[h]
        n = max(len(cab), max((len(l) for l in linhas[h + 1:]), default=0))
        colunas, vistos = [], {}
        for k in range(n):
            c = str(cab[k]).strip() if k < len(cab) and cab[k] is not None and str(cab[k]).strip() else f'Coluna {k + 1}'
            if c in vistos:
                vistos[c] += 1
                c = f'{c} ({vistos[c]})'
            else:
                vistos[c] = 1
            colunas.append(c)
        dados = linhas[h + 1:]
        titulos = [str(next(c for c in l if c is not None and str(c).strip())).strip()
                   for l in linhas[:h] if any(c is not None and str(c).strip() for c in l)]
    registros = []
    for l in dados:
        if all(c is None or str(c).strip() == '' for c in l):
            continue
        registros.append([(l[k] if k < len(l) else None) for k in range(len(colunas))])
    return {'nome': nome, 'titulos': titulos[:6], 'colunas': colunas, 'linhas': registros}


def carregar_planilha(caminho, nome_exibicao):
    """{'nome': título do relatório, 'abas': [{'nome', 'titulos', 'colunas', 'linhas'}]} -- com cache por arquivo."""
    chave = (caminho, os.path.getmtime(caminho))
    if chave in _CACHE:
        _CACHE.move_to_end(chave)
        return _CACHE[chave]
    abas = []
    ext = os.path.splitext(caminho)[1].lower()
    if ext == '.csv':
        bruto = None
        for enc in ('utf-8-sig', 'latin-1'):
            try:
                with open(caminho, 'r', encoding=enc, newline='') as f:
                    bruto = f.read()
                break
            except UnicodeDecodeError:
                continue
        if bruto is not None:
            try:
                dialeto = csv.Sniffer().sniff(bruto[:5000], delimiters=';,\t|')
            except csv.Error:
                dialeto = csv.excel
            aba = _montar_aba('CSV', list(csv.reader(io.StringIO(bruto), dialeto)))
            if aba:
                abas.append(aba)
    else:
        wb = openpyxl.load_workbook(caminho, data_only=True, read_only=True)
        try:
            for ws in wb.worksheets:
                aba = _montar_aba(ws.title, list(ws.iter_rows(values_only=True)))
                if aba:
                    abas.append(aba)
        finally:
            wb.close()
    plan = {'nome': nome_exibicao, 'abas': abas}
    _CACHE[chave] = plan
    while len(_CACHE) > 4:
        _CACHE.popitem(last=False)
    return plan


# ---------------------------------------------------------------------------------------------------------------
# estrutura (o que a IA vê das planilhas)
# ---------------------------------------------------------------------------------------------------------------
def _fmt(v):
    if v is None:
        return ''
    if isinstance(v, float):
        if v != v:
            return ''
        if abs(v) >= 1000 or v == int(v):
            return f'{v:.2f}'.rstrip('0').rstrip('.') if v != int(v) else str(int(v))
        return f'{v:.6g}'
    s = str(v)
    return s if len(s) <= 80 else s[:77] + '...'


def esquema_texto(planilhas):
    partes = []
    for p in planilhas:
        partes.append(f'ARQUIVO "{p["nome"]}":')
        for a in p['abas']:
            linhas = a['linhas']
            descr = []
            for k, c in enumerate(a['colunas']):
                vals = [l[k] for l in linhas if l[k] is not None and str(l[k]).strip() != '']
                nums = [x for x in (_num(v) for v in vals) if x is not None]
                if vals and len(nums) >= 0.8 * len(vals):
                    descr.append(f'{c} [num]')
                else:
                    distintos = list(OrderedDict.fromkeys(str(v).strip() for v in vals))
                    ex = ', '.join(f'"{_fmt(x)[:24]}"' for x in distintos[:3])
                    descr.append(f'{c} [txt {len(distintos)}: {ex}]')
            tit = (' | ' + ' / '.join(t[:60] for t in a['titulos'][:2])) if a['titulos'] else ''
            partes.append(f'- Aba "{a["nome"]}" ({len(linhas)} linhas){tit}\n  colunas: ' + '; '.join(descr))
    return '\n'.join(partes)


# ---------------------------------------------------------------------------------------------------------------
# ferramentas
# ---------------------------------------------------------------------------------------------------------------
_OPERADORES = ['contem', 'nao_contem', 'igual', 'diferente', 'comeca_com', 'maior', 'maior_igual', 'menor', 'menor_igual']
_FUNCOES = ['soma', 'media', 'media_ponderada', 'minimo', 'maximo', 'contagem', 'contagem_distinta']

FERRAMENTAS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_planilha",
            "description": (
                "Consulta uma aba de uma planilha anexada, sobre TODAS as linhas: filtra, agrupa e agrega (soma, média, "
                "média ponderada, mínimo, máximo, contagem) e calcula divisões entre colunas do resultado. Sem "
                "'agregacoes', devolve as linhas filtradas (as colunas pedidas). Use para qualquer número que precisar."),
            "parameters": {
                "type": "object",
                "properties": {
                    "arquivo": {"type": "string", "description": "Nome do arquivo (pode omitir se houver só um)."},
                    "aba": {"type": "string", "description": "Nome da aba."},
                    "filtros": {"type": "array", "description": "Condições (todas precisam valer).", "items": {
                        "type": "object", "properties": {
                            "coluna": {"type": "string"},
                            "operador": {"type": "string", "enum": _OPERADORES},
                            "valor": {"type": "string", "description": "Texto ou número. 'contem' ignora maiúsculas e acentos."}},
                        "required": ["coluna", "operador", "valor"]}},
                    "agrupar_por": {"type": "array", "items": {"type": "string"}, "description": "Colunas para agrupar (ex.: [\"Mês\"])."},
                    "agregacoes": {"type": "array", "items": {
                        "type": "object", "properties": {
                            "coluna": {"type": "string"},
                            "funcao": {"type": "string", "enum": _FUNCOES},
                            "peso": {"type": "string", "description": "Coluna de peso (só para media_ponderada)."},
                            "nome": {"type": "string", "description": "Nome da coluna no resultado (opcional)."}},
                        "required": ["coluna", "funcao"]}},
                    "calculos": {"type": "array", "description": "Colunas calculadas DEPOIS da agregação: numerador / denominador x multiplicar_por.",
                                 "items": {"type": "object", "properties": {
                                     "nome": {"type": "string"}, "numerador": {"type": "string"}, "denominador": {"type": "string"},
                                     "multiplicar_por": {"type": "number"}},
                                     "required": ["nome", "numerador", "denominador"]}},
                    "colunas": {"type": "array", "items": {"type": "string"}, "description": "Colunas a devolver (sem agregação)."},
                    "ordenar_por": {"type": "string", "description": "Coluna do resultado para ordenar."},
                    "decrescente": {"type": "boolean"},
                    "limite": {"type": "integer", "description": f"Máximo de linhas devolvidas (até {MAX_LINHAS_RESULTADO})."},
                },
                "required": ["aba"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "valores_distintos",
            "description": "Lista os valores distintos de uma coluna de texto (com quantas linhas têm cada um) -- para achar o nome exato de um item, equipamento, mês...",
            "parameters": {
                "type": "object",
                "properties": {
                    "arquivo": {"type": "string"},
                    "aba": {"type": "string"},
                    "coluna": {"type": "string"},
                    "contem": {"type": "string", "description": "Só os valores que contêm este texto (opcional)."},
                    "limite": {"type": "integer"},
                },
                "required": ["aba", "coluna"],
            },
        },
    },
]


class ErroConsulta(Exception):
    pass


def _achar(lista, nome, rotulo):
    """Acha pelo nome sem diferenciar maiúsculas/acentos; aceita pedaço do nome se for único."""
    alvo = _norm(nome)
    exatos = [x for x in lista if _norm(x) == alvo]
    if exatos:
        return exatos[0]
    parciais = [x for x in lista if alvo and alvo in _norm(x)]
    if len(parciais) == 1:
        return parciais[0]
    raise ErroConsulta(f'{rotulo} "{nome}" não encontrado(a). Opções: ' + '; '.join(lista[:60]))


def _aba(planilhas, arquivo, aba):
    if not planilhas:
        raise ErroConsulta('Nenhuma planilha anexada.')
    if arquivo:
        nomes = [p['nome'] for p in planilhas]
        p = planilhas[nomes.index(_achar(nomes, arquivo, 'Arquivo'))]
    elif len(planilhas) == 1:
        p = planilhas[0]
    else:
        # sem arquivo: a aba com esse nome no primeiro arquivo que a tiver
        for p in planilhas:
            try:
                _achar([a['nome'] for a in p['abas']], aba, 'Aba')
                break
            except ErroConsulta:
                continue
    nomes_abas = [a['nome'] for a in p['abas']]
    return p['abas'][nomes_abas.index(_achar(nomes_abas, aba, 'Aba'))]


def _filtro(a, f):
    k = a['colunas'].index(_achar(a['colunas'], f.get('coluna', ''), 'Coluna'))
    op = f.get('operador', 'igual')
    if op not in _OPERADORES:
        raise ErroConsulta(f'Operador "{op}" inválido. Use: ' + ', '.join(_OPERADORES))
    valor = f.get('valor', '')
    vn, vt = _num(valor), _norm(valor)

    def ok(l):
        c = l[k]
        if op in ('maior', 'maior_igual', 'menor', 'menor_igual'):
            x = _num(c)
            if x is None or vn is None:
                # comparação de texto (ex.: Mês '2024/01' >= '2023/12')
                x, y = _norm(c), vt
            else:
                y = vn
            return {'maior': x > y, 'maior_igual': x >= y, 'menor': x < y, 'menor_igual': x <= y}[op]
        t = _norm(c)
        if op == 'contem':
            return vt in t
        if op == 'nao_contem':
            return vt not in t
        if op == 'comeca_com':
            return t.startswith(vt)
        igual = (_num(c) == vn) if (vn is not None and _num(c) is not None) else (t == vt)
        return igual if op == 'igual' else not igual
    return ok


def _tabela_texto(colunas, linhas, total, aviso=''):
    cab = ' | '.join(colunas)
    corpo, tam, mostradas = [], len(cab), 0
    for l in linhas:
        s = ' | '.join(_fmt(v) for v in l)
        if tam + len(s) + 1 > MAX_CARACTERES_RESULTADO:
            break
        corpo.append(s)
        tam += len(s) + 1
        mostradas += 1
    rodape = f'\n({total} linha(s) no resultado' + (f'; mostradas {mostradas}' if mostradas < total else '') + ')'
    return cab + '\n' + '\n'.join(corpo) + rodape + (('\n' + aviso) if aviso else '')


def consultar_planilha(planilhas, args):
    a = _aba(planilhas, args.get('arquivo'), args.get('aba', ''))
    filtros = [_filtro(a, f) for f in (args.get('filtros') or [])]
    linhas = [l for l in a['linhas'] if all(f(l) for f in filtros)]
    limite = max(1, min(int(args.get('limite') or 100), MAX_LINHAS_RESULTADO))
    ags = args.get('agregacoes') or []
    if not ags:
        cols = [_achar(a['colunas'], c, 'Coluna') for c in (args.get('colunas') or [])] or a['colunas']
        ks = [a['colunas'].index(c) for c in cols]
        res = [[l[k] for k in ks] for l in linhas]
        res = _ordenar(res, cols, args)
        return _tabela_texto(cols, res[:limite], len(res))
    grupos = [_achar(a['colunas'], c, 'Coluna') for c in (args.get('agrupar_por') or [])]
    kg = [a['colunas'].index(c) for c in grupos]
    specs = []
    for g in ags:
        fn = g.get('funcao', 'soma')
        if fn not in _FUNCOES:
            raise ErroConsulta(f'Função "{fn}" inválida. Use: ' + ', '.join(_FUNCOES))
        col = _achar(a['colunas'], g.get('coluna', ''), 'Coluna')
        peso = _achar(a['colunas'], g['peso'], 'Coluna') if fn == 'media_ponderada' else None
        if fn == 'media_ponderada' and not g.get('peso'):
            raise ErroConsulta('media_ponderada precisa de "peso".')
        specs.append((g.get('nome') or f'{fn}({col}' + (f' por {peso}' if peso else '') + ')', fn,
                      a['colunas'].index(col), a['colunas'].index(peso) if peso else None))
    acc = OrderedDict()
    for l in linhas:
        chave = tuple(l[k] for k in kg)
        r = acc.setdefault(chave, [[] for _ in specs])
        for i, (_n, fn, k, kp) in enumerate(specs):
            if fn in ('contagem', 'contagem_distinta'):
                r[i].append(l[k])
            else:
                x = _num(l[k])
                if x is not None:
                    r[i].append((x, _num(l[kp]) if kp is not None else None))
    cols = grupos + [s[0] for s in specs]
    res = []
    for chave, rs in acc.items():
        out = list(chave)
        for (_n, fn, _k, _kp), vals in zip(specs, rs):
            if fn == 'contagem':
                out.append(float(sum(1 for v in vals if v is not None and str(v).strip() != '')))
            elif fn == 'contagem_distinta':
                out.append(float(len({str(v).strip() for v in vals if v is not None and str(v).strip() != ''})))
            elif not vals:
                out.append(None)
            elif fn == 'soma':
                out.append(sum(x for x, _w in vals))
            elif fn == 'media':
                out.append(sum(x for x, _w in vals) / len(vals))
            elif fn == 'minimo':
                out.append(min(x for x, _w in vals))
            elif fn == 'maximo':
                out.append(max(x for x, _w in vals))
            else:
                sw = sum(w for _x, w in vals if w is not None)
                out.append(sum(x * w for x, w in vals if w is not None) / sw if sw else None)
        res.append(out)
    for c in args.get('calculos') or []:
        kn = cols.index(_achar(cols, c.get('numerador', ''), 'Coluna do resultado'))
        kd = cols.index(_achar(cols, c.get('denominador', ''), 'Coluna do resultado'))
        mult = float(c.get('multiplicar_por') or 1)
        for r in res:
            n, d = _num(r[kn]), _num(r[kd])
            r.append(n / d * mult if n is not None and d not in (None, 0) else None)
        cols.append(c.get('nome') or f'{cols[kn]} / {cols[kd]}')
    res = _ordenar(res, cols, args)
    aviso = '' if filtros or not grupos or len(linhas) else 'Nenhuma linha.'
    return _tabela_texto(cols, res[:limite], len(res), aviso)


def _ordenar(res, cols, args):
    o = args.get('ordenar_por')
    if not o:
        return res
    k = cols.index(_achar(cols, o, 'Coluna para ordenar'))

    def chave(r):
        x = _num(r[k])
        return (0, x) if x is not None else (1, _norm(r[k]))
    return sorted(res, key=chave, reverse=bool(args.get('decrescente')))


def valores_distintos(planilhas, args):
    a = _aba(planilhas, args.get('arquivo'), args.get('aba', ''))
    k = a['colunas'].index(_achar(a['colunas'], args.get('coluna', ''), 'Coluna'))
    filtro = _norm(args.get('contem') or '')
    cont = OrderedDict()
    for l in a['linhas']:
        v = l[k]
        if v is None or str(v).strip() == '':
            continue
        if filtro and filtro not in _norm(v):
            continue
        cont[str(v).strip()] = cont.get(str(v).strip(), 0) + 1
    limite = max(1, min(int(args.get('limite') or 100), MAX_LINHAS_RESULTADO))
    itens = list(cont.items())
    return _tabela_texto([a['colunas'][k], 'linhas'], [[v, float(n)] for v, n in itens[:limite]], len(itens))


def executar_ferramenta(planilhas, nome, args):
    try:
        if nome == 'consultar_planilha':
            return consultar_planilha(planilhas, args)
        if nome == 'valores_distintos':
            return valores_distintos(planilhas, args)
        return f'Ferramenta "{nome}" não existe.'
    except ErroConsulta as erro:
        return f'ERRO NA CONSULTA: {erro}'
    except Exception as erro:
        return f'ERRO NA CONSULTA: {type(erro).__name__}: {erro}'


# ---------------------------------------------------------------------------------------------------------------
# conversa com a IA (rodadas de ferramenta)
# ---------------------------------------------------------------------------------------------------------------
INSTRUCAO_PLANILHAS = (
    "Você é um analista de dados sênior. O usuário anexou planilha(s) e você tem acesso ao arquivo INTEIRO através das "
    "ferramentas 'consultar_planilha' e 'valores_distintos' -- você não vê as linhas, só a ESTRUTURA abaixo. Para "
    "qualquer número, consulte: filtre, agrupe por mês/ano/item, some, faça média ou média ponderada e divida colunas. "
    "Faça quantas consultas precisar (até " + str(MAX_RODADAS) + "), uma por vez ou várias juntas; se uma consulta der "
    "erro, corrija o nome da aba/coluna usando a lista de opções do erro e tente de novo. Se não souber o nome exato de "
    "um item, use 'valores_distintos' com 'contem'.\n\n"
    "SEMPRE consulte a planilha antes de responder, a cada pergunta -- inclusive para refazer uma tabela ou um gráfico "
    "de uma resposta anterior: a conversa anterior chega CORTADA e não serve como fonte de números. Nunca responda de "
    "forma genérica (\"pode estar relacionado a fatores...\", \"revisar o cenário\", \"consultar indicadores\") quando a "
    "resposta pode ser buscada na planilha.\n\n"
    "PERGUNTAS DE CAUSA ('por que o custo aumentou a partir de X?'): compare ANTES x DEPOIS com consultas, do produto "
    "para os insumos: (1) o custo por unidade do produto (total, variável, fixo) antes e depois; (2) no equipamento que "
    "faz o produto, por insumo, antes e depois: soma do Valor total / soma da Produção do equipamento (= valor do insumo "
    "por unidade produzida), o consumo específico (soma da Quantidade / soma da Produção) e o custo do insumo por unidade "
    "(soma do Valor total / soma da Quantidade) -- assim separa efeito CONSUMO de efeito PREÇO; (3) ordene os insumos pela "
    "variação do valor por unidade produzida e explique os maiores; (4) se o maior for um item produzido no próprio "
    "fluxo, repita (2) no equipamento que o produz. Filtre períodos com 'maior_igual'/'menor' na coluna Mês (texto "
    "'AAAA/MM'). Mostre uma tabela com antes, depois e variação e diga quanto da variação total cada item explica.\n\n"
    "REGRAS: use SOMENTE números que vieram das ferramentas (ou contas simples sobre eles, mostrando a conta em texto "
    "simples, sem LaTeX). Nunca diga que faltam dados sem antes consultar. Cuidado com colunas que se repetem em cada "
    "linha (ex.: a produção do equipamento aparece em todas as linhas de insumo dele): não some essas colunas entre "
    "itens diferentes -- agrupe primeiro ou use a aba certa. Média de valores por unidade ao longo de meses = média "
    "PONDERADA pela produção (media_ponderada com peso = produção), não média simples. Formate números no padrão "
    "brasileiro (1.234.567,89; 0,1823), nunca separe dígitos com espaço. Responda em markdown simples (parágrafos curtos, "
    "listas com '- ', tabelas markdown com até 6 colunas). Se o usuário pedir gráfico (ou um gráfico ajudar), inclua um "
    "bloco separado: uma linha ```chart, um JSON válido de configuração do Chart.js versão 4 (type 'line' ou 'bar', "
    "data.labels e data.datasets com label e data numéricos, sem funções) e uma linha ```.\n\n"
    "PLANILHA EXPORTADA PELO SPS ('Exportar Dados' do fluxo de produção): as abas 'Produto período' e 'Produto mês a mês' "
    "têm o custo por unidade do produto final; 'Insumo por un produto' tem o consumo de cada insumo POR UNIDADE DO "
    "PRODUTO FINAL, mês a mês, já somado em toda a cadeia (a linha com Mês = 'Média do período' é a média ponderada do "
    "período) -- use-a para consumo específico de energia/matéria-prima no produto; 'Equipamentos' e 'Equipamentos mês "
    "a mês' têm produção e custo por unidade de cada equipamento; 'Consumos período' e 'Consumos mês a mês' têm "
    "consumo, consumo específico, custo do insumo por unidade e valor total por equipamento; 'Contribuição custo' tem a "
    "parte de cada equipamento no custo por unidade do produto final; 'Balanço itens' tem o balanço de cada item."
)


def _pedido_grande_demais(erro):
    """413 / 'Request too large' / 'reduce your message size': o PEDIDO passa do limite de tokens por minuto -- esperar
    não adianta, tem que diminuir."""
    msg = str(erro).lower()
    return '413' in msg or 'too large' in msg or 'reduce your message' in msg or 'context_length' in msg or 'maximum context' in msg


def _erro_de_limite(erro):
    msg = (str(erro) + ' ' + type(erro).__name__).lower()
    return not _pedido_grande_demais(erro) and ('429' in msg or 'rate_limit' in msg or 'ratelimit' in msg or 'rate limit' in msg)


def _erro_de_ferramenta(erro):
    msg = str(erro).lower()
    return 'tool_use_failed' in msg or 'failed to call a function' in msg or 'tool call validation' in msg or 'failed_generation' in msg


class ErroPlanilhaIA(Exception):
    """Erro já explicado para o usuário (a mensagem vai para a tela)."""


def _compactar(mensagens, nivel):
    """Diminui o pedido: resultados de consultas ANTERIORES à última rodada ficam resumidos (nível 1: 900 caracteres;
    nível 2: 350 e sem a conversa anterior; nível 3: também a última rodada resumida)."""
    if nivel <= 0:
        return mensagens
    lim_antigo = 900 if nivel == 1 else 350
    ult_assist = max((i for i, m in enumerate(mensagens) if m.get('role') == 'assistant' and m.get('tool_calls')), default=-1)
    novas = []
    for i, m in enumerate(mensagens):
        if nivel >= 2 and i > 0 and m.get('role') in ('user', 'assistant') and not m.get('tool_calls') and i < len(mensagens) - 1 \
                and any(x.get('role') == 'user' for x in mensagens[i + 1:]):
            continue                       # conversa anterior sai
        if m.get('role') == 'tool':
            lim = lim_antigo if (i < ult_assist or nivel >= 3) else None
            if lim and len(m['content']) > lim:
                m = dict(m, content=m['content'][:lim] + '\n[... resultado resumido para caber no limite da IA -- consulte de novo, com filtro, se precisar]')
        novas.append(m)
    return novas


def _espera_pedida(erro, padrao=15.0):
    try:
        h = getattr(getattr(erro, 'response', None), 'headers', None) or {}
        v = h.get('retry-after') or h.get('Retry-After')
        if v:
            return min(float(v), 60.0)
    except Exception:
        pass
    m = re.search(r'try again in ([\d.]+)\s*s', str(erro), re.IGNORECASE)
    return min(float(m.group(1)) + 0.5, 60.0) if m else padrao


def responder_com_planilhas(instrucao_base, mensagem_usuario, historico, planilhas, contexto_extra=''):
    """instrucao_base: instrução curta do Agente (prompt do Admin, idioma, nome SPS...); historico: [(papel, texto)].
    Devolve o texto da resposta final."""
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    sistema = ((instrucao_base or '')[:MAX_INSTRUCAO_BASE] + "\n\n" + INSTRUCAO_PLANILHAS + "\n\nESTRUTURA DAS PLANILHAS ANEXADAS:\n" + esquema_texto(planilhas)
               + (("\n\n" + contexto_extra) if contexto_extra else ''))
    mensagens = [{"role": "system", "content": sistema}]
    for papel, texto in historico or []:
        mensagens.append({"role": papel, "content": texto})
    mensagens.append({"role": "user", "content": mensagem_usuario})
    esperado = 0.0
    rodada = 0
    sem_required = False
    nivel = 0            # compactação do pedido (sobe quando a IA recusa o tamanho)
    sem_max_tokens = False
    falhas_ferramenta = 0
    while True:
        usar_ferramentas = rodada < MAX_RODADAS
        envio = _compactar(mensagens, nivel)
        kwargs = {"model": "openai/gpt-oss-120b", "messages": envio, "temperature": 0.1, "timeout": 90}
        if MAX_TOKENS_RESPOSTA and not sem_max_tokens:
            kwargs["max_completion_tokens"] = MAX_TOKENS_RESPOSTA
        if usar_ferramentas:
            # 🌟 24/10/2026: na 1ª rodada a IA é OBRIGADA a consultar a planilha (antes respondia de memória, com números
            # inventados ou "não há dados"); depois decide sozinha
            kwargs.update(tools=FERRAMENTAS, tool_choice=("required" if rodada == 0 and not sem_required else "auto"))
        else:
            # chegou ao máximo de consultas: pede a resposta com o que já tem
            mensagens.append({"role": "user", "content": "Responda agora com os resultados das consultas já feitas (não há mais consultas disponíveis)."})
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as erro:
            texto_erro = str(erro)
            print(f"[agente_ia][planilhas] erro da IA ({type(erro).__name__}, rodada {rodada}, nível {nivel}, "
                  f"{sum(len(str(m.get('content') or '')) for m in envio)} caracteres): {texto_erro[:800]}")
            if 'max_completion_tokens' in texto_erro.lower() and not sem_max_tokens:
                sem_max_tokens = True      # provedor não aceita o parâmetro: tenta sem ele
                continue
            if rodada == 0 and not sem_required and 'tool_choice' in texto_erro.lower():
                sem_required = True        # provedor não aceita "required": segue com "auto"
                continue
            if _pedido_grande_demais(erro) and nivel < 3:
                nivel += 1                 # esperar não adianta: diminui o pedido e tenta de novo
                continue
            if _erro_de_ferramenta(erro) and falhas_ferramenta < 2:
                falhas_ferramenta += 1     # a IA montou mal a chamada da ferramenta: tenta de novo
                continue
            if _erro_de_limite(erro) and esperado < MAX_ESPERA_LIMITE_S:
                espera = _espera_pedida(erro)
                print(f"[agente_ia][planilhas] limite de uso da IA -- aguardando {espera:.0f}s")
                time.sleep(espera)
                esperado += espera
                continue
            if _pedido_grande_demais(erro):
                raise ErroPlanilhaIA(
                    "A pergunta precisou de mais dados do que o limite de tamanho do serviço de IA permite (limite de tokens "
                    "por minuto do plano). Tente uma pergunta mais específica (um período ou um insumo), ou divida em partes.")
            if _erro_de_limite(erro):
                raise ErroPlanilhaIA("O serviço de IA está no limite de uso por minuto. Tente de novo em 1 minuto.")
            raise ErroPlanilhaIA("O serviço de IA recusou o pedido (" + type(erro).__name__ + "): " + texto_erro[:300])
        msg = resp.choices[0].message
        chamadas = getattr(msg, 'tool_calls', None) or []
        if not chamadas:
            return (msg.content or '').strip()
        rodada += 1
        mensagens.append({"role": "assistant", "content": msg.content or "",
                          "tool_calls": [{"id": c.id, "type": "function",
                                          "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                         for c in chamadas]})
        for c in chamadas[:6]:
            try:
                args = json.loads(c.function.arguments or "{}")
            except Exception:
                args = {}
            resultado = executar_ferramenta(planilhas, c.function.name, args)
            print(f"[agente_ia][planilhas] {c.function.name} {json.dumps(args, ensure_ascii=False)[:300]} -> {len(resultado)} caracteres")
            mensagens.append({"role": "tool", "tool_call_id": c.id, "content": resultado})
        for c in chamadas[6:]:
            mensagens.append({"role": "tool", "tool_call_id": c.id, "content": "Não executada: no máximo 6 consultas por vez."})
