from django.db import connection
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
from equipamentos.models import TbEquipamentosCadastro, TbEquipamentos
from parameters.models import TbCenarios
from .models import TbFluxoProducao
import datetime


def editor_view(request, fluxo_id=None):
    """
    View para o editor de fluxo de produção.
    """
    context = {}
    if fluxo_id:
        fluxo = get_object_or_404(TbFluxoProducao, id=fluxo_id)
        context['fluxo'] = fluxo
        context['fluxo_id'] = fluxo_id
    return render(request, 'fluxo_producao/editor.html', context)

def api_equipamentos(request):
    """
    API para obter a lista de equipamentos disponíveis a partir da tabela TbEquipamentosCadastro
    """
    try:
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        equipamentos = TbEquipamentos.objects.filter(tbcenarios_id = cen_ativo)
        #print(f"API equipamentos: Encontrados {equipamentos.count()} equipamentos ativos")

        data = []
        for equip in equipamentos:
            #print(TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem.url)
            # Vamos verificar se ordem está rodando em pelo menos 01 dos períodos
            cursor = connection.cursor()
            # Expressão SQL
            sql = "select count(*) from equipamentos_tbequipamentosdaugther where mae_id = " + str(
                equip.id) + " and dau_valor_3 = true"
            cursor.execute(sql)
            retorno_ativa = cursor.fetchone()[0]
            if retorno_ativa > 0:
                item = {
                    'id':              equip.id,
                    'nome':            TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_codigo + '/' + str(equip.equ_ordem_codigo),
                    'codigo':          TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_codigo + '/' + str(equip.equ_ordem_codigo),
                    'descricao':       equip.equ_ordem_descricao or '',
                    'codigo_cadastro': TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_codigo ,

                    #'categoria': '',
                    #'imagem': TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem.url if TbEquipamentosCadastro.objects.get(id=equip.equ_codigo_id).equ_cad_imagem else ''
                }
                data.append(item)

        #print(f"API equipamentos: Retornando {len(data)} itens")
        #print(f"Passei")
        return JsonResponse({'equipamentos': data})
    except Exception as e:
        print(f"ERRO na API equipamentos: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

def equipamento_imagem_view(request, equipamento_id):
    #Temos que pegar o id do equipamento na tabela TbEquipamentosCadastro
    equipamento_id = TbEquipamentos.objects.get(id=equipamento_id).equ_codigo_id

    """
    View para servir a imagem do equipamento diretamente.
    """
    equipamento = get_object_or_404(TbEquipamentosCadastro, id=equipamento_id)
    if equipamento.equ_cad_imagem:
        return redirect(equipamento.equ_cad_imagem.url)
    else:
        return redirect('/static/fluxo_producao/img/equipamento-placeholder.png')


def api_fluxo(request, fluxo_id):

    """
    API para obter detalhes de um fluxo específico.
    """
    fluxo = get_object_or_404(TbFluxoProducao, id=fluxo_id)

    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id

    data = {
        'id': fluxo.id,
        'nome': 'Cenário: ' + str(cen_ativo) + ' - ID: ' + str(fluxo.id),
        'descricao': fluxo.flu_pro_descricao,
        'dados_fluxo': fluxo.flu_pro_dados_fluxo,
        'data_criacao': fluxo.flu_pro_data_criacao.isoformat(),
        'data_modificacao': fluxo.flu_pro_data_modificacao.isoformat(),
        'ativo': fluxo.flu_pro_ativo
    }

    return JsonResponse(data)

@csrf_exempt
def api_fluxo_salvar(request):
    """
    API para salvar um fluxo de produção.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fluxo_id = int(data.get('fluxo_id').replace('.',''))
        nome = data.get('nome')
        descricao = data.get('descricao', '')
        dados_fluxo = data.get('dados_fluxo', {})

        #if not nome:
        #    return JsonResponse({'status': 'error', 'message': 'Nome é obrigatório'}, status=400)

        if fluxo_id:
            # Atualizar fluxo existente
            fluxo = get_object_or_404(TbFluxoProducao, id=fluxo_id)
            fluxo.flu_pro_nome = nome
            fluxo.flu_pro_descricao = descricao
            fluxo.flu_pro_dados_fluxo = dados_fluxo
            fluxo.data_modificacao = datetime.datetime.now()
            fluxo.save()
            message = 'Fluxo atualizado com sucesso'
        else:
            # Criar novo fluxo
            fluxo = TbFluxoProducao.objects.create(
                flu_pro_nome=nome,
                flu_pro_descricao=descricao,
                dados_fluxo=dados_fluxo
            )
            message = 'Fluxo criado com sucesso'

        return JsonResponse({
            'status': 'success',
            'message': message,
            'fluxo_id': fluxo.id
        })

    except Exception as e:
        print(f"Erro ao salvar fluxo: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

