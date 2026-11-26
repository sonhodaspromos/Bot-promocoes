-- Testes · auditoria complementar (migration 017)
-- Cadastro "como o Supabase Auth faz": inserção em auth.users SEM o atalho de teste.
create or replace function testes.cadastrar_como_auth(p_email text)
returns uuid language plpgsql set search_path = pg_catalog as $$
declare v uuid := gen_random_uuid();
begin
  insert into auth.users (id, instance_id, aud, role, email, encrypted_password, created_at, updated_at)
  values (v, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', p_email, '', now(), now());
  return v;
end $$;
revoke execute on function testes.cadastrar_como_auth(text) from public;

create or replace function testes.t_auditoria_complementar()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare c record; e text; e2 text; e3 text; u uuid; u2 uuid; v text; n int; n2 int; r record;
begin
  teste := 'cadastro público fechado: ninguém cria conta sem convite (P0028)';
  begin
    e := testes.erro_de('select testes.cadastrar_como_auth(''invasor@teste.com'')');
    ok := e like 'P0028%'; detalhe := coalesce(e, 'cadastrou sem convite');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'banco sem dono: o primeiro cadastro NÃO vira dono; só o e-mail definido no SQL Editor';
  begin
    perform set_config('app.transferindo_posse', 'sim', true);
    delete from public.administradores;
    perform set_config('app.transferindo_posse', '', true);
    e := testes.erro_de('select testes.cadastrar_como_auth(''primeiro@teste.com'')');
    perform public.definir_email_dono('Dona@Teste.com');
    e2 := testes.erro_de('select testes.cadastrar_como_auth(''outro@teste.com'')');
    u := testes.cadastrar_como_auth('dona@teste.com');
    select papel into v from public.administradores where user_id = u;
    select valor #>> '{}' into r from public.configuracoes where chave = 'auth.email_dono';
    e3 := testes.erro_de('select public.definir_email_dono(''segundo@teste.com'')');
    ok := e like 'P0028%' and e2 like 'P0028%' and v = 'dono' and e3 like 'P0001%';
    detalhe := coalesce(e, 'primeiro entrou') || ' | ' || coalesce(e2, 'outro entrou') || ' | papel: ' || coalesce(v, '-') || ' | ' || coalesce(e3, 'redefiniu dono');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'convite: dono convida; convidado entra com o papel certo; convite não é reutilizado nem vale vencido';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    insert into public.convites (email, papel) values ('editor@teste.com', 'editor'), ('velho@teste.com', 'leitor');
    perform testes.sair();
    update public.convites set expira_em = now() - interval '1 day' where email = 'velho@teste.com';
    u2 := testes.cadastrar_como_auth('editor@teste.com');
    select papel into v from public.administradores where user_id = u2;
    delete from auth.users where id = u2;
    e := testes.erro_de('select testes.cadastrar_como_auth(''editor@teste.com'')');
    e2 := testes.erro_de('select testes.cadastrar_como_auth(''velho@teste.com'')');
    ok := v = 'editor' and e like 'P0028%' and e2 like 'P0028%';
    detalhe := 'papel: ' || coalesce(v, '-') || ' | reuso: ' || coalesce(e, 'entrou') || ' | vencido: ' || coalesce(e2, 'entrou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'só o dono convida; painel não define o e-mail do dono';
  begin
    perform testes.garantir_dono();
    u := testes.novo_usuario('ed-conv@teste.com');
    insert into public.administradores (user_id, papel) values (u, 'editor');
    perform testes.como(u);
    e := testes.erro_de('insert into public.convites (email, papel) values (''x@teste.com'', ''editor'')');
    e2 := testes.erro_de('select public.definir_email_dono(''x@teste.com'')');
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'editor convidou') || ' | ' || coalesce(e2, 'definiu dono');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'MFA: dono só com senha (aal1) não vê nem altera nada; com 2º fator (aal2) sim';
  begin
    select * into c from testes.cenario();
    u := testes.garantir_dono();
    perform testes.como_sem_mfa(u);
    select count(*) into n from public.promocoes;
    e := testes.erro_de('update public.configuracoes set valor = ''25'' where chave = ''filtros.desconto_minimo''');
    select papel || '/' || aal || '/' || mfa_exigido into v from public.minha_sessao();
    perform testes.sair();
    perform testes.como(u);
    select count(*) into n2 from public.promocoes;
    perform testes.sair();
    ok := n = 0 and n2 > 0 and v = 'dono/aal1/true';
    detalhe := 'aal1 vê ' || n || ' | aal2 vê ' || n2 || ' | sessão ' || coalesce(v, '-') || ' | update aal1: ' || coalesce(e, 'sem erro (0 linhas)');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'comissões: o ON CONFLICT do backend funciona e atualiza mudança de status (cancelamento)';
  begin
    select * into c from testes.cenario();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, data_venda, origem, status)
      values (c.mkt, 'conv-1-p-1-i-1', 5, current_date, 'api', 'pendente')
      on conflict (marketplace_id, referencia_externa) do update set status = excluded.status;
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, data_venda, origem, status)
      values (c.mkt, 'conv-1-p-1-i-1', 5, current_date, 'api', 'cancelada')
      on conflict (marketplace_id, referencia_externa) do update set status = excluded.status;
    insert into public.comissoes (marketplace_id, valor_comissao, data_venda, origem) values (c.mkt, 1, current_date, 'manual'), (c.mkt, 2, current_date, 'manual');
    select status into v from public.comissoes where referencia_externa = 'conv-1-p-1-i-1';
    select count(*) into n from public.comissoes where marketplace_id = c.mkt;
    ok := v = 'cancelada' and n = 3; detalhe := 'status: ' || v || ' | linhas: ' || n;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'contrato com o backend: todo on_conflict usado pelo backend tem índice único completo';
  begin
    select count(*) into n from (values ('comissoes', 'marketplace_id,referencia_externa'), ('cupons', 'marketplace_id,id_externo'),
                                        ('cupons_produtos', 'cupom_id,promocao_id')) alvo(tabela, colunas)
     where not exists (
       select 1 from pg_index ix join pg_class t on t.oid = ix.indrelid
        where t.relname = alvo.tabela and ix.indisunique and ix.indpred is null
          and (select string_agg(a.attname, ',' order by k.ord) from unnest(ix.indkey) with ordinality k(attnum, ord)
                 join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum) = alvo.colunas);
    ok := n = 0; detalhe := n || ' alvo(s) sem índice compatível';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'editor não altera domínio/identificador de afiliado nem o #publi; dono altera; ninguém troca o ambiente pelo painel';
  begin
    select * into c from testes.cenario();
    u := testes.garantir_dono();
    u2 := testes.novo_usuario('ed-sens@teste.com');
    insert into public.administradores (user_id, papel) values (u2, 'editor');
    perform testes.como(u2);
    e := testes.erro_de(format('update public.marketplaces set id_afiliado = ''golpista'' where id = %s', c.mkt));
    e2 := testes.erro_de('update public.configuracoes set valor = ''false'' where chave = ''transparencia.marcar_publi''');
    perform testes.erro_de(format('update public.marketplaces set nome = ''Loja Renomeada'' where id = %s', c.mkt));
    perform testes.sair();
    perform testes.como(u);
    e3 := testes.erro_de(format('update public.marketplaces set id_afiliado = ''meu_id'' where id = %s', c.mkt));
    v := testes.erro_de('update public.configuracoes set valor = ''"homologacao"'' where chave = ''sistema.ambiente''');
    perform testes.sair();
    select nome || '/' || coalesce(id_afiliado, '-') into r from public.marketplaces where id = c.mkt;
    ok := e like 'P0027%' and e2 like 'P0027%' and e3 is null and v like 'P0027%' and r::text like '%Loja Renomeada/meu_id%';
    detalhe := coalesce(e, 'editor trocou id') || ' | ' || coalesce(e2, 'editor desligou publi') || ' | dono: ' || coalesce(e3, 'ok')
               || ' | ambiente: ' || coalesce(v, 'trocou') || ' | ' || r::text;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'parada de emergência: editor liga; fila não entrega nada; só o dono desliga; ninguém muda direto';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link);
    u := testes.garantir_dono();
    u2 := testes.novo_usuario('ed-parada@teste.com');
    insert into public.administradores (user_id, papel) values (u2, 'editor');
    perform testes.como(u2);
    perform public.ativar_parada_emergencia('post errado no ar');
    e := testes.erro_de('select public.desativar_parada_emergencia()');
    perform testes.sair();
    select count(*) into n from public.pegar_proximas_publicacoes(100, 10);
    perform testes.como(u);
    e2 := testes.erro_de('update public.configuracoes set valor = ''false'' where chave = ''sistema.parada_emergencia''');
    perform public.desativar_parada_emergencia();
    perform testes.sair();
    select count(*) into n2 from public.pegar_proximas_publicacoes(100, 10);
    ok := n = 0 and n2 >= 1 and e like '42501%' and e2 like 'P0027%';
    detalhe := 'com parada: ' || n || ' | depois: ' || n2 || ' | editor desliga: ' || coalesce(e, 'conseguiu') || ' | update direto: ' || coalesce(e2, 'conseguiu');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cliques: mesmo visitante conta 1 vez em 24 h; sem identificação e excesso viram suspeitos (fora das métricas)';
  begin
    select * into c from testes.cenario();
    perform public.registrar_clique(c.link, 'telegram', repeat('a', 64), 'Mozilla', null);
    perform public.registrar_clique(c.link, 'telegram', repeat('a', 64), 'Mozilla', null);
    perform public.registrar_clique(c.link, 'telegram', null, 'Mozilla', null);
    update public.configuracoes set valor = '5' where chave = 'cliques.max_por_visitante_10min';
    for i in 1..8 loop
      perform public.registrar_clique(c.link, 'telegram', repeat('b', 64), 'Mozilla', null);
    end loop;
    select count(*) into n from public.cliques where link_afiliado_id = c.link;
    select string_agg(motivo || '=' || quantidade, ',' order by motivo) into v from public.cliques_suspeitos where link_afiliado_id = c.link;
    select cliques into n2 from public.vw_promocoes_painel where id = c.promo;
    ok := n = 2 and n2 = 2 and v = 'repetido=8,sem_identificacao=1';
    detalhe := 'válidos: ' || n || ' | métrica do painel: ' || n2 || ' | suspeitos: ' || coalesce(v, '-');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'cliques: excesso de um visitante em 10 minutos vira suspeito';
  begin
    select * into c from testes.cenario();
    update public.configuracoes set valor = '5' where chave = 'cliques.max_por_visitante_10min';
    insert into public.cliques (link_afiliado_id, ip_hash, ocorrido_em)
      select c.link, repeat('c', 64), now() - make_interval(secs => g) from generate_series(1, 5) g;
    perform public.registrar_clique(c.link, 'telegram', repeat('c', 64), 'Mozilla', null);
    select string_agg(motivo, ',') into v from public.cliques_suspeitos where link_afiliado_id = c.link;
    ok := v = 'excesso_visitante'; detalhe := coalesce(v, 'contou normal');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'variação de preço gravada no produto apaga a análise (nova análise antes de publicar)';
  begin
    select * into c from testes.cenario();
    perform testes.classificar(c.promo);
    update public.produtos set preco_maximo_variacao = 999 where id = c.prod;
    select coalesce(classificacao, 'sem análise') into v from public.promocoes where id = c.promo;
    ok := v = 'sem análise'; detalhe := v;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'LGPD: manutenção apaga cliques além da retenção e convites vencidos';
  begin
    select * into c from testes.cenario();
    insert into public.cliques (link_afiliado_id, ip_hash, ocorrido_em) values (c.link, repeat('d', 64), now() - interval '400 days');
    insert into public.convites (email, papel, expira_em) values ('esquecido@teste.com', 'leitor', now() - interval '60 days');
    perform public.manutencao_diaria();
    select count(*) into n from public.cliques where ip_hash = repeat('d', 64);
    select count(*) into n2 from public.convites where email = 'esquecido@teste.com';
    ok := n = 0 and n2 = 0; detalhe := 'cliques antigos: ' || n || ' | convites vencidos: ' || n2;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'visitante (anon) não chama parada de emergência nem lê a sessão';
  begin
    execute 'set local role anon';
    e := testes.erro_de('select public.ativar_parada_emergencia(''x'')');
    e2 := testes.erro_de('select * from public.minha_sessao()');
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'executou') || ' | ' || coalesce(e2, 'executou');
  exception when others then perform testes.sair(); ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_auditoria_complementar() from public;
