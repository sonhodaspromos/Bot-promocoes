-- =====================================================================
-- Testes de integridade · utilitários
-- Tudo roda dentro de subtransações desfeitas no final: nenhum dado de teste fica no banco.
-- Como rodar:  select * from testes.rodar();            -- todos
--              select * from testes.rodar('catalogo');  -- um grupo
-- =====================================================================
create schema if not exists testes;
revoke all on schema testes from public, anon, service_role;

-- Executa um SQL e devolve o erro (SQLSTATE: mensagem) ou NULL se não houve erro
create or replace function testes.erro_de(p_sql text)
returns text language plpgsql as $$
begin
  execute p_sql;
  return null;
exception when others then
  return sqlstate || ': ' || sqlerrm;
end $$;

-- Cria um usuário de teste no Auth
-- Cria um usuário direto no banco (sem convite). Ajuda de TESTE: como antes, o primeiro usuário vira dono,
-- para os testes antigos continuarem simples. Em produção, o dono só nasce pelo e-mail de definir_email_dono()
-- e os demais só por convite (ver 11_auditoria_complementar.sql, que testa os gatilhos reais sem este atalho).
create or replace function testes.novo_usuario(p_email text)
returns uuid language plpgsql as $$
declare v uuid := gen_random_uuid();
begin
  perform set_config('testes.cadastro_direto', 'sim', true);
  insert into auth.users (id, instance_id, aud, role, email, encrypted_password,
                          created_at, updated_at, raw_app_meta_data, raw_user_meta_data)
  values (v, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', p_email, '',
          now(), now(), '{}'::jsonb, '{}'::jsonb);
  perform set_config('testes.cadastro_direto', '', true);
  if not exists (select 1 from public.administradores where papel = 'dono') then
    insert into public.administradores (user_id, email, papel) values (v, p_email, 'dono');
  end if;
  return v;
end $$;

-- Garante que já existe um dono (para que o próximo usuário criado não vire dono automaticamente)
create or replace function testes.garantir_dono()
returns uuid language plpgsql as $$
declare v uuid;
begin
  select user_id into v from public.administradores where papel = 'dono';
  if v is null then v := testes.novo_usuario('dono-fixo@teste.com'); end if;
  return v;
end $$;

-- Passa a executar como um usuário logado (RLS passa a valer)
create or replace function testes.como(p_user uuid)
returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
                     json_build_object('sub', p_user, 'role', 'authenticated', 'aal', 'aal2')::text, true);
  execute 'set local role authenticated';
end $$;

-- Como testes.como, mas logado SÓ com senha (sem o 2º fator)
create or replace function testes.como_sem_mfa(p_user uuid)
returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
                     json_build_object('sub', p_user, 'role', 'authenticated', 'aal', 'aal1')::text, true);
  execute 'set local role authenticated';
end $$;

-- Volta a executar como o dono do banco
create or replace function testes.sair()
returns void language plpgsql as $$
begin
  execute 'reset role';
  perform set_config('request.jwt.claims', '', true);
end $$;

-- Dados mínimos para os testes (marketplace, categoria e produto de teste)
create or replace function testes.base(out mkt smallint, out cat int, out prod bigint)
language plpgsql as $$
begin
  insert into public.marketplaces (codigo, nome, dominios_afiliado, modo)
    values ('teste_mkt', 'Loja Teste', array['afiliado.teste.com.br'], 'real') returning id into mkt;
  insert into public.categorias (nome, slug) values ('Categoria Teste', 'categoria-teste') returning id into cat;
  insert into public.produtos (marketplace_id, id_externo, categoria_id, titulo, preco_atual, preco_original)
    values (mkt, 'T-1', cat, 'Produto de teste', 100, 150) returning id into prod;
end $$;

-- Executa todas as funções testes.t_<grupo>()
create or replace function testes.rodar(p_grupo text default null)
returns table (grupo text, teste text, ok boolean, detalhe text)
language plpgsql as $$
declare f record; r record;
begin
  for f in
    select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'testes' and p.proname like 't\_%'
      and (p_grupo is null or p.proname = 't_' || p_grupo)
    order by p.proname
  loop
    for r in execute format('select * from testes.%I()', f.proname) loop
      -- resultado NULL (condição que não deu true nem false) conta como FALHA: nunca some do placar
      grupo := substr(f.proname, 3); teste := r.teste; ok := coalesce(r.ok, false);
      detalhe := case when r.ok is null then '[resultado indefinido] ' else '' end || coalesce(r.detalhe, '');
      return next;
    end loop;
  end loop;
end $$;

-- Resumo: total, aprovados, reprovados
create or replace function testes.resumo()
returns table (total bigint, aprovados bigint, reprovados bigint)
language sql as $$
  select count(*), count(*) filter (where ok), count(*) filter (where not ok or ok is null)
  from testes.rodar()
$$;

-- Permissões: só o dono do banco roda os testes. O papel "authenticated" pode usar apenas
-- erro_de() e sair(), necessários enquanto um teste simula um usuário logado.
-- O schema "testes" não é exposto pela API do Supabase.
revoke execute on all functions in schema testes from public;
grant usage on schema testes to authenticated;
grant execute on function testes.erro_de(text), testes.sair() to authenticated;

-- search_path fixo em todas as funções de teste (recomendação do advisor de segurança)
do $$ declare f record; begin
  for f in select p.oid::regprocedure as assinatura from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'testes' loop
    execute format('alter function %s set search_path = pg_catalog', f.assinatura);
  end loop;
end $$;
