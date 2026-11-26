-- Reverte a 029 (republicar oferta) e volta ao esquema da 028.
drop function if exists public.republicar_oferta(bigint, numeric, numeric, boolean, boolean);
delete from public.configuracoes where chave = 'monitor.conferir_fila';
delete from public.configuracoes where chave = 'monitor.max_por_rodada';
update public.configuracoes set valor = '6' where chave = 'monitor.intervalo_pagina_horas' and valor = '1'::jsonb;
update public.configuracoes set minimo = 6 where chave = 'monitor.intervalo_pagina_horas';
update public.configuracoes set valor = '7' where chave = 'monitor.acompanhar_dias' and valor = '30'::jsonb;
