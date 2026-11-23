-- Testes · correções da auditoria final (migration 015)
create or replace function testes.t_auditoria()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; e text; e2 text; f bigint; l bigint; r record; n int; v text; ex uuid;
begin
  teste := 'link no domínio certo mas sem atribuição de afiliado é recusado (P0024)';
  begin
    select * into c from testes.cenario();
    update public.marketplaces set padrao_afiliado = '^https://afiliado\.teste\.com\.br/sec/[A-Za-z0-9]+' where id = c.mkt;
    insert into public.links_afiliados (produto_id, url) values (c.prod, 'https://afiliado.teste.com.br/p/2') returning id into l;
    e := testes.erro_de(format('update public.links_afiliados set validado = true where id = %s', l));
    insert into public.links_afiliados (produto_id, url) values (c.prod, 'https://afiliado.teste.com.br/sec/AbC9') returning id into f;
    e2 := testes.erro_de(format('update public.links_afiliados set validado = true where id = %s', f));
    ok := e like 'P0024%' and e2 is null; detalhe := coalesce(e, 'aceitou sem atribuição') || ' | ' || coalesce(e2, 'com /sec/ ok');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'padrão de afiliado inválido não é aceito';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('update public.marketplaces set padrao_afiliado = %L where id = %s', '^https://(quebrado', c.mkt));
    e2 := testes.erro_de(format('update public.marketplaces set padrao_afiliado = %L where id = %s', 'qualquer', c.mkt));
    ok := e like '23514%' and e2 like '23514%'; detalhe := coalesce(e, 'aceitou regex quebrada') || ' | ' || coalesce(e2, 'aceitou sem ^https://');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'job morreu DURANTE o envio: o item vira "resultado incerto" e não é publicado de novo';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform 1 from public.pegar_proximas_publicacoes(100, 10) where id = f;
    perform public.fila_marcar_publicando(f);
    update public.fila_publicacoes set bloqueado_ate = now() - interval '1 minute' where id = f;   -- trava venceu
    select count(*) into n from public.pegar_proximas_publicacoes(100, 10) where id = f;
    select status || ' · ' || ultimo_erro into v from public.fila_publicacoes where id = f;
    ok := n = 0 and v like 'falhou%incerto%'; detalhe := 'pego de novo: ' || n || ' | ' || v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'job morreu ANTES de enviar: o item volta sozinho depois da trava';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform 1 from public.pegar_proximas_publicacoes(100, 10) where id = f;
    select count(*) into n from public.pegar_proximas_publicacoes(100, 10) where id = f;   -- trava ainda vale
    update public.fila_publicacoes set bloqueado_ate = now() - interval '1 minute' where id = f;
    select count(*) + n * 10 into n from public.pegar_proximas_publicacoes(100, 10) where id = f;
    select tentativas into r from public.fila_publicacoes where id = f;
    ok := n = 1 and r.tentativas = 2; detalhe := 'resultado ' || n || ', tentativas ' || r.tentativas;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'devolver à fila (token vencido / limite) não gasta tentativa e respeita a espera';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform 1 from public.pegar_proximas_publicacoes(100, 10) where id = f;
    perform public.fila_devolver(f, 60, 'credencial recusada');
    select count(*) into n from public.pegar_proximas_publicacoes(100, 10) where id = f;
    select * into r from public.fila_publicacoes where id = f;
    ok := r.status = 'pendente' and r.tentativas = 0 and r.agendado_para > now() + interval '50 minutes' and n = 0;
    detalhe := r.status || ', tentativas ' || r.tentativas || ', pego agora: ' || n;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'registrar o resultado limpa a marca de "publicando"';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    perform 1 from public.pegar_proximas_publicacoes(100, 10) where id = f;
    perform public.fila_marcar_publicando(f);
    insert into public.historico_publicacoes (fila_id, promocao_id, canal_id, link_afiliado_id, resultado, id_mensagem_externa)
      values (f, c.promo, c.canal_real, c.link, 'sucesso', '1');
    select * into r from public.fila_publicacoes where id = f;
    ok := r.status = 'concluida' and r.publicando_desde is null; detalhe := r.status || ' / ' || coalesce(r.publicando_desde::text, 'sem marca');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'rodada que ficou "rodando" (job morto) é encerrada como erro';
  begin
    insert into public.execucoes (origem, iniciada_em) values ('agendada', now() - interval '2 hours') returning id into ex;
    n := public.encerrar_execucoes_orfas(30);
    select status || ' · ' || (resumo->>'interrompida') into v from public.execucoes where id = ex;
    ok := n >= 1 and v = 'erro · true'; detalhe := n || ' encerrada(s): ' || v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'oferta publicada perde a análise quando o preço muda (volta para o motor)';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso');
    update public.produtos set preco_atual = 99 where id = c.prod;    -- caiu: continua ativa, mas precisa de nova análise
    select status || ' · ' || coalesce(classificacao, 'sem análise') into v from public.promocoes where id = c.promo;
    ok := v = 'publicada · sem análise'; detalhe := v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'painel (usuário logado) não chama as funções internas da fila';
  begin
    perform testes.como(testes.garantir_dono());
    e := testes.erro_de('select public.fila_devolver(1, 1, ''x'')');
    e2 := testes.erro_de('select public.encerrar_execucoes_orfas(30)');
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'executou') || ' | ' || coalesce(e2, 'executou');
    raise exception using errcode = 'TT000';   -- desfaz o usuário de teste
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'stories cabem na cota da API do Instagram (≤ 45 + 1 feed por dia)';
  begin
    select coalesce(sum(limite_diario), 0) into n from public.canais where codigo in ('instagram_story', 'instagram_feed');
    ok := n <= 50; detalhe := n || ' publicações/dia no Instagram (cota: 50)';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_auditoria() from public;
