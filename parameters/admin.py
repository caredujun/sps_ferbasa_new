import xlwt, time
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
    consolidar_cenario_celery, limpar_cenario_celery, remover_cenario_celery, atualizar_fluxos_celery

from django.http import HttpResponse
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.forms.models import BaseInlineFormSet
from django.urls import path, reverse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.db.models import Case, When, Value, IntegerField
from .contexto_usuario import get_usuario_atual, eh_superuser_ou_superuser_empresa


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
    # 🌟 CORRIGIDO (multi-empresa): 'id' trocado por 'numero_sequencial' --
    # o id real da tabela agora é compartilhado entre várias empresas e
    # não faz mais sentido como número visível; numero_sequencial é o
    # número certo, específico da empresa.
    list_display = ['numero_sequencial', 'ativo', 'botao_ativar', 'cen_nome', 'status_cenario', 'cen_descricao',
                    'cen_tipo',
                    'cen_inicio', 'cen_fim', 'cen_grupo']
    search_fields = ('cen_nome',)
    readonly_fields = ['status_cenario']
    list_display_links = ['numero_sequencial', 'cen_nome']
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
        # 🌟 Superusuário (de verdade ou de empresa) sempre pode trocar o
        # PRÓPRIO cenário ativo, mesmo que o campo pode_trocar_cenario do
        # perfil dele esteja desmarcado -- a trava é pensada pra
        # restringir usuários comuns, não pra travar quem administra a
        # própria trava.
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
        # 🌟 CORRIGIDO (multi-empresa): ordena por numero_sequencial (o
        # número visível, por empresa) em vez do id real da tabela.
        return (ordem_ativo, 'numero_sequencial')

    # 🌟 NOVO (multi-empresa, Parte 4): a listagem só mostra os cenários
    # da empresa EFETIVA do usuário logado (empresa fixa dele, ou a
    # empresa_ativa escolhida, se for superusuário). Sem isso, a lista
    # mistura cenários de todas as empresas.
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil is None:
            return qs.none()
        empresa_id = perfil.empresa_efetiva_id()
        if empresa_id is None:
            # Sem empresa efetiva definida (usuário comum sem empresa
            # cadastrada, ou superusuário que ainda não ativou nenhuma) --
            # não mostra nada, em vez de vazar tudo por engano.
            return qs.none()
        return qs.filter(empresa_id=empresa_id)

    # 🌟 NOVO: usado pelo dropdown dependente empresa -> cenário no
    # formulário de usuário (autocomplete do campo cenario_ativo). Quando
    # vem "empresa_id" explícito na busca, ignora a restrição normal de
    # get_queryset (que só mostra a empresa do usuário LOGADO) e busca
    # livremente na empresa que está sendo ESCOLHIDA pra pessoa sendo
    # editada -- que pode ser diferente da empresa do administrador.
    # 🌟 NOVO: endpoint JSON simples (sem passar pelo autocomplete/Select2
    # do Django) -- devolve os cenários de uma empresa, usado pelo JS que
    # popula o dropdown de cenario_ativo no formulário de usuário
    # (ver empresa_filtra_cenario.js). Só exige estar logado no Admin
    # (admin_view), sem restringir por "empresa do usuário logado" --
    # um administrador pode estar escolhendo o cenário de OUTRA pessoa,
    # numa empresa diferente da dele.
    def cenarios_por_empresa_json(self, request, empresa_id):
        from django.http import JsonResponse
        cenarios = TbCenarios.objects_real.filter(empresa_id=empresa_id).order_by('numero_sequencial', 'id')
        dados = [
            {'id': c.id, 'texto': f"{c.numero_sequencial if c.numero_sequencial is not None else c.id}/{c.cen_nome}"}
            for c in cenarios
        ]
        return JsonResponse({'cenarios': dados})

    def delete_selected(modeladmin, request, queryset):
        # 🌟 CORRIGIDO (multi-empresa): superuser de empresa também pode
        # excluir -- o queryset aqui já vem filtrado pela empresa dele
        # (get_queryset da listagem), então não tem risco de excluir
        # cenário de outra empresa mesmo com esse poder ampliado.
        # Também o cenário ativo não poderá ser removido
        if eh_superuser_ou_superuser_empresa(request.user):
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
                # 🌟 CORRIGIDO (multi-empresa): antes bloqueava ids fixos
                # 1/2/3 (só fazia sentido pra uma empresa). Agora bloqueia
                # qualquer cenário marcado como base, de qualquer empresa.
                tem_cenario_base_selecionado = TbCenarios.objects_real.filter(
                    id__in=lista_id, eh_cenario_base=True
                ).exists()
                if not tem_cenario_base_selecionado:
                    # Vamos excluir os cenários selecionados
                    for id_list in lista_id:
                        remover_cenario_celery.delay(id_list)
                        # remover_cenario_celery(id_list)
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
                solucao = str(_('Sim'))
            else:
                if row[22] == 0:  # Não encontrou solução
                    solucao = str(_('Não'))
                else:
                    if row[22] == 2:
                        solucao = str(_('Limpo'))
                    else:
                        if row[22] == 3:
                            solucao = str(_('Limpando'))
                        else:
                            solucao = str(_('Otimizando'))

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

    exportar_excel.short_description = _('Exportar Excel Cenário(s) Selecionado(s)')

    # Importa do cenário ativo
    # Usado para pegar os dados de um cenário mensal e lançar num cenário trimestral por exemplo
    # Copia as informações a nível de dau_order
    # Antes apaga todas as informações anteriormente cadastradas para copiar as informações do cenário ativo
    def importar_ativo(self, request, obj):
        # Vamos verificar se o cenário está ativo. Se está, não pode copiar dele mesmo
        if obj.id == TbCenarios.objects.get(cen_ativo=True).id:
            messages.error(request, _('Este cenário está ativo. Não pode importar dele mesmo!'))
        else:
            if request.POST.get('post'):
                messages.success(request,
                                 _('Importação do cenário ativo sendo feita em segundo plano. Favor aguardar...'))

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

    importar_ativo.label = _('Importar Ativo')

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
                solucao = str(_('Sim'))
            else:
                if row[22] == 0:  # Não encontrou solução
                    solucao = str(_('Não'))
                else:
                    if row[22] == 2:
                        solucao = str(_('Limpo'))
                    else:
                        if row[22] == 3:
                            solucao = str(_('Limpando'))
                        else:
                            solucao = str(_('Otimizando'))

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

    exportar_excel_cenario.label = _('Exportar Excel')

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
                             _('Atualização dos Fluxos de Produção sendo realizado em segundo plano. Favor aguardar!'))

        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    update_fluxos.label = _("Atualizar Fluxos")  # optional

    # Action limpar tabelas para novo cálculo da otimização
    def limpar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
            return
        if self.ativo(obj):

            # Temos que verificar se tem algum fluxo onde não foi calculado os custos variáveis
            if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=None, tbcenarios_id=obj.id,
                                                        mae_id__flu_pro_ativo=True).count() > 0:
                messages.error(request,
                               _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) SEM O CÁLCULO DOS CUSTOS VARIÁVEIS. FAVOR VERIFICAR!'))
            else:
                # Temos que verificar se tem algum fluxo com o I/O desatualizado
                if TbFluxoProducao.objects.filter(flu_pro_input_output_atualizado=False, tbcenarios_id=obj.id,
                                                  flu_pro_ativo=True).count() > 0:
                    messages.error(request,
                                   _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM I/O DESATUALIZADO(S). FAVOR VERIFICAR!'))
                else:
                    # Temos que verificar se tem algum fluxo com o custo variável zerado
                    if TbFluxoProducaoDaugther01.objects.filter(custo_variavel=0, tbcenarios_id=obj.id,
                                                                mae_id__flu_pro_ativo=True).count() > 0:
                        messages.error(request,
                                       _('TEMOS FLUXO(S) DE PRODUÇÃO ATIVO(S) COM CUSTO VARIÁVEL ZERADO. FAVOR VERIFICAR!'))
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
                                         _('LIMPEZA do cenário sendo realizada em segundo plano. Favor aguardar!'))

        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    limpar_cenario.label = _("Limpar")  # optional

    # Action otimizar cenário
    def otimizar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
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

                messages.success(request, _('OTIMIZAÇÃO do cenário sendo realizada em segundo plano. Favor aguardar!'))
            else:
                if obj.flag == 1:
                    messages.error(request, _('Cenário só pode ser LIMPO!'))
                else:
                    messages.error(request, _('Cenário só pode ser LIMPO ou CONSOLIDADO!'))
        else:
            messages.error(request, 'ESSE CENÁRIO (' + str(
                obj.id) + ') NÃO É O SEU CENÁRIO ATIVO. Operação não pode ser realizada!')

    otimizar_cenario.label = _("Otimizar")  # optional

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

    status_otimizacao.label = _("Status Otimização")  # optional

    # Action consolidar os resultados do cenário após otimização
    def consolidar_cenario(self, request, obj):
        if self.tem_fluxo_desatualizado(obj):
            messages.error(request,
                           _('Existem fluxos de produção com I/O desatualizado. Use "Atualizar Fluxos" antes.'))
            return
        if self.ativo(obj):
            if obj.flag == 3:  # 3 significa que o cenário foi otimizado.
                # Vamos alterar o flag do cenário para mostrar que está otimizando
                obj.flag = 6  # 6 significa que o cenário está sendo consolidado
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

    consolidar_cenario.label = _("Consolidar")  # optional

    # 🌟 NOVO: botão "Ativar Este Cenário" dentro do próprio registro,
    # reaproveitando a mesma lógica do botão da listagem.
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

    # 🌟 REMOVIDO (multi-empresa): o bloco que criava o grupo "AS IS" e os
    # 3 cenários base com id fixo (1, 2, 3), rodando uma única vez quando
    # o sistema sobia -- pensado pra uma única empresa. Substituído pelo
    # sinal post_save em parameters/models.py
    # (criar_estrutura_base_para_empresa_nova), que roda pra CADA empresa
    # nova criada, isolando os cenários base por empresa. Também não é
    # mais necessário o ajuste manual de sequência (ajustar_sequencia) --
    # sem mais ids fixos forçados, o autoincremento do banco cuida disso
    # sozinho.

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
        # 🌟 CORRIGIDO (multi-empresa): antes checava id == 1/2/3 (só fazia
        # sentido pra uma empresa só). Agora usa a flag eh_cenario_base,
        # marcada nos 3 cenários criados automaticamente pra CADA empresa.
        if obj:
            if obj.eh_cenario_base:
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

    # 🌟 CORRIGIDO (multi-empresa): superuser de empresa também pode
    # criar cenário (dentro da própria empresa, já garantido pelo save()
    # em models.py, que atribui a empresa efetiva do usuário logado).
    def has_add_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)

    inlines = [TbCenariosDaugtherAdmin, TbCenariosDaugther1Admin]


# registrando
admin.site.register(TbCenarios, TbCenariosAdmin)


class TbEmpresaAdmin(admin.ModelAdmin):
    # fields = [('emp_nome', 'emp_tipo'), 'emp_descricao', 'emp_moeda', ('emp_moeda_imagem', 'emp_moeda_imagem_tag'), ('emp_logo', 'emp_logo_tag'), ('emp_fluxo', 'emp_fluxo_tag'), ('emp_informacao1', 'emp_fonte1'), ('emp_informacao2', 'emp_fonte2'), ('emp_informacao3', 'emp_fonte3')]
    fields = [('emp_nome', 'emp_tipo'), 'emp_descricao', 'emp_moeda', ('emp_moeda_imagem', 'emp_moeda_imagem_tag'),
              ('emp_logo', 'emp_logo_tag'), ('emp_fluxo', 'emp_fluxo_tag'), 'idioma_padrao', 'apps_habilitados',
              'acoes_comuns_habilitadas']
    # 🌟 NOVO (multi-empresa): apps opcionais habilitados pra essa
    # empresa (ex: custo_ferbasa só marcado pra Ferbasa) -- widget de
    # múltipla escolha lado a lado, só um superusuário DE VERDADE edita
    # (has_change_permission dessa tela já é restrito a is_superuser).
    # 🌟 NOVO (Ações Comuns por empresa): mesmo widget, agora também pras
    # ações individuais do menu "Ações Comuns" do chat (categoria+ação).
    filter_horizontal = ['apps_habilitados', 'acoes_comuns_habilitadas']

    readonly_fields = ['emp_moeda_imagem_tag', 'emp_logo_tag', 'emp_fluxo_tag']

    # 🌟 NOVO (multi-empresa): só um superusuário DE VERDADE decide quais
    # apps opcionais uma empresa tem acesso -- mesmo raciocínio de
    # eh_superuser_empresa (não faz sentido a própria empresa se
    # autoconceder acesso a um app novo). Vale igual pras Ações Comuns.
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.append('apps_habilitados')
            readonly.append('acoes_comuns_habilitadas')
        return readonly

    list_display = ['id', 'ativo', 'botao_ativar', 'emp_nome', 'emp_logo', 'emp_logo_tag', 'emp_tipo', 'emp_descricao',
                    'emp_moeda',
                    'emp_moeda_imagem', 'emp_moeda_imagem_tag', 'emp_fluxo', 'emp_fluxo_tag']

    # 🌟 NOVO (multi-empresa): usuário comum só vê a PRÓPRIA empresa na
    # listagem -- e sem as colunas id/Ativa/Ação, que só fazem sentido
    # pra superusuário escolhendo entre várias.
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

    # 🌟 NOVO: o nome da empresa também vira link clicável pra abrir o
    # registro -- antes só o "id" (superuser) funcionava assim. Adapta
    # aos dois conjuntos de colunas (get_list_display acima), já que
    # usuário comum nem tem a coluna "id" na lista dele.
    def get_list_display_links(self, request, list_display):
        if 'id' in list_display:
            return ['id', 'emp_nome']
        return ['emp_nome']

    # 🌟 NOVO (multi-empresa, Parte 4): botão "Ativar" por linha, mesmo
    # padrão já usado em Cenários -- só que aqui é só pra SUPERUSUÁRIO
    # (usuário comum tem empresa fixa, não escolhe/troca).
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

            # 🌟 CORRIGIDO: em vez de sempre zerar o cenário ativo, guarda
            # o cenário atual como "o da empresa que está saindo" e tenta
            # restaurar o que estava ativo da última vez nessa empresa
            # nova -- só fica sem cenário (forçando escolher) se essa
            # empresa nunca foi visitada antes, ou se o cenário salvo não
            # existe mais.
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

    # Cria empresa se não existir
    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar coluna
        # nova (acontece durante makemigrations de uma migration ainda
        # não aplicada).
        try:
            # Se existe, verificamos se tem pelo menos um registro. Se não tem registro, criamos um para ser ajustado posteriormente.
            qtde = TbEmpresa.objects.count()
            if qtde == 0:
                emp = TbEmpresa.objects.create(emp_nome='Favor alterar', emp_descricao='Favor alterar', emp_moeda='BRL')
        except Exception:
            pass

    # Tabela Empresa só pode ser modificada pelo administrador. Não pode ser deletada ou receber mais dados.

    # Tabela tipo parâmetro.
    # Prevent deletion from admin portal
    def has_delete_permission(self, request, obj=None):
        return False

    # Vamos ver se usuário é superuser. Se sim, permite adição na tabela.
    def has_add_permission(self, request, obj=None):
        # 🌟 CORRIGIDO (multi-empresa): antes só permitia criar empresa se
        # a tabela estivesse VAZIA (qtde == 0) -- exatamente o oposto do
        # que multi-empresa precisa. Agora qualquer superusuário pode
        # criar quantas empresas quiser, a qualquer momento.
        return request.user.is_superuser

    # 🌟 CORRIGIDO (multi-empresa): superuser de empresa pode editar a
    # PRÓPRIA empresa -- o get_queryset acima já garante que ele só
    # enxerga o próprio registro, então esse poder ampliado nunca dá
    # acesso ao registro de outra empresa.
    def has_change_permission(self, request, obj=None):
        return eh_superuser_ou_superuser_empresa(request.user)


# registrando
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
    # 🌟 NOVO (multi-empresa): mostra a empresa na listagem -- antes o
    # campo nem aparecia em lugar nenhum, deixando o usuário sem
    # visibilidade de qual empresa um registro pertence.
    list_display = ['glo_nome', 'empresa', 'glo_descricao', 'glo_imagem', 'glo_imagem_tag', 'glo_fonte']

    readonly_fields = ['glo_imagem_tag']

    # 🌟 NOVO (multi-empresa): filtra a listagem pela empresa efetiva do
    # usuário logado -- mesmo padrão já usado em TbCenariosAdmin. Sem
    # isso, a listagem mostrava registros de TODAS as empresas
    # misturados.
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
        # models.CharField: {'widget': TextInput(attrs={'size': '15'})},
        models.TextField: {'widget': Textarea(attrs={'rows': 7, 'cols': 100})},
    }


admin.site.register(TbGlossario, TbGlossarioAdmin)


# Cenário ativo por usuário
class DefinirCenarioAtivoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    cenario = forms.ModelChoiceField(
        queryset=TbCenarios.objects.order_by('id'), label=_('Cenário'), required=True
    )


class PerfilUsuarioForm(forms.ModelForm):
    """
    🌟 NOVO: torna 'cenario_ativo' E 'empresa' obrigatórios no formulário
    do Admin -- tanto ao criar um usuário novo (via PerfilUsuarioInline)
    quanto ao editar um perfil existente (via PerfilUsuarioAdmin). Isso
    evita o problema na raiz: sem isso, dava pra criar/deixar um usuário
    sem cenário ativo ou sem empresa definidos, e ele só descobria isso
    depois, tentando usar o sistema e sendo redirecionado com um aviso
    (ou, no caso de empresa, simplesmente não vendo nada em lugar nenhum).

    'empresa' fica obrigatória mesmo pro perfil de um superusuário -- ele
    usa 'empresa_ativa' pra trabalhar de verdade (trocável a qualquer
    momento), mas ainda assim precisa de um valor inicial aqui, pelo
    mesmo motivo que 'cenario_ativo' já era obrigatório pra ele antes.

    Propositalmente NÃO mudamos isso no model (PerfilUsuario.cenario_ativo
    e .empresa continuam null=True, blank=True lá) -- só aqui, no
    formulário. Mudar no model tornaria obrigatório em QUALQUER contexto,
    inclusive no get_or_create do signal que cria o perfil vazio
    automaticamente (criar_perfil_usuario), o que quebraria esse
    mecanismo.
    """

    class Meta:
        model = PerfilUsuario
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cenario_ativo'].required = True
        self.fields['empresa'].required = True


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    form = PerfilUsuarioForm
    list_display = ('usuario', 'empresa', 'eh_superuser_empresa', 'empresa_ativa', 'cenario_ativo',
                    'pode_trocar_cenario')
    list_editable = ('pode_trocar_cenario',)
    list_filter = ('empresa', 'cenario_ativo', 'pode_trocar_cenario')
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name')
    # 🌟 CORRIGIDO: autocomplete tirado de 'cenario_ativo' -- a tentativa
    # de filtrar via hack no Select2 (injetar empresa_id na busca AJAX)
    # não funcionou de forma confiável. Volta a ser um select comum,
    # populado via JS puro (fetch + <option>), sem depender de API
    # interna nenhuma -- ver Media abaixo.
    autocomplete_fields = ('usuario',)
    actions = ['definir_cenario_ativo_em_massa', 'bloquear_troca_cenario', 'desbloquear_troca_cenario']

    class Media:
        js = ('admin/js/empresa_filtra_cenario.js',)

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

    # 🌟 NOVO (multi-empresa): superuser de empresa só vê/edita os
    # perfis DA PRÓPRIA EMPRESA -- mesmo padrão já aplicado em
    # CustomUserAdmin.
    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        perfil = getattr(request.user, 'perfilusuario', None)
        if not (perfil and perfil.eh_superuser_empresa and perfil.empresa_id):
            return False
        if obj is None:
            return True
        return obj.empresa_id == perfil.empresa_id

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil and perfil.eh_superuser_empresa and perfil.empresa_id:
            return qs.filter(empresa_id=perfil.empresa_id)
        return qs.none()


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
    fields = ('empresa', 'empresa_ativa', 'eh_superuser_empresa', 'idioma', 'cenario_ativo', 'pode_trocar_cenario')
    # 🌟 CORRIGIDO: autocomplete tirado -- a tentativa de filtrar via hack
    # no Select2 não funcionou de forma confiável. cenario_ativo volta a
    # ser um select comum, populado via JS puro (fetch + <option>), sem
    # depender de API interna nenhuma -- ver Media abaixo.
    verbose_name_plural = 'Empresa e Cenário ativo'
    # 🌟 NOVO: sem isso, o Django pode simplesmente IGNORAR um formulário
    # de inline deixado totalmente em branco (comportamento padrão de
    # formset, achando que é "opcional"), sem nem chegar a checar se
    # cenario_ativo é obrigatório. min_num=1 + validate_min=True força
    # a existir pelo menos 1 formulário de fato preenchido e validado.
    min_num = 1
    max_num = 1
    extra = 1
    validate_min = True

    class Media:
        js = ('admin/js/empresa_filtra_cenario.js',)

    # 🌟 NOVO (multi-empresa): o StackedInline tem a PRÓPRIA checagem de
    # permissão (separada da tela de Usuário) -- por padrão, checa se o
    # usuário tem permissão Django pro model PerfilUsuario, que um
    # superuser de empresa não tem nenhuma concedida. Sem isso, a seção
    # inteira ("Empresa e Cenário ativo") simplesmente não aparecia pra
    # ele, mesmo já podendo editar o Usuário em si. Delega pra mesma
    # regra já aplicada em CustomUserAdmin (o pai) -- quem pode editar o
    # Usuário também pode editar o perfil dele.
    def has_view_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_add_permission(self, request, obj=None):
        return True

    # 🌟 NOVO (multi-empresa): só um superusuário DE VERDADE pode
    # conceder/revogar "superuser de empresa" -- um superuser de empresa
    # não pode fazer isso nem pra si mesmo nem pra ninguém (evita
    # escalonamento de privilégio).
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.append('eh_superuser_empresa')
        # 🌟 NOVO (multi-idioma, Fase 1): usuário comum editando o
        # PRÓPRIO perfil só pode mexer no idioma -- empresa, cenário
        # ativo etc. continuam travados (ele já tem telas dedicadas pra
        # trocar cenário; a empresa dele não é algo que ele mesmo decide).
        eh_auto_edicao_comum = (
                obj is not None and obj.pk == request.user.pk
                and not eh_superuser_ou_superuser_empresa(request.user)
        )
        if eh_auto_edicao_comum:
            for campo in ('empresa', 'empresa_ativa', 'cenario_ativo', 'pode_trocar_cenario'):
                if campo not in readonly:
                    readonly.append(campo)
        return readonly

    # 🌟 NOVO: superuser de empresa só pode vincular usuários novos (ou
    # existentes) à PRÓPRIA empresa -- trava o dropdown pra mostrar só
    # ela, sem opção de escolher outra.
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'empresa' and not request.user.is_superuser:
            perfil = getattr(request.user, 'perfilusuario', None)
            if perfil and perfil.eh_superuser_empresa and perfil.empresa_id:
                kwargs['queryset'] = TbEmpresa.objects.filter(id=perfil.empresa_id)
                kwargs['initial'] = perfil.empresa_id
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class CustomUserAdmin(UserAdmin):
    inlines = (PerfilUsuarioInline,)

    def _eh_superuser_empresa_nao_real(self, request):
        if request.user.is_superuser:
            return False
        perfil = getattr(request.user, 'perfilusuario', None)
        return bool(perfil and perfil.eh_superuser_empresa)

    # 🌟 NOVO (multi-idioma, Fase 1): qualquer usuário -- mesmo comum,
    # sem ser superuser de nenhum tipo -- pode abrir e editar o PRÓPRIO
    # registro (usado principalmente pra trocar o próprio idioma, mas
    # também soma-o="troco minha senha", etc.). get_fieldsets abaixo
    # restringe o que aparece nesse caso, pra ele não poder se
    # auto-conceder permissões.
    def _eh_auto_edicao(self, request, obj):
        return obj is not None and obj.pk == request.user.pk

    # 🌟 NOVO (multi-empresa): superuser de empresa pode gerenciar
    # usuários -- criar, editar, excluir -- mas só os DA PRÓPRIA EMPRESA.
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
        # 🌟 CORRIGIDO: auto-edição NÃO inclui poder se auto-excluir --
        # aqui sempre cai na regra normal (superuser/superuser de
        # empresa), nunca no atalho de _eh_auto_edicao.
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
        # 🌟 NOVO: usuário comum não vê a LISTAGEM de ninguém (nem ele
        # mesmo) -- mas ainda assim consegue abrir o PRÓPRIO registro
        # direto pela URL, já que has_change_permission libera pra esse
        # caso específico (Django não passa pelo get_queryset da
        # changelist pra resolver o objeto de uma URL de edição direta).
        return qs.none()

    # 🌟 NOVO: superuser de empresa não vê (nem pode mexer em)
    # is_superuser/grupos/permissões Django de ninguém -- evita ele criar
    # outro superuser de verdade ou se auto-conceder permissões extras.
    # Usuário comum editando o PRÓPRIO registro vê um conjunto ainda mais
    # restrito -- só o essencial (nome, email, senha), nunca permissões.
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
            campos_bloqueados = ('is_superuser', 'groups', 'user_permissions')
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


# Agente de IA
@admin.register(AgenteConfig)
class AgenteConfigAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_editable = ('ativo',)  # 🌟 Permite marcar/desmarcar direto na listagem, sem abrir o registro

    # 🌟 NOVO (multi-empresa): sem isso, a tela nem aparecia no menu do
    # Admin pra quem não fosse superusuário de verdade (Django checa
    # permissão própria pra cada model, e superuser de empresa não tem
    # nenhuma concedida por padrão).
    # ⚠️ Nota: AgenteConfig ainda NÃO tem campo empresa (fica pra Parte 5)
    # -- por enquanto, qualquer superuser de empresa vê/edita a MESMA
    # configuração, compartilhada com todo mundo.
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


@admin.register(HistoricoAgente)
class HistoricoAgenteAdmin(admin.ModelAdmin):
    list_display = ('data', 'usuario', 'comando_usuario')
    readonly_fields = ('data', 'usuario', 'comando_usuario', 'resposta_ia')  # Evita edição dos logs

    def get_list_filter(self, request):
        # Filtro por usuário só faz sentido pra quem vê o histórico de todo mundo
        if eh_superuser_ou_superuser_empresa(request.user):
            return ('usuario',)
        return ()

    # 🌟 NOVO (multi-empresa): libera a tela aparecer no menu pro
    # superuser de empresa também.
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
        if request.user.is_superuser:
            return qs
        perfil = getattr(request.user, 'perfilusuario', None)
        if perfil and perfil.eh_superuser_empresa and perfil.empresa_id:
            # 🌟 NOVO: superuser de empresa vê o histórico de TODOS os
            # usuários da própria empresa, não só o dele mesmo.
            return qs.filter(usuario__perfilusuario__empresa_id=perfil.empresa_id)
        return qs.filter(usuario=request.user)


@admin.register(RelatorioPDF)
class RelatorioPDFAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'empresa', 'ativo', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('titulo',)  # Removed texto_extraido from here

    # 🌟 NOVO (multi-empresa): libera a tela aparecer no menu pro
    # superuser de empresa também.
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

    # 🌟 NOVO (multi-empresa): filtra a listagem pela empresa EFETIVA do
    # usuário logado -- mesmo padrão de TbCenariosAdmin. Vale até pro
    # superuser real: ele só vê os relatórios da empresa que está ativa
    # pra ele no momento, não de todas ao mesmo tempo.
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        perfil = getattr(request.user, 'perfilusuario', None)
        empresa_id = perfil.empresa_efetiva_id() if perfil else None
        if empresa_id is None:
            return qs.none()
        return qs.filter(empresa_id=empresa_id)