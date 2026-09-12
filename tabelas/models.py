from parameters.models import TbCenarios, TbEmpresa
from custo_ferbasa.models import TbCustoVariavelAdicionado
from django.db import models
from django.db import connection
from decimal import Decimal
from django.db.models.signals import post_save, pre_save
from django.utils.safestring import mark_safe
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group

import locale
locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  #  Estou usando esse pois Heroku não aceita pt_BR

# Vai ser executado após o save para algumas tabelas
# Verifica se tem as filhas e faz os ajustes necessários (cria ou deleta)
# from produtos.models import TbProdutos

def verifica_filha(sender, instance, **kwargs):  # passa 01 valor inicial
    # Vamos atualizar a filha usando o Stored Procedure verifica_filha
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure verifica_filha
    sql = "call public.verifica_filha('tabelas_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial) + ")"
    cursor.execute(sql)
    cursor.close()

def verifica_filha_2(sender, instance, **kwargs):  # passa 02 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_2
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "cal public.verifica_filha_2 'tabelas_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", " + str(instance.valor_inicial_2) + ")"
    cursor.execute(sql)
    cursor.close()

def verifica_filha_3_valor_1_bit(sender, instance, **kwargs):  # passa 03 valores iniciais. Valor Inicial 1 é bit
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3_Valor_1_Bit
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "exec dbo.Verifica_Filha_3_Valor_1_Bit 'tabelas_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", '" + str(instance.valor_inicial_2) + "', '" + str(instance.valor_inicial_3) + "'"
    cursor.execute(sql)
    cursor.close()

# Vai ser executado antes do save.
def atualiza_cenario(sender, instance, **kwargs):
    # Vamos pegar o cenário ativo e lançar no campo tbcenarios_id. Isso porque o indicador esta sendo mostrado/editado se tiver o tbcenarios_id = id do cenário ativo.
    # Só executa este método se estiver incluindo dados.
    instance.tbcenarios_id = TbCenarios.objects.get(cen_ativo=True).id

# Divisor de tabelas .....................................................................

class TbIndicadores(models.Model):
    ind_nome = models.CharField(max_length=25, verbose_name='Nome')
    ind_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    ind_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name='Fonte')
    valor_inicial = models.DecimalField(max_digits=8, decimal_places=4, verbose_name='Valor Inicial (%)', default=0.00)
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return f'{self.ind_nome}'

    def clean(self):

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        novo_nome = self.ind_nome.strip().upper()
        # Se estiver adicionando
        if self.pk is None:
            count = TbIndicadores.objects.filter(ind_nome=novo_nome, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbIndicadores.objects.filter(ind_nome=novo_nome, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Indicador ' + self.ind_nome + ' já cadastrado para esse cenário!')

    class Meta:
        verbose_name = '                 Indicador'
        verbose_name_plural = '                 Indicadores'
        ordering = ['ind_nome']

    def save(self, *args, **kwargs):
        self.ind_nome = self.ind_nome.strip().upper()

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbIndicadores)

        # Vamos salvar o super save
        super(TbIndicadores, self).save(*args, **kwargs)

        # Temos que verificar quais são as tabelas que estão usando esse indicador e atualizar os valores
        # Fazemos isso rodando um stored procedure chamado Atualiza_Indicador passando para o mesmo o ID do indicador
        # que no caso é o self.id
        cursor = connection.cursor()
        sql = "call public.atualiza_indicador(" + str(self.tbcenarios_id) + ", " + str(self.id) + ")"
        cursor.execute(sql)
        cursor.close()


# Signals a serem executados na tabela TbIndicadores
post_save.connect(verifica_filha, sender=TbIndicadores)

class TbIndicadoresDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=8, decimal_places=4, verbose_name='Valor (%)', default=0.0000)
    mae = models.ForeignKey(TbIndicadores, on_delete=models.CASCADE, verbose_name='Indicador')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbIndicadoresDaugther, self).save(*args, **kwargs)

        # Temos que verificar quais são as tabelas que estão usando esse indicador e atualizar os valores
        # Fazemos isso rodando um stored procedure chamado Atualiza_Indicador passando para o mesmo o ID do indicador
        # que no caso é o self.mae_id
        cursor = connection.cursor()
        sql = "call public.atualiza_indicador(" + str(self.tbcenarios_id) + ", " + str(self.mae_id) + ")"
        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = 'Valores Previstos (%)'
        verbose_name_plural = 'Valores Previstos (%)'
        ordering = ['dau_order']

class TbCambio(models.Model):
    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        # Vamos ver se tem registro
        if TbEmpresa.objects.filter(id=1).count() == 1:
            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
        else:
            moeda_empresa = ''
    else:
        moeda_empresa = ''

    cam_moeda_choice = ()
    if moeda_empresa == 'BRL':
        cam_moeda_choice = (
            ('USD', 'DÓLAR'),
            ('EUR', 'EURO')
        )
    if moeda_empresa == 'USD':
        cam_moeda_choice = (
            ('BRL', 'REAL'),
            ('EUR', 'EURO')
        )
    if moeda_empresa == 'EUR':
        cam_moeda_choice = (
            ('BRL', 'REAL'),
            ('USD', 'DÓLAR'),
        )

    cam_moeda = models.CharField(max_length=3, choices=cam_moeda_choice, null=False, blank=False, default='BRL',
                                 verbose_name='Moeda')
    cam_moeda_imagem = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Imagem da Moeda')
    cam_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    cam_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name='Fonte')
    valor_inicial = models.DecimalField(max_digits=10, decimal_places=4, verbose_name='Valor Inicial', null=True)
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.get_cam_moeda_display()

    def cam_moeda_imagem_tag(self):
        if self.cam_moeda_imagem:
            return mark_safe('<img src="%s" style="width: 270px; height:90px;" />' % self.cam_moeda_imagem.url)
        else:
            return 'Sem imagem!'

    cam_moeda_imagem_tag.short_description = ''

    def clean(self):

        # Verificando se já foi cadastrado registro com o mesmo nome
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCambio.objects.filter(cam_moeda=self.cam_moeda, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCambio.objects.filter(cam_moeda=self.cam_moeda, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Taxa de Câmbio ' + self.cam_moeda + ' já cadastrado!')

    class Meta:
        verbose_name = '               Taxa de Câmbio'
        verbose_name_plural = '               Taxas de Câmbio'
        ordering = ['cam_moeda']
        # constraints = [
        #              models.UniqueConstraint(fields=['cam_moeda', 'tbcenarios'], name='Moeda / Cenário')
        #              ]
        # unique_together = ('cam_moeda', 'tbcenarios',)

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando o cenário
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbCambio)

        # Vamos salvar o super save
        super(TbCambio, self).save(*args, **kwargs)


# Signals a serem executados na tabela TbCambio
post_save.connect(verifica_filha, sender=TbCambio)

class TbCambioDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=10, decimal_places=4, verbose_name='Valor')
    mae = models.ForeignKey(TbCambio, on_delete=models.CASCADE, verbose_name='Câmbio')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    class Meta:
        verbose_name = 'Valores Previstos'
        verbose_name_plural = 'Valores Previstos'
        ordering = ['dau_order']

class TbImpostoRenda(models.Model):
    imp_observacao = models.TextField(verbose_name='Imposto de Renda', blank=True, null=True)
    valor_inicial = models.DecimalField(max_digits=6, decimal_places=2, default=32.00, verbose_name='Valor Inicial')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return ''

    class Meta:
        verbose_name = '              Imposto de Renda'
        verbose_name_plural = '              Imposto de Renda'

# Signals a serem executados na tabela TbImpostoRenda
post_save.connect(verifica_filha, sender=TbImpostoRenda)

class TbImpostoRendaDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Valor (%)')
    mae = models.ForeignKey(TbImpostoRenda, on_delete=models.CASCADE, verbose_name='Imposto de Renda')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    class Meta:
        verbose_name = 'Valores Previstos (%)'
        verbose_name_plural = 'Valores Previstos (%)'
        ordering = ['dau_order']

class TbTaxaDesconto(models.Model):
    tax_observacao = models.TextField(verbose_name='Taxa de Desconto (WACC)', blank=True, null=True)
    valor_inicial = models.DecimalField(max_digits=6, decimal_places=2, default=10.00, verbose_name='Valor Inicial')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return ''

    class Meta:
        verbose_name = '             Taxa de Desconto (WACC)'
        verbose_name_plural = '             Taxa de Desconto (WACC)'

# Signals a serem executados na tabela TbTaxaDesconto
post_save.connect(verifica_filha, sender=TbTaxaDesconto)

class TbTaxaDescontoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Valor (%)')
    mae = models.ForeignKey(TbTaxaDesconto, on_delete=models.CASCADE, verbose_name='Taxa de Desconto (WACC)')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    class Meta:
        verbose_name = 'Valores Previstos (%)'
        verbose_name_plural = 'Valores Previstos (%)'
        ordering = ['dau_order']

class TbCustoFixo(models.Model):
    class FixMoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    fix_nome = models.CharField(max_length=50, verbose_name='Nome')
    fix_indicador = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Indicador')
    fix_moeda = models.CharField(max_length=3, choices=FixMoedaChoices.choices, null=False, blank=False, default='BRL', verbose_name='Moeda')
    fix_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    fix_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name='Fonte')
    valor_inicial_1 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inicial Valor')
    valor_inicial_2 = models.IntegerField(verbose_name='Valor Inicial Pagamento (dias)')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.fix_nome

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        self.fix_nome = self.fix_nome.upper()

        # Montando restrições
        if self.fix_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.fix_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError('A moeda ' + self.fix_moeda + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCustoFixo.objects.filter(fix_nome=self.fix_nome, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCustoFixo.objects.filter(fix_nome=self.fix_nome, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Custo Fixo ' + self.fix_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '            Custo Fixo'
        verbose_name_plural = '            Custos Fixos'
        ordering = ['fix_nome']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbCustoFixo)

        # Vamos salvar o super save
        super(TbCustoFixo, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.fix_indicador_id is not None and self.fix_indicador_id != '':
            id_indicador = int(self.fix_indicador_id)
        else:
            id_indicador = int(0)

        # Atenção: o sp verifica_filha_2_com_indicador_a só considera os valores iniciais no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_2_com_indicador_a('tabelas_" + "tbcustofixo" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial_1) + ", " + str(self.valor_inicial_2) + ", " + str(id_indicador) + ")"
        cursor.execute(sql)
        cursor.close()

# Signals a serem executados na tabela TbCustoFixo
#post_save.connect(verifica_filha, sender=TbCustoFixo)

class TbCustoFixoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor')
    dau_valor_2 = models.IntegerField(verbose_name='Pagamento (dias)')
    mae = models.ForeignKey(TbCustoFixo, on_delete=models.CASCADE, verbose_name='Custo Fixo')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    def valor_moeda_empresa(self):  # valor do custo fixo na moeda da empresa.
        valor_retorno = self.dau_valor_1
        taxa_cambio = 1
        # Verificando as moedas para ver se será necessário ajustar pelo câmbio
        if TbCustoFixo.objects.get(id=self.mae_id).fix_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Temos que pegar a taxa de câmbio para a moeda informado para o referido dau_order
            # Vamos pegar o cenário ativo
            ativo = TbCenarios.objects.get(cen_ativo=True).id
            id_mae_cambio = TbCambio.objects.get(cam_moeda=TbCustoFixo.objects.get(id=self.mae_id).fix_moeda, tbcenarios_id=ativo).id
            taxa_cambio = TbCambioDaugther.objects.get(mae_id=id_mae_cambio, dau_order=self.dau_order).dau_valor

        valor_retorno = valor_retorno * taxa_cambio
        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        if TbEmpresa.objects.filter(id=1).count() > 0:
            valor_moeda_empresa.short_description = 'Valor (' + TbEmpresa.objects.get(id=1).emp_moeda + ')'


    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbCustoFixoDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicadores para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_Com_Indicador
        id_indicador = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbCustoFixo.objects.get(pk=self.mae_id)
        if campo_mae.fix_indicador_id is not None:
            id_indicador = campo_mae.fix_indicador_id

        # Atenção: o sp verifica_filha_2_com_indicador_a só considera os valores iniciais no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_2_com_indicador_a('tabelas_" + "tbcustofixo" + "daugther', " + str(campo_mae.pk) \
            + ", " + str(campo_mae.tbcenarios_id) + ", '" + str(campo_mae.valor_inicial_1) + "', '" + str(campo_mae.valor_inicial_2) + "', " + str(id_indicador) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = 'Valores Previstos'
        verbose_name_plural = 'Valores Previstos'
        ordering = ['dau_order']

class TbDepreAmorti(models.Model):
    class DepMoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    dep_nome = models.CharField(max_length=50, verbose_name='Nome')
    dep_recorrente = models.BooleanField(blank=False, null=False, default=False, verbose_name='Recorrente')
    dep_indicador = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Indicador')
    dep_moeda = models.CharField(max_length=3, choices=DepMoedaChoices.choices, null=False, blank=False, default='BRL', verbose_name='Moeda')
    dep_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    dep_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name='Fonte')
    valor_inicial = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inicial')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.dep_nome

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        self.dep_nome = self.dep_nome.upper()

        # Montando restrições
        if self.dep_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.dep_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError('A moeda ' + self.dep_moeda + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbDepreAmorti.objects.filter(dep_nome=self.dep_nome, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbDepreAmorti.objects.filter(dep_nome=self.dep_nome, tbcenarios_id=ativo).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Depreciação/Amortização ' + self.dep_nome + ' já cadastrada!')

    class Meta:
        verbose_name = '           Deprec./Amortização'
        verbose_name_plural = '           Deprec./Amortizações'
        ordering = ['dep_nome']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbDepreAmorti)

        # Vamos salvar o super save
        super(TbDepreAmorti, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.dep_indicador_id is not None and self.dep_indicador_id != '':
            id_indicador = int(self.dep_indicador_id)
        else:
            id_indicador = int(0)

        # Atenção: o sp verifica_filha_com_indicador só considera os valores iniciais no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_com_indicador('tabelas_" + "tbdepreamorti" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial) + ", " + str(id_indicador) + ")"
        cursor.execute(sql)
        cursor.close()

# Signals a serem executados na tabela TbDepreAmorti
#post_save.connect(verifica_filha, sender=TbDepreAmorti)

class TbDepreAmortiDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor')
    mae = models.ForeignKey(TbDepreAmorti, on_delete=models.CASCADE, verbose_name='Depreciação/Amortização')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    def valor_moeda_empresa(self):  # valor da depreciação/amortização na moeda da empresa.
        valor_retorno = self.dau_valor
        taxa_cambio = 1
        # Verificando as moedas para ver se será necessário ajustar pelo câmbio
        if TbDepreAmorti.objects.get(id=self.mae_id).dep_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Temos que pegar a taxa de câmbio para a moeda informado para o referido dau_order
            # Vamos pegar o cenário ativo
            ativo = TbCenarios.objects.get(cen_ativo=True).id
            id_mae_cambio = TbCambio.objects.get(cam_moeda=TbDepreAmorti.objects.get(id=self.mae_id).dep_moeda, tbcenarios_id=ativo).id
            taxa_cambio = TbCambioDaugther.objects.get(mae_id=id_mae_cambio, dau_order=self.dau_order).dau_valor

        valor_retorno = valor_retorno * taxa_cambio
        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        if TbEmpresa.objects.filter(id=1) == 1:
            valor_moeda_empresa.short_description = 'Valor (' + TbEmpresa.objects.get(id=1).emp_moeda + ')'
        else:
            valor_moeda_empresa.short_description = ''

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbDepreAmortiDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicadores para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_Com_Indicador
        id_indicador = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbDepreAmorti.objects.get(pk=self.mae_id)
        if campo_mae.dep_indicador_id is not None:
            id_indicador = campo_mae.dep_indicador_id

        # Atenção: o sp Verifica_Filha_Com_Indicador só considera os valores iniciais no caso de não termos nenhuma filha
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)
        sql = "call public.verifica_filha_com_indicador('tabelas_" + "tbdepreamorti" + "daugther', " + str(campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", '" + str(campo_mae.valor_inicial) + "', " + str(id_indicador) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = 'Valores Previstos'
        verbose_name_plural = 'Valores Previstos'
        ordering = ['dau_order']

class TbCapex(models.Model):
    class CapMoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    cap_nome = models.CharField(max_length=50, verbose_name='Nome')
    cap_recorrente = models.BooleanField(blank=False, null=False, default=False, verbose_name='Recorrente')
    cap_indicador = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Indicador')
    cap_moeda = models.CharField(max_length=3, choices=CapMoedaChoices.choices, null=False, blank=False, default='BRL', verbose_name='Moeda')
    cap_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    cap_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name='Fonte')
    valor_inicial = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inicial')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.cap_nome

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        self.cap_nome = self.cap_nome.upper()

        # Montando restrições
        if self.cap_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.cap_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError('A moeda ' + self.cap_moeda + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário

        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCapex.objects.filter(cap_nome=self.cap_nome, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCapex.objects.filter(cap_nome=self.cap_nome, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('CAPEX ' + self.cap_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '          CAPEX'
        verbose_name_plural = '          CAPEX'
        ordering = ['cap_nome']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbCapex)

        # Vamos salvar o super save
        super(TbCapex, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.cap_indicador_id is not None and self.cap_indicador_id != '':
            id_indicador = int(self.cap_indicador_id)
        else:
            id_indicador = int(0)

        # Atenção: o sp verifica_filha_com_indicador só considera os valores iniciais no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_com_indicador('tabelas_" + "tbcapex" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial) + ", " + str(id_indicador) + ")"
        cursor.execute(sql)
        cursor.close()

# Signals a serem executados na tabela TbCapex
#post_save.connect(verifica_filha, sender=TbCapex)

class TbCapexDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor')
    mae = models.ForeignKey(TbCapex, on_delete=models.CASCADE, verbose_name='Depreciação/Amortização')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    def valor_moeda_empresa(self):  # valor do CAPEX na moeda da empresa.
        valor_retorno = self.dau_valor
        taxa_cambio = 1
        # Verificando as moedas para ver se será necessário ajustar pelo câmbio
        if TbCapex.objects.get(id=self.mae_id).cap_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Temos que pegar a taxa de câmbio para a moeda informado para o referido dau_order
            # Vamos pegar o cenário ativo
            ativo = TbCenarios.objects.get(cen_ativo=True).id
            id_mae_cambio = TbCambio.objects.get(cam_moeda=TbCapex.objects.get(id=self.mae_id).cap_moeda, tbcenarios_id=ativo).id
            taxa_cambio = TbCambioDaugther.objects.get(mae_id=id_mae_cambio, dau_order=self.dau_order).dau_valor

        valor_retorno = valor_retorno * taxa_cambio
        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
    all_tables = connection.introspection.table_names()
    if 'parameters_tbempresa' in all_tables:
        if TbEmpresa.objects.filter(id=1) == 1:
            valor_moeda_empresa.short_description = 'Valor (' + TbEmpresa.objects.get(id=1).emp_moeda + ')'
        else:
            valor_moeda_empresa.short_description = ''

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbCapexDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicadores para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_Com_Indicador
        id_indicador = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbCapex.objects.get(pk=self.mae_id)
        if campo_mae.cap_indicador_id is not None:
            id_indicador = campo_mae.cap_indicador_id

        # Atenção: o sp Verifica_Filha_Com_Indicador só considera os valores iniciais no caso de não termos nenhuma filha
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)
        sql = "call public.verifica_filha_com_indicador('tabelas_" + "tbcapex" + "daugther', " + str(campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", '" + str(campo_mae.valor_inicial) + "', " + str(id_indicador) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = 'Valores Previstos'
        verbose_name_plural = 'Valores Previstos'
        ordering = ['dau_order']

class TbUnidadeProducao(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    uni_nome = models.CharField(max_length=40, null=False, blank=False, verbose_name='Nome')
    uni_imagem = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Imagem')
    uni_localizacao = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Localização')
    uni_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)

    def __str__(self):
        return self.uni_nome

    def uni_imagem_tag(self):
        if self.uni_imagem:
            return mark_safe('<img src="%s" style="width: 600px; height:400px;" />' % self.uni_imagem.url)
        else:
            return 'Sem imagem!'

    uni_imagem_tag.short_description = ''

    def uni_imagem_tag_small(self):
        if self.uni_imagem:
            return mark_safe('<img src="%s" style="width: 200px; height:130px;" />' % self.uni_imagem.url)
        else:
            return 'Sem imagem!'

    uni_imagem_tag_small.short_description = ''

    def uni_localizacao_tag(self):
        if self.uni_localizacao:
            return mark_safe('<img src="%s" style="width: 600px; height:400px;" />' % self.uni_localizacao.url)
        else:
            return 'Sem imagem!'

    uni_localizacao_tag.short_description = ''

    def uni_localizacao_tag_small(self):
        if self.uni_localizacao:
            return mark_safe('<img src="%s" style="width: 200px; height:130px;" />' % self.uni_localizacao.url)
        else:
            return 'Sem imagem!'

    uni_localizacao_tag_small.short_description = ''

    def clean(self):

        self.uni_nome = self.uni_nome.upper()

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbUnidadeProducao.objects.filter(uni_nome=self.uni_nome).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbUnidadeProducao.objects.filter(uni_nome=self.uni_nome).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Planta de Produção ' + self.uni_nome + ' já cadastrada!')

    class Meta:
        verbose_name = '         Unidade de Produção'
        verbose_name_plural = '         Unidades de Produção'
        ordering = ['uni_nome']

class TbMercado(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    mer_nome = models.CharField(max_length=40, null=False, blank=False, verbose_name='Nome')
    mer_imagem = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Imagem')
    mer_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)

    def __str__(self):
        return self.mer_nome

    def mer_imagem_tag(self):
        if self.mer_imagem:
            return mark_safe('<img src="%s" style="width: 270px; height:200px;" />' % self.mer_imagem.url)
        else:
            return 'Sem imagem!'

    mer_imagem_tag.short_description = ''

    def mer_imagem_tag_small(self):
        if self.mer_imagem:
            return mark_safe('<img src="%s" style="width: 110px; height:80px;" />' % self.mer_imagem.url)
        else:
            return 'Sem imagem!'

    mer_imagem_tag_small.short_description = ''

    # Mostra somente os registros do cenário ativo
    def get_queryset(self, request):
        cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
        return super(TbMercado, self).get_queryset(request).filter(tbcenarios=cen_ativo)

    def clean(self):

        self.mer_nome = self.mer_nome.upper()

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbMercado.objects.filter(mer_nome=self.mer_nome).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbMercado.objects.filter(mer_nome=self.mer_nome).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Mercado ' + self.mer_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '        Mercado'
        verbose_name_plural = '        Mercados'
        ordering = ['mer_nome']

class TbCustoTipo(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    cus_tip_nome = models.CharField(max_length=25, null=False, blank=False, verbose_name='Nome')
    cus_tip_group = models.ManyToManyField(Group, blank=True, verbose_name='Grupo(s) Usuários com Permissão')
    cus_tip_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)

    def __str__(self):
        return self.cus_tip_nome

    def clean(self):

        self.cus_tip_nome = self.cus_tip_nome.upper()

        # Verificando se já foi cadastrado registro com o mesmo nome
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCustoTipo.objects.filter(cus_tip_nome=self.cus_tip_nome).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCustoTipo.objects.filter(cus_tip_nome=self.cus_tip_nome).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Tipo de Custo ' + self.cus_tip_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '     Custo Variável - Tipo'
        verbose_name_plural = '     Custo Variável - Tipos'
        ordering = ['cus_tip_nome']

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbCustoTipo, self).save(*args, **kwargs)

class TbCustoItem(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    class UnidadeItemChoices(models.TextChoices):
        un = ('un', 'un')
        pe = ('pe', 'pe')
        g = ('g', 'g')
        kg = ('kg', 'kg')
        t = ('t', 't')
        h = ('h', 'h')
        hh = ('hh', 'hh')
        m3 = ('m3', 'm3')
        Nm3 = ('Nm3', 'Nm3')
        L = ('L', 'L')
        kw = ('kw', 'kw')
        kwh = ('kwh', 'kwh')
        Mw = ('Mw', 'Mw')
        Mwh = ('Mwh', 'Mwh')

    cus_ite_nome = models.CharField(max_length=60, verbose_name='Nome')
    cus_ite_codigo_interno = models.CharField(max_length=15, blank=True, null=True, verbose_name='Código Interno')
    cus_ite_unidade = models.CharField(max_length=3, choices=UnidadeItemChoices.choices, verbose_name='Unidade')
    cus_ite_tipo = models.ForeignKey(TbCustoTipo, null=True, blank=True, verbose_name='Tipo', on_delete=models.CASCADE)
    cus_ite_imagem = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Imagem do Item')
    cus_ite_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)

    def __str__(self):
        return self.cus_ite_nome

    def cus_ite_imagem_tag(self):
        if self.cus_ite_imagem:
            return mark_safe('<img src="%s" style="width: 125px; height:150px;" />' % self.cus_ite_imagem.url)
        else:
            return 'Sem imagem!'

    cus_ite_imagem_tag.short_description = ''

    def cus_ite_imagem_tag_small(self):
        if self.cus_ite_imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % self.cus_ite_imagem.url)
        else:
            return 'Sem imagem!'

    cus_ite_imagem_tag_small.short_description = ''

    def clean(self):

        self.cus_ite_nome = self.cus_ite_nome.upper()

        # Verificando se já foi cadastrado registro
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCustoItem.objects.filter(cus_ite_nome=self.cus_ite_nome).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCustoItem.objects.filter(cus_ite_nome=self.cus_ite_nome).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Item ' + self.cus_ite_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '     Custo Variável - Item'
        verbose_name_plural = '    Custo Variável - Itens'
        ordering = ['cus_ite_nome']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando o cenário
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbCustoItem)

        # Vamos salvar o super save
        super(TbCustoItem, self).save(*args, **kwargs)

class TbCustoItemPreco(models.Model):
    class ItemPrecoMoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    cus_ite_pre_item = models.ForeignKey(TbCustoItem, on_delete=models.CASCADE, verbose_name='Item de Custo')
    cus_ite_pre_unidade_producao = models.ForeignKey(TbUnidadeProducao, on_delete=models.CASCADE,  verbose_name='Unidade de Produção')
    cus_ite_pre_validado = models.BooleanField(blank=False, null=False, default=False, verbose_name='Validado')
    cus_ite_pre_custo_variavel_adiconado = models.ForeignKey(TbCustoVariavelAdicionado, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Custo Var. Adic. CF')
    cus_ite_pre_indicador_preco = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Indicador do Preço', related_name='cus_ite_pre_indicador_preco')
    cus_ite_pre_moeda_preco = models.CharField(max_length=3, choices=ItemPrecoMoedaChoices.choices, verbose_name='Moeda do Preço')
    cus_ite_pre_indicador_inbound = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Indicador do Inbound', related_name='cus_ite_pre_indicador_inbound')
    cus_ite_pre_moeda_inbound = models.CharField(max_length=3, choices=ItemPrecoMoedaChoices.choices, verbose_name='Moeda do Inbound')
    cus_ite_pre_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    valor_inicial_1 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inicial: Preço')
    valor_inicial_2 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inicial: Inbound')
    valor_inicial_3 = models.IntegerField(verbose_name='Pagamento (dias)')
    valor_inicial_4 = models.IntegerField(verbose_name='Pagamento (dias)')
    valor_inicial_5 = models.IntegerField(verbose_name='Estoque (dias)')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.cus_ite_pre_item) + ' / ' + str(self.cus_ite_pre_unidade_producao)

    # Campo para mostrar a imagem do item de custo
    def cus_ite_imagem_tag_small(self):
        item_imagem = TbCustoItem.objects.get(id=self.cus_ite_pre_item_id).cus_ite_imagem
        if item_imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % item_imagem.url)
        else:
            return 'Sem imagem!'
    cus_ite_imagem_tag_small.short_description = 'Imagem'
    cus_ite_imagem_tag_small.allow_tags = True

    # Campo para mostrar a unidade do item de custo (kg, m3, un, etc)
    def cus_item_unidade(self):
        item_unidade = TbCustoItem.objects.get(id=self.cus_ite_pre_item_id).cus_ite_unidade
        if item_unidade:
            return item_unidade
        else:
            return '?'

    cus_item_unidade.short_description = 'Unidade'

    # Campo para mostrar o tipo de custo do item de custo
    def cus_item_tipo(self):
        item_tipo = TbCustoItem.objects.get(id=self.cus_ite_pre_item_id).cus_ite_tipo
        if item_tipo:
            return item_tipo
        else:
            return '?'

    cus_item_tipo.short_description = 'Tipo'

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Montando restrições
        if self.cus_ite_pre_moeda_preco != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.cus_ite_pre_moeda_preco)
            if not achou:
                raise ValidationError(
                    'A moeda ' + self.cus_ite_pre_moeda_preco + ' não cadastrada na Tabela Taxas de Câmbio!')
        if self.cus_ite_pre_moeda_inbound != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.cus_ite_pre_moeda_inbound)
            if not achou:
                raise ValidationError(
                    'A moeda ' + self.cus_ite_pre_moeda_inbound + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo item/unidade de produção

        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbCustoItemPreco.objects.filter(cus_ite_pre_item=self.cus_ite_pre_item, cus_ite_pre_unidade_producao=self.cus_ite_pre_unidade_producao, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbCustoItemPreco.objects.filter(cus_ite_pre_item=self.cus_ite_pre_item,
                                                    cus_ite_pre_unidade_producao=self.cus_ite_pre_unidade_producao, tbcenarios_id=ativo).exclude(id=self.pk).count()

            # Vamos ver se esse item está sendo utilizado em planta diferente da informada (self.cus_ite_pre_unidade_producao)
            # Vamos fazer isso usando o sp verifica_item_unidade_producao

            cursor = connection.cursor()
            # Montando a expressão sql para rodar o Stored Procedure verifica_filha
            sql = "CALL public.cus_ite_pre_unidade_producao(" + str(self.id) + ", " + str(self.cus_ite_pre_unidade_producao_id) + ", true)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()
            if not retorno:
                raise ValidationError('A unidade de produção não pode ser alterada. Favor cancelar alteração!')

        if count >= 1:
            raise ValidationError('Item: ' + str(self.cus_ite_pre_item) + '/' + 'Unidade de Produção: ' + str(
                self.cus_ite_pre_unidade_producao) + ' já cadastrado!')

    def periodo_inicio(self): # Mostra o periodo inicio do custo variável adicionado
        valor_retorno = ''
        if self.cus_ite_pre_custo_variavel_adiconado is not None:
            #print('achei')
            #print(self.cus_ite_pre_custo_variavel_adiconado)
            #print(self.cus_ite_pre_custo_variavel_adiconado_id)
            valor_retorno = TbCustoVariavelAdicionado.objects.get(id=self.cus_ite_pre_custo_variavel_adiconado_id).cus_var_adi_ano_mes_inicio
            #print(valor_retorno)

        return valor_retorno
    periodo_inicio.short_description = 'Período Inicio'

    def periodo_fim(self):  # Mostra o periodo fim do custo variável adicionado
        valor_retorno = ''
        if self.cus_ite_pre_custo_variavel_adiconado is not None:
            valor_retorno = TbCustoVariavelAdicionado.objects.get(id=self.cus_ite_pre_custo_variavel_adiconado_id).cus_var_adi_ano_mes_fim

        return valor_retorno

    periodo_fim.short_description = 'Período Fim'

    def valor_custo_variavel_adicionado(self): # Mostra o valor do custo variavel adicionado
        valor_retorno = 0
        if self.cus_ite_pre_custo_variavel_adiconado is not None:
            valor_retorno = TbCustoVariavelAdicionado.objects.get(id=self.cus_ite_pre_custo_variavel_adiconado_id).cus_var_adi_custo_variavel_adicionado_material + TbCustoVariavelAdicionado.objects.get(id=self.cus_ite_pre_custo_variavel_adiconado_id).cus_var_adi_custo_variavel_adicionado_ggf

        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    valor_custo_variavel_adicionado.short_description = 'Valor Custo Var. Adicionado'

    def preco_medio(self):
        cursor = connection.cursor()
        # Expressão SQL
        sql = "select avg(dau_valor_1) from tabelas_tbcustoitemprecodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()
        if retorno != None:
            # Vamos formatar o valor no padrão brasileiro
            retorno = locale.format_string('%.2f', retorno, True)
            return retorno
        else:
            return ''

    preco_medio.short_description = 'Preço Médio'

    class Meta:
        verbose_name = '    Custo Variável - Preço'
        verbose_name_plural = '    Custo Variável - Preços'
        ordering = ['cus_ite_pre_item']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbCustoItemPreco)

        # Vamos salvar o super save
        super(TbCustoItemPreco, self).save(*args, **kwargs)

        # Essa tabela tem indicadores para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_5_Com_Indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.cus_ite_pre_indicador_preco_id is not None and self.cus_ite_pre_indicador_preco_id != '':
            id_indicador_1 = int(self.cus_ite_pre_indicador_preco_id)
        else:
            id_indicador_1 = int(0)

        if self.cus_ite_pre_indicador_inbound_id is not None and self.cus_ite_pre_indicador_inbound_id != '':
            id_indicador_2 = int(self.cus_ite_pre_indicador_inbound_id)
        else:
            id_indicador_2 = int(0)

        # Atenção: o sp verifica_filha_5_com_indicador só considera os valores iniciais  no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_5_com_indicador('tabelas_" + "tbcustoitempreco" + "daugther', " + str(self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial_1) + ", " + str(self.valor_inicial_2) + ", " + str(int(self.valor_inicial_3)) + ", " + str(int(self.valor_inicial_4)) + ", " + str(int(self.valor_inicial_5)) + ", " + str(id_indicador_1) + ", " + str(id_indicador_2) + ")"

        cursor.execute(sql)
        cursor.close()

class TbCustoItemPrecoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Preço')
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Valor Inbound')
    dau_valor_3 = models.IntegerField(verbose_name='Pagamento (dias)')
    dau_valor_4 = models.IntegerField(verbose_name='Pagamento (dias)')
    dau_valor_5 = models.IntegerField(verbose_name='Estoque (dias)')
    mae = models.ForeignKey(TbCustoItemPreco, on_delete=models.CASCADE, verbose_name='Mãe')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

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

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbCustoItemPrecoDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicadores para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_2_Com_Indicador
        id_indicador_1 = 0
        id_indicador_2 = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbCustoItemPreco.objects.get(pk=self.mae_id)
        if campo_mae.cus_ite_pre_indicador_preco_id is not None:
            id_indicador_1 = int(campo_mae.cus_ite_pre_indicador_preco_id)

        if campo_mae.cus_ite_pre_indicador_inbound_id is not None:
            id_indicador_2 = int(campo_mae.cus_ite_pre_indicador_inbound_id)

        # Atenção: o sp Verifica_Filha_5_Com_Indicador só considera os valores iniciais no caso de não termos nenhuma filha
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)
        sql = "call public.verifica_filha_5_com_indicador('tabelas_" + "tbcustoitempreco" + "daugther', " + str(campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", " + str(campo_mae.valor_inicial_1) + ", " + str(campo_mae.valor_inicial_2) + ", " + str(int(campo_mae.valor_inicial_3)) + ", " + str(int(campo_mae.valor_inicial_4)) + ", " + str(int(campo_mae.valor_inicial_5)) + ", " + str(id_indicador_1) + ", " + str(id_indicador_2) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = 'Valores Previstos'
        verbose_name_plural = 'Valores Previstos'
        ordering = ['dau_order']

class TbTipoProducao(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    tip_nome = models.CharField(max_length=50, null=False, blank=False, verbose_name='Nome')
    tip_imagem = models.ImageField(upload_to='tabelas', null=True, blank=True, verbose_name='Imagem')
    tip_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)

    def __str__(self):
        return self.tip_nome

    def tip_imagem_tag(self):
        if self.tip_imagem:
            return mark_safe('<img src="%s" style="width: 250px; height:120px;" />' % self.tip_imagem.url)
        else:
            return 'Sem imagem!'

    tip_imagem_tag.short_description = ''

    def clean(self):

        self.tip_nome = self.tip_nome.upper()

        # Verificando se já foi cadastrado registro com o mesmo nome para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbTipoProducao.objects.filter(tip_nome=self.tip_nome).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbTipoProducao.objects.filter(tip_nome=self.tip_nome).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Tipo de Produção ' + self.tip_nome + ' já cadastrado!')

    class Meta:
        verbose_name = '   Tipo de Produção'
        verbose_name_plural = '   Tipos de Produção'
        ordering = ['tip_nome']

class TbFamiliaProduto(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    fam_pro_codigo = models.CharField(max_length=35, null=False, blank=False, verbose_name='Família')

    def __str__(self):
        return self.fam_pro_codigo

    def clean(self):

        self.fam_pro_codigo = self.fam_pro_codigo.upper()

        # Verificando se já foi cadastrado registro com o mesmo código de família do produto
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbFamiliaProduto.objects.filter(fam_pro_codigo=self.fam_pro_codigo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbFamiliaProduto.objects.filter(fam_pro_codigo=self.fam_pro_codigo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Família de Produto ' + self.fam_pro_codigo + ' já cadastrada!')

    class Meta:
        verbose_name = '  Família de Produto'
        verbose_name_plural = '  Famílias de Produtos'
        ordering = ['fam_pro_codigo']

class TbGrupoCenarios(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    gru_cen_codigo = models.CharField(max_length=35, null=False, blank=False, verbose_name='Grupo de Cenários')

    def __str__(self):
        return self.gru_cen_codigo

    def clean(self):

        self.gru_cen_codigo = self.gru_cen_codigo.upper()

        # Verificando se já foi cadastrado registro com o mesmo código de grupo de cenários
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbGrupoCenarios.objects.filter(gru_cen_codigo=self.gru_cen_codigo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbGrupoCenarios.objects.filter(gru_cen_codigo=self.gru_cen_codigo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Grupo de Cenários ' + self.gru_cen_codigo+ ' já cadastrado!')

    class Meta:
        verbose_name = ' Grupo de Cenários'
        verbose_name_plural = ' Grupos de Cenários'
        ordering = ['gru_cen_codigo']

class TbEquacaoAjustePreco(models.Model): # Independe do cenário. Não é necessário duplicar ao copiar cenário.
    class MoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    equ_aju_pre_descricao = models.CharField(max_length=40, null=False, blank=False, verbose_name='Descrição')
    equ_aju_pre_moeda = models.CharField(max_length=3, choices=MoedaChoices.choices, verbose_name='Moeda do Preço')
    equ_aju_pre_constante_a = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='A')
    equ_aju_pre_observacao_a = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_constante_b = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='B')
    equ_aju_pre_observacao_b = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_constante_c = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='C')
    equ_aju_pre_observacao_c = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_constante_d = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='D')
    equ_aju_pre_observacao_d = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_constante_e = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='E')
    equ_aju_pre_observacao_e = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_constante_f = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, default=0, verbose_name='F')
    equ_aju_pre_observacao_f = models.CharField(max_length=50, null=True, blank=True, verbose_name='')
    equ_aju_pre_formula = models.CharField(max_length=50, null=False, blank=False, verbose_name='Fórmula')
    equ_aju_pre_primeiro_titulo = models.CharField(max_length=6, null=True, blank=True, verbose_name='Títulos: Primeiro')
    equ_aju_pre_segundo_titulo = models.CharField(max_length=6, null=True, blank=True, verbose_name='Segundo')
    equ_aju_pre_var = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='Valor VAR para teste')
    equ_aju_pre_valor1 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0, verbose_name='')
    equ_aju_pre_valor2 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0, verbose_name='')

    # Atenção. Essa tabela nao tem o campo tbcenarios, pois é utilizada por todos os cenários

    def __str__(self):
        return str(self.equ_aju_pre_descricao)

    def clean(self):

        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Convertendo para maiúsculo
        self.equ_aju_pre_descricao = self.equ_aju_pre_descricao.upper()
        self.equ_aju_pre_formula = self.equ_aju_pre_formula.upper()

        # Montando restrições
        if self.equ_aju_pre_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.equ_aju_pre_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError(
                    'A moeda ' + self.get_equ_aju_pre_moeda_display() + ' não foi cadastrada na Tabela Taxas de Câmbio. Favor cadastrar!')

        # Verificando se já foi cadastrado a mesma descrição da equação

        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbEquacaoAjustePreco.objects.filter(equ_aju_pre_descricao=self.equ_aju_pre_descricao).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbEquacaoAjustePreco.objects.filter(equ_aju_pre_descricao=self.equ_aju_pre_descricao).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Equação ' + str(self.equ_aju_pre_descricao) + ' já cadastrada!')

        # Vamos calcular de acordo com valor informado para VAR teste.
        if self.equ_aju_pre_formula is not None:  # Foi informado fórmula
            # Vamos calcular usando o valor de VAR informado para teste
            var_ajustado = str(self.equ_aju_pre_var).strip()
            formula_ajustada = self.equ_aju_pre_formula.replace('VAR', var_ajustado)

            if self.equ_aju_pre_constante_a != 0:
                ajustado = str(self.equ_aju_pre_constante_a).strip()
                formula_ajustada = formula_ajustada.replace('A', ajustado)

            if self.equ_aju_pre_constante_b != 0:
                ajustado = str(self.equ_aju_pre_constante_b).strip()
                formula_ajustada = formula_ajustada.replace('B', ajustado)

            if self.equ_aju_pre_constante_c != 0:
                ajustado = str(self.equ_aju_pre_constante_c).strip()
                formula_ajustada = formula_ajustada.replace('C', ajustado)

            if self.equ_aju_pre_constante_d != 0:
                ajustado = str(self.equ_aju_pre_constante_d).strip()
                formula_ajustada = formula_ajustada.replace('D', ajustado)

            if self.equ_aju_pre_constante_e != 0:
                ajustado = str(self.equ_aju_pre_constante_e).strip()
                formula_ajustada = formula_ajustada.replace('E', ajustado)

            if self.equ_aju_pre_constante_f != 0:
                ajustado = str(self.equ_aju_pre_constante_f).strip()
                formula_ajustada = formula_ajustada.replace('F', ajustado)

            try:
                self.equ_aju_pre_valor1 = round(eval(formula_ajustada), 2)

                # Temos que pegar a taxa de câmbio para passar para a moeda que a empresa está utilizando. Iremos considerar
                # câmbio para o primeiro período (dau_order = 1)

                # Primeiro verificando se a moeda informada para a equação é igual à moeda adotada pela empresa.
                # Vamos ver a moeda que a empresa está usando
                # Temos que primeiro ver se a tabela TbEmpresa existe no banco de dados
                all_tables = connection.introspection.table_names()
                if 'parameters_tbempresa' in all_tables:
                    # Vamos ver se tem registro
                    if TbEmpresa.objects.filter(id=1).count() == 1:
                        moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
                    else:
                        moeda_empresa = ''
                else:
                    moeda_empresa = ''

                if moeda_empresa == self.equ_aju_pre_moeda:
                    # É igual. Câmbio igual a 1
                    taxa_cambio = 1
                else:
                    # Não é igual.
                    # Vamos pegar o cenário ativo
                    ativo = TbCenarios.objects.get(cen_ativo=True).id

                    # Pegando o id da mâe na tabela mãe do câmbio
                    id_mae = TbCambio.objects.get(tbcenarios_id=ativo, cam_moeda=self.equ_aju_pre_moeda).id

                    # Pegando o valor da taxa de câmbio na tabela filha
                    taxa_cambio = TbCambioDaugther.objects.get(tbcenarios_id=ativo, mae_id=id_mae, dau_order=1).dau_valor

                # Vamos agora salvar o valor na moeda da empresa
                self.equ_aju_pre_valor2 = round(Decimal(self.equ_aju_pre_valor1) * taxa_cambio, 2)

            except SyntaxError:
                raise ValidationError('Erro na fórmula ou nos valores de A até F informados. Favor verificar.')
                self.equ_aju_pre_valor1 = 0
                self.equ_aju_pre_valor2 = 0

            except ZeroDivisionError:
                raise ValidationError('Divisão por zero na fórmula. Favor verificar.')
                self.equ_aju_pre_valor1 = 0
                self.equ_aju_pre_valor2 = 0

    class Meta:
        verbose_name = 'Equação Ajuste Preço'
        verbose_name_plural = 'Equações Ajustes Preços'
        ordering = ['equ_aju_pre_descricao']

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbEquacaoAjustePreco, self).save(*args, **kwargs)