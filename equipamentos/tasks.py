from celery import shared_task
from django.db import connection


@shared_task
def update_indicador(id_consumo_padrao):
    cursor = connection.cursor()
    sql = "call public.update_indicador_consumo_especifico_equipamento(" + str(id_consumo_padrao) + ")"
    cursor.execute(sql)
    cursor.close()