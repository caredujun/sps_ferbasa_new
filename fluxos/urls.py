from django.urls import path
from . import views
from .diagnostico_explosao import diagnostico_explosao_view  # 🌟 NOVO (30/10/2026)

urlpatterns = [
    # Views para o editor
    path('editor/', views.editor_view, name='editor'),
    path('editor/<int:fluxo_id>/', views.editor_view, name='editor_com_fluxo'),

    # APIs para equipamentos
    path('api/equipamentos/', views.api_equipamentos, name='api_equipamentos'),
    path('equipamento_imagem/<int:equipamento_id>/', views.equipamento_imagem_view, name='equipamento_imagem'),

    # APIs para fluxos
    path('api/fluxo/<int:fluxo_id>/', views.api_fluxo, name='api_fluxo'),
    path('api/fluxo/<int:fluxo_id>/inspecao/', views.api_fluxo_inspecao, name='api_fluxo_inspecao'),
    path('api/fluxo/<int:fluxo_id>/custo_variavel_distribuicao/', views.api_fluxo_custo_variavel_distribuicao, name='api_fluxo_custo_variavel_distribuicao'),
    path('api/fluxo/<int:fluxo_id>/custo_por_equipamento/', views.api_fluxo_custo_por_equipamento, name='api_fluxo_custo_por_equipamento'),
    path('api/fluxo/<int:fluxo_id>/indicadores_ligacoes/', views.api_fluxo_indicadores_ligacoes, name='api_fluxo_indicadores_ligacoes'),
    # 🌟 NOVO (27/10/2026): círculo "C" dos quadrados do editor (custo do equipamento/ordem no fluxo)
    path('api/fluxo/<int:fluxo_id>/custo_equipamento/<int:equipamento_id>/', views.api_fluxo_custo_equipamento, name='api_fluxo_custo_equipamento'),
    path('api/fluxo/<int:fluxo_id>/itens_custo_equipamento/', views.api_fluxo_itens_custo_equipamento, name='api_fluxo_itens_custo_equipamento'),
    # 🌟 NOVO (28/10/2026): itens do Custo Var. Adic. das ordens de um equipamento (gráfico Custo Por Equipamento)
    path('api/fluxo/<int:fluxo_id>/custo_codigo_equipamento/', views.api_fluxo_custo_codigo_equipamento, name='api_fluxo_custo_codigo_equipamento'),
    path('api/fluxo/salvar/', views.api_fluxo_salvar, name='api_fluxo_salvar'),
    # 🌟 NOVO (30/10/2026): mapa da explosão de fluxos de um produto (só leitura, nada é gravado)
    path('diagnostico_explosao/<int:produto_id>/', diagnostico_explosao_view, name='diagnostico_explosao'),

]