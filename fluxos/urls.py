from django.urls import path
from . import views

urlpatterns = [
    # Views para o editor
    path('editor/', views.editor_view, name='editor'),
    path('editor/<int:fluxo_id>/', views.editor_view, name='editor_com_fluxo'),

    # APIs para equipamentos
    path('api/equipamentos/', views.api_equipamentos, name='api_equipamentos'),
    path('equipamento_imagem/<int:equipamento_id>/', views.equipamento_imagem_view, name='equipamento_imagem'),

    # APIs para fluxos
    path('api/fluxo/<int:fluxo_id>/', views.api_fluxo, name='api_fluxo'),
    path('api/fluxo/salvar/', views.api_fluxo_salvar, name='api_fluxo_salvar'),

]