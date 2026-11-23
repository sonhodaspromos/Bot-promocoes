-- Testes · administradores
create or replace function testes.t_administradores()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare u1 uuid; u2 uuid; e text; v text; t timestamptz;
begin
  -- 1
  teste := 'primeiro usuário cadastrado vira dono';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    u1 := testes.novo_usuario('dono@teste.com');
    select papel into v from public.administradores where user_id = u1;
    ok := v = 'dono'; detalhe := coalesce(v, 'sem registro');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 2
  teste := 'segundo usuário não recebe acesso automático';
  begin
    u1 := testes.novo_usuario('a@teste.com'); u2 := testes.novo_usuario('b@teste.com');
    ok := not exists (select 1 from public.administradores where user_id = u2);
    detalhe := case when ok then 'sem acesso' else 'recebeu acesso' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 3
  teste := 'dono não pode ser removido';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    u1 := testes.novo_usuario('dono@teste.com');
    e := testes.erro_de(format('delete from public.administradores where user_id = %L', u1));
    ok := e like 'P0001%'; detalhe := coalesce(e, 'apagou o dono');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 4
  teste := 'dono não pode ser rebaixado nem desativado direto';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    u1 := testes.novo_usuario('dono@teste.com');
    e := testes.erro_de(format('update public.administradores set papel = ''editor'' where user_id = %L', u1));
    v := testes.erro_de(format('update public.administradores set ativo = false where user_id = %L', u1));
    ok := e like 'P0001%' and v like 'P0001%'; detalhe := coalesce(e, 'rebaixou') || ' | ' || coalesce(v, 'desativou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 5
  teste := 'só pode existir um dono';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    u1 := testes.novo_usuario('dono@teste.com'); u2 := testes.novo_usuario('outro@teste.com');
    e := testes.erro_de(format('insert into public.administradores (user_id, papel) values (%L, ''dono'')', u2));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou dois donos');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 6
  teste := 'papel inválido é recusado';
  begin
    u2 := testes.novo_usuario('x@teste.com');
    e := testes.erro_de(format('insert into public.administradores (user_id, papel) values (%L, ''superadmin'')', u2));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou papel inválido');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 7
  teste := 'e-mail repetido (maiúsculas/minúsculas) é recusado';
  begin
    perform testes.garantir_dono();
    u1 := testes.novo_usuario('p@teste.com'); u2 := testes.novo_usuario('q@teste.com');
    insert into public.administradores (user_id, email, papel) values (u1, 'Ana@Teste.com', 'editor')
      on conflict (user_id) do update set email = excluded.email, papel = 'editor';
    e := testes.erro_de(format('insert into public.administradores (user_id, email) values (%L, ''ana@teste.COM'')', u2));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou e-mail repetido');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 8
  teste := 'atualizado_em é preenchido automaticamente';
  begin
    perform testes.garantir_dono();
    u1 := testes.novo_usuario('t@teste.com');
    insert into public.administradores (user_id, papel, criado_em, atualizado_em)
      values (u1, 'leitor', now() - interval '2 days', now() - interval '2 days')
      on conflict (user_id) do update set atualizado_em = now() - interval '2 days';
    update public.administradores set nome = 'Novo nome' where user_id = u1;
    select atualizado_em into t from public.administradores where user_id = u1;
    ok := t >= now() - interval '1 minute'; detalhe := t::text;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 9
  teste := 'transferir_posse: dono transfere; editor não consegue';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    u1 := testes.novo_usuario('dono@teste.com'); u2 := testes.novo_usuario('ed@teste.com');
    insert into public.administradores (user_id, papel) values (u2, 'editor');
    perform testes.como(u2);
    e := testes.erro_de(format('select public.transferir_posse(%L)', u2));
    perform testes.sair();
    perform testes.como(u1);
    perform public.transferir_posse(u2);
    perform testes.sair();
    select papel into v from public.administradores where user_id = u2;
    ok := e like '42501%' and v = 'dono'
          and (select papel from public.administradores where user_id = u1) = 'editor';
    detalhe := 'editor: ' || coalesce(e, 'conseguiu') || ' | novo papel: ' || coalesce(v, '?');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  -- 10
  teste := 'meu_papel() devolve o papel do usuário logado';
  begin
    perform testes.garantir_dono();
    u1 := testes.novo_usuario('l@teste.com');
    insert into public.administradores (user_id, papel) values (u1, 'leitor') on conflict (user_id) do update set papel = 'leitor';
    perform testes.como(u1);
    v := public.meu_papel();
    perform testes.sair();
    ok := v = 'leitor'; detalhe := coalesce(v, 'nulo');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
