from django.db import models
from django.db import connection
from equipamentos.models import TbEquipamentos, TbEquipamentosDaugther, TbEquipamentosCadastroDaugther, \
    TbEquipamentosCadastro
from django.utils.translation import gettext_lazy as _
from parameters.models import TbCenarios, TbEmpresa
from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.core.exceptions import ValidationError
from django.dispatch import receiver
from produtos.models import TbProdutos
from tabelas.models import atualiza_cenario
from decimal import Decimal
from custo_ferbasa.models import TbConsumoEspecifico

# Para permitir mostrar valores numéricos no padrão Brasil
# Estou usando nos campos numéricos criados no model
import locale

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


# Vai ser executado após o save para algumas tabelas
def verifica_filha(sender, instance, **kwargs):  # passa 01 valor inicial
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha('fluxos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial) + ")"
    cursor.execute(sql)
    cursor.close()


# Vai ser executado após o save para algumas tabelas
def verifica_filha_boolean(sender, instance,
                           **kwargs):  # o valor inicial é do tipo boolean. Vamos passar true sempre.    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_boolean('fluxos_" + sender._meta.object_name + "daugther01', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", true)"
    cursor.execute(sql)

    #  Vamos verificar se esse fluxo tem erro
    sql = "call public.verifica_fluxo(" + str(instance.pk) + ")"
    cursor.execute(sql)

    cursor.close()


def verifica_filha_2(sender, instance, **kwargs):  # passa 02 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure verifica_filha_2
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure verifica_filha_2
    sql = "call public.verifica_filha_2('fluxos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", " + str(
        instance.valor_inicial_2) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_3_valor_1_bit(sender, instance, **kwargs):  # passa 03 valores iniciais. Valor Inicial 1 é bit
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3_Valor_1_Bit
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "exec dbo.Verifica_Filha_3 'fluxos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", '" + str(
        instance.valor_inicial_2) + "', '" + str(instance.valor_inicial_3) + "'"
    cursor.execute(sql)
    cursor.close()


class TbFluxoConsumoPadrao(models.Model):
    flu_con_pad_descricao = models.CharField(max_length=100, blank=True, null=True, verbose_name=_('Descrição'))
    flu_con_pad_validado = models.BooleanField(blank=False, null=False, default=False, verbose_name=_('Validado'))
    flu_con_pad_from_equipamento = models.ForeignKey(TbEquipamentos, on_delete=models.CASCADE,
                                                     verbose_name=_('From'),
                                                     related_name='flu_con_pad_from_equipamento')
    flu_con_pad_to_equipamento = models.ForeignKey(TbEquipamentos, on_delete=models.CASCADE,
                                                   verbose_name=_('To'),
                                                   related_name='flu_con_pad_to_equipamento')
    flu_con_pad_consumo_especifico = models.ForeignKey(TbConsumoEspecifico, on_delete=models.SET_NULL, blank=True,
                                                       null=True,
                                                       verbose_name=_(
                                                           'Consumo Específico'))  # SE DELETAR O CONSUMO ESPECÍFICO, O CAMPO MUDA PARA NULL
    flu_con_pad_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    valor_inicial = models.DecimalField(max_digits=11, decimal_places=4, verbose_name=_('Valor Inicial Consumo Padrão'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.flu_con_pad_descricao)

    def periodo_inicio(self):  # Mostra o periodo inicio do consumo específico
        valor_retorno = ''
        if self.flu_con_pad_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(
                id=self.flu_con_pad_consumo_especifico_id).con_esp_ano_mes_inicio

        return valor_retorno

    periodo_inicio.short_description = _('Período Inicio')

    def periodo_fim(self):  # Mostra o periodo fim do consumo específico
        valor_retorno = ''
        if self.flu_con_pad_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(
                id=self.flu_con_pad_consumo_especifico_id).con_esp_ano_mes_fim

        return valor_retorno

    periodo_fim.short_description = _('Período Fim')

    def valor_indicador(self):  # Mostra o periodo fim do consumo específico
        valor_retorno = 0
        if self.flu_con_pad_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(id=self.flu_con_pad_consumo_especifico_id).con_esp_indicador

        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.4f', valor_retorno, True)

        return valor_retorno

    valor_indicador.short_description = _('Valor Indicador')

    def clean(self):
        # Verificando se já foi cadastrado registro
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbFluxoConsumoPadrao.objects.filter(flu_con_pad_from_equipamento=self.flu_con_pad_from_equipamento,
                                                        flu_con_pad_to_equipamento=self.flu_con_pad_to_equipamento,
                                                        tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbFluxoConsumoPadrao.objects.filter(flu_con_pad_from_equipamento=self.flu_con_pad_from_equipamento,
                                                        flu_con_pad_to_equipamento=self.flu_con_pad_to_equipamento,
                                                        tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError(
                'From Equipamento ' + str(self.flu_con_pad_from_equipamento) + ' / To Equipamento ' + str(
                    self.flu_con_pad_to_equipamento) + ' já cadastrado!')

        if self.flu_con_pad_from_equipamento == self.flu_con_pad_to_equipamento:
            raise ValidationError('From deve ser diferente de To. Favor corrigir!')

        # Vamos ajustar a descrição do fluxo de produção
        codigo_from = TbEquipamentos.objects.get(id=self.flu_con_pad_from_equipamento_id).equ_codigo
        ordem_from = TbEquipamentos.objects.get(id=self.flu_con_pad_from_equipamento_id).equ_ordem_codigo
        codigo_to = TbEquipamentos.objects.get(id=self.flu_con_pad_to_equipamento_id).equ_codigo
        ordem_to = TbEquipamentos.objects.get(id=self.flu_con_pad_to_equipamento_id).equ_ordem_codigo
        self.flu_con_pad_descricao = str(codigo_from) + '/' + str(ordem_from) + ' --> ' + str(codigo_to) + '/' + str(
            ordem_to)

    class Meta:
        verbose_name = _('   Consumo Padrão')
        verbose_name_plural = _('   Consumos Padrões')
        ordering = ['flu_con_pad_from_equipamento', 'flu_con_pad_to_equipamento']
        unique_together = ('flu_con_pad_descricao', 'tbcenarios',)

    def save(self, *args, **kwargs):
        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbFluxoConsumoPadrao)
        # Vamos salvar o super save
        super(TbFluxoConsumoPadrao, self).save(*args, **kwargs)


# Signals a serem executados na tabela TbFluxoConsumoPadrao
post_save.connect(verifica_filha, sender=TbFluxoConsumoPadrao)

# Tiramos pois antes de rodar o cenário será feito a atualização dos fluxos
'''
@receiver([post_save], sender=TbFluxoConsumoPadrao)
def ajusta_input_output2(sender, instance, **kwargs):  # Vai atualizar todos os fluxos que usam esse consumo padrão
    from fluxos.tasks import atualiza_input_output_1_celery
    atualiza_input_output_1_celery.delay(instance.id)
'''


class TbFluxoConsumoPadraoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Consumo Padrão'))
    mae = models.ForeignKey(TbFluxoConsumoPadrao, on_delete=models.CASCADE, verbose_name=_('Mãe'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    # Vamos criar um campo para mostrar o periodo no formato adequado
    def display_order(self):

        if int(self.dau_order) >= 1:

            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + self.dau_order)

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
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

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
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

    # Vamos criar um campo para o custo variável adicionado. É o custo variável adicionado do equipamento from da mãe (campo flu_con_pad_from_equipamento_id)
    def custo_variavel(self):
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o custo variável do equipamento From
            # Primeiro pegando o id do equipamento From
            id_from = TbFluxoConsumoPadrao.objects.get(id=self.mae_id).flu_con_pad_from_equipamento_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_adic_equipamento_order(" + str(self.tbcenarios_id) + ", " + str(
                id_from) + ", " + str(self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Temos que multiplicar pelo consumo específico informado em dau_valor
            retorno = retorno * self.dau_valor

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

            # Vamos ver a moeda da empresa
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel.short_description = _('Custo Var. Adicionado')

    class Meta:
        verbose_name = _('Consumo Padrão Previsto')
        verbose_name_plural = _('Consumos Padrões Previstos')
        ordering = ['dau_order']


# Para alterar o input/output dos fluxos de produção que usam o consumo padrão
@receiver([post_save], sender=TbFluxoConsumoPadraoDaugther)
def ajusta_input_output_fluxo_producao(sender, instance, **kwargs):
    TbFluxoProducao.objects.filter(
        id__in=TbFluxoProducaoDaugther.objects.filter(flu_pro_dau_consumo_padrao_id=instance.mae_id).values_list(
            'mae_id', flat=True)).update(flu_pro_input_output_atualizado=False)


'''
# Tiramos pois antes de rodar o cenário será feito a atualização dos fluxos
# Retiramos porque o save da mãe é ativado também. Se deixarmos roda duas vezes
@receiver([post_save], sender=TbFluxoConsumoPadraoDaugther)
def ajusta_input_output3(sender, instance, **kwargs):
    from fluxos.tasks import atualiza_input_output_1_celery
    atualiza_input_output_1_celery.delay(instance.mae_id)
    #messages.success(sender, 'Novo(s) Fluxo(s) de Produção foi(ram) adicionado(s) com sucesso.')

'''


class TbFluxoProducao(models.Model):
    flu_pro_descricao = models.CharField(max_length=150, verbose_name=_('Descrição'))
    flu_pro_nome = models.CharField(max_length=100, null=True, blank=True, verbose_name=_('Nome'))
    flu_pro_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, null=True, blank=False,
                                        verbose_name=_('Produto'))
    flu_pro_custo_variavel_medio = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                                       verbose_name=_('Custo Variável Médio'))
    flu_pro_ativo = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Ativo'))
    flu_pro_input_output_atualizado = models.BooleanField(default=False, verbose_name=_('I/O Atualizado'))
    flu_pro_copiar_de = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                          verbose_name=_('Copiar de'))
    flu_pro_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    flu_pro_erro = models.CharField(max_length=3, default='SIM', verbose_name=_('Tem Erro'))
    flu_pro_pdf_file = models.FileField(upload_to='pdf_file', null=True, blank=True, verbose_name=_('Fluxo (PDF)'))
    flu_pro_dados_fluxo = models.JSONField(default=dict, null=True, blank=True, verbose_name=_('Dados Fluxo'))
    flu_pro_data_criacao = models.DateTimeField(auto_now_add=True, null=True, blank=True,
                                                verbose_name=_('Data Criação'))
    flu_pro_data_modificacao = models.DateTimeField(auto_now=True, null=True, blank=True,
                                                    verbose_name=_('Data Modificação'))

    '''
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True, null=True)
    dados_fluxo = models.JSONField(default=dict)
    ativo = models.BooleanField(default=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
    data_modificacao = models.DateTimeField(auto_now=True)
    '''

    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.id) + ' /' + self.flu_pro_descricao

    # Vamos criar um campo para mostrar a descrição do fluxo
    def display_descricao(self):
        return self.flu_pro_descricao

    def clean(self):

        print(self.flu_pro_dados_fluxo)

        # Verificando se já foi cadastrado registro
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbFluxoProducao.objects.filter(flu_pro_descricao=self.flu_pro_descricao,
                                                   tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbFluxoProducao.objects.filter(flu_pro_descricao=self.flu_pro_descricao,
                                                   tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError(
                'Fluxo de Produção ' + str(self.flu_pro_descricao) + ' já cadastrado!')

    '''
    # Comentamos pois passamos para campo na tabela
    def custo_variavel_medio(self):  # Campo para mostrar o custo variável médio do fluxo de produção para os períodos considerados no cenário em análise

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o custo variável médio do fluxo de produção
        # Primeiro pegando o id do fluxo de produção
        if self.pk: # Não está incluindo
            id_fluxo_producao = self.pk
            # Expressão SQL
            sql = "call public.atualiza_custo_var_med_fluxo_producao(" + str(id_fluxo_producao) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
        else:
            retorno = 0

        # Vamos formatar o valor no padrão brasileiro
        #locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        # Vamos ver a moeda da empresa
        moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
        if moeda_empresa == 'BRL':
            retorno = 'R$ ' + retorno
        if moeda_empresa == 'USD':
            retorno = 'US$ ' + retorno
        if moeda_empresa == 'EUR':
            retorno = '€ ' + retorno

        return retorno

    custo_variavel_medio.short_description = _('Custo Variável Médio')
    '''

    class Meta:
        verbose_name = _(' Fluxo de Produção')
        verbose_name_plural = _(' Fluxos de Produção')
        ordering = ['flu_pro_produto', 'flu_pro_descricao']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        state = 'Editando'
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbFluxoProducao)
            state = 'Adicionando'

        # Convertendo para maiúsculo
        self.flu_pro_descricao = self.flu_pro_descricao.upper()

        # Vamos salvar o super save
        super(TbFluxoProducao, self).save(*args, **kwargs)
        # Vamos ver se está adicionando e se é para copiar de algum fluxo
        if state == 'Adicionando' and self.flu_pro_copiar_de is not None:
            # Vamos copiar as filhas usando stored procedure
            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure Duplicar_Filhas_Fluxo_Producao
            sql = "call public.duplicar_filhas_fluxo_producao(" + str(self.flu_pro_copiar_de_id) + ", " + str(
                self.pk) + ")"
            cursor.execute(sql)
            cursor.close()


# Signals a serem executados na tabela TbFluxoProducao
post_save.connect(verifica_filha_boolean, sender=TbFluxoProducao)


class TbFluxoProducao01(TbFluxoProducao):
    class Meta:
        verbose_name = _('Detalhe Fluxo/Equipamento')
        verbose_name_plural = _('Detalhes Fluxo/Equipamento')
        proxy = True


class TbFluxoProducaoDaugther(models.Model):
    flu_pro_dau_coluna = models.IntegerField(verbose_name=_('Coluna'))
    flu_pro_dau_linha = models.IntegerField(verbose_name=_('Linha'))
    flu_pro_dau_consumo_padrao = models.ForeignKey(TbFluxoConsumoPadrao, on_delete=models.CASCADE,
                                                   verbose_name=_('From --> To'))
    flu_pro_dau_margem_horaria = models.BooleanField(blank=False, null=False, default=False,
                                                     verbose_name=_('Margem Horária'))
    mae = models.ForeignKey(TbFluxoProducao, on_delete=models.CASCADE, verbose_name=_('Mãe'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    class Meta:
        verbose_name = _('Sequenciamento da Produção')
        verbose_name_plural = _('Sequenciamento da Produção')
        ordering = ['flu_pro_dau_coluna', 'flu_pro_dau_linha']

    def descricao_consumo_padrao(self):

        # Vamos pegar o id da ordem de produção
        id_ordem_from = TbFluxoConsumoPadrao.objects.get(
            id=self.flu_pro_dau_consumo_padrao_id).flu_con_pad_from_equipamento_id
        from_descricao = TbEquipamentos.objects.get(id=id_ordem_from).equ_ordem_descricao

        id_ordem_to = TbFluxoConsumoPadrao.objects.get(
            id=self.flu_pro_dau_consumo_padrao_id).flu_con_pad_to_equipamento_id
        to_descricao = TbEquipamentos.objects.get(id=id_ordem_to).equ_ordem_descricao
        # print(from_descricao + '  ' + to_descricao)

        # Valor Médio do Indicador Padrão e Real
        # Id do InPut e OutPut

        id_input_output = TbFluxoProducaoInputOutput.objects.get(mae_id=self.mae_id,
                                                                 flu_pro_inp_out_equipamento_id=id_ordem_from,
                                                                 flu_pro_inp_out_envia_para=id_ordem_to,
                                                                 flu_pro_inp_out_coluna=self.flu_pro_dau_coluna,
                                                                 flu_pro_inp_out_linha=self.flu_pro_dau_linha).id
        cursor = connection.cursor()
        # Expressão SQL

        sql = "select avg(dau_valor_3), avg(dau_valor_4) from fluxos_tbfluxoproducaoinputoutputdaugther where mae_id = " + str(
            id_input_output)
        cursor.execute(sql)
        retorno = cursor.fetchone()
        output_padrao = retorno[0]
        output_real = retorno[1]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro

        output_padrao = locale.format_string('%.4f', output_padrao, True)
        output_real = locale.format_string('%.4f', output_real, True)
        # print('Passei')

        return from_descricao + ' (P=' + output_padrao + ' / R=' + output_real + ')' + ' --> ' + to_descricao

    descricao_consumo_padrao.short_description = _('Descrição')

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbFluxoProducaoDaugther)

        # Para garantir que somente um equipamento está ativo para a margem de contribuição horária
        if self.flu_pro_dau_margem_horaria:
            # select all other active items
            qs = type(self).objects.filter(mae_id=self.mae, flu_pro_dau_margem_horaria=True)
            # except self (if self already exists)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            # and deactive them
            qs.update(flu_pro_dau_margem_horaria=False)

        # Vamos salvar o super save
        super(TbFluxoProducaoDaugther, self).save(*args, **kwargs)


# Temos que atualizar a sequência do fluxo de produção
# Signal a ser executado na tabela TbFluxoProducaoDaugther
# Vai ser executado após o save da tabela TbFluxoProducaoDaugther para ajustar a ordem das linhas e colunas (sequenciamento do fluxo de produção)
# @receiver([post_save, post_delete], sender=TbFluxoProducaoDaugther)
# Deixamos somente para o caso do save
@receiver([post_save, ], sender=TbFluxoProducaoDaugther)
def ajusta_input_output4(sender, instance, **kwargs):
    from fluxos.tasks import ajusta_sequencia_fluxo_celery
    ajusta_sequencia_fluxo_celery.delay(instance.mae_id)


class TbFluxoProducaoDaugther01(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Running'))
    custo_variavel = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                         verbose_name=_('Custo Variável'))
    custo_variavel_item = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                              verbose_name=_('`Parcela Itens`'))
    custo_variavel_inbound = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                                 verbose_name=_('`Parcela Inbound`'))
    custo_variavel_manutencao = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                                                    verbose_name=_('`Parcela Manutencão`'))
    mae = models.ForeignKey(TbFluxoProducao, on_delete=models.CASCADE, verbose_name=_('Mãe'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    # Vamos criar um campo para mostrar o periodo no formato adequado
    def display_order(self):

        if int(self.dau_order) >= 1:

            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + self.dau_order)

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
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

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
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

    '''
    # Comentamos pois passamos para campo na tabela
    # Vamos criar um campo para o custo variável do fluxo de produção
    def custo_variavel(self):
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o custo variável do fluxo de produção para cada um dos períodos
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = self.mae_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            #locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.2f', retorno, True)

            # Vamos ver a moeda da empresa
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel.short_description = _('Custo Variável')
    '''
    '''
    # Comentamos pois passamos para campo na tabela
    # Vamos criar um campo para o custo variável do fluxo de produção
    def custo_variavel_item(self):
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o custo variável do fluxo de produção para cada um dos períodos
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = self.mae_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_item_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
                self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.2f', retorno, True)

            # Vamos ver a moeda da empresa
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_item.short_description = _('Parcela Itens')
    '''
    '''
    # Comentamos pois passamos para campo na tabela
    # Vamos criar um campo para o custo variável do fluxo de produção
    def custo_variavel_inbound(self):
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o custo variável do fluxo de produção para cada um dos períodos
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = self.mae_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_inbound_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
                self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.2f', retorno, True)

            # Vamos ver a moeda da empresa
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_inbound.short_description = _('Parcela Inbound')
    '''
    '''
    # Comentamos pois passamos para campo na tabela
    # Vamos criar um campo para o custo variável do fluxo de produção
    def custo_variavel_manutencao(self):
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o custo variável do fluxo de produção para cada um dos períodos
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = self.mae_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_manutencao_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
                self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
            retorno = locale.format_string('%.2f', retorno, True)

            # Vamos ver a moeda da empresa
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_manutencao.short_description = _('Parcela Manutencao')
    '''

    # Vamos criar um campo para o gargalo e a produtividade no gargalo
    def gargalo_produtividade(self):
        if int(self.dau_order) >= 1:
            retorno = ''
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para identificar o gargalo no fluxo de produção e a produtividade equivalente nesse gargalo
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = self.mae_id
            # Expressão SQL
            sql = "call public.gargalo_produtividade_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
                self.dau_order) + ", '')"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

        return retorno

    gargalo_produtividade.short_description = _('Gargalo/Produtividade')

    # Vamos criar um campo para mostrar a produção máxima no gargalo no período (mensal, trimestral ou anual)
    def producao_maxima(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular a produtividade equivalente no gargalo.
        # Primeiro pegando o id do fluxo de produção
        id_fluxo_producao = self.mae_id
        # Expressão SQL
        sql = "call public.valor_produtividade_gargalo_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
            self.dau_order) + ", 0)"
        cursor.execute(sql)
        produtividade = cursor.fetchone()[0]
        cursor.close()

        if int(self.dau_order) >= 1:

            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + self.dau_order)

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
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

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
                year = int(inicio_periodo[:4])
                quarter = int(inicio_periodo[-2:])
                for j in range(self.dau_order - 1):
                    if quarter + 1 > 4:
                        quarter = 1
                        year = year + 1
                    else:
                        quarter = quarter + 1
                periodo_str = str(year) + '/0' + str(quarter)

        cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o número de horas no período
        # Expressão SQL
        sql = "call public.horas_no_periodo(" + str(cen_ativo_id) + ", '" + periodo_str + "', 0)"
        # print(sql)
        cursor.execute(sql)
        horas_periodo = cursor.fetchone()[0]
        cursor.close()

        prod_maxima = locale.format_string('%.0f', produtividade * horas_periodo, True)

        return prod_maxima

    producao_maxima.short_description = _('Produção Máxima')

    class Meta:
        verbose_name = _('Detalhes por Período')
        verbose_name_plural = _('Detalhes por Período')
        ordering = ['dau_order']


class TbFluxoProducaoInputOutput(models.Model):
    flu_pro_inp_out_coluna = models.IntegerField(verbose_name=_('Coluna'))
    flu_pro_inp_out_linha = models.IntegerField(verbose_name=_('Linha'))
    flu_pro_inp_out_equipamento = models.ForeignKey(TbEquipamentos, related_name='flu_pro_inp_out_equipamento',
                                                    on_delete=models.CASCADE, verbose_name=_('Equipamento'))
    flu_pro_inp_out_envia_para = models.ForeignKey(TbEquipamentos, blank=True, null=True,
                                                   related_name='flu_pro_inp_out_envia_para', on_delete=models.CASCADE,
                                                   verbose_name=_('Envia para'))
    flag = models.BooleanField(blank=False, null=False, default=False, verbose_name=_('Ativo'))
    mae = models.ForeignKey(TbFluxoProducao, on_delete=models.CASCADE, verbose_name=_('Mãe'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.flu_pro_inp_out_coluna) + '/' + str(self.flu_pro_inp_out_linha) + '/' + str(
            self.flu_pro_inp_out_equipamento)

    class Meta:
        verbose_name = _('Detalhamento por Equipamento')
        verbose_name_plural = _('Detalhamento por Equipamento')
        ordering = ['flu_pro_inp_out_coluna', 'flu_pro_inp_out_linha']


class TbFluxoProducaoInputOutputDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    # dau_valor_1 = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Input Padrão'))
    # dau_valor_2 = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Input Real'))
    dau_valor_3 = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Output Padrão'))
    dau_valor_4 = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Output Real'))
    mae = models.ForeignKey(TbFluxoProducaoInputOutput, on_delete=models.CASCADE, verbose_name=_('Mãe'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    # Vamos criar um campo para mostrar o periodo no formato adequado
    def display_order(self):

        if int(self.dau_order) >= 1:

            # Temos que pegar o inicio do cenário
            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
                # Temos que considerar o inicio-periodo somente os 4 digitos iniciais
                inicio_periodo = inicio_periodo[:4]
                periodo_str = str(int(inicio_periodo) - 1 + self.dau_order)

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Mensal':
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

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
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

    # Vamos criar um campo para mostrar se equipamento está rodando (running)

    def running(self):
        # Temos que pegar o id do equipamento/tipo de produção que está na tabela mãe.
        id_equipamento_tipo_producao = TbFluxoProducaoInputOutput.objects.get(
            id=self.mae_id).flu_pro_inp_out_equipamento_id
        # Temos que pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=id_equipamento_tipo_producao).equ_codigo_id
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        return TbEquipamentosCadastroDaugther.objects.get(mae_id=id_equipamento, dau_order=self.dau_order,
                                                          tbcenarios_id=ativo).dau_valor_1

    # Para mostrar um icon e não True/False

    running.boolean = True

    def produtividade(self):
        # Temos que pegar o id do equipamento/tipo de produção que está na tabela mãe.
        id_equipamento_tipo_produção = TbFluxoProducaoInputOutput.objects.get(
            id=self.mae_id).flu_pro_inp_out_equipamento_id
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        retorno = TbEquipamentosDaugther.objects.get(mae_id=id_equipamento_tipo_produção, dau_order=self.dau_order,
                                                     tbcenarios_id=ativo).dau_valor_2
        retorno = locale.format_string('%.2f', retorno, True)
        return retorno

    def custo_ate_equipamento(self):  # Calcula o custo do fluxo de produção até o equipamento (inclusive o equipamento)
        #  Iremos precisar dessa infomação para calcular o WIP
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL
            # Primeiro pegando o id do fluxo de produção
            id_fluxo_producao = TbFluxoProducaoInputOutput.objects.get(
                id=self.mae_id).mae_id  # Essa tabela é filha da tabela do fluxo de produção
            # Pegando o número da coluna anterior
            id_coluna = TbFluxoProducaoInputOutput.objects.get(id=self.mae_id).flu_pro_inp_out_coluna
            # Pegando o id do equipamento tipo de producao
            id_equipamento_tipo_producao = TbFluxoProducaoInputOutput.objects.get(
                id=self.mae_id).flu_pro_inp_out_equipamento_id
            # Expressão SQL
            sql = "call public.atualiza_custo_var_fluxo_producao_order_coluna_equipamento(" + str(
                self.tbcenarios_id) + ", " + str(id_fluxo_producao) + ", " + str(
                self.dau_order) + ", " + str(id_coluna) + ", " + str(id_equipamento_tipo_producao) + ", " + str(
                self.mae_id) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]

            retorno = locale.format_string('%.2f', retorno, True)
        return retorno

    custo_ate_equipamento.short_description = _('Custo Var. Adic.')

    def indfun(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Temos que pegar o id do equipamento/tipo de produção que está na tabela mãe.
        id_equipamento_tipo_producao = TbFluxoProducaoInputOutput.objects.get(
            id=self.mae_id).flu_pro_inp_out_equipamento_id

        # Vamos pegar o % de horas paradas não programadas
        perc_horas_paradas_nao_programadas = TbEquipamentosDaugther.objects.get(mae_id=id_equipamento_tipo_producao,
                                                                                dau_order=self.dau_order,
                                                                                tbcenarios_id=ativo).dau_valor_1

        # Temos que pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=id_equipamento_tipo_producao).equ_codigo_id

        # Vamos pegar o % de horas paradas programadas
        perc_horas_paradas_programadas = TbEquipamentosCadastroDaugther.objects.get(mae_id=id_equipamento,
                                                                                    dau_order=self.dau_order,
                                                                                    tbcenarios_id=ativo).dau_valor_2
        ind_func = ((1 - Decimal(perc_horas_paradas_programadas) / 100) * (
                    1 - Decimal(perc_horas_paradas_nao_programadas) / 100)) * 100
        ind_func = locale.format_string('%.2f', ind_func, True)
        return ind_func

    def produtividade_equivalente(self):
        pass
        '''
        # Temos que pegar o id do equipamento/tipo de produção que está na tabela mãe.
        id_equipamento_tipo_produção = TbFluxoProducaoInputOutput.objects.get(id=self.mae_id).flu_pro_inp_out_equipamento_id
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        produtividade = TbEquipamentosDaugther.objects.get(mae_id=id_equipamento_tipo_produção, dau_order=self.dau_order, tbcenarios_id=ativo).dau_valor_2

        # Temos que pegar o id do equipamento/tipo de produção que está na tabela mãe.
        id_equipamento_tipo_producao = TbFluxoProducaoInputOutput.objects.get(id=self.mae_id).flu_pro_inp_out_equipamento_id

        # Vamos pegar o % de horas paradas não programadas
        perc_horas_paradas_nao_programadas = TbEquipamentosDaugther.objects.get(mae_id=id_equipamento_tipo_producao, dau_order=self.dau_order, tbcenarios_id=ativo).dau_valor_1

        # Temos que pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=id_equipamento_tipo_producao).equ_codigo_id

        # Vamos pegar o % de horas paradas programadas
        perc_horas_paradas_programadas = TbEquipamentosCadastroDaugther.objects.get(mae_id=id_equipamento, dau_order=self.dau_order, tbcenarios_id=ativo).dau_valor_2
        ind_func = ((1 - Decimal(perc_horas_paradas_programadas) / 100) * (1 - Decimal(perc_horas_paradas_nao_programadas) / 100))

        retorno = 1 / ((self.dau_valor_4 / produtividade) / ind_func)
        retorno = locale.format_string('%.2f', retorno, True)
        return retorno
        '''

    produtividade_equivalente.short_description = _('Prod. Equivalente')

    class Meta:
        verbose_name = _('Detalhe por Período (click e use setas para direita e esquerda)')
        verbose_name_plural = _('Detalhes por Período (click e use setas para direita e esquerda)')
        ordering = ['dau_order']
        unique_together = ('mae', 'dau_order', 'tbcenarios',)

# ***********************************************************************************************************