-- Simulação mínima do Supabase para HOMOLOGAÇÃO LOCAL (Postgres puro).
-- Cria só o que as migrations e os testes usam: papéis, auth.users/uid()/jwt(), vault e extensões.
-- NUNCA rode este arquivo no projeto Supabase real.
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'auth') and exists (select 1 from pg_class where relname = 'flow_state') then
    raise exception 'Isto parece um projeto Supabase real: este arquivo é só para homologação local.';
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin noinherit bypassrls; end if;
end $$;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;

create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;
create extension if not exists pgcrypto with schema extensions;
grant usage on schema extensions to anon, authenticated, service_role;

create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;
create table if not exists auth.users (
  instance_id uuid, id uuid primary key, aud text, role text, email text unique, encrypted_password text,
  email_confirmed_at timestamptz, created_at timestamptz default now(), updated_at timestamptz default now(),
  raw_app_meta_data jsonb default '{}', raw_user_meta_data jsonb default '{}', is_anonymous boolean default false
);
create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid $$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(auth.jwt() ->> 'role', current_user) $$;
grant execute on all functions in schema auth to anon, authenticated, service_role;

-- Vault simulado: cifra com pgcrypto (chave fixa de teste; no Supabase real a chave é gerenciada pelo Vault)
create schema if not exists vault;
create table if not exists vault.secrets (id uuid primary key default gen_random_uuid(), name text unique, description text,
  secret text not null, created_at timestamptz default now(), updated_at timestamptz default now());
create or replace view vault.decrypted_secrets as
  select id, name, description, secret,
         extensions.pgp_sym_decrypt(decode(secret, 'base64'), 'chave-de-homologacao') as decrypted_secret from vault.secrets;
create or replace function vault.create_secret(p_secret text, p_name text default null, p_description text default '')
returns uuid language sql as $$
  insert into vault.secrets (secret, name, description)
  values (encode(extensions.pgp_sym_encrypt(p_secret, 'chave-de-homologacao'), 'base64'), p_name, p_description) returning id $$;
create or replace function vault.update_secret(p_id uuid, p_secret text default null, p_name text default null, p_description text default null)
returns void language sql as $$
  update vault.secrets set secret = coalesce(encode(extensions.pgp_sym_encrypt(p_secret, 'chave-de-homologacao'), 'base64'), secret),
         updated_at = now() where id = p_id $$;
revoke all on schema vault from anon, authenticated;
