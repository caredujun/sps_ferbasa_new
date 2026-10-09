import xlwt, time, types
from django.forms import TextInput, Textarea
from django import forms
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _

from fluxos.models import TbFluxoProducaoDaugther01, TbFluxoProducao
from otimizacao.models import TbProdutoMercadoFluxo, TbProdutoMercadoFluxoDaugther
from tabelas.models import TbGrupoCenarios
from .models import *
from .forms import TbCenariosFormAdmin, TbCenariosDaugtherFormAdmin
from .models import TbCenarios, TbEmpresa, AgenteConfig, HistoricoAgente, RelatorioPDF
from django.contrib import admin, messages
from django_object_actions import DjangoObjectActions
from .tasks import limpar_cenario_celery, limpar_cenario_tabela_mae_celery, otimizar_cenario_celery, \
    consolidar_cenario_celery, limpar_cenario_celery, remover_cenario_celery, remover_empresa_celery, \
    atualizar_fluxos_celery

from django.http import HttpResponse
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.forms.models import BaseInlineFormSet
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.utils.html import format_html, format_html_join
from django.db.models import Case, When, Value, IntegerField, Q
from django.db import transaction
from django.db.models.signals import post_migrate
from .contexto_usuario import get_usuario_atual, eh_superuser_ou_superuser_empresa


# ---------------------------------------------------------------------------------------------------------------
# 🌟 NOVO: garante que exista pelo menos uma empresa (o resto do sistema usa TbEmpresa id=1 em vários lugares).
#
# Antes isso era feito no CORPO da classe TbEmpresaAdmin (connection.introspection.table_names() + count + create),
# que roda na importação do admin, antes de o Django terminar de iniciar -- era o que gerava o aviso "Accessing the
# database during app initialization is discouraged". Agora é feito:
#   1) depois de cada "migrate" (sinal post_migrate -- o lugar indicado pelo Django pra dados iniciais), o que cobre
#      a instalação de um banco novo;
#   2) ao abrir a lista de Empresas no Admin (changelist_view), por garantia.
# ---------------------------------------------------------------------------------------------------------------
def garantir_empresa_inicial():
    try:
        with transaction.atomic():      # savepoint: se a tabela ainda não existir, não derruba a transação de quem chamou
            if not TbEmpresa.objects.exists():
                TbEmpresa.objects.create(emp_nome='Favor alterar', emp_descricao='Favor alterar', emp_moeda='BRL')
    except Exception:
        pass


def _garantir_empresa_inicial_apos_migrate(sender, **kwargs):
    if getattr(sender, 'name', None) == 'parameters':
        garantir_empresa_inicial()


post_migrate.connect(_garantir_empresa_inicial_apos_migrate, dispatch_uid='parameters_garantir_empresa_inicial')


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

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return True


class TbCenariosAdmin(DjangoObjectActions, admin.ModelAdmin):
    fields = (('cen_nome', 'cen_grupo'), ('cen_descricao'))
    list_display = ['numero_sequencial', 'ativo', 'botao_ativar', 'cen_nome', 'status_cenario',
                    'cen_descricao', 'cen_tipo',
                    'cen_inicio', 'cen_fim', 'cen_grupo']
    search_fields = ('cen_nome',)
    readonly_fields = ['status_cenario', 'ultimas_alteracoes']
    list_display_links = ['numero_sequencial', 'cen_nome']
    list_filter = (('cen_grupo', admin.RelatedOnlyFieldListFilter),)

    class Media:
        js = ('jquery.mask.min.js', 'custom.js')

    actions = ['delete_selected', 'exportar_excel']

    def get_urls(self):
        urls_customizadas = [
            path('<int:cenario_id>/ativar-para-mim/',
                 self.admin_site.admin_view(self.ativar_para_mim),
                 name='parameters_tbcenarios_ativar_para_mim'),
            path('cenarios-por-empresa/<int:empresa_id>/',
                 self.admin_site.admin_view(self.cenarios_por_empresa_json),
                 name='parameters_tbcenarios_por_empresa'),
        ]
        return urls_customizadas + super().get_urls()

    def ativar_para_mim(self, request, cenario_id):
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            messages.error(request, _('Seu usuário não tem um Perfil de Usuário. Contate o administrador.'))
        elif not perfil.pode_trocar_cenario and not request.user.is_superuser:
            messages.error(request,
                           _('Seu usuário está bloqueado para trocar de cenário ativo. Contate o administrador.'))
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

    ativo.short_description = _('Ativo')
    ativo.boolean = True

    def pode_trocar_cenario(self):
        usuario = get_usuario_atual()
        if usuario is None:
            return False
        if eh_superuser_ou_superuser_empresa(usuario):
            return True
        perfil = getattr(usuario, 'perfilusuario', None)
        return bool(perfil and perfil.pode_trocar_cenario)

    def botao_ativar(self, obj):
        if self.ativo(obj) or not self.pode_trocar_cenario():
            return '—'
        url = reverse('admin:parameters_tbcenarios_ativar_para_mim', args=[obj.id])
        return format_html(_('<a class="button" href="{}">Ativar</a>'), url)

    botao_ativar.short_description = _('Ação')

    def get_ordering(self, request):
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
        return (ordem_ativo, 'numero_sequencial')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            return qs.none()
        empresa_id = perfil.empresa_efetiva_id()
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)

    def cenarios_por_empresa_json(self, request, empresa_id):
        from django.http import JsonResponse
        cenarios = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('numero_sequencial', 'id')
        dados = [
            {'id': c.id, 'texto': f"{c.numero_sequencial if c.numero_sequencial is not None else c.id}/{c.cen_nome}"}
            for c in cenarios
        ]
        return JsonResponse({'cenarios': dados})

    def delete_selected(modeladmin, request, queryset):
        if eh_superuser_ou_superuser_empresa(request.user):
            lista_id = list(queryset.values_list('id', flat=True))
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
                tem_cenario_base_selecionado = TbCenarios.objects_real.filter(
                    id__in=lista_id, eh_cenario_base=True
                ).exists()
                if not tem_cenario_base_selecionado:
                    for id_list in lista_id:
                        remover_cenario_celery.delay(id_list)
                    messages.success(request,
                                     _('Cenário(s) selecionado(s) sendo excluidos em segundo plano. Para verificar o status da exclusão, refresh a tela.'))
                else:
                    messages.error(request,
                                   _('Um ou mais cenários selecionados são cenários base da empresa e não podem ser excluídos'))
        else:
            messages.error(request,
                           _("Você não tem autorização para excluir cenários. Favor entrar em contato com administrador do sistema!"))

    delete_selected.short_description = _('Remover Cenário(s) Selecionado(s)')

    def exportar_excel(self, request, queryset):
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="resultados_cenarios.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('Resultados')
        lista_id = queryset.values_list('id', )
        filhas = TbCenariosDaugther.objects.filter(mae_id__in=lista_id).order_by('id')

        row_num = 0
        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        ws.write(row_num, 0, str(_('RESULTADOS POR CENÁRIO')), font_style)

        columns = [str(c) for c in [
            _('Id Cenário'), _('Nome Cenário'), _('Período'), _('Solução Ótima'), _('Vendas'), _('Variável'),
            _('Inbound'), _('Outbound'), _('Manut.'), _('Margem'), _('Fixo'), _('EBTIDA'), _('EBTIDA (%)'),
            _('D&A'), _('IR (%)'), _('MP'), _('WIP'), _('PF'), _('Total Est.'), _('Receber'), _('Pagar'),
            _('OWCR'), _('CAPEX'), _('OFCF'),
        ]]

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        font_style = xlwt.XFStyle()
        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'dau_valor_8', 'dau_valor_9',
                                  'dau_valor_10', 'dau_valor_11', 'dau_valor_12', 'dau_valor_13', 'dau_valor_14',
                                  'dau_valor_15', 'dau_valor_16', 'dau_valor_17', 'dau_valor_18', 'dau_valor_19',
                                  'dau_valor_20', 'mae_id', 'flag').order_by('mae_id', 'dau_order')

        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[21]
            if row[22] == 1:
                solucao = str(_('Sim'))
            else:
                if row[22] == 0:
                    solucao = str(_('Não'))
                else:
                    if row[22] == 2:
                        solucao = str(_('Limpo'))
                    else:
                        if row[22] == 3:
                            solucao = str(_('Limpando'))
                        else:
                            solucao = str(_('Otimizando'))

            nome_cenario = TbCenarios.objects.get(id=id_mae).cen_nome
            list_aux = list(rows[linha_num])
            list_aux.insert(0, id_mae)
            list_aux.insert(1, nome_cenario)
            list_aux.insert(3, solucao)
            del (list_aux[24])
            del (list_aux[24])

            inicio_periodo = TbCenarios.objects.get(id=id_mae).cen_inicio

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Anual':
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

    exportar_excel.short_description = _('Exportar Excel Cenário(s) Selecionado(s)')

    def importar_ativo(self, request, obj):
        if obj.id == TbCenarios.objects.get(cen_ativo=True).id:
            messages.error(request, _('Este cenário está ativo. Não pode importar dele mesmo!'))
        else:
            if request.POST.get('post'):
                messages.success(request,
                                 _('Importação do cenário ativo sendo feita em segundo plano. Favor aguardar...'))

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
                                )

                from .tasks import importar_cenario_ativo_celery
                cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
                transaction.on_commit(lambda: importar_cenario_ativo_celery.delay(lista_tabela, obj.id, cen_ativo_id))

            else:
                request.current_app = self.admin_site.name
                return TemplateResponse(request, "my_action_confirmation.html")

    importar_ativo.label = _('Importar Ativo')

    def exportar_excel_cenario(self, request, obj):
        response = HttpResponse(content_type='application/ms-excel')
        response['Content-Disposition'] = 'attachment; filename="resultados_cenario.xls"'

        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('Resultados')
        filhas = TbCenariosDaugther.objects.filter(mae_id=obj.id)

        row_num = 0
        font_style = xlwt.XFStyle()
        font_style.font.bold = True

        nome_cenario = TbCenarios.objects.get(id=obj.id).cen_nome
        ws.write(row_num, 0, str(_('RESULTADOS CENÁRIO ')) + str(obj.id) + '/' + nome_cenario, font_style)

        columns = [str(c) for c in [
            _('Período'), _('Solução Ótima'), _('Vendas'), _('Variável'), _('Inbound'), _('Outbound'),
            _('Manut.'), _('Margem'), _('Fixo'), _('EBTIDA'), _('EBTIDA (%)'), _('D&A'), _('IR (%)'),
            _('MP'), _('WIP'), _('PF'), _('Total Est.'), _('Receber'), _('Pagar'), _('OWCR'),
            _('CAPEX'), _('OFCF'),
        ]]

        row_num += 1
        for col_num in range(len(columns)):
            ws.write(row_num, col_num, columns[col_num], font_style)

        font_style = xlwt.XFStyle()
        rows = filhas.values_list('dau_order', 'dau_valor_1', 'dau_valor_2', 'dau_valor_3', 'dau_valor_4',
                                  'dau_valor_5', 'dau_valor_6', 'dau_valor_7', 'dau_valor_8', 'dau_valor_9',
                                  'dau_valor_10', 'dau_valor_11', 'dau_valor_12', 'dau_valor_13', 'dau_valor_14',
                                  'dau_valor_15', 'dau_valor_16', 'dau_valor_17', 'dau_valor_18', 'dau_valor_19',
                                  'dau_valor_20', 'mae_id', 'flag').order_by('mae_id', 'dau_order')

        new_rows = []
        linha_num = 0
        for row in rows:
            id_mae = row[21]
            if row[22] == 1:
                solucao = str(_('Sim'))
            else:
                if row[22] == 0:
                    solucao = str(_('Não'))
                else:
                    if row[22] == 2:
                        solucao = str(_('Limpo'))
                    else:
                        if row[22] == 3:
                            solucao = str(_('Limpando'))
                        else:
                            solucao = str(_('Otimizando'))

            list_aux = list(rows[linha_num])
            list_aux.insert(1, solucao)
            del (list_aux[22])
            del (list_aux[22])

            inicio_periodo = TbCenarios.objects.get(id=id_mae).cen_inicio

            if TbCenarios.objects.get(id=id_mae).cen_tipo == 'Anual':
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

    exportar_excel_cenario.label = _('Exportar Excel')

    def update_fluxos(self, request, obj):
        if self.ativo(obj):
            obj.flag = 7
            obj.save()
            cen_ativo = obj.id

            cursor = connection.cursor()
            sql = "select conta_periodos(" + str(cen_ativo) + ")"
            cursor.execute(sql)
            total_periodos = cursor.fetchone()[0]
            cursor.close()

            for i in range(total_periodos):
                if TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1).otimizar:
                    t = TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1)
                    t.flag = 6
                    t.save()

            atualizar_fluxos_celery.delay(obj.id)
            messages.success(request,
                             _('Atualização dos Fluxos de Produção sendo realizado em segundo plano. Favor aguardar!'))
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    update_fluxos.label = _("Atualizar Fluxos")

    def limpar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
            return
        # Vamos verificar se temos Equipamentos/Ordem de produção com produtos escolhidos com nenhum fluxo usando o equipamento/ordem
        # ou se temos Produto com equipamento/ordem escolhido mas nenhum fluxo usando esse equipamento

        # 🌟 NOVO: interrompe a limpeza se algum produto ou algum equipamento/ordem do cenário estiver com
        # "Escolhidos Igual Fluxos" = Não (é o mesmo campo que aparece na lista e no formulário de Produtos e
        # de Equipamentos). Mostra até 10 de cada; havendo mais, "e outros". Pára de procurar ao achar o 11º
        # de cada tipo, pra não calcular o cenário inteiro à toa.
        # Só os produtos ATIVOS (pro_ativo=True) entram na verificação: produto inativo não bloqueia a limpeza.
        from itertools import islice
        from produtos.models import TbProdutos
        from equipamentos.models import TbEquipamentos
        produtos_nao = list(islice(
            (p.pro_codigo for p in TbProdutos.objects.filter(tbcenarios_id=obj.id, pro_ativo=True).order_by('pro_codigo')
             if p.escolhidos_igual_fluxos() == 'Não'), 11))
        # Equipamento/ordem: compara "escolhidos" x "usados nos fluxos" considerando só os produtos ATIVOS
        # (produto inativo que apareça só de um dos lados não bloqueia a limpeza).
        ids_produtos_ativos = set(
            TbProdutos.objects.filter(tbcenarios_id=obj.id, pro_ativo=True).values_list('pk', flat=True))
        equipamentos_nao = list(islice(
            (str(e) for e in TbEquipamentos.objects.filter(tbcenarios_id=obj.id).order_by(
                'equ_codigo__equ_cad_codigo', 'equ_ordem_codigo')
             if (e._ids_produtos_escolhidos() & ids_produtos_ativos) != (e._ids_produtos_usados() & ids_produtos_ativos)),
            11))
        if produtos_nao or equipamentos_nao:
            if produtos_nao:
                lista = ', '.join(produtos_nao[:10]) + (' e outros' if len(produtos_nao) > 10 else '')
                messages.error(request,
                               _('Limpeza interrompida. Produto(s) com "Escolhidos Igual Fluxos" = Não: %(lista)s. '
                                 'Abra o produto e veja o que está em vermelho.') % {'lista': lista})
            if equipamentos_nao:
                lista = ', '.join(equipamentos_nao[:10]) + (' e outros' if len(equipamentos_nao) > 10 else '')
                messages.error(request,
                               _('Limpeza interrompida. Equipamento(s)/Ordem(ns) com "Escolhidos Igual Fluxos" = Não: '
                                 '%(lista)s. Abra o equipamento e veja o que está em vermelho.') % {'lista': lista})
            return

        if self.ativo(obj):
            if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=obj.id,
                                                        mae_id__flu_pro_ativo=True).count() > 0:
                messages.error(request,
                               _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) SEM O CÁLCULO DOS CUSTOS VARIÁVEIS. FAVOR VERIFICAR!'))
            else:
                if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=obj.id,
                                                  flu_pro_ativo=True).count() > 0:
                    messages.error(request,
                                   _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM I/O DESATUALIZADO(S). FAVOR VERIFICAR!'))
                else:
                    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=obj.id,
                                                                mae_id__flu_pro_ativo=True).count() > 0:
                        messages.error(request,
                                       _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM CUSTO VARIÁVEL ZERADO. FAVOR VERIFICAR!'))
                    else:
                        cursor = connection.cursor()
                        sql = "update parameters_tbcenarios set flag = 5 where id = " + str(obj.id)
                        cursor.execute(sql)
                        cen_ativo = obj.id
                        sql = "update parameters_tbcenariosdaugther set flag = 3 where otimizar = true and mae_id = " + str(
                            cen_ativo)
                        cursor.execute(sql)
                        cursor.close()

                        transaction.on_commit(lambda: limpar_cenario_celery.delay(obj.id))
                        messages.success(request,
                                         _('LIMPEZA do cenário sendo realizada em segundo plano. Favor aguardar!'))
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    limpar_cenario.label = _("Limpar")

    def otimizar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
            return
        if self.ativo(obj):
            if obj.flag == 2:
                cursor = connection.cursor()
                sql = "update parameters_tbcenarios set flag = 4 where id = " + str(obj.id)
                cursor.execute(sql)
                cen_ativo = obj.id
                sql = "update parameters_tbcenariosdaugther set flag = 4 where otimizar = true and mae_id = " + str(
                    cen_ativo)
                cursor.execute(sql)
                cursor.close()

                cursor = connection.cursor()
                sql = "select conta_periodos(" + str(cen_ativo) + ")"
                cursor.execute(sql)
                total_periodos = cursor.fetchone()[0]
                cursor.close()

                total_variaveis = TbProdutoMercadoFluxo.objects.filter(tbcenarios_id=cen_ativo, flag=True).count()

                for i in range(total_periodos):
                    if TbCenariosDaugther.objects.get(mae_id=cen_ativo, dau_order=i + 1).otimizar:
                        transaction.on_commit(lambda: otimizar_cenario_celery.delay(i, cen_ativo, total_variaveis))

                messages.success(request, _('OTIMIZAÇÃO do cenário sendo realizada em segundo plano. Favor aguardar!'))
            else:
                if obj.flag == 1:
                    messages.error(request, _('Cenário só pode ser LIMPO!'))
                else:
                    messages.error(request, _('Cenário só pode ser LIMPO ou CONSOLIDADO!'))
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    otimizar_cenario.label = _("Otimizar")

    def status_otimizacao(self, request, obj):
        if self.ativo(obj):
            cursor = connection.cursor()
            sql = "select conta_periodos(" + str(obj.id) + ")"
            cursor.execute(sql)
            total_periodos = cursor.fetchone()[0]
            cursor.close()

            cen_ativo = obj.id
            total_otimizado = TbCenariosDaugther.objects.filter(mae_id=cen_ativo,
                                                                flag=1).count() + TbCenariosDaugther.objects.filter(
                mae_id=cen_ativo, flag=0).count()
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

    status_otimizacao.label = _("Status Otimização")

    def consolidar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
            return
        if self.ativo(obj):
            if obj.flag == 3:
                obj.flag = 6
                obj.save()
                consolidar_cenario_celery.delay(obj.id)
                messages.success(request,
                                 _('CONSOLIDAÇÃO do cenário sendo realizada em segundo plano. Favor aguardar!'))
            else:
                if obj.flag == 1:
                    messages.error(request, _('Cenário só pode ser LIMPO!'))
                else:
                    if obj.flag == 2:
                        messages.error(request, _('Cenário só pode ser OTIMIZADO!'))
                    else:
                        if obj.flag == 3:
                            messages.error(request, _('Cenário só pode ser CONSOLIDADO!'))
                        else:
                            if obj.flag == 4:
                                messages.error(request, _('Cenário só pode ser LIMPO!'))
                            else:
                                if obj.flag == 5:
                                    messages.error(request, _('Cenário só pode ser LIMPO!'))
                                else:
                                    messages.error(request, _('Cenário só pode ser LIMPO!'))
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    consolidar_cenario.label = _("Consolidar")

    def ativar_cenario_action(self, request, obj):
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            messages.error(request, _('Seu usuário não tem um Perfil de Usuário. Contate o administrador.'))
            return
        if not perfil.pode_trocar_cenario and not request.user.is_superuser:
            messages.error(request,
                           _('Seu usuário está bloqueado para trocar de cenário ativo. Contate o administrador.'))
            return
        if perfil.cenario_ativo_id == obj.id:
            messages.info(request, f'Cenário {obj.id}/{obj.cen_nome} já é o seu cenário ativo.')
            return
        perfil.cenario_ativo = obj
        perfil.save()
        messages.success(request, f'Cenário {obj.id}/{obj.cen_nome} agora é o seu cenário ativo.')

    ativar_cenario_action.label = _("Ativar Este Cenário")

    change_actions = ('ativar_cenario_action', 'update_fluxos', 'limpar_cenario', 'otimizar_cenario',
                      'status_otimizacao', 'consolidar_cenario', 'exportar_excel_cenario')

    def get_change_actions(self, request, object_id, form_url):
        actions = super(TbCenariosAdmin, self).get_change_actions(request, object_id, form_url)
        actions = list(actions)

        u = User.objects.get(username=request.user)
        if not u.has_perm('parameters.change_tbcenarios'):
            return []

        obj = self.get_object(request, object_id)
        if obj is not None and self.ativo(obj) and 'ativar_cenario_action' in actions:
            actions.remove('ativar_cenario_action')

        if obj is not None and not self.pode_trocar_cenario() and 'ativar_cenario_action' in actions:
            actions.remove('ativar_cenario_action')

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

    # 🌟 CORRIGIDO: "Últimas Alterações" só aparece no formulário quando o
    # cenário JÁ TEM alguma coisa registrada nesse campo (ver
    # parameters/signals.py) -- um cenário nunca alterado (ou recém
    # Limpo, que reseta esse campo) não mostra a linha vazia à toa. Quem
    # é de fato exibido é o MÉTODO ultimas_alteracoes_formatado (abaixo),
    # que transforma o texto bruto em HTML com link clicável pro
    # registro alterado, igual a "Ações recentes" do Django Admin.
    def get_fields(self, request, obj=None):
        if not obj:
            return self.fields + ('cen_copiar_de',)
        campos = self.fields + (('cen_tipo', 'status_cenario'), ('cen_inicio', 'cen_fim'), 'ativo')
        if obj.ultimas_alteracoes:
            campos = campos + ('ultimas_alteracoes_formatado',)
        return campos

    readonly_fields = ('cen_tipo', 'status_cenario', 'ativo', 'ultimas_alteracoes_formatado')

    # 🌟 NOVO: transforma o texto bruto de ultimas_alteracoes (uma linha
    # por alteração, terminando em " | <url>" quando o registro alterado
    # ainda existe e está registrado no Admin -- ver
    # parameters/signals.py) em HTML de verdade, com um link "VER
    # REGISTRO" clicável (maiúsculo, negrito, azul) no final de cada
    # linha -- igual à funcionalidade "Ações recentes" do próprio Django
    # Admin, pra dar acesso rápido ao registro que causou a alteração.
    def ultimas_alteracoes_formatado(self, obj):
        if not obj.ultimas_alteracoes:
            return ''
        partes = []
        for linha in obj.ultimas_alteracoes.split('\n'):
            if not linha.strip():
                continue
            if ' | ' in linha:
                texto, url = linha.rsplit(' | ', 1)
            else:
                texto, url = linha, None
            partes.append((texto, url))
        return format_html_join(
            format_html('<br>'),
            '{} {}',
            (
                (texto, format_html(
                    ' &raquo; <a href="{}" target="_blank" '
                    'style="font-weight: bold; color: #0056b3; text-transform: uppercase; text-decoration: underline;">{}</a>',
                    url, _('Ver registro')
                ) if url else '')
                for texto, url in partes
            )
        )
    ultimas_alteracoes_formatado.short_description = _('Últimas Alterações')

    # 🌟 CORRIGIDO (multi-empresa): antes checava id == 1/2/3 (só fazia
    # sentido pra uma empresa só). Agora usa a flag eh_cenario_base,
    # marcada nos 3 cenários criados automaticamente pra CADA empresa.
    # 🌟 CORRIGIDO: removida a referência a obj.cen_ativo -- esse campo
    # não existe mais no modelo (removido numa correção anterior); a
    # condição que dependia dele foi retirada junto.
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.eh_cenario_base:
            return self.readonly_fields + ('cen_nome',)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    inlines = [TbCenariosDaugtherAdmin, TbCenariosDaugther1Admin]


admin.site.register(TbCenarios, TbCenariosAdmin)


class TbEmpresaAdmin(admin.ModelAdmin):
    actions = ['delete_selected']
    fields = [('emp_nome', 'emp_tipo'), 'emp_descricao', 'emp_moeda', ('emp_moeda_imagem', 'emp_moeda_imagem_tag'),
              ('emp_logo', 'emp_logo_tag'), ('emp_fluxo', 'emp_fluxo_tag'), 'idioma_padrao', 'apps_habilitados',
              'acoes_comuns_habilitadas']
    filter_horizontal = ['apps_habilitados', 'acoes_comuns_habilitadas']
    readonly_fields = ['emp_moeda_imagem_tag', 'emp_logo_tag', 'emp_fluxo_tag']

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.append('apps_habilitados')
            readonly.append('acoes_comuns_habilitadas')
        return readonly

    list_display = ['id', 'ativo', 'botao_ativar', 'emp_nome', 'emp_logo', 'emp_logo_tag', 'emp_tipo', 'emp_descricao',
                    'emp_moeda',
                    'emp_moeda_imagem', 'emp_moeda_imagem_tag', 'emp_fluxo', 'emp_fluxo_tag']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None or perfil.empresa_id is None:
            return qs.none()
        return qs.filter(id=perfil.empresa_id)

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return ['emp_nome', 'emp_logo', 'emp_logo_tag', 'emp_tipo', 'emp_descricao', 'emp_moeda',
                'emp_moeda_imagem', 'emp_moeda_imagem_tag', 'emp_fluxo', 'emp_fluxo_tag']

    def get_list_display_links(self, request, list_display):
        if 'id' in list_display:
            return ['id', 'emp_nome']
        return ['emp_nome']

    def get_urls(self):
        urls_customizadas = [
            path('<int:empresa_id>/ativar-para-mim/',
                 self.admin_site.admin_view(self.ativar_para_mim),
                 name='parameters_tbempresa_ativar_para_mim'),
        ]
        return urls_customizadas + super().get_urls()

    def ativar_para_mim(self, request, empresa_id):
        if not request.user.is_superuser:
            messages.error(request, _('Só superusuários podem trocar de empresa ativa.'))
        else:
            perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)
            empresa = TbEmpresa.objects.get(id=empresa_id)
            perfil.lembrar_cenario_ativo_para_empresa_atual()
            perfil.empresa_ativa = empresa
            perfil.restaurar_cenario_ativo_para_empresa(empresa.id)
            perfil.save()

            if perfil.cenario_ativo_id:
                messages.success(
                    request,
                    f'Empresa {empresa.emp_nome} agora é a sua empresa ativa -- '
                    f'restaurei o cenário que estava ativo por lá da última vez.'
                )
            else:
                messages.success(
                    request,
                    f'Empresa {empresa.emp_nome} agora é a sua empresa ativa. '
                    'Escolha um cenário dela na tela de Cenários antes de continuar.'
                )
        return redirect('admin:parameters_tbempresa_changelist')

    def ativo(self, obj):
        usuario = get_usuario_atual()
        if usuario is None or not usuario.is_superuser:
            return False
        perfil = getattr(usuario, 'perfilusuario', None)
        return bool(perfil and perfil.empresa_ativa_id == obj.id)

    ativo.short_description = _('Ativa')
    ativo.boolean = True

    def botao_ativar(self, obj):
        usuario = get_usuario_atual()
        if usuario is None or not usuario.is_superuser or self.ativo(obj):
            return '—'
        url = reverse('admin:parameters_tbempresa_ativar_para_mim', args=[obj.id])
        return format_html(_('<a class="button" href="{}">Ativar</a>'), url)

    botao_ativar.short_description = _('Ação')

    def get_ordering(self, request):
        id_empresa_ativa = None
        usuario = get_usuario_atual()
        if usuario is not None and usuario.is_superuser:
            perfil = getattr(usuario, 'perfilusuario', None)
            if perfil is not None:
                id_empresa_ativa = perfil.empresa_ativa_id

        ordem_ativa = Case(
            When(id=id_empresa_ativa, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
        return (ordem_ativa, 'id')

    # 🌟 CORRIGIDO: a criação da empresa inicial saiu daqui (rodava na importação do admin, consultando o banco antes
    # de o Django terminar de iniciar). Agora fica em garantir_empresa_inicial(), no topo do arquivo, chamada depois
    # de cada "migrate" e aqui, ao abrir a lista de Empresas.
    def changelist_view(self, request, extra_context=None):
        garantir_empresa_inicial()
        return super().changelist_view(request, extra_context)

    def has_delete_permission(self, request, obj=None):
        return False

    def _empresas_em_uso_detalhes(self, lista_id):
        perfis_afetados = PerfilUsuario.objects.filter(
            Q(empresa_id__in=lista_id) | Q(empresa_ativa_id__in=lista_id)
        ).select_related('usuario', 'empresa', 'empresa_ativa')
        if not perfis_afetados.exists():
            return None
        return ", ".join(
            f"{p.empresa.emp_nome if p.empresa_id in lista_id else p.empresa_ativa.emp_nome} (em uso por {p.usuario})"
            for p in perfis_afetados
        )

    def delete_selected(self, request, queryset):
        if not request.user.is_superuser:
            messages.error(request, "Você não tem autorização para excluir empresas.")
            return

        if 'confirmar' in request.POST:
            ids_confirmados = [int(i) for i in request.POST.getlist('_selected_action')]
            detalhes = self._empresas_em_uso_detalhes(ids_confirmados)
            if detalhes:
                messages.error(request,
                               f'Empresa(s) passaram a estar em uso nesse meio tempo e não foram excluídas: {detalhes}')
                return
            for id_empresa in ids_confirmados:
                remover_empresa_celery.delay(id_empresa)
            messages.success(request,
                             "Empresa(s) confirmada(s) sendo excluída(s) em segundo plano. Para verificar o status da exclusão, dá um refresh na tela daqui a pouco.")
            return

        lista_id = list(queryset.values_list('id', flat=True))
        detalhes = self._empresas_em_uso_detalhes(lista_id)
        if detalhes:
            messages.error(request,
                           f'Empresa(s) selecionada(s) estão em uso por algum usuário e não podem ser excluídas: {detalhes}')
            return

        return render(request, 'admin/parameters/tbempresa/confirmar_exclusao.html', {
            'empresas': queryset,
            'title': 'Confirmar exclusão de empresa(s)',
            'opts': self.model._meta,
        })

    delete_selected.short_description = "Remover Empresa(s) Selecionada(s)"

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)


admin.site.register(TbEmpresa, TbEmpresaAdmin)


class AppOpcionalAdmin(admin.ModelAdmin):
    fields = ('app_label', 'nome_exibicao')
    list_display = ['nome_exibicao', 'app_label']

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.register(AppOpcional, AppOpcionalAdmin)


class AcaoComumAdmin(admin.ModelAdmin):
    fields = ('categoria', 'chave', 'nome_exibicao')
    list_display = ['nome_exibicao', 'categoria', 'chave']
    list_filter = ['categoria']

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.register(AcaoComum, AcaoComumAdmin)


class TbGlossarioAdmin(admin.ModelAdmin):
    fields = ('glo_nome', 'glo_descricao', ('glo_imagem', 'glo_imagem_tag'), 'glo_fonte')
    list_display = ['glo_nome', 'empresa', 'glo_descricao', 'glo_imagem', 'glo_imagem_tag', 'glo_fonte']
    readonly_fields = ['glo_imagem_tag']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            return qs.none()
        empresa_id = perfil.empresa_efetiva_id()
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)

    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 7, 'cols': 100})},
    }


admin.site.register(TbGlossario, TbGlossarioAdmin)


class DefinirCenarioAtivoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    cenario = forms.ModelChoiceField(
        queryset=TbCenarios.objects.none(), label=_('Cenário'), required=True
    )

    def __init__(self, *args, empresa_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa_id:
            self.fields['cenario'].queryset = TbCenarios.objects_real.filter(
                empresa_id=empresa_id
            ).order_by('numero_sequencial', 'id')


class CenarioAtivoListFilter(admin.SimpleListFilter):
    title = _('Cenário Ativo')
    parameter_name = 'cenario_ativo'

    def lookups(self, request, model_admin):
        cenarios = TbCenarios.objects_real.select_related('empresa')
        perfil_logado = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil_logado.empresa_efetiva_id() if perfil_logado else None

        if empresa_id:
            cenarios = cenarios.filter(empresa_id=empresa_id)

        cenarios = cenarios.order_by('empresa__emp_nome', 'numero_sequencial')

        return [
            (
                c.id,
                f"{c.empresa.emp_nome if c.empresa_id else '(sem empresa)'} - "
                f"{c.numero_sequencial if c.numero_sequencial is not None else c.id}/{c.cen_nome}"
            )
            for c in cenarios
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(cenario_ativo_id=self.value())
        return queryset


class PerfilUsuarioForm(forms.ModelForm):
    class Meta:
        model = PerfilUsuario
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'cenario_ativo' in self.fields:
            self.fields['cenario_ativo'].required = True
        if 'empresa' in self.fields:
            self.fields['empresa'].required = True


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    form = PerfilUsuarioForm
    list_display = ('usuario', 'empresa', 'eh_superuser_empresa', 'empresa_ativa', 'cenario_ativo',
                    'pode_trocar_cenario')
    list_editable = ('pode_trocar_cenario',)
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name')
    actions = ['definir_cenario_ativo_em_massa', 'bloquear_troca_cenario', 'desbloquear_troca_cenario']
    filter_horizontal = ('apps_restritos', 'acoes_comuns_restritas')
    list_filter = (CenarioAtivoListFilter, 'pode_trocar_cenario')

    campos_base = ('usuario', 'empresa', 'empresa_ativa', 'eh_superuser_empresa', 'idioma', 'cenario_ativo',
                   'pode_trocar_cenario', 'pode_acessar_agente_ia', 'apps_restritos', 'acoes_comuns_restritas')

    def get_fields(self, request, obj=None):
        campos = list(self.campos_base)
        eh_superuser_real = obj is not None and obj.usuario.is_superuser
        eh_superuser_empresa = obj is not None and obj.eh_superuser_empresa

        if eh_superuser_real:
            campos.remove('empresa')
            campos.remove('eh_superuser_empresa')
        else:
            campos.remove('empresa_ativa')

        if eh_superuser_real or eh_superuser_empresa:
            campos.remove('pode_trocar_cenario')
            campos.remove('pode_acessar_agente_ia')

        return campos

    class Media:
        js = ('admin/js/empresa_filtra_cenario.js',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'cenario_ativo':
            object_id = request.resolver_match.kwargs.get('object_id')
            perfil = PerfilUsuario.objects.filter(pk=object_id).first() if object_id else None

            empresa_id = None
            if perfil is not None:
                campo_empresa_relevante = 'empresa_ativa' if perfil.usuario.is_superuser else 'empresa'
                valor_submetido = request.POST.get(campo_empresa_relevante)
                if valor_submetido and valor_submetido.isdigit():
                    empresa_id = int(valor_submetido)
                else:
                    empresa_id = perfil.empresa_efetiva_id()

            if empresa_id:
                kwargs['queryset'] = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('-id')
            else:
                kwargs['queryset'] = TbCenarios.objects_real.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def bloquear_troca_cenario(self, request, queryset):
        total = queryset.update(pode_trocar_cenario=False)
        self.message_user(request, f'{total} usuário(s) bloqueado(s) para trocar de cenário ativo.')

    bloquear_troca_cenario.short_description = "Bloquear troca de cenário ativo (selecionados)"

    def desbloquear_troca_cenario(self, request, queryset):
        total = queryset.update(pode_trocar_cenario=True)
        self.message_user(request, f'{total} usuário(s) desbloqueado(s) para trocar de cenário ativo.')

    desbloquear_troca_cenario.short_description = "Desbloquear troca de cenário ativo (selecionados)"

    def definir_cenario_ativo_em_massa(self, request, queryset):
        perfil_logado = getattr(request.user, 'perfilusuario', None)
        empresa_filtrada_id = perfil_logado.empresa_efetiva_id() if perfil_logado else None

        form = None
        if 'aplicar' in request.POST:
            form = DefinirCenarioAtivoForm(request.POST, empresa_id=empresa_filtrada_id)
            if form.is_valid():
                cenario = form.cleaned_data['cenario']
                total = queryset.update(cenario_ativo=cenario)
                numero_exibido = cenario.numero_sequencial if cenario.numero_sequencial is not None else cenario.id
                self.message_user(
                    request,
                    f'{total} usuário(s) tiveram o cenário ativo definido para {numero_exibido}/{cenario.cen_nome}.'
                )
                return None

        if form is None:
            form = DefinirCenarioAtivoForm(
                initial={'_selected_action': queryset.values_list('pk', flat=True)},
                empresa_id=empresa_filtrada_id,
            )

        return render(request, 'admin/parameters/perfilusuario/definir_cenario_em_massa.html', {
            'perfis': queryset,
            'form': form,
            'title': 'Definir cenário ativo para os usuários selecionados',
            'opts': self.model._meta,
        })

    definir_cenario_ativo_em_massa.short_description = "Definir cenário ativo para os selecionados"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if '/auth/user/' in request.path:
            return True
        return False

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        perfil = getattr(request.user, 'perfilusuario', None)
        if not (perfil and perfil.eh_superuser_empresa and perfil.empresa_id):
            return False
        if obj is None:
            return True
        return obj.empresa_id == perfil.empresa_id

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        readonly.append('usuario')
        if not request.user.is_superuser:
            readonly.append('empresa')
            readonly.append('empresa_ativa')
        if not eh_superuser_ou_superuser_empresa(request.user):
            readonly.append('apps_restritos')
            readonly.append('acoes_comuns_restritas')
            readonly.append('pode_acessar_agente_ia')
        return readonly

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ('apps_restritos', 'acoes_comuns_restritas'):
            object_id = request.resolver_match.kwargs.get('object_id')
            perfil = PerfilUsuario.objects.filter(pk=object_id).first() if object_id else None
            empresa_id = perfil.empresa_efetiva_id() if perfil else None
            empresa = TbEmpresa.objects.filter(id=empresa_id).first() if empresa_id else None
            if db_field.name == 'apps_restritos':
                kwargs['queryset'] = empresa.apps_habilitados.all() if empresa else AppOpcional.objects.none()
            else:
                kwargs['queryset'] = empresa.acoes_comuns_habilitadas.all() if empresa else AcaoComum.objects.none()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)


class PerfilUsuarioInlineFormSet(BaseInlineFormSet):
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
    filter_horizontal = ('apps_restritos', 'acoes_comuns_restritas')
    campos_base = ('empresa', 'empresa_ativa', 'eh_superuser_empresa', 'idioma', 'cenario_ativo', 'pode_trocar_cenario',
                   'pode_acessar_agente_ia', 'apps_restritos', 'acoes_comuns_restritas')
    verbose_name_plural = 'Empresa e Cenário ativo'
    min_num = 1
    max_num = 1
    extra = 1
    validate_min = True

    def get_fields(self, request, obj=None):
        campos = list(self.campos_base)
        eh_superuser_real = obj is not None and obj.is_superuser
        perfil_relacionado = getattr(obj, 'perfilusuario', None) if obj is not None else None
        eh_superuser_empresa = bool(perfil_relacionado and perfil_relacionado.eh_superuser_empresa)

        if eh_superuser_real:
            campos.remove('empresa')
            campos.remove('eh_superuser_empresa')
        else:
            campos.remove('empresa_ativa')

        if eh_superuser_real or eh_superuser_empresa:
            campos.remove('pode_trocar_cenario')
            campos.remove('pode_acessar_agente_ia')

        return campos

    class Media:
        js = ('admin/js/empresa_filtra_cenario.js',)

    def has_view_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_add_permission(self, request, obj=None):
        return True

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.append('eh_superuser_empresa')
            readonly.append('empresa')
            readonly.append('empresa_ativa')
        if not eh_superuser_ou_superuser_empresa(request.user):
            readonly.append('apps_restritos')
            readonly.append('acoes_comuns_restritas')
            readonly.append('pode_acessar_agente_ia')
        eh_auto_edicao_comum = (
                obj is not None and obj.pk == request.user.pk
                and not eh_superuser_ou_superuser_empresa(request.user)
        )
        if eh_auto_edicao_comum:
            for campo in ('empresa', 'empresa_ativa', 'cenario_ativo', 'pode_trocar_cenario', 'pode_acessar_agente_ia'):
                if campo not in readonly:
                    readonly.append(campo)
        return readonly

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'cenario_ativo':
            object_id = request.resolver_match.kwargs.get('object_id')
            perfil = PerfilUsuario.objects.filter(usuario_id=object_id).first() if object_id else None

            empresa_id = None
            if perfil is not None:
                campo_empresa_relevante = 'empresa_ativa' if perfil.usuario.is_superuser else 'empresa'
                valor_submetido = request.POST.get(f'perfilusuario-0-{campo_empresa_relevante}')
                if valor_submetido and valor_submetido.isdigit():
                    empresa_id = int(valor_submetido)
                else:
                    empresa_id = perfil.empresa_efetiva_id()

            if empresa_id:
                kwargs['queryset'] = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('-id')
            else:
                kwargs['queryset'] = TbCenarios.objects_real.none()
        elif db_field.name == 'empresa' and not request.user.is_superuser:
            perfil = getattr(request.user, 'perfilusuario', None)
            if perfil and perfil.eh_superuser_empresa and perfil.empresa_id:
                kwargs['queryset'] = TbEmpresa.objects.filter(id=perfil.empresa_id)
                kwargs['initial'] = perfil.empresa_id
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ('apps_restritos', 'acoes_comuns_restritas'):
            object_id = request.resolver_match.kwargs.get('object_id')
            perfil = PerfilUsuario.objects.filter(usuario_id=object_id).first() if object_id else None
            empresa_id = perfil.empresa_efetiva_id() if perfil else None
            empresa = TbEmpresa.objects.filter(id=empresa_id).first() if empresa_id else None
            if db_field.name == 'apps_restritos':
                kwargs['queryset'] = empresa.apps_habilitados.all() if empresa else AppOpcional.objects.none()
            else:
                kwargs['queryset'] = empresa.acoes_comuns_habilitadas.all() if empresa else AcaoComum.objects.none()
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class EmpresaUsuarioListFilter(admin.SimpleListFilter):
    title = 'Empresa'
    parameter_name = 'empresa_do_usuario'

    def lookups(self, request, model_admin):
        return [(e.id, e.emp_nome) for e in TbEmpresa.objects.all().order_by('emp_nome')]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(perfilusuario__empresa_id=self.value())
        return queryset


class CustomUserAdmin(UserAdmin):
    inlines = (PerfilUsuarioInline,)

    def get_list_filter(self, request):
        padrao = ('is_staff', 'is_superuser', 'is_active', 'groups')
        if request.user.is_superuser:
            return (EmpresaUsuarioListFilter,) + padrao
        return padrao

    def _eh_superuser_empresa_nao_real(self, request):
        if request.user.is_superuser:
            return False
        perfil = getattr(request.user, 'perfilusuario', None)
        return bool(perfil and perfil.eh_superuser_empresa)

    def has_module_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def _eh_auto_edicao(self, request, obj):
        return obj is not None and obj.pk == request.user.pk

    def has_add_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_view_permission(self, request, obj=None):
        return self.has_change_permission(request, obj) or self.has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if self._eh_auto_edicao(request, obj):
            return True
        perfil = getattr(request.user, 'perfilusuario', None)
        if not (perfil and perfil.eh_superuser_empresa and perfil.empresa_id):
            return False
        if obj is None:
            return True
        perfil_obj = getattr(obj, 'perfilusuario', None)
        return bool(perfil_obj and perfil_obj.empresa_id == perfil.empresa_id)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if self._eh_auto_edicao(request, obj):
            return False
        perfil = getattr(request.user, 'perfilusuario', None)
        if not (perfil and perfil.eh_superuser_empresa and perfil.empresa_id):
            return False
        if obj is None:
            return True
        perfil_obj = getattr(obj, 'perfilusuario', None)
        return bool(perfil_obj and perfil_obj.empresa_id == perfil.empresa_id)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil and perfil.eh_superuser_empresa and perfil.empresa_id:
            return qs.filter(perfilusuario__empresa_id=perfil.empresa_id)
        return qs.none()

    # 🌟 NOVO (multi-empresa): grupos agora pertencem a uma empresa
    # (TbGrupoEmpresa) -- sem esse filtro, o campo "groups" do usuário
    # listava TODOS os grupos do sistema, de qualquer empresa.
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == 'groups':
            perfil = getattr(request.user, 'perfilusuario', None)
            empresa_id = perfil.empresa_efetiva_id() if perfil is not None else None
            if empresa_id is not None:
                kwargs['queryset'] = Group.objects.filter(empresa_vinculo__empresa_id=empresa_id)
            else:
                kwargs['queryset'] = Group.objects.none()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        eh_auto_edicao_comum = (
                not eh_superuser_ou_superuser_empresa(request.user)
                and self._eh_auto_edicao(request, obj)
        )
        if eh_auto_edicao_comum:
            campos_bloqueados = ('is_superuser', 'is_staff', 'is_active', 'groups', 'user_permissions', 'last_login',
                                 'date_joined')
        elif self._eh_superuser_empresa_nao_real(request):
            campos_bloqueados = ('is_superuser', 'user_permissions')
        else:
            campos_bloqueados = ()

        if campos_bloqueados:
            fieldsets = tuple(
                (nome, {**opcoes, 'fields': tuple(
                    campo for campo in opcoes['fields'] if campo not in campos_bloqueados
                )})
                for nome, opcoes in fieldsets
            )
        return fieldsets


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# 🌟 NOVO: inline pra escolher a empresa do grupo, direto na tela de
# edição do próprio Grupo -- TbGrupoEmpresa é OneToOne (um grupo só
# pode pertencer a UMA empresa), então max_num=1 e sem exclusão solta.
class TbGrupoEmpresaInline(admin.StackedInline):
    model = TbGrupoEmpresa
    max_num = 1
    can_delete = False
    verbose_name = _('Empresa')
    verbose_name_plural = _('Empresa')


class CustomGroupAdmin(GroupAdmin):
    def has_module_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_view_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_add_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_change_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_delete_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    # 🌟 NOVO (multi-empresa): grupos não são mais globais -- cada um
    # pertence a uma empresa (TbGrupoEmpresa). A listagem só mostra os
    # da empresa EFETIVA do usuário logado.
    inlines = [TbGrupoEmpresaInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil is not None else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_vinculo__empresa_id=empresa_id)

    # 🌟 NOVO: o campo de permissões (Group.permissions) lista, por
    # padrão, TODAS as permissões de TODOS os apps instalados --
    # inclusive apps opcionais que a empresa nem tem habilitado. Filtra
    # pra excluir só as permissões de apps que ESTÃO no catálogo de
    # apps opcionais (AppOpcional) mas NÃO estão habilitados pra essa
    # empresa -- apps que nem fazem parte desse catálogo (core do
    # Django, etc.) continuam aparecendo normalmente.
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == 'permissions':
            perfil = getattr(request.user, 'perfilusuario', None)
            empresa_id = perfil.empresa_efetiva_id() if perfil is not None else None
            todos_apps_opcionais = set(AppOpcional.objects.values_list('app_label', flat=True))
            if empresa_id is not None:
                apps_habilitados = set(
                    TbEmpresa.objects.get(id=empresa_id).apps_habilitados.values_list('app_label', flat=True)
                )
            else:
                apps_habilitados = set()
            apps_bloqueados = todos_apps_opcionais - apps_habilitados
            kwargs['queryset'] = Permission.objects.exclude(content_type__app_label__in=apps_bloqueados)
        return super().formfield_for_manytomany(db_field, request, **kwargs)


admin.site.unregister(Group)
admin.site.register(Group, CustomGroupAdmin)


@admin.register(AgenteConfig)
class AgenteConfigAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_editable = ('ativo',)
    exclude = ('empresa',)

    def has_module_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_view_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_add_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_change_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_delete_permission(self, request, obj=None):
        if not eh_superuser_ou_superuser_empresa(request.user):
            return False
        if obj is not None and obj.ativo:
            return False
        return True

    def delete_view(self, request, object_id, extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.ativo:
            self.message_user(
                request,
                f'O comportamento "{obj.nome}" está marcado como ativo agora e não pode ser '
                'excluído. Marque outro comportamento como ativo primeiro, depois volte pra '
                'excluir este.',
                level=messages.ERROR,
            )
            return redirect(reverse('admin:parameters_agenteconfig_changelist'))
        return super().delete_view(request, object_id, extra_context)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)


@admin.register(HistoricoAgente)
class HistoricoAgenteAdmin(admin.ModelAdmin):
    list_display = ('data', 'empresa', 'usuario', 'comando_usuario')
    readonly_fields = ('data', 'empresa', 'usuario', 'comando_usuario', 'resposta_ia')

    def get_list_filter(self, request):
        if eh_superuser_ou_superuser_empresa(request.user):
            return ('usuario',)
        return ()

    def has_module_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_view_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)


@admin.register(RelatorioPDF)
class RelatorioPDFAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'empresa', 'ativo', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('titulo',)

    def has_module_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_view_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_add_permission(self, request):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_change_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def has_delete_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)


_ORDEM_MENU_PARAMETERS = [
    'TbEmpresa',
    'TbCenarios',
    'TbGlossario',
    'AgenteConfig',
    'HistoricoAgente',
    'RelatorioPDF',
    'AcaoComum',
    'PerfilUsuario',
    'AppOpcional',
]

_get_app_list_original = admin.site.__class__.get_app_list


def _get_app_list_customizado(self, request, app_label=None):
    app_list = _get_app_list_original(self, request, app_label)
    for app in app_list:
        if app['app_label'] == 'parameters':
            def _posicao_no_menu(model_dict):
                try:
                    return _ORDEM_MENU_PARAMETERS.index(model_dict['object_name'])
                except ValueError:
                    return len(_ORDEM_MENU_PARAMETERS)

            app['models'].sort(key=_posicao_no_menu)
    return app_list


admin.site.get_app_list = types.MethodType(_get_app_list_customizado, admin.site)