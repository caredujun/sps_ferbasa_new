"""
Armazena o usuário da requisição atual num contexto local à thread, pra que
o TbCenariosManager (em models.py) consiga resolver "o cenário ativo" sem
precisar que cada função receba o usuário explicitamente como parâmetro.

ESCOPO: isso só funciona para código síncrono dentro do ciclo de uma
requisição HTTP (views, admin, forms) -- é alimentado pelo middleware
DefinirUsuarioAtualMiddleware, registrado em settings.py.

NÃO funciona dentro de Celery tasks: elas rodam em processos de worker
separados, sem acesso a essa thread-local (cada worker tem a sua própria,
vazia). Tasks que precisam saber qual cenário usar devem RECEBER o id
explicitamente como parâmetro (padrão que a maioria das tasks já segue,
ex: otimizar_cenario_celery(self, periodo, cen_ativo, total_variaveis)).
"""

import threading

_contexto = threading.local()


class CenarioAtivoNaoDefinidoError(Exception):
    """
    Levantado pelo TbCenariosManager quando existe um usuário real logado
    no contexto atual, mas ele ainda não escolheu um cenário ativo no
    perfil (PerfilUsuario.cenario_ativo is None).

    Existe para EVITAR que o sistema caia silenciosamente no cenário
    ativo GLOBAL (cen_ativo=True no banco) nesse caso -- o que poderia
    mostrar ou operar sobre dados de um cenário diferente do que a
    pessoa pretende usar, sem nenhum aviso. Preferimos falhar alto e
    claro a devolver um resultado errado sem avisar ninguém.

    Capturada pelo DefinirUsuarioAtualMiddleware, que transforma isso
    numa mensagem amigável (redirect com messages.error) em vez de um
    erro 500 cru.
    """
    pass


def definir_usuario_atual(usuario):
    """Chamado pelo middleware no início de cada requisição."""
    _contexto.usuario = usuario


def get_usuario_atual():
    """
    Retorna o usuário da requisição atual, ou None se não houver nenhum
    definido (ex: chamado de dentro de uma Celery task, de um shell do
    Django, ou de um teste automatizado sem o middleware ativo).
    """
    return getattr(_contexto, 'usuario', None)


def limpar_usuario_atual():
    """Chamado pelo middleware ao final de cada requisição, pra não vazar
    o usuário de uma requisição para a próxima (threads de worker HTTP
    são reaproveitadas entre requisições diferentes)."""
    _contexto.usuario = None