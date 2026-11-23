-- =====================================================================
-- 006 · Credenciais das integrações (valores cifrados no Supabase Vault)
-- A tabela guarda só METADADOS. O valor secreto fica cifrado no Vault e
-- só a chave de serviço (backend) consegue lê-lo, pela função obter_credencial().
-- =====================================================================

-- 3. CREDENCIAIS DAS INTEGRAÇÕES ---------------------------------------
create table public.credenciais_integracoes (
  id              int generated always as identity primary key,
  integracao      text not null check (integracao ~ '^[a-z0-9_]{2,40}$'),
  nome            text not null check (nome ~ '^[A-Z0-9_]{2,60}$'),
  marketplace_id  smallint references public.marketplaces(id) on delete cascade,
  canal_id        smallint references public.canais(id) on delete cascade,
  vault_secret_id uuid not null unique,
  dica            text check (char_length(dica) <= 20),
  expira_em       timestamptz,
  ultimo_uso_em   timestamptz,
  criado_por      uuid references auth.users(id) on delete set null,
  criado_em       timestamptz not null default now(),
  atualizado_em   timestamptz not null default now(),
  constraint credenciais_unicas unique (integracao, nome),
  constraint credenciais_um_dono check (marketplace_id is null or canal_id is null)
);
comment on table public.credenciais_integracoes is 'Metadados das credenciais. O valor fica cifrado no Vault; nunca é exposto ao painel.';
create index credenciais_marketplace_idx on public.credenciais_integracoes (marketplace_id);
create index credenciais_canal_idx on public.credenciais_integracoes (canal_id);
create index credenciais_criado_por_idx on public.credenciais_integracoes (criado_por);
create trigger credenciais_atualizado_em before update on public.credenciais_integracoes
  for each row execute function privado.definir_atualizado_em();
create trigger credenciais_auditoria after insert or update or delete on public.credenciais_integracoes
  for each row execute function privado.auditar();

-- Salvar/atualizar credencial: só o dono (pelo painel) ou o backend (chave de serviço)
create or replace function public.salvar_credencial(
  p_integracao text, p_nome text, p_valor text, p_expira_em timestamptz default null)
returns void language plpgsql security definer set search_path = '' as $$
declare v_id uuid; v_nome_vault text := lower(p_integracao) || '.' || upper(p_nome);
begin
  if not (privado.eh_dono() or coalesce((select auth.role()), '') = 'service_role') then
    raise exception 'Apenas o dono pode cadastrar credenciais.' using errcode = '42501';
  end if;
  if p_valor is null or char_length(p_valor) not between 1 and 8192 then
    raise exception 'Valor da credencial inválido.' using errcode = '22023';
  end if;

  select vault_secret_id into v_id from public.credenciais_integracoes
   where integracao = p_integracao and nome = p_nome;
  if v_id is null then
    v_id := vault.create_secret(p_valor, v_nome_vault, 'Credencial do Bot de Ofertas');
    insert into public.credenciais_integracoes (integracao, nome, vault_secret_id, dica, expira_em, criado_por)
    values (p_integracao, p_nome, v_id,
            case when char_length(p_valor) >= 12 then '••••' || right(p_valor, 4) else '••••' end,
            p_expira_em, (select auth.uid()));
  else
    perform vault.update_secret(v_id, p_valor);
    update public.credenciais_integracoes
       set dica = case when char_length(p_valor) >= 12 then '••••' || right(p_valor, 4) else '••••' end,
           expira_em = p_expira_em
     where vault_secret_id = v_id;
  end if;
end $$;
revoke execute on function public.salvar_credencial(text, text, text, timestamptz) from public, anon;
grant execute on function public.salvar_credencial(text, text, text, timestamptz) to authenticated, service_role;

-- Remover credencial (apaga também do Vault)
create or replace function public.remover_credencial(p_integracao text, p_nome text)
returns void language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if not (privado.eh_dono() or coalesce((select auth.role()), '') = 'service_role') then
    raise exception 'Apenas o dono pode remover credenciais.' using errcode = '42501';
  end if;
  delete from public.credenciais_integracoes where integracao = p_integracao and nome = p_nome
    returning vault_secret_id into v_id;
  if v_id is not null then
    delete from vault.secrets where id = v_id;
  end if;
end $$;
revoke execute on function public.remover_credencial(text, text) from public, anon;
grant execute on function public.remover_credencial(text, text) to authenticated, service_role;

-- Ler o valor: SOMENTE o backend (chave de serviço). O painel nunca recebe o valor.
create or replace function public.obter_credencial(p_integracao text, p_nome text)
returns text language plpgsql security definer set search_path = '' as $$
declare v_id uuid; v_valor text;
begin
  select vault_secret_id into v_id from public.credenciais_integracoes
   where integracao = p_integracao and nome = p_nome;
  if v_id is null then return null; end if;
  select decrypted_secret into v_valor from vault.decrypted_secrets where id = v_id;
  update public.credenciais_integracoes set ultimo_uso_em = now() where vault_secret_id = v_id;
  return v_valor;
end $$;
revoke execute on function public.obter_credencial(text, text) from public, anon, authenticated;
grant execute on function public.obter_credencial(text, text) to service_role;

-- Credencial apagada direto na tabela: apaga o segredo órfão do Vault
create or replace function privado.credencial_apagar_vault()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  delete from vault.secrets where id = old.vault_secret_id;
  return old;
end $$;
create trigger credenciais_apagar_vault after delete on public.credenciais_integracoes
  for each row execute function privado.credencial_apagar_vault();

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
