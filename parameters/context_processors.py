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
        texto = f"Cenário Ativo: {cenario.id}/{cenario.cen_nome}"
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