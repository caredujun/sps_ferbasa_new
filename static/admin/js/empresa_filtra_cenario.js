/*
 * 🌟 NOVO (multi-empresa): filtra o dropdown de "cenario_ativo" pra
 * mostrar só os cenários da empresa escolhida no formulário.
 *
 * JAVASCRIPT PURO, sem jQuery. Busca os campos de empresa/empresa_ativa
 * na página INTEIRA (não restringe a um "closest" de fieldset/linha) --
 * como só existe um formulário de PerfilUsuario por página (seja a tela
 * solta ou o inline de Usuário, que tem no máximo 1 linha), não há risco
 * de pegar o campo errado.
 */
(function() {
    function buscarUrlCenariosPorEmpresa(empresaId) {
        return '/parameters/cenarios-por-empresa/' + empresaId + '/';
    }

    function popularCenarios(selectCenario, empresaId, valorParaManterSelecionado) {
        if (!empresaId) {
            selectCenario.innerHTML = '';
            selectCenario.appendChild(new Option('---------', '', true, true));
            return;
        }

        fetch(buscarUrlCenariosPorEmpresa(empresaId), {credentials: 'same-origin'})
            .then(function(resposta) { return resposta.json(); })
            .then(function(dados) {
                var opcoes = dados.cenarios || [];
                var temValorInicial = !!valorParaManterSelecionado;

                selectCenario.innerHTML = '';
                selectCenario.appendChild(new Option('---------', '', !temValorInicial, !temValorInicial));

                opcoes.forEach(function(c) {
                    var estaSelecionado = temValorInicial && String(c.id) === String(valorParaManterSelecionado);
                    selectCenario.appendChild(new Option(c.texto, c.id, estaSelecionado, estaSelecionado));
                });
            })
            .catch(function(erro) {
                console.error('Não consegui carregar os cenários da empresa:', erro);
            });
    }

    function encontrarCampoEmpresa() {
        // O seletor [id$="-empresa"] já NÃO bate com "...-empresa_ativa"
        // (termina diferente) -- não precisa de filtro extra.
        return document.querySelector('select[id$="-empresa"], select[id="id_empresa"]');
    }

    function encontrarCampoEmpresaAtiva() {
        return document.querySelector('select[id$="-empresa_ativa"], select[id="id_empresa_ativa"]');
    }

    function conectarCampos() {
        var camposCenario = document.querySelectorAll('select[id$="-cenario_ativo"], select[id="id_cenario_ativo"]');
        var campoEmpresa = encontrarCampoEmpresa();
        var campoEmpresaAtiva = encontrarCampoEmpresaAtiva();

        function empresaEfetivaAtual() {
            var valor = campoEmpresa ? campoEmpresa.value : null;
            if (!valor && campoEmpresaAtiva) {
                valor = campoEmpresaAtiva.value;
            }
            return valor;
        }

        camposCenario.forEach(function(selectCenario) {
            if (selectCenario.dataset.empresaFiltraConectado) {
                return;
            }
            selectCenario.dataset.empresaFiltraConectado = '1';

            var valorInicial = selectCenario.value;

            // Carrega já com o valor atual (importante ao EDITAR um
            // usuário que já tem empresa e cenário definidos).
            popularCenarios(selectCenario, empresaEfetivaAtual(), valorInicial);

            if (campoEmpresa) {
                campoEmpresa.addEventListener('change', function() {
                    popularCenarios(selectCenario, empresaEfetivaAtual(), null);
                });
            }
            if (campoEmpresaAtiva) {
                campoEmpresaAtiva.addEventListener('change', function() {
                    popularCenarios(selectCenario, empresaEfetivaAtual(), null);
                });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', conectarCampos);
    } else {
        conectarCampos();
    }
})();