-- =====================================================================
-- 005 · Cliques, comissões, configurações automáticas, logs de execução e auditoria
-- =====================================================================

-- Máscara de segredos (tokens, chaves, senhas) para qualquer texto gravado em log/erro
create or replace function privado.mascarar_segredos(p text)
returns text language sql immutable set search_path = '' as $$
  select regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
    p,
    'bot[0-9]{6,}:[A-Za-z0-9_-]{20,}', 'bot***', 'g'),
    'sb_(secret|publishable)_[A-Za-z0-9_-]+', 'sb_\1_***', 'g'),
    'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', '***JWT***', 'g'),
    '(github_pat_|ghp_|gho_)[A-Za-z0-9_]{10,}', '\1***', 'g'),
    '(access_token|client_secret|secret|password|senha|api_key|apikey|token|key)=([^&[:space:]"'']+)', '\1=***', 'gi'),
    '"(access_token|client_secret|secret|password|senha|api_key|apikey|token|chave|valor_secreto)"[[:space:]]*:[[:space:]]*"[^"]*"', '"\1": "***"', 'gi'),
    '(Bearer[[:space:]]+)[A-Za-z0-9._~+/=-]{8,}', '\1***', 'gi')
$$;

-- Trigger genérico: mascara as colunas de texto/jsonb informadas como argumento
create or replace function privado.mascarar_colunas()
returns trigger language plpgsql set search_path = '' as $$
declare col text; j jsonb := to_jsonb(new); v jsonb;
begin
  foreach col in array tg_argv loop
    v := j -> col;
    if v is null or jsonb_typeof(v) = 'null' then continue; end if;
    if jsonb_typeof(v) = 'string' then
      j := jsonb_set(j, array[col], to_jsonb(privado.mascarar_segredos(v #>> '{}')));
    else
      j := jsonb_set(j, array[col], privado.mascarar_segredos(v::text)::jsonb);
    end if;
  end loop;
  new := jsonb_populate_record(new, j);
  return new;
end $$;

create trigger historico_pub_mascarar before insert on public.historico_publicacoes
  for each row execute function privado.mascarar_colunas('erro');
create trigger fila_mascarar before insert or update of ultimo_erro on public.fila_publicacoes
  for each row execute function privado.mascarar_colunas('ultimo_erro');

-- 13. CLIQUES -----------------------------------------------------------
create table public.cliques (
  id               bigint generated always as identity primary key,
  link_afiliado_id bigint not null references public.links_afiliados(id) on delete cascade,
  promocao_id      bigint references public.promocoes(id) on delete set null,
  canal_id         smallint references public.canais(id) on delete set null,
  publicacao_id    bigint references public.historico_publicacoes(id) on delete set null,
  ip_hash          char(64) check (ip_hash ~ '^[0-9a-f]{64}$'),
  user_agent       text check (char_length(user_agent) <= 400),
  referer          text check (char_length(referer) <= 500),
  ocorrido_em      timestamptz not null default now()
);
comment on column public.cliques.ip_hash is 'SHA-256 do IP com sal (nunca o IP puro), usado só para evitar contar cliques repetidos.';
create index cliques_promocao_idx on public.cliques (promocao_id, ocorrido_em desc);
create index cliques_link_idx on public.cliques (link_afiliado_id, ocorrido_em desc);
create index cliques_canal_idx on public.cliques (canal_id, ocorrido_em desc);
create index cliques_publicacao_idx on public.cliques (publicacao_id);
create index cliques_data_brin on public.cliques using brin (ocorrido_em);
create trigger cliques_imutavel before update on public.cliques
  for each row execute function privado.bloquear_alteracao();

-- Registra um clique e devolve o destino. Usada pela Edge Function "r" (chave secreta).
-- Só devolve URL se o link estiver validado e ativo.
create or replace function public.registrar_clique(
  p_link bigint, p_canal text default null, p_ip_hash text default null,
  p_user_agent text default null, p_referer text default null)
returns table (url_destino text, oferta_ativa boolean)
language plpgsql security definer set search_path = '' as $$
declare l record; v_promo bigint; v_status text; v_canal smallint;
begin
  select id, produto_id, promocao_id, coalesce(url_curta, url) as destino, validado and ativo as valido
    into l from public.links_afiliados where id = p_link;
  if not found then return; end if;

  select id, status into v_promo, v_status from public.promocoes
   where id = l.promocao_id or (l.promocao_id is null and produto_id = l.produto_id)
   order by (id = l.promocao_id) desc, criado_em desc limit 1;
  select id into v_canal from public.canais where codigo = p_canal;

  -- não conta o mesmo visitante no mesmo link mais de uma vez em 30 segundos
  if p_ip_hash is null or not exists (
       select 1 from public.cliques
        where link_afiliado_id = p_link and ip_hash = p_ip_hash and ocorrido_em > now() - interval '30 seconds') then
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ip_hash, user_agent, referer)
    values (p_link, v_promo, v_canal, p_ip_hash, left(p_user_agent, 400), left(p_referer, 500));
  end if;

  url_destino := case when l.valido then l.destino end;
  oferta_ativa := l.valido and coalesce(v_status in ('aprovada', 'publicada'), false);
  return next;
end $$;
revoke execute on function public.registrar_clique(bigint, text, text, text, text) from public, anon, authenticated;
grant execute on function public.registrar_clique(bigint, text, text, text, text) to service_role;

-- 14. COMISSÕES ---------------------------------------------------------
create table public.comissoes (
  id                 bigint generated always as identity primary key,
  marketplace_id     smallint not null references public.marketplaces(id) on delete restrict,
  produto_id         bigint references public.produtos(id) on delete set null,
  promocao_id        bigint references public.promocoes(id) on delete set null,
  referencia_externa text check (char_length(referencia_externa) <= 200),
  descricao          text check (char_length(descricao) <= 300),
  valor_venda        numeric(12,2) check (valor_venda >= 0),
  valor_comissao     numeric(12,2) not null check (valor_comissao >= 0),
  status             text not null default 'pendente' check (status in ('pendente', 'confirmada', 'cancelada', 'paga')),
  origem             text not null default 'manual' check (origem in ('api', 'manual', 'demo')),
  data_venda         date not null,
  data_pagamento     date,
  criado_em          timestamptz not null default now(),
  atualizado_em      timestamptz not null default now(),
  constraint comissoes_pagamento_apos_venda check (data_pagamento is null or data_pagamento >= data_venda),
  constraint comissoes_paga_tem_data check (status <> 'paga' or data_pagamento is not null),
  constraint comissoes_api_tem_referencia check (origem <> 'api' or referencia_externa is not null),
  constraint comissoes_valor_coerente check (valor_venda is null or valor_comissao <= valor_venda)
);
-- Impede comissão duplicada vinda da API (mesma referência no mesmo marketplace)
create unique index comissoes_referencia_uq on public.comissoes (marketplace_id, referencia_externa)
  where referencia_externa is not null;
create index comissoes_data_idx on public.comissoes (data_venda desc);
create index comissoes_produto_idx on public.comissoes (produto_id);
create index comissoes_promocao_idx on public.comissoes (promocao_id);
create index comissoes_status_idx on public.comissoes (status, data_venda desc);
create trigger comissoes_atualizado_em before update on public.comissoes
  for each row execute function privado.definir_atualizado_em();

-- 15. CONFIGURAÇÕES AUTOMÁTICAS ----------------------------------------
create table public.configuracoes (
  chave          text primary key check (chave ~ '^[a-z0-9_]+(\.[a-z0-9_]+)*$' and char_length(chave) <= 80),
  valor          jsonb not null,
  tipo           text not null check (tipo in ('numero', 'texto', 'booleano', 'lista', 'objeto')),
  descricao      text check (char_length(descricao) <= 300),
  minimo         numeric,
  maximo         numeric,
  atualizado_por uuid references auth.users(id) on delete set null,
  criado_em      timestamptz not null default now(),
  atualizado_em  timestamptz not null default now(),
  constraint configuracoes_tipo_coerente check (
    (tipo = 'numero'   and jsonb_typeof(valor) = 'number')  or
    (tipo = 'texto'    and jsonb_typeof(valor) = 'string')  or
    (tipo = 'booleano' and jsonb_typeof(valor) = 'boolean') or
    (tipo = 'lista'    and jsonb_typeof(valor) = 'array')   or
    (tipo = 'objeto'   and jsonb_typeof(valor) = 'object')),
  constraint configuracoes_dentro_limites check (
    case when tipo = 'numero' and jsonb_typeof(valor) = 'number'
         then (minimo is null or (valor #>> '{}')::numeric >= minimo)
          and (maximo is null or (valor #>> '{}')::numeric <= maximo)
         else true end),
  constraint configuracoes_limites_ordem check (minimo is null or maximo is null or minimo <= maximo)
);
create index configuracoes_atualizado_por_idx on public.configuracoes (atualizado_por);

create or replace function privado.configuracao_quem_alterou()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.atualizado_por := coalesce((select auth.uid()), new.atualizado_por);
  new.atualizado_em := now();
  -- tipo e limites só mudam por migration (o painel altera apenas o valor)
  if tg_op = 'UPDATE' and (select auth.uid()) is not null
     and (new.tipo is distinct from old.tipo or new.minimo is distinct from old.minimo or new.maximo is distinct from old.maximo) then
    raise exception 'Só o valor da configuração pode ser alterado pelo painel.' using errcode = 'P0003';
  end if;
  return new;
end $$;
create trigger configuracoes_quem_alterou before insert or update on public.configuracoes
  for each row execute function privado.configuracao_quem_alterou();

-- Leitura prática de uma configuração numérica (com valor padrão)
create or replace function privado.config_numero(p_chave text, p_padrao numeric)
returns numeric language sql stable security definer set search_path = '' as $$
  select coalesce((select (valor #>> '{}')::numeric from public.configuracoes
                    where chave = p_chave and jsonb_typeof(valor) = 'number'), p_padrao)
$$;

-- 16. LOGS DE EXECUÇÃO --------------------------------------------------
create table public.execucoes (
  id            uuid primary key default gen_random_uuid(),
  origem        text not null default 'agendada' check (origem in ('agendada', 'manual', 'teste')),
  modo          text not null default 'real' check (modo in ('real', 'demo')),
  status        text not null default 'rodando' check (status in ('rodando', 'sucesso', 'parcial', 'erro')),
  versao        text check (char_length(versao) <= 40),
  iniciada_em   timestamptz not null default now(),
  finalizada_em timestamptz,
  resumo        jsonb not null default '{}'::jsonb check (jsonb_typeof(resumo) = 'object'),
  constraint execucoes_fim_apos_inicio check (finalizada_em is null or finalizada_em >= iniciada_em),
  constraint execucoes_finalizada check ((status = 'rodando') = (finalizada_em is null))
);
create index execucoes_inicio_idx on public.execucoes (iniciada_em desc);
create index execucoes_status_idx on public.execucoes (status) where status <> 'sucesso';

create table public.logs (
  id          bigint generated always as identity primary key,
  execucao_id uuid references public.execucoes(id) on delete cascade,
  nivel       text not null check (nivel in ('DEBUG', 'INFO', 'AVISO', 'ERRO', 'CRITICO')),
  modulo      text not null check (modulo ~ '^[a-z0-9_.]{2,60}$'),
  mensagem    text not null check (char_length(mensagem) between 1 and 4000),
  contexto    jsonb not null default '{}'::jsonb check (jsonb_typeof(contexto) = 'object'),
  criado_em   timestamptz not null default now()
);
create index logs_execucao_idx on public.logs (execucao_id, id);
create index logs_data_brin on public.logs using brin (criado_em);
create index logs_erros_idx on public.logs (criado_em desc) where nivel in ('ERRO', 'CRITICO');
create index logs_modulo_idx on public.logs (modulo, criado_em desc);
create trigger logs_mascarar before insert on public.logs
  for each row execute function privado.mascarar_colunas('mensagem', 'contexto');
create trigger logs_imutavel before update on public.logs
  for each row execute function privado.bloquear_alteracao();

-- AUDITORIA (alterações feitas por pessoas no painel) ------------------
create table public.auditoria (
  id           bigint generated always as identity primary key,
  tabela       text not null,
  registro_id  text,
  operacao     text not null check (operacao in ('INSERT', 'UPDATE', 'DELETE')),
  usuario_id   uuid references auth.users(id) on delete set null,
  dados_antes  jsonb,
  dados_depois jsonb,
  criado_em    timestamptz not null default now()
);
create index auditoria_tabela_idx on public.auditoria (tabela, registro_id);
create index auditoria_usuario_idx on public.auditoria (usuario_id);
create index auditoria_data_brin on public.auditoria using brin (criado_em);
create trigger auditoria_imutavel before update on public.auditoria
  for each row execute function privado.bloquear_alteracao();

create or replace function privado.auditar()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_user uuid := (select auth.uid()); v_antes jsonb; v_depois jsonb;
begin
  if v_user is null then return coalesce(new, old); end if;  -- só ações de pessoas (o bot tem os próprios logs)
  v_antes := case when tg_op <> 'INSERT' then to_jsonb(old) end;
  v_depois := case when tg_op <> 'DELETE' then to_jsonb(new) end;
  insert into public.auditoria (tabela, registro_id, operacao, usuario_id, dados_antes, dados_depois)
  values (tg_table_name,
          coalesce(v_depois ->> 'id', v_antes ->> 'id', v_depois ->> 'chave', v_antes ->> 'chave',
                   v_depois ->> 'user_id', v_antes ->> 'user_id'),
          tg_op, v_user,
          privado.mascarar_segredos(v_antes::text)::jsonb, privado.mascarar_segredos(v_depois::text)::jsonb);
  return coalesce(new, old);
end $$;

do $$
declare t text;
begin
  foreach t in array array['administradores', 'marketplaces', 'categorias', 'promocoes', 'links_afiliados',
                           'canais', 'configuracoes', 'comissoes'] loop
    execute format('create trigger %I after insert or update or delete on public.%I
                    for each row execute function privado.auditar()', t || '_auditoria', t);
  end loop;
end $$;

revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;
