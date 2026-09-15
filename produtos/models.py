from django.db import models
from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.utils.translation import gettext_lazy as _
from django.utils.safestring import mark_safe
from django.core.exceptions import ValidationError
from tabelas.models import TbFamiliaProduto, TbMercado, TbEquacaoAjustePreco, TbEmpresa, TbCambio, atualiza_cenario, \
    TbUnidadeProducao, TbIndicadores
from parameters.models import TbCenarios
import fluxos

# Para permitir mostrar valores numéricos no padrão Brasil
# Estou usando nos campos numéricos criados no model
import locale

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


# Vai ser executado após o save para algumas tabelas
def verifica_filha(sender, instance, **kwargs):  # passa 01 valor inicial
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha('produtos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_2(sender, instance, **kwargs):  # passa 02 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_2
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "exec dbo.Verifica_Filha_2 'produtos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", '" + str(instance.valor_inicial_1) + "', '" + str(
        instance.valor_inicial_2) + "'"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_3(sender, instance, **kwargs):  # passa 03 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_3('produtos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", " + str(
        instance.valor_inicial_2) + ", " + str(instance.valor_inicial_3) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_4(sender, instance, **kwargs):  # passa 04 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_4
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_4('produtos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", " + str(
        instance.valor_inicial_2) + ", " + str(instance.valor_inicial_3) + ", " + str(instance.valor_inicial_4) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_3_valor_1_bit(sender, instance, **kwargs):  # passa 03 valores iniciais. Valor Inicial 1 é bit
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3_Valor_1_Bit
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "exec dbo.Verifica_Filha_3_Valor_1_Bit 'produtos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ", " + str(instance.valor_inicial_1) + ", '" + str(
        instance.valor_inicial_2) + "', '" + str(instance.valor_inicial_3) + "'"
    cursor.execute(sql)
    cursor.close()


class TbProdutos(models.Model):  # NÃO TEM CAMPO DO CENÁRIO. SERÁ USADO POR TODOS OS CENÁRIOS

    class UnidadeProducaoChoices(models.TextChoices):
        un = ('un', 'un')
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
        Mwh = ('Mwh', 'Mwh')

    pro_codigo = models.CharField(max_length=25, verbose_name=_('Código'))
    pro_descricao = models.CharField(max_length=51, verbose_name=_('Descrição'))
    pro_ativo = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Ativo'))
    pro_unidade_producao = models.CharField(max_length=3, choices=UnidadeProducaoChoices.choices,
                                            verbose_name=_('Unidade'))
    pro_familia = models.ForeignKey(TbFamiliaProduto, on_delete=models.CASCADE, verbose_name=_('Família'))
    pro_imagem = models.ImageField(upload_to='produtos', null=True, blank=True, verbose_name=_('Imagem'))
    pro_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.pro_codigo

    def pro_imagem_tag(self):
        if self.pro_imagem:
            return mark_safe('<img src="%s" style="width: 270px; height:150px;" />' % self.pro_imagem.url)
        else:
            return 'Sem imagem!'

    pro_imagem_tag.short_description = _('')

    # Vamos criar um campo para mostrar o total de mercados cadastrados para o produto
    def total_mercados(self):
        retorno = TbProdutoMercadoPreco.objects.filter(pro_mer_pre_produto_id=self.id).count()
        return retorno

    total_mercados.short_description = _('Mercados')

    # Vamos criar um campo para mostrar o total de fluxos de produção cadastrados para o produto
    def total_fluxos(self):
        retorno = fluxos.models.TbFluxoProducao.objects.filter(flu_pro_produto_id=self.id).count()
        return retorno

    total_fluxos.short_description = _('Fluxos Total')

    # Vamos criar um campo para mostrar o total de fluxos de produção ativos cadastrados para o produto
    def total_fluxos_ativos(self):
        retorno = fluxos.models.TbFluxoProducao.objects.filter(flu_pro_produto_id=self.id, flu_pro_ativo=True).count()
        return retorno

    total_fluxos_ativos.short_description = _('Fluxos Ativos')

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Verificando se já foi cadastrado registro com o mesmo código do produto.
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbProdutos.objects.filter(pro_codigo=self.pro_codigo, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbProdutos.objects.filter(pro_codigo=self.pro_codigo, tbcenarios_id=ativo).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Produto ' + self.pro_codigo + ' já cadastrado para esse cenário!')

    class Meta:
        verbose_name = _('  Produto')
        verbose_name_plural = _('  Produtos')
        ordering = ['pro_codigo']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbProdutos)

        # Vamos salvar o super save
        super(TbProdutos, self).save(*args, **kwargs)


class TbProdutoMercadoPreco(models.Model):
    class ProMerPre(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    pro_mer_pre_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, verbose_name=_('Produto'))
    pro_mer_pre_mercado = models.ForeignKey(TbMercado, on_delete=models.CASCADE, verbose_name=_('Mercado'))
    pro_mer_pre_codigo_interno = models.CharField(max_length=8, blank=True, null=True, verbose_name=_('Cód. Interno'))
    pro_mer_pre_descricao_interna = models.CharField(max_length=51, blank=True, null=True,
                                                     verbose_name=_('Desc. Interna'))
    pro_mer_pre_estoque = models.IntegerField(verbose_name=_('Estoque (dias venda)'))
    pro_mer_pre_validado = models.BooleanField(default=0, verbose_name=_('Validado'))
    pro_mer_pre_ativo = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Ativo'))
    pro_mer_pre_indicador = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT,
                                              verbose_name=_('Indicador Preço'), related_name='pro_mer_pre_indicador')
    pro_mer_pre_indicador_vol_min = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT,
                                                      verbose_name=_('Indicador Volume: Mín.'),
                                                      related_name='pro_mer_pre_indicador_vol_min')
    pro_mer_pre_indicador_vol_max = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT,
                                                      verbose_name=_('Máx.'),
                                                      related_name='pro_mer_pre_indicador_vol_max')
    pro_mer_pre_moeda = models.CharField(max_length=3, choices=ProMerPre.choices, null=False, blank=False,
                                         default='BRL', verbose_name=_('Moeda'))
    pro_mer_pre_outbound = models.BooleanField(blank=False, null=False, default=True,
                                               verbose_name=_('Considerar Outbound (se existir...)'))
    pro_mer_pre_equacao = models.ForeignKey(TbEquacaoAjustePreco, null=True, blank=True, on_delete=models.CASCADE,
                                            verbose_name=_('Equação de Preço'))
    pro_mer_pre_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    pro_mer_pre_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name=_('Fonte'))
    valor_inicial_1 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Volume Mín. Inicial'))
    valor_inicial_2 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Volume Máx. Inicial'))
    valor_inicial_3 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Preço Inicial'))
    valor_inicial_4 = models.IntegerField(verbose_name=_('Pagamento (dias)'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return 'Produto/Mercado: ' + str(self.pro_mer_pre_produto) + '/' + str(self.pro_mer_pre_mercado)

    # Campo para mostrar a imagem do produto
    def produto_imagem_tag_small(self):
        imagem = TbProdutos.objects.get(id=self.pro_mer_pre_produto_id).pro_imagem
        if imagem:  # Se existir a imagem no banco de dados
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    produto_imagem_tag_small.short_description = _('Imagem')
    produto_imagem_tag_small.allow_tags = True

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Montando restrições
        if self.pro_mer_pre_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.pro_mer_pre_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError(
                    'A moeda ' + self.pro_mer_pre_moeda + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo produto e mercado para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbProdutoMercadoPreco.objects.filter(pro_mer_pre_produto_id=self.pro_mer_pre_produto,
                                                         pro_mer_pre_mercado_id=self.pro_mer_pre_mercado,
                                                         tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbProdutoMercadoPreco.objects.filter(pro_mer_pre_produto=self.pro_mer_pre_produto,
                                                         pro_mer_pre_mercado=self.pro_mer_pre_mercado,
                                                         tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Produto: ' + str(self.pro_mer_pre_produto) + ' / ' + 'Mercado: ' + str(
                self.pro_mer_pre_mercado) + ' já cadastrado!')

    # Vamos criar um campo para mostrar se tem outbound para o produto/mercado
    def tem_outbound(self):
        retorno = TbMercadoOutbound.objects.filter(mer_out_mercado_id=self.pro_mer_pre_mercado_id,
                                                   mer_out_produto_id=self.pro_mer_pre_produto_id).count()
        if retorno == 0:
            return False
        else:
            return True

    tem_outbound.short_description = _('Existe Outbound')
    # Para mostrar um icon e não True/False
    tem_outbound.boolean = True

    class Meta:
        verbose_name = _('  Volume/Preço por Mercado')
        verbose_name_plural = _('  Volume/Preços por Mercado')
        ordering = ['pro_mer_pre_produto', 'pro_mer_pre_mercado']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbProdutoMercadoPreco)

        # Se foi informado equação de preço, temos que limpar o indicador de preço. O que

        # Vamos salvar o super save
        super(TbProdutoMercadoPreco, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        # Indicador do preço
        if self.pro_mer_pre_indicador is not None and self.pro_mer_pre_indicador != '':
            id_indicador = int(self.pro_mer_pre_indicador_id)
        else:
            id_indicador = int(0)

        # Indicador do volume mínimo
        if self.pro_mer_pre_indicador_vol_min is not None and self.pro_mer_pre_indicador_vol_min != '':
            id_indicador_1 = int(self.pro_mer_pre_indicador_vol_min_id)
        else:
            id_indicador_1 = int(0)

        # Indicador do volume máximo
        if self.pro_mer_pre_indicador_vol_max is not None and self.pro_mer_pre_indicador_vol_max != '':
            id_indicador_2 = int(self.pro_mer_pre_indicador_vol_max_id)
        else:
            id_indicador_2 = int(0)

        # Atenção: o sp vverifica_filha_4_com_indicador só considera os valores iniciais no caso de não termos nenhuma filha.
        # Essa procedure só é usada por essa tabela: TbTbProdutoMercadoPreco
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_4_com_indicador('produtos_" + "TbProdutoMercadoPreco" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial_1) + ", " + str(
            self.valor_inicial_2) + ", " + str(self.valor_inicial_3) + ", " + str(self.valor_inicial_4) + ", " + str(
            id_indicador) + ", " + str(id_indicador_1) + ", " + str(id_indicador_2) + ")"
        cursor.execute(sql)
        cursor.close()


# Signals a serem executados na tabela TbProdutoMercadoPreco)
# post_save.connect(verifica_filha_4, sender=TbProdutoMercadoPreco)

class TbProdutoMercadoPrecoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Volume Mínimo'))
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Volume Máximo'))
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Preço'))
    dau_valor_4 = models.IntegerField(verbose_name=_('Pagamento (dias)'))
    mae = models.ForeignKey(TbProdutoMercadoPreco, on_delete=models.CASCADE, verbose_name=_('Produto Mercado Preço'))
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

    # Vamos criar um campo para mostrar o volume de vendas sugerido pelo SPS após a otimização
    def vendas(self):
        # Vamos pegar o id do produto e do mercado na tabela mãe
        id_produto = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_produto_id
        id_mercado = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_mercado_id
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o preço do produto
            sql = "call public.atualiza_preco_produto_mercado_order(" + str(self.tbcenarios_id) + ", " + str(
                id_produto) + ", " + str(
                id_mercado) + ", " + str(self.dau_order) + ",  0 , 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    # Vamos criar um campo para mostrar o preço na moeda informada
    def preco_moeda(self):
        # Vamos pegar o id do produto e do mercado na tabela mãe
        id_produto = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_produto_id
        id_mercado = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_mercado_id
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o preço do produto
            sql = "call public.atualiza_preco_produto_mercado_order(" + str(self.tbcenarios_id) + ", " + str(
                id_produto) + ", " + str(id_mercado) + ", " + str(self.dau_order) + ",  0 , 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    def preco_moeda_empresa(self):
        # Vamos pegar o id do produto e do mercado na tabela mãe
        id_produto = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_produto_id
        id_mercado = TbProdutoMercadoPreco.objects.get(id=self.mae_id).pro_mer_pre_mercado_id
        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            # Vamos montar uma expressão SQL para calcular o preço do produto
            sql = "call public.atualiza_preco_produto_mercado_order(" + str(self.tbcenarios_id) + ", " + str(
                id_produto) + ", " + str(
                id_mercado) + ", " + str(self.dau_order) + ",  1 , 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbProdutoMercadoPrecoDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicador para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_Com_Indicador
        # Indicador do preço
        id_indicador = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbProdutoMercadoPreco.objects.get(pk=self.mae_id)
        if campo_mae.pro_mer_pre_indicador_id is not None:
            id_indicador = campo_mae.pro_mer_pre_indicador_id

        # Indicador do volume mínimo
        id_indicador_1 = 0
        if campo_mae.pro_mer_pre_indicador_vol_min_id is not None:
            id_indicador_1 = campo_mae.pro_mer_pre_indicador_vol_min_id

        # Indicador do volume máximo
        id_indicador_2 = 0
        if campo_mae.pro_mer_pre_indicador_vol_max_id is not None:
            id_indicador_2 = campo_mae.pro_mer_pre_indicador_vol_max_id

        # Atenção: o sp Verifica_Filha_Com_Indicador só considera os valores iniciais no caso de não termos nenhuma filha
        # Essa procedure só é usada por essa tabela: TbTbProdutoMercadoPreco
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)
        sql = "call public.verifica_filha_4_com_indicador('produtos_" + "TbProdutoMercadoPreco" + "daugther', " + str(
            campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", " + str(campo_mae.valor_inicial_1) + ", " + str(
            campo_mae.valor_inicial_2) + ", " + str(campo_mae.valor_inicial_3) + ", " + str(
            campo_mae.valor_inicial_4) + ", " + str(id_indicador) + ", " + str(id_indicador_1) + ", " + str(
            id_indicador_2) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = _('Volume Mín/Máx - Preço Previsto')
        verbose_name_plural = _('Volumes Mín/Máx - Preços Previstos')
        ordering = ['dau_order']


class TbMercadoOutbound(models.Model):
    class MerOutChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    mer_out_mercado = models.ForeignKey(TbMercado, on_delete=models.CASCADE, verbose_name=_('Mercado'))
    mer_out_unidade = models.ForeignKey(TbUnidadeProducao, on_delete=models.CASCADE,
                                        verbose_name=_('Planta de Produção'))
    mer_out_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, verbose_name=_('Produto'))
    mer_out_indicador = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name=_('Indicador'))
    mer_out_moeda = models.CharField(max_length=3, choices=MerOutChoices.choices, null=False, blank=False,
                                     default='BRL', verbose_name=_('Moeda'))
    mer_out_observacao = models.TextField(max_length=80, verbose_name=_('Observação'), blank=True, null=True)
    valor_inicial = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Valor Inicial'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return 'Mercado: ' + str(self.mer_out_mercado) + '/' + 'Unidade de Produção: ' + str(
            self.mer_out_unidade) + '/' + 'Produto: ' + str(self.mer_out_produto)

    def clean(self):
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id

        # Montando restrições
        if self.mer_out_moeda != TbEmpresa.objects.get(id=1).emp_moeda:
            # Vamos ver se foi colocada na tabela de cambio
            achou = TbCambio.objects.filter(cam_moeda=self.mer_out_moeda, tbcenarios_id=ativo)
            if not achou:
                raise ValidationError('A moeda ' + self.mer_out_moeda + ' não cadastrada na Tabela Taxas de Câmbio!')

        # Verificando se já foi cadastrado registro com o mesmo mercado, unidade e produto para o cenário
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbMercadoOutbound.objects.filter(mer_out_mercado=self.mer_out_mercado,
                                                     mer_out_unidade=self.mer_out_unidade,
                                                     mer_out_produto=self.mer_out_produto, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbMercadoOutbound.objects.filter(mer_out_mercado=self.mer_out_mercado,
                                                     mer_out_unidade=self.mer_out_unidade,
                                                     mer_out_produto=self.mer_out_produto, tbcenarios_id=ativo).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Mercado: ' + str(self.mer_out_mercado) + ' / ' + 'Unidade de Produção: ' + str(
                self.mer_out_unidade) + ' / ' + 'Produto: ' + str(
                self.mer_out_produto) + ' já cadastrados!')

    class Meta:
        verbose_name = _('Custo Outbound')
        verbose_name_plural = _('Custos Outbound')
        ordering = ['mer_out_mercado']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbMercadoOutbound)

        # Vamos salvar o super save
        super(TbMercadoOutbound, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.mer_out_indicador_id is not None and self.mer_out_indicador_id != '':
            id_indicador = int(self.mer_out_indicador_id)
        else:
            id_indicador = int(0)

        # Atenção: o sp verifica_filha_com_indicador só considera os valores iniciais no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_com_indicador('produtos_" + "TbMercadoOutbound" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial) + ", " + str(id_indicador) + ")"
        cursor.execute(sql)
        cursor.close()


# Signals a serem executados na tabela TbMercadoOutbound)
post_save.connect(verifica_filha, sender=TbMercadoOutbound)


class TbMercadoOutboundDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Valor'))
    mae = models.ForeignKey(TbMercadoOutbound, on_delete=models.CASCADE, verbose_name=_('Mercado Outbound'))
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

    def save(self, *args, **kwargs):

        # Vamos salvar o super save
        super(TbMercadoOutboundDaugther, self).save(*args, **kwargs)

        # Essa tabela tem indicador para ajustar os preços. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha_Com_Indicador
        id_indicador = 0
        # Vamos pegar os campos da mãe
        campo_mae = TbMercadoOutbound.objects.get(pk=self.mae_id)
        if campo_mae.mer_out_indicador_id is not None:
            id_indicador = campo_mae.mer_out_indicador_id

        # Atenção: o sp Verifica_Filha_Com_Indicador só considera os valores iniciais no caso de não termos nenhuma filha
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)
        sql = "call public.verifica_filha_com_indicador('produtos_" + "TbMercadoOutbound" + "daugther', " + str(
            campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", " + str(campo_mae.valor_inicial) + ", " + str(
            id_indicador) + ")"

        cursor.execute(sql)
        cursor.close()

    class Meta:
        verbose_name = _('Valores Previstos')
        verbose_name_plural = _('Valores Previstos')
        ordering = ['dau_order']





