-- 026 · Monitor lê a página pública do produto (Mercado Livre) 1x por dia e atualiza preço/estoque:
-- o banco pausa a oferta sozinho se o preço subir além da tolerância (monitor.tolerancia_preco_pct) ou esgotar.
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('monitor.ler_preco_pagina', 'true', 'booleano', 'Conferir o preço na página do produto (lojas sem API, como o Mercado Livre)', null, null),
  ('monitor.intervalo_pagina_horas', '24', 'numero', 'De quantas em quantas horas conferir o preço na página', 6, 168)
on conflict (chave) do nothing;
