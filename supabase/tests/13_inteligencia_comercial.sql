-- Testes · inteligência comercial (migration 019)
create or replace function testes.t_inteligencia_comercial()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; e text; n int; r record; d uuid; v_id bigint; v_pub bigint; v_pub2 bigint; j jsonb; x numeric;
begin
  teste := 'histórico com âncora: devolve o último preço antes da janela';
  begin
    select * into c from testes.cenario();
    update public.produtos set preco_atual = 90 where id = c.prod;      -- 2º registro
    -- envelhece o 1º registro para antes da janela (histórico é imutável; teste roda como dono do banco)
    alter table public.historico_precos disable trigger historico_precos_imutavel;
    update public.historico_precos set registrado_em = now() - interval '200 days'
     where produto_id = c.prod and preco = 100;
    alter table public.historico_precos enable trigger historico_precos_imutavel;
    select count(*) filter (where ancora), count(*) filter (where not ancora) into r
      from public.historico_precos_janela(array[c.prod], now() - interval '90 days');
    ok := r.count = 1 and r.count = 1;
    select count(*) into n from public.historico_precos_janela(array[c.prod], now() - interval '90 days') where ancora and preco = 100;
    ok := ok and n = 1; detalhe := format('âncora com o preço antigo: %s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'GTIN: dígito verificador conferido (inválido é recusado)';
  begin
    select * into c from testes.cenario();
    update public.produtos set gtin = '4006381333931' where id = c.prod;
    e := testes.erro_de(format('update public.produtos set gtin = %L where id = %s', '4006381333932', c.prod));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou GTIN inválido');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'comissão da API ligada ao produto e à oferta publicada antes da venda';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado)
      values (c.promo, c.canal_real, c.link, 'sucesso');
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, id_externo_item)
      values (c.mkt, 'R-1', 5, 'api', current_date, 'T-1') returning produto_id, promocao_id, atribuicao into r;
    ok := r.produto_id = c.prod and r.promocao_id = c.promo and r.atribuicao = 'oferta_publicada';
    detalhe := format('%s / %s / %s', r.produto_id, r.promocao_id, r.atribuicao);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'comissão sem post antes da venda e item desconhecido não são atribuídos a ofertas';
  begin
    select * into c from testes.cenario();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, id_externo_item)
      values (c.mkt, 'R-2', 5, 'api', current_date, 'T-1') returning promocao_id, atribuicao into r;
    ok := r.promocao_id is null and r.atribuicao = 'produto_sem_publicacao';
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, id_externo_item)
      values (c.mkt, 'R-3', 5, 'api', current_date, 'NAO-EXISTE') returning produto_id, atribuicao into r;
    ok := ok and r.produto_id is null and r.atribuicao = 'item_desconhecido';
    detalhe := format('%s', r.atribuicao);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'eventos de comissão: criação e mudança de status registradas; paga só o dono desfaz (P0029)';
  begin
    select * into c from testes.cenario();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda)
      values (c.mkt, 'R-4', 5, 'api', current_date - 10) returning id into v_id;
    update public.comissoes set status = 'confirmada' where id = v_id;
    update public.comissoes set status = 'paga', data_pagamento = current_date where id = v_id;
    select count(*) into n from public.comissoes_eventos where comissao_id = v_id;
    e := testes.erro_de(format('update public.comissoes set status = %L, data_pagamento = null where id = %s', 'pendente', v_id));
    ok := n = 3 and e like 'P0029%'; detalhe := format('eventos=%s | %s', n, coalesce(e, 'desfez paga'));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'repasse: valor que bate concilia e marca pagas; divergente não marca nada';
  begin
    select * into c from testes.cenario();
    d := testes.garantir_dono();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, status)
      values (c.mkt, 'R-5', 10, 'api', date '2026-09-10', 'confirmada'), (c.mkt, 'R-6', 15.5, 'api', date '2026-09-20', 'confirmada'),
             (c.mkt, 'R-7', 3, 'api', date '2026-10-02', 'confirmada');
    perform testes.como(d);
    select * into r from public.registrar_repasse(c.mkt, date '2026-09-01', 25.5, date '2026-10-25');
    perform testes.sair();
    select count(*) into n from public.comissoes where marketplace_id = c.mkt and status = 'paga';
    ok := r.status = 'conciliado' and r.valor_esperado = 25.5 and n = 2;
    perform testes.como(d);
    select * into r from public.registrar_repasse(c.mkt, date '2026-10-01', 1.00, date '2026-11-25');
    perform testes.sair();
    select count(*) into n from public.comissoes where marketplace_id = c.mkt and status = 'paga';
    ok := ok and r.status = 'divergente' and r.diferenca = -2 and n = 2;
    detalhe := format('%s, pagas=%s', r.status, n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; perform testes.sair(); end;
  return next;

  teste := 'repasse: só o dono registra';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    perform testes.como(testes.novo_usuario('editor-repasse@teste.com'));
    e := testes.erro_de(format('select public.registrar_repasse(%s::smallint, date %L, 1, date %L)', c.mkt, '2026-09-01', '2026-10-01'));
    perform testes.sair();
    ok := e like '42501%'; detalhe := coalesce(e, 'não-dono registrou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; perform testes.sair(); end;
  return next;

  teste := 'publicação guarda preço e categoria; experimento sem variante é recusado (P0031)';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado)
      values (c.promo, c.canal_real, c.link, 'sucesso') returning preco_publicado, categoria_id into r;
    insert into public.experimentos (nome, tipo) values ('Teste legenda', 'legenda') returning id into v_id;
    e := testes.erro_de(format('insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, experimento_id) '
                               'values (%s, %s, %s, %L, %s)', c.promo, c.canal_demo, c.link, 'sucesso', v_id));
    ok := r.preco_publicado = 100 and r.categoria_id is not null and e like 'P0031%';
    detalhe := format('preço=%s cat=%s | %s', r.preco_publicado, r.categoria_id, coalesce(e, 'aceitou'));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'só um experimento ativo por vez';
  begin
    insert into public.experimentos (nome, tipo) values ('Exp 1', 'legenda');
    e := testes.erro_de($q$insert into public.experimentos (nome, tipo) values ('Exp 2', 'arte')$q$);
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou dois ativos');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'desempenho por publicação: cliques, cliques em 2 h e comissão dividida entre as publicações da oferta';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado)
      values (c.promo, c.canal_real, c.link, 'sucesso') returning id into v_pub;
    insert into public.canais (codigo, tipo, nome, modo) values ('teste_real2', 'instagram_story', 'Story', 'real') returning id into n;
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado)
      values (c.promo, n, c.link, 'sucesso') returning id into v_pub2;
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, publicacao_id, ocorrido_em)
      values (c.link, c.promo, c.canal_real, v_pub, now()), (c.link, c.promo, c.canal_real, v_pub, now() + interval '3 hours');
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, id_externo_item)
      values (c.mkt, 'R-8', 8, 'api', current_date, 'T-1');
    select cliques, cliques_2h, comissao_atribuida, conversoes_atribuidas, faixa_preco into r
      from public.vw_desempenho_publicacoes where publicacao_id = v_pub;
    ok := r.cliques = 2 and r.cliques_2h = 1 and r.comissao_atribuida = 4 and r.conversoes_atribuidas = 0.5
          and r.faixa_preco = 'R$ 100–250';
    detalhe := format('%s', row_to_json(r));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'conciliação por mês separa pendentes, confirmadas, canceladas e pagas';
  begin
    select * into c from testes.cenario();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, origem, data_venda, status, data_pagamento) values
      (c.mkt, 'C-1', 1, 'api', date '2026-08-05', 'pendente', null), (c.mkt, 'C-2', 2, 'api', date '2026-08-06', 'confirmada', null),
      (c.mkt, 'C-3', 4, 'api', date '2026-08-07', 'cancelada', null), (c.mkt, 'C-4', 8, 'api', date '2026-08-08', 'paga', date '2026-09-25');
    select * into r from public.vw_conciliacao_comissoes where marketplace = 'teste_mkt' and competencia = date '2026-08-01';
    ok := r.valor_pendente = 1 and r.valor_confirmado = 2 and r.valor_cancelado = 4 and r.valor_pago = 8
          and r.pendentes_antigas = 1 and r.confirmadas_sem_repasse = 1;
    detalhe := format('%s', row_to_json(r));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'seleções e resumos: anon não lê; painel lê; só o sistema grava';
  begin
    ok := not has_table_privilege('anon', 'public.selecoes_ofertas', 'select')
      and not has_table_privilege('anon', 'public.vw_desempenho_publicacoes', 'select')
      and not has_table_privilege('authenticated', 'public.selecoes_ofertas', 'insert')
      and not has_table_privilege('authenticated', 'public.inteligencia_resumos', 'insert')
      and not has_table_privilege('authenticated', 'public.comissoes_pagamentos', 'insert')
      and has_table_privilege('authenticated', 'public.vw_conciliacao_comissoes', 'select');
    detalhe := case when ok then 'permissões corretas' else 'permissão indevida' end;
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_inteligencia_comercial() from public;
