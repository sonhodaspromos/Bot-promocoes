-- =============================================================================================
-- 019 · Inteligência comercial
--   1. historico_precos_janela: histórico + o último preço ANTES da janela (âncora), para 7/30/90 dias
--   2. Produtos: GTIN (com dígito verificador), modelo, vendedor (loja oficial), estoque — tudo opcional:
--      nulo = a loja não informou (nunca preenchido com suposição)
--   3. produtos_equivalentes: mesmo produto em marketplaces diferentes, com nível de confiança
--   4. Comissões: código do item do relatório oficial → produto e oferta publicada (atribuição),
--      histórico de mudanças de status e conciliação do repasse (pagamento) por competência
--   5. Publicações: preço e categoria no momento do post; variante do teste A/B
--   6. experimentos (A/B), selecoes_ofertas (por que cada oferta foi escolhida ou recusada),
--      inteligencia_resumos (aprendizado e saturação calculados pelo bot, para o painel)
--   7. Views do painel: desempenho por publicação e conciliação de comissões
-- =============================================================================================

-- 1. histórico com âncora ---------------------------------------------------------------------
create or replace function public.historico_precos_janela(p_produtos bigint[], p_desde timestamptz)
returns table (produto_id bigint, preco numeric, disponivel boolean, registrado_em timestamptz, ancora boolean)
language sql stable set search_path = '' as $$
  select h.produto_id, h.preco, h.disponivel, h.registrado_em, false
    from public.historico_precos h
   where h.produto_id = any(p_produtos) and h.registrado_em >= p_desde
  union all
  select a.produto_id, a.preco, a.disponivel, a.registrado_em, true
    from unnest(p_produtos) as u(id)
    cross join lateral (
      select h.produto_id, h.preco, h.disponivel, h.registrado_em from public.historico_precos h
       where h.produto_id = u.id and h.registrado_em < p_desde
       order by h.registrado_em desc limit 1) a
  order by 1, 4;
$$;
revoke execute on function public.historico_precos_janela(bigint[], timestamptz) from public, anon;
grant execute on function public.historico_precos_janela(bigint[], timestamptz) to authenticated, service_role;
comment on function public.historico_precos_janela(bigint[], timestamptz) is
  'Histórico de preços desde p_desde + o último registro anterior (âncora). RLS de historico_precos vale (security invoker).';

-- 2. dados opcionais do produto --------------------------------------------------------------------
create or replace function privado.gtin_valido(p text)
returns boolean language plpgsql immutable set search_path = '' as $$
declare g text := regexp_replace(coalesce(p, ''), '\D', '', 'g'); soma int := 0; i int; d int;
begin
  if length(g) not in (8, 12, 13, 14) or g ~ '^(\d)\1*$' then return false; end if;
  for i in 1 .. length(g) - 1 loop
    d := substr(g, length(g) - i, 1)::int;
    soma := soma + d * (case when i % 2 = 1 then 3 else 1 end);
  end loop;
  return (10 - soma % 10) % 10 = substr(g, length(g), 1)::int;
end $$;

alter table public.produtos
  add column if not exists gtin text,
  add column if not exists modelo text check (char_length(modelo) <= 80),
  add column if not exists vendedor_id text check (char_length(vendedor_id) <= 80),
  add column if not exists vendedor_nome text check (char_length(vendedor_nome) <= 120),
  add column if not exists loja_oficial boolean,
  add column if not exists estoque integer check (estoque >= 0);
alter table public.produtos drop constraint if exists produtos_gtin_valido;
alter table public.produtos add constraint produtos_gtin_valido check (gtin is null or privado.gtin_valido(gtin));
comment on column public.produtos.gtin is 'GTIN/EAN oficial (dígito verificador conferido). Nulo = não informado.';
comment on column public.produtos.loja_oficial is 'true = loja oficial/Mall; false = vendedor comum; nulo = a loja não informou.';
comment on column public.produtos.estoque is 'Unidades informadas pela loja. Nulo = não informado (a API da Shopee não informa).';
create index if not exists produtos_gtin_idx on public.produtos (gtin) where gtin is not null;

-- 3. produtos equivalentes entre marketplaces ------------------------------------------------------
create table if not exists public.produtos_equivalentes (
  id           bigint generated always as identity primary key,
  produto_a    bigint not null references public.produtos(id) on delete cascade,
  produto_b    bigint not null references public.produtos(id) on delete cascade,
  confianca    text not null check (confianca in ('alta', 'media', 'baixa')),
  metodo       text not null check (metodo in ('gtin', 'marca_modelo', 'titulo')),
  pontuacao    numeric(4,3) not null check (pontuacao between 0 and 1),
  evidencia    text not null check (char_length(evidencia) <= 300),
  confirmado   boolean,           -- dono/editor confirma (true) ou rejeita (false); nulo = automático
  detectado_em timestamptz not null default now(),
  constraint equivalentes_ordem check (produto_a < produto_b),
  constraint equivalentes_unicos unique (produto_a, produto_b)
);
comment on table public.produtos_equivalentes is
  'Mesmo produto em marketplaces diferentes. alta = GTIN; media = marca + modelo; baixa = só título (apenas sinaliza).';
alter table public.produtos_equivalentes enable row level security;
create policy equivalentes_ler on public.produtos_equivalentes for select to authenticated using (privado.papel_atual() is not null);
create policy equivalentes_confirmar on public.produtos_equivalentes for update to authenticated
  using (privado.pode_editar()) with check (privado.pode_editar());
grant select, update (confirmado) on public.produtos_equivalentes to authenticated;
grant all on public.produtos_equivalentes to service_role;
create index if not exists equivalentes_b_idx on public.produtos_equivalentes (produto_b);

-- 4. comissões: atribuição, eventos e repasse ------------------------------------------------------
alter table public.comissoes
  add column if not exists id_externo_item text check (char_length(id_externo_item) <= 80),
  add column if not exists atribuicao text check (atribuicao in ('oferta_publicada', 'produto_sem_publicacao', 'item_desconhecido', 'manual'));
comment on column public.comissoes.atribuicao is
  'oferta_publicada = o item vendido é um produto que o bot publicou antes da venda; produto_sem_publicacao = produto conhecido, sem post antes da venda; item_desconhecido = item que o bot não monitora.';

create or replace function privado.comissao_atribuir()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_produto bigint; v_promo bigint;
begin
  if new.origem <> 'api' then
    new.atribuicao := coalesce(new.atribuicao, 'manual');
    return new;
  end if;
  if new.produto_id is null and new.id_externo_item is not null then
    select id into v_produto from public.produtos where marketplace_id = new.marketplace_id and id_externo = new.id_externo_item;
    new.produto_id := v_produto;
  end if;
  if new.produto_id is null then
    new.atribuicao := 'item_desconhecido';
    return new;
  end if;
  if new.promocao_id is null then
    select h.promocao_id into v_promo
      from public.historico_publicacoes h join public.promocoes pr on pr.id = h.promocao_id
     where pr.produto_id = new.produto_id and h.resultado = 'sucesso' and not h.demo
       and h.publicado_em < (new.data_venda + 1)::timestamptz
     order by h.publicado_em desc limit 1;
    new.promocao_id := v_promo;
  end if;
  new.atribuicao := case when new.promocao_id is not null then 'oferta_publicada' else 'produto_sem_publicacao' end;
  return new;
end $$;
drop trigger if exists comissoes_atribuir on public.comissoes;
create trigger comissoes_atribuir before insert on public.comissoes for each row execute function privado.comissao_atribuir();

create table if not exists public.comissoes_eventos (
  id           bigint generated always as identity primary key,
  comissao_id  bigint not null references public.comissoes(id) on delete cascade,
  status_de    text,
  status_para  text not null,
  valor_de     numeric(12,2),
  valor_para   numeric(12,2) not null,
  origem       text not null,
  ocorrido_em  timestamptz not null default now()
);
comment on table public.comissoes_eventos is 'Cada mudança de status ou de valor de uma comissão (trilha para conciliação). Imutável.';
alter table public.comissoes_eventos enable row level security;
create policy comissoes_eventos_ler on public.comissoes_eventos for select to authenticated using (privado.papel_atual() is not null);
grant select on public.comissoes_eventos to authenticated;
grant all on public.comissoes_eventos to service_role;
create index if not exists comissoes_eventos_idx on public.comissoes_eventos (comissao_id, ocorrido_em);
create trigger comissoes_eventos_imutavel before update or delete on public.comissoes_eventos
  for each row execute function privado.bloquear_alteracao();

create or replace function privado.comissao_transicao()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'UPDATE' then
    -- paga é estado final: só o dono desfaz (estorno registrado à mão). A API nunca altera uma comissão paga.
    if old.status = 'paga' and new.status <> 'paga' and not privado.eh_dono() then
      raise exception 'Comissão já marcada como paga: só o dono pode alterar.' using errcode = 'P0029';
    end if;
    if new.status is distinct from old.status or new.valor_comissao is distinct from old.valor_comissao then
      insert into public.comissoes_eventos (comissao_id, status_de, status_para, valor_de, valor_para, origem)
      values (new.id, old.status, new.status, old.valor_comissao, new.valor_comissao,
              case when privado.papel_atual() is null then 'sistema' else 'painel' end);
    end if;
  else
    insert into public.comissoes_eventos (comissao_id, status_de, status_para, valor_de, valor_para, origem)
    values (new.id, null, new.status, null, new.valor_comissao, new.origem);
  end if;
  return new;
end $$;
drop trigger if exists comissoes_transicao_antes on public.comissoes;
create trigger comissoes_transicao_antes before update on public.comissoes for each row execute function privado.comissao_transicao();
drop trigger if exists comissoes_transicao_depois on public.comissoes;
create trigger comissoes_transicao_depois after insert on public.comissoes for each row execute function privado.comissao_transicao();

create table if not exists public.comissoes_pagamentos (
  id               bigint generated always as identity primary key,
  marketplace_id   smallint not null references public.marketplaces(id),
  competencia      date not null check (extract(day from competencia) = 1),
  valor_recebido   numeric(12,2) not null check (valor_recebido >= 0),
  data_recebimento date not null,
  valor_esperado   numeric(12,2) not null,
  diferenca        numeric(12,2) not null,
  itens            integer not null,
  status           text not null check (status in ('conciliado', 'divergente')),
  observacao       text check (char_length(observacao) <= 500),
  registrado_em    timestamptz not null default now(),
  constraint comissoes_pagamentos_unico unique (marketplace_id, competencia)
);
comment on table public.comissoes_pagamentos is
  'Repasse recebido do marketplace por competência (mês das vendas) × soma das comissões confirmadas daquele mês.';
alter table public.comissoes_pagamentos enable row level security;
create policy comissoes_pagamentos_ler on public.comissoes_pagamentos for select to authenticated using (privado.papel_atual() is not null);
grant select on public.comissoes_pagamentos to authenticated;
grant all on public.comissoes_pagamentos to service_role;
create trigger comissoes_pagamentos_imutavel before update or delete on public.comissoes_pagamentos
  for each row execute function privado.bloquear_alteracao();

create or replace function public.registrar_repasse(p_marketplace smallint, p_competencia date, p_valor numeric,
                                                    p_data date, p_tolerancia numeric default 1.00, p_observacao text default null)
returns public.comissoes_pagamentos language plpgsql security definer set search_path = '' as $$
declare v_inicio date := date_trunc('month', p_competencia)::date; v_esperado numeric; v_itens int; r public.comissoes_pagamentos;
begin
  if not privado.eh_dono() then
    raise exception 'Só o dono registra repasses.' using errcode = '42501';
  end if;
  if p_valor < 0 or p_data < v_inicio then
    raise exception 'Valor ou data do repasse inválidos.' using errcode = 'P0030';
  end if;
  select coalesce(sum(valor_comissao), 0), count(*) into v_esperado, v_itens from public.comissoes
   where marketplace_id = p_marketplace and status = 'confirmada'
     and data_venda >= v_inicio and data_venda < (v_inicio + interval '1 month');
  insert into public.comissoes_pagamentos (marketplace_id, competencia, valor_recebido, data_recebimento, valor_esperado,
                                           diferenca, itens, status, observacao)
  values (p_marketplace, v_inicio, p_valor, p_data, v_esperado, p_valor - v_esperado, v_itens,
          case when abs(p_valor - v_esperado) <= p_tolerancia then 'conciliado' else 'divergente' end, p_observacao)
  returning * into r;
  -- só marca como paga quando o valor bate; divergente fica para conferência (nada é marcado)
  if r.status = 'conciliado' then
    update public.comissoes set status = 'paga', data_pagamento = p_data
     where marketplace_id = p_marketplace and status = 'confirmada'
       and data_venda >= v_inicio and data_venda < (v_inicio + interval '1 month');
  end if;
  return r;
end $$;
revoke execute on function public.registrar_repasse(smallint, date, numeric, date, numeric, text) from public, anon;
grant execute on function public.registrar_repasse(smallint, date, numeric, date, numeric, text) to authenticated, service_role;

-- 5. publicações: preço, categoria e variante --------------------------------------------------------
alter table public.historico_publicacoes
  add column if not exists preco_publicado numeric(12,2),
  add column if not exists categoria_id int references public.categorias(id) on delete set null,
  add column if not exists experimento_id bigint,
  add column if not exists variante text check (variante in ('A', 'B'));

create or replace function privado.historico_pub_contexto()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  select pr.preco_promocional, p.categoria_id into new.preco_publicado, new.categoria_id
    from public.promocoes pr join public.produtos p on p.id = pr.produto_id where pr.id = new.promocao_id;
  if (new.experimento_id is null) <> (new.variante is null) then
    raise exception 'Experimento e variante andam juntos.' using errcode = 'P0031';
  end if;
  return new;
end $$;
drop trigger if exists historico_pub_contexto on public.historico_publicacoes;
create trigger historico_pub_contexto before insert on public.historico_publicacoes
  for each row execute function privado.historico_pub_contexto();

-- 6. experimentos, seleções e resumos ----------------------------------------------------------------
create table if not exists public.experimentos (
  id              bigint generated always as identity primary key,
  nome            text not null check (char_length(btrim(nome)) between 3 and 80),
  tipo            text not null check (tipo in ('legenda', 'arte')),
  ativo           boolean not null default true,
  amostra_minima  integer not null default 30 check (amostra_minima between 10 and 1000),
  inicio_em       timestamptz not null default now(),
  fim_em          timestamptz,
  resultado       jsonb,                 -- calculado pelo bot (teste estatístico); o painel só lê
  resultado_em    timestamptz,
  criado_em       timestamptz not null default now()
);
create unique index if not exists experimentos_um_ativo on public.experimentos ((true)) where ativo;
alter table public.historico_publicacoes drop constraint if exists historico_pub_experimento_fk;
alter table public.historico_publicacoes add constraint historico_pub_experimento_fk
  foreign key (experimento_id) references public.experimentos(id) on delete set null;
alter table public.experimentos enable row level security;
create policy experimentos_ler on public.experimentos for select to authenticated using (privado.papel_atual() is not null);
create policy experimentos_criar on public.experimentos for insert to authenticated with check (privado.pode_editar());
create policy experimentos_editar on public.experimentos for update to authenticated using (privado.pode_editar()) with check (privado.pode_editar());
grant select, insert, update (nome, ativo, fim_em, amostra_minima) on public.experimentos to authenticated;
grant all on public.experimentos to service_role;

create table if not exists public.selecoes_ofertas (
  id            bigint generated always as identity primary key,
  promocao_id   bigint not null references public.promocoes(id) on delete cascade,
  decidido_em   timestamptz not null default now(),
  selecionada   boolean not null,
  posicao       integer,
  prioridade    numeric(6,2),
  receita_esperada numeric(12,4),
  detalhes      jsonb not null check (jsonb_typeof(detalhes) = 'object')
);
comment on table public.selecoes_ofertas is 'Decisão da seleção a cada rodada: escolhida ou recusada, com fatores, motivos e o que ficou sem dados.';
create index if not exists selecoes_promocao_idx on public.selecoes_ofertas (promocao_id, decidido_em desc);
alter table public.selecoes_ofertas enable row level security;
create policy selecoes_ler on public.selecoes_ofertas for select to authenticated using (privado.papel_atual() is not null);
grant select on public.selecoes_ofertas to authenticated;
grant all on public.selecoes_ofertas to service_role;

create table if not exists public.inteligencia_resumos (
  tipo         text primary key check (tipo in ('aprendizado', 'saturacao')),
  dados        jsonb not null,
  calculado_em timestamptz not null default now()
);
alter table public.inteligencia_resumos enable row level security;
create policy resumos_ler on public.inteligencia_resumos for select to authenticated using (privado.papel_atual() is not null);
grant select on public.inteligencia_resumos to authenticated;
grant all on public.inteligencia_resumos to service_role;

-- 7. views do painel ------------------------------------------------------------------------------------
create or replace view public.vw_desempenho_publicacoes with (security_invoker = true) as
with pubs as (
  select h.id, h.promocao_id, h.publicado_em, h.preco_publicado, h.categoria_id, h.experimento_id, h.variante,
         c.codigo as canal,
         count(*) over (partition by h.promocao_id) as pubs_da_oferta
    from public.historico_publicacoes h join public.canais c on c.id = h.canal_id
   where h.resultado = 'sucesso' and not h.demo
), vendas as (
  select promocao_id, sum(valor_comissao) as comissao, count(*) as conversoes
    from public.comissoes where status <> 'cancelada' and promocao_id is not null and atribuicao = 'oferta_publicada'
   group by promocao_id
)
select p.id as publicacao_id, p.promocao_id, p.publicado_em, p.canal,
       extract(hour from p.publicado_em at time zone privado.fuso())::int as hora,
       case when extract(hour from p.publicado_em at time zone privado.fuso()) < 6 then 'madrugada (0h–6h)'
            when extract(hour from p.publicado_em at time zone privado.fuso()) < 12 then 'manhã (6h–12h)'
            when extract(hour from p.publicado_em at time zone privado.fuso()) < 18 then 'tarde (12h–18h)'
            else 'noite (18h–24h)' end as periodo,
       cat.nome as categoria, p.preco_publicado,
       case when p.preco_publicado is null then null
            when p.preco_publicado < 50 then 'até R$ 50' when p.preco_publicado < 100 then 'R$ 50–100'
            when p.preco_publicado < 250 then 'R$ 100–250' when p.preco_publicado < 500 then 'R$ 250–500'
            else 'acima de R$ 500' end as faixa_preco,
       p.experimento_id, p.variante,
       (select count(*) from public.cliques k where k.publicacao_id = p.id)::int as cliques,
       (select count(*) from public.cliques k where k.publicacao_id = p.id
           and k.ocorrido_em < p.publicado_em + interval '2 hours')::int as cliques_2h,
       round(coalesce(v.comissao, 0) / p.pubs_da_oferta, 4) as comissao_atribuida,
       round(coalesce(v.conversoes, 0)::numeric / p.pubs_da_oferta, 4) as conversoes_atribuidas
  from pubs p left join public.categorias cat on cat.id = p.categoria_id
  left join vendas v on v.promocao_id = p.promocao_id;
comment on view public.vw_desempenho_publicacoes is
  'Uma linha por publicação real: cliques únicos (suspeitos fora), cliques nas 2 primeiras horas e comissão atribuída (dividida igualmente entre as publicações da oferta).';
grant select on public.vw_desempenho_publicacoes to authenticated, service_role;

create or replace view public.vw_conciliacao_comissoes with (security_invoker = true) as
select m.codigo as marketplace, x.competencia,
       count(*) filter (where c.status = 'pendente')   as pendentes,
       coalesce(sum(c.valor_comissao) filter (where c.status = 'pendente'), 0)   as valor_pendente,
       count(*) filter (where c.status = 'confirmada') as confirmadas,
       coalesce(sum(c.valor_comissao) filter (where c.status = 'confirmada'), 0) as valor_confirmado,
       count(*) filter (where c.status = 'cancelada')  as canceladas,
       coalesce(sum(c.valor_comissao) filter (where c.status = 'cancelada'), 0)  as valor_cancelado,
       count(*) filter (where c.status = 'paga')       as pagas,
       coalesce(sum(c.valor_comissao) filter (where c.status = 'paga'), 0)       as valor_pago,
       count(*) filter (where c.status = 'pendente' and c.data_venda < current_date - 90) as pendentes_antigas,
       count(*) filter (where c.status = 'confirmada' and c.data_venda < current_date - 75) as confirmadas_sem_repasse,
       count(*) filter (where c.atribuicao = 'oferta_publicada') as atribuidas_a_posts,
       (select p.status from public.comissoes_pagamentos p
         where p.marketplace_id = m.id and p.competencia = x.competencia) as repasse
  from public.comissoes c join public.marketplaces m on m.id = c.marketplace_id
  cross join lateral (select date_trunc('month', c.data_venda)::date as competencia) x
 where c.origem <> 'demo'
 group by m.id, m.codigo, x.competencia;
comment on view public.vw_conciliacao_comissoes is
  'Por marketplace e mês da venda: pendentes, confirmadas (aprovadas), canceladas e pagas; antigas sem definição; confirmadas sem repasse após 75 dias; situação do repasse.';
grant select on public.vw_conciliacao_comissoes to authenticated, service_role;

-- privilégios: o painel só lê (escrita via RLS + RPC); anon nada
revoke all on public.produtos_equivalentes, public.comissoes_eventos, public.comissoes_pagamentos, public.experimentos,
              public.selecoes_ofertas, public.inteligencia_resumos, public.vw_desempenho_publicacoes,
              public.vw_conciliacao_comissoes from anon, authenticated;
grant select, update (confirmado) on public.produtos_equivalentes to authenticated;
grant select on public.comissoes_eventos, public.comissoes_pagamentos, public.selecoes_ofertas, public.inteligencia_resumos,
                public.vw_desempenho_publicacoes, public.vw_conciliacao_comissoes to authenticated;
grant select, insert, update (nome, ativo, fim_em, amostra_minima) on public.experimentos to authenticated;

-- 8. configurações -----------------------------------------------------------------------------------
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('inteligencia.bloquear_desconto_artificial', 'true', 'booleano', 'Bloqueia oferta cujo preço subiu antes do "desconto" (sobe e desce), com histórico suficiente', null, null),
  ('inteligencia.historico_cobertura_minima_pct', '80', 'numero', 'Percentual mínimo da janela (7/30/90 dias) observado para tirar conclusões', 50, 100),
  ('inteligencia.alta_artificial_pct', '15', 'numero', 'Alta recente acima do típico de 90 dias que caracteriza desconto artificial', 5, 100),
  ('inteligencia.amostra_minima_segmento', '20', 'numero', 'Publicações mínimas para um segmento (categoria, horário, canal, faixa) influenciar a seleção', 5, 500),
  ('inteligencia.min_conversoes', '10', 'numero', 'Vendas atribuídas mínimas para estimar conversão e receita esperada', 3, 500),
  ('inteligencia.peso_receita', '30', 'numero', 'Peso (%) da receita esperada na prioridade (só reordena dentro da mesma classificação)', 0, 70),
  ('inteligencia.equivalencia_titulo_min', '0.85', 'numero', 'Similaridade mínima de títulos para sinalizar possível produto repetido (confiança baixa)', 0.6, 1),
  ('inteligencia.saturacao_queda_pct', '50', 'numero', 'Queda de cliques (2 h) em relação à base que caracteriza canal saturado', 20, 90),
  ('inteligencia.saturacao_max_categoria', '3', 'numero', 'Máximo de posts da mesma categoria na janela de saturação', 1, 20),
  ('inteligencia.saturacao_janela_horas', '6', 'numero', 'Janela (horas) da contagem de posts por categoria', 1, 48)
on conflict (chave) do nothing;

-- 9. manutenção: decisões antigas ----------------------------------------------------------------------
create or replace function privado.limpar_selecoes_antigas()
returns int language sql security definer set search_path = '' as $$
  with apagadas as (delete from public.selecoes_ofertas where decidido_em < now() - interval '30 days' returning 1)
  select count(*)::int from apagadas;
$$;

create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        v_cliques int := privado.config_numero('cliques.retencao_dias', 365)::int;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int; n_cupons int;
        n_cliques int; n_suspeitos int; n_convites int; n_selecoes int;
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
  delete from public.cliques where ocorrido_em < now() - make_interval(days => v_cliques);
  get diagnostics n_cliques = row_count;
  delete from public.cliques_suspeitos where dia < (now() - interval '90 days')::date;
  get diagnostics n_suspeitos = row_count;
  delete from public.convites where usado_em is null and expira_em < now() - interval '30 days';
  get diagnostics n_convites = row_count;
  n_selecoes := privado.limpar_selecoes_antigas();

  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'cupons_expirados', n_cupons, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria,
                            'cliques_apagados', n_cliques, 'cliques_suspeitos_apagados', n_suspeitos, 'convites_apagados', n_convites,
                            'selecoes_apagadas', n_selecoes);
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;
