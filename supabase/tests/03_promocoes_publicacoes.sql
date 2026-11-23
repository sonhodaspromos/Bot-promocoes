-- Testes · promoções, links de afiliado, criativos, canais, fila e histórico de publicações

-- Classifica uma promoção como se o motor de inteligência tivesse analisado (só para testes)
create or replace function testes.classificar(p_promo bigint, p_classe text default 'excelente')
returns void language plpgsql set search_path = pg_catalog as $$
begin
  update public.promocoes set pontuacao = case p_classe when 'excelente' then 85 when 'boa' then 60 else 20 end,
         classificacao = p_classe, confianca = 80, analise = '{"teste": true}'::jsonb, analisada_em = now()
   where id = p_promo;
end $$;
revoke execute on function testes.classificar(bigint, text) from public;

-- Cenário completo: produto + promoção aprovada + link validado + canal real + canal demo
create or replace function testes.cenario(out mkt smallint, out prod bigint, out promo bigint, out link bigint,
                                          out canal_real smallint, out canal_demo smallint)
language plpgsql as $$
declare b record;
begin
  select * into b from testes.base();
  mkt := b.mkt; prod := b.prod;
  insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status)
    values (prod, 100, 150, 'aprovada') returning id into promo;
  insert into public.links_afiliados (produto_id, promocao_id, url, validado, status_http)
    values (prod, promo, 'https://afiliado.teste.com.br/p/1', true, 200) returning id into link;
  perform testes.classificar(promo);
  insert into public.canais (codigo, tipo, nome, modo) values ('teste_real', 'telegram', 'Canal real', 'real') returning id into canal_real;
  insert into public.canais (codigo, tipo, nome, modo) values ('teste_demo', 'telegram', 'Canal demo', 'demo') returning id into canal_demo;
end $$;
revoke execute on function testes.cenario() from public;

-- Uma segunda oferta pronta para a fila (outro produto, link validado, classificada)
create or replace function testes.outra_oferta(p_mkt smallint, out promo bigint, out link bigint)
language plpgsql set search_path = pg_catalog as $$
declare v_prod bigint;
begin
  insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual, preco_original)
    values (p_mkt, 'T-' || gen_random_uuid(), 'Outro produto de teste', 50, 100) returning id into v_prod;
  insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status)
    values (v_prod, 50, 100, 'aprovada') returning id into promo;
  insert into public.links_afiliados (produto_id, promocao_id, url, validado)
    values (v_prod, promo, 'https://afiliado.teste.com.br/o/' || v_prod, true) returning id into link;
  perform testes.classificar(promo);
end $$;
revoke execute on function testes.outra_oferta(smallint) from public;

create or replace function testes.t_promocoes_publicacoes()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare c record; e text; e2 text; v numeric; n int; s text; f bigint; p2 bigint; m2 smallint;
begin
  teste := 'desconto é calculado automaticamente';
  begin
    select * into c from testes.cenario();
    select desconto_pct into v from public.promocoes where id = c.promo;
    ok := v = 33.33; detalhe := v || '%';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'segunda promoção ativa do mesmo produto é recusada';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.promocoes (produto_id, preco_promocional) values (%s, 90)', c.prod));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou duplicada');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'nova promoção é aceita depois que a anterior foi pausada';
  begin
    select * into c from testes.cenario();
    update public.promocoes set status = 'pausada' where id = c.promo;
    e := testes.erro_de(format('insert into public.promocoes (produto_id, preco_promocional) values (%s, 90)', c.prod));
    ok := e is null; detalhe := coalesce(e, 'aceita');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'promoção sem link validado não pode ser publicada';
  begin
    select * into c from testes.cenario();
    update public.links_afiliados set validado = false where id = c.link;
    insert into public.promocoes (produto_id, preco_promocional, status) values (c.prod, 95, 'aprovada') returning id into p2;
    e := testes.erro_de(format('update public.promocoes set status = ''publicada'' where id = %s', p2));
    ok := e like 'P0010%'; detalhe := coalesce(e, 'publicou sem link');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link fora dos domínios de afiliado não pode ser validado';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format($q$insert into public.links_afiliados (produto_id, url, validado) values (%s, 'https://loja-qualquer.com/p/1', true)$q$, c.prod));
    ok := e like 'P0011%'; detalhe := coalesce(e, 'validou link estranho');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'domínio disfarçado (afiliado.teste.com.br.golpe.com) é recusado; subdomínio legítimo é aceito';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format($q$insert into public.links_afiliados (produto_id, url, validado) values (%s, 'https://afiliado.teste.com.br.golpe.com/x', true)$q$, c.prod));
    e2 := testes.erro_de(format($q$insert into public.links_afiliados (produto_id, url, validado) values (%s, 'https://br.afiliado.teste.com.br/x', true)$q$, c.prod));
    ok := e like 'P0011%' and e2 is null; detalhe := coalesce(e, 'aceitou golpe') || ' | ' || coalesce(e2, 'subdomínio aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link com URL alterada perde a validação';
  begin
    select * into c from testes.cenario();
    update public.links_afiliados set url = 'https://afiliado.teste.com.br/p/2' where id = c.link;
    select validado::text into s from public.links_afiliados where id = c.link;
    ok := s = 'false'; detalhe := 'validado = ' || s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link repetido (mesma URL) é recusado';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format($q$insert into public.links_afiliados (produto_id, url) values (%s, 'https://afiliado.teste.com.br/p/1')$q$, c.prod));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link sem HTTPS é recusado';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format($q$insert into public.links_afiliados (produto_id, url) values (%s, 'http://afiliado.teste.com.br/p/9')$q$, c.prod));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'fila recusa link não validado';
  begin
    select * into c from testes.cenario();
    insert into public.links_afiliados (produto_id, url) values (c.prod, 'https://afiliado.teste.com.br/p/3') returning id into f;
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, f));
    ok := e like 'P0010%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'fila recusa promoção que não foi aprovada';
  begin
    select * into c from testes.cenario();
    update public.promocoes set status = 'encontrada' where id = c.promo;
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    ok := e like 'P0013%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'fila recusa a mesma promoção duas vezes no mesmo canal';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link);
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou duplicada');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'fila recusa link de outro produto';
  begin
    select * into c from testes.cenario();
    insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual) values (c.mkt, 'T-2', 'Outro produto', 50) returning id into p2;
    insert into public.links_afiliados (produto_id, url, validado) values (p2, 'https://afiliado.teste.com.br/p/outro', true) returning id into f;
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, f));
    ok := e like 'P0012%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'oferta demo não entra em canal real, mas entra em canal demo';
  begin
    select * into c from testes.cenario();
    insert into public.marketplaces (codigo, nome, modo, dominios_afiliado) values ('teste_demo', 'Demo', 'demo', array['demo.invalid']) returning id into m2;
    insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual) values (m2, 'D1', 'Produto demo', 10) returning id into p2;
    insert into public.promocoes (produto_id, preco_promocional, status) values (p2, 9, 'aprovada') returning id into f;
    insert into public.links_afiliados (produto_id, url, validado) values (p2, 'https://demo.invalid/d1', true);
    perform testes.classificar(f);
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, (select id from public.links_afiliados where produto_id = %s))', f, c.canal_real, p2));
    e2 := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, (select id from public.links_afiliados where produto_id = %s))', f, c.canal_demo, p2));
    ok := e like 'P0015%' and e2 is null; detalhe := coalesce(e, 'foi para canal real') || ' | demo: ' || coalesce(e2, 'aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'publicação com sucesso marca promoção publicada e conclui a fila';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    update public.fila_publicacoes set status = 'processando', tentativas = 1 where id = f;
    insert into public.historico_publicacoes (fila_id, promocao_id, canal_id, link_afiliado_id, resultado, id_mensagem_externa)
      values (f, c.promo, c.canal_real, c.link, 'sucesso', '123');
    select status into s from public.promocoes where id = c.promo;
    ok := s = 'publicada' and (select status from public.fila_publicacoes where id = f) = 'concluida'
          and (select publicada_em is not null from public.promocoes where id = c.promo);
    detalhe := 'promoção: ' || s || ', fila: ' || (select status from public.fila_publicacoes where id = f);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'erro na publicação reagenda; depois do limite de tentativas vira "falhou"';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, max_tentativas) values (c.promo, c.canal_real, c.link, 2) returning id into f;
    update public.fila_publicacoes set status = 'processando', tentativas = 1 where id = f;
    insert into public.historico_publicacoes (fila_id, promocao_id, canal_id, link_afiliado_id, resultado, erro) values (f, c.promo, c.canal_real, c.link, 'erro', 'timeout');
    select status into s from public.fila_publicacoes where id = f;
    update public.fila_publicacoes set status = 'processando', tentativas = 2 where id = f;
    insert into public.historico_publicacoes (fila_id, promocao_id, canal_id, link_afiliado_id, resultado, erro) values (f, c.promo, c.canal_real, c.link, 'erro', 'timeout');
    ok := s = 'pendente' and (select status from public.fila_publicacoes where id = f) = 'falhou';
    detalhe := '1º erro: ' || s || ', 2º erro: ' || (select status from public.fila_publicacoes where id = f);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'publicação em canal demo NÃO marca oferta real como publicada';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_demo, c.link, 'sucesso');
    select status into s from public.promocoes where id = c.promo;
    ok := s = 'aprovada' and (select bool_and(demo) from public.historico_publicacoes where promocao_id = c.promo);
    detalhe := 'promoção continua: ' || s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'pausar a promoção cancela o que estava na fila';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    update public.promocoes set status = 'pausada', motivo_status = 'preço subiu' where id = c.promo;
    select status into s from public.fila_publicacoes where id = f;
    ok := s = 'cancelada'; detalhe := 'fila: ' || s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'invalidar o link cancela a fila e pausa a promoção';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    update public.links_afiliados set validado = false, motivo_invalido = '404' where id = c.link;
    ok := (select status from public.fila_publicacoes where id = f) = 'cancelada'
          and (select status from public.promocoes where id = c.promo) = 'pausada';
    detalhe := 'fila: ' || (select status from public.fila_publicacoes where id = f) || ', promoção: ' || (select status from public.promocoes where id = c.promo);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'histórico de publicações não pode ser alterado';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso') returning id into f;
    e := testes.erro_de(format('update public.historico_publicacoes set resultado = ''erro'', erro = ''x'' where id = %s', f));
    ok := e like 'P0002%'; detalhe := coalesce(e, 'alterou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'publicação com erro precisa informar o motivo';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (%s, %s, %s, ''erro'')', c.promo, c.canal_real, c.link));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'criativo sem local é recusado; criativo repetido (mesmo hash) é recusado';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.criativos (promocao_id, tipo) values (%s, ''story'')', c.promo));
    insert into public.criativos (promocao_id, tipo, url, hash_sha256) values (c.promo, 'story', 'https://img.teste/1.jpg', repeat('a', 64));
    e2 := testes.erro_de(format('insert into public.criativos (promocao_id, tipo, url, hash_sha256) values (%s, ''story'', ''https://img.teste/2.jpg'', repeat(''a'', 64))', c.promo));
    ok := e like '23514%' and e2 like '23505%'; detalhe := coalesce(e, 'aceitou sem local') || ' | ' || coalesce(e2, 'aceitou repetido');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
