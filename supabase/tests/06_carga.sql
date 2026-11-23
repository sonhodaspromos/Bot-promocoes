-- Teste de volume: cria milhares de registros, mede as consultas principais e desfaz tudo no final.
-- Como rodar:  select * from testes.carga();          -- 10.000 produtos (padrão)
--              select * from testes.carga(20000);
create or replace function testes.carga(p_produtos int default 10000)
returns table (etapa text, quantidade bigint, tempo_ms numeric, usa_indice boolean)
language plpgsql as $$
declare
  t0 timestamptz; v_mkt smallint; v_cat int; v_canal smallint; v_plano text;
  res_etapa text[] := '{}'; res_qtd bigint[] := '{}'; res_ms numeric[] := '{}'; res_idx boolean[] := '{}';
  n bigint; i int;
begin
  begin
    insert into public.marketplaces (codigo, nome, dominios_afiliado, modo) values ('carga', 'Carga', array['afiliado.carga.com.br'], 'real') returning id into v_mkt;
    insert into public.categorias (nome, slug) values ('Carga', 'carga') returning id into v_cat;
    insert into public.canais (codigo, tipo, nome, modo) values ('carga_tg', 'telegram', 'Carga', 'real') returning id into v_canal;

    -- 1. produtos
    t0 := clock_timestamp();
    insert into public.produtos (marketplace_id, id_externo, categoria_id, titulo, preco_atual, preco_original, nota)
    select v_mkt, 'C-' || g, v_cat,
           (array['Fone Bluetooth', 'Air Fryer', 'Smartwatch', 'Tênis Feminino', 'Perfume', 'Luminária LED'])[1 + g % 6] || ' modelo ' || g,
           50 + (g % 500), 100 + (g % 500), 4 + (g % 10) / 10.0
      from generate_series(1, p_produtos) g;
    res_etapa := res_etapa || 'inserir produtos (+ histórico de preço automático)'::text; res_qtd := res_qtd || p_produtos::bigint;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    -- 2. promoções + links validados
    t0 := clock_timestamp();
    insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status)
    select id, preco_atual, preco_original, 'aprovada' from public.produtos where marketplace_id = v_mkt;
    insert into public.links_afiliados (produto_id, url, validado)
    select id, 'https://afiliado.carga.com.br/p/' || id, true from public.produtos where marketplace_id = v_mkt;
    update public.promocoes set pontuacao = 70, classificacao = 'boa', confianca = 70, analise = '{}'::jsonb, analisada_em = now()
     where produto_id in (select id from public.produtos where marketplace_id = v_mkt);
    res_etapa := res_etapa || 'inserir promoções e links validados'::text; res_qtd := res_qtd || (2 * p_produtos)::bigint;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    -- 3. publicações (2 por promoção) -> marcam a promoção como publicada
    t0 := clock_timestamp();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
    select pr.id, v_canal, l.id, 'sucesso', now() - interval '2 days' - (g * interval '7 hours') - (pr.id % 600) * interval '1 minute'
      from public.promocoes pr join public.produtos p on p.id = pr.produto_id and p.marketplace_id = v_mkt
      join public.links_afiliados l on l.produto_id = p.id
      cross join generate_series(0, 1) g;
    res_etapa := res_etapa || 'inserir publicações'::text; res_qtd := res_qtd || (2 * p_produtos)::bigint;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    -- 4. cliques (5 por promoção) e fila (1 pendente para cada 2 promoções)
    t0 := clock_timestamp();
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ocorrido_em)
    select l.id, pr.id, v_canal, now() - (g * interval '3 hours')
      from public.promocoes pr join public.links_afiliados l on l.produto_id = pr.produto_id
      join public.produtos p on p.id = pr.produto_id and p.marketplace_id = v_mkt
      cross join generate_series(1, 5) g;
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, prioridade)
    select pr.id, v_canal, l.id, 1 + pr.id % 9
      from public.promocoes pr join public.links_afiliados l on l.produto_id = pr.produto_id
      join public.produtos p on p.id = pr.produto_id and p.marketplace_id = v_mkt
     where pr.id % 2 = 0;
    res_etapa := res_etapa || 'inserir cliques e fila'::text; res_qtd := res_qtd || (5 * p_produtos + p_produtos / 2)::bigint;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    analyze public.produtos; analyze public.promocoes; analyze public.links_afiliados;
    analyze public.historico_publicacoes; analyze public.cliques; analyze public.fila_publicacoes; analyze public.historico_precos;

    -- 5. consultas medidas
    t0 := clock_timestamp();
    select count(*) into n from public.produtos where marketplace_id = v_mkt and id_externo = 'C-' || (p_produtos / 2);
    execute format('explain (format text) select * from public.produtos where marketplace_id = %s and id_externo = %L', v_mkt, 'C-' || (p_produtos / 2)) into v_plano;
    res_etapa := res_etapa || 'achar produto pelo ID externo (evita duplicidade)'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || (v_plano ilike '%index%');

    t0 := clock_timestamp();
    select count(*) into n from public.produtos where titulo ilike '%smartwatch modelo 12%';
    execute 'explain (format text) select * from public.produtos where titulo ilike ''%smartwatch modelo 12%''' into v_plano;
    res_etapa := res_etapa || 'buscar produto pelo título (busca do painel)'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || (v_plano ilike '%index%');

    t0 := clock_timestamp();
    select count(*) into n from (select * from public.vw_promocoes_painel where status = 'publicada' order by publicada_em desc limit 50) x;
    res_etapa := res_etapa || 'painel: 50 ofertas publicadas mais recentes (com cliques e comissão)'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    t0 := clock_timestamp();
    select count(*) into n from public.vw_metricas_diarias;
    res_etapa := res_etapa || 'painel: métricas dos últimos 90 dias'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    t0 := clock_timestamp();
    select count(*) into n from public.historico_precos h join public.produtos p on p.id = h.produto_id
     where p.marketplace_id = v_mkt and h.produto_id = (select min(id) from public.produtos where marketplace_id = v_mkt);
    execute 'explain (format text) select * from public.historico_precos where produto_id = 1 order by registrado_em desc limit 30' into v_plano;
    res_etapa := res_etapa || 'histórico de preço de um produto'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || (v_plano ilike '%index%');

    t0 := clock_timestamp();
    select count(*) into n from public.pegar_proximas_publicacoes(10, 10);
    res_etapa := res_etapa || 'worker: pegar 10 itens da fila'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    t0 := clock_timestamp();
    update public.produtos set preco_atual = preco_atual * 1.5 where marketplace_id = v_mkt and id % 100 = 0;
    get diagnostics n = row_count;
    res_etapa := res_etapa || 'monitor: 1% dos produtos sobe de preço (pausa automática)'::text; res_qtd := res_qtd || n;
    res_ms := res_ms || round(extract(epoch from clock_timestamp() - t0) * 1000, 1); res_idx := res_idx || null::boolean;

    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null;
  end;

  for i in 1 .. coalesce(array_length(res_etapa, 1), 0) loop
    etapa := res_etapa[i]; quantidade := res_qtd[i]; tempo_ms := res_ms[i]; usa_indice := res_idx[i];
    return next;
  end loop;
end $$;
revoke execute on function testes.carga(int) from public, anon, authenticated;
