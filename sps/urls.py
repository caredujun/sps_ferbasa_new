from django.contrib import admin
from django.urls import path, include
from django.conf import settings  # Importe settings
from django.conf.urls.static import static  # Importe static
from django.views.generic import RedirectView
from parameters import views
# 🌟 NOVO: gettext_lazy (não gettext "eager") -- essencial aqui porque as
# 3 linhas abaixo rodam só UMA VEZ, na inicialização do servidor, não a
# cada requisição. Com a versão eager, o texto seria traduzido uma única
# vez pro idioma que estivesse ativo naquele instante do startup, e
# ficaria TRAVADO nesse idioma pro resto da vida do processo -- errado
# num sistema multi-idioma onde cada usuário pode ver um idioma
# diferente. gettext_lazy adia a tradução de verdade pra quando o texto
# for efetivamente exibido (aí sim, usando o idioma daquela requisição).
from django.utils.translation import gettext_lazy as _

# 🌟 NOVO: personalização do cabeçalho/título do Django Admin.
# Altera o título da página de login e do topo do painel
admin.site.site_header = _("Administração do Sistema")

# Altera o título da aba do navegador -- "SPS" é a marca/sigla do
# produto, igual em qualquer idioma -- por isso NÃO é envolvida em
# gettext_lazy, propositalmente.
admin.site.site_title = "SPS"

# Altera o texto de boas-vindas da página inicial do admin
admin.site.index_title = _("Administração")

# 1. Comece com uma lista de URLs vazia ou apenas com a regra de mídia
urlpatterns = []

# 2. Adicione as URLs de mídia primeiro, se estiver em modo DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

urlpatterns += [
    # 🌟 CORRIGIDO: faltava a barra final ('admin' -> 'admin/') -- forma
    # não padrão de montar o Admin, que pode gerar comportamento
    # inconsistente na resolução de URLs aninhadas dele (como o
    # get_urls() customizado que tentamos usar antes). Mantido por
    # segurança, mas agora com a barra certa.
    path('admin/'         , admin.site.urls),
    path('chat/', views.chat_view, name='chat'),
    # NOVA ROTA: Vincula o botão do front-end à view de limpeza do Python
    path('chat/limpar/', views.limpar_historico_view, name='limpar_historico'),
    path('chat/upload/', views.upload_pdf_view, name='upload_pdf'),
    path('chat/relatorios/', views.listar_relatorios_json, name='listar_relatorios_json'),
    path('chat/arquivo-atualizacao/', views.upload_arquivo_atualizacao_view, name='upload_arquivo_atualizacao'),
    path('chat/arquivo-atualizacao/status/', views.status_arquivo_atualizacao_json, name='status_arquivo_atualizacao'),
    # 🌟 NOVO (multi-empresa): endpoint JSON usado pelo JS que filtra o
    # dropdown de cenário pela empresa escolhida no formulário de
    # usuário -- URL direta, fora do mecanismo de URLs do Admin.
    path('parameters/cenarios-por-empresa/<int:empresa_id>/',
         views.cenarios_por_empresa_json, name='cenarios_por_empresa_json'),
    # 🌟 NOVO (multi-idioma, Fase 1): troca o idioma pessoal do usuário
    # logado -- acessível a QUALQUER usuário, mesmo sem acesso ao Admin.
    path('trocar-idioma/', views.trocar_idioma_view, name='trocar_idioma'),
    path('fluxo_producao/', include('fluxos.urls')),
    path('two_factor/'    , include(('admin_two_factor.urls', 'admin_two_factor'), namespace='two_factor')),
    # 🌟 CORRIGIDO: antes incluía admin.site.urls uma SEGUNDA vez aqui na
    # raiz (''), pra abrir o Admin direto ao acessar só o domínio -- mas
    # isso registrava o namespace "admin" duas vezes, causando o aviso
    # "URL namespace 'admin' isn't unique" toda vez que o servidor
    # subia. Um REDIRECT simples pra /admin/ tem o mesmo efeito prático
    # (acessar a raiz leva pro Admin) sem duplicar o registro de URLs.
    path(''               , RedirectView.as_view(url='/admin/', permanent=False)),
]