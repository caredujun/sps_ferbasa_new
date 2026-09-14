from django.core.exceptions import ValidationError
from django.db import models, connection
from django.db import models
from django.db import transaction
from django.contrib import admin, messages
import locale, datetime
from django.db.models.signals import post_save
from django.utils.html import format_html
from parameters.models import TbCenarios, TbEmpresa
from parameters.contexto_usuario import get_usuario_atual
import pandas as pd
import statsmodels.api as sm
from decimal import Decimal


#from fluxos.models import TbFluxoConsumoPadrao

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


def _atribuir_empresa_se_necessario(instance):
    """
    🌟 NOVO (multi-empresa, Parte 3): preenche "empresa" automaticamente
    na CRIAÇÃO de qualquer registro dessas tabelas independentes de
    cenário -- a partir da empresa efetiva do usuário logado. Mesmo
    mecanismo já usado em tabelas/models.py e parameters/models.py.
    """
    if instance.pk is None and instance.empresa_id is None:
        usuario = get_usuario_atual()
        if usuario is not None:
            perfil = getattr(usuario, 'perfilusuario', None)
            if perfil is not None:
                instance.empresa_id = perfil.empresa_efetiva_id()


def custom_titled_filter(title):
    class Wrapper(admin.FieldListFilter):
        def __new__(cls, *args, **kwargs):
            instance = admin.FieldListFilter.create(*args, **kwargs)
            instance.title = title
            return instance

    return Wrapper


# Não estamos usnado a class abaixo.Deixamos só para manter o código
class BonificacaoFilter(admin.SimpleListFilter):
    title = 'Bonificação'
    parameter_name = 'Bonificação'

    def lookups(self, request, model_admin):
        return (
            ('Sim', 'Sim'),
            ('Não', 'Não'),
        )

    def queryset(self, request, queryset):
        value = self.value()

        # Vamos criar uma lista de id dos itens de consumo que foram lançados como bonificação na tabela de produção mensal
        lista_id_bonificacao_sim = []
        lista_id_bonificacao_nao = []
        for id_item_consumo in TbItensConsumo.objects.all():
            if TbProducaoMensal.objects.filter(pro_men_item_consumo_id=id_item_consumo.id, pro_men_qtde_consumo__lt=0).count() > 0:
                lista_id_bonificacao_sim.append(id_item_consumo.id)
            else:
                lista_id_bonificacao_nao.append(id_item_consumo.id)

        if value == 'Sim':
            # Vamos selecionar os id que estão na lista_id_bonificacao_sim
            return queryset.filter(id__in=lista_id_bonificacao_sim)
        elif value == 'Não':
            # Vamos selecionar os id que estão na lista_id_bonificacao_nao
            return queryset.filter(id__in=lista_id_bonificacao_nao)
        return queryset


class TbItensConsumo(models.Model):
    ite_con_tipo_choice = (('COMPRADO', 'COMPRADO'), ('FABRICADO', 'FABRICADO'))

    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado (era global) --
    # unicidade agora fica no Meta, junto com empresa.
    ite_con_codigo = models.CharField(max_length=10, null=False, blank=False, verbose_name='Código')
    ite_con_descricao = models.CharField(max_length=60, null=False, blank=False, verbose_name='Descrição')
    ite_con_unidade = models.CharField(max_length=2, null=False, blank=False, verbose_name='Unidade')
    ite_con_tipo = models.CharField(max_length=9, choices=ite_con_tipo_choice, null=False, blank=False, verbose_name='Tipo')
    ite_subproduto = models.BooleanField(default=False, verbose_name='Subproduto')
    ite_con_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.ite_con_descricao + ' - ' + self.ite_con_codigo

    def clean(self):
        self.ite_con_codigo = self.ite_con_codigo.upper()  # Passa o código para maiusculo antes de salvar
        self.ite_con_descricao = self.ite_con_descricao.upper()  # Passa o nome para maiusculo antes de salvar
        self.ite_con_unidade = self.ite_con_unidade.upper()  # Passa a unidade para maiusculo antes de salvar
        if self.ite_con_tipo == 'COMPRADO':
            self.ite_con_produto_interno = False

    class Meta:
        verbose_name = '          Item de Consumo'
        verbose_name_plural = '          Itens de Consumo'
        ordering = ['ite_con_descricao']
        constraints = [
            models.UniqueConstraint(fields=['ite_con_codigo', 'empresa'], name='itemconsumo_codigo_unico_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def bonificacao(self):
        #Vamos ver se o item de consumo aparece como bonificação no arquivo de produção
        #Assumimos a princípio como Não
        retorno = 'Não'
        if TbProducaoMensal.objects.filter(pro_men_item_consumo_id=self.id, pro_men_qtde_consumo__lt=0).count() > 0:
            retorno = 'Sim'

        return retorno

    bonificacao.short_description = 'Bonificação'

class TbItensProducao(models.Model):
    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- unicidade
    # agora fica no Meta, junto com empresa.
    ite_pro_codigo = models.CharField(max_length=10, null=False, blank=False, verbose_name='Código')
    ite_pro_descricao = models.CharField(max_length=60, null=False, blank=False, verbose_name='Descrição')
    ite_pro_unidade = models.CharField(max_length=2, null=False, blank=False, verbose_name='Unidade')
    ite_pro_ano_mes_inicio = models.CharField(max_length=7, null=True, blank=True, verbose_name='Ano/Mês Início')
    ite_pro_ano_mes_fim = models.CharField(max_length=7, null=True, blank=True, verbose_name='Ano/Mês Fim')
    ite_pro_qtde_produzida_real = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida Real')
    ite_pro_qtde_produzida_vazio = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida Mov./Ext.')
    ite_pro_percentual_variavel_ggf = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='Perc. Variável GGF (%)')
    ite_pro_percentual_fixo_outros_ggf = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='Perc. Fixo/Outros GGF (%)')
    ite_pro_ggf_variavel_direto = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Valor GGF Variável Direto')
    ite_pro_ggf_fixo_outros_direto = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Valor GGF Fixo/Outros Direto')
    ite_pro_ggf_variavel_pi = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Valor GGF Variável Prod. Internos')
    ite_pro_ggf_fixo_outros_pi = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, verbose_name='Valor GGF Fixo/Outros Prod. Internos')
    ite_pro_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.ite_pro_descricao + ' / ' + self.ite_pro_codigo

    def qtde_produzida_total(self):
        retorno = 0
        if self.pk:
            retorno = self.ite_pro_qtde_produzida_real + self.ite_pro_qtde_produzida_vazio

        retorno = locale.format_string('%.2f', retorno, True)
        return retorno

    qtde_produzida_total.short_description = 'Qtde Produzida Total'

    def clean(self):
        self.ite_pro_codigo = self.ite_pro_codigo.upper()  # Passa o código para maiusculo antes de salvar
        self.ite_pro_descricao = self.ite_pro_descricao.upper()  # Passa o nome para maiusculo antes de salvar
        self.ite_pro_unidade = self.ite_pro_unidade.upper()  # Passa a unidade para maiusculo antes de salvar

    class Meta:
        verbose_name = '           Item de Produção'
        verbose_name_plural = '           Itens de Produção'
        ordering = ['ite_pro_descricao']
        constraints = [
            models.UniqueConstraint(fields=['ite_pro_codigo', 'empresa'], name='itemproducao_codigo_unico_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbItensProducaoDaugther(models.Model):
    item_consumo = models.ForeignKey(TbItensConsumo, on_delete=models.CASCADE, verbose_name='Item Consumo')
    qtde_consumo = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Quantidade Consumida')
    valor_material = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Valor Material')
    valor_ggf = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Valor GGF')
    mae = models.ForeignKey(TbItensProducao, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return '' #  str(self.item_consumo)

    class Meta:
        verbose_name = '          Item de Consumo'
        verbose_name_plural = '          Itens de Consumo'
        ordering = ['item_consumo']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def tipo_insumo(self):
        retorno = TbItensConsumo.objects.get(id=self.item_consumo_id).ite_con_tipo
        return retorno

    def indicador(self):
        qtde_produzida = TbItensProducao.objects.get(id=self.mae_id).ite_pro_qtde_produzida_real + TbItensProducao.objects.get(id=self.mae_id).ite_pro_qtde_produzida_vazio
        if qtde_produzida > 0 and self.qtde_consumo is not None:
            valor_retorno = self.qtde_consumo / qtde_produzida
        else:
            valor_retorno = 0
        return locale.format_string('%.4f', valor_retorno, True)


class TbItensProducaoDaugther1(models.Model):
    chave = models.TextField(verbose_name='Chave', blank=True, null=True)
    item_consumo = models.ForeignKey(TbItensConsumo, on_delete=models.CASCADE, verbose_name='Item de Consumo')
    indicador = models.DecimalField(max_digits=15, decimal_places=4, verbose_name='Indicador')
    mae = models.ForeignKey(TbItensProducao, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = 'Arvore Genealógica de Produção'
        verbose_name_plural = 'Arvore Genealógica de Produção'
        ordering = ['chave']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbEstabelecimentos(models.Model):
    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado dos dois -- vira
    # constraint no Meta, junto com empresa.
    est_codigo = models.CharField(max_length=3, null=False, blank=False, verbose_name='Código')
    est_nome = models.CharField(max_length=60, null=False, blank=False, verbose_name='Nome')
    est_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.est_nome + ' / ' + self.est_codigo

    def clean(self):
        self.est_codigo = self.est_codigo.upper()  # Passa o codigo para maiusculo antes de salvar
        self.est_nome = self.est_nome.upper()  # Passa o nome para maiusculo antes de salvar

    class Meta:
        verbose_name = '         Estabelecimento'
        verbose_name_plural = '         Estabelecimentos'
        ordering = ['est_nome']
        constraints = [
            models.UniqueConstraint(fields=['est_codigo', 'empresa'], name='estabelecimento_codigo_unico_por_empresa'),
            models.UniqueConstraint(fields=['est_nome', 'empresa'], name='estabelecimento_nome_unico_por_empresa'),
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbGruposMaquinas(models.Model):
    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- vira constraint
    # no Meta, junto com empresa.
    gru_maq_codigo = models.CharField(max_length=10, null=False, blank=False, verbose_name='Código')
    gru_maq_nome = models.CharField(max_length=60, null=False, blank=False, verbose_name='Nome')
    gru_maq_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.gru_maq_nome + ' / ' + self.gru_maq_codigo

    def clean(self):
        self.gru_maq_codigo = self.gru_maq_codigo.upper()  # Passa o codigo para maiusculo antes de salvar
        self.gru_maq_nome = self.gru_maq_nome.upper()  # Passa o nome para maiusculo antes de salvar

    class Meta:
        verbose_name = '        Grupo Máquina'
        verbose_name_plural = '        Grupos Máquinas'
        ordering = ['gru_maq_codigo']
        constraints = [
            models.UniqueConstraint(fields=['gru_maq_codigo', 'empresa'], name='grupomaquina_codigo_unico_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbContaContabil(models.Model):
    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- vira constraint
    # no Meta, junto com empresa.
    con_con_codigo = models.CharField(max_length=6, null=False, blank=False, verbose_name='Código')
    con_con_descricao = models.CharField(max_length=60, null=False, blank=False, verbose_name='Descricao')
    con_con_pessoal = models.BooleanField(blank=False, null=False, default=False, verbose_name='Conta de Pessoal')
    con_con_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.con_con_codigo + '/' + self.con_con_descricao

    def clean(self):
        self.con_con_codigo = self.con_con_codigo.upper()  # Passa o codigo para maiusculo antes de salvar
        self.con_con_descricao = self.con_con_descricao.upper()  # Passa o descricao para maiusculo antes de salvar

    class Meta:
        verbose_name = '       Conta Contábil'
        verbose_name_plural = '       Contas Contábeis'
        ordering = ['con_con_descricao']
        constraints = [
            models.UniqueConstraint(fields=['con_con_codigo', 'empresa'], name='contacontabil_codigo_unico_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbCentroCusto(models.Model):
    cen_cus_referencia_choice = (
        ('1', '<--2025'),
        ('2', '2026-->')

    )
    cen_cus_codigo = models.CharField(max_length=6, null=False, blank=False, verbose_name='Código')
    cen_cus_referencia = models.CharField(max_length=1, choices=cen_cus_referencia_choice, null=False, blank=False, verbose_name='Referência')
    cen_cus_descricao = models.CharField(max_length=60, null=False, blank=False, verbose_name='Descricao')
    cen_cus_mod = models.IntegerField(blank=False, null=False, verbose_name='Mão-de-obra Direta (MOD)', default=0)
    cen_cus_moi = models.IntegerField(blank=False, null=False, verbose_name='Mão-de-obra Indireta (MOI)', default=0)
    cen_cus_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.cen_cus_codigo + '/' + self.cen_cus_descricao

    def clean(self):
        self.cen_cus_codigo = self.cen_cus_codigo.upper()  # Passa o codigo para maiusculo antes de salvar
        self.cen_cus_descricao = self.cen_cus_descricao.upper()  # Passa o descricao para maiusculo antes de salvar

    class Meta:
        verbose_name        = '      Centro de Custo'
        verbose_name_plural = '      Centros de Custo'
        ordering = ['cen_cus_descricao']
        # 🌟 CORRIGIDO (multi-empresa): "empresa" acrescentada na
        # combinação única -- antes só (código, referência), o que
        # impediria duas empresas de terem o mesmo código+referência.
        unique_together = ('cen_cus_codigo', 'cen_cus_referencia', 'empresa')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

class TbContaContabilCentroCusto(models.Model):
    con_con_cen_cus_tipo_choice = (
                                   ('V', 'VARIÁVEL'),
                                   ('F', 'FIXO'),
                                   ('O', 'OUTRO'),
                                   ('A', 'AGUARDANDO')
                                   )
    con_con_cen_cus_conta = models.ForeignKey(TbContaContabil, on_delete=models.CASCADE, verbose_name='Conta Contábil')
    con_con_cen_cus_cc = models.ForeignKey(TbCentroCusto, on_delete=models.CASCADE, verbose_name='Centro de Custo')
    con_con_cen_cus_estabelecimento = models.ForeignKey(TbEstabelecimentos, on_delete=models.CASCADE, verbose_name='Estabelecimento')
    #con_con_cen_cus_descricao = models.CharField(max_length=80, null=False, blank=False, verbose_name='Descricao')
    con_con_cen_cus_tipo = models.CharField(max_length=8, choices=con_con_cen_cus_tipo_choice, null=False, blank=False, verbose_name='Tipo')
    con_con_cen_cus_percentual = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Percentual (%)', default=100.00)
    con_con_cen_cus_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return f"{self.con_con_cen_cus_conta} - {self.con_con_cen_cus_cc}"

    class Meta:
        verbose_name = '     Conta Contábil/Centro de Custo'
        verbose_name_plural = '     Contas Contábeis/Centros de Custo'
        unique_together = ('con_con_cen_cus_conta', 'con_con_cen_cus_cc', 'con_con_cen_cus_estabelecimento')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbProducaoMensal(models.Model):
    pro_men_ano_mes = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês')
    pro_men_estabelecimento = models.ForeignKey(TbEstabelecimentos, on_delete=models.CASCADE, verbose_name='Estabelecimento')
    pro_men_grupo_maquina = models.ForeignKey(TbGruposMaquinas, on_delete=models.CASCADE, verbose_name='Grupo Máquina')
    pro_men_ordem_producao = models.CharField(max_length=9, null=False, blank=False, verbose_name='Ordem Produção')
    pro_men_item_producao = models.ForeignKey(TbItensProducao, on_delete=models.CASCADE, verbose_name='Item Produção')
    pro_men_qtde_produzida = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='Qtde Produzida')
    pro_men_item_consumo = models.ForeignKey(TbItensConsumo, on_delete=models.CASCADE, verbose_name='Item Consumo')
    pro_men_qtde_consumo = models.DecimalField(max_digits=15, null=True, decimal_places=2, verbose_name='Qtde Consumo')
    pro_men_valor_material = models.DecimalField(max_digits=15, null=True, decimal_places=2, verbose_name='Valor Material')
    pro_men_valor_ggf = models.DecimalField(max_digits=15, null=True, decimal_places=2, verbose_name='Valor GGF')
    pro_men_valor_ultima_entrada = models.DecimalField(max_digits=10, null=True, decimal_places=2, verbose_name='Valor Última Entrada')
    pro_men_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = '    Produção Mensal'
        verbose_name_plural = '    Produção Mensal'
        ordering = ['pro_men_ano_mes', 'pro_men_item_producao']
        unique_together = ('pro_men_ano_mes', 'pro_men_estabelecimento', 'pro_men_grupo_maquina', 'pro_men_ordem_producao', 'pro_men_item_producao', 'pro_men_item_consumo')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def indicador(self):
        valor_retorno = 0
        if self.pk is not None:
            if self.pro_men_qtde_produzida != 0 and self.pro_men_qtde_consumo is not None:
                valor_retorno = self.pro_men_qtde_consumo / self.pro_men_qtde_produzida

        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.4f', valor_retorno, True)

        return valor_retorno


class TbDistribuicaoGGFMensal(models.Model):
    dis_ggf_men_ano_mes = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês')
    dis_ggf_men_estabelecimento = models.ForeignKey(TbEstabelecimentos, on_delete=models.CASCADE, verbose_name='Estabelecimento')
    dis_ggf_men_ordem_producao = models.CharField(max_length=9, null=False, blank=False, verbose_name='Ordem Produção')
    dis_ggf_men_grupo_maquina = models.ForeignKey(TbGruposMaquinas, on_delete=models.CASCADE, verbose_name='Grupo Máquina')
    dis_ggf_men_item_producao = models.ForeignKey(TbItensProducao, on_delete=models.CASCADE, verbose_name='Item Produção')
    dis_ggf_men_qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Qtde Produzida')
    dis_ggf_men_conta_cc = models.ForeignKey(TbContaContabilCentroCusto, on_delete=models.CASCADE, verbose_name='Conta Contábil/Centro de Custo')
    dis_ggf_men_valor = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor')
    dis_ggf_men_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = '   Distribuição GGF Mensal'
        verbose_name_plural = '   Distribuição GGF Mensal'
        ordering = ['dis_ggf_men_ano_mes', 'dis_ggf_men_item_producao']
        unique_together = ('dis_ggf_men_ano_mes', 'dis_ggf_men_estabelecimento', 'dis_ggf_men_grupo_maquina', 'dis_ggf_men_ordem_producao', 'dis_ggf_men_item_producao', 'dis_ggf_men_conta_cc')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def referencia_cc(self):
        id_cc = TbContaContabilCentroCusto.objects.get(id=self.dis_ggf_men_conta_cc_id).con_con_cen_cus_cc_id
        referencia_cc = TbCentroCusto.objects.get(id=id_cc).cen_cus_referencia
        if referencia_cc == '1':
            return '<--2025'
        else:
            return '2026-->'
        return id_cc
    referencia_cc.short_description = 'Referência CC'

    def valor_especifico(self):
        valor_retorno = self.dis_ggf_men_valor / self.dis_ggf_men_qtde_produzida

        # Vamos formatar o valor do retorno no padrão brasileiro
        valor_retorno = locale.format_string('%.4f', valor_retorno, True)

        return valor_retorno


class TbConsumoEspecifico(models.Model):
    class AreaResponsavelChoices(models.TextChoices):
        MET = ('MET', 'METALURGIA')
        MIN = ('MIN', 'MINERAÇÃO')
        FLO = ('FLO', 'FLORESTAL')

    class TipoEquacaolChoices(models.TextChoices):
        SOM = ('SOM', 'SOMA')
        MUL = ('MUL', 'MULTIPLICAÇÃO')

    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- vira constraint
    # no Meta, junto com empresa.
    con_esp_descricao = models.CharField(max_length=100, null=False, blank=False, verbose_name='Descrição')
    con_esp_validado = models.BooleanField(default=False, verbose_name='Validado')
    con_esp_area_responsavel = models.CharField(max_length=3, choices=AreaResponsavelChoices.choices, null=False, blank=False, verbose_name='Área Responsável')
    con_esp_criado_por = models.CharField(max_length=20, null=True, blank=True, verbose_name='Criado por')
    con_esp_alterado_por = models.CharField(max_length=20, null=True, blank=True, verbose_name='Alterado por')
    con_esp_compartilhar = models.BooleanField(default=False, verbose_name='Compartilhar')
    con_esp_ano_mes_inicio = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Início')
    con_esp_ano_mes_fim = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Fim')
    con_esp_ajustar_periodo = models.BooleanField(default=True, verbose_name='Ajustar Período Pela Prod. Mínima')
    con_esp_producao_minima = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True, default=1000, verbose_name='Prod. Minima')
    con_esp_status = models.CharField(max_length=20, null=False, blank=False, default='CALCULADO', verbose_name='Status')
    con_esp_desvio_producao = models.BooleanField(default=True, verbose_name='Considerar Desvio como Produção (P+D)')
    con_esp_qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida')
    con_esp_qtde_desviada = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Desviada')
    con_esp_qtde_consumo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Consumo')
    con_esp_indicador = models.DecimalField(max_digits=12, null=True, blank=True, decimal_places=4, verbose_name=format_html('<b style="color:{};">{}</b>', 'blue', 'Indicador (SPS)'))
    con_esp_item_producao = models.ManyToManyField(TbItensProducao, verbose_name='Itens de Produção')
    con_esp_grupo_maquina = models.ManyToManyField(TbGruposMaquinas, blank=True, verbose_name='Grupos Máquina')
    con_esp_item_consumo = models.ManyToManyField(TbItensConsumo, blank=True, verbose_name='Itens de Consumo')
    con_esp_equacao = models.CharField(max_length=100, null=True, blank=True, verbose_name='Equação Cons. Específico')
    con_esp_tipo_equacao = models.CharField(max_length=3, choices=TipoEquacaolChoices.choices, default='SOM', null=True, blank=True, verbose_name='Tipo Equação')
    con_esp_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.con_esp_descricao

    def clean(self):

        self.con_esp_descricao = self.con_esp_descricao.upper()  # Passa a descrição para maiusculo antes de salvar

        if self.con_esp_equacao:
            self.con_esp_equacao = self.con_esp_equacao.upper()  # Passa a equacao para maiusculo antes de salvar

        if self.con_esp_ano_mes_fim < self.con_esp_ano_mes_inicio:
            raise ValidationError('Ano/Mês Fim deve ser maior ou igual ao Ano/Mês Início. Favor corrigir!')

        year, month = self.con_esp_ano_mes_inicio.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Início informado está incorreto. Favor corrigir!')

        year, month = self.con_esp_ano_mes_fim.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Fim informado está incorreto. Favor corrigir!')


    class Meta:
        verbose_name = '  Consumo Específico SPS'
        verbose_name_plural = '  Consumos Específicos SPS'
        ordering = ['con_esp_descricao']
        constraints = [
            models.UniqueConstraint(fields=['con_esp_descricao', 'empresa'], name='consumoespecifico_descricao_unica_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def desv_prod(self):
        return self.con_esp_desvio_producao
    desv_prod.short_description = '(D+P)'
    desv_prod.boolean = True

    def percentual_desvio(self):
        if self.con_esp_qtde_produzida != None and self.con_esp_qtde_desviada != None:
            if (self.con_esp_qtde_produzida + self.con_esp_qtde_desviada) > 0:
                valor_retorno = self.con_esp_qtde_desviada * 100 / (self.con_esp_qtde_produzida + self.con_esp_qtde_desviada)
            else:
                valor_retorno = 0
            valor_retorno = locale.format_string('%.2f', valor_retorno, True)
            return valor_retorno
        else:
            return ''

    percentual_desvio.short_description = 'Desvio (%)'

    def status_colored_model(self):
        colors = {
            'CALCULADO': 'green',
            'CALCULANDO': 'red',
            'A CALCULAR': 'blue',
        }
        return format_html(
            '<b style="color:{};">{}</b>',
            colors[self.con_esp_status],
            self.con_esp_status,
        )

    status_colored_model.short_description = 'Status'

    def sps(self):
        from fluxos.models import TbFluxoConsumoPadrao
        from equipamentos.models import TbEquipamentosConsumoEspecifico

        cen_ativo = str(TbCenarios.objects.get(cen_ativo=True).id)

        if TbFluxoConsumoPadrao.objects.filter(flu_con_pad_consumo_especifico_id=self.id, tbcenarios_id=cen_ativo).exists() or TbEquipamentosConsumoEspecifico.objects.filter(equ_con_consumo_especifico_id=self.id, tbcenarios_id=cen_ativo).exists():
            return 'Sim'
        else:
            return 'Não'

    sps.short_description = 'SPS'

    def indicador_sps(self):
        if self.con_esp_indicador != None:
            retorno = locale.format_string('%.4f', self.con_esp_indicador, True)
            return format_html('<b style="color:{};">{}</b>', 'blue', retorno,)
        else:
            return ''

    indicador_sps.short_description = format_html('<b style="color:{};">{}</b>', 'blue', 'Indicador (SPS)')

    def indicador_d_0(self):
        if self.con_esp_qtde_produzida != None:
            if self.con_esp_qtde_produzida > 0:
                retorno = self.con_esp_qtde_consumo / self.con_esp_qtde_produzida
                return locale.format_string('%.4f', retorno, True)
            else:
                return 0
        else:
            return ''

    indicador_d_0.short_description = 'Indicador (P)'

    def indicador_d_p(self):
        if self.con_esp_qtde_desviada == None:
            qtde_desviada = 0
        else:
            qtde_desviada = self.con_esp_qtde_desviada

        if self.con_esp_qtde_produzida == None:
            qtde_produzida = 0
        else:
            qtde_produzida = self.con_esp_qtde_produzida

        if (qtde_produzida + qtde_desviada) > 0:
            retorno = self.con_esp_qtde_consumo / (qtde_produzida + qtde_desviada)
            return locale.format_string('%.4f', retorno, True)
        else:
            return 0

    indicador_d_p.short_description = 'Indicador (P+D)'

    def legenda(self):
        return 'P: DESVIO NÃO É CONSIDERADO COMO PRODUÇÃO - P+D: DESVIO É CONSIDERADO COMO PRODUÇÃO'

    legenda.short_description = ''


class TbConsumoEspecificoDaugther(models.Model):
    ano_mes = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês')
    qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, null = True, verbose_name='Qtde Produzida')
    qtde_desviada = models.DecimalField(max_digits=10, decimal_places=2, null = True, verbose_name='Qtde Desviada')
    qtde_consumo = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Qtde Consumo')
    indicador = models.DecimalField(max_digits=12, decimal_places=4, verbose_name='Indicador (SPS)')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    mae = models.ForeignKey(TbConsumoEspecifico, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = 'Valor Histórico'
        verbose_name_plural = 'Valores Históricos'
        ordering = ['ano_mes']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def indicador_d_0(self):
        if self.qtde_produzida > 0:
            retorno = self.qtde_consumo / self.qtde_produzida
            return locale.format_string('%.4f', retorno, True)
        else:
            return None

    indicador_d_0.short_description = 'Indicador (P)'

    def indicador_d_p(self):
        if self.qtde_desviada == None:
            qtde_desviada = 0
        else:
            qtde_desviada = self.qtde_desviada

        if (self.qtde_produzida + qtde_desviada) > 0:
            retorno = self.qtde_consumo / (self.qtde_produzida + qtde_desviada)
            return locale.format_string('%.4f', retorno, True)
        else:
            return None

    indicador_d_p.short_description = 'Indicador (P+D)'

    def percentual_desvio(self):
        if self.qtde_desviada == None:
            qtde_desviada = 0
        else:
            qtde_desviada = self.qtde_desviada
        if (self.qtde_produzida + qtde_desviada) != 0:
            valor_retorno = self.qtde_desviada * 100 / (self.qtde_produzida + qtde_desviada)
        else:
            valor_retorno = 0

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    percentual_desvio.short_description = 'Desvio (%)'


class TbConsumoEspecificoDaugther1(models.Model):
    codigo = models.CharField(max_length=3, null=False, blank=False, verbose_name='Código')
    consumo_especifico = models.ForeignKey(TbConsumoEspecifico, on_delete=models.CASCADE, verbose_name='Consumo Específico')
    mae = models.ForeignKey(TbConsumoEspecifico, on_delete=models.CASCADE, related_name='mae', verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    def clean(self):

        self.codigo = self.codigo.upper()  # Passa o código para maiusculo antes de salvar

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def indicador_atual(self):
        if self.consumo_especifico is not None:
            cursor = connection.cursor()
            ano_mes_inicio = TbConsumoEspecifico.objects.get(id=self.mae_id).con_esp_ano_mes_inicio
            ano_mes_fim = TbConsumoEspecifico.objects.get(id=self.mae_id).con_esp_ano_mes_fim
            sql = "call public.consumo_especifico_indicador_periodo(" + str(self.consumo_especifico_id) + ", '" + \
                  ano_mes_inicio + "', '" + ano_mes_fim + "', 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = locale.format_string('%.4f', retorno, True)
        else:
            retorno = None
        return retorno

    indicador_atual.short_description = 'Indicador Atual'

    class Meta:
        verbose_name = 'Consumo Específico Complementar'
        verbose_name_plural = 'Consumos Específicos Complementares'
        ordering = ['codigo']


class TbCustoVariavelAdicionado(models.Model):
    class TipoEquacaolChoices(models.TextChoices):
        SOM = ('SOM', 'SOMA')
        MUL = ('MUL', 'MULTIPLICAÇÃO')

    cus_var_adi_descricao = models.CharField(max_length=100, null=False, blank=False, verbose_name='Descrição')
    cus_var_adi_validado = models.BooleanField(default=0, verbose_name='Validado')
    cus_var_adi_ano_mes_inicio = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Início')
    cus_var_adi_ano_mes_fim = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Fim')
    cus_var_adi_ajustar_periodo = models.BooleanField(default=True, verbose_name='Ajustar Período Pela Prod. Mínima')
    cus_var_adi_producao_minima = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True, default=1000, verbose_name='Prod. Minima')
    cus_var_adi_status = models.CharField(max_length=20, null=False, blank=False, default='CALCULADO', verbose_name='Status')
    cus_var_adi_qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida')
    cus_var_adi_qtde_desviada = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Desviada')
    cus_var_adi_desvio_producao = models.BooleanField(default=True, verbose_name='Considerar Desvio como Produção (P+D)')
    cus_var_adi_subproduto_producao = models.BooleanField(default=False, verbose_name='Considerar Subproduto como Produção (Quando Escolhido)')
    cus_var_adi_custo_variavel_adicionado_material = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. Mat (SPS)')
    cus_var_adi_custo_variavel_adicionado_ggf = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. GGF (SPS)')

    cus_var_adi_custo_variavel_adicionado_material_p = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. Mat (P)')
    cus_var_adi_custo_variavel_adicionado_ggf_p = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. GGF (P)')

    cus_var_adi_custo_variavel_adicionado_material_p_d = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. Mat (P+D)')
    cus_var_adi_custo_variavel_adicionado_ggf_p_d = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. Adic. GGF (P+D)')

    cus_var_adi_custo_variavel = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Variável (P)')
    cus_var_adi_custo_fixo_outros = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Fixo/Outros (P)')
    cus_var_adi_custo_total = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Total (P)')
    cus_var_adi_custo_variavel_ggf_direto = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. GGF Fase (P)')
    cus_var_adi_custo_variavel_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Variável (P+D)')
    cus_var_adi_custo_fixo_outros_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Fixo/Outros (P+D)')
    cus_var_adi_custo_total_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Total (P+D)')
    cus_var_adi_custo_variavel_ggf_direto_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='Custo Var. GGF Fase (P+D)')
    cus_var_adi_item_producao = models.ManyToManyField(TbItensProducao, verbose_name='Itens de Produção')
    cus_var_adi_grupo_maquina = models.ManyToManyField(TbGruposMaquinas, blank=True, verbose_name='Grupos Máquina')
    cus_var_adi_item_consumo = models.ManyToManyField(TbItensConsumo, blank=True, verbose_name='Itens de Consumo para Desconsiderar')
    cus_var_adi_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.cus_var_adi_descricao

    def clean(self):
        self.cus_var_adi_descricao = self.cus_var_adi_descricao.upper()  # Passa a descrição para maiusculo antes de salvar

        if self.cus_var_adi_ano_mes_fim < self.cus_var_adi_ano_mes_inicio:
            raise ValidationError('Ano/Mês Fim deve ser maior ou igual ao Ano/Mês Início. Favor corrigir!')

        year, month = self.cus_var_adi_ano_mes_inicio.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Início informado está incorreto. Favor corrigir!')

        year, month = self.cus_var_adi_ano_mes_fim.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Fim informado está incorreto. Favor corrigir!')

    def desv_prod(self):
        return self.cus_var_adi_desvio_producao
    desv_prod.short_description = '(D+P)'
    desv_prod.boolean = True

    def percentual_desvio(self):
        if self.cus_var_adi_qtde_produzida != None and  self.cus_var_adi_qtde_desviada != None:
            if (self.cus_var_adi_qtde_produzida + self.cus_var_adi_qtde_desviada) > 0:
                valor_retorno = self.cus_var_adi_qtde_desviada * 100 / (self.cus_var_adi_qtde_produzida + self.cus_var_adi_qtde_desviada)
            else:
                valor_retorno = 0
            valor_retorno = locale.format_string('%.2f', valor_retorno, True)

            return valor_retorno
        else:
            return ''

    percentual_desvio.short_description = 'Desvio (%)'

    def sps(self):
        from tabelas.models import TbCustoItemPreco
        if TbCustoItemPreco.objects.filter(cus_ite_pre_custo_variavel_adiconado_id=self.id).exists():
            return 'Sim'
        else:
            return 'Não'

    sps.short_description = 'SPS'

    class Meta:
        verbose_name = ' Custo Variável Adicionado SPS'
        verbose_name_plural = ' Custos Variáveis Adicionados SPS'
        ordering = ['cus_var_adi_descricao']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def status_colored_model(self):
        colors = {
            'CALCULADO': 'green',
            'CALCULANDO': 'red',
            'ERRO': 'red',
            'A CALCULAR': 'blue',
        }
        return format_html(
            '<b style="color:{};">{}</b>',
            colors[self.cus_var_adi_status],
            self.cus_var_adi_status,
        )

    status_colored_model.short_description = 'Status'

    def custo_variavel_adicionado_total(self):
        if self.cus_var_adi_custo_variavel_adicionado_material != None and self.cus_var_adi_custo_variavel_adicionado_ggf != None:
            valor_retorno = self.cus_var_adi_custo_variavel_adicionado_material + self.cus_var_adi_custo_variavel_adicionado_ggf
        else:
            valor_retorno = 0

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return format_html('<b style="color:{};">{}</b>', 'blue', valor_retorno,)

    custo_variavel_adicionado_total.short_description = format_html('<b style="color:{};">{}</b>', 'blue', 'Custo Var. Adic. Tot. (SPS)')

    def custo_variavel_adicionado_total_p(self):
        if self.cus_var_adi_custo_variavel_adicionado_material_p != None and self.cus_var_adi_custo_variavel_adicionado_ggf_p != None:
            valor_retorno = self.cus_var_adi_custo_variavel_adicionado_material_p + self.cus_var_adi_custo_variavel_adicionado_ggf_p
        else:
            valor_retorno = 0

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_variavel_adicionado_total_p.short_description = 'Custo Var. Adic. Tot. (P)'

    def custo_variavel_adicionado_total_p_d(self):
        if self.cus_var_adi_custo_variavel_adicionado_material_p_d != None and self.cus_var_adi_custo_variavel_adicionado_ggf_p_d != None:
            valor_retorno = self.cus_var_adi_custo_variavel_adicionado_material_p_d + self.cus_var_adi_custo_variavel_adicionado_ggf_p_d
        else:
            valor_retorno = 0

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_variavel_adicionado_total_p_d.short_description = 'Custo Var. Adic. Tot. (P+D)'

class TbCustoVariavelAdicionadoDaugther(models.Model):
    ano_mes = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês')
    qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, null = True, default=0, verbose_name='Qtde Produzida')
    qtde_desviada = models.DecimalField(max_digits=10, decimal_places=2, null = True, default=0, verbose_name='Qtde Desviada')

    custo_variavel_adicionado_material = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. Mat (SPS)')
    custo_variavel_adicionado_ggf = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. GGF (SPS)')

    custo_variavel_adicionado_material_p = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. Mat (P)')
    custo_variavel_adicionado_ggf_p = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. GGF (P)')

    custo_variavel_adicionado_material_p_d = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. Mat (P+D)')
    custo_variavel_adicionado_ggf_p_d = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='Custo Var. Adic. GGF (P+D)')

    custo_variavel = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Varíável (P)')
    custo_fixo_outros = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name="Custo Fixo/Outros (P)")
    custo_total = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name="Custo Total (P)")
    custo_variavel_ggf_direto = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Var. GGF Fase (P)')
    custo_variavel_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Varíável (P+D)')
    custo_fixo_outros_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Fixo/Outros (P+D)')
    custo_total_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Total (P+D)')
    custo_variavel_ggf_direto_desv = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, default=0, verbose_name='Custo Var. GGF Fase (P+D)')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    mae = models.ForeignKey(TbCustoVariavelAdicionado, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = 'Valor Histórico'
        verbose_name_plural = 'Valores Históricos'
        ordering = ['ano_mes']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def custo_var_adic_total(self):
        valor_retorno = self.custo_variavel_adicionado_material  + self.custo_variavel_adicionado_ggf

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_var_adic_total.short_description = 'Custo Var. Adic. Total (SPS)'

    def percentual_desvio(self):
        if (self.qtde_produzida + self.qtde_desviada) > 0:
            valor_retorno = self.qtde_desviada * 100 / (self.qtde_produzida + self.qtde_desviada)
            valor_retorno = locale.format_string('%.2f', valor_retorno, True)
            return valor_retorno
        else:
            return ''
    percentual_desvio.short_description = 'Desvio (%)'

    def custo_var_adic_total_p(self):
        valor_retorno = self.custo_variavel_adicionado_material_p  + self.custo_variavel_adicionado_ggf_p

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_var_adic_total_p.short_description = 'Custo Var. Adic. Total (P)'

    def custo_var_adic_total_p_d(self):
        valor_retorno = self.custo_variavel_adicionado_material_p_d  + self.custo_variavel_adicionado_ggf_p_d

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_var_adic_total_p_d.short_description = 'Custo Var. Adic. Total (P+D)'


class TbCustoVariavelAdicionadoDaugther1(models.Model):
    codigo = models.CharField(max_length=3, null=False, blank=False, verbose_name='Código')
    custo_variavel_adicionado = models.ForeignKey(TbCustoVariavelAdicionado, on_delete=models.CASCADE, verbose_name='Custo Variável Adicionado')
    consumo_especifico = models.ForeignKey(TbConsumoEspecifico, on_delete=models.CASCADE, verbose_name='Consumo Específico')
    mae = models.ForeignKey(TbCustoVariavelAdicionado, on_delete=models.CASCADE, related_name='mae', verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    def clean(self):
        self.codigo = self.codigo.upper()  # Passa o código para maiusculo antes de salvar

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def custo_variavel_adicionado_atual(self):
        valor_retorno = TbCustoVariavelAdicionado.objects.get(id=self.custo_variavel_adicionado_id).cus_var_adi_custo_variavel_adicionado_material + TbCustoVariavelAdicionado.objects.get(id=self.custo_variavel_adicionado_id).cus_var_adi_custo_variavel_adicionado_ggf

        valor_retorno = locale.format_string('%.2f', valor_retorno, True)

        return valor_retorno

    custo_variavel_adicionado_atual.short_description = 'Valor Atual'

    def indicador_atual(self):
        valor_retorno = TbConsumoEspecifico.objects.get(id=self.consumo_especifico_id).con_esp_indicador

        valor_retorno = locale.format_string('%.4f', valor_retorno, True)

        return valor_retorno

    indicador_atual.short_description = 'Indicador Atual'

    class Meta:
        verbose_name = 'Custo Variável Adicional'
        verbose_name_plural = 'Custos Variáveis Adicionais'
        ordering = ['codigo']


class TbAjusteCusto(models.Model):
    item_producao_id = models.IntegerField(null=False, blank=False, verbose_name='Id Item Produção')
    chave = models.TextField(verbose_name='Chave', blank=False, null=False)
    item_consumo_id = models.IntegerField(null=False, blank=False, verbose_name='Id Item Consumo')
    indicador = models.DecimalField(max_digits=15, decimal_places=4, verbose_name='Indicador')
    flag = models.BooleanField(blank=True, null=False, default=False, verbose_name='Flag')
    ordem_lançamento = models.IntegerField(null=False, blank=False, verbose_name='Ordem Lançamento')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbAjusteCustoCopy(models.Model):
    item_producao_id = models.IntegerField(null=True, blank=True, verbose_name='Id Item Produção')
    chave = models.TextField(verbose_name='Chave', blank=True, null=True)
    item_consumo_id = models.IntegerField(null=True, blank=True, verbose_name='Id Item Consumo')
    indicador = models.DecimalField(max_digits=15, decimal_places=4, verbose_name='Indicador')
    flag = models.BooleanField(blank=True, null=False, default=False, verbose_name='Flag')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

class TbRegressaoLinearMultipla(models.Model):
    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- vira constraint
    # no Meta, junto com empresa.
    reg_lin_mul_descricao = models.CharField(max_length=100, null=False, blank=False, verbose_name='Descrição')
    reg_lin_mul_ano_mes_inicio = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Início')
    reg_lin_mul_ano_mes_fim = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês Fim')
    reg_lin_mul_status = models.TextField(verbose_name='Status', default='CALCULANDO', blank=True, null=True)
    reg_lin_mul_equacao_regressao = models.CharField(max_length=200, verbose_name='Equação Regressão', blank=True, null=True)
    reg_lin_mul_r2_regressao = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='R2 Regressão')
    reg_lin_mul_desvio_producao = models.BooleanField(default=True, verbose_name='Considerar desvio como produção')
    reg_lin_mul_qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida')
    reg_lin_mul_item_producao = models.ManyToManyField(TbItensProducao, verbose_name='Itens de Produção')
    reg_lin_mul_grupo_maquina = models.ManyToManyField(TbGruposMaquinas, blank=True, verbose_name='Grupos Máquina')
    reg_lin_mul_sumario = models.TextField(verbose_name='Sumário', blank=True, null=True)
    reg_lin_mul_observacao = models.TextField(verbose_name='Observação', blank=True, null=True)
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return self.reg_lin_mul_descricao

    def clean(self):
        self.reg_lin_mul_descricao = self.reg_lin_mul_descricao.upper()  # Passa a descrição para maiusculo antes de salvar

        if self.reg_lin_mul_ano_mes_fim < self.reg_lin_mul_ano_mes_inicio:
            raise ValidationError('Ano/Mês Fim deve ser maior ou igual ao Ano/Mês Início. Favor corrigir!')

        year, month = self.reg_lin_mul_ano_mes_inicio.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Início informado está incorreto. Favor corrigir!')

        year, month = self.reg_lin_mul_ano_mes_fim.split('/')
        day = '01'
        isValidDate = True
        try:
            datetime.datetime(int(year), int(month), int(day))
        except ValueError:
            isValidDate = False

        if not isValidDate:
            raise ValidationError('Ano/Mês Fim informado está incorreto. Favor corrigir!')

    class Meta:
        verbose_name = 'Regressão Linear Múltipla'
        verbose_name_plural = 'Regressões Lineares Múltiplas'
        ordering = ['reg_lin_mul_descricao']
        constraints = [
            models.UniqueConstraint(fields=['reg_lin_mul_descricao', 'empresa'], name='regressao_descricao_unica_por_empresa')
        ]

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)

    def status_colored_model(self):
        if self.reg_lin_mul_status == 'CALCULANDO':
            color = 'blue'
        else:
            if self.reg_lin_mul_status[0:2] == 'OK':
                color = 'green'
            else:
                color = 'red'

        return format_html(
            '<b style="color:{};">{}</b>',
            color,
            self.reg_lin_mul_status,
        )

    status_colored_model.short_description = 'Status'

class TbRegressaoLinearMultiplaDaugther(models.Model):
    class TipoVariavelChoices(models.TextChoices):
        DEP = ('Y', 'DEPENDENTE(Y)')
        IND = ('X', 'INDEPENDENTE(X)')
        DES = ('D', 'DESCONSIDERAR(D)')

    item_consumo = models.ForeignKey(TbItensConsumo, on_delete=models.CASCADE, verbose_name='Item de Consumo / Código')
    classificacao = models.CharField(max_length=10, default='CONSUMO', verbose_name='Classificação')
    indicador = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='Indicador')
    minimo = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='Mínimo')
    media = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='Média')
    maximo = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='Máximo')
    tipo_variavel = models.CharField(max_length=3, choices=TipoVariavelChoices.choices, default='D', null=False, blank=False, verbose_name='Tipo Variável')
    agrupamento = models.IntegerField(blank=True, null=True, verbose_name='Agrupamento')
    coeficiente = models.DecimalField(max_digits=15, decimal_places=4, default=0, verbose_name='Coeficiente')
    pt = models.DecimalField(max_digits=15, decimal_places=4, default=0, verbose_name='P>|t|')

    flag = models.BooleanField(default=False, verbose_name='Controle')
    mae = models.ForeignKey(TbRegressaoLinearMultipla, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = 'Variável'
        verbose_name_plural = 'Variáveis'
        ordering = ['-tipo_variavel', 'agrupamento', 'item_consumo']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)


class TbRegressaoLinearMultiplaDaugther1(models.Model):
    ano_mes = models.CharField(max_length=7, null=False, blank=False, verbose_name='Ano/Mês')
    variavel = models.CharField(max_length=5, verbose_name='Variável')
    itens_consumo = models.TextField(verbose_name='Itens Consumo', blank=True, null=True)
    qtde_produzida = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='Qtde Produzida')
    qtde_consumo = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='Qtde Consumo')
    indicador = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name='Indicador')
    flag = models.BooleanField(default=False, verbose_name='Controle')
    mae = models.ForeignKey(TbRegressaoLinearMultipla, on_delete=models.CASCADE, verbose_name='Mãe')
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Empresa')

    def __str__(self):
        return ''

    class Meta:
        verbose_name = 'Base de Dados'
        verbose_name_plural = 'Base de Dados'
        ordering = ['ano_mes', '-variavel']

    def save(self, *args, **kwargs):
        _atribuir_empresa_se_necessario(self)
        super().save(*args, **kwargs)