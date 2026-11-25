-- =====================================================================
-- 017 · Auditoria complementar (homologada em banco local; NÃO aplicada em produção sem autorização)
--   C1  primeiro administrador: fim do "primeiro cadastro vira dono"; cadastro só por convite
--   C2  autenticação multifator (aal2) exigida para qualquer acesso do painel
--   C3  importação de comissões: índice parcial impedia o ON CONFLICT (a importação sempre falhava)
--   A1  editor não altera domínios/identificador de afiliado nem chaves do sistema
--   A2  parada de emergência (botão) + ambiente declarado no banco
--   A3  cliques: visitante único por 24 h, limites por visitante/link, cliques suspeitos fora das métricas
--   A4  atribuição de afiliado: domínios de destino, parâmetros e id de afiliado por loja
--   A5  preços: variação entre opções e "preço de" derivado gravados no produto
--   A6  LGPD: retenção de cliques; registro de importações/reconciliações de comissões
-- =====================================================================

-- ---------------------------------------------------------------- utilitário
create or replace function privado.config_bool(p_chave text, p_padrao boolean)
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select (valor #>> '{}')::boolean from public.configuracoes where chave = p_chave and tipo = 'booleano'), p_padrao)
$$;
create or replace function privado.config_texto(p_chave text)
returns text language sql stable security definer set search_path = '' as $$
  select valor #>> '{}' from public.configuracoes where chave = p_chave
$$;

insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('sistema.ambiente',               '"producao"', 'texto',    'Ambiente deste banco (demonstracao, homologacao ou producao). O bot só roda com AMBIENTE igual.', null, null),
  ('sistema.parada_emergencia',      'false',      'booleano', 'Parada de emergência: nenhuma publicação real enquanto estiver ligada', null, null),
  ('sistema.parada_motivo',          '""',         'texto',    'Motivo da última parada de emergência', null, null),
  ('auth.exigir_mfa',                'true',       'booleano', 'Exigir autenticação em dois fatores (app autenticador) para usar o painel', null, null),
  ('auth.email_dono',                '""',         'texto',    'E-mail autorizado a criar a conta de dono (definido pelo SQL Editor)', null, null),
  ('publicacao.validade_preco_horas','24',         'numero',   'Ofertas sem consulta automática de preço só são publicadas se o preço foi conferido nestas horas', 1, 72),
  ('publicacao.alerta_fila_atrasada_min','120',  'numero',   'Alerta crítico quando há oferta na fila atrasada há mais destes minutos', 30, 1440),
  ('inteligencia.exigir_desconto_comprovado', 'false', 'booleano', 'Só divulgar desconto confirmado pelo histórico de preços ou por cupom (sempre ligado em produção)', null, null),
  ('inteligencia.variacao_preco_max_pct', '5',     'numero',   'Diferença máxima (%) entre o menor e o maior preço das variações do produto', 0, 50),
  ('cliques.janela_unico_horas',     '24',         'numero',   'O mesmo visitante conta 1 clique por link nesta janela', 1, 168),
  ('cliques.max_por_visitante_10min','20',         'numero',   'Acima disso, os cliques do visitante viram "suspeitos" (fora das métricas)', 5, 500),
  ('cliques.max_por_link_minuto',    '120',        'numero',   'Acima disso, os cliques do link no minuto viram "suspeitos"', 10, 5000),
  ('cliques.retencao_dias',          '365',        'numero',   'Por quantos dias os cliques (com IP anonimizado) são guardados (LGPD)', 30, 730)
on conflict (chave) do nothing;

-- ---------------------------------------------------------------- C1 primeiro administrador
drop trigger if exists ao_criar_usuario_admin on auth.users;
drop function if exists privado.registrar_primeiro_dono();

create table public.convites (
  email        text primary key check (email = lower(btrim(email)) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  papel        text not null check (papel in ('editor', 'leitor')),
  convidado_por uuid references auth.users(id) on delete set null default auth.uid(),
  expira_em    timestamptz not null default now() + interval '7 days',
  usado_em     timestamptz,
  criado_em    timestamptz not null default now()
);
comment on table public.convites is 'Só quem tem convite válido (ou o e-mail do dono definido no SQL Editor) consegue criar conta.';
create index convites_convidado_por_idx on public.convites (convidado_por);
alter table public.convites enable row level security;
revoke all on public.convites from anon;
create policy "convites: dono lê" on public.convites for select to authenticated using ((select privado.eh_dono()));
create policy "convites: dono cria" on public.convites for insert to authenticated with check ((select privado.eh_dono()));
create policy "convites: dono remove" on public.convites for delete to authenticated using ((select privado.eh_dono()));

-- dono definido SÓ pelo SQL Editor (postgres) ou pelo backend (service_role)
create or replace function public.definir_email_dono(p_email text)
returns text language plpgsql security definer set search_path = '' as $$
begin
  if exists (select 1 from public.administradores where papel = 'dono') then
    raise exception 'Já existe um dono. Use transferir_posse.' using errcode = 'P0001';
  end if;
  update public.configuracoes set valor = to_jsonb(lower(btrim(p_email))) where chave = 'auth.email_dono';
  return 'O próximo cadastro com ' || lower(btrim(p_email)) || ' vira dono. Todos os outros cadastros estão fechados.';
end $$;
revoke execute on function public.definir_email_dono(text) from public, anon, authenticated;
grant execute on function public.definir_email_dono(text) to service_role;

create or replace function privado.autorizar_cadastro()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_email text := lower(btrim(coalesce(new.email, '')));
        v_dono text := privado.config_texto('auth.email_dono');
begin
  -- testes automatizados (só o superusuário do banco, nunca a API pública) criam usuários direto
  if current_user = 'postgres' and coalesce(current_setting('testes.cadastro_direto', true), '') = 'sim' then
    return new;
  end if;
  if v_email = '' then
    raise exception 'Cadastro fechado: só por convite.' using errcode = 'P0028';
  end if;
  if not exists (select 1 from public.administradores where papel = 'dono') and v_dono is not null and v_dono <> '' and v_email = v_dono then
    return new;
  end if;
  if exists (select 1 from public.convites where email = v_email and usado_em is null and expira_em > now()) then
    return new;
  end if;
  raise exception 'Cadastro fechado: só por convite.' using errcode = 'P0028';
end $$;
create trigger autorizar_cadastro before insert on auth.users
  for each row execute function privado.autorizar_cadastro();

create or replace function privado.registrar_administrador()
returns trigger language plpgsql security definer set search_path = '' as $$
declare v_email text := lower(btrim(new.email)); c record;
begin
  if current_user = 'postgres' and coalesce(current_setting('testes.cadastro_direto', true), '') = 'sim' then
    return new;
  end if;
  if not exists (select 1 from public.administradores where papel = 'dono') and v_email = privado.config_texto('auth.email_dono') then
    insert into public.administradores (user_id, email, papel) values (new.id, new.email, 'dono');
    update public.configuracoes set valor = '""'::jsonb where chave = 'auth.email_dono';
    return new;
  end if;
  select * into c from public.convites where email = v_email and usado_em is null and expira_em > now();
  if found then
    insert into public.administradores (user_id, email, papel) values (new.id, new.email, c.papel);
    update public.convites set usado_em = now() where email = v_email;
  end if;
  return new;
end $$;
create trigger registrar_administrador after insert on auth.users
  for each row execute function privado.registrar_administrador();

-- ---------------------------------------------------------------- C2 MFA (aal2)
create or replace function privado.sessao_segura()
returns boolean language sql stable security definer set search_path = '' as $$
  select not privado.config_bool('auth.exigir_mfa', true) or coalesce((select auth.jwt()) ->> 'aal', '') = 'aal2'
$$;
create or replace function privado.papel_atual()
returns text language sql stable security definer set search_path = '' as $$
  select papel from public.administradores
   where user_id = (select auth.uid()) and ativo and privado.sessao_segura()
$$;
-- o painel precisa saber o papel ANTES do 2º fator (para mostrar a tela de MFA)
create or replace function public.minha_sessao()
returns table (papel text, aal text, mfa_exigido boolean)
language sql stable security definer set search_path = '' as $$
  select (select a.papel from public.administradores a where a.user_id = (select auth.uid()) and a.ativo),
         coalesce((select auth.jwt()) ->> 'aal', 'aal1'), privado.config_bool('auth.exigir_mfa', true)
$$;
revoke execute on function public.minha_sessao() from public, anon;
grant execute on function public.minha_sessao() to authenticated;

-- ---------------------------------------------------------------- C3 comissões
drop index if exists public.comissoes_referencia_uq;
alter table public.comissoes add constraint comissoes_referencia_unica unique (marketplace_id, referencia_externa);
comment on constraint comissoes_referencia_unica on public.comissoes is
  'Referência única por loja (NULL permitido para lançamentos manuais). Índice completo: o ON CONFLICT do backend precisa dele.';

create table public.comissoes_importacoes (
  id             bigint generated always as identity primary key,
  marketplace_id smallint not null references public.marketplaces(id),
  tipo           text not null check (tipo in ('diaria', 'reconciliacao')),
  periodo_inicio date not null,
  periodo_fim    date not null,
  itens_api      int not null default 0 check (itens_api >= 0),
  novos          int not null default 0,
  atualizados    int not null default 0,
  total_api      numeric(12,2),
  total_banco    numeric(12,2),
  divergencias   jsonb not null default '[]',
  status         text not null check (status in ('ok', 'divergente', 'erro')),
  erro           text check (char_length(erro) <= 1000),
  criado_em      timestamptz not null default now(),
  constraint comissoes_importacoes_periodo check (periodo_fim >= periodo_inicio)
);
comment on table public.comissoes_importacoes is 'Cada importação/reconciliação de comissões com os relatórios oficiais (auditoria financeira).';
create index comissoes_importacoes_idx on public.comissoes_importacoes (marketplace_id, tipo, criado_em desc);
alter table public.comissoes_importacoes enable row level security;
revoke all on public.comissoes_importacoes from anon;
create policy "comissoes_importacoes: admins leem" on public.comissoes_importacoes for select to authenticated using ((select privado.eh_admin()));
create trigger comissoes_importacoes_imutavel before update on public.comissoes_importacoes
  for each row execute function privado.bloquear_alteracao();

-- ---------------------------------------------------------------- A4/A5 colunas novas
alter table public.marketplaces
  add column dominios_destino text[] not null default '{}' check (cardinality(dominios_destino) <= 20),
  add column parametros_afiliado text[] not null default '{}' check (cardinality(parametros_afiliado) <= 10),
  add column id_afiliado text check (id_afiliado ~ '^[A-Za-z0-9_.-]{2,80}$');
comment on column public.marketplaces.dominios_destino is 'Sites da loja onde o link de afiliado pode terminar (cada redirecionamento é conferido).';
comment on column public.marketplaces.parametros_afiliado is 'Parâmetros da URL final que identificam o afiliado. Em produção, obrigatório (confirme com um link real).';
comment on column public.marketplaces.id_afiliado is 'Seu identificador de afiliado: o valor do parâmetro precisa contê-lo (senão a comissão iria para outra conta).';
update public.marketplaces set dominios_destino = '{shopee.com.br}' where codigo = 'shopee';
update public.marketplaces set dominios_destino = '{mercadolivre.com.br,mercadolivre.com}' where codigo = 'mercado_livre';

alter table public.produtos
  add column preco_maximo_variacao numeric(10,2) check (preco_maximo_variacao > 0),
  add column referencia_derivada boolean not null default false;
comment on column public.produtos.preco_maximo_variacao is 'Maior preço entre as variações (cor, tamanho...). Diferença grande bloqueia a oferta.';
comment on column public.produtos.referencia_derivada is '"Preço de" calculado a partir de uma taxa de desconto da loja (não é um preço visto).';

-- mudança na variação de preço também exige nova análise
create or replace function privado.invalidar_analise()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.id and status in ('encontrada', 'aprovada', 'publicada', 'pausada') and analisada_em is not null;
  return new;
end $$;
drop trigger if exists produtos_invalidar_analise on public.produtos;
create trigger produtos_invalidar_analise after update of preco_atual, disponivel, preco_maximo_variacao, referencia_derivada
  on public.produtos for each row
  when (old.preco_atual is distinct from new.preco_atual or old.disponivel is distinct from new.disponivel
        or old.preco_maximo_variacao is distinct from new.preco_maximo_variacao
        or old.referencia_derivada is distinct from new.referencia_derivada)
  execute function privado.invalidar_analise();

-- ---------------------------------------------------------------- A1 campos sensíveis só do dono
create or replace function privado.proteger_campos_sensiveis()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is null then return new; end if;     -- backend / SQL Editor
  if tg_table_name = 'marketplaces' then  -- (IFs separados: cada tabela tem colunas diferentes)
    if not privado.eh_dono() and (
         new.dominios_afiliado is distinct from old.dominios_afiliado or new.padrao_afiliado is distinct from old.padrao_afiliado
         or new.dominios_destino is distinct from old.dominios_destino or new.parametros_afiliado is distinct from old.parametros_afiliado
         or new.id_afiliado is distinct from old.id_afiliado) then
      raise exception 'Só o dono altera domínios e identificadores de afiliado (protege as suas comissões).' using errcode = 'P0027';
    end if;
    return new;
  end if;
  if tg_table_name = 'configuracoes' then
    if new.chave in ('sistema.ambiente', 'sistema.parada_emergencia', 'sistema.parada_motivo', 'auth.email_dono')
       and coalesce(current_setting('bot.via_rpc', true), '') <> 'sim' then
      raise exception 'Essa configuração não pode ser alterada pelo painel.' using errcode = 'P0027';
    end if;
    if (new.chave like 'auth.%' or new.chave = 'transparencia.marcar_publi') and not privado.eh_dono() then
      raise exception 'Só o dono altera essa configuração.' using errcode = 'P0027';
    end if;
  end if;
  return new;
end $$;
create trigger marketplaces_campos_sensiveis before update on public.marketplaces
  for each row execute function privado.proteger_campos_sensiveis();
create trigger configuracoes_campos_sensiveis before update on public.configuracoes
  for each row execute function privado.proteger_campos_sensiveis();

-- ---------------------------------------------------------------- A2 parada de emergência
create or replace function public.ativar_parada_emergencia(p_motivo text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not privado.pode_editar() then
    raise exception 'Sem permissão.' using errcode = '42501';
  end if;
  perform set_config('bot.via_rpc', 'sim', true);
  update public.configuracoes set valor = 'true'::jsonb, atualizado_por = auth.uid() where chave = 'sistema.parada_emergencia';
  update public.configuracoes set valor = to_jsonb(left(coalesce(nullif(btrim(p_motivo), ''), 'sem motivo informado'), 280))
   where chave = 'sistema.parada_motivo';
  perform set_config('bot.via_rpc', '', true);   -- a autorização vale só para estas duas linhas
  update public.fila_publicacoes set bloqueado_ate = null where status = 'processando' and publicando_desde is null;
end $$;
create or replace function public.desativar_parada_emergencia()
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not privado.eh_dono() then
    raise exception 'Só o dono desliga a parada de emergência.' using errcode = '42501';
  end if;
  perform set_config('bot.via_rpc', 'sim', true);
  update public.configuracoes set valor = 'false'::jsonb, atualizado_por = auth.uid() where chave = 'sistema.parada_emergencia';
  perform set_config('bot.via_rpc', '', true);
end $$;
revoke execute on function public.ativar_parada_emergencia(text) from public, anon;
revoke execute on function public.desativar_parada_emergencia() from public, anon;
grant execute on function public.ativar_parada_emergencia(text) to authenticated;
grant execute on function public.desativar_parada_emergencia() to authenticated;

-- a fila não entrega nada enquanto a parada estiver ligada (defesa em profundidade)
create or replace function public.pegar_proximas_publicacoes(p_limite int default 5, p_bloqueio_min int default 10)
returns setof public.fila_publicacoes
language plpgsql security definer set search_path = '' as $$
declare v_inicio_dia timestamptz := (date_trunc('day', now() at time zone privado.fuso())) at time zone privado.fuso();
begin
  if privado.config_bool('sistema.parada_emergencia', false) then
    return;
  end if;

  update public.fila_publicacoes
     set status = 'falhou', bloqueado_ate = null,
         ultimo_erro = 'Resultado incerto: o bot parou durante o envio — confira o canal antes de publicar de novo.'
   where status = 'processando' and bloqueado_ate < now() and publicando_desde is not null;

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

-- ---------------------------------------------------------------- A3 cliques
create table public.cliques_suspeitos (
  id               bigint generated always as identity primary key,
  link_afiliado_id bigint not null references public.links_afiliados(id) on delete cascade,
  dia              date not null default (now() at time zone 'America/Recife')::date,
  motivo           text not null check (motivo in ('repetido', 'excesso_visitante', 'pico_link', 'sem_identificacao')),
  quantidade       int not null default 1 check (quantidade > 0),
  ultimo_em        timestamptz not null default now(),
  constraint cliques_suspeitos_unico unique (link_afiliado_id, dia, motivo)
);
comment on table public.cliques_suspeitos is 'Cliques que NÃO entram nas métricas (repetidos, robôs, excesso). Só contagem, sem dado pessoal.';
alter table public.cliques_suspeitos enable row level security;
revoke all on public.cliques_suspeitos from anon;
create policy "cliques_suspeitos: admins leem" on public.cliques_suspeitos for select to authenticated using ((select privado.eh_admin()));

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
create index cliques_ip_idx on public.cliques (ip_hash, ocorrido_em desc) where ip_hash is not null;

-- ---------------------------------------------------------------- A6 manutenção (retenção LGPD)
create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        v_cliques int := privado.config_numero('cliques.retencao_dias', 365)::int;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int; n_cupons int;
        n_cliques int; n_suspeitos int; n_convites int;
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

  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'cupons_expirados', n_cupons, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria,
                            'cliques_apagados', n_cliques, 'cliques_suspeitos_apagados', n_suspeitos, 'convites_apagados', n_convites);
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;

-- funções novas do schema privado: só o necessário
revoke execute on all functions in schema privado from public, anon;
grant execute on all functions in schema privado to authenticated, service_role;

-- ---------------------------------------------------------------------------------------------
-- Limite da Meta: 50 publicações por conta do Instagram em 24 h móveis (stories + feed + cupons).
-- O padrão de 60 stories/dia passava do limite; o bot já consulta a cota antes de publicar, mas o
-- painel prometia um número impossível. Folga de 4 para o feed e os stories de cupom.
-- ---------------------------------------------------------------------------------------------
update public.canais set limite_diario = 45 where codigo = 'instagram_story' and (limite_diario is null or limite_diario > 45);
