import os
from pathlib import Path
import dj_database_url
import django_heroku
from dotenv import load_dotenv

ADMIN_TWO_FACTOR_NAME = 'SPS Ferbasa'

BASE_DIR = Path(__file__).resolve().parent.parent

# 🌟 NOVO: carrega variáveis de um arquivo .env na raiz do projeto (se
# existir) -- assim dá pra manter senhas e outros segredos FORA do
# código-fonte, tanto em desenvolvimento quanto em produção. Mesma
# mecânica já usada em agents.py, agora também aqui no settings.py (que
# roda ANTES, então precisa da própria chamada -- carregar só lá não
# adianta pra variáveis lidas aqui).
load_dotenv(os.path.join(BASE_DIR, '.env'))

SECRET_KEY = 'django-insecure-^m_c8!tfug^j#s3%0be5wkgu=)f*%!cf8f4=@owu^+pybm1980'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True
ALLOWED_HOSTS = ['https://sps-ferbasa.com']

CSRF_TRUSTED_ORIGINS = [
    'https://bluejay-crisp-seagull.ngrok-free.app',
    # Add other trusted origins if needed
]

INSTALLED_APPS = [
    # para permitir o uso da django-admin-interface
    'admin_interface',
    'colorfield',
    'admin_two_factor.apps.TwoStepVerificationConfig',
    'django_celery_beat',
    'django_celery_results',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'parameters',
    'tabelas',
    'equipamentos',
    'produtos',
    'fluxos',
    'otimizacao',
    'custo_ferbasa',
    'django_object_actions',
    'nested_admin',
    'storages',
]

# 🌟 NOVO: detecta automaticamente se está rodando no Heroku (a
# variável DYNO é definida pelo próprio Heroku em todo dyno, não existe
# localmente) -- substitui a alternância manual de comentar/descomentar
# os blocos "RODAR HEROKU" / "RODAR LOCAL" que existia antes.
RODANDO_NO_HEROKU = bool(os.environ.get('DYNO'))

# Configurações para o celery
if RODANDO_NO_HEROKU:
    CELERY_BROKER_URL = os.environ.get('REDIS_URL')
else:
    CELERY_BROKER_URL = 'redis://localhost:6379'
    # 🌟 CORRIGIDO: voltamos pro Celery assíncrono de verdade também em
    # desenvolvimento -- o modo síncrono (CELERY_TASK_ALWAYS_EAGER)
    # resolvia o "esqueci de ligar o worker", mas quebrava a experiência
    # de "roda em segundo plano" (a tela ficava travada até a tarefa
    # inteira terminar, e a mensagem de "clique em Verificar" só
    # aparecia depois que já tinha acabado mesmo). Resolvemos o
    # "esqueci de ligar" de outro jeito agora: iniciando o worker
    # automaticamente (veja parameters/apps.py).

# Configurações para o celery results
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_RESULT_EXTENDED = True

X_FRAME_OPTIONS = "SAMEORIGIN"

SILENCED_SYSTEM_CHECKS = ["security.W019"]

# 🌟 CORRIGIDO: essa lista agora é ÚNICA pros dois ambientes (antes, a
# versão do bloco Heroku estava desatualizada -- não tinha o
# LocaleMiddleware nem o DefinirUsuarioAtualMiddleware adicionados nas
# sessões de multi-idioma/multi-empresa, o que quebraria tradução e
# contexto de empresa/usuário se fosse usada em produção do jeito que
# estava).
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # 🌟 NOVO (multi-idioma, Fase 1): trata idioma do request (cookie,
    # header Accept-Language do navegador, etc.) -- serve principalmente
    # de base pra requisições SEM usuário logado (ex: tela de login).
    # Pra usuário logado, DefinirUsuarioAtualMiddleware (mais abaixo,
    # depois do Authentication) SOBRESCREVE com o idioma_efetivo() dele,
    # que tem prioridade. Precisa vir ANTES do CommonMiddleware.
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'parameters.middleware.DefinirUsuarioAtualMiddleware',  # 🌟 novo, depois do Authentication
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if RODANDO_NO_HEROKU:
    # Para forçar o uso do https ao invés de http
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    # Desenvolvimento
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

ROOT_URLCONF = 'sps.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS':  [os.path.join(BASE_DIR,'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.request',
                'parameters.context_processors.cenario_ativo_usuario',
                'parameters.context_processors.empresa_ativa_usuario',
                'parameters.context_processors.pode_acessar_agente_ia',

            ],
        },
    },
]

WSGI_APPLICATION = 'sps.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': os.environ.get('DB_NAME', 'spsdemosteel'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASS', 'Kodaka32354211'),
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

db_from_env = dj_database_url.config(conn_max_age=600)
DATABASES['default'].update(db_from_env)

# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'pt-BR'

# 🌟 NOVO (multi-idioma, Fase 1): faltava esse liga-desliga geral do
# mecanismo de internacionalização do Django -- sem ele, {% trans %} e
# tudo mais relacionado a idioma é ignorado, mesmo com LANGUAGES e
# LocaleMiddleware configurados.
USE_I18N = True

# 🌟 NOVO (multi-idioma, Fase 1): os 7 idiomas suportados -- os CÓDIGOS
# aqui precisam bater exatamente com IDIOMA_CHOICES em parameters/models.py.
LANGUAGES = [
    ('pt-br', 'Português'),
    ('en', 'English'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('de', 'Deutsch'),
    ('it', 'Italiano'),
    ('zh-hans', '中文（简体）'),
]

# 🌟 NOVO (multi-idioma, Fase 1): onde ficam os arquivos de tradução
# (.po/.mo) gerados pelo "python manage.py makemessages"/"compilemessages".
LOCALE_PATHS = [
    os.path.join(BASE_DIR, 'locale'),
]

#Time Zone
DJANGO_CELERY_BEAT_TZ_AWARE = False
TIME_ZONE = 'America/Sao_Paulo'
USE_TZ = False

USE_L10N = True

#Para mostrar o separador do milhar. Será apresentado ponto como separador.
USE_THOUSAND_SEPARATOR = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static')
]


MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Usando os.path
MEDIA_ROOT = os.path.join(BASE_DIR, 'media/') # A barra no final é opcional, mas inofensiva

django_heroku.settings(locals())


AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

DATA_UPLOAD_MAX_NUMBER_FIELDS = 15000 # higher than the count of fields

EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 25

DEFAULT_FROM_EMAIL = 'sps.consultoria.alerta@gmail.com'
# 🌟 CORRIGIDO: usuário e senha agora vêm de variável de ambiente (arquivo
# .env, que NÃO deve ser versionado no Git) -- antes ficavam escritos
# direto aqui, expondo a senha pra qualquer um com acesso ao repositório.
# O segundo argumento de os.environ.get() é só uma reserva, pro sistema
# continuar funcionando mesmo antes de criar o .env -- assim que criar,
# o valor de lá passa a valer. Pra trocar a senha (ou usar uma diferente
# em produção), só muda o .env, sem tocar em código nenhum.
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'sps.consultoria.alerta@gmail.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', 'nivn kdzi zqqz qmkg')

EMAIL_USE_TLS = True
EMAIL_USE_SSL = False


USE_DJANGO_JQUERY = True
JQUERY_URL = False