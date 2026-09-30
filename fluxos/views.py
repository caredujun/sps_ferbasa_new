from django.db import connection
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods
import json
import re
from urllib.parse import urlparse
from equipamentos.models import TbEquipamentos
from parameters.models import TbCenarios
from .models import TbFluxoProducao
# 🌟 NOVO: só o IMPORT já basta pra registrar o sinal que mantém
# flu_pro_dados_fluxo sincronizado com a tabela filha (ver o arquivo
# pra detalhes) -- precisa estar aqui porque views.py é sempre
# carregado na subida do Django (via urls.py). O nome do módulo não é
# usado direto neste arquivo (só a função salvar_fluxo_a_partir_do_json,
# importada localmente dentro de api_fluxo_salvar), então o editor pode
# marcar esse import como "não utilizado" -- é intencional.
from . import sincronizacao_fluxo_visual  # noqa: F401
import datetime


def _empresa_efetiva_id_ou_403(usuario):
    """Retorna a empresa efetiva do usuário ou bloqueia a requisição."""
    perfil = getattr(usuario, 'perfilusuario', None)
    empresa_id = perfil.empresa_efetiva_id() if perfil else None
    if empresa_id is None:
        raise PermissionDenied("Selecione uma empresa antes de acessar o editor de fluxo.")
    return empresa_id


def _fluxo_acessivel_ou_404(usuario, fluxo_id):
    """Busca um fluxo somente dentro da empresa efetiva do usuário."""
    empresa_id = _empresa_efetiva_id_ou_403(usuario)
    return get_object_or_404(
        TbFluxoProducao.objects.select_related('tbcenarios', 'tbcenarios__empresa'),
        id=fluxo_id,
        tbcenarios__empresa_id=empresa_id,
    )


def _normalizar_fluxo_id(valor):
    """Aceita um PK comum ou formatado pela localização pt-BR (ex.: 1.234)."""
    texto = str(valor or '').strip()
    if texto.isdigit():
        return int(texto)
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', texto):
        return int(texto.replace('.', ''))
    return None


def _fluxo_id_da_requisicao(request):
    """Obtém o fluxo pelo parâmetro ou, para HTML antigo em cache, pelo Referer."""
    fluxo_id = _normalizar_fluxo_id(request.GET.get('fluxo_id'))
    if fluxo_id is not None:
        return fluxo_id

    caminho_origem = urlparse(request.META.get('HTTP_REFERER', '')).path
    correspondencia = re.search(r'/fluxo_producao/editor/(\d+)/?$', caminho_origem)
    return int(correspondencia.group(1)) if correspondencia else None


@login_required
@ensure_csrf_cookie
def editor_view(request, fluxo_id=None):
    """
    View para o editor de fluxo de produção.
    """
    _empresa_efetiva_id_ou_403(request.user)
    context = {}
    if fluxo_id:
        fluxo = _fluxo_acessivel_ou_404(request.user, fluxo_id)
        context['fluxo'] = fluxo
        context['fluxo_id'] = fluxo_id

        # 🌟 NOVO: banner com empresa/cenário/logos, mesmo padrão já usado
        # no topo do chat do Agente IA (parameters/views.py) -- usa o
        # cenário e a empresa DO PRÓPRIO FLUXO sendo editado, não o
        # "ativo" do usuário logado no momento (um fluxo pode pertencer a
        # um cenário diferente do que está ativo pra quem o abre).
        cenario_obj = TbCenarios.objects_real.filter(id=fluxo.tbcenarios_id).select_related('empresa').first()
        if cenario_obj:
            numero_exibido = cenario_obj.numero_sequencial if cenario_obj.numero_sequencial is not None else cenario_obj.id
            context['nome_cenario'] = f"{numero_exibido}/{cenario_obj.cen_nome}"
            # 🌟 NOVO: status do cenário no banner, igual ao chat do Agente
            # IA -- mesmo dicionário de mensagens por flag (cross-app,
            # definido em parameters/fluxo_criar_cenario.py).
            from parameters.fluxo_criar_cenario import MENSAGENS_FLAG
            if cenario_obj.flag is None:
                context['status_cenario'] = "Ainda não processado"
            else:
                context['status_cenario'] = MENSAGENS_FLAG.get(cenario_obj.flag, f"Desconhecido (flag={cenario_obj.flag})")
            empresa_obj = cenario_obj.empresa
            if empresa_obj:
                context['nome_empresa'] = empresa_obj.emp_nome
                if empresa_obj.emp_logo:
                    context['logo_empresa_url'] = empresa_obj.emp_logo.url

        from admin_interface.models import Theme
        tema_ativo = Theme.objects.filter(active=True).first()
        if tema_ativo:
            if tema_ativo.logo:
                context['logo_tema_url'] = tema_ativo.logo.url
            context['cor_header_tema'] = tema_ativo.css_header_background_color

    return render(request, 'fluxo_producao/editor.html', context)

@login_required
@require_GET
def api_equipamentos(request):
    """
    API para obter a lista de equipamentos disponíveis a partir da tabela TbEquipamentosCadastro
    """
    try:
        empresa_id = _empresa_efetiva_id_ou_403(request.user)
        fluxo_id = _fluxo_id_da_requisicao(request)

        if fluxo_id is not None:
            fluxo = _fluxo_acessivel_ou_404(request.user, fluxo_id)
            cenario_id = fluxo.tbcenarios_id
        else:
            perfil = getattr(request.user, 'perfilusuario', None)
            cenario_id = perfil.cenario_ativo_id if perfil else None
            if cenario_id is None:
                return JsonResponse({'error': 'Nenhum cenário ativo foi selecionado.'}, status=400)
            if not TbCenarios.objects_real.filter(id=cenario_id, empresa_id=empresa_id).exists():
                raise PermissionDenied("O cenário ativo não pertence à empresa selecionada.")

        equipamentos = TbEquipamentos.objects.filter(
            tbcenarios_id=cenario_id,
        ).select_related('equ_codigo')
        #print(f"API equipamentos: Encontrados {equipamentos.count()} equipamentos ativos")

        data = []
        for equip in equipamentos:
            #print(TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem.url)
            # Vamos verificar se ordem está rodando em pelo menos 01 dos períodos
            cursor = connection.cursor()
            # Expressão SQL
            sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(
                equip.id) + " and dau_valor_3 = true"
            cursor.execute(sql)
            retorno_ativa = cursor.fetchone()[0]
            if retorno_ativa > 0:
                item = {
                    'id':              equip.id,
                    'nome':            equip.equ_codigo.equ_cad_codigo + '/' + str(equip.equ_ordem_codigo),
                    'codigo':          equip.equ_codigo.equ_cad_codigo + '/' + str(equip.equ_ordem_codigo),
                    'descricao':       equip.equ_ordem_descricao or '',
                    'codigo_cadastro': equip.equ_codigo.equ_cad_codigo,

                    #'categoria': '',
                    #'imagem': TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem.url if TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem else ''
                }
                data.append(item)

        #print(f"API equipamentos: Retornando {len(data)} itens")
        #print(f"Passei")
        return JsonResponse({'equipamentos': data})
    except (PermissionDenied, Http404):
        raise
    except Exception as e:
        print(f"ERRO na API equipamentos: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@login_required
@require_GET
def equipamento_imagem_view(request, equipamento_id):
    """
    Serve a imagem somente quando o equipamento pertence à empresa efetiva
    do usuário autenticado.
    """
    empresa_id = _empresa_efetiva_id_ou_403(request.user)
    equipamento = get_object_or_404(
        TbEquipamentos.objects.select_related('equ_codigo'),
        id=equipamento_id,
        tbcenarios__empresa_id=empresa_id,
    )
    cadastro = equipamento.equ_codigo
    if cadastro.equ_cad_imagem:
        return redirect(cadastro.equ_cad_imagem.url)
    else:
        return redirect('/static/fluxo_producao/img/equipamento-placeholder.png')


@login_required
@require_GET
def api_fluxo(request, fluxo_id):

    """
    API para obter detalhes de um fluxo específico.
    """
    fluxo = _fluxo_acessivel_ou_404(request.user, fluxo_id)

    # 🌟 CORRIGIDO: usava TbCenarios.objects.get(cen_ativo=True) -- o
    # cenário ATIVO do usuário logado, que não é necessariamente o
    # mesmo cenário a que ESTE fluxo pertence (ex: fluxo copiado pra
    # outro cenário, ou usuário com um cenário ativo diferente do que
    # ele estava editando). Usa o cenário do PRÓPRIO fluxo -- mais
    # preciso e consistente com o resto do sistema.
    cenario_obj = TbCenarios.objects_real.filter(id=fluxo.tbcenarios_id).first()
    if cenario_obj:
        numero_exibido = cenario_obj.numero_sequencial if cenario_obj.numero_sequencial is not None else cenario_obj.id
        # 🌟 CORRIGIDO: mostrava "Cenário: 31 - ID: xxxx" -- confuso e
        # redundante com o banner novo (que já mostra empresa + este
        # mesmo texto). Agora só "Cenário <número>/<nome>", igual ao banner.
        nome_exibido = f"Cenário {numero_exibido}/{cenario_obj.cen_nome}"
    else:
        nome_exibido = f"Fluxo {fluxo.id}"

    data = {
        'id': fluxo.id,
        'nome': nome_exibido,
        'descricao': fluxo.flu_pro_descricao,
        'dados_fluxo': fluxo.flu_pro_dados_fluxo,
        'data_criacao': fluxo.flu_pro_data_criacao.isoformat(),
        'data_modificacao': fluxo.flu_pro_data_modificacao.isoformat(),
        'ativo': fluxo.flu_pro_ativo
    }

    return JsonResponse(data)


@login_required
@require_http_methods(["GET", "POST"])
def api_fluxo_inspecao(request, fluxo_id):
    """Inspeciona o grafo salvo ou a versão ainda aberta no navegador, sem gravar."""
    fluxo = _fluxo_acessivel_ou_404(request.user, fluxo_id)
    dados_fluxo = None
    if request.method == 'POST':
        try:
            payload = json.loads(request.body or b'{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'JSON inválido.'}, status=400)
        dados_fluxo = payload.get('dados_fluxo')
        if not isinstance(dados_fluxo, dict):
            return JsonResponse({'error': 'O campo dados_fluxo deve ser um objeto JSON.'}, status=400)

    from .assistente_editor import inspecionar_fluxo
    return JsonResponse(inspecionar_fluxo(fluxo, dados_fluxo=dados_fluxo))


@login_required
@require_POST
def api_fluxo_salvar(request):
    """
    API para salvar um fluxo de produção.

    🌟 CORRIGIDO: esta função tinha dois problemas sérios:
    1. O caminho "criar novo fluxo" usava nomes de campo errados
       (nome=, descricao=, dados_fluxo=, sem o prefixo flu_pro_) --
       nunca funcionava de verdade, e de qualquer forma um
       TbFluxoProducao exige flu_pro_produto (obrigatório), que o
       editor nunca pergunta. Removido: o editor agora só EDITA um
       fluxo que já existe (criado pelo Admin, que trata o produto
       corretamente).
    2. Salvava o JSON do editor "cru", sem verificar consistência no
       servidor. Agora chama salvar_fluxo_a_partir_do_json, que verifica
       equipamento isolado, ciclo e se todos os equipamentos pertencem
       ao cenário do fluxo antes de atualizar flu_pro_dados_fluxo.
    """
    try:
        data = json.loads(request.body)
        fluxo_id_bruto = data.get('fluxo_id')
        if not fluxo_id_bruto:
            return JsonResponse({
                'status': 'error',
                'message': 'Este editor só funciona para um fluxo já existente -- crie o fluxo de '
                           'produção pelo Admin primeiro (lá o produto é escolhido corretamente), '
                           'depois abra "Visualizar / Editar Fluxo" para montá-lo aqui.',
            }, status=400)
        fluxo_id = _normalizar_fluxo_id(fluxo_id_bruto)
        if fluxo_id is None:
            return JsonResponse({
                'status': 'error',
                'message': 'Identificador de fluxo inválido.',
            }, status=400)
        dados_fluxo = data.get('dados_fluxo', {})
        valores_iniciais_ligacoes = data.get('valores_iniciais_ligacoes', {})

        fluxo = _fluxo_acessivel_ou_404(request.user, fluxo_id)

        from .sincronizacao_fluxo_visual import salvar_fluxo_a_partir_do_json
        ok, problemas = salvar_fluxo_a_partir_do_json(fluxo, dados_fluxo, valores_iniciais_ligacoes)

        if not ok:
            return JsonResponse({
                'status': 'error',
                'message': 'Não salvei -- o fluxo tem problema(s):\n' + '\n'.join(problemas),
            }, status=400)

        return JsonResponse({
            'status': 'success',
            'message': 'Fluxo verificado e salvo com sucesso.',
            'fluxo_id': fluxo.id,
        })

    except (PermissionDenied, Http404):
        raise
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'JSON inválido.'}, status=400)
    except Exception as e:
        print(f"Erro ao salvar fluxo: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
