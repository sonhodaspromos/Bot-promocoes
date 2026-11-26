-- Reverte a 029 (republicar oferta) e volta ao esquema da 028.
drop function if exists public.republicar_oferta(bigint, numeric, numeric, boolean, boolean);
delete from public.configuracoes where chave = 'monitor.conferir_fila';
