from django.contrib import messages
from django.shortcuts import redirect

from .contexto_usuario import definir_usuario_atual, limpar_usuario_atual, CenarioAtivoNaoDefinidoError


class DefinirUsuarioAtualMiddleware:
    """
    Registra o usuário da requisição atual no contexto local à thread
    (parameters/contexto_usuario.py), pra que o TbCenariosManager consiga
    resolver "o cenário ativo" com base em quem está logado, sem precisar
    que toda função no sistema receba o usuário como parâmetro.

    Precisa vir DEPOIS de AuthenticationMiddleware no MIDDLEWARE do
    settings.py (senão request.user ainda não existe nesse ponto).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        definir_usuario_atual(usuario if usuario and usuario.is_authenticated else None)
        try:
            response = self.get_response(request)
        finally:
            # Sempre limpa, mesmo se a view levantar exceção -- evita que o
            # usuário de uma requisição vaze pra próxima na mesma thread
            # (threads de worker são reaproveitadas entre requisições).
            limpar_usuario_atual()
        return response

    def process_exception(self, request, exception):
        """
        🌟 NOVO: hook que o Django chama de verdade quando uma view levanta
        uma exceção não tratada -- diferente de um try/except comum em volta
        de self.get_response, que NÃO funciona aqui (o próprio Django já
        converte a exceção em resposta HTTP numa camada mais interna, antes
        de qualquer try/except em volta do get_response conseguir vê-la).

        Captura CenarioAtivoNaoDefinidoError (levantada pelo TbCenariosManager
        quando o usuário logado ainda não escolheu um cenário ativo) e
        transforma num redirect com mensagem amigável, em vez de deixar virar
        um erro 500 cru.
        """
        if isinstance(exception, CenarioAtivoNaoDefinidoError):
            messages.error(
                request,
                'Você ainda não escolheu um cenário ativo. Acesse a tela de '
                'Cenários e clique em "Ativar" no cenário desejado antes de continuar.'
            )
            return redirect('admin:parameters_tbcenarios_changelist')
        return None