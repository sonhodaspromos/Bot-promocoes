-- =====================================================================
-- 015 · Correções da auditoria final
--   A1  link sem atribuição de afiliado: padrão por marketplace (P0024)
--   A2  publicação com resultado incerto não é repetida (evita post duplicado)
--   A3  devolver item à fila sem gastar tentativa (token vencido, limite da API)
--   A4  rodadas interrompidas (job morto) são encerradas na rodada seguinte
--   A5  oferta publicada também perde a análise quando preço/estoque/link mudam
--   A7  limite diário dos stories cabe na cota da API do Instagram (50 / 24 h)
-- =====================================================================

-- A1 ------------------------------------------------------------------
create or replace function privado.regex_valida(p text)
returns boolean language plpgsql immutable set search_path = '' as $$
begin
  if p is null then return true; end if;
  perform '' ~ p;
  return char_length(p) between 5 and 300 and left(p, 9) = '^https://';
exception when others then
  return false;
end $$;

alter table public.marketplaces
  add column padrao_afiliado text,
  add constraint marketplaces_padrao_valido check (privado.regex_valida(padrao_afiliado));
comment on column public.marketplaces.padrao_afiliado is
  'Expressão regular que o link precisa seguir para ter atribuição de afiliado (além do domínio). Ex.: ^https://mercadolivre\.com/sec/';

update public.marketplaces set padrao_afiliado = '^https://(s\.shopee\.com\.br|shope\.ee)/[A-Za-z0-9_-]+' where codigo = 'shopee';
update public.marketplaces set padrao_afiliado = '^https://mercadolivre\.com/sec/[A-Za-z0-9]+' where codigo = 'mercado_livre';
update public.marketplaces set padrao_afiliado = '^https://demo\.invalid/' where codigo = 'demo';

create or replace function privado.link_regras()
returns trigger language plpgsql set search_path = '' as $$
declare v_dominios text[]; v_marketplace text; v_padrao text; v_produto_promo bigint;
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
    select m.dominios_afiliado, m.nome, m.padrao_afiliado into v_dominios, v_marketplace, v_padrao
    from public.produtos p join public.marketplaces m on m.id = p.marketplace_id
    where p.id = new.produto_id;
    if not privado.dominio_permitido(new.url, v_dominios)
       or (new.url_curta is not null and not privado.dominio_permitido(new.url_curta, v_dominios)) then
      raise exception 'Link recusado: o domínio "%" não é um domínio de afiliado cadastrado para %.',
        privado.host_da_url(coalesce(case when privado.dominio_permitido(new.url, v_dominios) then new.url_curta end, new.url)),
        v_marketplace using errcode = 'P0011';
    end if;
    if v_padrao is not null and coalesce(new.url_curta, new.url) !~ v_padrao then
      raise exception 'Link recusado: não tem a atribuição de afiliado de % (formato esperado: %).', v_marketplace, v_padrao
        using errcode = 'P0024';
    end if;
    new.validado_em := coalesce(new.validado_em, now());
    new.motivo_invalido := null;
  else
    new.validado_em := null;
  end if;
  return new;
end $$;

-- A2 / A3 ---------------------------------------------------------------
alter table public.fila_publicacoes add column publicando_desde timestamptz;
comment on column public.fila_publicacoes.publicando_desde is
  'Marcado logo antes de enviar ao canal. Se o job morrer antes de registrar o resultado, o item vira "falhou (resultado incerto)" em vez de ser publicado de novo.';

create or replace function public.fila_marcar_publicando(p_id bigint)
returns void language sql security definer set search_path = '' as $$
  update public.fila_publicacoes set publicando_desde = now() where id = p_id and status = 'processando';
$$;

create or replace function public.fila_devolver(p_id bigint, p_minutos int, p_motivo text)
returns void language sql security definer set search_path = '' as $$
  update public.fila_publicacoes
     set status = 'pendente', tentativas = greatest(tentativas - 1, 0), bloqueado_ate = null, publicando_desde = null,
         agendado_para = now() + make_interval(mins => least(greatest(p_minutos, 1), 1440)),
         ultimo_erro = left(p_motivo, 1000)
   where id = p_id and status = 'processando';
$$;

create or replace function public.fila_marcar_incerta(p_id bigint, p_motivo text)
returns void language sql security definer set search_path = '' as $$
  update public.fila_publicacoes
     set status = 'falhou', bloqueado_ate = null,
         ultimo_erro = left('Resultado incerto: ' || p_motivo || ' — confira o canal antes de publicar de novo.', 1000)
   where id = p_id and status in ('pendente', 'processando');
$$;

revoke execute on function public.fila_marcar_publicando(bigint) from public, anon, authenticated;
revoke execute on function public.fila_devolver(bigint, int, text) from public, anon, authenticated;
revoke execute on function public.fila_marcar_incerta(bigint, text) from public, anon, authenticated;
grant execute on function public.fila_marcar_publicando(bigint) to service_role;
grant execute on function public.fila_devolver(bigint, int, text) to service_role;
grant execute on function public.fila_marcar_incerta(bigint, text) to service_role;

-- o histórico (sucesso ou erro registrado) encerra a marca de "publicando"
create or replace function privado.historico_limpa_publicando()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if new.fila_id is not null then
    update public.fila_publicacoes set publicando_desde = null where id = new.fila_id and publicando_desde is not null;
  end if;
  return new;
end $$;
create trigger historico_pub_limpa_publicando after insert on public.historico_publicacoes
  for each row execute function privado.historico_limpa_publicando();

create or replace function public.pegar_proximas_publicacoes(p_limite int default 5, p_bloqueio_min int default 10)
returns setof public.fila_publicacoes
language plpgsql security definer set search_path = '' as $$
declare v_inicio_dia timestamptz := (date_trunc('day', now() at time zone privado.fuso())) at time zone privado.fuso();
begin
  -- job morreu DEPOIS de enviar ao canal e antes de registrar: não repete (evita post duplicado)
  update public.fila_publicacoes
     set status = 'falhou', bloqueado_ate = null,
         ultimo_erro = 'Resultado incerto: o bot parou durante o envio — confira o canal antes de publicar de novo.'
   where status = 'processando' and bloqueado_ate < now() and publicando_desde is not null;

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
            or (f.status = 'processando' and f.bloqueado_ate < now() and f.publicando_desde is null))
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
     set status = 'processando', tentativas = f.tentativas + 1, publicando_desde = null,
         bloqueado_ate = now() + make_interval(mins => greatest(p_bloqueio_min, 1))
    from escolhidos e
   where f.id = e.id
  returning f.*;
end $$;
revoke execute on function public.pegar_proximas_publicacoes(int, int) from public, anon, authenticated;
grant execute on function public.pegar_proximas_publicacoes(int, int) to service_role;

-- A4 ------------------------------------------------------------------
create or replace function public.encerrar_execucoes_orfas(p_minutos int default 30)
returns int language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  update public.execucoes
     set status = 'erro', finalizada_em = now(),
         resumo = resumo || jsonb_build_object('interrompida', true,
                  'motivo', 'A rodada não terminou (job encerrado por tempo limite ou queda do servidor).')
   where status = 'rodando' and iniciada_em < now() - make_interval(mins => greatest(p_minutos, 10));
  get diagnostics n = row_count;
  return n;
end $$;
revoke execute on function public.encerrar_execucoes_orfas(int) from public, anon, authenticated;
grant execute on function public.encerrar_execucoes_orfas(int) to service_role;

-- A5 ------------------------------------------------------------------
create or replace function privado.invalidar_analise()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.id and status in ('encontrada', 'aprovada', 'publicada', 'pausada') and analisada_em is not null;
  return new;
end $$;

create or replace function privado.invalidar_analise_link()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.produto_id and status in ('encontrada', 'aprovada', 'publicada', 'pausada') and analisada_em is not null;
  return new;
end $$;

drop index if exists public.promocoes_analisar_idx;
create index promocoes_analisar_idx on public.promocoes (criado_em)
  where analisada_em is null and status in ('encontrada', 'aprovada', 'publicada');

-- A7 ------------------------------------------------------------------
update public.canais set limite_diario = 45 where codigo = 'instagram_story' and limite_diario > 45;
