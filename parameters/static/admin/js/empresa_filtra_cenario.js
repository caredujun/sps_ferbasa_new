/*
 * 🌟 NOVO: mantém o dropdown "Cenário Ativo" sempre coerente com a
 * empresa escolhida NA MESMA TELA, mesmo antes de salvar -- sem isso,
 * trocar "Empresa" (usuário comum) ou "Empresa Ativa" (superusuário) só
 * refletia no dropdown de cenário depois de salvar e abrir a tela de
 * novo (a filtragem em Python, em PerfilUsuarioAdmin/PerfilUsuarioInline,
 * só enxerga o que já está salvo no banco).
 *
 * Endpoint usado: parameters.views.cenarios_por_empresa_json, registrado
 * em sps/urls.py como "parameters/cenarios-por-empresa/".
 *
 * Funciona tanto na tela direta de "Perfis de Usuário" (campos
 * id_empresa / id_empresa_ativa / id_cenario_ativo) quanto na seção
 * "Empresa e Cenário ativo" dentro da tela de Usuário (inline, campos
 * com prefixo tipo id_perfilusuario-0-empresa).
 */
(function () {
    'use strict';

    function buscarCenariosDaEmpresa(empresaId) {
        var url = '/parameters/cenarios-por-empresa/' + empresaId + '/';
        return fetch(url).then(function (resposta) { return resposta.json(); });
    }

    function repopularDropdownCenario(campoCenario, cenarios) {
        campoCenario.innerHTML = '';

        var opcaoVazia = document.createElement('option');
        opcaoVazia.value = '';
        opcaoVazia.textContent = '---------';
        campoCenario.appendChild(opcaoVazia);

        cenarios.forEach(function (cenario) {
            var opcao = document.createElement('option');
            opcao.value = cenario.id;
            opcao.textContent = cenario.texto;
            campoCenario.appendChild(opcao);
        });
    }

    function ligarCampoEmpresa(campoEmpresa, campoCenario) {
        if (!campoEmpresa || !campoCenario) return;

        campoEmpresa.addEventListener('change', function () {
            var empresaId = campoEmpresa.value;
            if (!empresaId) {
                repopularDropdownCenario(campoCenario, []);
                return;
            }
            buscarCenariosDaEmpresa(empresaId)
                .then(function (dados) {
                    repopularDropdownCenario(campoCenario, dados.cenarios || []);
                })
                .catch(function () {
                    // Falha de rede -- mantém a lista atual como está,
                    // sem quebrar o resto do formulário por causa disso.
                });
        });
    }

    function iniciar() {
        // Tela direta de "Perfis de Usuário" (PerfilUsuarioAdmin).
        ligarCampoEmpresa(document.getElementById('id_empresa'), document.getElementById('id_cenario_ativo'));
        ligarCampoEmpresa(document.getElementById('id_empresa_ativa'), document.getElementById('id_cenario_ativo'));

        // Seção "Empresa e Cenário ativo" dentro da tela de Usuário
        // (PerfilUsuarioInline) -- os campos têm prefixo de formset,
        // tipo "id_perfilusuario-0-empresa". Descobre o campo de
        // cenário irmão trocando só o final do id.
        document.querySelectorAll('select[id$="-empresa"]').forEach(function (campoEmpresa) {
            var prefixo = campoEmpresa.id.slice(0, -('empresa'.length));
            var campoCenario = document.getElementById(prefixo + 'cenario_ativo');
            ligarCampoEmpresa(campoEmpresa, campoCenario);
        });
        document.querySelectorAll('select[id$="-empresa_ativa"]').forEach(function (campoEmpresa) {
            var prefixo = campoEmpresa.id.slice(0, -('empresa_ativa'.length));
            var campoCenario = document.getElementById(prefixo + 'cenario_ativo');
            ligarCampoEmpresa(campoEmpresa, campoCenario);
        });
    }

    // Cobre tanto o caso do script carregar antes do DOM estar pronto
    // (espera o evento) quanto depois (roda direto).
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
})();