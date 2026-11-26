-- Reversão da migration 020 (20261125190000_expansao_modular): volta ao estado da 019.
-- Rode ANTES a reversão da 021. Apaga os dados dos módulos novos (inscritos do WhatsApp, alertas,
-- fila de tarefas, regras de canais por categoria). Marketplaces amazon/aliexpress/magalu só são
-- removidos se não houver produtos deles.
begin;
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

create or replace function public.registrar_clique(
  p_link bigint, p_canal text default null, p_ip_hash text default null,
  p_user_agent text default null, p_referer text default null)
returns table (url_destino text, oferta_ativa boolean)
language plpgsql security definer set search_path = '' as $$
declare l record; v_promo bigint; v_status text; v_canal smallint; v_motivo text;
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

  if p_ip_hash is null then
    v_motivo := 'sem_identificacao';
  elsif (select count(*) from public.cliques where ip_hash = p_ip_hash and ocorrido_em > now() - interval '10 minutes') >= v_max_visitante then
    v_motivo := 'excesso_visitante';
  elsif (select count(*) from public.cliques where link_afiliado_id = p_link and ocorrido_em > now() - interval '1 minute') >= v_max_link then
    v_motivo := 'pico_link';
  elsif exists (select 1 from public.cliques where link_afiliado_id = p_link and ip_hash = p_ip_hash
                 and ocorrido_em > now() - make_interval(hours => v_janela)) then
    v_motivo := 'repetido';
  end if;

  if v_motivo is null then
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ip_hash, user_agent, referer)
    values (p_link, v_promo, v_canal, p_ip_hash, left(p_user_agent, 400), left(p_referer, 500));
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

drop index if exists public.cliques_publicacao_data_idx;
do $$ declare f text; begin
  foreach f in array array['whatsapp_destinatarios(integer, integer, bigint)', 'whatsapp_reservar(bigint, bigint)',
    'whatsapp_gasto_hoje()', 'whatsapp_inscrever(text, text, text, integer[])', 'alertas_avisos_hoje()',
    'ofertas_publicadas_desde(timestamptz)', 'ofertas_vitrine(integer)', 'cupons_vitrine()', 'ultimo_agendado(smallint)',
    'whatsapp_descadastrar(text)', 'tarefa_enfileirar(text, jsonb, text, timestamptz)',
    'tarefas_pegar(text[], integer, integer, text)', 'tarefa_concluir(bigint, boolean, text, jsonb, text)'] loop
    execute format('drop function if exists public.%s', f);
  end loop;
end $$;
drop function if exists privado.limpar_expansao();
drop table if exists public.whatsapp_envios, public.whatsapp_eventos, public.whatsapp_inscritos,
                     public.alertas_avisos, public.alertas_preco, public.tarefas, public.canais_categorias;
delete from public.marketplaces m where m.codigo in ('amazon', 'aliexpress', 'magalu')
   and not exists (select 1 from public.produtos p where p.marketplace_id = m.id);
comment on column public.marketplaces.dominios_afiliado is 'Domínios aceitos em links de afiliado (ex.: s.shopee.com.br). Links fora desta lista nunca são validados.';
delete from public.configuracoes where chave like 'recursos.%' or chave like 'whatsapp.%' or chave like 'alertas.%'
   or chave in ('vitrine.url_base', 'agendamento.min_intervalo_canal_min');
commit;
