-- 022 · Mercado Livre: aceita o link curto oficial meli.la (gerado pela Central/Barra de Afiliados)
-- Já aplicado em produção em 26/11/2026 (mesmo conteúdo). O destino continua conferido em dominios_destino.
update public.marketplaces
   set dominios_afiliado = array['meli.la', 'mercadolivre.com'],
       padrao_afiliado = '^https://(meli\.la/[A-Za-z0-9]+|mercadolivre\.com/sec/[A-Za-z0-9]+)'
 where codigo = 'mercado_livre';
