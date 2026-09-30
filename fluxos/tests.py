from types import SimpleNamespace
from unittest.mock import patch, sentinel
import json

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from . import views
from .assistente_editor import inspecionar_fluxo
from .sincronizacao_fluxo_visual import salvar_fluxo_a_partir_do_json

salvar_fluxo_sem_transacao = getattr(
    salvar_fluxo_a_partir_do_json,
    '__wrapped__',
    salvar_fluxo_a_partir_do_json,
)


class EditorSecurityViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_editor_exige_autenticacao(self):
        request = self.factory.get('/fluxo_producao/editor/1/')
        request.user = AnonymousUser()

        response = views.editor_view(request, fluxo_id=1)

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_salvar_nao_e_isento_de_csrf(self):
        self.assertFalse(getattr(views.api_fluxo_salvar, 'csrf_exempt', False))

    def test_usuario_sem_empresa_efetiva_recebe_403(self):
        usuario = SimpleNamespace(
            perfilusuario=SimpleNamespace(empresa_efetiva_id=lambda: None)
        )

        with self.assertRaises(PermissionDenied):
            views._empresa_efetiva_id_ou_403(usuario)

    @patch('fluxos.views.get_object_or_404', return_value=sentinel.fluxo)
    def test_busca_do_fluxo_sempre_filtra_empresa_efetiva(self, mock_get_object_or_404):
        usuario = SimpleNamespace(
            perfilusuario=SimpleNamespace(empresa_efetiva_id=lambda: 37)
        )

        resultado = views._fluxo_acessivel_ou_404(usuario, 81)

        self.assertIs(resultado, sentinel.fluxo)
        _, filtros = mock_get_object_or_404.call_args
        self.assertEqual(filtros['id'], 81)
        self.assertEqual(filtros['tbcenarios__empresa_id'], 37)


class EditorGraphIsolationTests(SimpleTestCase):
    @staticmethod
    def _grafo(equipamentos_ids):
        ids = list(equipamentos_ids)
        nodes = {}
        for indice, equipamento_id in enumerate(ids):
            node_id = str(indice + 1)
            nodes[node_id] = {
                'id': node_id,
                'name': f'Equipamento {equipamento_id}',
                'data': {'equipamento_id': equipamento_id},
                'inputs': {'input_1': {'connections': []}},
                'outputs': {'output_1': {'connections': []}},
                'pos_x': indice * 100,
                'pos_y': 0,
            }
        return {'drawflow': {'Home': {'data': nodes}}}

    @patch('fluxos.sincronizacao_fluxo_visual.verificar_consistencia_grafo', return_value=[])
    @patch('fluxos.sincronizacao_fluxo_visual.TbFluxoProducao.objects.filter')
    @patch('fluxos.sincronizacao_fluxo_visual.TbEquipamentos.objects.filter')
    def test_recusa_equipamento_de_outro_cenario(
        self, mock_equipamentos_filter, mock_fluxo_filter, _mock_consistencia
    ):
        mock_equipamentos_filter.return_value.values_list.return_value = [10]
        fluxo = SimpleNamespace(id=5, tbcenarios_id=7)
        dados = self._grafo([10, 99])

        ok, problemas = salvar_fluxo_sem_transacao(fluxo, dados)

        self.assertFalse(ok)
        self.assertIn('99', problemas[0])
        mock_equipamentos_filter.assert_called_once_with(
            id__in={10, 99},
            tbcenarios_id=7,
        )
        mock_fluxo_filter.assert_not_called()

    @patch('fluxos.sincronizacao_fluxo_visual.verificar_consistencia_grafo', return_value=[])
    @patch('fluxos.sincronizacao_fluxo_visual.TbFluxoProducao.objects.filter')
    @patch('fluxos.sincronizacao_fluxo_visual.TbEquipamentos.objects.filter')
    def test_aceita_equipamentos_do_mesmo_cenario(
        self, mock_equipamentos_filter, mock_fluxo_filter, _mock_consistencia
    ):
        mock_equipamentos_filter.return_value.values_list.return_value = [10, 11]
        fluxo = SimpleNamespace(id=5, tbcenarios_id=7)
        dados = self._grafo([10, 11])

        ok, problemas = salvar_fluxo_sem_transacao(fluxo, dados)

        self.assertTrue(ok)
        self.assertIsNone(problemas)
        mock_fluxo_filter.assert_called_once_with(id=5)
        mock_fluxo_filter.return_value.update.assert_called_once_with(
            flu_pro_dados_fluxo=dados
        )


class EditorAssistantInspectionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.grafo = {
            'drawflow': {'Home': {'data': {
                '1': {
                    'name': 'EQ-A/1',
                    'data': {'equipamento_id': 10, 'codigo': 'EQ-A/1'},
                    'inputs': {'input_1': {'connections': []}},
                    'outputs': {'output_1': {'connections': [{'node': '2'}]}},
                },
                '2': {
                    'name': 'EQ-B/2',
                    'data': {'equipamento_id': 11, 'codigo': 'EQ-B/2'},
                    'inputs': {'input_1': {'connections': [{'node': '1'}]}},
                    'outputs': {'output_1': {'connections': []}},
                },
            }}}
        }

    @patch('fluxos.assistente_editor.verificar_consistencia_grafo', return_value=[])
    @patch('fluxos.assistente_editor.TbEquipamentos.objects.filter')
    def test_inspecao_resume_nos_ligacoes_entradas_e_saidas(
        self, mock_equipamentos_filter, _mock_consistencia
    ):
        equipamentos = [
            SimpleNamespace(
                id=10,
                equ_codigo=SimpleNamespace(equ_cad_codigo='EQ-A'),
                equ_ordem_codigo=1,
                equ_ordem_descricao='Entrada',
            ),
            SimpleNamespace(
                id=11,
                equ_codigo=SimpleNamespace(equ_cad_codigo='EQ-B'),
                equ_ordem_codigo=2,
                equ_ordem_descricao='Saída',
            ),
        ]
        mock_equipamentos_filter.return_value.select_related.return_value = equipamentos
        fluxo = SimpleNamespace(
            id=5,
            tbcenarios_id=7,
            tbcenarios=SimpleNamespace(id=7, numero_sequencial=3, cen_nome='Teste'),
            flu_pro_nome='',
            flu_pro_descricao='Fluxo principal',
            flu_pro_produto_id=1,
            flu_pro_produto='Produto A',
            flu_pro_dados_fluxo={},
        )

        resultado = inspecionar_fluxo(fluxo, dados_fluxo=self.grafo)

        self.assertTrue(resultado['valido'])
        self.assertEqual(resultado['resumo']['total_nos'], 2)
        self.assertEqual(resultado['resumo']['total_ligacoes'], 1)
        self.assertEqual(resultado['raizes'], ['EQ-A/1'])
        self.assertEqual(resultado['terminais'], ['EQ-B/2'])

    @patch('fluxos.assistente_editor.inspecionar_fluxo')
    @patch('fluxos.views._fluxo_acessivel_ou_404', return_value=sentinel.fluxo)
    def test_api_analisa_o_json_atualmente_aberto_sem_salvar(
        self, _mock_fluxo, mock_inspecionar
    ):
        mock_inspecionar.return_value = {
            'fluxo': {'id': 5},
            'resumo': {'total_nos': 2},
            'valido': True,
            'problemas': [],
        }
        request = self.factory.post(
            '/fluxo_producao/api/fluxo/5/inspecao/',
            data=json.dumps({'dados_fluxo': self.grafo}),
            content_type='application/json',
        )
        request.user = SimpleNamespace(is_authenticated=True)

        response = views.api_fluxo_inspecao(request, fluxo_id=5)

        self.assertEqual(response.status_code, 200)
        mock_inspecionar.assert_called_once_with(
            sentinel.fluxo,
            dados_fluxo=self.grafo,
        )
