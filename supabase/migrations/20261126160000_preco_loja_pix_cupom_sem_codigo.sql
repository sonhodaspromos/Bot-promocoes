-- 027 · (1) "preço de" riscado lido pelo bot na página da loja conta como desconto comprovado;
--       (2) indicação de preço no Pix; (3) cupom sem código (ativado na página, ex.: "cupom por seguir a loja").

-- (1)+(2) colunas novas na oferta
alter table public.promocoes
  add column if not exists referencia_origem text check (referencia_origem in ('loja', 'manual', 'pagina')),
  add column if not exists preco_no_pix boolean not null default false;
comment on column public.promocoes.referencia_origem is
  'De onde veio o preco_referencia: loja (API), manual (digitado) ou pagina (riscado lido pelo bot na página da loja).';
comment on column public.promocoes.preco_no_pix is 'O preço promocional é o do pagamento no Pix (o post diz "no Pix").';

insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo)
values ('inteligencia.aceitar_preco_pagina', 'true', 'booleano',
        'Aceitar o "preço de" riscado lido pelo bot na página da loja como desconto comprovado', null, null)
on conflict (chave) do nothing;

-- cadastro manual: dois parâmetros novos (opcionais, o painel antigo continua funcionando)
drop function if exists public.cadastrar_oferta_manual(text, text, numeric, text, numeric, int, text, text);
create or replace function public.cadastrar_oferta_manual(
  p_marketplace text, p_titulo text, p_preco numeric, p_link text,
  p_preco_antigo numeric default null, p_categoria_id int default null,
  p_imagem_url text default null, p_id_externo text default null,
  p_referencia_da_pagina boolean default false, p_preco_pix boolean default false)
returns bigint
language plpgsql security invoker set search_path = '' as $$
declare
  v_mkt record; v_produto bigint; v_promo bigint; v_id_externo text;
begin
  if not privado.pode_editar() then
    raise exception 'Apenas dono ou editor podem cadastrar ofertas.' using errcode = '42501';
  end if;
  select id, nome, modo, ativo, dominios_afiliado into v_mkt from public.marketplaces where codigo = p_marketplace;
  if not found or not v_mkt.ativo or v_mkt.modo = 'desativado' then
    raise exception 'Loja "%" não encontrada ou desativada.', p_marketplace using errcode = 'P0020';
  end if;
  if p_link is null or p_link !~* '^https://[^[:space:]]+$' then
    raise exception 'O link precisa começar com https:// e não ter espaços.' using errcode = 'P0021';
  end if;
  if not privado.dominio_permitido(p_link, v_mkt.dominios_afiliado) then
    raise exception 'Esse link não é um link de afiliado de % (domínios aceitos: %).',
      v_mkt.nome, array_to_string(v_mkt.dominios_afiliado, ', ') using errcode = 'P0011';
  end if;
  if p_preco_antigo is not null and p_preco_antigo <= p_preco then
    p_preco_antigo := null;  -- preço antigo menor ou igual não é desconto
  end if;

  v_id_externo := coalesce(nullif(btrim(p_id_externo), ''), 'manual-' || left(md5(lower(p_link)), 16));

  insert into public.produtos (marketplace_id, id_externo, categoria_id, titulo, preco_atual, preco_original, imagem_url)
  values (v_mkt.id, v_id_externo, p_categoria_id, btrim(p_titulo), p_preco, p_preco_antigo, nullif(btrim(p_imagem_url), ''))
  on conflict (marketplace_id, id_externo) do update
     set titulo = excluded.titulo, preco_atual = excluded.preco_atual, preco_original = excluded.preco_original,
         categoria_id = coalesce(excluded.categoria_id, public.produtos.categoria_id),
         imagem_url = coalesce(excluded.imagem_url, public.produtos.imagem_url)
  returning id into v_produto;

  if exists (select 1 from public.promocoes where produto_id = v_produto and status in ('encontrada', 'aprovada', 'publicada')) then
    raise exception 'Este produto já tem uma oferta ativa. Pause ou rejeite a anterior antes de cadastrar outra.' using errcode = 'P0022';
  end if;

  insert into public.promocoes (produto_id, preco_promocional, preco_referencia, referencia_origem, preco_no_pix, status, origem)
  values (v_produto, p_preco, p_preco_antigo,
          case when p_preco_antigo is null then null when coalesce(p_referencia_da_pagina, false) then 'pagina' else 'manual' end,
          coalesce(p_preco_pix, false), 'aprovada', 'manual')
  returning id into v_promo;

  insert into public.links_afiliados (produto_id, promocao_id, url)
  values (v_produto, v_promo, btrim(p_link))
  on conflict (url) do update set promocao_id = excluded.promocao_id, ativo = true,
         validado = false, status_http = null, motivo_invalido = null
  where public.links_afiliados.produto_id = excluded.produto_id;

  return v_promo;
end $$;
revoke execute on function public.cadastrar_oferta_manual(text, text, numeric, text, numeric, int, text, text, boolean, boolean) from public, anon;
grant execute on function public.cadastrar_oferta_manual(text, text, numeric, text, numeric, int, text, text, boolean, boolean) to authenticated;

-- (3) cupom sem código: permitido quando tem desconto informado (o bot precisa calcular o preço com cupom)
alter table public.cupons drop constraint if exists cupons_codigo_obrigatorio;
alter table public.cupons add constraint cupons_codigo_obrigatorio
  check (tipo = 'campanha' or codigo is not null or desconto_tipo is not null);
comment on column public.cupons.codigo is 'Código digitado no pagamento. Nulo em campanhas e em cupons ativados na página do produto.';
