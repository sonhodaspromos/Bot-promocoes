-- =============================================================================================
-- 020 · Expansão modular (tudo DESLIGADO por padrão: recursos.* = false)
--   1. Feature flags (recursos.*) e parâmetros de cada módulo
--   2. Marketplaces novos: Amazon e AliExpress (API oficial; modo desativado até validar) e
--      Magalu (manual: o programa não oferece API pública de afiliados)
--   3. Canais por categoria (vários canais, cada um com as suas categorias)
--   4. WhatsApp: inscritos com consentimento registrado, descadastro e trilha imutável
--   5. Alertas de preço (pelo bot do Telegram: a pessoa inicia a conversa)
--   6. Fila persistente de tarefas para workers independentes (SKIP LOCKED, idempotência, retentativa)
-- =============================================================================================

-- 1. feature flags ----------------------------------------------------------------------------
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('recursos.fonte_amazon', 'false', 'booleano', 'Fonte Amazon (Creators API oficial). Exige validação real antes de ligar', null, null),
  ('recursos.fonte_aliexpress', 'false', 'booleano', 'Fonte AliExpress (Open Platform oficial de afiliados). Exige validação real', null, null),
  ('recursos.whatsapp', 'false', 'booleano', 'Envio pelo WhatsApp Business (Cloud API) só para quem consentiu', null, null),
  ('recursos.vitrine', 'false', 'booleano', 'Gera a vitrine pública de ofertas (site estático)', null, null),
  ('recursos.alertas_preco', 'false', 'booleano', 'Alertas de preço pelo bot do Telegram', null, null),
  ('recursos.canais_por_categoria', 'false', 'booleano', 'Vários canais, cada um com as suas categorias', null, null),
  ('recursos.agendamento_inteligente', 'false', 'booleano', 'Distribui os posts nos horários de melhor desempenho', null, null),
  ('recursos.workers', 'false', 'booleano', 'Tarefas pesadas vão para a fila de workers independentes', null, null),
  ('whatsapp.orcamento_diario_usd', '5', 'numero', 'Teto diário de gasto com mensagens de marketing no WhatsApp (US$)', 0, 1000),
  ('whatsapp.custo_mensagem_usd', '0.0625', 'numero', 'Custo por mensagem de marketing entregue no Brasil (confira a tabela da Meta)', 0, 1),
  ('whatsapp.max_por_inscrito_dia', '3', 'numero', 'Máximo de mensagens por inscrito por dia', 1, 20),
  ('alertas.max_por_pessoa_dia', '5', 'numero', 'Máximo de avisos de preço por pessoa por dia', 1, 50),
  ('alertas.max_alertas_por_pessoa', '10', 'numero', 'Máximo de alertas ativos por pessoa', 1, 50),
  ('vitrine.url_base', '"https://example.invalid"', 'texto', 'Endereço público da vitrine (para SEO e sitemap)', null, null),
  ('agendamento.min_intervalo_canal_min', '20', 'numero', 'Intervalo mínimo entre posts agendados no mesmo canal', 5, 240)
on conflict (chave) do nothing;

-- 2. marketplaces novos ----------------------------------------------------------------------------
insert into public.marketplaces (codigo, nome, site_url, dominios_afiliado, possui_api, modo) values
  ('amazon', 'Amazon', 'https://www.amazon.com.br', array['amzn.to', 'www.amazon.com.br', 'amazon.com.br'], true, 'desativado'),
  ('aliexpress', 'AliExpress', 'https://pt.aliexpress.com', array['s.click.aliexpress.com', 'pt.aliexpress.com', 'aliexpress.com'], true, 'desativado'),
  ('magalu', 'Magalu', 'https://www.magazineluiza.com.br', '{}', false, 'manual')
on conflict (codigo) do nothing;
comment on column public.marketplaces.dominios_afiliado is
  'Domínios aceitos em links de afiliado. Magalu começa vazio: preencha com o domínio do link gerado no app/portal do Parceiro Magalu.';

-- 3. canais por categoria ------------------------------------------------------------------------
create table if not exists public.canais_categorias (
  canal_id     smallint not null references public.canais(id) on delete cascade,
  categoria_id int not null references public.categorias(id) on delete cascade,
  criado_em    timestamptz not null default now(),
  primary key (canal_id, categoria_id)
);
comment on table public.canais_categorias is
  'Canal com linhas aqui só recebe ofertas dessas categorias. Canal sem linhas recebe todas (comportamento anterior).';
alter table public.canais_categorias enable row level security;
create policy canais_cat_ler on public.canais_categorias for select to authenticated using (privado.papel_atual() is not null);
create policy canais_cat_criar on public.canais_categorias for insert to authenticated with check (privado.pode_editar());
create policy canais_cat_apagar on public.canais_categorias for delete to authenticated using (privado.pode_editar());
create index if not exists canais_categorias_cat_idx on public.canais_categorias (categoria_id);

-- 4. WhatsApp: consentimento e descadastro -------------------------------------------------------
create table if not exists public.whatsapp_inscritos (
  id                 bigint generated always as identity primary key,
  telefone           text not null unique check (telefone ~ '^\+[1-9][0-9]{7,14}$'),
  categorias         int[] not null default '{}',
  consentimento_em   timestamptz not null,
  consentimento_origem text not null check (consentimento_origem in ('mensagem_recebida', 'formulario', 'painel')),
  texto_consentimento text not null check (char_length(texto_consentimento) between 10 and 1000),
  descadastrado_em   timestamptz,
  ultimo_envio_em    timestamptz,
  criado_em          timestamptz not null default now()
);
comment on table public.whatsapp_inscritos is
  'Só recebe mensagem quem pediu (opt-in registrado, com o texto aceito). Descadastrado nunca recebe; os dados pessoais são apagados após 30 dias.';
create table if not exists public.whatsapp_eventos (
  id          bigint generated always as identity primary key,
  inscrito_id bigint references public.whatsapp_inscritos(id) on delete set null,
  tipo        text not null check (tipo in ('optin', 'optout', 'envio', 'falha', 'bloqueio_orcamento')),
  custo_usd   numeric(10,4),
  detalhe     text check (char_length(detalhe) <= 300),
  ocorrido_em timestamptz not null default now()
);
create trigger whatsapp_eventos_imutavel before update or delete on public.whatsapp_eventos
  for each row execute function privado.bloquear_alteracao();
create index if not exists whatsapp_eventos_data_idx on public.whatsapp_eventos (ocorrido_em desc);
alter table public.whatsapp_inscritos enable row level security;
alter table public.whatsapp_eventos enable row level security;
create policy wa_inscritos_dono on public.whatsapp_inscritos for select to authenticated using (privado.eh_dono());
create policy wa_eventos_ler on public.whatsapp_eventos for select to authenticated using (privado.papel_atual() is not null);

-- descadastro: a pessoa sai e o telefone deixa de existir no banco após 30 dias (LGPD: necessidade)
create or replace function public.whatsapp_descadastrar(p_telefone text)
returns boolean language plpgsql security definer set search_path = '' as $$
declare v_id bigint;
begin
  update public.whatsapp_inscritos set descadastrado_em = now()
   where telefone = p_telefone and descadastrado_em is null returning id into v_id;
  if v_id is not null then
    insert into public.whatsapp_eventos (inscrito_id, tipo) values (v_id, 'optout');
  end if;
  return v_id is not null;
end $$;
revoke execute on function public.whatsapp_descadastrar(text) from public, anon, authenticated;
grant execute on function public.whatsapp_descadastrar(text) to service_role;

-- 5. alertas de preço ------------------------------------------------------------------------------
create table if not exists public.alertas_preco (
  id               bigint generated always as identity primary key,
  telegram_chat_id bigint not null,
  termo            text not null check (char_length(btrim(termo)) between 3 and 60),
  preco_maximo     numeric(12,2) check (preco_maximo > 0),
  ativo            boolean not null default true,
  criado_em        timestamptz not null default now()
);
comment on table public.alertas_preco is
  'Alertas criados pela própria pessoa no bot do Telegram (/alerta). /parar apaga tudo dela.';
create index if not exists alertas_preco_chat_idx on public.alertas_preco (telegram_chat_id) where ativo;
create table if not exists public.alertas_avisos (
  alerta_id   bigint not null references public.alertas_preco(id) on delete cascade,
  promocao_id bigint not null references public.promocoes(id) on delete cascade,
  enviado_em  timestamptz not null default now(),
  primary key (alerta_id, promocao_id)
);
alter table public.alertas_preco enable row level security;
alter table public.alertas_avisos enable row level security;
create policy alertas_dono on public.alertas_preco for select to authenticated using (privado.eh_dono());

-- 6. fila persistente de tarefas -------------------------------------------------------------------
create table if not exists public.tarefas (
  id                 bigint generated always as identity primary key,
  tipo               text not null check (tipo ~ '^[a-z_]+\.[a-z_]+$'),
  payload            jsonb not null default '{}'::jsonb,
  chave_idempotencia text check (char_length(chave_idempotencia) <= 200),
  status             text not null default 'pendente' check (status in ('pendente', 'processando', 'concluida', 'falhou')),
  tentativas         smallint not null default 0,
  max_tentativas     smallint not null default 5 check (max_tentativas between 1 and 20),
  agendada_para      timestamptz not null default now(),
  bloqueada_ate      timestamptz,
  worker             text check (char_length(worker) <= 80),
  ultimo_erro        text check (char_length(ultimo_erro) <= 2000),
  resultado          jsonb,
  criada_em          timestamptz not null default now(),
  atualizada_em      timestamptz not null default now()
);
create unique index if not exists tarefas_idempotencia on public.tarefas (tipo, chave_idempotencia) where chave_idempotencia is not null;
create index if not exists tarefas_proximas on public.tarefas (tipo, agendada_para) where status in ('pendente', 'processando');
alter table public.tarefas enable row level security;
create policy tarefas_ler on public.tarefas for select to authenticated using (privado.papel_atual() is not null);

create or replace function public.tarefa_enfileirar(p_tipo text, p_payload jsonb, p_chave text default null,
                                                    p_agendar_para timestamptz default now())
returns bigint language plpgsql security definer set search_path = '' as $$
declare v bigint;
begin
  insert into public.tarefas (tipo, payload, chave_idempotencia, agendada_para)
  values (p_tipo, coalesce(p_payload, '{}'::jsonb), p_chave, coalesce(p_agendar_para, now()))
  on conflict (tipo, chave_idempotencia) where chave_idempotencia is not null do nothing
  returning id into v;
  return v;   -- null = já existia (idempotente)
end $$;

create or replace function public.tarefas_pegar(p_tipos text[], p_limite int, p_bloqueio_min int, p_worker text)
returns setof public.tarefas language plpgsql security definer set search_path = '' as $$
begin
  -- trava vencida e sem tentativas: falhou (não fica girando para sempre)
  update public.tarefas set status = 'falhou', ultimo_erro = coalesce(ultimo_erro, 'Tempo esgotado no worker'),
         bloqueada_ate = null, atualizada_em = now()
   where status = 'processando' and bloqueada_ate < now() and tentativas >= max_tentativas;
  return query
  update public.tarefas t set status = 'processando', tentativas = t.tentativas + 1, worker = p_worker,
         bloqueada_ate = now() + make_interval(mins => greatest(p_bloqueio_min, 1)), atualizada_em = now()
   where t.id in (
     select id from public.tarefas
      where tipo = any(p_tipos) and tentativas < max_tentativas
        and ((status = 'pendente' and agendada_para <= now()) or (status = 'processando' and bloqueada_ate < now()))
      order by agendada_para, id
      limit greatest(p_limite, 0)
      for update skip locked)
  returning t.*;
end $$;

create or replace function public.tarefa_concluir(p_id bigint, p_ok boolean, p_erro text default null,
                                                  p_resultado jsonb default null, p_worker text default null)
returns boolean language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  update public.tarefas set
    status = case when p_ok then 'concluida' when tentativas >= max_tentativas then 'falhou' else 'pendente' end,
    agendada_para = case when p_ok then agendada_para else now() + make_interval(secs => least(3600, 30 * power(2, tentativas))) end,
    bloqueada_ate = null, ultimo_erro = case when p_ok then null else left(p_erro, 2000) end,
    resultado = case when p_ok then p_resultado else resultado end, atualizada_em = now()
   where id = p_id and status = 'processando' and (p_worker is null or worker = p_worker);
  get diagnostics n = row_count;
  return n = 1;   -- false = outro worker assumiu (trava vencida): o resultado deste é descartado
end $$;
revoke execute on function public.tarefa_enfileirar(text, jsonb, text, timestamptz) from public, anon, authenticated;
revoke execute on function public.tarefas_pegar(text[], int, int, text) from public, anon, authenticated;
revoke execute on function public.tarefa_concluir(bigint, boolean, text, jsonb, text) from public, anon, authenticated;
grant execute on function public.tarefa_enfileirar(text, jsonb, text, timestamptz) to service_role;
grant execute on function public.tarefas_pegar(text[], int, int, text) to service_role;
grant execute on function public.tarefa_concluir(bigint, boolean, text, jsonb, text) to service_role;

-- privilégios -----------------------------------------------------------------------------------------
revoke all on public.canais_categorias, public.whatsapp_inscritos, public.whatsapp_eventos, public.alertas_preco,
              public.alertas_avisos, public.tarefas from anon, authenticated;
grant select, insert, delete on public.canais_categorias to authenticated;
grant select on public.whatsapp_inscritos, public.whatsapp_eventos, public.alertas_preco, public.tarefas to authenticated;
grant all on public.canais_categorias, public.whatsapp_inscritos, public.whatsapp_eventos, public.alertas_preco,
             public.alertas_avisos, public.tarefas to service_role;

-- manutenção: dados pessoais de descadastrados e tarefas antigas ------------------------------------------
create or replace function privado.limpar_expansao()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a int; b int;
begin
  delete from public.whatsapp_inscritos where descadastrado_em < now() - interval '30 days';
  get diagnostics a = row_count;
  delete from public.tarefas where status in ('concluida', 'falhou') and atualizada_em < now() - interval '30 days';
  get diagnostics b = row_count;
  return jsonb_build_object('inscritos_apagados', a, 'tarefas_apagadas', b);
end $$;

create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        v_cliques int := privado.config_numero('cliques.retencao_dias', 365)::int;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int; n_cupons int;
        n_cliques int; n_suspeitos int; n_convites int; n_selecoes int; v_expansao jsonb;
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
  v_expansao := privado.limpar_expansao();

  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'cupons_expirados', n_cupons, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria,
                            'cliques_apagados', n_cliques, 'cliques_suspeitos_apagados', n_suspeitos, 'convites_apagados', n_convites,
                            'selecoes_apagadas', n_selecoes) || v_expansao;
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;

-- 7. funções de apoio aos módulos (service_role) -------------------------------------------------------
create table if not exists public.whatsapp_envios (
  inscrito_id bigint not null references public.whatsapp_inscritos(id) on delete cascade,
  promocao_id bigint not null references public.promocoes(id) on delete cascade,
  reservado_em timestamptz not null default now(),
  primary key (inscrito_id, promocao_id)
);
alter table public.whatsapp_envios enable row level security;
revoke all on public.whatsapp_envios from anon, authenticated;
grant all on public.whatsapp_envios to service_role;

create or replace function public.whatsapp_destinatarios(p_categoria int, p_max_dia int, p_promocao bigint)
returns table (id bigint, telefone text) language sql stable security definer set search_path = '' as $$
  select i.id, i.telefone from public.whatsapp_inscritos i
   where i.descadastrado_em is null
     and (cardinality(i.categorias) = 0 or p_categoria = any(i.categorias))
     and not exists (select 1 from public.whatsapp_envios e where e.inscrito_id = i.id and e.promocao_id = p_promocao)
     and (select count(*) from public.whatsapp_eventos ev where ev.inscrito_id = i.id and ev.tipo = 'envio'
           and ev.ocorrido_em >= date_trunc('day', now() at time zone privado.fuso()) at time zone privado.fuso()) < p_max_dia
   order by i.id;
$$;
create or replace function public.whatsapp_reservar(p_inscrito bigint, p_promocao bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  insert into public.whatsapp_envios (inscrito_id, promocao_id) values (p_inscrito, p_promocao) on conflict do nothing;
  get diagnostics n = row_count;
  if n = 1 then update public.whatsapp_inscritos set ultimo_envio_em = now() where id = p_inscrito; end if;
  return n = 1;
end $$;
create or replace function public.whatsapp_gasto_hoje()
returns numeric language sql stable security definer set search_path = '' as $$
  select coalesce(sum(custo_usd), 0) from public.whatsapp_eventos
   where ocorrido_em >= date_trunc('day', now() at time zone privado.fuso()) at time zone privado.fuso();
$$;
create or replace function public.whatsapp_inscrever(p_telefone text, p_origem text, p_texto text, p_categorias int[] default '{}')
returns bigint language plpgsql security definer set search_path = '' as $$
declare v bigint;
begin
  insert into public.whatsapp_inscritos (telefone, categorias, consentimento_em, consentimento_origem, texto_consentimento)
  values (p_telefone, coalesce(p_categorias, '{}'), now(), p_origem, p_texto)
  on conflict (telefone) do update set descadastrado_em = null, consentimento_em = now(),
         consentimento_origem = excluded.consentimento_origem, texto_consentimento = excluded.texto_consentimento
  returning id into v;
  insert into public.whatsapp_eventos (inscrito_id, tipo, detalhe) values (v, 'optin', p_origem);
  return v;
end $$;

create or replace function public.alertas_avisos_hoje()
returns table (telegram_chat_id bigint, avisos bigint) language sql stable security definer set search_path = '' as $$
  select a.telegram_chat_id, count(*) from public.alertas_avisos v join public.alertas_preco a on a.id = v.alerta_id
   where v.enviado_em >= date_trunc('day', now() at time zone privado.fuso()) at time zone privado.fuso()
   group by a.telegram_chat_id;
$$;

create or replace function public.ofertas_publicadas_desde(p_desde timestamptz)
returns table (promocao_id bigint, titulo text, preco numeric, link_id bigint, categoria_id int)
language sql stable security definer set search_path = '' as $$
  select distinct on (pr.id) pr.id, p.titulo, pr.preco_promocional, h.link_afiliado_id, p.categoria_id
    from public.historico_publicacoes h
    join public.promocoes pr on pr.id = h.promocao_id
    join public.produtos p on p.id = pr.produto_id
   where h.resultado = 'sucesso' and not h.demo and h.publicado_em >= p_desde and pr.status = 'publicada'
   order by pr.id, h.publicado_em desc;
$$;

create or replace function public.ofertas_vitrine(p_dias int default 7)
returns table (promocao_id bigint, titulo text, preco numeric, preco_referencia numeric, desconto_pct numeric,
               desconto_comprovado boolean, imagem_url text, marca text, categoria text, marketplace_nome text,
               link_id bigint, publicada_em timestamptz)
language sql stable security definer set search_path = '' as $$
  select pr.id, p.titulo, pr.preco_promocional,
         case when coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false) then pr.preco_referencia end,
         case when coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false)
              then coalesce((pr.analise ->> 'desconto_real')::numeric, pr.desconto_pct) end,
         coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false),
         p.imagem_url, p.marca, c.nome, m.nome, l.id, pr.publicada_em
    from public.promocoes pr
    join public.produtos p on p.id = pr.produto_id and p.disponivel
    join public.marketplaces m on m.id = p.marketplace_id
    left join public.categorias c on c.id = p.categoria_id
    join lateral (select id from public.links_afiliados where produto_id = p.id and ativo and validado
                   order by validado_em desc nulls last limit 1) l on true
   where pr.status = 'publicada' and not pr.demo and not p.demo
     and pr.publicada_em >= now() - make_interval(days => greatest(p_dias, 1))
     and pr.classificacao in ('excelente', 'boa')
   order by pr.publicada_em desc
   limit 500;
$$;

create or replace function public.cupons_vitrine()
returns table (titulo text, codigo text, regras text, fim_em timestamptz, marketplace_nome text)
language sql stable security definer set search_path = '' as $$
  select c.titulo, c.codigo, c.regras, c.fim_em, m.nome from public.cupons c join public.marketplaces m on m.id = c.marketplace_id
   where c.status = 'ativo' and not c.demo and now() >= c.inicio_em and now() < c.fim_em order by c.fim_em;
$$;

create or replace function public.ultimo_agendado(p_canal smallint)
returns timestamptz language sql stable security definer set search_path = '' as $$
  select max(agendado_para) from public.fila_publicacoes where canal_id = p_canal and status in ('pendente', 'processando');
$$;

do $$ declare f text; begin
  foreach f in array array['whatsapp_destinatarios(integer, integer, bigint)', 'whatsapp_reservar(bigint, bigint)',
    'whatsapp_gasto_hoje()', 'whatsapp_inscrever(text, text, text, integer[])', 'alertas_avisos_hoje()',
    'ofertas_publicadas_desde(timestamptz)', 'ofertas_vitrine(integer)', 'cupons_vitrine()', 'ultimo_agendado(smallint)'] loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;

-- ---------------------------------------------------------------- desempenho dos cliques (teste de carga)
-- Achado no teste de 100 mil cliques/dia: p_ip_hash (text) comparado com ip_hash (char(64)) forçava
-- conversão da coluna e o limite por visitante varria a tabela (~330 ms por clique com 3 milhões de linhas).
-- O painel contava cliques por publicação lendo o heap; o índice abaixo permite leitura só do índice.
create or replace function public.registrar_clique(
  p_link bigint, p_canal text default null, p_ip_hash text default null,
  p_user_agent text default null, p_referer text default null)
returns table (url_destino text, oferta_ativa boolean)
language plpgsql security definer set search_path = '' as $$
declare l record; v_promo bigint; v_status text; v_canal smallint; v_motivo text;
        v_ip char(64) := p_ip_hash;   -- mesmo tipo da coluna: sem isso o índice cliques_ip_idx não é usado
        v_janela int := privado.config_numero('cliques.janela_unico_horas', 24)::int;
        v_max_visitante int := privado.config_numero('cliques.max_por_visitante_10min', 20)::int;
        v_max_link int := privado.config_numero('cliques.max_por_link_minuto', 120)::int;
begin
  select id, produto_id, promocao_id, coalesce(url_curta, url) as destino, validado and ativo as valido
    into l from public.links_afiliados where id = p_link;
  if not found then return; end if;

  select id, status into v_promo, v_status from public.promocoes
   where id = l.promocao_id or (l.promocao_id is null and produto_id = l.produto_id)
   order by (id = l.promocao_id) desc, criado_em desc limit 1;
  select id into v_canal from public.canais where codigo = p_canal;

  if v_ip is null then
    v_motivo := 'sem_identificacao';
  elsif (select count(*) from public.cliques where ip_hash = v_ip and ocorrido_em > now() - interval '10 minutes') >= v_max_visitante then
    v_motivo := 'excesso_visitante';
  elsif (select count(*) from public.cliques where link_afiliado_id = p_link and ocorrido_em > now() - interval '1 minute') >= v_max_link then
    v_motivo := 'pico_link';
  elsif exists (select 1 from public.cliques where link_afiliado_id = p_link and ip_hash = v_ip
                 and ocorrido_em > now() - make_interval(hours => v_janela)) then
    v_motivo := 'repetido';
  end if;

  if v_motivo is null then
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ip_hash, user_agent, referer)
    values (p_link, v_promo, v_canal, v_ip, left(p_user_agent, 400), left(p_referer, 500));
  else
    insert into public.cliques_suspeitos as s (link_afiliado_id, motivo) values (p_link, v_motivo)
    on conflict (link_afiliado_id, dia, motivo) do update set quantidade = s.quantidade + 1, ultimo_em = now();
  end if;

  url_destino := case when l.valido then l.destino end;
  oferta_ativa := l.valido and coalesce(v_status in ('aprovada', 'publicada'), false);
  return next;
end $$;
revoke execute on function public.registrar_clique(bigint, text, text, text, text) from public, anon, authenticated;
grant execute on function public.registrar_clique(bigint, text, text, text, text) to service_role;
create index if not exists cliques_publicacao_data_idx on public.cliques (publicacao_id, ocorrido_em);
