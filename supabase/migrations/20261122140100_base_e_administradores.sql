-- =====================================================================
-- 001 · Base: extensões, schema privado, funções utilitárias e administradores
-- =====================================================================
create extension if not exists pg_trgm with schema extensions;

-- Schema "privado": funções internas, NÃO expostas pela API do Supabase
create schema if not exists privado;
revoke all on schema privado from public, anon;
grant usage on schema privado to authenticated, service_role;

-- Timestamp automático de atualização
create or replace function privado.definir_atualizado_em()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.atualizado_em := now();
  return new;
end $$;

-- Extrai o domínio de uma URL (https://s.shopee.com.br/abc -> s.shopee.com.br)
create or replace function privado.host_da_url(p_url text)
returns text language sql immutable set search_path = '' as $$
  select lower(substring(p_url from '^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)'))
$$;

-- O domínio da URL é um dos domínios permitidos (ou subdomínio deles)?
create or replace function privado.dominio_permitido(p_url text, p_dominios text[])
returns boolean language sql immutable set search_path = '' as $$
  select exists (
    select 1 from unnest(coalesce(p_dominios, '{}'::text[])) d
    where privado.host_da_url(p_url) = lower(d)
       or privado.host_da_url(p_url) like '%.' || lower(d)
  )
$$;

-- ---------------------------------------------------------------------
-- 1. ADMINISTRADORES
-- ---------------------------------------------------------------------
create table public.administradores (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  nome          text not null default '' check (char_length(nome) <= 120),
  email         text check (email is null or position('@' in email) > 1),
  papel         text not null default 'leitor' check (papel in ('dono', 'editor', 'leitor')),
  ativo         boolean not null default true,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);
comment on table public.administradores is 'Quem acessa o painel. dono = tudo; editor = opera ofertas e ajustes; leitor = só visualiza.';
create unique index administradores_email_uq on public.administradores (lower(email)) where email is not null;
create unique index administradores_um_dono_uq on public.administradores (papel) where papel = 'dono';

create trigger administradores_atualizado_em before update on public.administradores
  for each row execute function privado.definir_atualizado_em();

-- Funções de permissão (usadas pelas políticas RLS)
create or replace function privado.papel_atual()
returns text language sql stable security definer set search_path = '' as $$
  select papel from public.administradores where user_id = (select auth.uid()) and ativo
$$;
create or replace function privado.eh_admin()
returns boolean language sql stable security definer set search_path = '' as $$
  select privado.papel_atual() is not null
$$;
create or replace function privado.pode_editar()
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce(privado.papel_atual() in ('dono', 'editor'), false)
$$;
create or replace function privado.eh_dono()
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce(privado.papel_atual() = 'dono', false)
$$;

-- O primeiro usuário cadastrado vira o dono; os demais não recebem acesso automaticamente
create or replace function privado.registrar_primeiro_dono()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.administradores) then
    insert into public.administradores (user_id, email, papel) values (new.id, new.email, 'dono');
  end if;
  return new;
end $$;
create trigger ao_criar_usuario_admin after insert on auth.users
  for each row execute function privado.registrar_primeiro_dono();

-- Impede apagar, desativar ou rebaixar o dono (exceto pela função transferir_posse)
create or replace function privado.proteger_dono()
returns trigger language plpgsql set search_path = '' as $$
begin
  if coalesce(current_setting('app.transferindo_posse', true), '') = 'sim' then
    return coalesce(new, old);
  end if;
  if tg_op = 'DELETE' and old.papel = 'dono' then
    raise exception 'O dono não pode ser removido. Transfira a posse antes.' using errcode = 'P0001';
  end if;
  if tg_op = 'UPDATE' and old.papel = 'dono' and (new.papel <> 'dono' or not new.ativo) then
    raise exception 'O dono não pode ser rebaixado ou desativado. Use transferir_posse().' using errcode = 'P0001';
  end if;
  return coalesce(new, old);
end $$;
create trigger administradores_proteger_dono before update or delete on public.administradores
  for each row execute function privado.proteger_dono();

-- Transferência de posse (só o dono atual)
create or replace function public.transferir_posse(p_novo_dono uuid)
returns void language plpgsql security definer set search_path = '' as $$
declare v_atual uuid := (select auth.uid());
begin
  if not privado.eh_dono() then
    raise exception 'Apenas o dono pode transferir a posse.' using errcode = '42501';
  end if;
  if not exists (select 1 from public.administradores where user_id = p_novo_dono and ativo) then
    raise exception 'O novo dono precisa ser um administrador ativo.' using errcode = 'P0001';
  end if;
  perform set_config('app.transferindo_posse', 'sim', true);
  update public.administradores set papel = 'editor' where user_id = v_atual;
  update public.administradores set papel = 'dono' where user_id = p_novo_dono;
  perform set_config('app.transferindo_posse', '', true);
end $$;
revoke execute on function public.transferir_posse(uuid) from public, anon;

-- Papel do usuário logado (o painel usa para saber o que mostrar)
create or replace function public.meu_papel()
returns text language sql stable security invoker set search_path = '' as $$
  select papel from public.administradores where user_id = (select auth.uid()) and ativo
$$;
revoke execute on function public.meu_papel() from public, anon;
grant execute on function public.meu_papel() to authenticated;

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
