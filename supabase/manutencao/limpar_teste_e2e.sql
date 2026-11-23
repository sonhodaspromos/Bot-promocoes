-- Remove os dados do teste ponta a ponta da função de cliques (feito em 22/11/2026)
-- e a extensão pg_net, usada só nesse teste.
-- Os dados já estão desativados (marketplace "teste_e2e" desativado, link inativo,
-- promoção pausada) e não aparecem em nenhuma publicação.
-- Rode no SQL Editor do Supabase quando quiser.
delete from public.produtos where marketplace_id = (select id from public.marketplaces where codigo = 'teste_e2e');
delete from public.marketplaces where codigo = 'teste_e2e';
drop extension if exists pg_net;
