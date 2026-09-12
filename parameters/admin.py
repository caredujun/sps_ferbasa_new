import xlwt, time
from django.forms import TextInput, Textarea
from django import forms
from django.template.response import TemplateResponse

from fluxos.models import TbFluxoProducaoDaugther01, TbFluxoProducao
from otimizacao.models import TbProdutoMercadoFluxo, TbProdutoMercadoFluxoDaugther
from tabelas.models import TbGrupoCenarios
from .models import *
from .forms import TbCenariosFormAdmin, TbCenariosDaugtherFormAdmin
from .models import TbCenarios, TbEmpresa, AgenteConfig, HistoricoAgente, RelatorioPDF
from django.contrib import admin, messages
from django_object_actions import DjangoObjectActions
from .tasks import limpar_cenario_celery, limpar_cenario_tabela_mae_celery, otimizar_cenario_celery, \
    consolidar_cenario_celery, limpar_cenario_celery, remover_cenario_celery, atualizar_fluxos_celery

from django.http import HttpResponse
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.forms.models import BaseInlineFormSet
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.db.models import Case, When, Value, IntegerField
from .contexto_usuario import get_usuario_atual


class TbCenariosDaugther1Admin(admin.TabularInline):
    fields = ('data', 'o_que')

    model = TbCenariosDaugther1

    extra = 0


class TbCenariosDaugtherAdmin(admin.TabularInline):
    fields = ('display_order', 'otimizar', 'solucao_otima', 'str_dau_valor_1', 'str_dau_valor_2', 'str_dau_valor_3',
              'str_dau_valor_4', 'str_dau_valor_5', 'str_dau_valor_6', 'str_dau_valor_7', 'str_dau_valor_8',
              'str_dau_valor_9', 'str_dau_valor_10', 'str_dau_valor_11', 'str_dau_valor_12', 'str_dau_valor_13',
              'str_dau_valor_14', 'str_dau_valor_15', 'str_dau_valor_16', 'str_dau_valor_17', 'str_dau_valor_18',
              'str_dau_valor_19', 'str_dau_valor_20')

    readonly_fields = (
        'display_order', 'solucao_otima', 'str_dau_valor_1', 'str_dau_valor_2', 'str_dau_valor_3', 'str_dau_valor_4',
        'str_dau_valor_5', 'str_dau_valor_6', 'str_dau_valor_7', 'str_dau_valor_8', 'str_dau_valor_9',
        'str_dau_valor_10',
        'str_dau_valor_11', 'str_dau_valor_12', 'str_dau_valor_13', 'str_dau_valor_14', 'str_dau_valor_15',
        'str_dau_valor_16', 'str_dau_valor_17', 'str_dau_valor_18', 'str_dau_valor_19', 'str_dau_valor_20')

    model = TbCenariosDaugther
    form = TbCenariosDaugtherFormAdmin

    # Não permite delete
    def has_delete_permission(self, request, obj=None):
        return False

    # Não permite add
    def has_add_permission(self, request, obj=None):
        return False

    # Não permite edição
    def has_change_permission(self, request, obj=None):
        return True


class TbCenariosAdmin(DjangoObjectActions, admin.ModelAdmin):
    # 🌟 REMOVIDO 'cen_ativo' deste fields: editar aqui escrevia direto no
    # campo global (mesma armadilha que já tiramos da listagem). Ativar um
    # cenário agora é só pelo botão "Ativar" na listagem (por usuário).
    fields = (('cen_nome', 'cen_grupo'), ('cen_descricao'))
    list_display = ['id', 'ativo', 'botao_ativar', 'cen_nome', 'status_cenario', 'cen_descricao', 'cen_tipo',
                    'cen_inicio', 'cen_fim', 'cen_grupo']
    search_fields = ('cen_nome',)
    readonly_fields = ['status_cenario']
    list_display_links = ['id', 'cen_nome']
    # 🌟 REMOVIDO: list_editable = ['cen_ativo'] -- editava o campo global,
    # confundindo com o conceito de "cenário ativo por usuário". Substituído
    # pela coluna 'ativo' (calculada por usuário) + botão 'Ativar' por linha
    # (ver get_urls/ativar_para_mim/botao_ativar mais abaixo).
    list_filter = (('cen_grupo', admin.RelatedOnlyFieldListFilter),)

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    actions = ['delete_selected', 'exportar_excel']

    # 🌟 NOVO: URL customizada + coluna com botão "Ativar" por linha na
    # própria listagem, sem precisar abrir o registro do cenário.
    def get_urls(self):
        urls_customizadas = [
            path('<int:cenario_id>/ativar-para-mim/',
                 self.admin_site.admin_view(self.ativar_para_mim),
                 name='parameters_tbcenarios_ativar_para_mim'),
        ]
        return urls_customizadas + super().get_urls()

    def ativar_para_mim(self, request, cenario_id):
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            messages.error(request, 'Seu usuário não tem um Perfil de Usuário. Contate o administrador.')
        elif not perfil.pode_trocar_cenario and not request.user.is_superuser:
            messages.error(request, 'Seu usuário está bloqueado para trocar de cenário ativo. Contate o administrador.')
        else:
            cenario = TbCenarios.objects_real.get(id=cenario_id)
            perfil.cenario_ativo = cenario
            perfil.save()
            messages.success(request, f'Cenário {cenario.id}/{cenario.cen_nome} agora é o seu cenário ativo.')
        return redirect('admin:parameters_tbcenarios_changelist')

    def ativo(self, obj):
        usuario = get_usuario_atual()
        if usuario is None:
            return False
        perfil = getattr(usuario, 'perfilusuario', None)
        return bool(perfil and perfil.cenario_ativo_id == obj.id)

    ativo.short_description = 'Ativo'
    ativo.boolean = True

    def pode_trocar_cenario(self):
        # 🌟 Superusuário sempre pode trocar o PRÓPRIO cenário ativo, mesmo
        # que o campo pode_trocar_cenario do perfil dele esteja desmarcado
        # -- a trava é pensada pra restringir usuários comuns, não pra
        # travar quem administra a própria trava.
        usuario = get_usuario_atual()
        if usuario is None:
            return False
        if usuario.is_superuser:
            return True
        perfil = getattr(usuario, 'perfilusuario', None)
        return bool(perfil and perfil.pode_trocar_cenario)

    def botao_ativar(self, obj):
        if self.ativo(obj) or not self.pode_trocar_cenario():
            return '—'
        url = reverse('admin:parameters_tbcenarios_ativar_para_mim', args=[obj.id])
        return format_html('<a class="button" href="{}">Ativar</a>', url)

    botao_ativar.short_description = 'Ação'

    def get_ordering(self, request):
        # 🌟 CORRIGIDO: em vez de anotar um campo no get_queryset e ordenar
        # por nome (que quebra se algum código no caminho -- aqui foi o
        # django-object-actions -- pegar o queryset de outro jeito, sem
        # a anotação), a expressão de cálculo vai DIRETO na ordenação.
        # order_by() aceita expressões, não só nomes de campo -- isso
        # funciona em qualquer consulta, não depende de nada ter rodado
        # antes.
        id_cenario_do_usuario = None
        usuario = get_usuario_atual()
        if usuario is not None:
            perfil = getattr(usuario, 'perfilusuario', None)
            if perfil is not None:
                id_cenario_do_usuario = perfil.cenario_ativo_id

        ordem_ativo = Case(
            When(id=id_cenario_do_usuario, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
        return (ordem_ativo, 'id')

    def delete_selected(modeladmin, request, queryset):
        # Vamos ver se é superusuário.
        # Se sim, remove cenários selecionados. Os id 1, 2 e 3, mesmo se marcados, não serão rmovidos
        # Também o cenário ativo não poderá ser removido
        if request.user.is_superuser:
            lista_id = list(queryset.values_list('id', flat=True))
            # 🌟 CORRIGIDO: antes protegia só o cenário ativo GLOBAL
            # (cen_ativo=True) -- mas com "cenário ativo por usuário", um
            # cenário pode estar em uso ativo por algum usuário mesmo sem
            # ser o antigo "global". Agora bloqueia se QUALQUER usuário
            # tiver um dos selecionados como o seu próprio cenário ativo.
            perfis_afetados = PerfilUsuario.objects.filter(
                cenario_ativo_id__in=lista_id
            ).select_related('usuario', 'cenario_ativo')
            if perfis_afetados.exists():
                detalhes = ", ".join(
                    f"{p.cenario_ativo_id}/{p.cenario_ativo.cen_nome} (ativo para {p.usuario})"
                    for p in perfis_afetados
                )
                messages.error(request,
                               f'Cenário(s) selecionado(s) estão ativos para algum usuário e não podem ser excluídos: {detalhes}')
            else:
                if 1 not in lista_id and 2 not in lista_id and 3 not in lista_id:
                    # Vamos excluir os cenários selecionados
                    for id_list in lista_id:
                        remover_cenario_celery.delay(id_list)
                        # remover_cenario_celery(id_list)
                    messages.success(request,
                                     'Cenário(s) selecionado(s) sendo excluidos em segundo plano. Para verificar o status da exclusão, refresh a tela.')
                else:
                    messages.error(request,
                                   'Cenário 1, 2 ou 3 selecionado(s) e o(s) mesmo(s) não pode(m) ser excluído(s)')
        else:
            messages.error(request,
                           "Você não tem autorização para excluir cenários. Favor entrar em contato com administrador do sistema!")

    delete_selected.short_description = 'Remover Cenário(s) Selecionado(s)'

    def exportar_excel(self, request, queryset):
        # Só exporta a tabela com os valores selecionados
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="resultados_cenarios.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Resultados')

        # Vamos montar uma queryset com os valores das filhas
        lista_id = queryset.values_list('id', )

        filhas = TbCenariosDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        ws.write(row_num, 0, 'RESULTADOS POR CENÁRIO', font_style)

        columns = ['Id Cenário', 'Nome Cenário', 'Período', 'Solução Ótima', 'Vendas', 'Variável', 'Inbound',
                   'Outbound', 'Manut.', 'Margem', 'Fixo', 'EBTIDA', 'EBTIDA (%)', 'D&A', 'IR (%)', 'MP', 'WIP', 'PF',
                   'Total Est.', 'Receber', 'Pagar', 'OWCR', 'CAPEX', 'OFCF']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'dau_valor_8', 'dau_valor_9',
                                  'dau_valor_10', 'dau_valor_11', 'dau_valor_12', 'dau_valor_13', 'dau_valor_14',
                                  'dau_valor_15', 'dau_valor_16', 'dau_valor_17', 'dau_valor_18', 'dau_valor_19',
                                  'dau_valor_20', 'mae_id', 'flag').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[21]
            if row[22] == 1:  # Encontrou solução
                solucao = 'Sim'
            else:
                if row[22] == 0:  # Não encontrou solução
                    solucao = 'Não'
                else:
                    if row[22] == 2:
                        solucao = 'Limpo'
                    else:
                        if row[22] == 3:
                            solucao = 'Limpando'
                        else:
                            solucao = 'Otimizando'

            # Vamos pegar o nome do cenário
            nome_cenario = TbCenarios.objects.get(id=id_mae).cen_nome

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(0, id_mae)
            list_aux.insert(1, nome_cenario)
            list_aux.insert(3, solucao)

            # Vamos remover o mae_id e flag da lista. Estão na posição 24 e 25 após as inclusões dos nomes.
            del (list_aux[24])
            del (list_aux[24])

            # Vamos agora alterar o dau_order para mostrar o período no formato (ano, ano/mês ou ano/trimestre)
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(id=id_mae).cen_inicio

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[2])

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[2] - 1):
                    if month + 1 > 12:
                        month = 1
                        year = year + 1
                    else:
                        month = month + 1
                if month < 10:
                    periodo_str = str(year) + '/0' + str(month)
                else:
                    periodo_str = str(year) + '/' + str(month)

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Trimestral':
                year = int(inicio_periodo[:4])
                quarter = int(inicio_periodo[-2:])
                for j in range(list_aux[2] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[2] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel.short_description = 'Exportar Excel Cenário(s) Selecionado(s)'

    # Importa do cenário ativo
    # Usado para pegar os dados de um cenário mensal e lançar num cenário trimestral por exemplo
    # Copia as informações a nível de dau_order
    # Antes apaga todas as informações anteriormente cadastradas para copiar as informações do cenário ativo
    def importar_ativo(self, request, obj):
        # Vamos verificar se o cenário está ativo. Se está, não pode copiar dele mesmo
        if obj.id == TbCenarios.objects.get(cen_ativo=True).id:
            messages.error(request, 'Este cenário está ativo. Não pode importar dele mesmo!')
        else:
            if request.POST.get('post'):
                messages.success(request, 'Importação do cenário ativo sendo feita em segundo plano. Favor aguardar...')

                # Vamos montar uma lista das tabelas que queremos importar. Toda vez que for criada nova tabela, temos que atualizar essa lista
                lista_tabela = ('tabelas_tbcambio',
                                'tabelas_tbimpostorenda',
                                'tabelas_tbtaxadesconto',
                                'tabelas_tbcustofixo',
                                'tabelas_tbdepreamorti',
                                'tabelas_tbcapex',
                                'equipamentos_tbequipamentos',
                                'equipamentos_tbequipamentosconsumoespecifico',
                                'equipamentos_tbequipamentoscadastro',
                                'produtos_tbprodutos',
                                'produtos_tbprodutomercadopreco',
                                'produtos_tbmercadooutbound',
                                'fluxos_tbfluxoconsumopadrao',
                                'fluxos_tbfluxoproducao',
                                'fluxos_tbfluxoproducaoinputoutput',
                                'otimizacao_tbotimizacaoproduto',
                                'otimizacao_tbprodutomercado',
                                'otimizacao_tbprodutomercadofluxo',
                                'otimizacao_tbotimizacaoequipamentos',
                                'otimizacao_tbotimizacaoequipamentosordem',
                                'otimizacao_tbotimizacaocustoitem',
                                'otimizacao_tbconsumoespecificotipoproducao',
                                'otimizacao_tbotimizacaocomparacaocenarios'
                                'tabelas_tbcustoitempreco',
                                'tabelas_tbindicadores'
                                # Colocamos aqui pois tem outras tabelas que usam os indicadores
                                )

                # Removemos as tabelas abaixo da lista pois não é necessário duplicar.
                # Apesar de que se a tabela não tem o campo tbcenarios, a procedure duplica_tabela não faz nada.
                # 'tabelas_tbunidadeproducao',
                # 'tabelas_tbmercado',
                # 'tabelas_tbcustotipo',
                # 'tabelas_tbcustoitem',
                # 'tabelas_tbtipoproducao',
                # 'tabelas_tbfamiliaproduto',
                # 'tabelas_tbgrupocenarios,
                # 'tabelas_tbequacaoajustepreco',

                # Vamos duplicar as tabelas em segundo plano para evitar o erro no Heroku dos 30 segundos
                from .tasks import importar_cenario_ativo_celery

                # Vamos pegar o cenário ativo
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id

                transaction.on_commit(lambda: importar_cenario_ativo_celery.delay(lista_tabela, obj.id, cen_ativo_id))

            else:
                # modeladmin, request, queryset
                request.current_app = self.admin_site.name
                return TemplateResponse(request, "my_action_confirmation.html")

    importar_ativo.label = 'Importar Ativo'

    def exportar_excel_cenario(self, request, obj):
        # Só exporta o cenário selecionado
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="resultados_cenario.xls"'

        wb = xlwt.Workbook(encoding='utf-8')

        # Lançando valores
        ws = wb.add_sheet('Resultados')

        # Vamos montar uma queryset com os valores das filhas
        filhas = TbCenariosDaugther.objects.filter(mae_id=obj.id)

        # Sheet header, first row
        row_num = 0

        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        # Vamos pegar o nome do cenário
        nome_cenario = TbCenarios.objects.get(id=obj.id).cen_nome

        ws.write(row_num, 0, 'RESULTADOS CENÁRIO ' + str(obj.id) + '/' + nome_cenario, font_style)

        columns = ['Período', 'Solução Ótima', 'Vendas', 'Variável', 'Inbound', 'Outbound', 'Manut.', 'Margem', 'Fixo',
                   'EBTIDA', 'EBTIDA (%)', 'D&A', 'IR (%)', 'MP', 'WIP', 'PF', 'Total Est.', 'Receber', 'Pagar', 'OWCR',
                   'CAPEX', 'OFCF']

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'dau_valor_8', 'dau_valor_9',
                                  'dau_valor_10', 'dau_valor_11', 'dau_valor_12', 'dau_valor_13', 'dau_valor_14',
                                  'dau_valor_15', 'dau_valor_16', 'dau_valor_17', 'dau_valor_18', 'dau_valor_19',
                                  'dau_valor_20', 'mae_id', 'flag').order_by('mae_id', 'dau_order')

        # Vamos incluir os nomes de algumas colunas nos valores da lista new_rows
        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[21]
            if row[22] == 1:  # Encontrou solução
                solucao = 'Sim'
            else:
                if row[22] == 0:  # Não encontrou solução
                    solucao = 'Não'
                else:
                    if row[22] == 2:
                        solucao = 'Limpo'
                    else:
                        if row[22] == 3:
                            solucao = 'Limpando'
                        else:
                            solucao = 'Otimizando'

            # Inserindo na lista new_rows
            list_aux = list(rows[linha_num])
            list_aux.insert(1, solucao)

            # Vamos remover o mae_id e flag da lista. Estão na posição 22 e 23 após as inclusões dos nomes.
            del (list_aux[22])
            del (list_aux[22])

            # Vamos agora alterar o dau_order para mostrar o período no formato (ano, ano/mês ou ano/trimestre)
            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(id=id_mae).cen_inicio

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + list_aux[0])

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(list_aux[0] - 1):
                    if month + 1 > 12:
                        month = 1
                        year = year + 1
                    else:
                        month = month + 1
                if month < 10:
                    periodo_str = str(year) + '/0' + str(month)
                else:
                    periodo_str = str(year) + '/' + str(month)

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Trimestral':
                year = int(inicio_periodo[:4])
                quarter = int(inicio_periodo[-2:])
                for j in range(list_aux[0] - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

            list_aux[0] = periodo_str

            new_rows.append(list_aux)

            linha_num += 1

        rows = new_rows
        for row in rows:
            row_num += 1
            for col_num in range(len(row)):
                ws.write(row_num, col_num, row[col_num], font_style)

        wb.save(response)

        return response

    exportar_excel_cenario.label = 'Exportar Excel'

    # Action Atualizar Fluxos de produção para garantir que eventuais ajustes nos consumos padrões serão atualizados
    def update_fluxos(self, request, obj):
        if self.ativo(obj):

            # Vamos mudar o status para 7 para mostrar que está atualizando os fluxos de produção
            obj.flag = 7  # change field
            obj.save()  # this will update only

            # Vamos mudar o status das filhas para mostrar que está atualizando os fluxos de produção
            cen_ativo = obj.id

            # Vamos obter o total de períodos para o cenário ativo
            cursor = connection.cursor()
            sql = "select conta_periodos(" + str(cen_ativo) + ")"
            cursor.execute(sql)
            total_periodos = cursor.fetchone()[0]
            cursor.close()

            # Vamos alterar o flag das filhas para efeito de controle (6 significa que está atualizando os fluxos de produção)
            # Só vamos alterar o flag para as filhas que foi marcado para otimizar
            for i in range(total_periodos):
                if TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1).otimizar:
                    t = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1)
                    t.flag = 6
                    t.save()

            # Atualizando fluxos do cenário ativo
            atualizar_fluxos_celery.delay(obj.id)

            messages.success(request,
                             'Atualização dos Fluxos de Produção sendo realizado em segundo plano. Favor aguardar!')

        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    update_fluxos.label = "Atualizar Fluxos"  # optional

    # Action limpar tabelas para novo cálculo da otimização
    def limpar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request, 'Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.')
            return
        if self.ativo(obj):

            # Temos que verificar se tem algum fluxo onde não foi calculado os custos variáveis
            if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=obj.id,
                                                        mae_id__flu_pro_ativo=True).count() > 0:
                messages.error(request,
                               'TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) SEM O CÁLCULO DOS CUSTOS VARIÁVEIS. FAVOR VERIFICAR!')
            else:
                # Temos que verificar se tem algum fluxo com o I/O desatualizado
                if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=obj.id,
                                                  flu_pro_ativo=True).count() > 0:
                    messages.error(request,
                                   'TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM I/O DESATUALIZADO(S). FAVOR VERIFICAR!')
                else:
                    # Temos que verificar se tem algum fluxo com o custo variável zerado
                    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=obj.id,
                                                                mae_id__flu_pro_ativo=True).count() > 0:
                        messages.error(request,
                                       'TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM CUSTO VARIÁVEL ZERADO. FAVOR VERIFICAR!')
                    else:

                        cursor = connection.cursor()
                        # Vamos mudar o status para 5 para mostrar que está limpando
                        sql = "update parameters_tbcenarios set flag = 5 where id = " + str(obj.id)
                        cursor.execute(sql)
                        # Vamos mudar o status das filhas para mostrar que está limpando
                        cen_ativo = obj.id
                        sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(
                            cen_ativo)
                        cursor.execute(sql)

                        cursor.close()

                        # Limpando cenário
                        transaction.on_commit(lambda: limpar_cenario_celery.delay(obj.id))
                        # limpar_cenario_celery.delay()
                        # limpar_cenario_celery()

                        messages.success(request,
                                         'LIMPEZA do cenário sendo realizada em segundo plano. Favor aguardar!')

        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    limpar_cenario.label = "Limpar"  # optional

    # Action otimizar cenário
    def otimizar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request, 'Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.')
            return
        if self.ativo(obj):
            if obj.flag == 2:  # Significa que foi limpo e está pronto para otimizar

                cursor = connection.cursor()

                # Vamos mudar o status para 4 para mostrar que está otimizando.
                sql = "update parameters_tbcenarios set flag = 4 where id = " + str(obj.id)
                cursor.execute(sql)

                # Vamos mudar o status das filhas para mostrar que está otimizando
                cen_ativo = obj.id
                sql = "update parameters_tbcenariosdaugther set flag = 4 where otimizar = true and mae_id = " + str(
                    cen_ativo)
                cursor.execute(sql)

                cursor.close()

                # Vamos obter o total de períodos para o cenário ativo
                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                # Vamos ver quantas variáveis nós temos na tabela de otimização
                total_variaveis = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).count()

                for i in range(total_periodos):
                    if TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1).otimizar:
                        transaction.on_commit(lambda: otimizar_cenario_celery.delay(i, cen_ativo, total_variaveis))
                        # otimizar_cenario_celery(i, cen_ativo, total_variaveis)

                messages.success(request, 'OTIMIZAÇÃO do cenário sendo realizada em segundo plano. Favor aguardar!')
            else:
                if obj.flag == 1:
                    messages.error(request, 'Cenário só pode ser LIMPO!')
                else:
                    messages.error(request, 'Cenário só pode ser LIMPO ou CONSOLIDADO!')
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    otimizar_cenario.label = "Otimizar"  # optional

    # Action status da otimização
    def status_otimizacao(self, request, obj):
        if self.ativo(obj):
            # Vamos obter o total de períodos para o cenário ativo
            cursor = connection.cursor()
            sql = "select conta_periodos(" + str(obj.id) + ")"
            cursor.execute(sql)
            total_periodos = cursor.fetchone()[0]
            cursor.close()

            cen_ativo = obj.id

            # Vamos obter o total de periodos já otimizados
            total_otimizado = TbCenariosDaugther.objects.filter(mae_id=cen_ativo,
                                                                flag=1).count() + TbCenariosDaugther.objects.filter(
                mae_id=cen_ativo, flag=0).count()
            # Vamos mudar a cor da mensagem
            if total_otimizado < total_periodos:
                messages.error(request, 'Realizado ' + str(total_otimizado) + ' de um total de ' + str(
                    total_periodos) + ' períodos.')
            else:
                messages.success(request, 'Realizado ' + str(total_otimizado) + ' de um total de ' + str(
                    total_periodos) + ' períodos.')
        else:
            messages.error(request,
                           'ESSE CENÁRIO (' + str(
                               obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    status_otimizacao.label = "Status Otimização"  # optional

    # Action consolidar os resultados do cenário após otimização
    def consolidar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request, 'Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.')
            return
        if self.ativo(obj):
            if obj.flag == 3:  # 3 significa que o cenário foi otimizado.
                # Vamos alterar o flag do cenário para mostrar que está otimizando
                obj.flag = 6  # 6 significa que o cenário está sendo consolidado
                obj.save()

                consolidar_cenario_celery.delay(obj.id)

                messages.success(request, 'CONSOLIDAÇÃO do cenário sendo realizada em segundo plano. Favor aguardar!')

            else:
                if obj.flag == 1:
                    messages.error(request, 'Cenário só pode ser LIMPO!')
                else:
                    if obj.flag == 2:
                        messages.error(request, 'Cenário só pode ser OTIMIZADO!')
                    else:
                        if obj.flag == 3:
                            messages.error(request, 'Cenário só pode ser CONSOLIDADO!')
                        else:
                            if obj.flag == 4:
                                messages.error(request, 'Cenário só pode ser LIMPO!')
                            else:
                                if obj.flag == 5:
                                    messages.error(request, 'Cenário só pode ser LIMPO!')
                                else:
                                    messages.error(request, 'Cenário só pode ser LIMPO!')
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    consolidar_cenario.label = "Consolidar"  # optional

    # 🌟 NOVO: botão "Ativar Este Cenário" dentro do próprio registro,
    # reaproveitando a mesma lógica do botão da listagem.
    def ativar_cenario_action(self, request, obj):
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            messages.error(request, 'Seu usuário não tem um Perfil de Usuário. Contate o administrador.')
            return
        if not perfil.pode_trocar_cenario and not request.user.is_superuser:
            messages.error(request, 'Seu usuário está bloqueado para trocar de cenário ativo. Contate o administrador.')
            return
        if perfil.cenario_ativo_id == obj.id:
            messages.info(request, f'Cenário {obj.id}/{obj.cen_nome} já é o seu cenário ativo.')
            return
        perfil.cenario_ativo = obj
        perfil.save()
        messages.success(request, f'Cenário {obj.id}/{obj.cen_nome} agora é o seu cenário ativo.')

    ativar_cenario_action.label = "Ativar Este Cenário"

    change_actions = ('ativar_cenario_action', 'update_fluxos', 'limpar_cenario', 'otimizar_cenario',
                      'status_otimizacao', 'consolidar_cenario', 'exportar_excel_cenario')

    # Removemos a opção de update_fluxos pois se tiver algum fluxo ATIVO com I/O desatualizado, não é possível limpar

    # Vamos ajustar a lista de ações que o usuário pode fazer no cenário selecionado
    # Se o usuário não pode editar o registro, só mostra a ação de histórico
    def get_change_actions(self, request, object_id, form_url):
        actions = super(TbCenariosAdmin, self).get_change_actions(request, object_id, form_url)
        actions = list(actions)

        u = User.objects.get(username=request.user)
        if not u.has_perm('parameters.change_tbcenarios'):
            return []

        # 🌟 NOVO: se este cenário já é o ativo do usuário logado, não faz
        # sentido mostrar o botão "Ativar Este Cenário" -- some da lista.
        obj = self.get_object(request, object_id)
        if obj is not None and self.ativo(obj) and 'ativar_cenario_action' in actions:
            actions.remove('ativar_cenario_action')

        # 🌟 NOVO: se o usuário está bloqueado para trocar de cenário,
        # o botão "Ativar Este Cenário" nunca aparece, independente de
        # qual cenário está sendo visualizado.
        if obj is not None and not self.pode_trocar_cenario() and 'ativar_cenario_action' in actions:
            actions.remove('ativar_cenario_action')

        # 🌟 NOVO: se existe fluxo de produção ativo com I/O desatualizado,
        # só faz sentido mostrar "Atualizar Fluxos" -- as outras ações
        # (Limpar, Otimizar, Consolidar, Status) operariam sobre dados que
        # ainda não refletem a última alteração feita no fluxo.
        if obj is not None and self.tem_fluxo_desatualizado(obj):
            actions = ['update_fluxos'] if 'update_fluxos' in actions else []
        elif 'update_fluxos' in actions:
            actions.remove('update_fluxos')

        return actions

    def tem_fluxo_desatualizado(self, obj):
        return TbFluxoProducao.objects.filter(
            flu_pro_input_output_atualizado=False, tbcenarios_id=obj.id, flu_pro_ativo=True
        ).exists()

    form = TbCenariosFormAdmin

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '20'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 105})},
    }

    # Cria o grupo de cenários AS IS se não existir
    # Temos que primeiro ver se a tabela TbGrupoCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()

    if 'tabelas_tbgrupocenarios' in all_tables:
        # Existe. Vamos ver se tem o grupo com id = 1 (QUE É 'AS IS')
        if TbGrupoCenarios.objects.filter(id=1).count() == 0:  # Não tem. Vamos criar
            cen = TbGrupoCenarios.objects.create(id=1, gru_cen_codigo='AS IS')

    # Cria os cenários As Is (Mensal, Trimestral e Anual se não existirem)
    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    # all_tables = connection.introspection.table_names()
    if 'parameters_tbcenarios' in all_tables:
        # Existe. Vamos ver se tem registros padrões

        if TbCenarios.objects.filter(id=1).count() == 0:  # Não tem. Vamos criar
            cen = TbCenarios.objects.create(id=1, cen_nome='AS IS MENSAL',
                                            cen_descricao='AS IS MensaL. Cenário foi criado automaticamente pelo sistema!',
                                            cen_tipo='Mensal', cen_inicio='2022/01', cen_fim='2022/12', cen_ativo=True,
                                            cen_grupo_id=1)

        if TbCenarios.objects.filter(id=2).count() == 0:  # Não tem. Vamos criar
            cen = TbCenarios.objects.create(id=2, cen_nome='AS IS TRIMESTRAL',
                                            cen_descricao='AS IS Trimestral. Cenário foi criado automaticamente pelo sistema!',
                                            cen_tipo='Trimestral', cen_inicio='2022/01', cen_fim='2022/04',
                                            cen_ativo=False, cen_grupo_id=1)

        if TbCenarios.objects.filter(id=3).count() == 0:  # Não tem. Vamos criar
            cen = TbCenarios.objects.create(id=3, cen_nome='AS IS ANUAL',
                                            cen_descricao='As Is Anual. Cenário foi criado automaticamente pelo sistema!',
                                            cen_tipo='Anual', cen_inicio='2022', cen_fim='2031', cen_ativo=False,
                                            cen_grupo_id=1)

        # Vamos ajustar a sequência da tabela parameters_tbcenarios
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure ajustar_sequencia
        sql = "call public.ajustar_sequencia('parameters_tbcenarios'" + ")"
        cursor.execute(sql)
        cursor.close()

    # Mostra o campo ind_valor_inicial somente se estiver incluindo novo registro
    def get_fields(self, request, obj=None):
        if not obj:  # Se estiver adicionando,  irá aparecer o campo para informar o cenário para copiar
            return self.fields + ('cen_copiar_de',)
        # 🌟 'ativo' acrescentado aqui -- mostra se ESTE registro é o
        # cenário ativo do usuário logado, direto no formulário de edição
        # (como você pediu, igual a como era antes com o campo cen_ativo).
        return self.fields + (('cen_tipo', 'status_cenario'), ('cen_inicio', 'cen_fim'), 'ativo')

    # Campo cen_tipo não poderá ser editado
    readonly_fields = ('cen_tipo', 'status_cenario', 'ativo')

    def get_readonly_fields(self, request, obj=None):
        # Se for cenarios As Is não pode alterar o nome
        if obj:
            if obj.id == 1 or obj.id == 2 or obj.id == 3:
                return self.readonly_fields + ('cen_nome',)
            # 🌟 'cen_ativo' removido daqui -- o campo não aparece mais em
            # 'fields', então não faz sentido listá-lo como readonly (ele
            # simplesmente não é exibido). A condição em si (obj.cen_ativo,
            # acesso direto ao atributo do registro já carregado, não passa
            # pelo Manager) continua válida pra travar o NOME do cenário
            # que é o ativo global.
            if obj.cen_ativo == 1:
                return self.readonly_fields + ('cen_nome',)
        return self.readonly_fields

    # Tabela Cenários só pode ser modificada, deletada ou receber novos dados pelo super administrador.

    def has_delete_permission(self, request, obj=None):
        return False

    # Vamos ver se usuário é superuser. Se sim, permite adição na tabela.
    def has_add_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    inlines = [TbCenariosDaugtherAdmin, TbCenariosDaugther1Admin]


# registrando
admin.site.register(TbCenarios, TbCenariosAdmin)


class TbEmpresaAdmin(admin.ModelAdmin):
    # fields = [('emp_nome', 'emp_tipo'), 'emp_descricao', 'emp_moeda', ('emp_moeda_imagem', 'emp_moeda_imagem_tag'), ('emp_logo', 'emp_logo_tag'), ('emp_fluxo', 'emp_fluxo_tag'), ('emp_informacao1', 'emp_fonte1'), ('emp_informacao2', 'emp_fonte2'), ('emp_informacao3', 'emp_fonte3')]
    fields = [('emp_nome', 'emp_tipo'), 'emp_descricao', 'emp_moeda', ('emp_moeda_imagem', 'emp_moeda_imagem_tag'),
              ('emp_logo', 'emp_logo_tag'), ('emp_fluxo', 'emp_fluxo_tag')]

    readonly_fields = ['emp_moeda_imagem_tag', 'emp_logo_tag', 'emp_fluxo_tag']
    list_display = ['emp_nome', 'emp_logo', 'emp_logo_tag', 'emp_tipo', 'emp_descricao', 'emp_moeda',
                    'emp_moeda_imagem', 'emp_moeda_imagem_tag', 'emp_fluxo', 'emp_fluxo_tag']

    # Cria empresa se não existir
    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        # Se existe, verificamos se tem pelo menos um registro. Se não tem registro, criamos um para ser ajustado posteriormente.
        qtde = TbEmpresa.objects.count()
        if qtde == 0:
            emp = TbEmpresa.objects.create(emp_nome='Favor alterar', emp_descricao='Favor alterar', emp_moeda='BRL')

    # Tabela Empresa só pode ser modificada pelo administrador. Não pode ser deletada ou receber mais dados.

    # Tabela tipo parâmetro.
    # Prevent deletion from admin portal
    def has_delete_permission(self, request, obj=None):
        return False

    # Vamos ver se usuário é superuser. Se sim, permite adição na tabela se não tiver dados
    def has_add_permission(self, request, obj=None):
        current_user = request.user

        # Vamos ver se tem registro na tabela empresa
        qtde = TbEmpresa.objects.count()  # ou pode usar qtde = len(TbEmpresa.objects.all())

        if current_user.is_superuser and qtde == 0:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser

    # Vamos ver se usuário é superuser. Se sim, permite edição da tabela.
    def has_change_permission(self, request, obj=None):
        current_user = request.user
        if current_user.is_superuser:
            issuperuser = True
        else:
            issuperuser = False
        return issuperuser


# registrando
admin.site.register(TbEmpresa, TbEmpresaAdmin)


class TbGlossarioAdmin(admin.ModelAdmin):
    fields = ('glo_nome', 'glo_descricao', ('glo_imagem', 'glo_imagem_tag'), 'glo_fonte')
    list_display = ['glo_nome', 'glo_descricao', 'glo_imagem', 'glo_imagem_tag', 'glo_fonte']

    readonly_fields = ['glo_imagem_tag']

    formfield_overrides = {
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 7, 'cols': 100})},
    }


admin.site.register(TbGlossario, TbGlossarioAdmin)


# Cenário ativo por usuário
class DefinirCenarioAtivoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    cenario = forms.ModelChoiceField(
        queryset=TbCenarios.objects.order_by('id'), label='Cenário', required=True
    )


class PerfilUsuarioForm(forms.ModelForm):
    """
    🌟 NOVO: torna 'cenario_ativo' obrigatório no formulário do Admin --
    tanto ao criar um usuário novo (via PerfilUsuarioInline) quanto ao
    editar um perfil existente (via PerfilUsuarioAdmin). Isso evita o
    problema na raiz: sem isso, dava pra criar/deixar um usuário sem
    cenário ativo definido, e ele só descobria isso depois, tentando usar
    o sistema e sendo redirecionado com um aviso.

    Propositalmente NÃO mudamos isso no model (PerfilUsuario.cenario_ativo
    continua null=True, blank=True lá) -- só aqui, no formulário. Mudar no
    model tornaria obrigatório em QUALQUER contexto, inclusive no
    get_or_create do signal que cria o perfil vazio automaticamente
    (criar_perfil_usuario), o que quebraria esse mecanismo.
    """

    class Meta:
        model = PerfilUsuario
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cenario_ativo'].required = True


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    form = PerfilUsuarioForm
    list_display = ('usuario', 'cenario_ativo', 'pode_trocar_cenario')
    list_editable = ('pode_trocar_cenario',)
    list_filter = ('cenario_ativo', 'pode_trocar_cenario')
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name')
    autocomplete_fields = ('usuario', 'cenario_ativo')
    actions = ['definir_cenario_ativo_em_massa', 'bloquear_troca_cenario', 'desbloquear_troca_cenario']

    def bloquear_troca_cenario(self, request, queryset):
        total = queryset.update(pode_trocar_cenario=False)
        self.message_user(request, f'{total} usuário(s) bloqueado(s) para trocar de cenário ativo.')

    bloquear_troca_cenario.short_description = "Bloquear troca de cenário ativo (selecionados)"

    def desbloquear_troca_cenario(self, request, queryset):
        total = queryset.update(pode_trocar_cenario=True)
        self.message_user(request, f'{total} usuário(s) desbloqueado(s) para trocar de cenário ativo.')

    desbloquear_troca_cenario.short_description = "Desbloquear troca de cenário ativo (selecionados)"

    def definir_cenario_ativo_em_massa(self, request, queryset):
        form = None
        if 'aplicar' in request.POST:
            form = DefinirCenarioAtivoForm(request.POST)
            if form.is_valid():
                cenario = form.cleaned_data['cenario']
                total = queryset.update(cenario_ativo=cenario)
                self.message_user(
                    request,
                    f'{total} usuário(s) tiveram o cenário ativo definido para {cenario.id}/{cenario.cen_nome}.'
                )
                return None

        if form is None:
            form = DefinirCenarioAtivoForm(initial={'_selected_action': queryset.values_list('pk', flat=True)})

        return render(request, 'admin/parameters/perfilusuario/definir_cenario_em_massa.html', {
            'perfis': queryset,
            'form': form,
            'title': 'Definir cenário ativo para os usuários selecionados',
            'opts': self.model._meta,
        })

    definir_cenario_ativo_em_massa.short_description = "Definir cenário ativo para os selecionados"

    def has_add_permission(self, request):
        # Todo usuário já ganha um PerfilUsuario automaticamente via signal
        # (criar_perfil_usuario, em models.py) -- não faz sentido criar um
        # solto manualmente, sem vínculo com um usuário real já existente.
        return False

    def has_delete_permission(self, request, obj=None):
        # Apagar o perfil deixaria o usuário sem lugar pra guardar o
        # cenário ativo -- bloqueado pelo mesmo motivo do has_add acima.
        return False


class PerfilUsuarioInlineFormSet(BaseInlineFormSet):
    """
    O signal em models.py (criar_perfil_usuario) já cria um PerfilUsuario
    vazio no exato momento em que um novo User é salvo -- isso acontece
    ANTES deste formset rodar (o Admin salva o User primeiro, o formset do
    inline depois). Se o formset tentasse simplesmente inserir um novo
    registro, ia falhar (o OneToOneField já estaria ocupado pelo que o
    signal criou). Por isso, em vez de inserir, buscamos o que o signal já
    criou e só atualizamos os campos escolhidos no formulário.
    """

    def save_new(self, form, commit=True):
        perfil, _ = PerfilUsuario.objects.get_or_create(usuario=self.instance)
        for campo, valor in form.cleaned_data.items():
            if campo not in ('usuario', 'id', 'DELETE'):
                setattr(perfil, campo, valor)
        if commit:
            perfil.save()
        return perfil


class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    form = PerfilUsuarioForm
    formset = PerfilUsuarioInlineFormSet
    can_delete = False
    fields = ('cenario_ativo', 'pode_trocar_cenario')
    verbose_name_plural = 'Cenário ativo'
    # 🌟 NOVO: sem isso, o Django pode simplesmente IGNORAR um formulário
    # de inline deixado totalmente em branco (comportamento padrão de
    # formset, achando que é "opcional"), sem nem chegar a checar se
    # cenario_ativo é obrigatório. min_num=1 + validate_min=True força
    # a existir pelo menos 1 formulário de fato preenchido e validado.
    min_num = 1
    max_num = 1
    extra = 1
    validate_min = True


class CustomUserAdmin(UserAdmin):
    inlines = (PerfilUsuarioInline,)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# Agente de IA
@admin.register(AgenteConfig)
class AgenteConfigAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_editable = ('ativo',)  # 🌟 Permite marcar/desmarcar direto na listagem, sem abrir o registro


@admin.register(HistoricoAgente)
class HistoricoAgenteAdmin(admin.ModelAdmin):
    list_display = ('data', 'usuario', 'comando_usuario')
    readonly_fields = ('data', 'usuario', 'comando_usuario', 'resposta_ia')  # Evita edição dos logs

    def get_list_filter(self, request):
        # Filtro por usuário só faz sentido pra quem vê o histórico de todo mundo
        if request.user.is_superuser:
            return ('usuario',)
        return ()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(usuario=request.user)


@admin.register(RelatorioPDF)
class RelatorioPDFAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'ativo', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('titulo',)  # Removed texto_extraido from here