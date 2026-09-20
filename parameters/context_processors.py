from .models import TbCenarios
from .contexto_usuario import CenarioAtivoNaoDefinidoError


def cenario_ativo_usuario(request):
    """
    Injeta, em TODA página do Admin, o texto do cenário ativo calculado em
    tempo real PARA O USUÁRIO da requisição -- substitui o antigo mecanismo
    de theme.title (admin_interface_theme.title), que era gravado uma
    única vez no banco a cada save() de TbCenarios e por isso ficava igual
    pra todo mundo até o próximo save.

    TbCenarios.objects.get(cen_ativo=True) já passa pelo TbCenariosManager
    (parameters/models.py), que resolve pelo PerfilUsuario do usuário
    logado -- aqui só formatamos o texto pra exibição.
    """
    if not request.user.is_authenticated:
        return {}

    try:
        cenario = TbCenarios.objects.get(cen_ativo=True)
        # 🌟 CORRIGIDO: usa numero_sequencial (número da empresa) em vez
        # do id real da tabela -- mesma lógica de fallback do __str__ do
        # model, pra não quebrar cenários antigos sem numero_sequencial.
        numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
        texto = f"Cenário {numero_exibido}/{cenario.cen_nome}"
    except TbCenarios.DoesNotExist:
        texto = "Nenhum cenário ativo definido"
    except TbCenarios.MultipleObjectsReturned:
        # Não deveria mais acontecer depois da correção no save() (uso de
        # objects_real na lógica de invariante), mas fica como rede de
        # segurança visível em vez de quebrar a página inteira com uma
        # exception de servidor.
        texto = "⚠️ Múltiplos cenários marcados como ativos -- contate o administrador"
    except CenarioAtivoNaoDefinidoError:
        # 🌟 NOVO: o usuário está logado mas ainda não escolheu um cenário
        # ativo no perfil. Isso é tratado de verdade (redirect com aviso)
        # pelo DefinirUsuarioAtualMiddleware, em qualquer tela que
        # DEPENDA de fato do cenário ativo -- aqui é só o cabeçalho
        # informativo, que roda em TODA página (inclusive a própria tela
        # de Cenários, pra onde o middleware redireciona). Se isso também
        # levantasse a exceção, o redirect cairia numa página que levanta
        # a MESMA exceção de novo -- loop infinito de redirect. Por isso
        # aqui só mostra um texto neutro, sem levantar nada.
        texto = "Nenhum cenário ativo escolhido"

    return {'cenario_ativo_usuario_texto': texto}


def empresa_ativa_usuario(request):
    """
    🌟 NOVO (multi-empresa): injeta, em TODA página do Admin, o texto da
    empresa "efetiva" do usuário logado -- pra usuário comum, a empresa
    fixa dele; pra superusuário, a empresa_ativa escolhida (trocável).
    Mesmo espírito do cenario_ativo_usuario acima: elimina qualquer
    dúvida sobre em qual empresa você está trabalhando agora, sem
    precisar abrir a tela de Empresas pra conferir.

    🌟 NOVO: também injeta a URL do logo da empresa (campo emp_logo em
    TbEmpresa), pra aparecer ao lado do nome no cabeçalho -- None se a
    empresa não tiver logo cadastrado, ou se não houver empresa
    definida (o template trata isso simplesmente não mostrando a tag
    <img>).
    """
    if not request.user.is_authenticated:
        return {}

    perfil = getattr(request.user, 'perfilusuario', None)
    if perfil is None:
        return {'empresa_ativa_usuario_texto': 'Nenhuma empresa definida', 'empresa_ativa_usuario_logo_url': None}

    empresa_id = perfil.empresa_efetiva_id()
    logo_url = None
    if empresa_id is None:
        if request.user.is_superuser:
            texto = 'Nenhuma empresa ativa escolhida'
        else:
            texto = 'Nenhuma empresa definida para o seu usuário'
    else:
        from .models import TbEmpresa
        try:
            empresa = TbEmpresa.objects.get(id=empresa_id)
            texto = empresa.emp_nome
            if empresa.emp_logo:
                logo_url = empresa.emp_logo.url
        except TbEmpresa.DoesNotExist:
            texto = 'Empresa definida não existe mais'

    return {'empresa_ativa_usuario_texto': texto, 'empresa_ativa_usuario_logo_url': logo_url}


def pode_acessar_agente_ia(request):
    """
    🌟 NOVO (multi-empresa): injeta, em toda página do Admin, se o
    usuário logado pode acessar o Agente IA -- usado pelo botão do
    cabeçalho (templates/admin/sps/base_site.html), que antes checava só
    "é superusuário ou está no grupo Agente de IA" direto no template
    (sem saber do superuser de empresa, que também deve ter acesso
    automático).
    """
    if not request.user.is_authenticated:
        return {}

    from .contexto_usuario import eh_superuser_ou_superuser_empresa
    NOME_GRUPO_AGENTE_IA = "Agente de IA"

    tem_acesso = (
        eh_superuser_ou_superuser_empresa(request.user)
        or request.user.groups.filter(name=NOME_GRUPO_AGENTE_IA).exists()
    )
    return {'pode_acessar_agente_ia': tem_acesso}