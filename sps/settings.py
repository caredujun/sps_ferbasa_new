import os
from pathlib import Path
import dj_database_url
import django_heroku

ADMIN_TWO_FACTOR_NAME = 'SPS Ferbasa'

BASE_DIR = Path(__file__).resolve().parent.parent

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

'''

# INÍCIO DO RODAR HEROKU
# Configurações para o celery
CELERY_BROKER_URL = os.environ.get('REDIS_URL')

# Configurações para o celery results
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_RESULT_EXTENDED = True

X_FRAME_OPTIONS = "SAMEORIGIN"

SILENCED_SYSTEM_CHECKS = ["security.W019"]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Para forçar o uso do https ao invés de http
    'django.middleware.security.SecurityMiddleware',
]

# Para forçar o uso do https ao invés de http
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# FIM DO RODAR HEROKU

'''

# INÍCIO DO RODAR LOCAL
# Configuraçẽs para o celery
CELERY_BROKER_URL = 'redis://localhost:6379'

# Configuraçẽs para o celery results
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_RESULT_EXTENDED = True

X_FRAME_OPTIONS = "SAMEORIGIN"

SILENCED_SYSTEM_CHECKS = ["security.W019"]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'parameters.middleware.DefinirUsuarioAtualMiddleware',  # 🌟 novo, depois do Authentication
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Para forçar o uso do https ao invés de http
    #'django.middleware.security.SecurityMiddleware',
]

#Desenvolvimento
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
# FIM DO RODAR LOCAL

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
        'NAME': os.environ.get('DB_NAME', 'spsferbasa_dev'),
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
EMAIL_HOST_USER = 'sps.consultoria.alerta@gmail.com'
EMAIL_HOST_PASSWORD = 'acmd ydnn zytj zbrv'  # ATENÇÃO: SENHA GERADA PELO GOOGLE MAIL PARA CADA UMA DAS APP. ENTRA em sps.consultoria.alerta.@gmail.com / Gerenciar Sua Conta Google (logo abaixo Olá SPS Consultoria.
                                             # Na lupa de pesquisa digitar senha app
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False


USE_DJANGO_JQUERY = True
JQUERY_URL = False