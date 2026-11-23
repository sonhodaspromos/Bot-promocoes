-- =====================================================================
-- 013 · Motor de inteligência de ofertas
--   * pontuação (0–100), classificação e análise explicada em cada promoção
--   * só ofertas "excelente" ou "boa" entram na fila (trava no banco)
--   * a mesma oferta (produto) não repete no mesmo canal dentro da janela (padrão 24 h)
--   * mudança de preço/disponibilidade invalida a análise anterior (precisa analisar de novo)
-- =====================================================================

alter table public.promocoes
  add column pontuacao     smallint check (pontuacao between 0 and 100),
  add column classificacao text check (classificacao in ('excelente', 'boa', 'nao_recomendada')),
  add column confianca     smallint check (confianca between 0 and 100),
  add column analise       jsonb check (analise is null or jsonb_typeof(analise) = 'object'),
  add column analisada_em  timestamptz,
  add constraint promocoes_analise_completa check (
    (classificacao is null and pontuacao is null and analisada_em is null)
    or (classificacao is not null and pontuacao is not null and analisada_em is not null and analise is not null));

comment on column public.promocoes.pontuacao is 'Pontuação do motor de inteligência (0–100). Calculada pelo sistema; o painel não altera.';
comment on column public.promocoes.analise is 'Explicação da análise: componentes, motivos de aprovação, bloqueios e alertas.';

create index promocoes_fila_inteligente_idx on public.promocoes (classificacao, pontuacao desc)
  where status in ('encontrada', 'aprovada');
create index promocoes_analisar_idx on public.promocoes (analisada_em nulls first)
  where status in ('encontrada', 'aprovada');

-- Mudou preço, preço de referência ou disponibilidade do produto -> análise vencida
create or replace function privado.invalidar_analise()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.id and status in ('encontrada', 'aprovada') and analisada_em is not null;
  return new;
end $$;
create trigger produtos_invalidar_analise after update of preco_atual, preco_original, disponivel on public.produtos
  for each row when (old.preco_atual is distinct from new.preco_atual
                     or old.preco_original is distinct from new.preco_original
                     or old.disponivel is distinct from new.disponivel)
  execute function privado.invalidar_analise();

-- Link mudou de situação -> análise vencida (qualidade do link faz parte da nota)
create or replace function privado.invalidar_analise_link()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.produto_id and status in ('encontrada', 'aprovada') and analisada_em is not null;
  return new;
end $$;
create trigger links_invalidar_analise after update of validado, ativo on public.links_afiliados
  for each row when (old.validado is distinct from new.validado or old.ativo is distinct from new.ativo)
  execute function privado.invalidar_analise_link();

-- Painel não altera pontuação/classificação/análise (é tarefa do motor)
create or replace function privado.restricoes_do_painel()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is null then
    return new;
  end if;
  if tg_table_name = 'links_afiliados' then
    if new.validado and (tg_op = 'INSERT' or not old.validado or new.url is distinct from old.url) then
      raise exception 'A validação do link de afiliado é feita automaticamente pelo sistema.' using errcode = 'P0016';
    end if;
  elsif tg_table_name = 'promocoes' then
    if new.status = 'publicada' and (tg_op = 'INSERT' or old.status <> 'publicada') then
      raise exception 'Só o sistema marca uma oferta como publicada (depois de publicar de verdade).' using errcode = 'P0017';
    end if;
    if (tg_op = 'INSERT' and (new.pontuacao is not null or new.classificacao is not null or new.analise is not null))
       or (tg_op = 'UPDATE' and (new.pontuacao is distinct from old.pontuacao or new.classificacao is distinct from old.classificacao
                                 or new.analise is distinct from old.analise or new.confianca is distinct from old.confianca)) then
      raise exception 'A pontuação e a classificação são calculadas pelo motor de inteligência.' using errcode = 'P0023';
    end if;
  end if;
  return new;
end $$;

-- Fila: além das regras anteriores, exige análise favorável e respeita a janela de repetição
create or replace function privado.fila_regras()
returns trigger language plpgsql set search_path = '' as $$
declare l record; p record; c record; v_janela numeric;
begin
  if new.status not in ('pendente', 'processando') then
    return new;
  end if;
  select validado, ativo, produto_id into l from public.links_afiliados where id = new.link_afiliado_id;
  select status, produto_id, demo, classificacao into p from public.promocoes where id = new.promocao_id;
  select ativo, modo into c from public.canais where id = new.canal_id;

  if not (l.validado and l.ativo) then
    raise exception 'Fila recusada: o link de afiliado não está validado ou está inativo.' using errcode = 'P0010';
  end if;
  if l.produto_id <> p.produto_id then
    raise exception 'Fila recusada: o link é de outro produto.' using errcode = 'P0012';
  end if;
  if p.status not in ('aprovada', 'publicada') then
    raise exception 'Fila recusada: a promoção está "%" (precisa estar aprovada).', p.status using errcode = 'P0013';
  end if;
  if not c.ativo or c.modo = 'desativado' then
    raise exception 'Fila recusada: canal inativo.' using errcode = 'P0014';
  end if;
  if p.demo and c.modo = 'real' then
    raise exception 'Fila recusada: oferta de demonstração não pode ir para canal real.' using errcode = 'P0015';
  end if;
  if tg_op = 'INSERT' then
    if coalesce(p.classificacao, 'nao_recomendada') not in ('excelente', 'boa') then
      raise exception 'Fila recusada: a oferta não foi aprovada pelo motor de inteligência (classificação: %).',
        coalesce(p.classificacao, 'sem análise') using errcode = 'P0019';
    end if;
    v_janela := privado.config_numero('inteligencia.janela_repeticao_horas', 24);
    if exists (select 1 from public.historico_publicacoes h join public.promocoes pr on pr.id = h.promocao_id
                where pr.produto_id = p.produto_id and h.canal_id = new.canal_id and h.resultado = 'sucesso'
                  and not h.demo and h.publicado_em > now() - make_interval(hours => v_janela::int)) then
      raise exception 'Fila recusada: esta oferta já foi publicada neste canal nas últimas % horas.', v_janela::int
        using errcode = 'P0018';
    end if;
  end if;
  return new;
end $$;

-- View do painel: mesmas colunas + análise (colunas novas no final)
create or replace view public.vw_promocoes_painel with (security_invoker = true) as
select pr.id, pr.status, pr.motivo_status, pr.preco_promocional, pr.preco_referencia, pr.desconto_pct,
       pr.comissao_pct, pr.origem, pr.demo, pr.criado_em, pr.aprovada_em, pr.publicada_em, pr.pausada_em,
       p.id as produto_id, p.titulo, p.imagem_url, p.nota, p.disponivel, p.preco_atual,
       m.codigo as marketplace, m.nome as marketplace_nome,
       c.id as categoria_id, c.nome as categoria,
       l.id as link_id, coalesce(l.url_curta, l.url) as link_url, coalesce(l.validado and l.ativo, false) as link_validado,
       coalesce(cl.total, 0)::int as cliques,
       coalesce(co.total, 0)::numeric(12,2) as comissao_total,
       hp.ultima_publicacao,
       pr.pontuacao, pr.classificacao, pr.confianca, pr.analise, pr.analisada_em
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

-- Regras configuráveis do motor
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('inteligencia.desconto_meta',              '60',    'numero',   'Desconto real que já vale a nota máxima no quesito desconto (%)', 1, 95),
  ('inteligencia.desconto_maximo_plausivel',  '90',    'numero',   'Acima deste desconto (%) a oferta é bloqueada como inconsistente', 30, 99),
  ('inteligencia.comissao_meta',              '10',    'numero',   'Comissão que já vale a nota máxima no quesito comissão (%)', 1, 50),
  ('inteligencia.comissao_maxima_plausivel',  '50',    'numero',   'Acima desta comissão (%) a oferta é bloqueada como inconsistente', 5, 100),
  ('inteligencia.peso_desconto',              '35',    'numero',   'Peso do desconto real na pontuação', 0, 100),
  ('inteligencia.peso_comissao',              '20',    'numero',   'Peso da comissão na pontuação', 0, 100),
  ('inteligencia.peso_popularidade',          '15',    'numero',   'Peso da popularidade (nota e vendas da loja) na pontuação', 0, 100),
  ('inteligencia.peso_relevancia',            '15',    'numero',   'Peso da relevância comercial na pontuação', 0, 100),
  ('inteligencia.peso_qualidade',             '15',    'numero',   'Peso da qualidade (imagem e link) na pontuação', 0, 100),
  ('inteligencia.nota_excelente',             '75',    'numero',   'Pontuação mínima para "Excelente oportunidade"', 1, 100),
  ('inteligencia.nota_boa',                   '50',    'numero',   'Pontuação mínima para "Boa oportunidade"', 1, 100),
  ('inteligencia.confianca_minima_excelente', '60',    'numero',   'Confiança mínima dos dados (%) para ser "Excelente"', 0, 100),
  ('inteligencia.historico_minimo_registros', '3',     'numero',   'Registros de preço necessários para usar o histórico', 2, 100),
  ('inteligencia.historico_dias',             '90',    'numero',   'Dias de histórico de preço analisados', 7, 365),
  ('inteligencia.tolerancia_inflacao_pct',    '5',     'numero',   'Quanto o "preço de" pode passar do maior preço já visto antes de ser considerado inflado (%)', 0, 50),
  ('inteligencia.faixa_preco_min',            '10',    'numero',   'Preço mínimo da faixa de maior apelo comercial (R$)', 0, 100000),
  ('inteligencia.faixa_preco_max',            '1500',  'numero',   'Preço máximo da faixa de maior apelo comercial (R$)', 1, 100000),
  ('inteligencia.janela_repeticao_horas',     '24',    'numero',   'Horas antes de a mesma oferta poder voltar ao mesmo canal', 1, 720),
  ('inteligencia.exigir_imagem',              'false', 'booleano', 'Bloquear ofertas sem imagem', null, null),
  ('inteligencia.verificar_imagem',           'true',  'booleano', 'Baixar a imagem para conferir o tamanho', null, null)
on conflict (chave) do nothing;
