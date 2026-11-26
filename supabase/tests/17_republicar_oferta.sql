-- Testes · republicar oferta (migration 029)
create or replace function testes.t_republicar_oferta()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare dono uuid; leitor uuid; r record; novo bigint; s text;
begin
  dono := testes.garantir_dono();
  select id into leitor from auth.users where email = 'leitor-repub@teste.com';
  leitor := coalesce(leitor, testes.novo_usuario('leitor-repub@teste.com'));
  insert into public.administradores (user_id, email, papel) values (leitor, 'leitor-repub@teste.com', 'leitor') on conflict do nothing;

  teste := 'publicada → antiga expira, nasce oferta nova na fila com o preço conferido e o link aponta para ela';
  begin
    select * into r from testes.cenario();
    update public.promocoes set status = 'publicada', publicada_em = now() - interval '8 days' where id = r.promo;
    perform testes.como(dono);
    novo := public.republicar_oferta(r.promo, 89.90, 149.90, true, true);
    perform testes.sair();
    ok := (select status = 'expirada' and motivo_status = 'Republicada' from public.promocoes where id = r.promo)
      and (select status = 'aprovada' and preco_promocional = 89.90 and preco_referencia = 149.90
                  and referencia_origem = 'pagina' and preco_no_pix from public.promocoes where id = novo)
      and (select preco_atual = 89.90 and verificado_em > now() - interval '1 minute' from public.produtos where id = r.prod)
      and (select promocao_id = novo from public.links_afiliados where id = r.link);
    detalhe := format('antiga=%s nova=%s', r.promo, novo);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'recusa oferta ainda na fila, preço inválido e usuário só leitor';
  begin
    select * into r from testes.cenario();
    update public.promocoes set status = 'aprovada' where id = r.promo;
    perform testes.como(dono);
    s := testes.erro_de(format('select public.republicar_oferta(%s, 10)', r.promo));
    ok := s like 'P0044%';
    update public.promocoes set status = 'pausada' where id = r.promo;
    s := testes.erro_de(format('select public.republicar_oferta(%s, 0)', r.promo));
    ok := ok and s like 'P0045%';
    perform testes.sair();
    perform testes.como(leitor);
    s := testes.erro_de(format('select public.republicar_oferta(%s, 10)', r.promo));
    perform testes.sair();
    ok := ok and s like '42501%';
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'preço antigo menor que o atual é descartado e anônimo não executa';
  begin
    select * into r from testes.cenario();
    update public.promocoes set status = 'pausada' where id = r.promo;
    perform testes.como(dono);
    novo := public.republicar_oferta(r.promo, 100, 90);
    perform testes.sair();
    ok := (select preco_referencia is null and referencia_origem is null from public.promocoes where id = novo)
          and not has_function_privilege('anon', 'public.republicar_oferta(bigint, numeric, numeric, boolean, boolean)', 'execute');
    detalhe := null;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'preços acompanhados o tempo todo: conferência a cada 1 h (mínimo 1), 30 dias, 30 por rodada, fila ligada';
  ok := (select valor = '1'::jsonb and minimo = 1 from public.configuracoes where chave = 'monitor.intervalo_pagina_horas')
        and (select valor = '30'::jsonb from public.configuracoes where chave = 'monitor.acompanhar_dias')
        and (select valor = '30'::jsonb from public.configuracoes where chave = 'monitor.max_por_rodada')
        and (select valor = 'true'::jsonb from public.configuracoes where chave = 'monitor.conferir_fila');
  detalhe := null;
  return next;
end $$;
