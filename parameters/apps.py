import os
import sys
import time
import subprocess

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ParametersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'parameters'
    verbose_name = _("       PARÂMETROS")  # 7 espaços

    def ready(self):
        # 🌟 NOVO: em desenvolvimento local, (re)inicia o worker do Celery
        # automaticamente toda vez que o runserver sobe/recarrega --
        # resolve o "esqueci de deixar o `python -m celery -A sps worker
        # -l info` aberto", sem precisar trocar o Celery pro modo
        # síncrono (que travava a tela até a tarefa terminar).

        # Só faz sentido ao rodar o servidor -- não em migrate/shell/
        # makemigrations/etc, que não atendem requisição nenhuma.
        if 'runserver' not in sys.argv:
            return

        # O autoreloader do "runserver" roda esse ready() duas vezes: uma
        # no processo "vigia" inicial, outra no processo de verdade (que
        # tem RUN_MAIN=true). Sem essa checagem, mexeríamos no worker 2x
        # a cada reinício. Com --noreload não existe esse processo
        # duplicado, então segue direto.
        if '--noreload' not in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return

        from django.conf import settings
        if getattr(settings, 'RODANDO_NO_HEROKU', False):
            return  # produção usa o worker de verdade, gerenciado separadamente (Procfile)

        self._reiniciar_celery()

    def _reiniciar_celery(self):
        from django.conf import settings

        # 🌟 NOVO: log dedicado do worker, na raiz do projeto -- resolve
        # dois problemas que apareceram com o worker sendo filho direto do
        # runserver: (1) a saída dele ficava misturada com a do Django no
        # mesmo terminal, difícil de acompanhar; (2) a saída "sumia" depois
        # de reinícios -- agora fica tudo aqui, persistente, dá pra
        # acompanhar ao vivo com "tail -f" num terminal separado, e o
        # histórico continua disponível mesmo depois de vários reinícios.
        caminho_log = os.path.join(str(settings.BASE_DIR), 'celery_worker.log')

        # 🌟 CORRIGIDO: o problema real não era "já tem um worker rodando,
        # não duplica" -- era o OPOSTO. O Celery carrega o código das
        # tarefas uma única vez, quando o worker liga, e NÃO recarrega
        # sozinho quando um .py muda (diferente do runserver, que tem
        # esse recarregamento automático embutido). Como a checagem
        # antiga só via "já tem um rodando?" e não fazia nada nesse caso,
        # o worker ficava preso pra sempre no código de quando foi ligado
        # pela primeira vez -- mesmo depois de editar tasks.py e reiniciar
        # o runserver várias vezes. Agora, MATA o worker antigo (se
        # existir) e sobe um novo, toda vez que o runserver (re)inicia --
        # garantindo que o worker sempre reflita o código atual em disco.
        try:
            # 🌟 "[c]elery" (com colchetes) -- truque clássico do pgrep/ps
            # pra evitar que ele "se autodetecte": sem os colchetes, o
            # PRÓPRIO comando de busca (que contém o texto "celery -A sps
            # worker" no argumento) também bateria como resultado.
            subprocess.run(['pkill', '-f', '[c]elery -A sps worker'], timeout=3)

            # Espera o processo antigo terminar de verdade antes de subir
            # o novo (até 5s) -- evita, por um instante, ter dois workers
            # pegando tarefa da mesma fila ao mesmo tempo.
            for _tentativa in range(10):
                resultado = subprocess.run(
                    ['pgrep', '-f', '[c]elery -A sps worker'],
                    capture_output=True, timeout=3,
                )
                if resultado.returncode != 0:  # não achou mais nenhum -- já encerrou
                    break
                time.sleep(0.5)
        except Exception as erro_checagem:
            print(f"[celery] não consegui checar/encerrar um worker antigo ({erro_checagem}); vou tentar subir um novo mesmo assim.")

        try:
            arquivo_log = open(caminho_log, 'a')
            subprocess.Popen(
                [sys.executable, '-m', 'celery', '-A', 'sps', 'worker', '-l', 'info'],
                cwd=str(settings.BASE_DIR),
                stdout=arquivo_log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # 🌟 Desvincula o worker da sessão/grupo de processos do
                # runserver -- sem isso, um encerramento abrupto do
                # runserver (ex: Ctrl+C) podia deixar o worker num estado
                # instável. Com sessão própria, ele só é derrubado quando
                # ESTE código explicitamente manda (pkill acima).
                start_new_session=True,
            )
            print("[celery] worker (re)iniciado com o código atual.")
            print(f"[celery] acompanhe ao vivo com: tail -f {caminho_log}")
        except Exception as erro_inicio:
            print(f"[celery] não consegui (re)iniciar automaticamente ({erro_inicio}) -- inicie manualmente com: python -m celery -A sps worker -l info")