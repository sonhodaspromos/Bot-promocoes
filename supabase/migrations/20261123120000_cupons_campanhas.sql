-- =====================================================================
-- 016 · Cupons de desconto e campanhas
--   cupons             cupom (com código) ou campanha da loja (sem código, com link de afiliado)
--   cupons_produtos    produtos em que o cupom vale (manual = aprovado; sugestão do bot = aguarda aprovação)
--   cupons_publicacoes stories/posts diários de cada cupom (1 por canal por dia, imutável)
-- Nada aqui é inventado: código, desconto, mínimo e validade vêm do cadastro do dono
-- ou da API oficial da loja (campanhas da Shopee).
-- =====================================================================

create table public.cupons (
  id               bigint generated always as identity primary key,
  marketplace_id   smallint not null references public.marketplaces(id),
  tipo             text not null default 'cupom' check (tipo in ('cupom', 'campanha')),
  origem           text not null default 'manual' check (origem in ('manual', 'automatica')),
  id_externo       text check (char_length(id_externo) <= 100),
  titulo           text not null check (char_length(btrim(titulo)) between 3 and 120),
  codigo           text check (codigo ~ '^[A-Z0-9_-]{3,30}$'),
  desconto_tipo    text check (desconto_tipo in ('percentual', 'valor')),
  desconto_valor   numeric(10,2) check (desconto_valor > 0),
  valor_minimo     numeric(10,2) check (valor_minimo >= 0),
  desconto_maximo  numeric(10,2) check (desconto_maximo > 0),
  link_afiliado    text check (link_afiliado ~ '^https://' and char_length(link_afiliado) <= 2000),
  imagem_url       text check (imagem_url ~ '^https://' and char_length(imagem_url) <= 2000),
  palavras_chave   text[] not null default '{}' check (cardinality(palavras_chave) <= 20),
  categoria_id     int references public.categorias(id) on delete set null,
  regras           text check (char_length(regras) <= 300),
  inicio_em        timestamptz not null default now(),
  fim_em           timestamptz not null,
  status           text not null default 'ativo' check (status in ('ativo', 'pausado', 'expirado')),
  demo             boolean not null default false,
  criado_por       uuid references auth.users(id) on delete set null default auth.uid(),
  criado_em        timestamptz not null default now(),
  atualizado_em    timestamptz not null default now(),
  constraint cupons_periodo check (fim_em > inicio_em),
  constraint cupons_codigo_obrigatorio check (tipo = 'campanha' or codigo is not null),
  constraint cupons_link_obrigatorio check (tipo = 'cupom' or link_afiliado is not null),
  constraint cupons_desconto_completo check ((desconto_tipo is null) = (desconto_valor is null)),
  constraint cupons_percentual_plausivel check (desconto_tipo is distinct from 'percentual' or desconto_valor <= 90),
  constraint cupons_externo_unico unique (marketplace_id, id_externo)
);
comment on table public.cupons is 'Cupons (código digitado no checkout) e campanhas da loja, divulgados nos stories enquanto válidos.';
create unique index cupons_codigo_ativo_unico on public.cupons (marketplace_id, codigo) where status = 'ativo' and codigo is not null;
create index cupons_vigentes_idx on public.cupons (fim_em) where status = 'ativo';
create index cupons_categoria_idx on public.cupons (categoria_id);
create index cupons_criado_por_idx on public.cupons (criado_por);
create trigger cupons_atualizado_em before update on public.cupons
  for each row execute function privado.definir_atualizado_em();

-- link de afiliado do cupom/campanha: mesmo domínio e formato exigidos dos produtos
create or replace function privado.cupom_regras()
returns trigger language plpgsql set search_path = '' as $$
declare v_dominios text[]; v_padrao text; v_nome text; v_modo text;
begin
  new.codigo := nullif(upper(btrim(new.codigo)), '');
  new.titulo := btrim(new.titulo);
  select dominios_afiliado, padrao_afiliado, nome, modo into v_dominios, v_padrao, v_nome, v_modo
    from public.marketplaces where id = new.marketplace_id;
  if v_modo = 'demo' then new.demo := true; end if;
  if new.link_afiliado is not null then
    if not privado.dominio_permitido(new.link_afiliado, v_dominios) then
      raise exception 'Link do cupom recusado: o domínio "%" não é um domínio de afiliado cadastrado para %.',
        privado.host_da_url(new.link_afiliado), v_nome using errcode = 'P0011';
    end if;
    if v_padrao is not null and new.link_afiliado !~ v_padrao then
      raise exception 'Link do cupom recusado: não tem a atribuição de afiliado de %.', v_nome using errcode = 'P0024';
    end if;
  end if;
  if new.status = 'ativo' and new.fim_em <= now() then
    new.status := 'expirado';
  end if;
  return new;
end $$;
create trigger cupons_regras before insert or update on public.cupons
  for each row execute function privado.cupom_regras();

-- ---------------------------------------------------------------------
create table public.cupons_produtos (
  cupom_id     bigint not null references public.cupons(id) on delete cascade,
  promocao_id  bigint not null references public.promocoes(id) on delete cascade,
  origem       text not null default 'manual' check (origem in ('manual', 'sugestao')),
  aprovado     boolean not null default false,
  motivo       text check (char_length(motivo) <= 200),
  criado_em    timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  primary key (cupom_id, promocao_id)
);
comment on table public.cupons_produtos is 'Produtos em que o cupom vale. Sugestões do bot só são usadas depois de aprovadas.';
create index cupons_produtos_promocao_idx on public.cupons_produtos (promocao_id) where aprovado;
create trigger cupons_produtos_atualizado_em before update on public.cupons_produtos
  for each row execute function privado.definir_atualizado_em();

create or replace function privado.cupom_produto_regras()
returns trigger language plpgsql set search_path = '' as $$
declare v_mkt_cupom smallint; v_mkt_prod smallint;
begin
  select marketplace_id into v_mkt_cupom from public.cupons where id = new.cupom_id;
  select p.marketplace_id into v_mkt_prod from public.promocoes pr join public.produtos p on p.id = pr.produto_id
   where pr.id = new.promocao_id;
  if v_mkt_cupom is distinct from v_mkt_prod then
    raise exception 'O cupom é de outra loja: só vale para produtos da mesma loja.' using errcode = 'P0026';
  end if;
  if tg_op = 'INSERT' and new.origem = 'manual' then new.aprovado := true; end if;
  return new;
end $$;
create trigger cupons_produtos_regras before insert or update on public.cupons_produtos
  for each row execute function privado.cupom_produto_regras();

-- ---------------------------------------------------------------------
create table public.cupons_publicacoes (
  id                   bigint generated always as identity primary key,
  cupom_id             bigint not null references public.cupons(id) on delete cascade,
  canal_id             smallint not null references public.canais(id),
  dia                  date not null default (now() at time zone 'America/Recife')::date,
  resultado            text not null check (resultado in ('sucesso', 'erro')),
  id_mensagem_externa  text check (char_length(id_mensagem_externa) <= 200),
  erro                 text check (char_length(erro) <= 1000),
  demo                 boolean not null default false,
  publicado_em         timestamptz not null default now()
);
comment on table public.cupons_publicacoes is 'Publicações diárias dos cupons (1 com sucesso por cupom, canal e dia). Imutável.';
create unique index cupons_pub_um_por_dia on public.cupons_publicacoes (cupom_id, canal_id, dia) where resultado = 'sucesso';
create index cupons_pub_canal_idx on public.cupons_publicacoes (canal_id, dia);
create trigger cupons_publicacoes_imutavel before update on public.cupons_publicacoes
  for each row execute function privado.bloquear_alteracao();

create or replace function privado.cupom_publicacao_regras()
returns trigger language plpgsql set search_path = '' as $$
declare c record; v_canal_modo text; v_canal_ativo boolean;
begin
  select status, inicio_em, fim_em, demo into c from public.cupons where id = new.cupom_id;
  select modo, ativo into v_canal_modo, v_canal_ativo from public.canais where id = new.canal_id;
  new.dia := (now() at time zone privado.fuso())::date;
  new.demo := c.demo or v_canal_modo = 'demo';
  if new.resultado = 'sucesso' then
    if c.status <> 'ativo' or now() < c.inicio_em or now() >= c.fim_em then
      raise exception 'Cupom fora da validade ou pausado: não pode ser publicado.' using errcode = 'P0025';
    end if;
    if not v_canal_ativo or v_canal_modo = 'desativado' then
      raise exception 'Canal inativo.' using errcode = 'P0014';
    end if;
    if c.demo and v_canal_modo = 'real' then
      raise exception 'Cupom de demonstração não pode ir para canal real.' using errcode = 'P0015';
    end if;
  end if;
  return new;
end $$;
create trigger cupons_publicacoes_regras before insert on public.cupons_publicacoes
  for each row execute function privado.cupom_publicacao_regras();

-- ---------------------------------------------------------------------
-- segurança: mesmas regras das outras tabelas
alter table public.cupons enable row level security;
alter table public.cupons_produtos enable row level security;
alter table public.cupons_publicacoes enable row level security;
revoke all on public.cupons, public.cupons_produtos, public.cupons_publicacoes from anon;

create policy "cupons: admins leem" on public.cupons for select to authenticated using ((select privado.eh_admin()));
create policy "cupons: editores inserem" on public.cupons for insert to authenticated
  with check ((select privado.pode_editar()) and origem = 'manual');
create policy "cupons: editores alteram" on public.cupons for update to authenticated
  using ((select privado.pode_editar())) with check ((select privado.pode_editar()));
create policy "cupons: editores removem" on public.cupons for delete to authenticated using ((select privado.pode_editar()));

create policy "cupons_produtos: admins leem" on public.cupons_produtos for select to authenticated using ((select privado.eh_admin()));
create policy "cupons_produtos: editores inserem" on public.cupons_produtos for insert to authenticated
  with check ((select privado.pode_editar()) and origem = 'manual');
create policy "cupons_produtos: editores alteram" on public.cupons_produtos for update to authenticated
  using ((select privado.pode_editar())) with check ((select privado.pode_editar()));
create policy "cupons_produtos: editores removem" on public.cupons_produtos for delete to authenticated using ((select privado.pode_editar()));

create policy "cupons_publicacoes: admins leem" on public.cupons_publicacoes for select to authenticated using ((select privado.eh_admin()));
-- (inserir publicação é só do backend, que usa a chave secreta)

create trigger cupons_auditoria after insert or update or delete on public.cupons
  for each row execute function privado.auditar();
create trigger cupons_produtos_auditoria after insert or update or delete on public.cupons_produtos
  for each row execute function privado.auditar();

-- o painel não pode forjar campos do sistema
create or replace function privado.cupom_restricoes_painel()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is null then return new; end if;   -- backend
  if tg_op = 'INSERT' then
    new.origem := 'manual'; new.id_externo := null;
  elsif new.origem is distinct from old.origem or new.id_externo is distinct from old.id_externo
        or new.demo is distinct from old.demo then
    raise exception 'Origem e marcação de demonstração do cupom são do sistema.' using errcode = 'P0023';
  end if;
  return new;
end $$;
create trigger cupons_restricoes_painel before insert or update on public.cupons
  for each row execute function privado.cupom_restricoes_painel();

-- ---------------------------------------------------------------------
-- vista do painel: cupons com contagens
create or replace view public.vw_cupons_painel with (security_invoker = true) as
select c.*, m.nome as marketplace_nome, m.codigo as marketplace_codigo,
       (select count(*) from public.cupons_produtos cp where cp.cupom_id = c.id and cp.aprovado) as produtos_aprovados,
       (select count(*) from public.cupons_produtos cp where cp.cupom_id = c.id and not cp.aprovado) as sugestoes,
       (select count(*) from public.cupons_publicacoes p where p.cupom_id = c.id and p.resultado = 'sucesso') as publicacoes,
       (select max(p.publicado_em) from public.cupons_publicacoes p where p.cupom_id = c.id and p.resultado = 'sucesso') as ultima_publicacao
  from public.cupons c join public.marketplaces m on m.id = c.marketplace_id;
revoke all on public.vw_cupons_painel from anon;
grant select on public.vw_cupons_painel to authenticated;

-- manutenção diária também expira cupons vencidos
create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int; n_cupons int;
begin
  update public.promocoes set status = 'expirada', motivo_status = 'Período da promoção terminou'
   where fim_em < now() and status in ('encontrada', 'aprovada', 'publicada');
  get diagnostics n_expiradas = row_count;
  update public.cupons set status = 'expirado' where status in ('ativo', 'pausado') and fim_em <= now();
  get diagnostics n_cupons = row_count;

  delete from public.execucoes where iniciada_em < now() - make_interval(days => v_logs);
  get diagnostics n_execucoes = row_count;
  delete from public.logs where criado_em < now() - make_interval(days => v_logs);
  get diagnostics n_logs = row_count;
  delete from public.fila_publicacoes
   where status in ('concluida', 'cancelada', 'falhou') and atualizado_em < now() - interval '90 days';
  get diagnostics n_fila = row_count;
  delete from public.auditoria where criado_em < now() - interval '365 days';
  get diagnostics n_auditoria = row_count;

  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'cupons_expirados', n_cupons, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria);
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;

-- ajustes
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('cupons.ativo',               'true',  'booleano', 'Divulgar cupons e campanhas nos stories todos os dias', null, null),
  ('cupons.max_stories_dia',     '3',     'numero',   'Máximo de stories de cupom por dia (somam na cota de 50 publicações/dia do Instagram)', 0, 4),
  ('cupons.publicar_telegram',   'true',  'booleano', 'Também publicar os cupons no canal do Telegram', null, null),
  ('cupons.horario',             '9',     'numero',   'Hora a partir da qual os cupons do dia são publicados', 0, 23),
  ('cupons.buscar_campanhas',    'true',  'booleano', 'Buscar campanhas oficiais da Shopee uma vez por dia', null, null),
  ('cupons.sugerir_produtos',    'true',  'booleano', 'Sugerir produtos encontrados que combinam com o cupom (só publica depois da sua aprovação)', null, null),
  ('cupons.max_sugestoes',       '10',    'numero',   'Máximo de sugestões de produto por cupom', 0, 50)
on conflict (chave) do nothing;
