from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
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


# 🌟 NOVO (multi-idioma, Fase 1): idiomas suportados pela interface --
# reaproveitado tanto no idioma padrão da empresa (TbEmpresa) quanto no
# idioma individual do usuário (PerfilUsuario). Os códigos seguem o
# padrão do Django (settings.LANGUAGES precisa ter a mesma lista).
IDIOMA_CHOICES = (
    ('pt-br', 'Português'),
    ('en', 'English'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('de', 'Deutsch'),
    ('it', 'Italiano'),
    ('zh-hans', '中文（简体）'),
)


class AppOpcional(models.Model):
    """
    🌟 NOVO (multi-empresa): catálogo dos apps "opcionais" do sistema --
    nem toda empresa usa todo app (ex: custo_ferbasa é específico de um
    cliente). Cadastro centralizado aqui, referenciado pelo campo
    apps_habilitados de TbEmpresa. Pra habilitar um app novo pra alguma
    empresa: (1) cadastra ele aqui uma vez, (2) marca na empresa que deve
    ter acesso.
    """
    app_label = models.CharField(max_length=50, unique=True, verbose_name=_('App (nome técnico)'),
                                 help_text=_('Precisa bater com o nome real do app Django (ex: "custo_ferbasa").'))
    nome_exibicao = models.CharField(max_length=100, verbose_name=_('Nome de Exibição'))

    def __str__(self):
        return self.nome_exibicao

    class Meta:
        verbose_name = _('   App Opcional')
        verbose_name_plural = _('   Apps Opcionais')
        ordering = ['nome_exibicao']


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

    emp_nome = models.CharField(max_length=50, null=False, blank=False, verbose_name=_('Nome'))
    emp_tipo = models.CharField(max_length=10, choices=emp_tipo_choice, null=False, blank=False, verbose_name=_('Tipo'))
    emp_descricao = models.TextField(null=True, blank=False, verbose_name=_('Descrição'))
    emp_moeda = models.CharField(max_length=3, choices=EmpMoedaChoice.choices, null=False, blank=False, default='BRL', verbose_name=_('Moeda'))
    emp_moeda_imagem = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name=_('Imagem da Moeda'))
    emp_logo = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name=_('Logo'))
    emp_fluxo = models.ImageField(upload_to='empresa/', null=True, blank=True, verbose_name=_('Fluxo de Produção'))
    # 🌟 NOVO (multi-idioma, Fase 1): idioma padrão dessa empresa -- todo
    # usuário novo dela nasce com esse idioma (PerfilUsuario.idioma
    # vazio), mas pode trocar o próprio a qualquer momento.
    idioma_padrao = models.CharField(max_length=10, choices=IDIOMA_CHOICES, default='pt-br', verbose_name=_('Idioma Padrão'))
    # 🌟 NOVO (multi-empresa): próximo número a distribuir pra um cenário
    # novo DESSA empresa -- substitui o uso do id real da tabela (que
    # agora é compartilhado entre várias empresas) como "número visível"
    # do cenário. Começa em 1 pra toda empresa nova.
    proximo_numero_cenario = models.IntegerField(default=1, verbose_name=_('Próximo Número de Cenário'))
    # 🌟 NOVO (multi-empresa): quais apps opcionais essa empresa tem
    # acesso -- ex: "custo_ferbasa" só marcado pra empresa Ferbasa. Some
    # do menu do Admin (inclusive pro superusuário, respeitando a empresa
    # ativa dele) pra qualquer empresa que não tenha marcado.
    apps_habilitados = models.ManyToManyField(AppOpcional, blank=True, verbose_name=_('Apps Habilitados'))

    class Meta:
        verbose_name        = _('    Empresa')
        verbose_name_plural = _('    Empresa')

    def __str__(self):
        return self.emp_nome

    def emp_moeda_imagem_tag(self):
        if self.emp_moeda_imagem:
            return mark_safe('<img src="%s" style="width: 270px; height:90px;" />' % self.emp_moeda_imagem.url)
        else:
            return 'Sem imagem!'

    emp_moeda_imagem_tag.short_description = _('')
    emp_moeda_imagem_tag.allow_tags = True

    def emp_logo_tag(self):
        all_tables = connection.introspection.table_names()
        if 'parameters_tbempresa' in all_tables:
            if self.emp_logo:
                return mark_safe('<img src = "%s" style="width: 90px; height:90px;" />' % self.emp_logo.url)
            else:
                return 'Sem imagem!'

    emp_logo_tag.allow_tags = True
    emp_logo_tag.short_description = _('')

    def emp_fluxo_tag(self):
        all_tables = connection.introspection.table_names()
        if 'parameters_tbempresa' in all_tables:
            if self.emp_fluxo:
                return mark_safe('<img src="%s" style="width: 600px; height:130px;" />' % self.emp_fluxo.url)
            else:
                return 'Sem imagem!'

    emp_fluxo_tag.short_description = _('')
    emp_fluxo_tag.allow_tags = True

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

    # 🌟 CORRIGIDO (multi-empresa): unique=True tirado -- era uma trava
    # GLOBAL (só podia existir "AS IS ANUAL" uma vez em TODO o sistema,
    # o que impediria empresas diferentes de terem cenários com o mesmo
    # nome). A trava certa fica no Meta, abaixo: único POR EMPRESA.
    cen_nome = models.CharField(max_length=150, null=False, blank=False, verbose_name=_('Nome'))
    cen_descricao = models.TextField(null=True, blank=False, verbose_name=_('Descrição'))
    cen_tipo = models.CharField(max_length=10, choices=cen_tipo_choice, null=False, blank=False, verbose_name=_('Tipo'))
    cen_inicio = models.CharField(max_length=7, null=False, blank=False, verbose_name=_('Início'), default='2021/01')
    cen_fim = models.CharField(max_length=7, null=False, blank=False, verbose_name=_('Fim'), default='2021/12')
    cen_ativo = models.BooleanField(blank=False, null=False, default=False, verbose_name=_('Ativo'))
    objects = TbCenariosManager()
    objects_real = models.Manager()  # 🌟 NOVO — Manager sem interceptação, só pra lógica
    # interna de invariante (save() abaixo). Nunca use
    # este fora daqui — o resto do sistema deve continuar
    # usando "objects" (por usuário).
    cen_grupo = models.ForeignKey('tabelas.TbGrupoCenarios', blank=True, null=True, on_delete=models.CASCADE, verbose_name=_('Grupo'))
    cen_copiar_de = models.ForeignKey('self', null=True, blank=False, on_delete=models.SET_NULL, verbose_name=_('Copiar de '))
    flag = models.IntegerField(blank=True, null=True, verbose_name=_('Controle'))
    # 🌟 NOVO (multi-empresa): a que empresa esse cenário pertence -- fica
    # opcional (null=True) só na transição, pra não quebrar cenários já
    # existentes antes da migração de dados; todo cenário NOVO deve
    # sempre vir com isso preenchido.
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name=_('Empresa'))
    # 🌟 NOVO: número de exibição do cenário, POR EMPRESA -- substitui o
    # uso do id real da tabela (que passa a ser compartilhado entre
    # várias empresas, então não serve mais como "número visível"). É
    # atribuído a partir de empresa.proximo_numero_cenario no save().
    numero_sequencial = models.IntegerField(null=True, blank=True, verbose_name=_('Cenário'))
    # 🌟 NOVO: substitui a checagem antiga por id fixo (1, 2, 3) pra
    # proteger os cenários base (AS IS Mensal/Trimestral/Anual) contra
    # exclusão/renomeação -- com várias empresas, cada uma tem seus
    # próprios 3 cenários base, com ids reais diferentes.
    eh_cenario_base = models.BooleanField(default=False, verbose_name=_('É Cenário Base'))

    def __str__(self):
        # 🌟 CORRIGIDO (multi-empresa): usa numero_sequencial (por empresa)
        # em vez de self.pk (id real da tabela, agora compartilhado entre
        # várias empresas) -- mantém o "Cenário 28/Nome" que você já usa,
        # só que o "28" volta a fazer sentido como sequência da empresa.
        numero_exibido = self.numero_sequencial if self.numero_sequencial is not None else self.pk
        return "Cenário " + str(numero_exibido) + '/' + self.cen_nome

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
        verbose_name        = _('   Cenário')
        verbose_name_plural = _('   Cenários')
        ordering = ['id']  # 🌟 SIMPLIFICADO: antes era ['-cen_ativo', '-cen_grupo', 'id'] --
        # "-cen_ativo" não faz mais sentido sem um único cenário "ativo" global de
        # referência (agora é por usuário). TbCenariosAdmin já tem seu próprio
        # get_ordering (cenário ativo do usuário no topo) -- isso aqui só afeta
        # quem usa TbCenarios.objects.all() sem especificar ordenação.
        # 🌟 NOVO (multi-empresa): nome do cenário único POR EMPRESA, não
        # mais global -- substitui o antigo unique=True no campo cen_nome.
        constraints = [
            models.UniqueConstraint(fields=['cen_nome', 'empresa'], name='cenario_nome_unico_por_empresa')
        ]

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

    status_cenario.short_description = _('Status')

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
            # 🌟 NOVO (multi-empresa): cenários-base (os 3 criados
            # automaticamente pra empresa nova) não têm de onde copiar --
            # eles DEFINEM os próprios tipo/início/fim, em vez de herdar
            # de outro cenário (que nem existe ainda, no caso da primeira
            # empresa). Pra qualquer outro cenário, comportamento igual
            # a sempre: copia tipo/início/fim de cen_copiar_de.
            if not self.eh_cenario_base:
                cen_tipo = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_tipo
                cen_inicio = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_inicio
                cen_fim = TbCenarios.objects.get(id=self.cen_copiar_de_id).cen_fim
                self.cen_tipo = cen_tipo
                self.cen_inicio = cen_inicio
                self.cen_fim = cen_fim
            flag = 0
            self.flag = flag

            # 🌟 NOVO (multi-empresa, Parte 4): se ninguém já preencheu
            # "empresa" explicitamente (como faz o sinal que cria os
            # cenários-base), pega a empresa efetiva do usuário logado
            # nesse momento -- assim TODO cenário novo criado pelo
            # Admin/wizard já nasce vinculado à empresa certa, sem
            # precisar mexer em cada ponto de criação espalhado pelo
            # sistema.
            if self.empresa_id is None:
                usuario = get_usuario_atual()
                if usuario is not None:
                    perfil = getattr(usuario, 'perfilusuario', None)
                    if perfil is not None:
                        self.empresa_id = perfil.empresa_efetiva_id()

            # 🌟 NOVO (multi-empresa): distribui o número sequencial dessa
            # empresa pro cenário novo, e já avança o contador -- só roda
            # se "empresa" já estiver preenchida (isso passa a ser exigido
            # de verdade quando o isolamento por empresa for concluído;
            # por enquanto, sem empresa definida, o cenário fica sem
            # número -- __str__ cai de volta pro id real nesse caso).
            if self.empresa_id and self.numero_sequencial is None:
                with transaction.atomic():
                    empresa_obj = TbEmpresa.objects.select_for_update().get(id=self.empresa_id)
                    self.numero_sequencial = empresa_obj.proximo_numero_cenario
                    empresa_obj.proximo_numero_cenario += 1
                    empresa_obj.save()

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

        if status == 'Adicionando' and not self.eh_cenario_base:
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

        if status == 'Adicionando':
            # 🌟 Roda pra QUALQUER cenário novo, inclusive os base -- é o
            # que cria os registros de período (TbCenariosDaugther) pra
            # esse cenário; um cenário base também precisa disso, mesmo
            # sem ter de onde duplicar os outros dados.
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


# 🌟 NOVO (multi-empresa): ao excluir um cenário, se ele era o ÚLTIMO
# número distribuído pra essa empresa, devolve esse número pro próximo
# cenário criado reaproveitar -- reproduz o ajuste manual de sequência que
# já era feito antes, agora automático e isolado por empresa. Se não for
# o último (sobrou algum número no meio), não faz nada -- esse número
# fica "pulado", igual já acontecia.
from django.db.models.signals import pre_delete


def devolver_numero_sequencial_ao_excluir(sender, instance, **kwargs):
    if instance.empresa_id and instance.numero_sequencial is not None:
        with transaction.atomic():
            empresa_obj = TbEmpresa.objects.select_for_update().get(id=instance.empresa_id)
            if instance.numero_sequencial == empresa_obj.proximo_numero_cenario - 1:
                empresa_obj.proximo_numero_cenario -= 1
                empresa_obj.save()


pre_delete.connect(devolver_numero_sequencial_ao_excluir, sender=TbCenarios)


# 🌟 NOVO (multi-empresa): substitui o mecanismo antigo (código solto no
# admin.py, rodando uma vez, com ids fixos 1/2/3 -- pensado pra uma única
# empresa) -- agora, toda vez que uma EMPRESA NOVA é criada, ela ganha
# automaticamente um grupo "AS IS" e os 3 cenários base (Mensal/
# Trimestral/Anual) em branco, isolados por empresa, prontos pra servir
# de ponto de partida (copiar e ajustar).
from django.db.models.signals import post_save as _post_save_empresa


def criar_estrutura_base_para_empresa_nova(sender, instance, created, **kwargs):
    if not created:
        return

    # Import local pra evitar import circular (tabelas/models.py importa
    # TbCenarios daqui, então não dá pra importar tabelas no topo deste
    # arquivo).
    from tabelas.models import TbGrupoCenarios

    # 🌟 CORRIGIDO (multi-empresa, Parte 3): agora que TbGrupoCenarios
    # tem o campo empresa de verdade, ele entra no LOOKUP do
    # get_or_create (não só nos "defaults") -- sem isso, a segunda
    # empresa reaproveitaria por engano o grupo "AS IS" da PRIMEIRA
    # empresa (o lookup antigo checava só o código, ignorando a empresa).
    grupo, _ = TbGrupoCenarios.objects.get_or_create(
        gru_cen_codigo='AS IS', empresa=instance,
    )

    def _criar_cenario_base(nome, tipo, inicio, fim):
        cenario = TbCenarios(
            cen_nome=nome,
            cen_descricao=f'{nome}. Cenário base criado automaticamente pelo sistema!',
            cen_tipo=tipo, cen_inicio=inicio, cen_fim=fim, cen_ativo=False,
            cen_grupo=grupo, empresa=instance, eh_cenario_base=True,
        )
        cenario.save()

    # 🌟 CORRIGIDO: nome do cenário sem repetir o nome da empresa -- ela
    # já aparece separada no cabeçalho do Admin ("Empresa: X"), então
    # incluir de novo aqui só duplicava a informação na mesma linha.
    _criar_cenario_base('AS IS MENSAL', 'Mensal', '2022/01', '2022/12')
    _criar_cenario_base('AS IS TRIMESTRAL', 'Trimestral', '2022/01', '2022/04')
    _criar_cenario_base('AS IS ANUAL', 'Anual', '2022', '2031')


_post_save_empresa.connect(criar_estrutura_base_para_empresa_nova, sender=TbEmpresa)


class TbCenariosDaugther(models.Model):
    dau_order = models.IntegerField(blank=True, null=True, verbose_name=_('Ano/Mês'))
    otimizar = models.BooleanField(default=True, verbose_name=_('Otimizar'))
    dau_valor_1 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Vendas'), default=0)
    dau_valor_2 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Variável'), default=0.00)
    dau_valor_3 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Inbound'), default=0.00)
    dau_valor_4 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Outbound'), default=0.00)
    dau_valor_5 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Manut.'), default=0.00)
    dau_valor_6 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Margem'), default=0.00)
    dau_valor_7 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Fixo'), default=0.00)
    dau_valor_8 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('EBTIDA'), default=0.00)
    dau_valor_9 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name=_('EBTIDA (%)'),  default=0.00)
    dau_valor_10 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('D&A'), default=0.00)
    dau_valor_11 = models.DecimalField(max_digits=6, decimal_places=2, verbose_name=_('IR (%)'), default=0.00)
    dau_valor_12 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('MP'), default=0.00)
    dau_valor_13 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('WIP'), default=0.00)
    dau_valor_14 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('PF'), default=0.00)
    dau_valor_15 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Total Est.'), default=0.00)
    dau_valor_16 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Receber'), default=0.00)
    dau_valor_17 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('Pagar'), default=0.00)
    dau_valor_18 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('OWCR'), default=0.00)
    dau_valor_19 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('CAPEX'), default=0.00)
    dau_valor_20 = models.DecimalField(max_digits=18, decimal_places=0, verbose_name=_('OFCF'), default=0.00)
    flag = models.IntegerField(blank=True, null=True, verbose_name=_('Solúção'))
    mae = models.ForeignKey('TbCenarios', on_delete=models.CASCADE, verbose_name=_('Mãe'))

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

    solucao_otima.short_description = _('Solução')

    def str_dau_valor_1(self):
        return locale.format_string('%.0f', self.dau_valor_1,True)
    str_dau_valor_1.short_description = _('Vendas')

    def str_dau_valor_2(self):
        return locale.format_string('%.0f', self.dau_valor_2,True)
    str_dau_valor_2.short_description = _('Variável')

    def str_dau_valor_3(self):
        return locale.format_string('%.0f', self.dau_valor_3,True)
    str_dau_valor_3.short_description = _('Inbound')

    def str_dau_valor_4(self):
        return locale.format_string('%.0f', self.dau_valor_4,True)
    str_dau_valor_4.short_description = _('Outbound')

    def str_dau_valor_5(self):
        return locale.format_string('%.0f', self.dau_valor_5,True)
    str_dau_valor_5.short_description = _('Manut.')

    def str_dau_valor_6(self):
        return locale.format_string('%.0f', self.dau_valor_6,True)
    str_dau_valor_6.short_description = _('Margem')

    def str_dau_valor_7(self):
        return locale.format_string('%.0f', self.dau_valor_7,True)
    str_dau_valor_7.short_description = _('Fixo')

    def str_dau_valor_8(self):
        return locale.format_string('%.0f', self.dau_valor_8,True)
    str_dau_valor_8.short_description = _('EBTIDA')

    def str_dau_valor_9(self):
        return locale.format_string('%.0f', self.dau_valor_9,True)
    str_dau_valor_9.short_description = _('EBTIDA (%)')

    def str_dau_valor_10(self):
        return locale.format_string('%.0f', self.dau_valor_10,True)
    str_dau_valor_10.short_description = _('D&A')

    def str_dau_valor_11(self):
        return locale.format_string('%.0f', self.dau_valor_11,True)
    str_dau_valor_11.short_description = _('IR (%)')

    def str_dau_valor_12(self):
        return locale.format_string('%.0f', self.dau_valor_12,True)
    str_dau_valor_12.short_description = _('MP')

    def str_dau_valor_13(self):
        return locale.format_string('%.0f', self.dau_valor_13,True)
    str_dau_valor_13.short_description = _('WIP')

    def str_dau_valor_14(self):
        return locale.format_string('%.0f', self.dau_valor_14,True)
    str_dau_valor_14.short_description = _('PF')

    def str_dau_valor_15(self):
        return locale.format_string('%.0f', self.dau_valor_15,True)
    str_dau_valor_15.short_description = _('Total Estoque')

    def str_dau_valor_16(self):
        return locale.format_string('%.0f', self.dau_valor_16,True)
    str_dau_valor_16.short_description = _('Receber')

    def str_dau_valor_17(self):
        return locale.format_string('%.0f', self.dau_valor_17,True)
    str_dau_valor_17.short_description = _('Pagar')

    def str_dau_valor_18(self):
        return locale.format_string('%.0f', self.dau_valor_18,True)
    str_dau_valor_18.short_description = _('OWCR')

    def str_dau_valor_19(self):
        return locale.format_string('%.0f', self.dau_valor_19,True)
    str_dau_valor_19.short_description = _('Capex')

    def str_dau_valor_20(self):
        return locale.format_string('%.0f', self.dau_valor_20,True)
    str_dau_valor_20.short_description = _('OFCF')

    class Meta:
        verbose_name = _('Resultado')
        verbose_name_plural = _('Resultados')
        ordering = ['dau_order']

class TbCenariosDaugther1(models.Model): # Diário de Bordo
    data = models.DateField(verbose_name=_('Data'))
    o_que = models.TextField(verbose_name=_('O Que'))

    mae = models.ForeignKey('TbCenarios', on_delete=models.CASCADE, verbose_name=_('Mãe'))

    def __str__(self):
        return ''


    class Meta:
        verbose_name = _('Diário de Bordo')
        verbose_name_plural = _('Diário de Bordo')
        ordering = ['-data']

class TbGlossario(models.Model):
    glo_nome = models.CharField(max_length=50, null=False, blank=False, verbose_name=_('Nome'))
    glo_descricao = models.TextField(verbose_name=_('Descrição'), blank=False, null=False)
    glo_imagem = models.ImageField(upload_to='parameters', null=True, blank=True, verbose_name=_('Imagem'))
    glo_fonte = models.FileField(upload_to='parameters', null=True, blank=True, verbose_name=_('Fonte'))
    # 🌟 NOVO (multi-empresa, Parte 3): tabela independente de cenário.
    empresa = models.ForeignKey(TbEmpresa, null=True, blank=True, on_delete=models.PROTECT, verbose_name=_('Empresa'))

    def __str__(self):
        return self.glo_nome

    def glo_imagem_tag(self):
        if self.glo_imagem:
            return mark_safe('<img src="%s" style="width: 250px; height:120px;" />' % self.glo_imagem.url)
        else:
            return 'Sem imagem!'

    glo_imagem_tag.short_description = _('')

    def clean(self):

        self.glo_nome = self.glo_nome.upper()

        # 🌟 CORRIGIDO (multi-empresa): checagem POR EMPRESA, não mais
        # global.
        count = 0
        if self.pk is None:
            count = TbGlossario.objects.filter(glo_nome=self.glo_nome, empresa_id=self.empresa_id).count()
        else:
            count = TbGlossario.objects.filter(glo_nome=self.glo_nome, empresa_id=self.empresa_id).exclude(id=self.pk).count()

        if count >= 1:
            raise ValidationError('Glossário ' + self.glo_nome + ' já cadastrado!')

    class Meta:
        verbose_name = _('  Glossário')
        verbose_name_plural = _('  Glossário')
        ordering = ['glo_nome']

    def save(self, *args, **kwargs):
        # 🌟 NOVO (multi-empresa, Parte 3): preenche empresa
        # automaticamente na criação, mesmo mecanismo usado em
        # TbCenarios.save().
        if self.pk is None and self.empresa_id is None:
            usuario = get_usuario_atual()
            if usuario is not None:
                perfil = getattr(usuario, 'perfilusuario', None)
                if perfil is not None:
                    self.empresa_id = perfil.empresa_efetiva_id()
        super().save(*args, **kwargs)

# Tabelas para o agente de IA

class AgenteConfig(models.Model):
    nome = models.CharField(max_length=100, verbose_name=_('Nome'))
    prompt_sistema = models.TextField(help_text=_("Instruções para o Agente IA"), verbose_name=_('Instruções'))
    ativo = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.ativo:
            AgenteConfig.objects.filter(ativo=True).exclude(pk=self.pk).update(ativo=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return "Comportamento Agente IA"

    class Meta:
        verbose_name        = _(' Comportamento Agente IA')
        verbose_name_plural = _(' Comportamentos Agente IA')
        ordering = ['nome']

class HistoricoAgente(models.Model):
    data = models.DateTimeField(auto_now_add=True, verbose_name=_('Data'))
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('Usuário')
    )
    comando_usuario = models.TextField(verbose_name=_('Pergunta Usuário'))
    resposta_ia = models.TextField(verbose_name=_('Resposta Agente IA'))

    def __str__(self):
        return "Histórico Agente IA"

    class Meta:
        verbose_name        = _('Histórico Agente IA')
        verbose_name_plural = _('Históricos Agente IA')
        ordering = ['-data']


class RelatorioPDF(models.Model):
    titulo = models.CharField(_("Título do Relatório"), max_length=200)
    arquivo = models.FileField(_("Arquivo"), upload_to="relatorios_pdf/")
    ativo = models.BooleanField(_("Disponível para a IA"), default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    # 🌟 NOVO (multi-empresa): cada empresa só deve ver/usar os próprios
    # relatórios no Agente IA -- antes esse campo não existia, e todo
    # mundo via os relatórios de todas as empresas misturados.
    empresa = models.ForeignKey('TbEmpresa', null=True, blank=True, on_delete=models.PROTECT, verbose_name=_('Empresa'))

    class Meta:
        # 🌟 CORRIGIDO: renomeado de "Relatório(s) PDF" -- não é só arquivo
        # PDF que pode ser salvo aqui pra consulta do Agente IA (também
        # aceita TXT e XLSX), então o nome antigo dava a entender uma
        # limitação que não existe de verdade.
        verbose_name = _("Relatório Consulta IA")
        verbose_name_plural = _("Relatórios Consulta IA")

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        # 🌟 NOVO (multi-empresa): preenche empresa automaticamente na
        # criação, mesmo mecanismo usado em TbCenarios/TbGlossario.
        if self.pk is None and self.empresa_id is None:
            usuario = get_usuario_atual()
            if usuario is not None:
                perfil = getattr(usuario, 'perfilusuario', None)
                if perfil is not None:
                    self.empresa_id = perfil.empresa_efetiva_id()
        super().save(*args, **kwargs)


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
        verbose_name=_('Fluxo em andamento')
    )
    etapa_atual = models.CharField(max_length=50, null=True, blank=True, verbose_name=_('Etapa atual'))
    dados_coletados = models.JSONField(default=dict, blank=True, verbose_name=_('Dados coletados'))
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name=_('Atualizado em'))

    def __str__(self):
        if self.fluxo_ativo:
            return f"{self.usuario} -- {self.get_fluxo_ativo_display()} ({self.etapa_atual})"
        return f"{self.usuario} -- sem fluxo ativo"

    class Meta:
        verbose_name = _('Estado de Conversa do Agente')
        verbose_name_plural = _('Estados de Conversa do Agente')


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
        verbose_name=_('Cenário Ativo')
    )
    pode_trocar_cenario = models.BooleanField(
        default=True,
        verbose_name=_('Pode trocar cenário ativo'),
        help_text=_('Se desmarcado, o usuário não verá a opção de ativar outro cenário.')
    )
    # 🌟 NOVO (multi-empresa): "superusuário DA EMPRESA" -- tem os mesmos
    # poderes de um superusuário de verdade (criar/excluir cenário,
    # gerenciar usuários), mas restritos à própria empresa: nunca troca
    # de empresa, nunca vê/mexe em dado de outra. Diferente do
    # superusuário real, que continua tendo acesso irrestrito a tudo.
    # Só um superusuário de verdade pode conceder essa flag (ver
    # get_readonly_fields em PerfilUsuarioInline/Admin).
    eh_superuser_empresa = models.BooleanField(
        default=False,
        verbose_name=_('Superusuário da Empresa'),
        help_text=_('Tem todos os poderes de superusuário, mas restritos à própria empresa (não pode trocar de empresa nem ver dados de outras).')
    )
    # 🌟 NOVO (multi-idioma, Fase 1): idioma PESSOAL do usuário -- se
    # vazio, usa o idioma padrão da empresa dele (ver idioma_efetivo()).
    # Qualquer usuário pode trocar o próprio, a qualquer momento --
    # diferente de eh_superuser_empresa/apps_habilitados, aqui não tem
    # restrição de quem pode editar.
    idioma = models.CharField(
        max_length=10, choices=IDIOMA_CHOICES, null=True, blank=True,
        verbose_name=_('Idioma'),
        help_text=_('Se vazio, usa o idioma padrão da empresa.')
    )
    # 🌟 NOVO (multi-empresa, Parte 4):
    # - "empresa" é FIXA pro usuário comum -- definida no cadastro dele,
    #   ele só vê/mexe nos dados dela. Não é usada pra superusuário.
    # - "empresa_ativa" é só pro SUPERUSUÁRIO -- em qual empresa ele está
    #   trabalhando agora, trocável a qualquer momento (mesmo padrão do
    #   cenario_ativo acima, só que um nível acima).
    empresa = models.ForeignKey(
        TbEmpresa, on_delete=models.PROTECT, null=True, blank=True,
        related_name='usuarios',
        verbose_name=_('Empresa'),
        help_text=_('A empresa desse usuário -- ele só vê os dados dela. Não se aplica a superusuários.')
    )
    empresa_ativa = models.ForeignKey(
        TbEmpresa, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='superusuarios_com_esta_ativa',
        verbose_name=_('Empresa Ativa (superusuário)'),
        help_text=_('Só usado por superusuários -- em qual empresa ele está trabalhando agora.')
    )
    # 🌟 NOVO: lembra, pra cada empresa que o superusuário já trabalhou,
    # qual era o cenário ativo dele lá -- assim, trocar de empresa e
    # voltar não obriga escolher o cenário de novo toda vez. Formato:
    # {"<empresa_id>": <cenario_id>, ...}. Só relevante pra superusuário
    # (usuário comum tem uma única empresa, não alterna).
    cenarios_ativos_por_empresa = models.JSONField(
        default=dict, blank=True,
        verbose_name=_('Cenários ativos por empresa (memória)')
    )

    def lembrar_cenario_ativo_para_empresa_atual(self):
        """
        Salva o cenario_ativo atual como "o cenário dessa empresa" antes
        de trocar de empresa -- chamado logo ANTES de mudar empresa_ativa.
        """
        if self.empresa_ativa_id and self.cenario_ativo_id:
            mapa = dict(self.cenarios_ativos_por_empresa)
            mapa[str(self.empresa_ativa_id)] = self.cenario_ativo_id
            self.cenarios_ativos_por_empresa = mapa

    def restaurar_cenario_ativo_para_empresa(self, empresa_id):
        """
        Restaura o cenário que estava ativo da última vez que o
        superusuário trabalhou nessa empresa, se houver e se o cenário
        ainda pertencer a ela -- senão deixa None (força escolher de
        novo, como já acontecia antes).
        """
        cenario_id = self.cenarios_ativos_por_empresa.get(str(empresa_id))
        if cenario_id and TbCenarios.objects_real.filter(id=cenario_id, empresa_id=empresa_id).exists():
            self.cenario_ativo_id = cenario_id
        else:
            self.cenario_ativo_id = None

    def empresa_efetiva_id(self):
        """
        A empresa que deve valer AGORA pra esse usuário: pra
        superusuário, é a empresa_ativa (a que ele escolheu); pra usuário
        comum, é a empresa fixa dele. Ponto único de verdade -- todo
        filtro por empresa no sistema deve passar por aqui, em vez de
        cada lugar decidir sozinho qual campo checar.
        """
        if self.usuario.is_superuser:
            return self.empresa_ativa_id
        return self.empresa_id

    def idioma_efetivo(self):
        """
        🌟 NOVO (multi-idioma, Fase 1): o idioma que deve valer AGORA
        pra esse usuário -- o dele mesmo (self.idioma), se ele já
        escolheu um; senão, o idioma padrão da empresa efetiva dele
        (ver empresa_efetiva_id() acima). Ponto único de verdade,
        chamado pelo middleware que ativa o idioma em cada request.
        """
        if self.idioma:
            return self.idioma
        empresa_id = self.empresa_efetiva_id()
        if empresa_id is None:
            return 'pt-br'
        empresa = TbEmpresa.objects.filter(id=empresa_id).first()
        return empresa.idioma_padrao if empresa else 'pt-br'

    def __str__(self):
        return _('Perfil de %(usuario)s') % {'usuario': self.usuario}

    class Meta:
        verbose_name = _('Perfil de Usuário')
        verbose_name_plural = _('Perfis de Usuário')

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=get_user_model())
def criar_perfil_usuario(sender, instance, created, **kwargs):
    if created:
        PerfilUsuario.objects.get_or_create(usuario=instance)