from celery import shared_task
from django.db import connection


@shared_task
def update_custo_variavel_adicionado(id_custo_item_preco):
    cursor = connection.cursor()
    sql = "call public.update_valor_custo_variavel_adicionado(" + str(id_custo_item_preco) + ")"
    cursor.execute(sql)
    cursor.close()