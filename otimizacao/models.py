from django.db import models, connection
from django.db.models.signals import post_save, pre_save
from django.utils.safestring import mark_safe
from equipamentos.models import TbEquipamentos, TbEquipamentosCadastro, TbEquipamentosDaugther
from fluxos.models import TbFluxoProducao
from parameters.models import TbCenarios, TbCenariosDaugther, TbEmpresa
from produtos.models import TbProdutos
from custo_ferbasa.models import TbConsumoEspecifico
from tabelas.models import TbMercado, TbCustoItemPreco, TbCustoItem, TbTipoProducao, TbTaxaDescontoDaugther, atualiza_cenario
from django.core.exceptions import ValidationError
from django.utils.html import format_html
import locale

locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')  # Estou usando esse pois Heroku não aceita pt_BR
import numpy_financial as npf

def verifica_filha_tbotimizacaocomparacaocenarios(sender, instance, **kwargs):
    # Vamos atualizar a filha usando o Stored Procedure verifica_filha_tbotimizacaocomparacaocenario
    cursor = connection.cursor()
    # Montando a expressão sql para rodar o Stored Procedure verifica_filha_tbotimizacaocomparacaocenario
    sql = "call public.verifica_filha_tbotimizacaocomparacaocenarios('otimizacao_" + sender._meta.object_name + "daugther', " + str(
        instance.pk) + ", " + str(instance.tbcenarios_id) + ")"
    cursor.execute(sql)
    cursor.close()

class TbOtimizacaoProduto(models.Model):
    oti_pro_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, verbose_name='Produto')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.oti_pro_produto)

    # Campo para mostrar a imagem do produto
    def produto_imagem_tag_small(self):
        imagem = TbProdutos.objects.get(id=self.oti_pro_produto_id).pro_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    produto_imagem_tag_small.short_description = 'Imagem'

    # Campo para mostrar a unidade do produto
    def produto_unidade(self):
        unidade = TbProdutos.objects.get(id=self.oti_pro_produto_id).pro_unidade_producao
        return unidade

    produto_unidade.short_description = 'Unidade'

    # Vamos criar um campo para mostrar o somatório do volume de vendas de todos os periodos
    def vendas_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoProdutoDaugther'," + str(
            self.id) + ", 'dau_valor_3'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    vendas_periodo.short_description = 'Vendas (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar o preço médio ponderado do produto para todos os periodos
    def preco_medio_periodo(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]
        cursor.close()
        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            return ''

    preco_medio_periodo.short_description = 'Preço Médio'

    # Vamos criar um campo para mostrar o custo variável médio ponderado do produto para todos os periodos
    def custo_variavel_medio_periodo(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]
        cursor.close()
        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            return ''

    custo_variavel_medio_periodo.short_description = 'Custo Var. Médio'

    # Vamos criar um campo para mostrar a margem de contribuição média ponderada do produto para todos os periodos
    def margem_contribuicao_media_periodo(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno1 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno2 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]
        cursor.close()
        if retorno_vendas != 0:
            return locale.format_string('%.2f', (retorno1 - retorno2) / retorno_vendas, True)
        else:
            return ''

    margem_contribuicao_media_periodo.short_description = 'Margem Contrib. Média'

    # Vamos criar um campo para mostrar o percentual da margem de contribuição do produto para todos os periodos
    def margem_percentual_periodo(self):
        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno1 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno2 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]
        cursor.close()
        if retorno1 != 0:
            return locale.format_string('%.2f', (retorno1 - retorno2) * 100 / retorno1, True)
        else:
            return ''

    margem_percentual_periodo.short_description = 'Margem Contrib. (%)'

    # Vamos criar um campo para mostrar a margem horária média ponderada do produto para todos os periodos
    def margem_horaria_media_periodo(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_7) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbotimizacaoprodutodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]
        cursor.close()
        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            return ''

    margem_horaria_media_periodo.short_description = 'Margem Horária Média'

    class Meta:
        verbose_name = '      Produto'
        verbose_name_plural = '      Produtos'
        ordering = ['oti_pro_produto', ]


class TbOtimizacaoProdutoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Volume Mínimo', default=0)
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Volume Máximo', default=0)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Vendas', default=0)
    dau_valor_5 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Preço Médio', default=0)
    dau_valor_6 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Custo Var. Médio', default=0)
    dau_valor_7 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Margem Cont. Horária Média', default=0)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbOtimizacaoProduto', on_delete=models.CASCADE, verbose_name='Mãe')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

    def __str__(self):
        return ''

    # Vamos criar um campo para mostrar a margem de contribuição bruta média
    def margem_cont_bruta_media(self):
        return self.dau_valor_5 - self.dau_valor_6

    margem_cont_bruta_media.short_description = 'Margem Cont. Bruta Média'

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

    def clean(self):
        if self.dau_valor_1 < 0:
            raise ValidationError('Vendas Mínimo deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_2 < 0:
            raise ValidationError('Vendas Máximo deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_2 < self.dau_valor_1:
            raise ValidationError('Vendas Máximo deve ser maior ou igual a Vendas Mínimo. Favor corrigir!')

    class Meta:
        verbose_name = 'Detalhe'
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']


class TbProdutoMercadoFluxo(models.Model):
    pro_mer_flu_variavel = models.IntegerField(verbose_name='Variável')
    pro_mer_flu_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, verbose_name='Produto')
    pro_mer_flu_mercado = models.ForeignKey(TbMercado, on_delete=models.CASCADE, verbose_name='Mercado')
    pro_mer_flu_fluxo_producao = models.ForeignKey(TbFluxoProducao, on_delete=models.CASCADE, verbose_name='Fluxo Produção')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.pro_mer_flu_produto) + '/' + str(self.pro_mer_flu_mercado) + '/' + str(
            self.pro_mer_flu_fluxo_producao)

    # Campo para mostrar a imagem do produto
    def produto_imagem_tag_small(self):
        imagem = TbProdutos.objects.get(id=self.pro_mer_flu_produto_id).pro_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    produto_imagem_tag_small.short_description = 'Imagem'
    produto_imagem_tag_small.allow_tags = True

    # Vamos criar um campo para mostrar o preço médio no periodo
    def preco_medio(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular
        # Expressão SQL
        sql = "select avg(dau_valor_4) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    preco_medio.short_description = 'Preço Médio'

    # Vamos criar um campo para mostrar o custo médio no periodo
    def custo_medio(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular
        # Expressão SQL
        sql = "select avg(dau_valor_5) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    custo_medio.short_description = 'Custo Médio'

    # Vamos criar um campo para mostrar o custo médio no periodo
    def margem_media(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular
        # Expressão SQL
        sql = "select avg(dau_valor_3) from otimizacao_tbprodutomercadofluxodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    margem_media.short_description = 'Margem Média'

    # Vamos criar um campo para mostrar o somatório do volume de vendas de todos os periodos
    def vendas_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbProdutoMercadoFluxoDaugther'," + str(
            self.id) + ", 'dau_valor_2'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    vendas_periodo.short_description = 'Vendas (' + inicio_periodo + ' a ' + fim_periodo + ')'

    class Meta:
        verbose_name = '    Produto / Mercado / Fluxo'
        verbose_name_plural = '    Produtos / Mercados / Fluxos'
        ordering = ['pro_mer_flu_variavel']


class TbProdutoMercadoFluxoDaugther(models.Model):
    dau_order    = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1  = models.BooleanField(blank=False, null=False, default=True, verbose_name='Fluxo')
    dau_valor_2  = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Vendas', default=0.00)
    dau_valor_3  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Margem Contrib.', default=0.00)
    dau_valor_4  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Preço', default=0.00)
    dau_valor_5  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Custo Variável', default=0.00)
    dau_valor_6  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Parcela Outbound', default=0.00)
    dau_valor_7  = models.BooleanField(blank=False, null=False, default=True, verbose_name='Equip.')
    dau_valor_8  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Parcela Itens', default=0.00)
    dau_valor_9  = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Parcela Inbound', default=0.00)
    dau_valor_10 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Parcela Manutenção', default=0.00)
    dau_valor_11 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Margem Horária', default=0.00)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbProdutoMercadoFluxo', on_delete=models.CASCADE, verbose_name='Mãe')
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

    '''
    # Vamos criar um campo para mostrar a margem horária
    def margem_horaria(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular a produtividade equivalente no gargalo
        # Primeiro pegando o id do fluxo de produção
        id_fluxo_producao = TbProdutoMercadoFluxo.objects.get(id=self.mae_id).pro_mer_flu_fluxo_producao_id

        # Expressão SQL
        sql = "call public.valor_produtividade_gargalo_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(self.dau_order) + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()
        # Retorna o valor da produtividade no equipamento selecionado para ser considerado na margem de contribuição horária.
        # Se não foi selecionado nenhum equipamento, retorna ZERO.
        # Se multiplicarmos pela margem de contribuição bruta (dau_valor_3) temos a margem horária.
        if retorno > 0:
            retorno = retorno * self.dau_valor_3
            retorno = locale.format_string('%.2f', retorno, True)
        else:
            retorno = 'ND'

        return retorno

    margem_horaria.short_description = 'Margem Horária'
    '''

    # Vamos criar um campo para mostrar o equipamento gargalo
    def equipamento_gargalo(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para mostrar o equipamento gargalo
        # Primeiro pegando o id do fluxo de produção
        id_fluxo_producao = TbProdutoMercadoFluxo.objects.get(id=self.mae_id).pro_mer_flu_fluxo_producao_id
        # Expressão SQL
        sql = "call public.gargalo_produtividade_fluxo_producao_order(" + str(id_fluxo_producao) + ", " + str(
            self.dau_order) + ", '')"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        return retorno

    equipamento_gargalo.short_description = 'Gargalo/Produtividade'

    class Meta:
        verbose_name = 'Detalhe'
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']


class TbProdutoMercado(models.Model):
    pro_mer_produto = models.ForeignKey(TbProdutos, on_delete=models.CASCADE, verbose_name='Produto')
    pro_mer_mercado = models.ForeignKey(TbMercado, on_delete=models.CASCADE, verbose_name='Mercado')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.pro_mer_produto) + '/' + str(self.pro_mer_mercado)

    # Campo para mostrar a imagem do produto
    def produto_imagem_tag_small(self):
        imagem = TbProdutos.objects.get(id=self.pro_mer_produto_id).pro_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    produto_imagem_tag_small.short_description = 'Imagem'
    produto_imagem_tag_small.allow_tags = True

    # Vamos criar um campo para mostrar o somatório do volume de vendas de todos os periodos
    def vendas_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbProdutoMercadoDaugther'," + str(
            self.id) + ", 'dau_valor_3'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Vamos criar um campo para mostrar o somatório do volume mínimo de vendas de todos os periodos
    def vendas_minimo_periodo(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume mínimo de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbProdutoMercadoDaugther'," + str(
            self.id) + ", 'dau_valor_1'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Vamos criar um campo para mostrar o somatório do volume máximo de vendas de todos os periodos
    def vendas_maximo_periodo(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume maximo de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbProdutoMercadoDaugther'," + str(
            self.id) + ", 'dau_valor_2'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    vendas_periodo.short_description = 'Vendas (' + inicio_periodo + ' a ' + fim_periodo + ')'
    vendas_minimo_periodo.short_description = 'Vendas Mín. (' + inicio_periodo + ' a ' + fim_periodo + ')'
    vendas_maximo_periodo.short_description = 'Vendas Máx. (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar o preço medio do produto / mercado
    def preco(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]

        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            # Vamos pegar a média aritimética pois não tivemos vendas sugeridas
            sql = "select avg(dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]

            return locale.format_string('%.2f', retorno, True)

        cursor.close()
    preco.short_description = 'Preço Médio'


    # Vamos criar um campo para mostrar o custo medio do produto / mercado
    def custo(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]

        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            # Vamos pegar a média aritimética pois não tivemos vendas sugeridas
            sql = "select avg(dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]

            return locale.format_string('%.2f', retorno, True)
        cursor.close()
    custo.short_description = 'Custo Médio'

    # Vamos criar um campo para mostrar a margem de contribuição média do produto / mercado
    def margem_contribuicao(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno1 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno2 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]

        if retorno_vendas != 0:
            return locale.format_string('%.2f', (retorno1 - retorno2) / retorno_vendas, True)
        else:

            # Vamos pegar a média aritimética pois não tivemos vendas sugeridas
            sql = "select avg(dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno1 = cursor.fetchone()[0]
            sql = "select avg(dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno2 = cursor.fetchone()[0]

            return locale.format_string('%.2f', retorno1 - retorno2, True)
        cursor.close()

    margem_contribuicao.short_description = 'Margem Contrib. Média'

    # Vamos criar um campo para mostrar a margem de contribuição percentual média do produto / mercado
    def margem_contribuicao_percentual(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno1 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3 * dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno2 = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]

        if retorno1 != 0:
            return locale.format_string('%.2f', (retorno1 - retorno2) * 100/ retorno1, True)
        else:
            sql = "select avg(dau_valor_5) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno1 = cursor.fetchone()[0]
            sql = "select avg(dau_valor_6) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno2 = cursor.fetchone()[0]
            if retorno1 > 0:
                return locale.format_string('%.2f', (retorno1 - retorno2) * 100 / retorno1, True)
            else:
                return ''
        cursor.close()

    margem_contribuicao_percentual.short_description = 'Margem Contrib.(%) Média'

    # Vamos criar um campo para mostrar a margem horária média do produto / mercado
    def margem_horaria(self):

        cursor = connection.cursor()

        # Expressão SQL
        sql = "select sum(dau_valor_3 * dau_valor_7) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(
            self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]

        sql = "select sum(dau_valor_3) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno_vendas = cursor.fetchone()[0]

        if retorno_vendas != 0:
            return locale.format_string('%.2f', retorno / retorno_vendas, True)
        else:
            # Vamos pegar a média aritimética pois não tivemos vendas sugeridas
            sql = "select avg(dau_valor_7) from otimizacao_tbprodutomercadodaugther where mae_id = " + str(self.id)
            cursor.execute(sql)
            retorno = cursor.fetchone()[0]

            return locale.format_string('%.2f', retorno, True)
        cursor.close()

    margem_horaria.short_description = 'Margem Horária Média'

    class Meta:
        verbose_name = '     Produto / Mercado'
        verbose_name_plural = '     Produtos / Mercados'
        ordering = ['pro_mer_produto', 'pro_mer_mercado']


class TbProdutoMercadoDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Volume Mínimo', default=0)
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Volume Máximo', default=0)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Vendas', default=0)
    dau_valor_5 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Preço Médio', default=0)
    dau_valor_6 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Custo Var. Médio', default=0)
    dau_valor_7 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Margem Cont. Horária Média', default=0)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbProdutoMercado', on_delete=models.CASCADE, verbose_name='Mãe')
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

    def __str__(self):
        return ''

    # Vamos criar um campo para mostrar a margem de contribuição bruta média
    def margem_cont_bruta_media(self):
        return self.dau_valor_5 -  self.dau_valor_6

    margem_cont_bruta_media.short_description = 'Margem Cont. Bruta Média'


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
        verbose_name = 'Detalhe'
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']


class TbOtimizacaoEquipamentos(models.Model):
    oti_equ_equipamento = models.ForeignKey(TbEquipamentosCadastro, on_delete=models.CASCADE,
                                            verbose_name='Equipamento')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.oti_equ_equipamento)

    def output_equipamento(self):  # Mostra o output do equipamento (t, m3, etc)
        return TbEquipamentosCadastro.objects.get(id=self.oti_equ_equipamento_id).equ_cad_output

    output_equipamento.short_description = 'Output'

    def gargalo_equipamento(self):  # Mostra se o equipamento é gargalo ou não
        return TbEquipamentosCadastro.objects.get(id=self.oti_equ_equipamento_id).equ_cad_gargalo

    gargalo_equipamento.short_description = 'Gargalo'
    gargalo_equipamento.boolean = True  # To show an icon instead of True or False

    # Campo para mostrar a imagem do equipamento
    def equipamento_imagem_tag_small(self):
        imagem = TbEquipamentosCadastro.objects.get(id=self.oti_equ_equipamento_id).equ_cad_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    equipamento_imagem_tag_small.short_description = 'Imagem'
    equipamento_imagem_tag_small.allow_tags = True

    # Vamos criar um campo para mostrar o somatório da produção de todos os periodos
    def producao_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        # Estamos usando o sp vendas_periodo mas nesse caso retorna a PRODUÇÂO de todos os periodos do cenário
        # Esse sp soma o campo informado da tabela filha (nesse caso da_valor_2) para todos os períodos
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosDaugther'," + str(self.id) + ", 'dau_valor_2'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    producao_periodo.short_description = 'Produção (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar a média da ocupação mínima especificada para todos os periodos
    def media_ocupacao_minima_periodo(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL
        # Expressão SQL
        sql = "select avg(dau_valor_5) from otimizacao_tbotimizacaoequipamentosdaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    media_ocupacao_minima_periodo.short_description = 'Média Ocup. Mín. % (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar a média da ocupação máxima especificada para todos os periodos
    def media_ocupacao_maxima_periodo(self):
        cursor = connection.cursor()
        # Vamos montar uma expressão SQL
        # Expressão SQL
        sql = "select avg(dau_valor_7) from otimizacao_tbotimizacaoequipamentosdaugther where mae_id = " + str(self.id)
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    media_ocupacao_maxima_periodo.short_description = 'Média Ocup. Máx. % (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar a ocupação durante todo o periodo
    def ocupacao_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do loading time de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosDaugther'," + str(
            self.id) + ", 'dau_valor_3'" + ", 0)"
        cursor.execute(sql)
        retorno_loading_time = cursor.fetchone()[0]
        cursor.close()

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório da produção em horas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosDaugther'," + str(
            self.id) + ", 'dau_valor_6'" + ", 0)"
        cursor.execute(sql)
        retorno_producao_horas = cursor.fetchone()[0]
        cursor.close()

        if retorno_loading_time > 0:
            retorno = retorno_producao_horas * 100 / retorno_loading_time
        else:
            retorno = 0.00

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    ocupacao_periodo.short_description = '% Ocupação (' + inicio_periodo + ' a ' + fim_periodo + ')'

    class Meta:
        verbose_name = '   Equipamento'
        verbose_name_plural = '   Equipamentos'
        ordering = ['oti_equ_equipamento']


class TbOtimizacaoEquipamentosDaugther(models.Model):
    dau_order   = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.BooleanField(blank=False, null=False, default=True, verbose_name='Running')
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Prod.(Volume)', default=0.00)
    dau_valor_3 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Loading Time(h)', default=0.00)
    dau_valor_4 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Ocupação(%)', default=0.00)
    dau_valor_5 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Ocupação Mín.(%)', default=0.00)
    dau_valor_7 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name='Ocupação Máx.(%)', default=100.00)
    dau_valor_6 = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='Prod.(Horas)', default=0.00)
    flag        = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')

    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbOtimizacaoEquipamentos', on_delete=models.CASCADE, verbose_name='Mãe')
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

    def clean(self):

        if self.dau_valor_5 < 0:
            raise ValidationError('Ocupação Mín.(%) deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_7 < 0:
            raise ValidationError('Ocupação Máx.(%) deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_7 < self.dau_valor_5:
            raise ValidationError('Ocupação Máx.(%) deve ser maior ou igual a Ocupação Mín.(%). Favor corrigir!')

    class Meta:
        verbose_name = 'Detalhe '
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']


class TbOtimizacaoEquipamentosOrdem(models.Model):
    oti_equ_ord_equipamento = models.ForeignKey(TbEquipamentos, on_delete=models.CASCADE,
                                                verbose_name='Equipamento/Ordem de Produção')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.oti_equ_ord_equipamento)

    def output_equipamento_ordem(self):  # Mostra o output do equipamento (t, m3, etc)
        # Vamos pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=self.oti_equ_ord_equipamento_id).equ_codigo_id
        return TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_output

    output_equipamento_ordem.short_description = 'Output'

    def gargalo_equipamento_ordem(self):  # Mostra se o equipamento é gargalo ou não
        # Vamos pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=self.oti_equ_ord_equipamento_id).equ_codigo_id
        return TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_gargalo

    gargalo_equipamento_ordem.short_description = 'Gargalo'
    gargalo_equipamento_ordem.boolean = True  # To show an icon instead of True or False

    # Campo para mostrar a imagem do equipamento
    def equipamento_ordem_imagem_tag_small(self):
        # Vamos pegar o id do equipamento
        id_equipamento = TbEquipamentos.objects.get(id=self.oti_equ_ord_equipamento_id).equ_codigo_id
        imagem = TbEquipamentosCadastro.objects.get(id=id_equipamento).equ_cad_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    equipamento_ordem_imagem_tag_small.short_description = 'Imagem'
    equipamento_ordem_imagem_tag_small.allow_tags = True

    def tipo_producao(self):  # Mostra o tipo de produção
        id_tipo_producao = TbEquipamentos.objects.get(id=self.oti_equ_ord_equipamento_id).equ_tipo_producao_id
        return TbTipoProducao.objects.get(id=id_tipo_producao).tip_nome

    tipo_producao.short_description = 'Tipo de Produção'

    # Vamos criar um campo para mostrar o somatório da produção de todos os periodos
    def producao_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosOrdemDaugther'," + str(
            self.id) + ", 'dau_valor_1'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    producao_periodo.short_description = 'Produção (' + inicio_periodo + ' a ' + fim_periodo + ')'

    # Vamos criar um campo para mostrar o percentual da produção de todos os periodos
    def percentual_periodo(self):

        cursor = connection.cursor()
        # Vamos montar uma expressão SQL para calcular o somatório do volume de vendas de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosOrdemDaugther'," + str(
            self.id) + ", 'dau_valor_1'" + ", 0)"
        cursor.execute(sql)
        retorno = cursor.fetchone()[0]
        cursor.close()

        # Vamos obter a produção total do equipamento
        # Temos que obter o id do equipamento
        id_equipamento_ordem = self.oti_equ_ord_equipamento_id
        id_equipamento = TbEquipamentos.objects.get(id=id_equipamento_ordem).equ_codigo_id
        mae_id = TbOtimizacaoEquipamentos.objects.get(oti_equ_equipamento_id=id_equipamento).id
        cursor = connection.cursor()

        # Vamos montar uma expressão SQL para calcular o somatório da produção de todos os períodos
        # Expressão SQL
        sql = "call public.vendas_periodo(" + str(self.tbcenarios_id) + ", 'otimizacao_TbOtimizacaoEquipamentosDaugther'," + str(
            mae_id) + ", 'dau_valor_2'" + ", 0)"
        cursor.execute(sql)
        retorno_producao_total = cursor.fetchone()[0]
        cursor.close()

        if retorno_producao_total > 0:
            retorno = retorno * 100 / retorno_producao_total
        else:
            retorno = 0.00

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR.utf8')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    # Temos que primeiro ver se a tabela TbCenarios existe no banco de dados
    all_tables = connection.introspection.table_names()
    inicio_periodo = ''
    fim_periodo = ''
    if 'parameters_tbcenarios' in all_tables:
        # 🌟 CORRIGIDO: protege contra a tabela existir mas faltar
        # coluna nova (acontece durante makemigrations de uma migration
        # ainda não aplicada).
        try:
            if TbCenarios.objects.filter(cen_ativo=True).count() == 1:
                inicio_periodo = TbCenarios.objects.get(cen_ativo=True).cen_inicio
                fim_periodo = TbCenarios.objects.get(cen_ativo=True).cen_fim
        except Exception:
            pass

    percentual_periodo.short_description = '% Prod. Equipamento (' + inicio_periodo + ' a ' + fim_periodo + ')'

    class Meta:
        verbose_name = '  Equipamento/Ordem de Produção'
        verbose_name_plural = '  Equipamentos/Ordem de Produção'
        ordering = ['oti_equ_ord_equipamento']


class TbOtimizacaoEquipamentosOrdemDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Produção (Volume)', default=0.00)
    dau_valor_2 = models.DecimalField(max_digits=9, decimal_places=0, verbose_name='Produção (Horas)', default=0.00)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='WIP (Volume)', default=0.00)
    dau_valor_4 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='WIP (Valor)', default=0.00)
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbOtimizacaoEquipamentosOrdem', on_delete=models.CASCADE, verbose_name='Mãe')
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

    # Vamos criar um campo para mostrar a participação do equipamento/ordem no total de produção do equipamento
    def participacao(self):

        if int(self.dau_order) >= 1:
            # Id do equipamento
            id_mae = TbOtimizacaoEquipamentosOrdem.objects.get(id=self.mae_id).oti_equ_ord_equipamento_id

            id_equipamento = TbEquipamentos.objects.get(id=id_mae).equ_codigo_id

            # Vamos pegar o id na tabela TbOtimizacaoEquipamentos
            id_mae = TbOtimizacaoEquipamentos.objects.get(oti_equ_equipamento_id=id_equipamento).id

            # Vamos ver a produção total do equipamento
            producao_total = TbOtimizacaoEquipamentosDaugther.objects.get(mae_id=id_mae,
                                                                          dau_order=self.dau_order).dau_valor_2
            if producao_total > 0:
                retorno = self.dau_valor_1 * 100 / producao_total
            else:
                retorno = 0

            # Vamos formatar o valor no padrão brasileiro

            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    participacao.short_description = 'Part. (%)'

    # Vamos criar um campo para mostrar se o equipamento/ordem esta running para o periodo
    def running(self):
        if int(self.dau_order) >= 1:
            id_equipamento_ordem = TbOtimizacaoEquipamentosOrdem.objects.get(id=self.mae_id).oti_equ_ord_equipamento
            retorno = TbEquipamentosDaugther.objects.get(mae_id=id_equipamento_ordem, dau_order=self.dau_order).dau_valor_3
            return retorno

    running.short_description = 'Running'
    running.boolean = True # Para mostrar um ícon

    class Meta:
        verbose_name = 'Detalhe '
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']

class TbOtimizacaoConjuntoEquipamentos(models.Model):
    oti_con_equ_descricao    = models.CharField(max_length=50, verbose_name='Descrição')
    oti_con_equ_ativo        = models.BooleanField(blank=False, null=False, default=True, verbose_name='Ativo')
    oti_con_equ_equipamentos = models.ManyToManyField(TbEquipamentosCadastro, verbose_name='Equipamentos')
    oti_con_equ_observacao   = models.TextField(verbose_name='Observação', blank=True, null=True)
    tbcenarios               = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem                = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.oti_con_equ_descricao)

    class Meta:
        verbose_name        = '  Equipamento/Conjunto'
        verbose_name_plural = '  Equipamentos/Conjunto'
        ordering            = ['oti_con_equ_descricao', ]
        unique_together     = ('oti_con_equ_descricao', 'tbcenarios')

    def nota(self):
        retorno = 'SOMENTE PERMITE A ESCOLHA DE EQUIPAMENTOS QUE NÃO SÃO GARGALO. SE NECESSÁRIO, ALTERAR EM EQUIPAMENTOS / CADASTRO.'
        return format_html(
            '<b style="color:{};">{}</b>',
            'blue',
            retorno,
        )
    nota.short_description = format_html(
                            '<b style="color:{};">{}</b>',
                            'blue',
                            'ATENÇÃO',
                        )

    def clean(self):
        self.oti_con_equ_descricao = self.oti_con_equ_descricao.upper()  # Passa a descrição para maiusculo antes de salvar

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbOtimizacaoConjuntoEquipamentos)
            self.flag = True

        # Vamos salvar o super save
        super(TbOtimizacaoConjuntoEquipamentos, self).save(*args, **kwargs)

        # Vamos atualizar a filha usando o Stored Procedure Verifica_Filha_2
        cursor = connection.cursor()
        # Montando a expressão sql para rodar o Stored Procedure Verifica_Filha
        sql = "call public.verifica_filha_2 ('otimizacao_tbotimizacaoconjuntoequipamentosdaugther', " + str(
            self.pk) + ", " + str(self.tbcenarios_id) + ", " + str(0) + ", " + str(999999999) + ")"
        cursor.execute(sql)
        cursor.close()

class TbOtimizacaoConjuntoEquipamentosDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=9, decimal_places=0, verbose_name='Produção Mínima', default=0)
    dau_valor_2 = models.DecimalField(max_digits=9, decimal_places=0, verbose_name='Produção Máxima', default=999999999)
    dau_valor_3 = models.DecimalField(max_digits=9, blank=True, null=True, decimal_places=0, verbose_name='Produção', default=0)
    mae = models.ForeignKey('TbOtimizacaoConjuntoEquipamentos', on_delete=models.CASCADE, verbose_name='Mãe')
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

    def clean(self):
        if self.dau_valor_1 < 0:
            raise ValidationError('Produção Mínima deve ser maior ou igual a zero. Favor corrigir!')
        if self.dau_valor_2 < 0:
            raise ValidationError('Produção Máxima deve ser maior ou igual a zero. Favor corrigir!')
        if self.dau_valor_2 < self.dau_valor_1:
            raise ValidationError('Produção Máxima deve ser maior ou igual a Produção Mínima. Favor corrigir!')

    class Meta:
        verbose_name = 'Produção Mínima e Máxima por Periodo'
        verbose_name_plural = 'Produções Mínimas e Máximas por Periodo'
        ordering = ['dau_order']

class TbOtimizacaoCustoItem(models.Model):
    oti_cus_ite_item = models.ForeignKey(TbCustoItemPreco, on_delete=models.CASCADE,
                                         verbose_name='Item de Custo/Unidade de Produção')
    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return str(self.oti_cus_ite_item)

    def unidade_item(self):  # Mostra a unidade do item de custo (t, m3, etc)
        id_custo_item = TbCustoItemPreco.objects.get(id=self.oti_cus_ite_item_id).cus_ite_pre_item_id
        return TbCustoItem.objects.get(id=id_custo_item).cus_ite_unidade

    unidade_item.short_description = 'Unidade'

    # Campo para mostrar a imagem do item de custo
    def custo_item_imagem_tag_small(self):
        id_custo_item = TbCustoItemPreco.objects.get(id=self.oti_cus_ite_item_id).cus_ite_pre_item_id
        imagem = TbCustoItem.objects.get(id=id_custo_item).cus_ite_imagem
        if imagem:
            return mark_safe('<img src="%s" style="width: 50px; height:60px;" />' % imagem.url)
        else:
            return 'Sem imagem!'

    custo_item_imagem_tag_small.short_description = 'Imagem'
    custo_item_imagem_tag_small.allow_tags = True

    class Meta:
        verbose_name = ' Item de Custo/Planta'
        verbose_name_plural = ' Itens de Custo/Planta'
        ordering = ['oti_cus_ite_item']


class TbOtimizacaoCustoItemDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Consumo Mínimo', default=0.00)
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Consumo Máximo', default=0.00)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Consumo Calculado', default=0.00)
    dau_valor_4 = models.BooleanField(blank=False, null=False, verbose_name='Ativo', default=False)
    dau_valor_5 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Estoque (Qtde)', default=0.00)
    dau_valor_6 = models.DecimalField(max_digits=18, decimal_places=2, verbose_name='Estoque (Valor)', default=0.00)

    flag = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    mae = models.ForeignKey('TbOtimizacaoCustoItem', on_delete=models.CASCADE, verbose_name='Mãe')
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

    def clean(self):
        if self.dau_valor_1 < 0:
            raise ValidationError('Consumo Mínimo deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_2 < 0:
            raise ValidationError('Consumo Máximo deve ser maior que zero. Favor corrigir!')
        if self.dau_valor_2 < self.dau_valor_1:
            raise ValidationError('Consumo Máximo deve ser maior ou igual a Consumo Mínimo. Favor corrigir!')

    class Meta:
        verbose_name = 'Detalhe '
        verbose_name_plural = 'Detalhes'
        ordering = ['dau_order']

class TbConsumoEspecificoTipoProducao(models.Model): # Essa tabela é para calcular itens de consumo por tipo de produção após o cálculo do resultado
    operacao_choice = (
                      ('Multiplicação', 'Multiplicação'),
                      ('Divisão', 'Divisão')
                      )
    con_esp_tip_pro_descricao                = models.CharField(max_length=100, null=False, blank=False, verbose_name='Descrição')
    con_esp_tip_pro_qtde_produzida           = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Produzida')
    con_esp_tip_pro_qtde_consumo             = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Qtde Consumo')
    con_esp_tip_pro_indicador                = models.DecimalField(max_digits=12, null=True, blank=True, decimal_places=4, verbose_name='Indicador')
    con_esp_tip_pro_indicador_referencia     = models.ForeignKey(TbConsumoEspecifico, related_name='con_esp_tip_pro_indicador_referencia', on_delete=models.CASCADE, blank=True, null=True, verbose_name='Referência')
    con_esp_tip_pro_tipo_producao            = models.ManyToManyField(TbTipoProducao, related_name='con_esp_tip_pro_tipo_producao', verbose_name='Tipo de Produção')
    con_esp_tip_pro_consumo_via_equipamento  = models.ManyToManyField(TbTipoProducao, related_name='con_esp_tip_pro_consumo_via_equipamento', blank=True, verbose_name='Consumo Via Equipamento')
    con_esp_tip_pro_ajuste1                  = models.ForeignKey(TbConsumoEspecifico, related_name='con_esp_tip_pro_ajuste1', on_delete=models.CASCADE, blank=True, null=True, verbose_name='Indicador Ajuste')
    con_esp_tip_pro_operacao1                = models.CharField(max_length = 13, choices=operacao_choice, blank=True, null=True, verbose_name='Operação')
    con_esp_tip_pro_consumo_via_item_consumo = models.ManyToManyField(TbCustoItem, blank=True, verbose_name='Consumo Via Item de Consumo')
    con_esp_tip_pro_ajuste2                  = models.ForeignKey(TbConsumoEspecifico, related_name='con_esp_tip_pro_ajuste2', on_delete=models.CASCADE, blank=True, null=True, verbose_name='Indicador Ajuste')
    con_esp_tip_pro_operacao2                = models.CharField(max_length=13, choices=operacao_choice, blank=True, null=True, verbose_name='Operação')
    con_esp_tip_pro_observacao               = models.TextField(verbose_name='Observação', blank=True, null=True)
    tbcenarios                               = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')
    id_origem                                = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela

    def __str__(self):
        return self.con_esp_tip_pro_descricao

    def clean(self):

        self.con_esp_tip_pro_descricao = self.con_esp_tip_pro_descricao.upper()  # Passa a descrição para maiusculo antes de salvar
        # Vamos verificar os ajustes e operação
        if self.con_esp_tip_pro_ajuste1 is None and self.con_esp_tip_pro_operacao1 is not None:
            raise ValidationError('Não foi informado Indicador Ajuste para Consumo Via Equipamento mas informado Operação. Remover Operação informada ou informe Indicador Ajuste!')
        if self.con_esp_tip_pro_ajuste1 is not None and self.con_esp_tip_pro_operacao1 is None:
            raise ValidationError('Foi informado Indicador Ajuste para Consumo Via Equipamento mas não informado Operação. Remover Indicador Ajuste ou informe Operação!')
        if self.con_esp_tip_pro_ajuste2 is None and self.con_esp_tip_pro_operacao2 is not None:
            raise ValidationError('Não foi informado Indicador Ajuste para Via Item de Consumo mas informado Operação. Remover Operação informada ou informe Indicador Ajuste!')
        if self.con_esp_tip_pro_ajuste2 is not None and self.con_esp_tip_pro_operacao2 is None:
            raise ValidationError('Foi informado Indicador Ajuste para Via Item de Consumo mas não informado Operação. Remover Indicador Ajuste ou informe Operação!')


    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbConsumoEspecificoTipoProducao)

        # Vamos salvar o super save
        super(TbConsumoEspecificoTipoProducao, self).save(*args, **kwargs)


    class Meta:
        verbose_name = ' Consumo Específico por Tipo de Produção'
        verbose_name_plural = ' Consumos Específicos por Tipo de Produção'
        ordering = ['con_esp_tip_pro_descricao']
        unique_together = ('con_esp_tip_pro_descricao', 'tbcenarios')

class TbOtimizacaoComparacaoCenarios(models.Model):
    oti_com_cenario_referencia = models.ForeignKey(TbCenarios, on_delete=models.CASCADE,
                                                   verbose_name='Cenário Referência',
                                                   related_name='oti_com_cenario_referencia')
    id_origem = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela
    tbcenarios = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário',
                                   related_name='tbcenarios_1')

    def __str__(self):
        #  return 'Cenário Referência ' + str(self.oti_com_cenario_referencia)
        return ''

    def clean(self):
        # Vamos pegar o cenário ativo
        cenario_ativo = TbCenarios.objects.get(cen_ativo=True)
        ativo = cenario_ativo.id

        # 🌟 NOVO: a comparação só faz sentido entre cenários do mesmo grupo,
        # mesmo tipo (Mensal/Trimestral/Anual) e mesmo período — caso contrário
        # vpl()/tir()/payback_descontado() acabam cruzando dau_order de
        # períodos que não representam o mesmo intervalo de tempo real.
        if self.oti_com_cenario_referencia_id:
            referencia = self.oti_com_cenario_referencia

            if referencia.cen_grupo_id != cenario_ativo.cen_grupo_id:
                raise ValidationError('O cenário de referência precisa estar no mesmo grupo do cenário ativo.')

            if referencia.cen_tipo != cenario_ativo.cen_tipo:
                raise ValidationError(
                    'O cenário de referência precisa ser do mesmo tipo (Mensal/Trimestral/Anual) do cenário ativo.')

            if referencia.cen_inicio != cenario_ativo.cen_inicio or referencia.cen_fim != cenario_ativo.cen_fim:
                raise ValidationError(
                    'O cenário de referência precisa ter o mesmo período (início e fim) do cenário ativo.')

        # Verificando se já foi cadastrado registro com o cenário de referência
        count = 0
        # Se estiver adicionando
        if self.pk is None:
            count = TbOtimizacaoComparacaoCenarios.objects.filter(
                oti_com_cenario_referencia=self.oti_com_cenario_referencia, tbcenarios_id=ativo).count()
        else:  # Está modificando
            # filter não aceita !=. Só aceita =. Seleciono e depois uso o exclude. Coisa de louco, mas funciona.
            count = TbOtimizacaoComparacaoCenarios.objects.filter(
                oti_com_cenario_referencia=self.oti_com_cenario_referencia, tbcenarios_id=ativo).exclude(
                id=self.pk).count()

        if count >= 1:
            raise ValidationError('Cenário de referência já cadastrado!')

    def vpl(self):
        # Cenário ativo
        cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id

        # Vamos obter o total de períodos para o cenário ativo
        cursor = connection.cursor()
        sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
        cursor.execute(sql)
        total_periodos = cursor.fetchone()[0]
        cursor.close()

        vpl_acumulado = 0
        for j in range(total_periodos):
            retorno_a = TbCenariosDaugther.objects.get(mae_id=self.oti_com_cenario_referencia_id,
                                                       dau_order=j + 1).dau_valor_20

            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=j + 1).dau_valor_20

            retorno = retorno_b - retorno_a
            wacc_acumulado = 1
            # Temos que calcular o WACC acumulado até j
            for i in range(j + 1):
                wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo_id,
                                                                                          dau_order=i + 1).dau_valor / 100)

            retorno = retorno / wacc_acumulado
            vpl_acumulado = vpl_acumulado + retorno

        retorno = vpl_acumulado
        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR')
        retorno = locale.format_string('%.0f', retorno, True)

        # Vamos ver a moeda da empresa
        moeda_empresa = TbEmpresa.objects.get(id=1).emp_moeda
        if moeda_empresa == 'BRL':
            retorno = 'R$ ' + retorno
        if moeda_empresa == 'USD':
            retorno = 'US$ ' + retorno
        if moeda_empresa == 'EUR':
            retorno = '€ ' + retorno

        return retorno

    vpl.short_description = 'VPL'

    def payback_descontado(self):
        # Cenário ativo
        cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id

        # Vamos obter o total de períodos para o cenário ativo
        cursor = connection.cursor()
        sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
        cursor.execute(sql)
        total_periodos = cursor.fetchone()[0]
        cursor.close()

        vpl_acumulado = 0
        pay_back = 0
        for j in range(total_periodos):
            retorno_a = TbCenariosDaugther.objects.get(mae_id=self.oti_com_cenario_referencia_id,
                                                       dau_order=j + 1).dau_valor_20

            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=j + 1).dau_valor_20

            retorno = retorno_b - retorno_a
            wacc_acumulado = 1
            # Temos que calcular o WACC acumulado até j
            for i in range(j + 1):
                wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo_id,
                                                                                          dau_order=i + 1).dau_valor / 100)

            retorno = retorno / wacc_acumulado
            vpl_acumulado = vpl_acumulado + retorno
            if vpl_acumulado < 0:
                pay_back = pay_back + 1
            else:  # Ficou positivo. Vamos calcular a parte fracionária
                if (abs(vpl_acumulado) + retorno) != 0:
                    pay_back = pay_back + abs(vpl_acumulado) / (abs(vpl_acumulado) + retorno)
                else:
                    pay_back = 0
                break

        if pay_back == total_periodos:  # Significa que chegou no final e não ficou positivo
            retorno = 999
        else:
            retorno = pay_back

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR')
        retorno = locale.format_string('%.2f', retorno, True)

        # Vamos ver o tipo de cenário
        tipo_cenario = TbCenarios.objects.get(cen_ativo=True).cen_tipo
        if tipo_cenario == 'Anual':
            retorno = retorno + ' ano(s)'
        if tipo_cenario == 'Mensal':
            retorno = retorno + ' mes(es)'
        if tipo_cenario == 'Trimestral':
            retorno = retorno + ' trimestre(s)'

        return retorno

    payback_descontado.short_description = 'Payback Descontado'

    def tir(self):
        # Cenário ativo
        cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id

        # Vamos obter o total de períodos para o cenário ativo
        cursor = connection.cursor()
        sql = "select conta_periodos(" + str(cen_ativo_id) + ")"
        cursor.execute(sql)
        total_periodos = cursor.fetchone()[0]
        cursor.close()

        lista_tir = []
        for j in range(total_periodos):
            retorno_a = TbCenariosDaugther.objects.get(mae_id=self.oti_com_cenario_referencia_id,
                                                       dau_order=j + 1).dau_valor_20

            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=j + 1).dau_valor_20

            retorno = retorno_b - retorno_a
            wacc_acumulado = 1
            # Temos que calcular o WACC acumulado até j
            for i in range(j + 1):
                wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo_id,
                                                                                          dau_order=i + 1).dau_valor / 100)

            lista_tir.append(retorno / wacc_acumulado)

        retorno = npf.irr(lista_tir) * 100

        # Vamos formatar o valor no padrão brasileiro
        # locale.setlocale(locale.LC_ALL, 'pt_BR')
        retorno = locale.format_string('%.2f', retorno, True)

        return retorno + ' (%)'

    tir.short_description = 'TIR (%)'

    class Meta:
        verbose_name = 'Comparação de Cenários'
        verbose_name_plural = 'Comparação de Cenários'
        ordering = ['oti_com_cenario_referencia']

    def save(self, *args, **kwargs):

        # Vamos ver se está adicionando ou modificando
        if self.pk is None:  # Nesse caso não existe a chave primária. Estamos adicionando. Vamos pegar o id do cenário para lançar no campo tbcenarios.
            pre_save.connect(atualiza_cenario, sender=TbOtimizacaoComparacaoCenarios)

        # Vamos salvar o super save
        super(TbOtimizacaoComparacaoCenarios, self).save(*args, **kwargs)


# Signals a serem executados na tabela TbOtimizacaoComparacaoCenarios
post_save.connect(verifica_filha_tbotimizacaocomparacaocenarios, sender=TbOtimizacaoComparacaoCenarios)

class TbOtimizacaoComparacaoCenariosDaugther(models.Model):
    dau_order = models.IntegerField(verbose_name='Ano/Mês')
    mae = models.ForeignKey('TbOtimizacaoComparacaoCenarios', on_delete=models.CASCADE, verbose_name='Mãe')
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

    # Criando campos para mostrar na tabela filha
    def ofcf_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_20
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    ofcf_referencia.short_description = 'OFCF Referência'

    def ofcf_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_20
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    ofcf_ativo.short_description = 'OFCF Ativo'

    def delta_ofcf(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_20

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_20

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_ofcf.short_description = 'Delta OFCF'

    def wacc(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo_id, dau_order=self.dau_order).dau_valor

            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    wacc.short_description = 'WACC (%)'

    def delta_ofcf_descontado(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_20

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_20

            retorno = retorno_b - retorno_a
            wacc_acumulado = 1
            # Temos que calcular o WACC acumulado até o dau_order
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            for i in range(self.dau_order):
                wacc_acumulado = wacc_acumulado * (1 + TbTaxaDescontoDaugther.objects.get(tbcenarios_id=cen_ativo_id,
                                                                                          dau_order=i + 1).dau_valor / 100)

            retorno = retorno / wacc_acumulado
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_ofcf_descontado.short_description = 'Delta OFCF Descontado'

    def vendas_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_1
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    vendas_referencia.short_description = 'Vendas Referência'

    def vendas_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_1
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    vendas_ativo.short_description = 'Vendas Ativo'

    def delta_vendas(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_1

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_1

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_vendas.short_description = 'Delta Vendas'

    def variavel_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_2
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    variavel_referencia.short_description = 'Variável Referência'

    def variavel_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_2
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    variavel_ativo.short_description = 'Variável Ativo'

    def delta_variavel(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_2

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_2

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_variavel.short_description = 'Delta Variável'

    def inbound_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_3
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    inbound_referencia.short_description = 'Inbound Referência'

    def inbound_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_3
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    inbound_ativo.short_description = 'Inbound Ativo'

    def delta_inbound(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_3

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_3

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_inbound.short_description = 'Delta Inbound'

    def outbound_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_4
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    outbound_referencia.short_description = 'Outbound Referência'

    def outbound_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_4
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    outbound_ativo.short_description = 'Outbound Ativo'

    def delta_outbound(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_4

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_4

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_outbound.short_description = 'Delta Outbound'

    def manutencao_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_5
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    manutencao_referencia.short_description = 'Manutenção Referência'

    def manutencao_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_5
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    manutencao_ativo.short_description = 'Manutenção Ativo'

    def delta_manutencao(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_5

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_5

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_manutencao.short_description = 'Delta Manutenção'

    def margem_contribuicao_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_6
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    margem_contribuicao_referencia.short_description = 'Margem Cont. Referência'

    def margem_contribuicao_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_6
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    margem_contribuicao_ativo.short_description = 'Margem Cont. Ativo'

    def delta_margem_contribuicao(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_6

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_6

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_margem_contribuicao.short_description = 'Delta Margem Cont.'

    def custo_fixo_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_7
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    custo_fixo_referencia.short_description = 'Custo Fixo Referência'

    def custo_fixo_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_7
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    custo_fixo_ativo.short_description = 'Custo Fixo Ativo'

    def delta_custo_fixo(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_7

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_7

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_custo_fixo.short_description = 'Delta Custo Fixo'

    def EBITDA_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_8
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    EBITDA_referencia.short_description = 'EBITDA Referência'

    def EBITDA_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_8
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    EBITDA_ativo.short_description = 'EBITDA Ativo'

    def delta_EBITDA(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_8

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_8

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_EBITDA.short_description = 'Delta EBITDA'

    def EBITDA_PERC_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_9
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    EBITDA_PERC_referencia.short_description = 'EBITDA (%) Referência'

    def EBITDA_PERC_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_9
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    EBITDA_PERC_ativo.short_description = 'EBITDA (%) Ativo'

    def delta_EBITDA_PERC(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_9

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_9

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    delta_EBITDA_PERC.short_description = 'Delta EBITDA (%)'

    def da_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_10
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    da_referencia.short_description = 'D&A Referência'

    def da_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_10
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    da_ativo.short_description = 'D&A Ativo'

    def delta_da(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_10

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_10

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_da.short_description = 'Delta D&A'

    def IR_PERC_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_11
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    IR_PERC_referencia.short_description = 'IR (%) Referência'

    def IR_PERC_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_11
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    IR_PERC_ativo.short_description = 'IR (%) Ativo'

    def delta_IR_PERC(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_11

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_11

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.2f', retorno, True)

        return retorno

    delta_IR_PERC.short_description = 'Delta IR (%)'

    def mp_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_12
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    mp_referencia.short_description = 'MP Referência'

    def mp_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_12
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    mp_ativo.short_description = 'MP Ativo'

    def delta_mp(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_12

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_12

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_mp.short_description = 'Delta MP'

    def wip_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_13
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    wip_referencia.short_description = 'WIP Referência'

    def wip_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_13
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    wip_ativo.short_description = 'WIP Ativo'

    def delta_wip(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_13

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_13

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_wip.short_description = 'Delta WIP'

    def pf_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_14
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    pf_referencia.short_description = 'PF Referência'

    def pf_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_14
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    pf_ativo.short_description = 'PF Ativo'

    def delta_pf(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_14

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_14

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_pf.short_description = 'Delta PF'

    def estoque_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_15
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    estoque_referencia.short_description = 'estoque Referência'

    def estoque_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_15
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    estoque_ativo.short_description = 'estoque Ativo'

    def delta_estoque(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_15

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_15

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_estoque.short_description = 'Delta estoque'

    def receber_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_16
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    receber_referencia.short_description = 'Receber Referência'

    def receber_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_16
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    receber_ativo.short_description = 'Receber Ativo'

    def delta_receber(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_16

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_16

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_receber.short_description = 'Delta Receber'

    def pagar_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_17
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    pagar_referencia.short_description = 'pagar Referência'

    def pagar_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_17
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    pagar_ativo.short_description = 'pagar Ativo'

    def delta_pagar(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_17

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_17

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_pagar.short_description = 'Delta pagar'

    def owcr_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_18
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    owcr_referencia.short_description = 'OWCR Referência'

    def owcr_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_18
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    owcr_ativo.short_description = 'OWCR Ativo'

    def delta_owcr(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_18

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_18

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_owcr.short_description = 'Delta OWCR'

    def capex_referencia(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_19
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    capex_referencia.short_description = 'capex Referência'

    def capex_ativo(self):

        if int(self.dau_order) >= 1:
            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_19
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    capex_ativo.short_description = 'capex Ativo'

    def delta_capex(self):

        if int(self.dau_order) >= 1:
            id_cenario = TbOtimizacaoComparacaoCenarios.objects.get(id=self.mae_id).oti_com_cenario_referencia_id
            retorno_a = TbCenariosDaugther.objects.get(mae_id=id_cenario, dau_order=self.dau_order).dau_valor_19

            cen_ativo_id = TbCenarios.objects.get(cen_ativo=True).id
            retorno_b = TbCenariosDaugther.objects.get(mae_id=cen_ativo_id, dau_order=self.dau_order).dau_valor_19

            retorno = retorno_b - retorno_a
            # Vamos formatar o valor no padrão brasileiro
            # locale.setlocale(locale.LC_ALL, 'pt_BR')
            retorno = locale.format_string('%.0f', retorno, True)

        return retorno

    delta_capex.short_description = 'Delta capex'

    class Meta:
        # verbose_name = 'Análise Comparativa Cenário Ativo - Cenário Referência (Valores em ' + TbEmpresa.objects.get(id=1).emp_moeda + ')'
        # verbose_name_plural = 'Análise Comparativa Cenário Ativo - Cenário Referência (Valores em ' + TbEmpresa.objects.get(id=1).emp_moeda + ')'
        verbose_name = 'Análise Comparativa Cenário Ativo - Cenário Referência'
        verbose_name_plural = 'Análise Comparativa Cenário Ativo - Cenário Referência'

        ordering = ['dau_order']


class TbOtimizacaoShadow(models.Model):
    class TipoRestricao(models.TextChoices):
            E  = ('E', 'EQUPAMENTO')
            CE = ('CE', 'CONJUNTO EQUIPAMENTOS')
            P  = ('P', 'PRODUTO')
            PN = ('PM', 'PRODUTO/MERCADO')
            IC = ('IC', 'ITEM DE CUSTO')
    oti_shadow_restricao = models.CharField(max_length=100, verbose_name='Restrição')
    oti_shadow_tipo = models.CharField(max_length=2, choices=TipoRestricao.choices, verbose_name='Tipo')
    oti_shadow_id_1 = models.IntegerField(blank=True, null=True, verbose_name='Id 1')
    oti_shadow_id_2 = models.IntegerField(blank=True, null=True, verbose_name='Id 2')
    dau_order       = models.IntegerField(blank=True, null=True, verbose_name='Periodo Order')
    dau_valor_1     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Sombra Mín' , default=0)
    dau_valor_2     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Sombra Máx' , default=0)
    dau_valor_3     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Res. Mín'   , default=0)
    dau_valor_4     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Res. Máx'   , default=0)
    dau_valor_5     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Mín'        , default=0)
    dau_valor_6     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Máx'        , default=0)
    dau_valor_7     = models.DecimalField(max_digits=18, decimal_places=0, verbose_name='Real'       , default=0)
    dau_valor_8     = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True, verbose_name='Lim Inf Mín', default=0)
    dau_valor_9     = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True, verbose_name='Lim Sup Mín', default=0)
    dau_valor_10    = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True, verbose_name='Lim Inf Máx', default=0)
    dau_valor_11    = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True, verbose_name='Lim Sup Máx', default=0)
    dau_texto_1     = models.CharField(max_length=20,  null=True, blank=True                    , verbose_name='Tipo')
    dau_texto_2     = models.CharField(max_length=300, null=True, blank=True                    , verbose_name='Conc Reduzir')  # concorrente ao reduzir
    dau_texto_3     = models.CharField(max_length=300, null=True, blank=True                    , verbose_name='Conc Aumentar')  # concorrente ao aumentar
    id_origem       = models.IntegerField(blank=True, null=True)  # Origem no caso de duplicação de tabela
    flag            = models.BooleanField(blank=True, null=True, default=False, verbose_name='Controle')
    # Este campo (flag) é somente para controle.
    # Se for igual a zero significa que é lixo. Deixamos na tabela pois pode voltar a ser utilizado
    # Se for igual a um (1) significa que está sendo usado no sistema
    tbcenarios      = models.ForeignKey(TbCenarios, on_delete=models.CASCADE, verbose_name='Cenário')

    def __str__(self):
        return ('Restrição: ') + str(self.oti_shadow_restricao)

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

    # 🌟 CORRIGIDO: protege contra a tabela/coluna ainda não existir
    # (acontece durante makemigrations de uma migration ainda não
    # aplicada) -- mesmo motivo já corrigido nos outros blocos deste
    # arquivo.
    try:
        if TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Anual':
            display_order.short_description = 'Ano'
        elif TbCenarios.objects.get(cen_ativo=True).cen_tipo == 'Trimestral':
            display_order.short_description = 'Ano/Trimestre'
        else:
            display_order.short_description = 'Ano/Mês'
    except Exception:
        display_order.short_description = 'Período'

    class Meta:
        verbose_name = 'Sombra da Otimização Simplex'
        verbose_name_plural = 'Sombras da Otimização Simplex'
        ordering = ['oti_shadow_tipo', 'oti_shadow_restricao', 'dau_order']