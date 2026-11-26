-- Reversão da migration 021 (20261125200000_auditoria_expansao): volta ao estado da 020.
-- Uso (homologação primeiro!): psql -v ON_ERROR_STOP=1 -f supabase/rollback/20261125200000_auditoria_expansao.down.sql
-- Mantém de propósito: a correção retroativa de cliques.publicacao_id (dado correto) e os canais
-- vitrine/whatsapp/alerta que já tenham cliques apontando para eles (apagar perderia a atribuição).
-- ATENÇÃO: apaga o rollup (cliques_diarios). Dias além da retenção dos brutos perdem a contagem agregada.
begin;
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

drop function if exists public.ofertas_vitrine(integer);
create function public.ofertas_vitrine(p_dias int default 7)
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
revoke execute on function public.ofertas_vitrine(integer) from public, anon, authenticated;
grant execute on function public.ofertas_vitrine(integer) to service_role;

delete from public.experimentos where tipo = 'horario';
alter table public.experimentos drop constraint if exists experimentos_tipo_check;
alter table public.experimentos add constraint experimentos_tipo_check check (tipo in ('legenda', 'arte'));

drop function if exists privado.manter_rollup();
drop function if exists public.fila_saude();
drop function if exists public.tarefa_renovar(bigint, text, integer);
drop function if exists public.whatsapp_processar_mensagem(text, text, text, timestamptz, text);
drop function if exists public.whatsapp_reservar_envio(bigint, bigint, numeric, numeric, integer);
drop function if exists public.orcamento_situacao(text);
drop function if exists public.orcamento_estornar(text, numeric);
drop function if exists public.orcamento_reservar(text, numeric, numeric);
drop function if exists public.verificar_rollup(date);
drop function if exists public.agregar_cliques_periodo(date, date);
drop function if exists public.agregar_cliques(date);
drop view if exists public.vw_financeiro_mensal;
drop view if exists public.vw_cliques_por_dia;
drop function if exists privado.dia_local(timestamptz);
drop table if exists public.custos_servicos, public.whatsapp_webhook_mensagens, public.orcamento_consumo,
                     public.cliques_agregacao, public.cliques_diarios;
drop index if exists public.historico_pub_link_canal_idx;
delete from public.canais c where c.codigo in ('vitrine', 'whatsapp', 'alerta')
   and not exists (select 1 from public.cliques k where k.canal_id = c.id);
comment on column public.canais.ativo is null;
comment on column public.experimentos.tipo is null;
delete from public.configuracoes where chave in ('cliques.rollup_retencao_dias', 'cliques.rollup_reprocessar_dias',
  'financeiro.alerta_pct', 'fila.alerta_atraso_min', 'fila.alerta_pendentes', 'vitrine.max_horas_sem_conferir',
  'inteligencia.min_comissoes_cancelamento');
commit;
