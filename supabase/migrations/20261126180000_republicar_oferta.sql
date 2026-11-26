-- 029 · Conferência de preço na fila (config) e republicar uma oferta já publicada (ou pausada/expirada) sem cadastrar de novo.
-- O painel confere o preço na página da loja ("Puxar dados") e manda o preço atual; aqui:
--   * a oferta antiga vira 'expirada' (motivo "Republicada"), preservando o histórico de posts e cliques;
--   * o produto recebe o preço conferido (verificado agora);
--   * nasce uma oferta nova 'aprovada' (vai para a fila e passa por análise e validações como qualquer outra);
--   * o link de afiliado do produto passa a apontar para a oferta nova.
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo)
values ('monitor.conferir_fila', 'true', 'booleano',
        'Conferir também o preço das ofertas na fila (antes de publicar), no mesmo intervalo das publicadas', null, null)
on conflict (chave) do nothing;

create or replace function public.republicar_oferta(
  p_promocao bigint, p_preco numeric, p_preco_antigo numeric default null,
  p_referencia_da_pagina boolean default false, p_preco_pix boolean default false)
returns bigint
language plpgsql security definer set search_path = '' as $$
declare v_old record; v_novo bigint;
begin
  if not privado.pode_editar() then
    raise exception 'Apenas dono ou editor podem republicar ofertas.' using errcode = '42501';
  end if;
  select pr.id, pr.produto_id, pr.status, pr.origem, pr.comissao_pct into v_old
    from public.promocoes pr where pr.id = p_promocao for update;
  if not found then
    raise exception 'Oferta não encontrada.' using errcode = 'P0043';
  end if;
  if v_old.status not in ('publicada', 'pausada', 'expirada') then
    raise exception 'Só ofertas publicadas, pausadas ou expiradas podem ser republicadas (esta está "%").', v_old.status
      using errcode = 'P0044';
  end if;
  if p_preco is null or p_preco <= 0 or p_preco > 1000000 then
    raise exception 'Informe o preço atual do produto.' using errcode = 'P0045';
  end if;
  if exists (select 1 from public.promocoes where produto_id = v_old.produto_id and id <> p_promocao
               and status in ('encontrada', 'aprovada', 'publicada')) then
    raise exception 'Este produto já tem outra oferta ativa.' using errcode = 'P0022';
  end if;
  if p_preco_antigo is not null and p_preco_antigo <= p_preco then
    p_preco_antigo := null;
  end if;

  update public.promocoes set status = 'expirada', motivo_status = 'Republicada'
   where id = p_promocao;
  update public.produtos set preco_atual = p_preco, preco_original = coalesce(p_preco_antigo, preco_original),
         disponivel = true, verificado_em = now()
   where id = v_old.produto_id;
  insert into public.promocoes (produto_id, preco_promocional, preco_referencia, referencia_origem, preco_no_pix,
                                comissao_pct, status, origem)
  values (v_old.produto_id, p_preco, p_preco_antigo,
          case when p_preco_antigo is null then null when coalesce(p_referencia_da_pagina, false) then 'pagina' else 'manual' end,
          coalesce(p_preco_pix, false), v_old.comissao_pct, 'aprovada', v_old.origem)
  returning id into v_novo;
  update public.links_afiliados set promocao_id = v_novo
   where produto_id = v_old.produto_id and ativo;
  return v_novo;
end $$;
revoke execute on function public.republicar_oferta(bigint, numeric, numeric, boolean, boolean) from public, anon;
grant execute on function public.republicar_oferta(bigint, numeric, numeric, boolean, boolean) to authenticated;
