-- =====================================================================
-- 011 · Correção de desempenho encontrada pelo teste de volume
-- Com 10 mil ofertas, a consulta do painel levava ~15 s: o índice parcial de links
-- (só validados) não servia para buscar "o link ativo do produto".
-- =====================================================================
create index links_produto_idx on public.links_afiliados (produto_id, validado desc, validado_em desc);
