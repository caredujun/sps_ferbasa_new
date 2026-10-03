from django.db import models
from django.db import connection
from django.db.models import ImageField
from django.utils.translation import gettext_lazy as _

from tabelas.models import TbTipoProducao, TbUnidadeProducao, TbCustoItem, TbIndicadores
from parameters.models import TbCenarios, TbEmpresa

from custo_ferbasa.models import TbConsumoEspecifico

from django.db.models.signals import post_save, pre_save
from django.utils.safestring import mark_safe
from django.core.exceptions import ValidationError
from tabelas.models import atualiza_cenario, TbCustoItemPreco
from parameters.contexto_usuario import limit_choices_to_empresa_ativa, limit_choices_to_cenario_ativo, get_usuario_atual
from produtos.models import TbProdutos

# Para permitir mostrar valores numéricos no padrão Brasil
# Estou usando nos campos numéricos criados no model
import locale

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR


# Vai ser executado após o save para algumas tabelas
def verifica_filha(sender, instance, **kwargs):  # passa 01 valor inicial
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha
    # Vamos pegar o cenário ativo
    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha('equipamentos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(cen_ativo) + ", " + str(instance.valor_inicial) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_2(sender, instance, **kwargs):  # passa 02 valores iniciais
    # Vamos atualizar a filha usando o Stored Procedure verifica_filha_2
    # Vamos pegar o cenário ativo
    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_2('equipamentos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(cen_ativo) + ", " + str(instance.valor_inicial_1) + ", " + str(instance.valor_inicial_2) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_2_valor_1_boolean(sender, instance, **kwargs):  # passa 02 valores iniciais. Valor inicial 1 é boolean
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_2
    # Vamos pegar o cenário ativo
    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_2_valor_1_boolean('equipamentos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(cen_ativo) + ", " + str(instance.valor_inicial_1) + ", " + str(instance.valor_inicial_2) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_3_valor_1_boolean(sender, instance, **kwargs):  # passa 03 valores iniciais. Valor Inicial 1 é boolean
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3_Valor_1_Boolean
    # Vamos pegar o cenário ativo
    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_3_Valor_1_boolean('equipamentos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(cen_ativo) + ", " + str(instance.valor_inicial_1) + ", " + str(instance.valor_inicial_2) + ", " + str(instance.valor_inicial_3) + ")"
    cursor.execute(sql)
    cursor.close()


def verifica_filha_3_valor_3_boolean(sender, instance, **kwargs):  # passa 03 valores iniciais. Valor Inicial 3 é boolean
    # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_3_Valor_1_Boolean
    # Vamos pegar o cenário ativo
    cen_ativo = TbCenarios.objects.get(cen_ativo=True).id
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
    sql = "call public.verifica_filha_3_Valor_3_boolean('equipamentos_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(cen_ativo) + ", " + str(instance.valor_inicial_1) + ", " + str(instance.valor_inicial_2) + ", " + str(instance.valor_inicial_3) + ")"
    cursor.execute(sql)
    cursor.close()


def zera_wip(sender, instance, **kwargs):  # Zera o equ_wip no caso do equipamento ser expedição
    # Vamos ver se é expedição. Se sim, zera o campo equ_wip
    expedicao = TbEquipamentosCadastro.objects.get(id=instance.equ_codigo_id).equ_cad_expedicao
    if expedicao:
        instance.equ_wip = 0


class TbEquipamentosCadastro(models.Model):  # Esta tabela é geral. Não possui o campo TbCenarios

    class OutputUnidChoices(models.TextChoices):
        un = ('un', 'un')
        pe = ('pe', 'pe')
        g = ('g', 'g')
        kg = ('kg', 'kg')
        t = ('t', 't')
        h = ('h', 'h')
        hh = ('hh', 'hh')
        m = ('m', 'm')
        m3 = ('m3', 'm3')
        Nm3 = ('Nm3', 'Nm3')
        L = ('L', 'L')
        kw = ('kw', 'kw')
        kwh = ('kwh', 'kwh')
        Mwh = ('Mwh', 'Mwh')

    class EquipamentoCadastroMoedaChoices(models.TextChoices):
        BRL = ('BRL', 'REAL')
        USD = ('USD', 'DÓLAR')
        EUR = ('EUR', 'EURO')

    equ_cad_codigo = models.CharField(max_length=20, verbose_name=_('Equipamento'))
    equ_cad_descricao = models.CharField(max_length=50, verbose_name=_('Descrição'))
    equ_cad_gargalo = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Gargalo'))
    equ_cad_expedicao = models.BooleanField(blank=False, null=False, default=False, verbose_name=_('Expedição'))
    equ_cad_output = models.CharField(max_length=3, choices=OutputUnidChoices.choices, verbose_name=_('Output'))
    equ_cad_unidade_producao = models.ForeignKey(TbUnidadeProducao, on_delete=models.CASCADE, verbose_name=_('Planta'), limit_choices_to=limit_choices_to_empresa_ativa)
    equ_cad_imagem = models.ImageField(upload_to='equipamentos', null=True, blank=True, verbose_name=_('Imagem'))
    equ_cad_indicador_manutencao = models.ForeignKey(TbIndicadores, null=True, blank=True, on_delete=models.PROTECT, verbose_name=_('Indicador Custo Manutenção'))
    equ_cad_moeda_manutencao = models.CharField(max_length=3, choices=EquipamentoCadastroMoedaChoices.choices, verbose_name=_('Moeda Custo Manutenção'))
    valor_inicial_1 = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Valor Inicial Running'))
    valor_inicial_2 = models.DecimalField(max_digits=6, decimal_places=2, default=0.00, verbose_name=_('Valor Inicial Paradas Programadas (%)'))
    valor_inicial_3 = models.DecimalField(max_digits=18, decimal_places=2, default=0, blank=True, null=True, verbose_name=_('Valor Inicial Manutenção'))
    equ_cad_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    equ_cad_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name=_('Fonte'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        # return self.equ_cad_codigo + ' / ' + self.equ_cad_descricao + ' / ' + str(self.equ_cad_unidade_producao)
        return self.equ_cad_codigo

    @property
    def equ_cad_imagem_tag(self):
        if self.equ_cad_imagem:
            return mark_safe('<img src="%s" style="width: 250x; height:350px;" />' % self.equ_cad_imagem.url)
        else:
            return 'Sem imagem!'

    @property
    def equ_cad_imagem_tag_small(self):
        if self.equ_cad_imagem:
            return mark_safe('<img src="%s" style="width: 50x; height:50px;" />' % self.equ_cad_imagem.url)
        else:
            return 'Sem imagem!'

    def clean(self):

        # Convertendo para maiúsculo tanto o código do equipamento quanto a descrição
        self.equ_codigo = self.equ_cad_codigo.upper()
        self.equ_descricao = self.equ_cad_descricao.upper()

        # Vamos ainda trocar caracter '/' por '-' na descrição
        self.equ_descricao = self.equ_descricao.replace('/', '-')

        # Verificando se já foi cadastrado registro com o mesmo código e cenário
        # Vamos pegar o cenário ativo
        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbEquipamentosCadastro.objects.filter(equ_cad_codigo=self.equ_cad_codigo, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbEquipamentosCadastro.objects.filter(equ_cad_codigo=self.equ_cad_codigo, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Equipamento ' + self.equ_cad_codigo + ' já cadastrado para esse cenário!')

    class Meta:
        verbose_name = _('  Cadastro')
        verbose_name_plural = _('  Cadastro')
        ordering = ['equ_cad_codigo']

    # Vamos criar um campo para mostrar o total de ordens cadastradas para o equipamento
    def total_ordens(self):
        retorno = TbEquipamentos.objects.filter(equ_codigo_id=self.id).count()
        return retorno

    total_ordens.short_description = _('Ordens')

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbEquipamentosCadastro)

        # Vamos salvar o super save
        super(TbEquipamentosCadastro, self).save(*args, **kwargs)

        # Essa tabela tem indicador(es) para ajustar os preços/custos. Não vamos usar o post_save. Vamos fazer localmente
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure verifica_filha_3_com_indicador

        # Se não uso a função int dá erro pois assume o valor como double e na procedure tem que ser bigint. Coisa de maluco mesmo.
        if self.equ_cad_indicador_manutencao_id is not None and self.equ_cad_indicador_manutencao_id != '':
            id_indicador_1 = int(self.equ_cad_indicador_manutencao_id)
        else:
            id_indicador_1 = int(0)

        # Atenção: o sp verifica_filha_3_com_indicador só considera os valores iniciais  no caso de não termos nenhuma filha.
        # Se já existir filha, o ponto de partida é o valor lançado na primeira filha (valor para o primeiro período)

        sql = "call public.verifica_filha_3_com_indicador('equipamentos_" + "tbequipamentoscadastro" + "daugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(self.valor_inicial_1) + ", " + str(
            self.valor_inicial_2) + ", " + str(self.valor_inicial_3) + ", " + str(id_indicador_1) + ")"
        cursor.execute(sql)
        cursor.close()


# Signals a serem executados na tabela TbEquipamentosCadastro
# post_save.connect(verifica_filha_3_valor_1_boolean, sender=TbEquipamentosCadastro)

class TbEquipamentosCadastroDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor_1 = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Running'))
    dau_valor_2 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name=_('Paradas Programadas (%)'))
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name=_('Custo Manutenção (Moeda/h)'))
    mae = models.ForeignKey(TbEquipamentosCadastro, on_delete=models.CASCADE, verbose_name=_('Equipamento'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    def display_order(self):

        if int(self.dau_order) >= 1:

            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
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

        super(TbEquipamentosCadastroDaugther, self).save(*args, **kwargs)

        cursor = connection.cursor()
        id_indicador_1 = 0
        campo_mae = TbEquipamentosCadastro.objects.get(pk=self.mae_id)
        if campo_mae.equ_cad_indicador_manutencao_id is not None:
            id_indicador_1 = campo_mae.equ_cad_indicador_manutencao_id

        sql = "call public.verifica_filha_3_com_indicador('equipamentos_" + "tbequipamentoscadastro" + "daugther', " + str(campo_mae.pk) + ", " + str(campo_mae.tbcenarios_id) + ", '" + str(campo_mae.valor_inicial_1) + "', '" + str(campo_mae.valor_inicial_2) + "', '" + str(campo_mae.valor_inicial_3) + "', " + str(id_indicador_1) + ")"

        cursor.execute(sql)
        cursor.close()

        if self.dau_valor_1 == True: #Equipamento está running
            ocupacao_minima = 0
            ocupacao_maxima = 100
        else:
            ocupacao_minima = 0
            ocupacao_maxima = 0

        from otimizacao.models import TbOtimizacaoEquipamentos
        if TbOtimizacaoEquipamentos.objects.filter(oti_equ_equipamento_id=self.mae_id, tbcenarios_id=self.tbcenarios_id).exists():
            id_equipamento_otimizacao = TbOtimizacaoEquipamentos.objects.get(oti_equ_equipamento_id=self.mae_id, tbcenarios_id=self.tbcenarios_id).id
            from otimizacao.models import TbOtimizacaoEquipamentosDaugther
            TbOtimizacaoEquipamentosDaugther.objects.filter(mae_id=id_equipamento_otimizacao, dau_order=self.dau_order).update(dau_valor_5=ocupacao_minima,dau_valor_7=ocupacao_maxima, dau_valor_1=self.dau_valor_1)

    class Meta:
        verbose_name = _('Valores Previstos')
        verbose_name_plural = _('Valores Previstos')
        ordering = ['dau_order']


class TbEquipamentos(models.Model):
    equ_codigo = models.ForeignKey(TbEquipamentosCadastro, on_delete=models.CASCADE, verbose_name=_('Equipamento'))
    equ_tipo_producao = models.ForeignKey(TbTipoProducao, on_delete=models.CASCADE, verbose_name=_('Tipo Produção'), limit_choices_to=limit_choices_to_empresa_ativa)
    # 🌟 NOVO: usado só em casos raros (ex: ENERGIA 03/3, criada só pra não
    # repetir ENERGIA 03/1 na mesma coluna do editor visual) -- marca que
    # essa ordem representa o MESMO recurso físico de outra ordem já
    # cadastrada. Nula na imensa maioria dos equipamentos.
    equ_e_clone_de = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='clones', verbose_name=_('É Clone Da Ordem'), limit_choices_to=limit_choices_to_cenario_ativo)
    equ_wip = models.IntegerField(default=0, verbose_name=_('WIP (Dias Produção)'))
    equ_ordem_codigo = models.IntegerField(verbose_name=_('Ordem'))
    equ_ordem_descricao = models.CharField(max_length=60, verbose_name=_('Descrição da Ordem'))
    equ_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    equ_fonte = models.FileField(upload_to='fontes', null=True, blank=True, verbose_name=_('Fonte'))
    valor_inicial_1 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name=_('Valor Inicial Paradas Não Programadas (%)'))
    valor_inicial_2 = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_('Valor Inicial Produtividade'))
    valor_inicial_3 = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Valor Inicial Running'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela
    # 🌟 NOVO: produtos que têm esse equipamento/ordem em algum dos seus
    # fluxos de produção cadastrados (TbFluxoProducao -> flu_pro_produto).
    # Marcação manual/backfillada, não calculada a cada consulta.
    equ_produtos = models.ManyToManyField(
        TbProdutos,
        blank=True,
        verbose_name=_('Produtos Participantes'),
        limit_choices_to=limit_choices_to_cenario_ativo,
    )

    def __str__(self):
        return str(self.equ_codigo) + ' / ' + str(self.equ_ordem_codigo) + ' / ' + self.equ_ordem_descricao

    def equipamento_imagem_tag_small(self):
        imagem = TbEquipamentosCadastro.objects.get(id=self.equ_codigo_id).equ_cad_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    equipamento_imagem_tag_small.short_description = _('Imagem')
    equipamento_imagem_tag_small.allow_tags = True

    def clean(self):

        ativo = TbCenarios.objects.get(cen_ativo=True).id

        count = 0
        if self.pk is None:
            count = TbEquipamentos.objects.filter(equ_codigo=self.equ_codigo, equ_ordem_codigo=self.equ_ordem_codigo, tbcenarios_id=ativo).count()
        else:
            count = TbEquipamentos.objects.filter(equ_codigo=self.equ_codigo, equ_ordem_codigo=self.equ_ordem_codigo, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Equipamento / Ordem de Produção ' + self.equ_codigo + ' / ' + self.equ_ordem_codigo + ' já cadastrados para esse cenário!')

        if self.equ_wip < 0:
            raise ValidationError('WIP (Dias de Venda) deve ser maior ou igual a Zero. Favor alterar!')

    def save(self, *args, **kwargs):
        if self.pk is None:
            pre_save.connect(atualiza_cenario, sender=TbEquipamentos)

        pre_save.connect(zera_wip, sender=TbEquipamentos)

        super(TbEquipamentos, self).save(*args, **kwargs)

    class Meta:
        verbose_name = _(' Ordem de Producao')
        verbose_name_plural = _(' Ordem de Produção')
        ordering = ['equ_codigo', 'equ_ordem_codigo']

    def total_itens(self):
        retorno = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento_id=self.id).count()
        return retorno

    total_itens.short_description = _('Itens')

    def gargalo(self):
        return TbEquipamentosCadastro.objects.get(id=self.equ_codigo_id).equ_cad_gargalo

    gargalo.short_description = _('Gargalo')
    gargalo.boolean = True

    def expedicao(self):
        return TbEquipamentosCadastro.objects.get(id=self.equ_codigo_id).equ_cad_expedicao

    expedicao.short_description = _('Expedição')
    expedicao.boolean = True


post_save.connect(verifica_filha_3_valor_3_boolean, sender=TbEquipamentos)


class TbEquipamentosDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor_1 = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_('Paradas Não Prog. (%)'))
    dau_valor_2 = models.DecimalField(max_digits=14, decimal_places=4, verbose_name=_('Produtividade'))
    dau_valor_3 = models.BooleanField(blank=False, null=False, default=True, verbose_name=_('Ativa'))
    mae = models.ForeignKey(TbEquipamentos, on_delete=models.CASCADE, verbose_name=_('Equipamentos'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    def display_order(self):

        if int(self.dau_order) >= 1:

            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
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

    def custo_variavel(self):

        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            sql = "CALL public.atualiza_custo_var_adic_equipamento_order(" + str(self.tbcenarios_id) + ", " + str(self.mae_id) + ", " + str(self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel.short_description = _('Custo Var. Adic.')

    def custo_variavel_item(self):

        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            sql = "CALL public.atualiza_custo_var_item_adic_equipamento_order(" + str(self.tbcenarios_id) + ", " + str(self.mae_id) + ", " + str(self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_item.short_description = _('Parc. Itens')

    def custo_variavel_inbound(self):

        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            sql = "CALL public.atualiza_custo_var_inbound_adic_equipamento_order(" + str(self.tbcenarios_id) + ", " + str(self.mae_id) + ", " + str(
                self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_inbound.short_description = _('Parc. Inbound')

    def custo_variavel_manutencao(self):

        if int(self.dau_order) >= 1:
            cursor = connection.cursor()
            sql = "CALL public.atualiza_custo_var_manutencao_adic_equipamento_order(" + str(self.tbcenarios_id) + ", " + str(self.mae_id) + ", " + str(self.dau_order) + ", 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_variavel_manutencao.short_description = _('Parc. Manutenção')

    def wip_volume(self):

        if int(self.dau_order) >= 1:
            wip_dias = TbEquipamentos.objects.get(id=self.mae_id).equ_wip
            id_equipamento = TbEquipamentos.objects.get(id=self.mae_id).equ_codigo_id
            perc_paradas_programadas = TbEquipamentosCadastroDaugther.objects.get(mae_id=id_equipamento, dau_order=self.dau_order).dau_valor_2
            retorno = wip_dias * 24 * self.dau_valor_2 * ((100 - self.dau_valor_1) / 100) * ((100 - perc_paradas_programadas) / 100)
            retorno = locale.format_string('%.0f', retorno, True)
        return retorno

    def clean(self):
        if self.dau_valor_2 <= 0:
            raise ValidationError('Produtividade deve ser maior que zero!')

    class Meta:
        verbose_name = _('Valores Previstos')
        verbose_name_plural = _('Valores Previstos')
        ordering = ['dau_order']


class TbEquipamentosConsumoEspecifico(models.Model):
    equ_con_esp_equipamento = models.ForeignKey(TbEquipamentos, on_delete=models.CASCADE, verbose_name=_('Equipamento / Ordem / Descrição'))
    equ_con_esp_custoitempreco = models.ForeignKey(TbCustoItemPreco, on_delete=models.CASCADE, verbose_name=_('Item de Custo / Planta'))
    equ_con_consumo_especifico = models.ForeignKey(TbConsumoEspecifico, on_delete=models.SET_NULL, blank=True, null=True, verbose_name=_('Consumo Específico CF'))
    equ_con_esp_observacao = models.TextField(verbose_name=_('Observação'), blank=True, null=True)
    valor_inicial = models.DecimalField(max_digits=11, decimal_places=4, verbose_name=_('Valor Inicial do Consumo Específico'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return ''

    def equipamento_imagem_tag_small(self):
        equipamento_id = TbEquipamentos.objects.get(id=self.equ_con_esp_equipamento_id).equ_codigo_id
        imagem = TbEquipamentosCadastro.objects.get(id=equipamento_id).equ_cad_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    equipamento_imagem_tag_small.short_description = _('Imagem')
    equipamento_imagem_tag_small.allow_tags = True

    def cus_ite_imagem_tag_small(self):
        custo_item_id = TbCustoItemPreco.objects.get(id=self.equ_con_esp_custoitempreco_id).cus_ite_pre_item_id
        item_imagem = TbCustoItem.objects.get(id=custo_item_id).cus_ite_imagem
        if item_imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % item_imagem.url)
        else:
            return 'Sem imagem!'

    cus_ite_imagem_tag_small.short_description = _('Imagem')
    cus_ite_imagem_tag_small.allow_tags = True

    def periodo_inicio(self):
        valor_retorno = ''
        if self.equ_con_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(id=self.equ_con_consumo_especifico_id).con_esp_ano_mes_inicio

        return valor_retorno

    periodo_inicio.short_description = _('Período Inicio')

    def periodo_fim(self):
        valor_retorno = ''
        if self.equ_con_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(id=self.equ_con_consumo_especifico_id).con_esp_ano_mes_fim

        return valor_retorno

    periodo_fim.short_description = _('Período Fim')

    def valor_indicador(self):
        valor_retorno = 0
        if self.equ_con_consumo_especifico is not None:
            valor_retorno = TbConsumoEspecifico.objects.get(id=self.equ_con_consumo_especifico_id).con_esp_indicador

        valor_retorno = locale.format_string('%.4f', valor_retorno, True)

        return valor_retorno

    valor_indicador.short_description = _('Valor Indicador')

    def clean(self):

        ativo = TbCenarios.objects.get(cen_ativo=True).id
        count = 0
        if self.pk is None:
            count = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento=self.equ_con_esp_equipamento, equ_con_esp_custoitempreco_id=self.equ_con_esp_custoitempreco_id, tbcenarios_id=ativo).count()
        else:
            count = TbEquipamentosConsumoEspecifico.objects.filter(equ_con_esp_equipamento=self.equ_con_esp_equipamento, equ_con_esp_custoitempreco=self.equ_con_esp_custoitempreco, tbcenarios_id=ativo).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Equipamento ' + str(self.equ_con_esp_equipamento) + ' / Item ' + str(self.equ_con_esp_custoitempreco) + ' já cadastrados!')

        if not self.equ_con_esp_equipamento.equ_codigo.equ_cad_unidade_producao == self.equ_con_esp_custoitempreco.cus_ite_pre_unidade_producao:
            raise ValidationError('A Planta de Produção tem que ser a mesma no Equipamento e no Item de Custo')

    class Meta:
        verbose_name = _('Consumo Específico')
        verbose_name_plural = _('Consumos Específicos')
        ordering = ['equ_con_esp_equipamento', 'equ_con_esp_custoitempreco']
        unique_together = ('equ_con_esp_equipamento', 'equ_con_esp_custoitempreco', 'tbcenarios')

    def save(self, *args, **kwargs):

        if self.pk is None:
            pre_save.connect(atualiza_cenario, sender=TbEquipamentosConsumoEspecifico)

        super(TbEquipamentosConsumoEspecifico, self).save(*args, **kwargs)


post_save.connect(verifica_filha, sender=TbEquipamentosConsumoEspecifico)


class TbEquipamentosConsumoEspecificoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name=_('Ano/Mês'))
    dau_valor = models.DecimalField(max_digits=11, decimal_places=4, default=0, verbose_name=_('Consumo Específico'))
    mae = models.ForeignKey(TbEquipamentosConsumoEspecifico, on_delete=models.CASCADE, verbose_name=_('Equipamento/Item'))
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name=_('Cenário'))

    def __str__(self):
        return ''

    def display_order(self):

        if int(self.dau_order) >= 1:

            inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio

            if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
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

    def custo_item_preco(self):
        if int(self.dau_order) >= 1:
            id_custo_item = TbEquipamentosConsumoEspecifico.objects.get(id=self.mae_id).equ_con_esp_custoitempreco_id

            cursor = connection.cursor()
            sql = "call public.custo_item_order(" + str(self.tbcenarios_id) + ", " + str(id_custo_item) + ", " + str(self.dau_order) + ", 1, 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = retorno * self.dau_valor

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_item_preco.short_description = _('Parcela Preço')

    def custo_item_inbound(self):
        if int(self.dau_order) >= 1:
            id_custo_item = TbEquipamentosConsumoEspecifico.objects.get(id=self.mae_id).equ_con_esp_custoitempreco_id

            cursor = connection.cursor()
            sql = "call public.custo_item_order(" + str(self.tbcenarios_id) + ", " + str(id_custo_item) + ", " + str(self.dau_order) + ", 2, 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = retorno * self.dau_valor

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_item_inbound.short_description = _('Parcela Inbound')

    def custo_item_total(self):
        if int(self.dau_order) >= 1:
            id_custo_item = TbEquipamentosConsumoEspecifico.objects.get(id=self.mae_id).equ_con_esp_custoitempreco_id

            cursor = connection.cursor()
            sql = "call public.custo_item_order(" + str(self.tbcenarios_id) + ", " + str(id_custo_item) + ", " + str(self.dau_order) + ", 3, 0)"
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]
            cursor.close()

            retorno = retorno * self.dau_valor

            retorno = locale.format_string('%.2f', retorno, True)

            moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
            if moeda_empresa == 'BRL':
                retorno = 'R$ ' + retorno
            if moeda_empresa == 'USD':
                retorno = 'US$ ' + retorno
            if moeda_empresa == 'EUR':
                retorno = '€ ' + retorno

        return retorno

    custo_item_total.short_description = _('Total (Preço + Inbound)')

    class Meta:
        verbose_name = _('Valores Previstos')
        verbose_name_plural = _('Valores Previstos')
        ordering = ['dau_order']