-- =============================================================================================
-- 021 · Correções da auditoria da expansão modular
--   A. Cliques atribuídos à publicação (registrar_clique nunca gravava publicacao_id: o painel de
--      desempenho, o aprendizado e os testes A/B viam ZERO cliques fora dos testes) + canais de
--      atribuição da vitrine, do WhatsApp e dos alertas
--   B. Agregação diária de cliques (rollup) idempotente, reprocessável e protegida da retenção
--   C. Limites financeiros atômicos (orçamento por serviço/dia) e reserva atômica do WhatsApp
--   D. Webhook do WhatsApp: deduplicação por id da mensagem e proteção contra replay/fora de ordem
--   E. Workers: renovação de trava e saúde da fila (alertas de acúmulo e falha definitiva)
--   F. Financeiro: custos por serviço e receita estimada x confirmada x lucro líquido
--   G. Vitrine: só oferta vigente com preço conferido recentemente e sem alta desde a publicação
--   H. Testes A/B de horário
-- Reversão: supabase/rollback/20261125200000_auditoria_expansao.down.sql
-- =============================================================================================

insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('cliques.rollup_retencao_dias', '1095', 'numero', 'Por quantos dias a agregação diária de cliques é mantida', 365, 3650),
  ('cliques.rollup_reprocessar_dias', '3', 'numero', 'Dias recentes reagregados todo dia (cliques que chegam atrasados)', 1, 30),
  ('financeiro.alerta_pct', '80', 'numero', 'Percentual do orçamento diário que dispara alerta de consumo', 10, 100),
  ('fila.alerta_atraso_min', '30', 'numero', 'Tarefa pendente atrasada há mais de N minutos gera alerta', 5, 1440),
  ('fila.alerta_pendentes', '50', 'numero', 'Mais de N tarefas pendentes vencidas gera alerta', 1, 10000),
  ('vitrine.max_horas_sem_conferir', '24', 'numero', 'Preço conferido há mais de N horas não aparece na vitrine', 1, 72),
  ('inteligencia.min_comissoes_cancelamento', '20', 'numero', 'Comissões decididas mínimas por loja para usar a comissão líquida no ranking', 5, 1000)
on conflict (chave) do nothing;

-- A. atribuição do clique à publicação ----------------------------------------------------------------
insert into public.canais (codigo, tipo, nome, modo, ativo) values
  ('vitrine', 'outro', 'Vitrine (site)', 'desativado', false),
  ('whatsapp', 'whatsapp', 'WhatsApp (inscritos)', 'desativado', false),
  ('alerta', 'outro', 'Alertas de preço (Telegram)', 'desativado', false)
on conflict (codigo) do nothing;
comment on column public.canais.ativo is
  'Canais vitrine/whatsapp/alerta existem só para atribuir cliques (ativo = false: a fila de publicação não os usa).';

create index if not exists historico_pub_link_canal_idx
  on public.historico_publicacoes (link_afiliado_id, canal_id, publicado_em desc) where resultado = 'sucesso';

create or replace function public.registrar_clique(
  p_link bigint, p_canal text default null, p_ip_hash text default null,
  p_user_agent text default null, p_referer text default null)
returns table (url_destino text, oferta_ativa boolean)
language plpgsql security definer set search_path = '' as $$
declare l record; v_promo bigint; v_status text; v_canal smallint; v_motivo text; v_pub bigint;
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
  -- publicação de origem: a última publicação real deste link neste canal (atribuição do clique)
  if v_canal is not null then
    select h.id into v_pub from public.historico_publicacoes h
     where h.link_afiliado_id = p_link and h.canal_id = v_canal and h.resultado = 'sucesso' and not h.demo
       and h.publicado_em <= now()
     order by h.publicado_em desc limit 1;
  end if;

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
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, publicacao_id, ip_hash, user_agent, referer)
    values (p_link, v_promo, v_canal, v_pub, v_ip, left(p_user_agent, 400), left(p_referer, 500));
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

-- correção retroativa: cliques antigos sem publicação recebem a publicação de origem (mesma regra).
-- A tabela é imutável por gatilho; ele é suspenso só durante esta correção, dentro da migration.
alter table public.cliques disable trigger cliques_imutavel;
update public.cliques k set publicacao_id = (
    select h.id from public.historico_publicacoes h
     where h.link_afiliado_id = k.link_afiliado_id and h.canal_id = k.canal_id and h.resultado = 'sucesso'
       and not h.demo and h.publicado_em <= k.ocorrido_em
     order by h.publicado_em desc limit 1)
 where k.publicacao_id is null and k.canal_id is not null;
alter table public.cliques enable trigger cliques_imutavel;

-- B. agregação diária de cliques (rollup) -------------------------------------------------------------
create table if not exists public.cliques_diarios (
  dia              date not null,
  link_afiliado_id bigint not null,
  canal_id         smallint,
  promocao_id      bigint,
  publicacao_id    bigint,
  cliques          integer not null check (cliques > 0),
  visitantes       integer not null check (visitantes >= 0)
);
create unique index if not exists cliques_diarios_chave on public.cliques_diarios
  (dia, link_afiliado_id, coalesce(canal_id, -1), coalesce(promocao_id, -1), coalesce(publicacao_id, -1));
create index if not exists cliques_diarios_promocao on public.cliques_diarios (promocao_id, dia);
create index if not exists cliques_diarios_publicacao on public.cliques_diarios (publicacao_id);
create table if not exists public.cliques_agregacao (
  dia          date primary key,
  agregado_em  timestamptz not null default now(),
  cliques      bigint not null,
  linhas       integer not null
);
comment on table public.cliques_diarios is
  'Cliques válidos por dia (fuso do bot), link, canal, oferta e publicação. Recalculado de forma idempotente a partir de public.cliques; '
  'mantido por cliques.rollup_retencao_dias depois que os cliques brutos são apagados pela retenção.';
alter table public.cliques_diarios enable row level security;
alter table public.cliques_agregacao enable row level security;
create policy cliques_diarios_ler on public.cliques_diarios for select to authenticated using (privado.papel_atual() is not null);
create policy cliques_agregacao_ler on public.cliques_agregacao for select to authenticated using (privado.papel_atual() is not null);
revoke all on public.cliques_diarios, public.cliques_agregacao from anon, authenticated;
grant select on public.cliques_diarios, public.cliques_agregacao to authenticated;
grant all on public.cliques_diarios, public.cliques_agregacao to service_role;

create or replace function privado.dia_local(t timestamptz) returns date
language sql stable set search_path = '' as $$ select (t at time zone privado.fuso())::date $$;

-- Recalcula um dia inteiro (apaga e regrava na mesma transação): rodar 2x dá o mesmo resultado.
-- Recusa dias cujos cliques brutos já podem ter sido apagados pela retenção (regravaria números menores).
create or replace function public.agregar_cliques(p_dia date)
returns integer language plpgsql security definer set search_path = '' as $$
declare v_ini timestamptz; v_fim timestamptz; n int; v_total bigint;
        v_ret int := privado.config_numero('cliques.retencao_dias', 365)::int;
begin
  if p_dia is null or p_dia > privado.dia_local(now()) then
    raise exception 'dia inválido para agregação: %', p_dia using errcode = '22023';
  end if;
  -- dia já agregado e fora da retenção: os brutos podem ter sido apagados; regravar perderia contagem
  if p_dia <= privado.dia_local(now() - make_interval(days => v_ret))
     and exists (select 1 from public.cliques_agregacao g where g.dia = p_dia) then
    raise exception 'dia % fora da retenção dos cliques brutos e já agregado: agregação preservada', p_dia using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('cliques_rollup'), p_dia - date '2000-01-01');
  v_ini := p_dia::timestamp at time zone privado.fuso();
  v_fim := (p_dia + 1)::timestamp at time zone privado.fuso();
  delete from public.cliques_diarios where dia = p_dia;
  insert into public.cliques_diarios (dia, link_afiliado_id, canal_id, promocao_id, publicacao_id, cliques, visitantes)
  select p_dia, link_afiliado_id, canal_id, promocao_id, publicacao_id, count(*), count(distinct ip_hash)
    from public.cliques where ocorrido_em >= v_ini and ocorrido_em < v_fim
   group by link_afiliado_id, canal_id, promocao_id, publicacao_id;
  get diagnostics n = row_count;
  select coalesce(sum(cliques), 0) into v_total from public.cliques_diarios where dia = p_dia;
  insert into public.cliques_agregacao (dia, agregado_em, cliques, linhas) values (p_dia, now(), v_total, n)
  on conflict (dia) do update set agregado_em = now(), cliques = excluded.cliques, linhas = excluded.linhas;
  return n;
end $$;

-- Reprocessa um intervalo (ex.: depois de corrigir dados). Pula dias fora da retenção, sem erro.
create or replace function public.agregar_cliques_periodo(p_de date, p_ate date)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare d date; n int := 0; pulados int := 0;
        v_limite date := privado.dia_local(now() - make_interval(days => privado.config_numero('cliques.retencao_dias', 365)::int));
begin
  if p_ate - p_de > 400 then raise exception 'intervalo grande demais (máx. 400 dias)' using errcode = '22023'; end if;
  d := p_de;
  while d <= least(p_ate, privado.dia_local(now())) loop
    if d <= v_limite and exists (select 1 from public.cliques_agregacao g where g.dia = d) then pulados := pulados + 1;
    else perform public.agregar_cliques(d); n := n + 1; end if;
    d := d + 1;
  end loop;
  return jsonb_build_object('dias_agregados', n, 'dias_fora_da_retencao', pulados);
end $$;

-- Confere o rollup contra os cliques brutos (auditoria).
create or replace function public.verificar_rollup(p_dia date)
returns table (dia date, cliques_brutos bigint, cliques_agregados bigint, diferenca bigint)
language sql stable security definer set search_path = '' as $$
  with b as (select count(*) c from public.cliques
              where ocorrido_em >= p_dia::timestamp at time zone privado.fuso()
                and ocorrido_em < (p_dia + 1)::timestamp at time zone privado.fuso()),
       a as (select coalesce(sum(cliques), 0) c from public.cliques_diarios where dia = p_dia)
  select p_dia, b.c, a.c, b.c - a.c from a, b;
$$;

create or replace view public.vw_cliques_por_dia with (security_invoker = true) as
  select d.dia, c.codigo as canal, sum(d.cliques)::bigint as cliques, sum(d.visitantes)::bigint as visitantes
    from public.cliques_diarios d left join public.canais c on c.id = d.canal_id
   group by d.dia, c.codigo;
revoke all on public.vw_cliques_por_dia from anon, authenticated;
grant select on public.vw_cliques_por_dia to authenticated;

-- C. limites financeiros atômicos --------------------------------------------------------------------
create table if not exists public.orcamento_consumo (
  servico   text not null check (servico ~ '^[a-z_]{2,30}$'),
  dia       date not null,
  gasto_usd numeric(12,4) not null default 0 check (gasto_usd >= 0),
  teto_usd  numeric(12,4) not null check (teto_usd >= 0),
  alertado  boolean not null default false,
  atualizado_em timestamptz not null default now(),
  primary key (servico, dia)
);
alter table public.orcamento_consumo enable row level security;
create policy orcamento_ler on public.orcamento_consumo for select to authenticated using (privado.papel_atual() is not null);
revoke all on public.orcamento_consumo from anon, authenticated;
grant select on public.orcamento_consumo to authenticated;
grant all on public.orcamento_consumo to service_role;

-- Reserva atômica: só soma se couber no teto (a linha do dia fica travada pelo UPDATE; concorrentes esperam).
create or replace function public.orcamento_reservar(p_servico text, p_valor numeric, p_teto numeric)
returns boolean language plpgsql security definer set search_path = '' as $$
declare v_dia date := privado.dia_local(now()); n int;
begin
  if p_valor is null or p_valor < 0 or p_teto is null or p_teto < 0 then
    raise exception 'valor/teto inválidos' using errcode = '22023';
  end if;
  insert into public.orcamento_consumo (servico, dia, gasto_usd, teto_usd) values (p_servico, v_dia, 0, p_teto)
  on conflict (servico, dia) do nothing;
  update public.orcamento_consumo set gasto_usd = gasto_usd + p_valor, teto_usd = p_teto, atualizado_em = now()
   where servico = p_servico and dia = v_dia and gasto_usd + p_valor <= p_teto;
  get diagnostics n = row_count;
  return n = 1;
end $$;
-- Estorno de uma reserva que comprovadamente não gerou custo (falha CERTA antes do envio).
create or replace function public.orcamento_estornar(p_servico text, p_valor numeric)
returns void language sql security definer set search_path = '' as $$
  update public.orcamento_consumo set gasto_usd = greatest(0, gasto_usd - abs(p_valor)), atualizado_em = now()
   where servico = p_servico and dia = privado.dia_local(now());
$$;
-- Situação do dia; devolve alertar = true UMA vez quando o consumo cruza financeiro.alerta_pct.
create or replace function public.orcamento_situacao(p_servico text)
returns table (gasto_usd numeric, teto_usd numeric, pct numeric, alertar boolean)
language plpgsql security definer set search_path = '' as $$
declare r record; v_lim numeric := privado.config_numero('financeiro.alerta_pct', 80);
begin
  select * into r from public.orcamento_consumo where servico = p_servico and dia = privado.dia_local(now()) for update;
  if not found then gasto_usd := 0; teto_usd := 0; pct := 0; alertar := false; return next; return; end if;
  gasto_usd := r.gasto_usd; teto_usd := r.teto_usd;
  pct := case when r.teto_usd > 0 then round(100 * r.gasto_usd / r.teto_usd, 1) else 0 end;
  alertar := not r.alertado and pct >= v_lim;
  if alertar then update public.orcamento_consumo set alertado = true where servico = p_servico and dia = r.dia; end if;
  return next;
end $$;

-- Reserva do WhatsApp num passo só: descadastro, repetição, limite por pessoa e orçamento.
-- Trava a linha do inscrito (dois workers não passam do limite por pessoa) e a do orçamento.
create or replace function public.whatsapp_reservar_envio(p_inscrito bigint, p_promocao bigint, p_custo numeric,
                                                          p_teto numeric, p_max_dia int)
returns text language plpgsql security definer set search_path = '' as $$
declare i record; n int;
begin
  select id, descadastrado_em into i from public.whatsapp_inscritos where id = p_inscrito for update;
  if not found or i.descadastrado_em is not null then return 'descadastrado'; end if;
  if exists (select 1 from public.whatsapp_envios where inscrito_id = p_inscrito and promocao_id = p_promocao) then
    return 'repetido';
  end if;
  select count(*) into n from public.whatsapp_envios
   where inscrito_id = p_inscrito and reservado_em >= privado.dia_local(now())::timestamp at time zone privado.fuso();
  if n >= p_max_dia then return 'limite_pessoa'; end if;
  if not public.orcamento_reservar('whatsapp', p_custo, p_teto) then
    insert into public.whatsapp_eventos (inscrito_id, tipo, detalhe) values (null, 'bloqueio_orcamento', format('teto US$ %s', p_teto));
    return 'orcamento';
  end if;
  insert into public.whatsapp_envios (inscrito_id, promocao_id) values (p_inscrito, p_promocao);
  update public.whatsapp_inscritos set ultimo_envio_em = now() where id = p_inscrito;
  return 'ok';
end $$;

-- D. webhook do WhatsApp: deduplicação e proteção contra replay ---------------------------------------
create table if not exists public.whatsapp_webhook_mensagens (
  mensagem_id text primary key check (char_length(mensagem_id) between 4 and 200),
  recebido_em timestamptz not null default now()
);
alter table public.whatsapp_webhook_mensagens enable row level security;
revoke all on public.whatsapp_webhook_mensagens from anon, authenticated;
grant all on public.whatsapp_webhook_mensagens to service_role;

-- Processa UMA mensagem do webhook: ignora id repetido (a Meta reentrega), mensagem com mais de 7 dias
-- (janela de reentrega) e mensagem mais antiga que a última mudança de consentimento do telefone
-- (fora de ordem ou replay de um "QUERO OFERTAS" antigo depois de um "SAIR").
create or replace function public.whatsapp_processar_mensagem(p_mensagem_id text, p_telefone text, p_acao text,
                                                              p_enviada_em timestamptz, p_texto text default null)
returns text language plpgsql security definer set search_path = '' as $$
declare i record; n int;
begin
  if p_acao not in ('entrar', 'sair') then raise exception 'ação inválida' using errcode = '22023'; end if;
  if p_enviada_em is null or p_enviada_em < now() - interval '7 days' or p_enviada_em > now() + interval '5 minutes' then
    return 'ignorada_data';
  end if;
  insert into public.whatsapp_webhook_mensagens (mensagem_id) values (p_mensagem_id) on conflict do nothing;
  get diagnostics n = row_count;
  if n = 0 then return 'duplicada'; end if;
  select * into i from public.whatsapp_inscritos where telefone = p_telefone for update;
  if found and p_enviada_em <= greatest(i.consentimento_em, coalesce(i.descadastrado_em, '-infinity')) then
    return 'ignorada_fora_de_ordem';
  end if;
  if p_acao = 'sair' then
    perform public.whatsapp_descadastrar(p_telefone);
    -- guarda a hora da MENSAGEM (não a do processamento) para comparar a ordem das próximas
    update public.whatsapp_inscritos set descadastrado_em = p_enviada_em where telefone = p_telefone;
    return 'descadastrado';
  end if;
  insert into public.whatsapp_inscritos (telefone, categorias, consentimento_em, consentimento_origem, texto_consentimento)
  values (p_telefone, '{}', p_enviada_em, 'mensagem_recebida', coalesce(p_texto, 'Pedido de inscrição pelo WhatsApp'))
  on conflict (telefone) do update set descadastrado_em = null, consentimento_em = excluded.consentimento_em,
         consentimento_origem = excluded.consentimento_origem, texto_consentimento = excluded.texto_consentimento;
  insert into public.whatsapp_eventos (inscrito_id, tipo, detalhe)
  select id, 'optin', 'mensagem_recebida' from public.whatsapp_inscritos where telefone = p_telefone;
  return 'inscrito';
end $$;

-- E. workers: renovação de trava e saúde da fila --------------------------------------------------------
create or replace function public.tarefa_renovar(p_id bigint, p_worker text, p_bloqueio_min int)
returns boolean language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  update public.tarefas set bloqueada_ate = now() + make_interval(mins => greatest(p_bloqueio_min, 1)), atualizada_em = now()
   where id = p_id and status = 'processando' and worker = p_worker and bloqueada_ate >= now();
  get diagnostics n = row_count;
  return n = 1;   -- false = a trava já tinha vencido (outro worker pode ter assumido): pare o trabalho
end $$;

create or replace function public.fila_saude()
returns table (pendentes_atrasadas bigint, travas_vencidas bigint, falhas_24h bigint, mais_antiga_min numeric,
               alerta boolean, motivo text)
language plpgsql stable security definer set search_path = '' as $$
declare v_atraso int := privado.config_numero('fila.alerta_atraso_min', 30)::int;
        v_max int := privado.config_numero('fila.alerta_pendentes', 50)::int;
begin
  select count(*) filter (where status = 'pendente' and agendada_para < now() - make_interval(mins => v_atraso)),
         count(*) filter (where status = 'processando' and bloqueada_ate < now()),
         count(*) filter (where status = 'falhou' and atualizada_em >= now() - interval '24 hours'),
         round(coalesce(extract(epoch from now() - min(agendada_para) filter (where status = 'pendente' and agendada_para <= now())), 0) / 60, 1)
    into pendentes_atrasadas, travas_vencidas, falhas_24h, mais_antiga_min
    from public.tarefas;
  motivo := concat_ws('; ',
    case when falhas_24h > 0 then falhas_24h || ' tarefa(s) com falha definitiva nas últimas 24 h' end,
    case when pendentes_atrasadas > 0 then pendentes_atrasadas || ' tarefa(s) atrasada(s) há mais de ' || v_atraso || ' min' end,
    case when (select count(*) from public.tarefas where status = 'pendente' and agendada_para <= now()) > v_max
         then 'fila acumulada acima de ' || v_max end,
    case when travas_vencidas > 0 then travas_vencidas || ' tarefa(s) abandonada(s) (trava vencida)' end);
  alerta := motivo <> '';
  return next;
end $$;

-- F. financeiro --------------------------------------------------------------------------------------
create table if not exists public.custos_servicos (
  id          bigint generated always as identity primary key,
  mes         date not null check (extract(day from mes) = 1),
  servico     text not null check (servico ~ '^[a-z_]{2,30}$'),
  valor_usd   numeric(12,2) not null check (valor_usd >= 0),
  origem      text not null check (origem in ('fatura', 'estimativa')),
  observacao  text check (char_length(observacao) <= 300),
  criado_em   timestamptz not null default now(),
  unique (mes, servico, origem)
);
comment on table public.custos_servicos is
  'Custos mensais por serviço (Supabase, GitHub, WhatsApp, domínio…). "fatura" = valor cobrado de fato; "estimativa" = previsão.';
alter table public.custos_servicos enable row level security;
create policy custos_ler on public.custos_servicos for select to authenticated using (privado.papel_atual() is not null);
create policy custos_dono on public.custos_servicos for all to authenticated using (privado.eh_dono()) with check (privado.eh_dono());
revoke all on public.custos_servicos from anon, authenticated;
grant select, insert, update, delete on public.custos_servicos to authenticated;
grant all on public.custos_servicos to service_role;

-- Receita ESTIMADA (pendente) separada da CONFIRMADA (confirmada/paga). Lucro líquido só sobre a confirmada.
create or replace view public.vw_financeiro_mensal with (security_invoker = true) as
with meses as (
  select date_trunc('month', data_venda)::date as mes from public.comissoes where origem <> 'demo'
  union select mes from public.custos_servicos
  union select date_trunc('month', dia)::date from public.orcamento_consumo),
rec as (
  select date_trunc('month', data_venda)::date as mes,
         sum(valor_comissao) filter (where status = 'pendente') as estimada,
         sum(valor_comissao) filter (where status in ('confirmada', 'paga')) as confirmada,
         sum(valor_comissao) filter (where status = 'paga') as recebida,
         sum(valor_comissao) filter (where status = 'cancelada') as cancelada
    from public.comissoes where origem <> 'demo' group by 1),
fat as (select mes, sum(valor_usd) filter (where origem = 'fatura') as faturado,
               sum(valor_usd) filter (where origem = 'estimativa') as estimado
          from public.custos_servicos group by mes),
wa as (select date_trunc('month', dia)::date as mes, sum(gasto_usd) as whatsapp from public.orcamento_consumo
        where servico = 'whatsapp' group by 1)
select m.mes,
       coalesce(r.estimada, 0)   as receita_estimada_brl,
       coalesce(r.confirmada, 0) as receita_confirmada_brl,
       coalesce(r.recebida, 0)   as receita_recebida_brl,
       coalesce(r.cancelada, 0)  as receita_cancelada_brl,
       coalesce(f.faturado, 0)   as custos_faturados_usd,
       coalesce(f.estimado, 0)   as custos_estimados_usd,
       coalesce(w.whatsapp, 0)   as whatsapp_consumido_usd
  from meses m left join rec r using (mes) left join fat f using (mes) left join wa w using (mes);
revoke all on public.vw_financeiro_mensal from anon, authenticated;
grant select on public.vw_financeiro_mensal to authenticated;
comment on view public.vw_financeiro_mensal is
  'Comissões em BRL e custos em USD ficam separados (sem câmbio inventado). O painel calcula o lucro líquido '
  'com a cotação informada pelo dono, só sobre a receita CONFIRMADA. Pendente é estimativa, não receita.';

-- G. vitrine: só preço vigente e conferido -----------------------------------------------------------
drop function if exists public.ofertas_vitrine(integer);
create function public.ofertas_vitrine(p_dias int default 7)
returns table (promocao_id bigint, titulo text, preco numeric, preco_referencia numeric, desconto_pct numeric,
               desconto_comprovado boolean, imagem_url text, marca text, categoria text, marketplace_nome text,
               marketplace_codigo text, link_id bigint, publicada_em timestamptz, conferido_em timestamptz)
language sql stable security definer set search_path = '' as $$
  select pr.id, p.titulo, pr.preco_promocional,
         case when coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false) then pr.preco_referencia end,
         case when coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false)
              then coalesce((pr.analise ->> 'desconto_real')::numeric, pr.desconto_pct) end,
         coalesce((pr.analise ->> 'desconto_comprovado')::boolean, false),
         p.imagem_url, p.marca, c.nome, m.nome, m.codigo, l.id, pr.publicada_em, p.verificado_em
    from public.promocoes pr
    join public.produtos p on p.id = pr.produto_id and p.disponivel
    join public.marketplaces m on m.id = p.marketplace_id
    left join public.categorias c on c.id = p.categoria_id
    join lateral (select id from public.links_afiliados where produto_id = p.id and ativo and validado
                   order by validado_em desc nulls last limit 1) l on true
   where pr.status = 'publicada' and not pr.demo and not p.demo
     and pr.publicada_em >= now() - make_interval(days => greatest(p_dias, 1))
     and pr.classificacao in ('excelente', 'boa')
     and (pr.fim_em is null or pr.fim_em > now())
     -- preço conferido recentemente e que não subiu desde a publicação
     and p.verificado_em >= now() - make_interval(hours => privado.config_numero('vitrine.max_horas_sem_conferir', 24)::int)
     and p.preco_atual is not null and p.preco_atual <= pr.preco_promocional * 1.005
   order by pr.publicada_em desc
   limit 500;
$$;

-- H. A/B de horário ------------------------------------------------------------------------------------
alter table public.experimentos drop constraint if exists experimentos_tipo_check;
alter table public.experimentos add constraint experimentos_tipo_check check (tipo in ('legenda', 'arte', 'horario'));
comment on column public.experimentos.tipo is
  'legenda | arte | horario (A = horário escolhido pelo agendamento inteligente; B = próximo horário livre). '
  'O teste de horário só tem efeito com recursos.agendamento_inteligente ligado.';

-- privilégios das funções novas ------------------------------------------------------------------------
do $$ declare f text; begin
  foreach f in array array['agregar_cliques(date)', 'agregar_cliques_periodo(date, date)', 'verificar_rollup(date)',
    'orcamento_reservar(text, numeric, numeric)', 'orcamento_estornar(text, numeric)', 'orcamento_situacao(text)',
    'whatsapp_reservar_envio(bigint, bigint, numeric, numeric, integer)',
    'whatsapp_processar_mensagem(text, text, text, timestamptz, text)', 'tarefa_renovar(bigint, text, integer)',
    'fila_saude()', 'ofertas_vitrine(integer)'] loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end $$;
revoke execute on function privado.dia_local(timestamptz) from public, anon, authenticated;

-- manutenção: reagrega os últimos dias, agrega antes de a retenção apagar e limpa os ids do webhook ----
create or replace function privado.manter_rollup()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_rep int := privado.config_numero('cliques.rollup_reprocessar_dias', 3)::int;
        v_ret int := privado.config_numero('cliques.retencao_dias', 365)::int;
        v_rret int := privado.config_numero('cliques.rollup_retencao_dias', 1095)::int;
        hoje date := privado.dia_local(now()); d date; n int := 0; a int; w int; v_tz text := privado.fuso();
begin
  -- Percorre só o calendário (sem varrer os cliques): reagrega os dias recentes (cliques atrasados) e
  -- agrega todo dia ainda não agregado desde uma semana antes do limite da retenção (o dia que a
  -- retenção vai apagar hoje sempre é agregado antes). Primeira execução: até ~372 dias, uma vez só.
  for d in select g::date from generate_series(hoje - v_ret - 7, hoje - 1, interval '1 day') g loop
    if d >= hoje - v_rep or not exists (select 1 from public.cliques_agregacao g where g.dia = d) then
      perform public.agregar_cliques(d); n := n + 1;
    end if;
  end loop;
  -- cliques além da janela acima que nunca foram agregados (ex.: manutenção parada por muito tempo):
  -- agrega antes de a retenção apagar. Lê só as linhas que vão ser apagadas mesmo.
  for d in select distinct (k.ocorrido_em at time zone v_tz)::date from public.cliques k
            where k.ocorrido_em < (hoje - v_ret - 7)::timestamp at time zone v_tz loop
    if not exists (select 1 from public.cliques_agregacao g where g.dia = d) then
      perform public.agregar_cliques(d); n := n + 1;
    end if;
  end loop;
  delete from public.cliques_diarios where dia < hoje - v_rret;
  get diagnostics a = row_count;
  delete from public.whatsapp_webhook_mensagens where recebido_em < now() - interval '14 days';
  get diagnostics w = row_count;
  return jsonb_build_object('dias_agregados', n, 'rollup_apagado', a, 'ids_webhook_apagados', w);
end $$;
revoke execute on function privado.manter_rollup() from public, anon, authenticated;

-- A agregação roda ANTES da exclusão por retenção dentro da manutenção diária.
create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        v_cliques int := privado.config_numero('cliques.retencao_dias', 365)::int;
        v_rollup jsonb; v_lim date;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int; n_cupons int;
        n_cliques int; n_suspeitos int; n_convites int; n_selecoes int; v_expansao jsonb;
begin
  v_rollup := privado.manter_rollup();
  v_lim := privado.dia_local(now() - make_interval(days => v_cliques));
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
  -- dias inteiros além da retenção (manter_rollup já agregou todos eles acima): a contagem fica no rollup
  delete from public.cliques k where k.ocorrido_em < (v_lim + 1)::timestamp at time zone privado.fuso();
  get diagnostics n_cliques = row_count;
  delete from public.cliques_suspeitos where dia < (now() - interval '90 days')::date;
  get diagnostics n_suspeitos = row_count;
  delete from public.convites where usado_em is null and expira_em < now() - interval '30 days';
  get diagnostics n_convites = row_count;
  n_selecoes := privado.limpar_selecoes_antigas();
  v_expansao := privado.limpar_expansao();
  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'cupons_expirados', n_cupons, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria,
                            'cliques_apagados', n_cliques, 'cliques_suspeitos_apagados', n_suspeitos,
                            'convites_apagados', n_convites, 'selecoes_apagadas', n_selecoes, 'rollup', v_rollup) || v_expansao;
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;
