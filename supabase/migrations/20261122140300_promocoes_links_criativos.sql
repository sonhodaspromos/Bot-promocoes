-- =====================================================================
-- 003 · Promoções, links de afiliado e criativos
-- =====================================================================

-- 7. PROMOÇÕES ----------------------------------------------------------
create table public.promocoes (
  id                bigint generated always as identity primary key,
  produto_id        bigint not null references public.produtos(id) on delete cascade,
  preco_promocional numeric(12,2) not null check (preco_promocional > 0),
  preco_referencia  numeric(12,2) check (preco_referencia > 0),
  desconto_pct      numeric(5,2) generated always as (
                      case when preco_referencia > preco_promocional
                           then round((1 - preco_promocional / preco_referencia) * 100, 2) else 0 end) stored,
  comissao_pct      numeric(5,2) check (comissao_pct between 0 and 100),
  status            text not null default 'encontrada'
                    check (status in ('encontrada', 'aprovada', 'publicada', 'pausada', 'expirada', 'rejeitada')),
  motivo_status     text check (char_length(motivo_status) <= 300),
  origem            text not null default 'automatica' check (origem in ('automatica', 'manual', 'demo')),
  demo              boolean not null default false,
  inicio_em         timestamptz not null default now(),
  fim_em            timestamptz,
  aprovada_em       timestamptz,
  publicada_em      timestamptz,
  pausada_em        timestamptz,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now(),
  constraint promocoes_periodo_valido check (fim_em is null or fim_em > inicio_em),
  constraint promocoes_origem_demo check (origem <> 'demo' or demo)
);
comment on table public.promocoes is 'Ofertas. Um produto só pode ter UMA promoção ativa (encontrada/aprovada/publicada) por vez.';
-- Impede oferta duplicada: no máximo uma promoção ativa por produto
create unique index promocoes_uma_ativa_por_produto on public.promocoes (produto_id)
  where status in ('encontrada', 'aprovada', 'publicada');
create index promocoes_status_desconto_idx on public.promocoes (status, desconto_pct desc);
create index promocoes_publicada_idx on public.promocoes (publicada_em desc) where publicada_em is not null;
create index promocoes_produto_idx on public.promocoes (produto_id, criado_em desc);
create trigger promocoes_atualizado_em before update on public.promocoes
  for each row execute function privado.definir_atualizado_em();

-- 8. LINKS DE AFILIADOS -------------------------------------------------
create table public.links_afiliados (
  id              bigint generated always as identity primary key,
  produto_id      bigint not null references public.produtos(id) on delete cascade,
  promocao_id     bigint references public.promocoes(id) on delete set null,
  url             text not null check (url ~* '^https://[^[:space:]]+$' and char_length(url) <= 2000),
  url_curta       text check (url_curta ~* '^https://[^[:space:]]+$' and char_length(url_curta) <= 300),
  sub_id          text check (char_length(sub_id) <= 100),
  validado        boolean not null default false,
  validado_em     timestamptz,
  status_http     smallint check (status_http between 100 and 599),
  motivo_invalido text check (char_length(motivo_invalido) <= 300),
  ativo           boolean not null default true,
  criado_em       timestamptz not null default now(),
  atualizado_em   timestamptz not null default now(),
  constraint links_url_unica unique (url),
  constraint links_validado_com_data check (not validado or validado_em is not null)
);
comment on column public.links_afiliados.validado is 'Só pode ser true se o domínio for de afiliado do marketplace. O backend também confere se o link abre.';
create unique index links_url_curta_uq on public.links_afiliados (url_curta) where url_curta is not null;
create index links_produto_validos_idx on public.links_afiliados (produto_id, validado_em desc) where validado and ativo;
create index links_promocao_idx on public.links_afiliados (promocao_id);
create trigger links_atualizado_em before update on public.links_afiliados
  for each row execute function privado.definir_atualizado_em();

-- Regras do link: domínio de afiliado obrigatório para validar; URL alterada = validar de novo
create or replace function privado.link_regras()
returns trigger language plpgsql set search_path = '' as $$
declare v_dominios text[]; v_marketplace text; v_produto_promo bigint;
begin
  if tg_op = 'UPDATE' and (new.url is distinct from old.url or new.url_curta is distinct from old.url_curta)
     and old.validado and new.validado then
    new.validado := false;
    new.motivo_invalido := 'URL alterada: precisa ser validada de novo';
  end if;

  if new.promocao_id is not null then
    select produto_id into v_produto_promo from public.promocoes where id = new.promocao_id;
    if v_produto_promo <> new.produto_id then
      raise exception 'O link pertence a um produto diferente do da promoção.' using errcode = 'P0012';
    end if;
  end if;

  if new.validado then
    select m.dominios_afiliado, m.nome into v_dominios, v_marketplace
    from public.produtos p join public.marketplaces m on m.id = p.marketplace_id
    where p.id = new.produto_id;
    if not privado.dominio_permitido(new.url, v_dominios)
       or (new.url_curta is not null and not privado.dominio_permitido(new.url_curta, v_dominios)) then
      raise exception 'Link recusado: o domínio "%" não é um domínio de afiliado cadastrado para %.',
        privado.host_da_url(coalesce(case when privado.dominio_permitido(new.url, v_dominios) then new.url_curta end, new.url)),
        v_marketplace using errcode = 'P0011';
    end if;
    new.validado_em := coalesce(new.validado_em, now());
    new.motivo_invalido := null;
  else
    new.validado_em := null;
  end if;
  return new;
end $$;
create trigger links_regras before insert or update on public.links_afiliados
  for each row execute function privado.link_regras();

-- Regras da promoção: datas de status, demo e trava de link validado
create or replace function privado.promocao_regras()
returns trigger language plpgsql set search_path = '' as $$
declare v_mudou boolean := tg_op = 'INSERT' or new.status is distinct from old.status;
begin
  if (select demo from public.produtos where id = new.produto_id) then
    new.demo := true;
  end if;
  if new.origem = 'demo' then new.demo := true; end if;

  if v_mudou then
    if new.status = 'aprovada' then new.aprovada_em := now(); end if;
    if new.status = 'pausada' then new.pausada_em := now(); end if;
    if new.status = 'publicada' then
      if not exists (select 1 from public.links_afiliados l
                     where l.produto_id = new.produto_id and l.validado and l.ativo) then
        raise exception 'Oferta sem link de afiliado validado não pode ser publicada.' using errcode = 'P0010';
      end if;
      new.publicada_em := coalesce(new.publicada_em, now());
    end if;
    if new.status in ('aprovada', 'publicada') then new.motivo_status := null; end if;
  end if;
  return new;
end $$;
create trigger promocoes_regras before insert or update on public.promocoes
  for each row execute function privado.promocao_regras();

-- 9. IMAGENS E CRIATIVOS ------------------------------------------------
create table public.criativos (
  id              bigint generated always as identity primary key,
  promocao_id     bigint not null references public.promocoes(id) on delete cascade,
  tipo            text not null check (tipo in ('feed', 'story', 'telegram', 'original')),
  url             text check (url ~* '^https://'),
  caminho_storage text check (char_length(caminho_storage) <= 500),
  largura         integer check (largura between 100 and 4000),
  altura          integer check (altura between 100 and 4000),
  formato         text check (formato in ('jpeg', 'png', 'webp')),
  tamanho_bytes   integer check (tamanho_bytes between 1 and 20000000),
  hash_sha256     char(64) check (hash_sha256 ~ '^[0-9a-f]{64}$'),
  expira_em       timestamptz,
  criado_em       timestamptz not null default now(),
  constraint criativos_tem_local check (url is not null or caminho_storage is not null)
);
create unique index criativos_sem_duplicidade on public.criativos (promocao_id, tipo, hash_sha256) where hash_sha256 is not null;
create index criativos_promocao_idx on public.criativos (promocao_id, tipo);

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
