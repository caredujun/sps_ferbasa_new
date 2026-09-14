from django.contrib import admin
from django.urls import path, include
from django.conf import settings  # Importe settings
from django.conf.urls.static import static  # Importe static
from parameters import views

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
    path(''               , admin.site.urls),
   ]