-- =====================================================================
-- 008 · Monitor de preço, fila do worker, manutenção e views do painel
-- =====================================================================

-- Fuso horário do sistema (configurável)
create or replace function privado.fuso()
returns text language sql stable security definer set search_path = '' as $$
  select coalesce((select valor #>> '{}' from public.configuracoes
                    where chave = 'sistema.fuso_horario' and jsonb_typeof(valor) = 'string'), 'America/Recife')
$$;

create or replace function privado.config_booleano(p_chave text, p_padrao boolean)
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select (valor #>> '{}')::boolean from public.configuracoes
                    where chave = p_chave and jsonb_typeof(valor) = 'boolean'), p_padrao)
$$;

-- MONITOR DE PREÇO (no próprio banco) ----------------------------------
-- Preço subiu além da tolerância ou produto ficou indisponível -> promoção pausada e fila cancelada
create or replace function privado.monitor_preco()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_tol numeric;
begin
  if not privado.config_booleano('monitor.ativo', true) then
    return new;
  end if;
  if old.disponivel and not new.disponivel then
    update public.promocoes set status = 'pausada', motivo_status = 'Produto indisponível'
     where produto_id = new.id and status in ('encontrada', 'aprovada', 'publicada');
  elsif new.preco_atual > old.preco_atual then
    v_tol := privado.config_numero('monitor.tolerancia_preco_pct', 5);
    update public.promocoes
       set status = 'pausada',
           motivo_status = 'Preço subiu de R$ ' || to_char(preco_promocional, 'FM999G999G990D00')
                           || ' para R$ ' || to_char(new.preco_atual, 'FM999G999G990D00')
     where produto_id = new.id and status in ('encontrada', 'aprovada', 'publicada')
       and new.preco_atual > preco_promocional * (1 + v_tol / 100);
  end if;
  return new;
end $$;
create trigger produtos_monitor_preco after update of preco_atual, disponivel on public.produtos
  for each row execute function privado.monitor_preco();

-- FILA: o worker pega os próximos itens com segurança (vários workers ao mesmo tempo não pegam o mesmo item)
-- Respeita: canal ativo, limite diário do canal, intervalo mínimo do canal, link validado e promoção aprovada.
create or replace function public.pegar_proximas_publicacoes(p_limite int default 5, p_bloqueio_min int default 10)
returns setof public.fila_publicacoes
language plpgsql security definer set search_path = '' as $$
declare v_inicio_dia timestamptz := (date_trunc('day', now() at time zone privado.fuso())) at time zone privado.fuso();
begin
  -- itens travados que já esgotaram as tentativas viram "falhou"
  update public.fila_publicacoes
     set status = 'falhou', bloqueado_ate = null, ultimo_erro = coalesce(ultimo_erro, 'Tempo esgotado no processamento')
   where status = 'processando' and bloqueado_ate < now() and tentativas >= max_tentativas;

  return query
  with uso as (
    select canal_id,
           count(*) filter (where publicado_em >= v_inicio_dia) as hoje,
           max(publicado_em) as ultima
      from public.historico_publicacoes
     where resultado = 'sucesso' and publicado_em >= now() - interval '2 days'
     group by canal_id
  ), candidatos as (
    select f.id,
           row_number() over (partition by f.canal_id order by f.prioridade, f.agendado_para) as ordem,
           c.intervalo_minimo_min, c.limite_diario, coalesce(u.hoje, 0) as hoje
      from public.fila_publicacoes f
      join public.canais c on c.id = f.canal_id and c.ativo and c.modo <> 'desativado'
      join public.promocoes p on p.id = f.promocao_id and p.status in ('aprovada', 'publicada')
      join public.links_afiliados l on l.id = f.link_afiliado_id and l.validado and l.ativo
      left join uso u on u.canal_id = f.canal_id
     where ((f.status = 'pendente' and f.agendado_para <= now())
            or (f.status = 'processando' and f.bloqueado_ate < now()))
       and f.tentativas < f.max_tentativas
       and (c.intervalo_minimo_min = 0 or u.ultima is null
            or u.ultima <= now() - make_interval(mins => c.intervalo_minimo_min))
  ), escolhidos as (
    select f.id
      from public.fila_publicacoes f
      join candidatos k on k.id = f.id
     where (k.limite_diario is null or k.hoje + k.ordem <= k.limite_diario)
       and (k.intervalo_minimo_min = 0 or k.ordem = 1)
     order by f.prioridade, f.agendado_para
     limit greatest(p_limite, 0)
       for update of f skip locked
  )
  update public.fila_publicacoes f
     set status = 'processando', tentativas = f.tentativas + 1,
         bloqueado_ate = now() + make_interval(mins => greatest(p_bloqueio_min, 1))
    from escolhidos e
   where f.id = e.id
  returning f.*;
end $$;
revoke execute on function public.pegar_proximas_publicacoes(int, int) from public, anon, authenticated;
grant execute on function public.pegar_proximas_publicacoes(int, int) to service_role;

-- VIEWS DO PAINEL (respeitam o RLS de quem consulta) --------------------
create view public.vw_promocoes_painel with (security_invoker = true) as
select pr.id, pr.status, pr.motivo_status, pr.preco_promocional, pr.preco_referencia, pr.desconto_pct,
       pr.comissao_pct, pr.origem, pr.demo, pr.criado_em, pr.aprovada_em, pr.publicada_em, pr.pausada_em,
       p.id as produto_id, p.titulo, p.imagem_url, p.nota, p.disponivel, p.preco_atual,
       m.codigo as marketplace, m.nome as marketplace_nome,
       c.id as categoria_id, c.nome as categoria,
       l.id as link_id, coalesce(l.url_curta, l.url) as link_url, coalesce(l.validado and l.ativo, false) as link_validado,
       coalesce(cl.total, 0)::int as cliques,
       coalesce(co.total, 0)::numeric(12,2) as comissao_total,
       hp.ultima_publicacao
  from public.promocoes pr
  join public.produtos p on p.id = pr.produto_id
  join public.marketplaces m on m.id = p.marketplace_id
  left join public.categorias c on c.id = p.categoria_id
  left join lateral (select id, url, url_curta, validado, ativo from public.links_afiliados
                      where produto_id = p.id and ativo order by validado desc, validado_em desc nulls last limit 1) l on true
  left join lateral (select count(*) as total from public.cliques where promocao_id = pr.id) cl on true
  left join lateral (select sum(valor_comissao) as total from public.comissoes
                      where promocao_id = pr.id and status <> 'cancelada') co on true
  left join lateral (select max(publicado_em) as ultima_publicacao from public.historico_publicacoes
                      where promocao_id = pr.id and resultado = 'sucesso' and not demo) hp on true;

create view public.vw_metricas_diarias with (security_invoker = true) as
with dias as (
  select generate_series((now() at time zone privado.fuso())::date - 89,
                         (now() at time zone privado.fuso())::date, interval '1 day')::date as dia
), prod as (
  select (criado_em at time zone privado.fuso())::date as dia, count(*) as n
    from public.produtos where criado_em >= now() - interval '91 days' group by 1
), promo as (
  select (criado_em at time zone privado.fuso())::date as dia, count(*) as n,
         count(*) filter (where status = 'pausada') as pausadas
    from public.promocoes where criado_em >= now() - interval '91 days' group by 1
), pub as (
  select (h.publicado_em at time zone privado.fuso())::date as dia,
         count(*) filter (where c.tipo = 'telegram') as telegram,
         count(*) filter (where c.tipo = 'instagram_story') as stories,
         count(*) filter (where c.tipo = 'instagram_feed') as feed,
         count(*) as total
    from public.historico_publicacoes h join public.canais c on c.id = h.canal_id
   where h.resultado = 'sucesso' and not h.demo and h.publicado_em >= now() - interval '91 days' group by 1
), cli as (
  select (ocorrido_em at time zone privado.fuso())::date as dia, count(*) as n
    from public.cliques where ocorrido_em >= now() - interval '91 days' group by 1
), com as (
  select data_venda as dia, sum(valor_comissao) as total
    from public.comissoes where status <> 'cancelada' and data_venda >= current_date - 91 group by 1
)
select d.dia,
       coalesce(prod.n, 0)::int as produtos_encontrados,
       coalesce(promo.n, 0)::int as promocoes_criadas,
       coalesce(promo.pausadas, 0)::int as promocoes_pausadas,
       coalesce(pub.total, 0)::int as publicacoes,
       coalesce(pub.telegram, 0)::int as publicacoes_telegram,
       coalesce(pub.stories, 0)::int as publicacoes_stories,
       coalesce(pub.feed, 0)::int as publicacoes_feed,
       coalesce(cli.n, 0)::int as cliques,
       coalesce(com.total, 0)::numeric(12,2) as comissoes
  from dias d
  left join prod on prod.dia = d.dia
  left join promo on promo.dia = d.dia
  left join pub on pub.dia = d.dia
  left join cli on cli.dia = d.dia
  left join com on com.dia = d.dia;

revoke all on public.vw_promocoes_painel, public.vw_metricas_diarias from anon;
grant select on public.vw_promocoes_painel, public.vw_metricas_diarias to authenticated;

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
