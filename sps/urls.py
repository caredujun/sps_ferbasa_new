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
    path('admin'          , admin.site.urls),
    path('chat/', views.chat_view, name='chat'),
    # NOVA ROTA: Vincula o botão do front-end à view de limpeza do Python
    path('chat/limpar/', views.limpar_historico_view, name='limpar_historico'),
    path('chat/upload/', views.upload_pdf_view, name='upload_pdf'),
    path('fluxo_producao/', include('fluxos.urls')),
    path('two_factor/'    , include(('admin_two_factor.urls', 'admin_two_factor'), namespace='two_factor')),
    path(''               , admin.site.urls),
   ]

