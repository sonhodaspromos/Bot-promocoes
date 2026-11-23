-- =====================================================================
-- 004 · Canais de divulgação, fila de publicações e histórico de publicações
-- =====================================================================

-- 10. CANAIS DE DIVULGAÇÃO ---------------------------------------------
create table public.canais (
  id                    smallint generated always as identity primary key,
  codigo                text not null unique check (codigo ~ '^[a-z0-9_]{2,40}$'),
  tipo                  text not null check (tipo in ('telegram', 'instagram_feed', 'instagram_story', 'whatsapp', 'outro')),
  nome                  text not null check (char_length(btrim(nome)) between 2 and 80),
  identificador_externo text check (char_length(identificador_externo) <= 120),
  modo                  text not null default 'demo' check (modo in ('real', 'demo', 'desativado')),
  limite_diario         integer check (limite_diario > 0),
  intervalo_minimo_min  integer not null default 0 check (intervalo_minimo_min between 0 and 1440),
  ativo                 boolean not null default true,
  criado_em             timestamptz not null default now(),
  atualizado_em         timestamptz not null default now()
);
comment on column public.canais.identificador_externo is 'Identificador público (ex.: @meucanal). Tokens NUNCA ficam aqui: use credenciais_integracoes.';
create trigger canais_atualizado_em before update on public.canais
  for each row execute function privado.definir_atualizado_em();

-- 11. FILA DE PUBLICAÇÕES ----------------------------------------------
create table public.fila_publicacoes (
  id               bigint generated always as identity primary key,
  promocao_id      bigint not null references public.promocoes(id) on delete cascade,
  canal_id         smallint not null references public.canais(id) on delete restrict,
  link_afiliado_id bigint not null references public.links_afiliados(id) on delete restrict,
  criativo_id      bigint references public.criativos(id) on delete set null,
  agendado_para    timestamptz not null default now(),
  prioridade       smallint not null default 5 check (prioridade between 1 and 9),
  status           text not null default 'pendente'
                   check (status in ('pendente', 'processando', 'concluida', 'falhou', 'cancelada')),
  tentativas       smallint not null default 0 check (tentativas >= 0),
  max_tentativas   smallint not null default 3 check (max_tentativas between 1 and 10),
  bloqueado_ate    timestamptz,
  ultimo_erro      text check (char_length(ultimo_erro) <= 1000),
  criado_em        timestamptz not null default now(),
  atualizado_em    timestamptz not null default now(),
  constraint fila_tentativas_limite check (tentativas <= max_tentativas)
);
comment on column public.fila_publicacoes.prioridade is '1 = mais urgente, 9 = menos urgente.';
-- Impede publicação duplicada: a mesma promoção não entra duas vezes na fila do mesmo canal
create unique index fila_sem_duplicidade on public.fila_publicacoes (promocao_id, canal_id)
  where status in ('pendente', 'processando');
create index fila_proximas_idx on public.fila_publicacoes (prioridade, agendado_para) where status = 'pendente';
create index fila_travadas_idx on public.fila_publicacoes (bloqueado_ate) where status = 'processando';
create index fila_promocao_idx on public.fila_publicacoes (promocao_id);
create index fila_canal_idx on public.fila_publicacoes (canal_id, status);
create index fila_link_idx on public.fila_publicacoes (link_afiliado_id);
create index fila_criativo_idx on public.fila_publicacoes (criativo_id);
create trigger fila_atualizado_em before update on public.fila_publicacoes
  for each row execute function privado.definir_atualizado_em();

-- Regras da fila: só entra/segue oferta aprovada, com link validado do mesmo produto, em canal ativo
create or replace function privado.fila_regras()
returns trigger language plpgsql set search_path = '' as $$
declare l record; p record; c record;
begin
  if new.status not in ('pendente', 'processando') then
    return new;
  end if;
  select validado, ativo, produto_id into l from public.links_afiliados where id = new.link_afiliado_id;
  select status, produto_id, demo into p from public.promocoes where id = new.promocao_id;
  select ativo, modo into c from public.canais where id = new.canal_id;

  if not (l.validado and l.ativo) then
    raise exception 'Fila recusada: o link de afiliado não está validado ou está inativo.' using errcode = 'P0010';
  end if;
  if l.produto_id <> p.produto_id then
    raise exception 'Fila recusada: o link é de outro produto.' using errcode = 'P0012';
  end if;
  if p.status not in ('aprovada', 'publicada') then
    raise exception 'Fila recusada: a promoção está "%" (precisa estar aprovada).', p.status using errcode = 'P0013';
  end if;
  if not c.ativo or c.modo = 'desativado' then
    raise exception 'Fila recusada: canal inativo.' using errcode = 'P0014';
  end if;
  if p.demo and c.modo = 'real' then
    raise exception 'Fila recusada: oferta de demonstração não pode ir para canal real.' using errcode = 'P0015';
  end if;
  return new;
end $$;
create trigger fila_regras before insert or update on public.fila_publicacoes
  for each row execute function privado.fila_regras();

-- 12. HISTÓRICO DE PUBLICAÇÕES -----------------------------------------
create table public.historico_publicacoes (
  id                  bigint generated always as identity primary key,
  fila_id             bigint references public.fila_publicacoes(id) on delete set null,
  promocao_id         bigint not null references public.promocoes(id) on delete cascade,
  canal_id            smallint not null references public.canais(id) on delete restrict,
  link_afiliado_id    bigint not null references public.links_afiliados(id) on delete restrict,
  criativo_id         bigint references public.criativos(id) on delete set null,
  resultado           text not null check (resultado in ('sucesso', 'erro')),
  id_mensagem_externa text check (char_length(id_mensagem_externa) <= 200),
  erro                text check (char_length(erro) <= 2000),
  demo                boolean not null default false,
  publicado_em        timestamptz not null default now(),
  constraint historico_erro_tem_motivo check (resultado = 'sucesso' or erro is not null)
);
comment on table public.historico_publicacoes is 'Registro imutável de cada tentativa de publicação.';
create index historico_pub_promocao_idx on public.historico_publicacoes (promocao_id, publicado_em desc);
create index historico_pub_canal_idx on public.historico_publicacoes (canal_id, publicado_em desc);
create index historico_pub_data_brin on public.historico_publicacoes using brin (publicado_em);
create index historico_pub_fila_idx on public.historico_publicacoes (fila_id);
create index historico_pub_link_idx on public.historico_publicacoes (link_afiliado_id);
create index historico_pub_criativo_idx on public.historico_publicacoes (criativo_id);

create or replace function privado.historico_pub_antes()
returns trigger language plpgsql set search_path = '' as $$
declare l record; p record; v_modo text;
begin
  select validado, ativo, produto_id into l from public.links_afiliados where id = new.link_afiliado_id;
  select status, produto_id, demo into p from public.promocoes where id = new.promocao_id;
  select modo into v_modo from public.canais where id = new.canal_id;
  new.demo := (v_modo = 'demo') or p.demo;

  if new.resultado = 'sucesso' then
    if not (l.validado and l.ativo) or l.produto_id <> p.produto_id then
      raise exception 'Publicação recusada: link de afiliado não validado para este produto.' using errcode = 'P0010';
    end if;
    if p.demo and v_modo = 'real' then
      raise exception 'Publicação recusada: oferta de demonstração em canal real.' using errcode = 'P0015';
    end if;
    if p.status not in ('aprovada', 'publicada') then
      raise exception 'Publicação recusada: a promoção está "%".', p.status using errcode = 'P0013';
    end if;
  end if;
  return new;
end $$;
create trigger historico_pub_antes before insert on public.historico_publicacoes
  for each row execute function privado.historico_pub_antes();

-- Depois de publicar: marca a promoção como publicada e atualiza o item da fila
create or replace function privado.historico_pub_depois()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_demo_promo boolean;
begin
  select demo into v_demo_promo from public.promocoes where id = new.promocao_id;
  if new.resultado = 'sucesso' and (not new.demo or v_demo_promo) then
    update public.promocoes set status = 'publicada'
    where id = new.promocao_id and status <> 'publicada';
  end if;

  if new.fila_id is not null then
    begin
    update public.fila_publicacoes f set
      status = case when new.resultado = 'sucesso' then 'concluida'
                    when f.tentativas >= f.max_tentativas then 'falhou'
                    else 'pendente' end,
      ultimo_erro = case when new.resultado = 'erro' then left(new.erro, 1000) end,
      bloqueado_ate = null,
      -- nova tentativa com espera crescente: 2, 4, 8... minutos
      agendado_para = case when new.resultado = 'erro'
                           then now() + make_interval(mins => power(2, greatest(f.tentativas, 1))::int)
                           else f.agendado_para end
    where f.id = new.fila_id;
    exception when sqlstate 'P0010' or sqlstate 'P0012' or sqlstate 'P0013' or sqlstate 'P0014' or sqlstate 'P0015' then
      -- não dá para tentar de novo (link/promoção/canal deixou de ser válido): cancela o item
      update public.fila_publicacoes
         set status = 'cancelada', bloqueado_ate = null, ultimo_erro = left(sqlerrm, 1000)
       where id = new.fila_id;
    end;
  end if;
  return new;
end $$;
create trigger historico_pub_depois after insert on public.historico_publicacoes
  for each row execute function privado.historico_pub_depois();

create trigger historico_pub_imutavel before update on public.historico_publicacoes
  for each row execute function privado.bloquear_alteracao();

-- Promoção pausada/rejeitada/expirada: cancela o que estava na fila
create or replace function privado.promocao_cancelar_fila()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.fila_publicacoes
     set status = 'cancelada', ultimo_erro = 'Promoção ' || new.status || coalesce(': ' || new.motivo_status, '')
   where promocao_id = new.id and status in ('pendente', 'processando');
  return new;
end $$;
create trigger promocoes_cancelar_fila after update of status on public.promocoes
  for each row when (new.status in ('pausada', 'rejeitada', 'expirada') and old.status is distinct from new.status)
  execute function privado.promocao_cancelar_fila();

-- Link invalidado/desativado: cancela a fila e pausa as promoções que ficaram sem link válido
create or replace function privado.link_invalidado()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.fila_publicacoes
     set status = 'cancelada', ultimo_erro = 'Link de afiliado invalidado'
   where link_afiliado_id = new.id and status in ('pendente', 'processando');

  update public.promocoes p
     set status = 'pausada', motivo_status = 'Sem link de afiliado validado'
   where p.produto_id = new.produto_id and p.status in ('aprovada', 'publicada')
     and not exists (select 1 from public.links_afiliados l
                     where l.produto_id = new.produto_id and l.validado and l.ativo and l.id <> new.id);
  return new;
end $$;
create trigger links_invalidado after update on public.links_afiliados
  for each row when ((old.validado and old.ativo) and not (new.validado and new.ativo))
  execute function privado.link_invalidado();

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
