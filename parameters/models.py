from django.db import models
from django.utils.safestring import mark_safe
from django.db import connection, transaction # para ter acesso as tabelas e stored procedures do banco de dados e executar celery task após commit
from django.core.exceptions import ValidationError

from admin_interface.models import Theme
from django.conf import settings  # para referenciar o modelo de usuário do projeto

import locale
from django.conf import settings
from .contexto_usuario import get_usuario_atual

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  #  Estou usando esse pois Heroku não aceita pt_BR

from pypdf import PdfReader  # 🌟 Biblioteca oficial para extração de texto
import os

class TbEmpresa(models.Model):

    emp_tipo_choice = (
                      ('G', 'Greenfield'),
                      ('B', 'Brownfield')
                      )

    #Choices na forma de classe. Assim podemos montar em tempo de execução
    class EmpMoedaChoice(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    emp_nome = models.CharField(max_length=50, null=False, blank=False, verbose_name='Nome')
    emp_tipo = models.CharField(max_length=10, choices=emp_tipo_choice, null=False, blank=False, verbose_name='Tipo')
    emp_descricao = models.TextField(null=True, blank=False, verbose_name='Descrição')
    emp_moeda = models.CharField(max_length=3, choices=EmpMoedaChoice.choices, null=False, blank=False, default='BRL', verbose_name='Moeda')
    emp_moeda_imagem = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name='Imagem da Moeda')
    emp_logo = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name='Logo')
    emp_fluxo = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name='Fluxo de Produção')

    def __str__(self):
        return self.emp_nome

    def emp_moeda_imagem_tag(self):
        if self.emp_moeda_imagem:
            return mark_safe('<img src="%s" style="width: 270px; height:90px;" />' % self.emp_moeda_imagem.url)
        else:
            return 'Sem imagem!'

    emp_moeda_imagem_tag.short_description = ''
    emp_moeda_imagem_tag.allow_tags = True

    def emp_logo_tag(self):
        all_tables = connection.introspection.table_names()
        if 'parameters_tbempresa' in all_tables:
            if self.emp_logo:
                return mark_safe('<img src = "%s" style="width: 90px; height:90px;" />' % self.emp_logo.url)
            else:
                return 'Sem imagem!'

    emp_logo_tag.allow_tags = True
    emp_logo_tag.short_description = ''

    def emp_fluxo_tag(self):
        all_tables = connection.introspection.table_names()
        if 'parameters_tbempresa' in all_tables:
            if self.emp_fluxo:
                return mark_safe('<img src="%s" style="width: 600px; height:130px;" />' % self.emp_fluxo.url)
            else:
                return 'Sem imagem!'

    emp_fluxo_tag.short_description = ''
    emp_fluxo_tag.allow_tags = True

    class Meta:
        verbose_name        = '    Empresa'
        verbose_name_plural = '    Empresa'

class TbCenariosManager(models.Manager):
    """
    Intercepta buscas por 'cen_ativo=True' (ou 'cen_ativo=1') e as traduz
    para 'id=<cenário ativo do usuário atual>', resolvido via
    PerfilUsuario. Isso permite que TODO o código existente que já escreve

        TbCenarios.objects.get(cen_ativo=True)
        TbCenarios.objects.filter(cen_ativo=True)

    continue funcionando exatamente como está (~900 ocorrências no projeto
    inteiro) -- sem precisar editar cada uma -- só que agora resolvendo
    por usuário, não mais por um único flag global no banco.

    Se não houver usuário no contexto atual (ex: chamado de dentro de uma
    Celery task, de um shell do Django, ou de um teste sem o middleware
    ativo), cai de volta no comportamento antigo (busca real por
    cen_ativo=True no banco) -- assim nada quebra em código que ainda não
    passou pela migração para "cenário ativo por usuário".

    🌟 NOVO: se HÁ um usuário real no contexto, mas ele ainda não escolheu
    um cenário ativo no perfil, NÃO cai mais no fallback global em
    silêncio -- levanta CenarioAtivoNaoDefinidoError. Antes, um usuário
    sem cenário definido acabava vendo (ou operando sobre) o cenário
    ativo global, que pode ser de outra pessoa -- um erro silencioso que
    pode gerar resultado errado sem nenhum aviso. Preferimos falhar alto
    e claro a mostrar dado errado.
    """

    def _resolver_kwargs(self, kwargs):
        valor = kwargs.get('cen_ativo')
        if valor not in (True, 1):
            return kwargs

        usuario = get_usuario_atual()

        if usuario is not None:
            perfil = getattr(usuario, 'perfilusuario', None)
            if perfil is not None and perfil.cenario_ativo_id is not None:
                novos_kwargs = dict(kwargs)
                novos_kwargs.pop('cen_ativo')
                novos_kwargs['id'] = perfil.cenario_ativo_id
                return novos_kwargs

            # Há um usuário real no contexto, mas sem cenário ativo
            # escolhido no perfil -- levanta erro claro em vez de cair
            # silenciosamente no cenário ativo global.
            from .contexto_usuario import CenarioAtivoNaoDefinidoError
            raise CenarioAtivoNaoDefinidoError(
                f"O usuário '{usuario}' ainda não escolheu um cenário ativo. "
                "Acesse a tela de Cenários e clique em 'Ativar' no cenário desejado."
            )

        # Sem usuário no contexto (Celery, shell, comando de management,
        # teste sem o middleware ativo, etc.): mantém o comportamento
        # antigo (fallback), não muda os kwargs -- a query original
        # 'cen_ativo=True' segue em frente.
        return kwargs

    def get(self, *args, **kwargs):
        return super().get(*args, **self._resolver_kwargs(kwargs))

    def filter(self, *args, **kwargs):
        return super().filter(*args, **self._resolver_kwargs(kwargs))

class TbCenarios(models.Model):

    cen_tipo_choice = (
                      ('Mensal', 'Mensal'),
                      ('Trimestral', 'Trimestral'),
                      ('Anual', 'Anual')
                      )

    cen_nome = models.CharField(max_length=150, null=False, blank=False, verbose_name='Nome', unique=True)
    cen_descricao = models.TextField(null=True, blank=False, verbose_name='Descrição')
    cen_tipo = models.CharField(max_length=10, choices=cen_tipo_choice, null=False, blank=False, verbose_name='Tipo')
    cen_inicio = models.CharField(max_length=7, null=False, blank=False, verbose_name='Início', default='2021/01')
    cen_fim = models.CharField(max_length=7, null=False, blank=False, verbose_name='Fim', default='2021/12')
    cen_ativo = models.BooleanField(blank=False, null=False, default=False, verbose_name='Ativo')
    objects = TbCenariosManager()
    objects_real = models.Manager()  # 🌟 NOVO — Manager sem interceptação, só pra lógica
    # interna de invariante (save() abaixo). Nunca use
    # este fora daqui — o resto do sistema deve continuar
    # usando "objects" (por usuário).
    cen_grupo = models.ForeignKey('tabelas.TbGrupoCenarios', blank=True, null=True, on_delete=models.CASCADE, verbose_name='Grupo')
    cen_copiar_de = models.ForeignKey('self', default=1, on_delete=models.SET_DEFAULT, verbose_name='Copiar de ')
    flag = models.IntegerField(blank=True, null=True, verbose_name='Controle')

    def __str__(self):
        return "Cenário " + str(self.pk) + '/' + self.cen_nome

    def clean(self):
        self.cen_nome = self.cen_nome.upper()

        if self.cen_fim < self.cen_inicio:
            tipo = self.cen_tipo
            if tipo == 'Anual':
                raise ValidationError('Campo "Ano Fim" deve ser maior ou igual campo "Ano Início"')
            if tipo == 'Mensal':
                raise ValidationError('Campo "Ano/Mês Fim" deve ser maior ou igual campo "Ano/Mes Início"')
            if tipo == 'Trimestral':
                raise ValidationError('Campo "Ano/Trimestre Fim" deve ser maior ou igual campo "Ano/Trimestre Início"')

        if self.cen_tipo == 'Mensal':
            mes_inicio = int(self.cen_inicio[5:])
            if mes_inicio < 1 or mes_inicio > 12:
                raise ValidationError('Mês informado no Campo "Ano/Mês Início" deve ficar no intervalo 1/12')

            mes_fim = int(self.cen_fim[5:])
            if mes_fim < 1 or mes_fim > 12:
                raise ValidationError('Mês informado no Campo "Ano/Mês Fim" deve ficar no intervalo 1/12')

        if self.cen_tipo == 'Trimestral':
            trimestre_inicio = int(self.cen_inicio[5:])
            if trimestre_inicio < 1 or trimestre_inicio > 4:
                raise ValidationError('Trimestre informado no Campo "Ano/Trimestre Início" deve ficar no intervalo 01/04')

            trimestre_fim = int(self.cen_fim[5:])
            if trimestre_fim < 1 or trimestre_fim > 4:
                raise ValidationError('Trimestre informado no Campo "Ano/Trimestre Fim" deve ficar no intervalo 01/04')

    class Meta:
        verbose_name        = '   Cenário'
        verbose_name_plural = '   Cenários'
        ordering = ['id']  # 🌟 SIMPLIFICADO: antes era ['-cen_ativo', '-cen_grupo', 'id'] --
        # "-cen_ativo" não faz mais sentido sem um único cenário "ativo" global de
        # referência (agora é por usuário). TbCenariosAdmin já tem seu próprio
        # get_ordering (cenário ativo do usuário no topo) -- isso aqui só afeta
        # quem usa TbCenarios.objects.all() sem especificar ordenação.

    def status_cenario(self):
        '''
        1 = CONSOLIDADO
        2 = LIMPO
        3 = OTIMIZADO
        4 = OTIMIZANDO
        5 = LIMPANDO
        6 = CONSOLIDANDO
        7 = UPDATING FLUXOS
        8 = FLUXOS ATUALIZADOS
        9 = TEM FLUXO(S) ATIVO(S) COM ERRO
        OUTRO = NÃO DEFINIDO
        '''

        if self.flag == 1:
            return 'CONSOLIDADO'
        else:
            if self.flag == 2:
                return 'LIMPO'
            else:
                if self.flag == 3:
                    return 'OTIMIZADO'
                else:
                    if self.flag == 4:
                        return 'OTIMIZANDO. AGUARDE ...'
                    else:
                        if self.flag == 5:
                            return 'LIMPANDO. AGUARDE ...'
                        else:
                            if self.flag == 6:
                                return 'CONSOLIDANDO. AGUARDE ...'
                            else:
                                if self.flag == 7:
                                    return 'ATUALIZANDO FLUXOS. AGUARDE ...'
                                else:
                                    if self.flag == 8:
                                        return 'FLUXOS ATUALIZADOS'
                                    else:
                                        if self.flag == 9:
                                            return 'TEM FLUXO(S) ATIVO(S) COM ERRO'
                                        else:
                                            return 'NÃO DEFINIDO'

    status_cenario.short_description = 'Status'

    def save(self, *args, **kwargs):
        if self.pk is None:
            status = 'Adicionando'
        else:
            status = 'Modificando'

        if self.cen_ativo:
            qs = type(self).objects_real.filter(cen_ativo=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            qs.update(cen_ativo=False)

        if self.cen_ativo == False:
            if TbCenarios.objects_real.filter(cen_ativo=True).count() == 1:
                if self.pk == TbCenarios.objects_real.get(cen_ativo=True).id:
                    self.cen_ativo = True

        if status == 'Adicionando':
            cen_tipo = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_tipo
            cen_inicio = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_inicio
            cen_fim = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_fim
            flag = 0
            self.cen_tipo = cen_tipo
            self.cen_inicio = cen_inicio
            self.cen_fim = cen_fim
            self.flag = flag

        if status == 'Modificando':
            if self.cen_inicio != TbCenarios.objects.get(id=self.pk).cen_inicio or self.cen_fim != TbCenarios.objects.get(id=self.pk).cen_fim:
                periodo_mudou = True
            else:
                periodo_mudou = False

        super(TbCenarios, self).save(*args, **kwargs)

        # 🌟 REMOVIDO: bloco que gravava "Cenário Ativo: X/Nome" direto no
        # campo admin_interface_theme.title. Esse mecanismo foi substituído
        # pelo context_processor cenario_ativo_usuario (parameters/
        # context_processors.py), que calcula o texto do cabeçalho na hora,
        # por usuário -- esse UPDATE ficou gravando um valor que ninguém
        # mais lê, a cada save() de qualquer cenário. Confirmado com o
        # usuário que o campo title/title_visible do tema não é usado em
        # nenhum outro lugar antes de remover.

        if status == 'Adicionando':
            lista_tabela = ('tabelas_tbindicadores',
                            'tabelas_tbcambio',
                            'tabelas_tbimpostorenda',
                            'tabelas_tbtaxadesconto',
                            'tabelas_tbcustofixo',
                            'tabelas_tbdepreamorti',
                            'tabelas_tbcapex',
                            'tabelas_tbcustoitempreco',
                            'equipamentos_tbequipamentoscadastro',
                            'equipamentos_tbequipamentos',
                            'equipamentos_tbequipamentosconsumoespecifico',
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
                            'otimizacao_tbotimizacaocomparacaocenarios',
                            'otimizacao_tbotimizacaoconjuntoequipamentos',
                            'otimizacao_tbconsumoespecificotipoproducao',
                            )

            from .tasks import duplica_tabela_celery
            transaction.on_commit(lambda: duplica_tabela_celery.delay(lista_tabela, self.pk, self.cen_copiar_de_id))

            cursor = connection.cursor()
            sql = "call public.verifica_cenario_filha(" + str(self.pk) + ")"
            cursor.execute(sql)
            cursor.close()

        if status == 'Modificando' and periodo_mudou:
            from .tasks import verifica_filhas
            transaction.on_commit(lambda: verifica_filhas.delay(self.pk))

        if Theme.objects.get(active=True):
            obj = Theme.objects.get(active=True)
            obj.save()

        from django.core.cache import cache
        cache.delete('sps-ferbasa.com')


class TbCenariosDaugther(models.Model):
    dau_order = models.IntegerField(blank=True, null=True, verbose_name='Ano/Mês')
    otimizar = models.BooleanField(default=True, verbose_name='Otimizar')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Vendas', default=0)
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Variável', default=0.00)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Inbound', default=0.00)
    dau_valor_4 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Outbound', default=0.00)
    dau_valor_5 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Manut.', default=0.00)
    dau_valor_6 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Margem', default=0.00)
    dau_valor_7 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Fixo', default=0.00)
    dau_valor_8 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='EBTIDA', default=0.00)
    dau_valor_9 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='EBTIDA (%)',  default=0.00)
    dau_valor_10 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='D&A', default=0.00)
    dau_valor_11 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='IR (%)', default=0.00)
    dau_valor_12 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='MP', default=0.00)
    dau_valor_13 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='WIP', default=0.00)
    dau_valor_14 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='PF', default=0.00)
    dau_valor_15 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Total Est.', default=0.00)
    dau_valor_16 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Receber', default=0.00)
    dau_valor_17 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Pagar', default=0.00)
    dau_valor_18 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='OWCR', default=0.00)
    dau_valor_19 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='CAPEX', default=0.00)
    dau_valor_20 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='OFCF', default=0.00)
    flag = models.IntegerField(blank=True, null=True, verbose_name='Solúção')
    mae = models.ForeignKey('TbCenarios', on_delete=models.CASCADE, verbose_name='Mãe')

    def __str__(self):
        return ''

    def display_order(self):

        if int(self.dau_order) >= 1:
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(id=self.mae_id).cen_tipo == 'Anual':
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + self.dau_order)

            if TbCenarios.objects.get(id=self.mae_id).cen_tipo == 'Mensal':
                year = int(inicio_periodo[:4])
                month = int(inicio_periodo[-2:])

                for j in range(self.dau_order - 1):
                    if month + 1 > 12:
                        month = 1
                        year = year + 1
                    else:
                        month = month + 1
                if month < 10:
                    periodo_str = str(year) + '/0' + str(month)
                else:
                    periodo_str = str(year) + '/' + str(month)

            if TbCenarios.objects.get(id=self.mae_id).cen_tipo == 'Trimestral':
                year = int(inicio_periodo[:4])
                quarter = int(inicio_periodo[-2:])
                for j in range(self.dau_order - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

        return periodo_str

    def solucao_otima(self):
        if self.flag == 1:
            return 'Sim'
        else:
            if self.flag == 0:
                return 'Não'
            else:
                if self.flag == 2:
                    return 'Limpo'
                else:
                    if self.flag == 3:
                        return 'Limpando. Aguarde ...'
                    else:
                        if self.flag == 4:
                           return 'Otimizando. Aguarde ...'
                        else:
                            if self.flag == 5:
                                return 'Consolidando. Aguarde ...'
                            else:
                                if self.flag == 6:
                                    return 'Atualizando fluxos. Aguarde ...'
                                else:
                                    if self.flag == 7:
                                        return 'Fluxos atualizados'
                                    else:
                                        return 'Não definido ...'

    solucao_otima.short_description = 'Solução'

    def str_dau_valor_1(self):
        return locale.format_string('%.0f', self.dau_valor_1,True)
    str_dau_valor_1.short_description = 'Vendas'

    def str_dau_valor_2(self):
        return locale.format_string('%.0f', self.dau_valor_2,True)
    str_dau_valor_2.short_description = 'Variável'

    def str_dau_valor_3(self):
        return locale.format_string('%.0f', self.dau_valor_3,True)
    str_dau_valor_3.short_description = 'Inbound'

    def str_dau_valor_4(self):
        return locale.format_string('%.0f', self.dau_valor_4,True)
    str_dau_valor_4.short_description = 'Outbound'

    def str_dau_valor_5(self):
        return locale.format_string('%.0f', self.dau_valor_5,True)
    str_dau_valor_5.short_description = 'Manut.'

    def str_dau_valor_6(self):
        return locale.format_string('%.0f', self.dau_valor_6,True)
    str_dau_valor_6.short_description = 'Margem'

    def str_dau_valor_7(self):
        return locale.format_string('%.0f', self.dau_valor_7,True)
    str_dau_valor_7.short_description = 'Fixo'

    def str_dau_valor_8(self):
        return locale.format_string('%.0f', self.dau_valor_8,True)
    str_dau_valor_8.short_description = 'EBTIDA'

    def str_dau_valor_9(self):
        return locale.format_string('%.0f', self.dau_valor_9,True)
    str_dau_valor_9.short_description = 'EBTIDA (%)'

    def str_dau_valor_10(self):
        return locale.format_string('%.0f', self.dau_valor_10,True)
    str_dau_valor_10.short_description = 'D&A'

    def str_dau_valor_11(self):
        return locale.format_string('%.0f', self.dau_valor_11,True)
    str_dau_valor_11.short_description = 'IR (%)'

    def str_dau_valor_12(self):
        return locale.format_string('%.0f', self.dau_valor_12,True)
    str_dau_valor_12.short_description = 'MP'

    def str_dau_valor_13(self):
        return locale.format_string('%.0f', self.dau_valor_13,True)
    str_dau_valor_13.short_description = 'WIP'

    def str_dau_valor_14(self):
        return locale.format_string('%.0f', self.dau_valor_14,True)
    str_dau_valor_14.short_description = 'PF'

    def str_dau_valor_15(self):
        return locale.format_string('%.0f', self.dau_valor_15,True)
    str_dau_valor_15.short_description = 'Total Estoque'

    def str_dau_valor_16(self):
        return locale.format_string('%.0f', self.dau_valor_16,True)
    str_dau_valor_16.short_description = 'Receber'

    def str_dau_valor_17(self):
        return locale.format_string('%.0f', self.dau_valor_17,True)
    str_dau_valor_17.short_description = 'Pagar'

    def str_dau_valor_18(self):
        return locale.format_string('%.0f', self.dau_valor_18,True)
    str_dau_valor_18.short_description = 'OWCR'

    def str_dau_valor_19(self):
        return locale.format_string('%.0f', self.dau_valor_19,True)
    str_dau_valor_19.short_description = 'Capex'

    def str_dau_valor_20(self):
        return locale.format_string('%.0f', self.dau_valor_20,True)
    str_dau_valor_20.short_description = 'OFCF'

    class Meta:
        verbose_name = 'Resultado'
        verbose_name_plural = 'Resultados'
        ordering = ['dau_order']

class TbCenariosDaugther1(models.Model): # Diário de Bordo
    data = models.DateField(verbose_name='Data')
    o_que = models.TextField(verbose_name='O Que')

    mae = models.ForeignKey('TbCenarios', on_delete=models.CASCADE, verbose_name='Mãe')

    def __str__(self):
        return ''


    class Meta:
        verbose_name = 'Diário de Bordo'
        verbose_name_plural = 'Diário de Bordo'
        ordering = ['-data']

class TbGlossario(models.Model):
    glo_nome = models.CharField(max_length=50, null=False, blank=False, verbose_name='Nome')
    glo_descricao = models.TextField(verbose_name='Descrição', blank=False, null=False)
    glo_imagem = models.ImageField(upload_to='parameters', null=True, blank=True, verbose_name='Imagem')
    glo_fonte = models.FileField(upload_to='parameters', null=True, blank=True, verbose_name='Fonte')

    def __str__(self):
        return self.glo_nome

    def glo_imagem_tag(self):
        if self.glo_imagem:
            return mark_safe('<img src="%s" style="width: 250px; height:120px;" />' % self.glo_imagem.url)
        else:
            return 'Sem imagem!'

    glo_imagem_tag.short_description = ''

    def clean(self):

        self.glo_nome = self.glo_nome.upper()

        count = 0
        if self.pk is None:
            count = TbGlossario.objects.filter(glo_nome=self.glo_nome).count()
        else:
            count = TbGlossario.objects.filter(glo_nome=self.glo_nome).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Glossário ' + self.glo_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '  Glossário'
        verbose_name_plural = '  Glossário'
        ordering = ['glo_nome']

# Tabelas para o agente de IA

class AgenteConfig(models.Model):
    nome = models.CharField(max_length=100, verbose_name='Nome')
    prompt_sistema = models.TextField(help_text="Instruções para o Agente IA", verbose_name='Instruções')
    ativo = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.ativo:
            AgenteConfig.objects.filter(ativo=True).exclude(pk=self.pk).update(ativo=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return "Comportamento Agente IA"

    class Meta:
        verbose_name        = ' Comportamento Agente IA'
        verbose_name_plural = ' Comportamentos Agente IA'
        ordering = ['nome']

class HistoricoAgente(models.Model):
    data = models.DateTimeField(auto_now_add=True, verbose_name='Data')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Usuário'
    )
    comando_usuario = models.TextField(verbose_name='Pergunta Usuário')
    resposta_ia = models.TextField(verbose_name='Resposta Agente IA')

    def __str__(self):
        return "Histórico Agente IA"

    class Meta:
        verbose_name        = 'Histórico Agente IA'
        verbose_name_plural = 'Históricos Agente IA'
        ordering = ['-data']


class RelatorioPDF(models.Model):
    titulo = models.CharField("Título do Relatório", max_length=200)
    arquivo = models.FileField("Arquivo PDF", upload_to="relatorios_pdf/")
    ativo = models.BooleanField("Disponível para a IA", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Relatório PDF"
        verbose_name_plural = "Relatórios PDF"

    def __str__(self):
        return self.titulo


class EstadoConversaAgente(models.Model):
    """
    Guarda o estado de um "fluxo guiado" (wizard) em andamento dentro do
    chat do Agente IA -- por exemplo, o passo-a-passo de criar um cenário
    novo através da conversa, em vez de preencher o formulário do Admin.

    Um usuário só pode ter UM fluxo em andamento por vez (OneToOne). O
    agente (agents.py) checa, a cada mensagem nova, se o usuário tem um
    registro aqui com fluxo_ativo preenchido -- se tiver, a mensagem é
    tratada como resposta ao passo atual do wizard (não vai pro LLM); se
    não tiver, segue o fluxo normal de conversa com IA.
    """
    FLUXO_CHOICES = (
        ('criar_cenario', 'Criar Cenário'),
    )

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fluxo_ativo = models.CharField(
        max_length=30, choices=FLUXO_CHOICES, null=True, blank=True,
        verbose_name='Fluxo em andamento'
    )
    etapa_atual = models.CharField(max_length=50, null=True, blank=True, verbose_name='Etapa atual')
    dados_coletados = models.JSONField(default=dict, blank=True, verbose_name='Dados coletados')
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    def __str__(self):
        if self.fluxo_ativo:
            return f"{self.usuario} -- {self.get_fluxo_ativo_display()} ({self.etapa_atual})"
        return f"{self.usuario} -- sem fluxo ativo"

    class Meta:
        verbose_name = 'Estado de Conversa do Agente'
        verbose_name_plural = 'Estados de Conversa do Agente'


class PerfilUsuario(models.Model):
    """
    Extensão do usuário padrão do Django (via OneToOne, sem precisar
    trocar AUTH_USER_MODEL -- mudar o modelo de usuário depois que o
    projeto já está em produção é uma migração de altíssimo risco, então
    evitamos isso deliberadamente).

    Guarda qual cenário está "ativo" PARA AQUELE usuário -- é o substituto
    do antigo campo único TbCenarios.cen_ativo, que era global pro sistema
    inteiro.
    """
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    cenario_ativo = models.ForeignKey(
        'TbCenarios', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='Cenário Ativo'
    )
    pode_trocar_cenario = models.BooleanField(
        default=True,
        verbose_name='Pode trocar cenário ativo',
        help_text='Se desmarcado, o usuário não verá a opção de ativar outro cenário.'
    )

    def __str__(self):
        return f"Perfil de {self.usuario}"

    class Meta:
        verbose_name = 'Perfil de Usuário'
        verbose_name_plural = 'Perfis de Usuário'

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=get_user_model())
def criar_perfil_usuario(sender, instance, created, **kwargs):
    if created:
        PerfilUsuario.objects.get_or_create(usuario=instance)