// Máscaras
django.jQuery(function(){
    django.jQuery('.mask-cenario-1').mask('0000', {reverse: true});
    django.jQuery('.mask-cenario-2').mask('0000/00', {reverse: true});
    django.jQuery('.mask-moeda').mask('000.000.000.000,00', {reverse: true});
    django.jQuery('.mask-percentual').mask('#,00%', {reverse: true});
    django.jQuery('.mask-cambio').mask('0.000,00', {reverse: true});
    django.jQuery('.mask-number10').mask('000.000,00', {reverse: true});
    django.jQuery('.mask-ordem').mask('00', {reverse: true});
    django.jQuery('.mask-consumoespecifico').mask('000.000,0000', {reverse: true});
    django.jQuery('.mask-var').mask('000.000,00', {reverse: true});
    django.jQuery('.mask-constanteformula').mask('0.000,000000', {reverse: true});
});

//django.jQuery(function(){

//    django.jQuery('.mask-constanteformula').mask('0.000,000000', {reverse: true});

//});

// Ajustando foreignKey na tabela Consumo Específico por equipamento. Seleciona somente itens da mesma planta do equipamento
//django.jQuery(function(){
    //django.jQuery('#id_equ_con_esp_equipamento').change(function() {
    //window.alert( "Campo equ_con_esp_equipamento foi alterado!" );
    //self.fields['equ_con_esp_custoitempreco'].queryset = TbCustoItemPreco.objects.filter(tbcenarios_id=ativo)
//    });
//});

