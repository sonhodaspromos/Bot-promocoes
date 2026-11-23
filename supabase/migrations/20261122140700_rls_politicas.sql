-- =====================================================================
-- 007 · Row Level Security e políticas de acesso administrativo
--   anon (visitante)        -> nenhum acesso
--   autenticado sem cadastro -> nenhum acesso
--   leitor                  -> lê tudo, exceto auditoria e credenciais
--   editor                  -> lê e opera ofertas, links, fila, categorias, comissões e ajustes
--   dono                    -> tudo, incluindo administradores, auditoria e credenciais
--   backend (service_role)  -> ignora RLS (usa a chave secreta, nunca exposta ao painel)
-- =====================================================================

do $$
declare t text;
begin
  foreach t in array array['administradores', 'marketplaces', 'categorias', 'produtos', 'historico_precos',
    'promocoes', 'links_afiliados', 'criativos', 'canais', 'fila_publicacoes', 'historico_publicacoes',
    'cliques', 'comissoes', 'configuracoes', 'execucoes', 'logs', 'auditoria', 'credenciais_integracoes'] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- Visitante anônimo não tem nenhum privilégio (defesa extra além do RLS)
revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
revoke all on all functions in schema public from anon;
alter default privileges in schema public revoke all on tables from anon;
alter default privileges in schema public revoke all on sequences from anon;
alter default privileges in schema public revoke all on functions from anon;

-- ADMINISTRADORES
create policy "administradores: ver a si mesmo ou dono vê todos" on public.administradores
  for select to authenticated using (user_id = (select auth.uid()) or (select privado.eh_dono()));
create policy "administradores: dono cadastra" on public.administradores
  for insert to authenticated with check ((select privado.eh_dono()));
create policy "administradores: dono altera" on public.administradores
  for update to authenticated using ((select privado.eh_dono())) with check ((select privado.eh_dono()));
create policy "administradores: dono remove" on public.administradores
  for delete to authenticated using ((select privado.eh_dono()));

-- Tabelas operacionais: admins leem; editores escrevem
do $$
declare t text;
begin
  foreach t in array array['categorias', 'produtos', 'promocoes', 'links_afiliados', 'criativos',
                           'fila_publicacoes', 'comissoes'] loop
    execute format('create policy %I on public.%I for select to authenticated using ((select privado.eh_admin()))', t || ': admins leem', t);
    execute format('create policy %I on public.%I for insert to authenticated with check ((select privado.pode_editar()))', t || ': editores inserem', t);
    execute format('create policy %I on public.%I for update to authenticated using ((select privado.pode_editar())) with check ((select privado.pode_editar()))', t || ': editores alteram', t);
    execute format('create policy %I on public.%I for delete to authenticated using ((select privado.pode_editar()))', t || ': editores removem', t);
  end loop;
end $$;

-- Estrutura (marketplaces, canais, configurações): editores alteram; só o dono cria/remove
do $$
declare t text;
begin
  foreach t in array array['marketplaces', 'canais', 'configuracoes'] loop
    execute format('create policy %I on public.%I for select to authenticated using ((select privado.eh_admin()))', t || ': admins leem', t);
    execute format('create policy %I on public.%I for insert to authenticated with check ((select privado.eh_dono()))', t || ': dono cria', t);
    execute format('create policy %I on public.%I for update to authenticated using ((select privado.pode_editar())) with check ((select privado.pode_editar()))', t || ': editores alteram', t);
    execute format('create policy %I on public.%I for delete to authenticated using ((select privado.eh_dono()))', t || ': dono remove', t);
  end loop;
end $$;

-- Registros gerados pelo sistema: só leitura no painel (quem grava é o backend)
do $$
declare t text;
begin
  foreach t in array array['historico_precos', 'historico_publicacoes', 'cliques', 'execucoes', 'logs'] loop
    execute format('create policy %I on public.%I for select to authenticated using ((select privado.eh_admin()))', t || ': admins leem', t);
  end loop;
end $$;

-- Auditoria e metadados de credenciais: só o dono vê
create policy "auditoria: dono lê" on public.auditoria
  for select to authenticated using ((select privado.eh_dono()));
create policy "credenciais: dono vê metadados" on public.credenciais_integracoes
  for select to authenticated using ((select privado.eh_dono()));

-- Comissão "api" só pode ser criada pelo backend
create policy "comissoes: manual pelo painel" on public.comissoes
  as restrictive for insert to authenticated with check (origem <> 'api');

-- Regras que valem para pessoas no painel (o backend não é afetado):
--   * validar link é tarefa do sistema (que testa o link de verdade)
--   * marcar como publicada é tarefa do sistema (depois de publicar de verdade)
create or replace function privado.restricoes_do_painel()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is null then return new; end if;
  if tg_table_name = 'links_afiliados' and new.validado
     and (tg_op = 'INSERT' or not old.validado or new.url is distinct from old.url) then
    raise exception 'A validação do link de afiliado é feita automaticamente pelo sistema.' using errcode = 'P0016';
  end if;
  if tg_table_name = 'promocoes' and new.status = 'publicada'
     and (tg_op = 'INSERT' or old.status <> 'publicada') then
    raise exception 'Só o sistema marca uma oferta como publicada (depois de publicar de verdade).' using errcode = 'P0017';
  end if;
  return new;
end $$;
create trigger links_restricoes_painel before insert or update on public.links_afiliados
  for each row execute function privado.restricoes_do_painel();
create trigger promocoes_restricoes_painel before insert or update on public.promocoes
  for each row execute function privado.restricoes_do_painel();

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
