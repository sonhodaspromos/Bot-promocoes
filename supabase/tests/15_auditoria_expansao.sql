-- Testes · auditoria da expansão modular (migration 021)
create or replace function testes.t_auditoria_expansao()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare r record; n int; v boolean; t1 bigint; i1 bigint; i2 bigint; d date; ts timestamptz; s text; x record;
        canal_vitrine smallint; pub bigint;
begin
  teste := 'clique é atribuído à publicação de origem (link + canal) e os canais da vitrine/WhatsApp/alerta existem inativos';
  begin
    select * into r from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, publicado_em)
    values (r.promo, r.canal_real, r.link, 'sucesso', now() - interval '1 hour') returning id into pub;
    perform public.registrar_clique(r.link, 'teste_real', repeat('a', 64), 'Mozilla/5.0', null);
    select publicacao_id into t1 from public.cliques where link_afiliado_id = r.link order by id desc limit 1;
    select count(*) into n from public.canais where codigo in ('vitrine', 'whatsapp', 'alerta') and not ativo and modo = 'desativado';
    perform public.registrar_clique(r.link, 'vitrine', repeat('b', 64), 'Mozilla/5.0', null);
    select canal_id into canal_vitrine from public.cliques where ip_hash = repeat('b', 64);
    ok := t1 = pub and n = 3 and canal_vitrine = (select id from public.canais where codigo = 'vitrine');
    detalhe := format('publicacao=%s esperado=%s canais=%s', t1, pub, n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'rollup: idempotente, confere com os brutos, absorve evento atrasado e ignora clique repetido';
  begin
    select * into r from testes.cenario();
    d := privado.dia_local(now()) - 1;
    ts := (d::timestamp + interval '12 hours') at time zone privado.fuso();
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ip_hash, ocorrido_em)
    select r.link, r.promo, r.canal_real, md5(g::text) || md5(g::text), ts + g * interval '1 second' from generate_series(1, 25) g;
    perform public.agregar_cliques(d);
    perform public.agregar_cliques(d);                         -- 2ª vez: mesmo resultado
    select * into x from public.verificar_rollup(d);
    ok := x.cliques_brutos = 25 and x.cliques_agregados = 25 and x.diferenca = 0;
    insert into public.cliques (link_afiliado_id, promocao_id, canal_id, ip_hash, ocorrido_em)
    values (r.link, r.promo, r.canal_real, repeat('c', 64), ts + interval '5 hours');   -- chegou atrasado
    perform public.agregar_cliques_periodo(d - 2, d);
    select * into x from public.verificar_rollup(d);
    ok := ok and x.cliques_agregados = 26 and x.diferenca = 0
          and (select visitantes from public.cliques_diarios where dia = d and link_afiliado_id = r.link) = 26;
    -- duplicado: o mesmo visitante 2x no mesmo link vira 1 clique + 1 suspeito
    perform public.registrar_clique(r.link, 'teste_real', repeat('e', 64), 'Mozilla/5.0', null);
    perform public.registrar_clique(r.link, 'teste_real', repeat('e', 64), 'Mozilla/5.0', null);
    perform public.agregar_cliques(privado.dia_local(now()));
    ok := ok and (select count(*) from public.cliques where ip_hash = repeat('e', 64)) = 1
          and (select diferenca from public.verificar_rollup(privado.dia_local(now()))) = 0;
    detalhe := format('brutos=%s agregados=%s', x.cliques_brutos, x.cliques_agregados);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'retenção: a manutenção agrega antes de apagar; o rollup do dia apagado é preservado e não é regravado';
  begin
    select * into r from testes.cenario();
    d := privado.dia_local(now()) - 400;
    ts := (d::timestamp + interval '12 hours') at time zone privado.fuso();
    insert into public.cliques (link_afiliado_id, canal_id, ip_hash, ocorrido_em)
    select r.link, r.canal_real, md5(g::text) || md5((g + 9)::text), ts from generate_series(1, 7) g;
    perform public.manutencao_diaria();
    ok := (select count(*) from public.cliques where ocorrido_em = ts) = 0
          and (select sum(cliques) from public.cliques_diarios where dia = d) = 7;
    begin
      perform public.agregar_cliques(d);
      ok := false; detalhe := 'regravou dia fora da retenção';
    exception when sqlstate '22023' then null; end;
    ok := ok and (select sum(cliques) from public.cliques_diarios where dia = d) = 7
          and (public.agregar_cliques_periodo(d, d) ->> 'dias_fora_da_retencao')::int = 1;
    detalhe := coalesce(detalhe, format('rollup do dia apagado=%s', (select sum(cliques) from public.cliques_diarios where dia = d)));
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'orçamento: reserva só cabe no teto, estorno devolve e o alerta de consumo dispara uma vez';
  begin
    ok := public.orcamento_reservar('teste_srv', 0.0625, 0.13) and public.orcamento_reservar('teste_srv', 0.0625, 0.13)
          and not public.orcamento_reservar('teste_srv', 0.0625, 0.13);
    select * into x from public.orcamento_situacao('teste_srv');
    ok := ok and x.gasto_usd = 0.125 and x.alertar;
    ok := ok and not (select alertar from public.orcamento_situacao('teste_srv'));
    perform public.orcamento_estornar('teste_srv', 0.0625);
    ok := ok and public.orcamento_reservar('teste_srv', 0.0625, 0.13);
    detalhe := format('gasto=%s pct=%s', x.gasto_usd, x.pct);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'WhatsApp: reserva atômica recusa repetido, limite por pessoa, descadastrado e falta de orçamento';
  begin
    select * into r from testes.cenario();
    select * into x from testes.outra_oferta(r.mkt);
    i1 := public.whatsapp_inscrever('+5581911112222', 'formulario', 'Quero receber ofertas pelo WhatsApp');
    i2 := public.whatsapp_inscrever('+5581933334444', 'formulario', 'Quero receber ofertas pelo WhatsApp');
    s := public.whatsapp_reservar_envio(i1, r.promo, 0.0625, 1, 1);
    ok := s = 'ok' and public.whatsapp_reservar_envio(i1, r.promo, 0.0625, 1, 1) = 'repetido'
          and public.whatsapp_reservar_envio(i1, x.promo, 0.0625, 1, 1) = 'limite_pessoa';
    perform public.whatsapp_descadastrar('+5581933334444');
    ok := ok and public.whatsapp_reservar_envio(i2, r.promo, 0.0625, 1, 5) = 'descadastrado';
    i2 := public.whatsapp_inscrever('+5581955556666', 'formulario', 'Quero receber ofertas pelo WhatsApp');
    ok := ok and public.whatsapp_reservar_envio(i2, r.promo, 0.0625, 0.0625, 5) = 'orcamento'
          and not exists (select 1 from public.whatsapp_envios where inscrito_id = i2);
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'webhook: id repetido, mensagem velha, replay de inscrição depois do SAIR e mensagem fora de ordem são ignorados';
  begin
    s := public.whatsapp_processar_mensagem('wamid.A1', '+5581977778888', 'entrar', now() - interval '10 minutes');
    ok := s = 'inscrito' and public.whatsapp_processar_mensagem('wamid.A1', '+5581977778888', 'entrar', now() - interval '10 minutes') = 'duplicada';
    ok := ok and public.whatsapp_processar_mensagem('wamid.A2', '+5581977778888', 'sair', now() - interval '5 minutes') = 'descadastrado';
    -- replay: mesmo conteúdo com outro id e data anterior ao SAIR
    ok := ok and public.whatsapp_processar_mensagem('wamid.A3', '+5581977778888', 'entrar', now() - interval '8 minutes') = 'ignorada_fora_de_ordem'
          and (select descadastrado_em is not null from public.whatsapp_inscritos where telefone = '+5581977778888');
    ok := ok and public.whatsapp_processar_mensagem('wamid.A4', '+5581999990000', 'entrar', now() - interval '8 days') = 'ignorada_data'
          and public.whatsapp_processar_mensagem('wamid.A5', '+5581977778888', 'entrar', now()) = 'inscrito';
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'workers: dono renova a trava; outro worker e trava vencida não renovam; saúde da fila alerta falha definitiva';
  begin
    t1 := public.tarefa_enfileirar('vitrine.gerar', '{}', null);
    perform public.tarefas_pegar(array['vitrine.gerar'], 1, 5, 'w1');
    ok := public.tarefa_renovar(t1, 'w1', 10) and not public.tarefa_renovar(t1, 'w2', 10);
    update public.tarefas set bloqueada_ate = now() - interval '1 second' where id = t1;
    ok := ok and not public.tarefa_renovar(t1, 'w1', 10);
    select * into x from public.fila_saude();
    ok := ok and x.travas_vencidas >= 1 and x.alerta;
    update public.tarefas set status = 'falhou', atualizada_em = now() where id = t1;
    select * into x from public.fila_saude();
    ok := ok and x.falhas_24h >= 1 and x.motivo like '%falha definitiva%';
    detalhe := x.motivo;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'vitrine: preço não conferido, preço que subiu e oferta vencida ficam de fora';
  begin
    select * into r from testes.cenario();
    update public.produtos set verificado_em = now(), preco_atual = 100 where id = r.prod;
    update public.promocoes set status = 'publicada', publicada_em = now() where id = r.promo;
    n := (select count(*) from public.ofertas_vitrine(7));
    update public.produtos set verificado_em = now() - interval '30 hours' where id = r.prod;
    ok := n = 1 and (select count(*) from public.ofertas_vitrine(7)) = 0;
    update public.produtos set verificado_em = now(), preco_atual = 120 where id = r.prod;
    ok := ok and (select count(*) from public.ofertas_vitrine(7)) = 0;
    update public.produtos set preco_atual = 100 where id = r.prod;
    update public.promocoes set inicio_em = now() - interval '2 days', fim_em = now() - interval '1 minute' where id = r.promo;
    ok := ok and (select count(*) from public.ofertas_vitrine(7)) = 0;
    detalhe := format('vigente=%s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'financeiro: pendente é estimativa, confirmada/paga é receita; demo fica fora; custos separados';
  begin
    select * into r from testes.cenario();
    insert into public.comissoes (marketplace_id, valor_comissao, status, origem, data_venda, data_pagamento) values
      (r.mkt, 10, 'pendente', 'manual', current_date, null), (r.mkt, 7, 'confirmada', 'manual', current_date, null),
      (r.mkt, 5, 'paga', 'manual', current_date, current_date), (r.mkt, 3, 'cancelada', 'manual', current_date, null),
      (r.mkt, 100, 'confirmada', 'demo', current_date, null);
    insert into public.custos_servicos (mes, servico, valor_usd, origem) values (date_trunc('month', current_date)::date, 'supabase', 25, 'fatura');
    select * into x from public.vw_financeiro_mensal where mes = date_trunc('month', current_date)::date;
    ok := x.receita_estimada_brl >= 10 and x.receita_confirmada_brl - x.receita_recebida_brl >= 7
          and x.receita_confirmada_brl < 100 and x.custos_faturados_usd = 25;
    detalhe := format('estimada=%s confirmada=%s recebida=%s custos=%s', x.receita_estimada_brl, x.receita_confirmada_brl,
                      x.receita_recebida_brl, x.custos_faturados_usd);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'A/B de horário aceito; funções e tabelas novas fechadas para anon/authenticated';
  begin
    insert into public.experimentos (nome, tipo) values ('teste horario', 'horario');
    select count(*) into n from pg_proc p join pg_namespace s on s.oid = p.pronamespace
     where s.nspname = 'public' and p.proname in ('agregar_cliques', 'agregar_cliques_periodo', 'verificar_rollup',
       'orcamento_reservar', 'orcamento_estornar', 'orcamento_situacao', 'whatsapp_reservar_envio',
       'whatsapp_processar_mensagem', 'tarefa_renovar', 'fila_saude', 'ofertas_vitrine')
       and (has_function_privilege('anon', p.oid, 'execute') or has_function_privilege('authenticated', p.oid, 'execute'));
    ok := n = 0 and not has_table_privilege('anon', 'public.cliques_diarios', 'select')
          and not has_table_privilege('anon', 'public.orcamento_consumo', 'select')
          and not has_table_privilege('authenticated', 'public.orcamento_consumo', 'insert')
          and not has_table_privilege('authenticated', 'public.whatsapp_webhook_mensagens', 'select');
    detalhe := format('funções expostas=%s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_auditoria_expansao() from public;
