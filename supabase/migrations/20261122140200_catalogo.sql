-- =====================================================================
-- 002 · Catálogo: marketplaces, categorias, produtos e histórico de preços
-- =====================================================================

-- 2. MARKETPLACES -------------------------------------------------------
-- Domínios válidos: minúsculos, sem protocolo, sem barras (ex.: s.shopee.com.br)
create or replace function privado.dominios_validos(p text[])
returns boolean language sql immutable set search_path = '' as $$
  select coalesce(bool_and(d is not null and d ~ '^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$'), true)
  from unnest(p) d
$$;

create table public.marketplaces (
  id                smallint generated always as identity primary key,
  codigo            text not null unique check (codigo ~ '^[a-z0-9_]{2,40}$'),
  nome              text not null check (char_length(btrim(nome)) between 2 and 80),
  site_url          text check (site_url ~* '^https://'),
  dominios_afiliado text[] not null default '{}',
  possui_api        boolean not null default false,
  modo              text not null default 'demo' check (modo in ('real', 'demo', 'manual', 'desativado')),
  ativo             boolean not null default true,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now(),
  constraint marketplaces_dominios_validos check (privado.dominios_validos(dominios_afiliado))
);
comment on column public.marketplaces.dominios_afiliado is 'Domínios aceitos em links de afiliado (ex.: s.shopee.com.br). Links fora desta lista nunca são validados.';
comment on column public.marketplaces.modo is 'real = API com credenciais; demo = dados de demonstração; manual = cadastro pelo painel; desativado.';
create trigger marketplaces_atualizado_em before update on public.marketplaces
  for each row execute function privado.definir_atualizado_em();

-- 5. CATEGORIAS ---------------------------------------------------------
create table public.categorias (
  id               int generated always as identity primary key,
  categoria_pai_id int references public.categorias(id) on delete set null,
  nome             text not null check (char_length(btrim(nome)) between 2 and 60),
  slug             text not null unique check (slug ~ '^[a-z0-9-]{2,60}$'),
  palavras_chave   text[] not null default '{}',
  ativa            boolean not null default true,
  ordem            smallint not null default 0,
  criado_em        timestamptz not null default now(),
  atualizado_em    timestamptz not null default now(),
  constraint categorias_pai_diferente check (categoria_pai_id is distinct from id)
);
create unique index categorias_nome_uq on public.categorias (lower(btrim(nome)));
create index categorias_pai_idx on public.categorias (categoria_pai_id);
create trigger categorias_atualizado_em before update on public.categorias
  for each row execute function privado.definir_atualizado_em();

-- 4. PRODUTOS -----------------------------------------------------------
create table public.produtos (
  id              bigint generated always as identity primary key,
  marketplace_id  smallint not null references public.marketplaces(id) on delete restrict,
  id_externo      text not null check (char_length(btrim(id_externo)) between 1 and 120),
  categoria_id    int references public.categorias(id) on delete set null,
  titulo          text not null check (char_length(btrim(titulo)) between 3 and 300),
  marca           text check (char_length(marca) <= 80),
  url_produto     text check (url_produto ~* '^https?://'),
  imagem_url      text check (imagem_url ~* '^https?://'),
  nota            numeric(2,1) check (nota between 0 and 5),
  total_vendas    integer check (total_vendas >= 0),
  preco_atual     numeric(12,2) not null check (preco_atual > 0),
  preco_original  numeric(12,2) check (preco_original > 0),
  moeda           char(3) not null default 'BRL' check (moeda ~ '^[A-Z]{3}$'),
  disponivel      boolean not null default true,
  demo            boolean not null default false,
  dados_extras    jsonb not null default '{}'::jsonb check (jsonb_typeof(dados_extras) = 'object'),
  verificado_em   timestamptz,
  criado_em       timestamptz not null default now(),
  atualizado_em   timestamptz not null default now(),
  -- Impede produto duplicado: o mesmo item do mesmo marketplace só existe uma vez
  constraint produtos_unicos unique (marketplace_id, id_externo)
);
comment on column public.produtos.id_externo is 'ID do item no marketplace (ex.: itemId da Shopee, MLB do Mercado Livre).';
create index produtos_categoria_idx on public.produtos (categoria_id);
create index produtos_marketplace_disp_idx on public.produtos (marketplace_id, disponivel);
create index produtos_atualizado_idx on public.produtos (atualizado_em desc);
create index produtos_titulo_trgm_idx on public.produtos using gin (titulo extensions.gin_trgm_ops);
create trigger produtos_atualizado_em before update on public.produtos
  for each row execute function privado.definir_atualizado_em();

-- Produto de marketplace demo é sempre demo
create or replace function privado.produto_marcar_demo()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select modo from public.marketplaces where id = new.marketplace_id) = 'demo' then
    new.demo := true;
  end if;
  return new;
end $$;
create trigger produtos_marcar_demo before insert or update of marketplace_id, demo on public.produtos
  for each row execute function privado.produto_marcar_demo();

-- 6. HISTÓRICO DE PREÇOS ------------------------------------------------
create table public.historico_precos (
  id             bigint generated always as identity primary key,
  produto_id     bigint not null references public.produtos(id) on delete cascade,
  preco          numeric(12,2) not null check (preco > 0),
  preco_original numeric(12,2) check (preco_original > 0),
  disponivel     boolean not null,
  variacao_pct   numeric(8,2),
  registrado_em  timestamptz not null default now()
);
comment on table public.historico_precos is 'Preenchido automaticamente a cada mudança de preço ou disponibilidade do produto. Não editável.';
create index historico_precos_produto_idx on public.historico_precos (produto_id, registrado_em desc);
create index historico_precos_data_brin on public.historico_precos using brin (registrado_em);

create or replace function privado.registrar_historico_preco()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'INSERT'
     or new.preco_atual is distinct from old.preco_atual
     or new.preco_original is distinct from old.preco_original
     or new.disponivel is distinct from old.disponivel then
    insert into public.historico_precos (produto_id, preco, preco_original, disponivel, variacao_pct)
    values (new.id, new.preco_atual, new.preco_original, new.disponivel,
            case when tg_op = 'UPDATE' and old.preco_atual > 0
                 then round((new.preco_atual - old.preco_atual) / old.preco_atual * 100, 2) end);
  end if;
  return new;
end $$;
create trigger produtos_historico_preco after insert or update of preco_atual, preco_original, disponivel
  on public.produtos for each row execute function privado.registrar_historico_preco();

-- Histórico é imutável (nem o painel pode alterar)
create or replace function privado.bloquear_alteracao()
returns trigger language plpgsql set search_path = '' as $$
begin
  raise exception 'Registros de % não podem ser alterados.', tg_table_name using errcode = 'P0002';
end $$;
create trigger historico_precos_imutavel before update on public.historico_precos
  for each row execute function privado.bloquear_alteracao();

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
