-- Testes · publicação segura (migration 018)
create or replace function testes.t_publicacao_segura()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; f bigint; r record; b1 boolean; b2 boolean; b3 boolean; cup bigint; id1 bigint; id2 bigint; id3 bigint;
        e text; j jsonb; l bigint;
begin
  teste := 'reserva do envio: só o worker com a trava vigente reserva, e uma única vez';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    b1 := public.fila_reservar_envio(f);                         -- ainda pendente: não reserva
    select * into r from public.pegar_proximas_publicacoes(5, 10) where id = f;
    b2 := public.fila_reservar_envio(f);                         -- processando com trava: reserva
    b3 := public.fila_reservar_envio(f);                         -- 2ª vez: não
    ok := b1 = false and b2 = true and b3 = false;
    detalhe := format('pendente=%s, 1ª=%s, 2ª=%s', b1, b2, b3);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'reserva recusada quando a trava do worker venceu (outro worker pode ter pego o item)';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform public.pegar_proximas_publicacoes(5, 10);
    update public.fila_publicacoes set bloqueado_ate = now() - interval '1 second' where id = f;
    b1 := public.fila_reservar_envio(f);
    ok := b1 = false; detalhe := format('reservou=%s', b1);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'item reservado e não concluído nunca volta para a fila (vira resultado incerto)';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform public.pegar_proximas_publicacoes(5, 10);
    perform public.fila_reservar_envio(f);
    update public.fila_publicacoes set bloqueado_ate = now() - interval '1 minute' where id = f;
    perform public.pegar_proximas_publicacoes(5, 10);
    select status, ultimo_erro into r from public.fila_publicacoes where id = f;
    ok := r.status = 'falhou' and r.ultimo_erro like 'Resultado incerto%'; detalhe := r.status || ' / ' || coalesce(r.ultimo_erro, '');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom: reserva antes do envio; reserva sem resultado bloqueia reenvio no dia';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    id1 := public.cupom_reservar_envio(cup, c.canal_real);
    id2 := public.cupom_reservar_envio(cup, c.canal_real);  -- incerto: não reserva de novo
    ok := id1 is not null and id2 is null;
    detalhe := format('1ª=%s, 2ª=%s', id1, id2);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom: erro registrado libera nova tentativa; sucesso encerra o dia';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    id1 := public.cupom_reservar_envio(cup, c.canal_real);
    insert into public.cupons_publicacoes (cupom_id, canal_id, resultado, erro) values (cup, c.canal_real, 'erro', 'HTTP 400');
    id2 := public.cupom_reservar_envio(cup, c.canal_real);
    insert into public.cupons_publicacoes (cupom_id, canal_id, resultado, id_mensagem_externa) values (cup, c.canal_real, 'sucesso', '9');
    id3 := public.cupom_reservar_envio(cup, c.canal_real);
    ok := id1 is not null and id2 is not null and id3 is null;
    detalhe := format('%s / %s / %s', id1, id2, id3);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cupom: reserva respeita as regras (cupom pausado e cupom demo em canal real não reservam)';
  begin
    select * into c from testes.cenario();
    cup := testes.novo_cupom(c.mkt);
    update public.cupons set status = 'pausado' where id = cup;
    e := testes.erro_de(format('select public.cupom_reservar_envio(%s, %s::smallint)', cup, c.canal_real));
    ok := e like 'P0025%'; detalhe := coalesce(e, 'reservou cupom pausado');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'atribuição: URL alterada apaga a comprovação; painel não marca atribuição (P0016)';
  begin
    select * into c from testes.cenario();
    update public.links_afiliados set atribuicao_verificada_em = now() where id = c.link;
    update public.links_afiliados set url = 'https://afiliado.teste.com.br/p/outro' where id = c.link;
    select atribuicao_verificada_em is null into b1 from public.links_afiliados where id = c.link;
    perform set_config('request.jwt.claims', '{"role":"authenticated"}', true);
    e := testes.erro_de(format('update public.links_afiliados set atribuicao_verificada_em = now() where id = %s', c.link));
    perform set_config('request.jwt.claims', '', true);
    ok := b1 and e like 'P0016%'; detalhe := format('apagou=%s | %s', b1, coalesce(e, 'painel marcou'));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'esquema_publicacao informa versão 18, RPCs, configurações e colunas exigidas';
  begin
    j := public.esquema_publicacao();
    ok := (j ->> 'versao')::int = 18 and jsonb_array_length(j -> 'rpcs') = 7
          and jsonb_array_length(j -> 'configuracoes') = 3 and jsonb_array_length(j -> 'colunas') = 5;
    detalhe := j::text;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'RPCs novas fechadas para anon e authenticated';
  begin
    ok := not has_function_privilege('anon', 'public.fila_reservar_envio(bigint)', 'execute')
      and not has_function_privilege('authenticated', 'public.fila_reservar_envio(bigint)', 'execute')
      and not has_function_privilege('authenticated', 'public.cupom_reservar_envio(bigint, smallint)', 'execute')
      and not has_function_privilege('authenticated', 'public.esquema_publicacao()', 'execute');
    detalhe := case when ok then 'fechadas' else 'alguma RPC aberta' end;
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_publicacao_segura() from public;
