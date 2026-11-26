-- Testes · motor de IA (migration 028): prompts versionados, rollback, permissões e conteúdos
create or replace function testes.t_motor_ia()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare dono uuid; editor uuid; v1 bigint; v2 bigint; n int; s text; r record; c bigint; p bigint;
begin
  dono := testes.garantir_dono();
  -- fora dos blocos de teste: cada bloco desfaz o que fez ao terminar (o editor precisa sobreviver entre eles)
  select id into editor from auth.users where email = 'editor-ia@teste.com';
  editor := coalesce(editor, testes.novo_usuario('editor-ia@teste.com'));
  insert into public.administradores (user_id, email, papel) values (editor, 'editor-ia@teste.com', 'editor') on conflict do nothing;

  teste := 'prompt inicial existe ativo; salvar cria nova versão e ativa; ativar volta para a anterior (rollback)';
  begin
    select id into v1 from public.ia_prompts where chave = 'copy_oferta' and ativo;
    perform testes.como(dono);
    v2 := public.ia_prompt_salvar('copy_oferta', 'Sistema de teste com regras suficientes para passar.', 'Modelo de teste: {dados} {titulo}', 'v2');
    perform testes.sair();
    ok := (select versao from public.ia_prompts where id = v2) = (select versao from public.ia_prompts where id = v1) + 1
          and (select count(*) from public.ia_prompts where chave = 'copy_oferta' and ativo) = 1
          and (select ativo from public.ia_prompts where id = v2);
    perform testes.como(dono); perform public.ia_prompt_ativar(v1); perform testes.sair();
    ok := ok and (select ativo from public.ia_prompts where id = v1) and not (select ativo from public.ia_prompts where id = v2)
          and (select count(*) from public.ia_prompts where chave = 'copy_oferta') >= 2;   -- nada é apagado
    detalhe := format('v1=%s v2=%s', v1, v2);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'prompt com variável desconhecida ou sem {dados} é recusado';
  begin
    perform testes.como(dono);
    s := testes.erro_de($q$select public.ia_prompt_salvar('copy_oferta', 'Sistema de teste com regras suficientes.', 'Modelo {dados} {senha_do_banco}')$q$);
    ok := s like 'P0040%';
    s := testes.erro_de($q$select public.ia_prompt_salvar('copy_oferta', 'Sistema de teste com regras suficientes.', 'Modelo sem o bloco de dados {titulo}')$q$);
    ok := ok and s like 'P0041%';
    perform testes.sair();
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'editor e anônimo não alteram prompts nem leem o histórico sem login';
  begin
    perform testes.como(editor);
    s := testes.erro_de($q$select public.ia_prompt_salvar('copy_oferta', 'Sistema de teste com regras suficientes.', 'Modelo {dados}')$q$);
    perform testes.sair();
    ok := s like '42501%';
    execute 'set local role anon';
    begin perform 1 from public.ia_prompts; ok := false; exception when insufficient_privilege then null; end;
    begin perform 1 from public.ia_chamadas; ok := false; exception when insufficient_privilege then null; end;
    execute 'reset role';
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'conteúdo: mesma chave de idempotência não duplica; editor aprova (fica registrado) e não troca a oferta';
  begin
    select * into r from testes.cenario();
    select id into p from public.ia_prompts where chave = 'copy_oferta' and ativo;
    insert into public.ia_conteudos (chave_idem, promocao_id, prompt_id, chamada, texto)
    values (repeat('a', 64), r.promo, p, 'CHAMADA DE TESTE', 'Texto de teste da oferta.') returning id into c;
    s := testes.erro_de(format($q$insert into public.ia_conteudos (chave_idem, promocao_id, prompt_id, chamada, texto) values (%L, %s, %s, 'OUTRA', 'Outro texto qualquer.')$q$, repeat('a', 64), r.promo, p));
    ok := s like '23505%';
    perform testes.como(editor);
    update public.ia_conteudos set status = 'aprovado' where id = c;
    s := testes.erro_de(format('update public.ia_conteudos set promocao_id = promocao_id + 1 where id = %s', c));
    perform testes.sair();
    ok := ok and (select status = 'aprovado' and decidido_por = editor and decidido_em is not null from public.ia_conteudos where id = c)
          and (s like '42501%' or s like '42703%');
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'IA nasce desligada e o teto mensal padrão é US$ 5';
  ok := (select valor from public.configuracoes where chave = 'recursos.ia') = 'false'::jsonb
        and (select valor from public.configuracoes where chave = 'ia.orcamento_mensal_usd') = '5'::jsonb
        and (select valor from public.configuracoes where chave = 'ia.aprovacao_manual') = 'true'::jsonb;
  detalhe := null;
  return next;
end $$;
