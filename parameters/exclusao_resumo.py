"""
🌟 NOVO (30/10/2026): tela de confirmação de exclusão do Admin SÓ COM O RESUMO (quantidade por tabela) e os botões
Sim / Não -- sem a lista de todos os objetos que serão excluídos.

O Django, por padrão, monta a lista de TODOS os registros que serão excluídos em cascata, com o nome e o link de cada
um -- com milhares de registros isso é lento e não ajuda. Aqui o Admin passa a calcular só:
  - a quantidade de registros por tabela (o "Resumo");
  - os registros protegidos (PROTECT / RESTRICT), que impedem a exclusão (até 50, para mostrar);
  - as tabelas que o usuário não tem permissão de excluir (como no Django).
Instalado uma vez, para todas as tabelas do Admin, por parameters.signals.conectar_sinais (chamado no ready() do app).
Os templates templates/admin/delete_confirmation.html e delete_selected_confirmation.html mostram só o resumo.
"""
from collections import Counter

MAX_PROTEGIDOS = 50


def _resumo(self, objs, request):
    from django.db import router
    from django.db.models.deletion import Collector, ProtectedError, RestrictedError
    objs = list(objs)
    if not objs:
        return [], {}, set(), []
    collector = Collector(using=router.db_for_write(objs[0]._meta.model), origin=objs)
    try:
        collector.collect(objs)
    except (ProtectedError, RestrictedError) as erro:
        bloqueio = getattr(erro, 'protected_objects', None) or getattr(erro, 'restricted_objects', None) or []
        protegidos = [f"{o._meta.verbose_name}: {o}" for o in list(bloqueio)[:MAX_PROTEGIDOS]]
        return [], {}, set(), protegidos or [str(erro)]
    contagem, exemplo = Counter(), {}
    for model, instancias in collector.data.items():
        if instancias:
            contagem[model] += len(instancias)
            exemplo.setdefault(model, next(iter(instancias)))
    for qs in collector.fast_deletes:            # excluídos direto no banco (sem carregar): conta com count()
        n = qs.count()
        if n:
            contagem[qs.model] += n
    perms_needed = set()
    for model, inst in exemplo.items():
        admin_modelo = self.admin_site._registry.get(model)
        if admin_modelo is not None and not admin_modelo.has_delete_permission(request, inst):
            perms_needed.add(model._meta.verbose_name)
    model_count = {model._meta.verbose_name_plural: n for model, n in contagem.items()}
    return [], model_count, perms_needed, []


def instalar():
    from django.contrib import admin
    if getattr(admin.ModelAdmin.get_deleted_objects, '_so_resumo', False):
        return
    _resumo._so_resumo = True
    admin.ModelAdmin.get_deleted_objects = _resumo
