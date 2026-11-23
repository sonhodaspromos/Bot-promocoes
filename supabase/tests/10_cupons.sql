-- Testes · cupons de desconto e campanhas (migration 016)
create or replace function testes.novo_cupom(p_mkt smallint, p_codigo text default 'TESTE10', p_dias int default 7)
returns bigint language sql set search_path = pg_catalog as $$
  insert into public.cupons (marketplace_id, titulo, codigo, desconto_tipo, desconto_valor, valor_minimo, fim_em)
  values (p_mkt, 'Cupom de teste', p_codigo, 'percentual', 10, 50, now() + make_interval(days => p_dias)) returning id;
$$;
revoke execute on function testes.novo_cupom(smallint, text, int) from public;

create or replace function testes.t_cupons()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; e text; e2 text; e3 text; cup bigint; v text; n int; u uuid; b record;
begin
  teste := 'cupom precisa de código; código vira maiúsculo e sem espaços';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.cupons (marketplace_id, titulo, fim_em) values (%s, %L, now() + interval ''1 day'')', c.mkt, 'Sem código'));
    cup := testes.novo_cupom(c.mkt, '  frete10 ');
    select codigo into v from public.cupons where id = cup;
    ok := e like '23514%' and v = 'FRETE10'; detalhe := coalesce(e, 'aceitou sem código') || ' | código salvo: ' || v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link do cupom precisa ser de afiliado (domínio e formato)';
  begin
    select * into c from testes.cenario();
    update public.marketplaces set padrao_afiliado = '^https://afiliado\.teste\.com\.br/sec/' where id = c.mkt;
    e := testes.erro_de(format('insert into public.cupons (marketplace_id, tipo, titulo, link_afiliado, fim_em) values (%s, ''campanha'', ''Campanha'', ''https://golpe.com/x'', now() + interval ''1 day'')', c.mkt));
    e2 := testes.erro_de(format('insert into public.cupons (marketplace_id, tipo, titulo, link_afiliado, fim_em) values (%s, ''campanha'', ''Campanha'', ''https://afiliado.teste.com.br/p/1'', now() + interval ''1 day'')', c.mkt));
    e3 := testes.erro_de(format('insert into public.cupons (marketplace_id, tipo, titulo, link_afiliado, fim_em) values (%s, ''campanha'', ''Campanha'', ''https://afiliado.teste.com.br/sec/AbC'', now() + interval ''1 day'')', c.mkt));
    ok := e like 'P0011%' and e2 like 'P0024%' and e3 is null; detalhe := coalesce(e, '-') || ' | ' || coalesce(e2, '-') || ' | ' || coalesce(e3, 'com atribuição ok');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'informações inconsistentes são recusadas (desconto > 90%, validade invertida, campanha sem link)';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.cupons (marketplace_id, titulo, codigo, desconto_tipo, desconto_valor, fim_em) values (%s, ''X cupom'', ''ABC'', ''percentual'', 95, now() + interval ''1 day'')', c.mkt));
    e2 := testes.erro_de(format('insert into public.cupons (marketplace_id, titulo, codigo, inicio_em, fim_em) values (%s, ''X cupom'', ''ABC'', now(), now() - interval ''1 day'')', c.mkt));
    e3 := testes.erro_de(format('insert into public.cupons (marketplace_id, tipo, titulo, fim_em) values (%s, ''campanha'', ''Sem link'', now() + interval ''1 day'')', c.mkt));
    ok := e like '23514%' and e2 like '23514%' and e3 like '23514%'; detalhe := e || ' | ' || e2 || ' | ' || e3;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom publica no máximo 1 vez por dia em cada canal';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (cup, c.canal_real, 'sucesso');
    e := testes.erro_de(format('insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (%s, %s, ''sucesso'')', cup, c.canal_real));
    insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (cup, c.canal_demo, 'sucesso');
    ok := e like '23505%'; detalhe := coalesce(e, 'publicou 2 vezes no mesmo dia');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom vencido ou pausado não é publicado (P0025) e vira "expirado" sozinho';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    update public.cupons set status = 'pausado' where id = cup;
    e := testes.erro_de(format('insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (%s, %s, ''sucesso'')', cup, c.canal_real));
    insert into public.cupons (marketplace_id, titulo, codigo, inicio_em, fim_em)
      values (c.mkt, 'Vencido', 'VELHO', now() - interval '3 days', now() - interval '1 day') returning id into cup;
    select status into v from public.cupons where id = cup;
    ok := e like 'P0025%' and v = 'expirado'; detalhe := coalesce(e, 'publicou pausado') || ' | vencido: ' || v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom de demonstração não vai para canal real (P0015)';
  begin
    select * into c from testes.cenario();
    update public.marketplaces set modo = 'demo' where id = c.mkt;
    cup := testes.novo_cupom(c.mkt);
    e := testes.erro_de(format('insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (%s, %s, ''sucesso'')', cup, c.canal_real));
    e2 := testes.erro_de(format('insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (%s, %s, ''sucesso'')', cup, c.canal_demo));
    ok := e like 'P0015%' and e2 is null; detalhe := coalesce(e, 'foi para o canal real') || ' | demo: ' || coalesce(e2, 'ok');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'produto manual entra aprovado; sugestão do bot aguarda aprovação; outra loja é recusada (P0026)';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    insert into public.cupons_produtos (cupom_id, promocao_id, origem) values (cup, c.promo, 'manual');
    select aprovado::text into v from public.cupons_produtos where cupom_id = cup and promocao_id = c.promo;
    select * into b from testes.outra_oferta(c.mkt);
    insert into public.cupons_produtos (cupom_id, promocao_id, origem) values (cup, b.promo, 'sugestao');
    select v || ' / ' || aprovado::text into v from public.cupons_produtos where cupom_id = cup and promocao_id = b.promo;
    insert into public.marketplaces (codigo, nome, dominios_afiliado, modo) values ('outra_loja', 'Outra', array['outra.com.br'], 'real');
    insert into public.cupons (marketplace_id, titulo, codigo, fim_em) select id, 'Da outra', 'OUTRA', now() + interval '1 day' from public.marketplaces where codigo = 'outra_loja' returning id into cup;
    e := testes.erro_de(format('insert into public.cupons_produtos (cupom_id, promocao_id) values (%s, %s)', cup, c.promo));
    ok := v = 'true / false' and e like 'P0026%'; detalhe := 'manual / sugestão: ' || v || ' | ' || coalesce(e, 'aceitou outra loja');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'painel: editor cadastra cupom, mas não forja origem/demo; leitor não cadastra';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    u := testes.novo_usuario('editor-cupom@teste.com');
    insert into public.administradores (user_id, papel) values (u, 'editor');
    perform testes.como(u);
    insert into public.cupons (marketplace_id, titulo, codigo, fim_em, origem, id_externo)
      values (c.mkt, 'Do painel', 'PAINEL5', now() + interval '1 day', 'automatica', 'forjado') returning id into cup;
    e := testes.erro_de(format('update public.cupons set demo = true where id = %s', cup));
    perform testes.sair();
    select origem || '/' || coalesce(id_externo, 'sem id') into v from public.cupons where id = cup;
    u := testes.novo_usuario('leitor-cupom@teste.com');
    insert into public.administradores (user_id, papel) values (u, 'leitor');
    perform testes.como(u);
    e2 := testes.erro_de(format('insert into public.cupons (marketplace_id, titulo, codigo, fim_em) values (%s, ''Leitor'', ''LEITOR'', now() + interval ''1 day'')', c.mkt));
    e3 := testes.erro_de(format('insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (%s, %s, ''sucesso'')', cup, c.canal_real));
    perform testes.sair();
    ok := v = 'manual/sem id' and e like 'P0023%' and e2 like '42501%' and e3 like '42501%';
    detalhe := v || ' | ' || coalesce(e, 'forjou demo') || ' | ' || coalesce(e2, 'leitor cadastrou') || ' | ' || coalesce(e3, 'painel publicou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'visitante não vê cupons';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    execute 'set local role anon';
    e := testes.erro_de('select count(*) from public.cupons');
    e2 := testes.erro_de('select count(*) from public.vw_cupons_painel');
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'leu cupons') || ' | ' || coalesce(e2, 'leu a vista');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'publicação de cupom é imutável e a manutenção expira cupons vencidos';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (cup, c.canal_real, 'sucesso');
    e := testes.erro_de(format('update public.cupons_publicacoes set resultado = ''erro'' where cupom_id = %s', cup));
    update public.cupons set inicio_em = now() - interval '2 days', fim_em = now() - interval '1 second', status = 'pausado' where id = cup;
    perform public.manutencao_diaria();
    select status into v from public.cupons where id = cup;
    ok := e like 'P0002%' and v = 'expirado'; detalhe := coalesce(e, 'alterou') || ' | ' || v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'stories de cupom ainda cabem na cota do Instagram (50 por dia)';
  begin
    select coalesce(sum(limite_diario), 0) + (select (maximo)::int from public.configuracoes where chave = 'cupons.max_stories_dia')
      into n from public.canais where codigo in ('instagram_story', 'instagram_feed');
    ok := n <= 50; detalhe := n || ' publicações/dia no pior caso (cota: 50)';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_cupons() from public;
