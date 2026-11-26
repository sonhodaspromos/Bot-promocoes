-- =============================================================================================
-- 018 · Publicação segura (auditoria corretiva P0)
--   1. fila_reservar_envio: reserva EXCLUSIVA do item antes do envio externo (devolve true/false).
--      A antiga fila_marcar_publicando (void) atualizava 0 linhas em silêncio e o bot enviava assim mesmo.
--   2. Cupons: linha 'enviando' gravada ANTES do envio (cupom_reservar_envio). Sem reserva, sem envio;
--      reserva sem resultado = resultado incerto = nunca reenviado automaticamente.
--   3. Atribuição do link: momento em que a atribuição foi comprovada (parâmetro + ID de afiliado
--      no destino final). Mudou a URL, a comprovação é apagada.
--   4. esquema_publicacao(): o bot confere versão, RPCs e configurações antes de publicar de verdade.
-- Não altera dados existentes além de colunas novas e de uma regra de check ampliada.
-- =============================================================================================

-- 1. reserva do item da fila ------------------------------------------------------------------
create or replace function public.fila_reservar_envio(p_id bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  update public.fila_publicacoes
     set publicando_desde = now()
   where id = p_id and status = 'processando' and publicando_desde is null
     and bloqueado_ate is not null and bloqueado_ate > now();
  get diagnostics n = row_count;
  return n = 1;
end $$;
revoke execute on function public.fila_reservar_envio(bigint) from public, anon, authenticated;
grant execute on function public.fila_reservar_envio(bigint) to service_role;
comment on function public.fila_reservar_envio(bigint) is
  'Reserva exclusiva antes do envio externo. false = item não está mais com este worker (trava vencida, já reservado ou status mudou): NÃO enviar.';

-- 2. cupons: reserva antes do envio --------------------------------------------------------------
alter table public.cupons_publicacoes drop constraint if exists cupons_publicacoes_resultado_check;
alter table public.cupons_publicacoes add constraint cupons_publicacoes_resultado_check
  check (resultado in ('sucesso', 'erro', 'enviando'));
comment on column public.cupons_publicacoes.resultado is
  'enviando = reserva gravada antes do envio externo. Uma reserva sem sucesso/erro correspondente é resultado incerto.';

create or replace function privado.cupom_publicacao_regras()
returns trigger language plpgsql set search_path = '' as $$
declare c record; v_canal_modo text; v_canal_ativo boolean;
begin
  select status, inicio_em, fim_em, demo into c from public.cupons where id = new.cupom_id;
  select modo, ativo into v_canal_modo, v_canal_ativo from public.canais where id = new.canal_id;
  new.dia := (now() at time zone privado.fuso())::date;
  new.demo := c.demo or v_canal_modo = 'demo';
  if new.resultado in ('sucesso', 'enviando') then
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

create or replace function public.cupom_reservar_envio(p_cupom bigint, p_canal smallint)
returns bigint language plpgsql security definer set search_path = '' as $$
declare v_dia date := (now() at time zone privado.fuso())::date; v_reservas int; v_fim int; v_sucesso int; v_id bigint;
begin
  -- serializa reservas do mesmo cupom no mesmo canal (duas rodadas ao mesmo tempo)
  perform pg_advisory_xact_lock(hashtextextended('cupom_envio:' || p_cupom || ':' || p_canal, 0));
  select count(*) filter (where resultado = 'enviando'),
         count(*) filter (where resultado in ('sucesso', 'erro')),
         count(*) filter (where resultado = 'sucesso')
    into v_reservas, v_fim, v_sucesso
    from public.cupons_publicacoes where cupom_id = p_cupom and canal_id = p_canal and dia = v_dia;
  if v_sucesso > 0 or v_reservas > v_fim then
    return null;  -- já publicado hoje, ou envio anterior sem resultado (incerto): não reenviar
  end if;
  insert into public.cupons_publicacoes (cupom_id, canal_id, resultado) values (p_cupom, p_canal, 'enviando')
  returning id into v_id;
  return v_id;
end $$;
revoke execute on function public.cupom_reservar_envio(bigint, smallint) from public, anon, authenticated;
grant execute on function public.cupom_reservar_envio(bigint, smallint) to service_role;

-- 3. comprovação de atribuição do link ------------------------------------------------------------
alter table public.links_afiliados add column if not exists atribuicao_verificada_em timestamptz;
comment on column public.links_afiliados.atribuicao_verificada_em is
  'Quando o bot seguiu o link até a loja e encontrou o parâmetro e o ID de afiliado no destino final. Nulo = sem comprovação.';

create or replace function privado.link_limpa_atribuicao()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.url is distinct from old.url or new.url_curta is distinct from old.url_curta or not new.validado then
    if new.atribuicao_verificada_em is not distinct from old.atribuicao_verificada_em then
      new.atribuicao_verificada_em := null;
    end if;
  end if;
  return new;
end $$;
drop trigger if exists links_limpa_atribuicao on public.links_afiliados;
create trigger links_limpa_atribuicao before update on public.links_afiliados
  for each row execute function privado.link_limpa_atribuicao();

-- o painel não marca atribuição: só o sistema (service_role)
create or replace function privado.link_atribuicao_so_sistema()
returns trigger language plpgsql set search_path = '' as $$
begin
  if coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role', '') = 'authenticated'
     and new.atribuicao_verificada_em is distinct from (case when tg_op = 'UPDATE' then old.atribuicao_verificada_em end)
     and new.atribuicao_verificada_em is not null then
    raise exception 'A comprovação de atribuição é feita automaticamente pelo sistema.' using errcode = 'P0016';
  end if;
  return new;
end $$;
drop trigger if exists links_atribuicao_so_sistema on public.links_afiliados;
create trigger links_atribuicao_so_sistema before insert or update on public.links_afiliados
  for each row execute function privado.link_atribuicao_so_sistema();

insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('links.validade_atribuicao_horas', '72', 'numero',
   'Em produção, só publica link cuja atribuição de afiliado foi comprovada nestas horas', 6, 336)
on conflict (chave) do nothing;

-- 4. requisitos de publicação real -----------------------------------------------------------------
create or replace function public.esquema_publicacao()
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'versao', 18,
    'rpcs', (select coalesce(jsonb_agg(p.proname order by p.proname), '[]'::jsonb)
               from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname in (
                'pegar_proximas_publicacoes', 'fila_reservar_envio', 'fila_devolver', 'fila_marcar_incerta',
                'cupom_reservar_envio', 'ativar_parada_emergencia', 'registrar_clique')),
    'configuracoes', (select coalesce(jsonb_agg(chave order by chave), '[]'::jsonb) from public.configuracoes
                       where chave in ('sistema.ambiente', 'sistema.parada_emergencia', 'links.validade_atribuicao_horas')),
    'colunas', (select coalesce(jsonb_agg(table_name || '.' || column_name order by 1), '[]'::jsonb)
                  from information_schema.columns where table_schema = 'public' and (table_name, column_name) in (
                    ('links_afiliados', 'atribuicao_verificada_em'), ('fila_publicacoes', 'publicando_desde'),
                    ('marketplaces', 'parametros_afiliado'), ('marketplaces', 'id_afiliado'), ('marketplaces', 'dominios_destino'))));
$$;
revoke execute on function public.esquema_publicacao() from public, anon, authenticated;
grant execute on function public.esquema_publicacao() to service_role;
