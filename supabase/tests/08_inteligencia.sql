-- Testes · regras do motor de inteligência garantidas pelo banco
create or replace function testes.t_inteligencia()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; e text; e2 text; f bigint; r record; u uuid; s text;
begin
  teste := 'oferta sem análise ou "não recomendada" não entra na fila';
  begin
    select * into c from testes.cenario();
    update public.promocoes set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null where id = c.promo;
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    perform testes.classificar(c.promo, 'nao_recomendada');
    e2 := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    ok := e like 'P0019%sem análise%' and e2 like 'P0019%nao_recomendada%';
    detalhe := coalesce(e, 'aceitou sem análise') || ' | ' || coalesce(e2, 'aceitou não recomendada');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := '"excelente" e "boa" entram na fila';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    select * into r from testes.outra_oferta(c.mkt);
    perform testes.classificar(r.promo, 'boa');
    e2 := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', r.promo, c.canal_real, r.link));
    ok := e is null and e2 is null; detalhe := coalesce(e, 'excelente ok') || ' | ' || coalesce(e2, 'boa ok');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'mesma oferta não repete no mesmo canal em 24 h; outro canal pode';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
      values (c.promo, c.canal_real, c.link, 'sucesso', now() - interval '3 hours');
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    insert into public.canais (codigo, tipo, nome, modo) values ('teste_real2', 'telegram', 'Outro canal', 'real') returning id into f;
    e2 := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, f, c.link));
    ok := e like 'P0018%' and e2 is null; detalhe := coalesce(e, 'repetiu') || ' | outro canal: ' || coalesce(e2, 'aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'depois de 24 h a oferta pode voltar ao canal; nova promoção do mesmo produto também respeita a janela';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
      values (c.promo, c.canal_real, c.link, 'sucesso', now() - interval '25 hours');
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
      values (c.promo, c.canal_real, c.link, 'sucesso', now() - interval '1 hour');
    update public.promocoes set status = 'expirada' where id = c.promo;
    insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status) values (c.prod, 90, 150, 'aprovada') returning id into f;
    perform testes.classificar(f);
    e2 := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', f, c.canal_real, c.link));
    ok := e is null and e2 like 'P0018%'; detalhe := 'após 25 h: ' || coalesce(e, 'aceito') || ' | nova promoção: ' || coalesce(e2, 'repetiu');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'janela de repetição é configurável';
  begin
    select * into c from testes.cenario();
    update public.configuracoes set valor = '2' where chave = 'inteligencia.janela_repeticao_horas';
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
      values (c.promo, c.canal_real, c.link, 'sucesso', now() - interval '3 hours');
    e := testes.erro_de(format('insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (%s, %s, %s)', c.promo, c.canal_real, c.link));
    ok := e is null; detalhe := coalesce(e, 'janela de 2 h: aceito após 3 h');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'mudança de preço, disponibilidade ou link invalida a análise';
  begin
    select * into c from testes.cenario();
    update public.produtos set preco_atual = 99 where id = c.prod;
    s := coalesce((select classificacao from public.promocoes where id = c.promo), 'sem análise');
    perform testes.classificar(c.promo);
    update public.links_afiliados set ativo = false where id = c.link;
    ok := s = 'sem análise' and (select classificacao is null from public.promocoes where id = c.promo);
    detalhe := 'após preço: ' || s || ' | após link: ' || coalesce((select classificacao from public.promocoes where id = c.promo), 'sem análise');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'painel não altera pontuação nem classificação';
  begin
    select * into c from testes.cenario();
    u := testes.garantir_dono();
    perform testes.como(u);
    e := testes.erro_de(format('update public.promocoes set pontuacao = 100, classificacao = ''excelente'' where id = %s', c.promo));
    perform testes.sair();
    ok := e like 'P0023%'; detalhe := coalesce(e, 'painel alterou a pontuação');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'análise precisa ser completa (pontuação, classificação, data e explicação juntas)';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('update public.promocoes set analise = null where id = %s', c.promo));
    e2 := testes.erro_de(format('update public.promocoes set pontuacao = 101 where id = %s', c.promo));
    ok := e like '23514%' and e2 like '23514%'; detalhe := coalesce(e, 'aceitou incompleta') || ' | ' || coalesce(e2, 'aceitou 101');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'regras configuráveis do motor existem com os valores iniciais';
  begin
    ok := (select (valor #>> '{}')::numeric from public.configuracoes where chave = 'filtros.desconto_minimo') = 20
      and (select count(*) from public.configuracoes where chave like 'inteligencia.%') >= 20
      and (select (valor #>> '{}')::numeric from public.configuracoes where chave = 'inteligencia.janela_repeticao_horas') = 24;
    detalhe := (select count(*) from public.configuracoes where chave like 'inteligencia.%') || ' regras do motor';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_inteligencia() from public;
