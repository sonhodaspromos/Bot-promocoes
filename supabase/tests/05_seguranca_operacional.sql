-- Testes · segurança (RLS e permissões) e funções operacionais

-- Visitante anônimo também precisa conseguir chamar erro_de()/sair() durante o teste
grant usage on schema testes to anon;
grant execute on function testes.erro_de(text), testes.sair() to anon;

create or replace function testes.t_seguranca()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare c record; e text; e2 text; n int; n2 int; u_dono uuid; u_ed uuid; u_le uuid; u_sem uuid; p2 bigint;
begin
  teste := 'visitante anônimo não lê nenhuma tabela';
  begin
    select * into c from testes.cenario();
    execute 'set local role anon';
    e := testes.erro_de('select count(*) from public.produtos');
    e2 := testes.erro_de('select count(*) from public.configuracoes');
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'leu produtos') || ' | ' || coalesce(e2, 'leu configurações');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'usuário logado sem cadastro de administrador não vê nem altera nada';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    u_sem := testes.novo_usuario('intruso@teste.com');
    perform testes.como(u_sem);
    select count(*) into n from public.produtos;
    select count(*) into n2 from public.vw_promocoes_painel;
    e := testes.erro_de('insert into public.categorias (nome, slug) values (''Invasão'', ''invasao'')');
    perform testes.sair();
    ok := n = 0 and n2 = 0 and e like '42501%'; detalhe := n || ' produtos, ' || n2 || ' na view, insert: ' || coalesce(e, 'aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'leitor lê, mas não altera';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    u_le := testes.novo_usuario('leitor@teste.com');
    insert into public.administradores (user_id, papel) values (u_le, 'leitor');
    perform testes.como(u_le);
    select count(*) into n from public.produtos where id = c.prod;
    update public.promocoes set status = 'rejeitada' where id = c.promo;
    get diagnostics n2 = row_count;
    e := testes.erro_de('insert into public.categorias (nome, slug) values (''Leitor'', ''leitor'')');
    perform testes.sair();
    ok := n = 1 and n2 = 0 and e like '42501%'; detalhe := 'vê ' || n || ', alterou ' || n2 || ', insert: ' || coalesce(e, 'aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'editor aprova ofertas, mas não valida link nem marca como publicada';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    u_ed := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u_ed, 'editor');
    update public.promocoes set status = 'encontrada' where id = c.promo;
    perform testes.como(u_ed);
    update public.promocoes set status = 'aprovada' where id = c.promo;
    get diagnostics n = row_count;
    e := testes.erro_de(format('update public.promocoes set status = ''publicada'' where id = %s', c.promo));
    e2 := testes.erro_de(format('insert into public.links_afiliados (produto_id, url, validado) values (%s, ''https://afiliado.teste.com.br/x'', true)', c.prod));
    perform testes.sair();
    ok := n = 1 and e like 'P0017%' and e2 like 'P0016%';
    detalhe := 'aprovou ' || n || ' | ' || coalesce(e, 'publicou') || ' | ' || coalesce(e2, 'validou link');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'editor não vê auditoria nem credenciais e não cria marketplace';
  begin
    u_dono := testes.garantir_dono();
    perform testes.como(u_dono);
    perform public.salvar_credencial('teste_api', 'TOKEN', 'abcdefghijklmnop');
    perform testes.sair();
    u_ed := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u_ed, 'editor');
    perform testes.como(u_ed);
    select count(*) into n from public.credenciais_integracoes;
    select count(*) into n2 from public.auditoria;
    e := testes.erro_de('insert into public.marketplaces (codigo, nome) values (''amazon'', ''Amazon'')');
    perform testes.sair();
    ok := n = 0 and n2 = 0 and e like '42501%'; detalhe := n || ' credenciais, ' || n2 || ' auditoria, marketplace: ' || coalesce(e, 'criou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'nem o dono grava direto em logs, históricos e cliques (só o sistema)';
  begin
    select * into c from testes.cenario();
    u_dono := testes.garantir_dono();
    perform testes.como(u_dono);
    e := testes.erro_de('insert into public.logs (nivel, modulo, mensagem) values (''INFO'', ''painel'', ''falso'')');
    e2 := testes.erro_de(format('insert into public.cliques (link_afiliado_id) values (%s)', c.link));
    n := case when testes.erro_de(format('insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (%s, %s, %s, ''sucesso'')', c.promo, c.canal_real, c.link)) like '42501%' then 1 else 0 end;
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%' and n = 1; detalhe := coalesce(e, 'gravou log') || ' | ' || coalesce(e2, 'gravou clique');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'dono vê auditoria e todos os administradores; outros só veem a si mesmos';
  begin
    u_dono := testes.garantir_dono();
    u_ed := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u_ed, 'editor');
    perform testes.como(u_dono);
    update public.categorias set ordem = ordem where slug = 'eletronicos';
    select count(*) into n from public.administradores;
    select count(*) into n2 from public.auditoria;
    perform testes.sair();
    perform testes.como(u_ed);
    ok := n >= 2 and n2 >= 1 and (select count(*) from public.administradores) = 1;
    perform testes.sair();
    detalhe := 'dono vê ' || n || ' admins e ' || n2 || ' auditoria(s)';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'painel não cria comissão "api" (só manual)';
  begin
    select * into c from testes.cenario();
    perform testes.garantir_dono();
    u_ed := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u_ed, 'editor');
    perform testes.como(u_ed);
    e := testes.erro_de(format('insert into public.comissoes (marketplace_id, valor_comissao, data_venda, origem, referencia_externa) values (%s, 5, current_date, ''api'', ''X'')', c.mkt));
    e2 := testes.erro_de(format('insert into public.comissoes (marketplace_id, valor_comissao, data_venda) values (%s, 5, current_date)', c.mkt));
    perform testes.sair();
    ok := e like '42501%' and e2 is null; detalhe := 'api: ' || coalesce(e, 'aceita') || ' | manual: ' || coalesce(e2, 'aceita');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'funções do backend são negadas para usuários do painel';
  begin
    u_dono := testes.garantir_dono();
    perform testes.como(u_dono);
    n := 0;
    if testes.erro_de('select public.pegar_proximas_publicacoes(1, 1)') like '42501%' then n := n + 1; end if;
    if testes.erro_de('select public.registrar_clique(1)') like '42501%' then n := n + 1; end if;
    if testes.erro_de('select public.obter_credencial(''a'', ''B'')') like '42501%' then n := n + 1; end if;
    perform testes.sair();
    ok := n = 3; detalhe := n || ' de 3 funções bloqueadas';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;

create or replace function testes.t_operacional()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare c record; s text; n int; f bigint; f2 bigint; r record; v text;
begin
  teste := 'dados iniciais presentes (marketplaces, categorias, canais, configurações)';
  begin
    ok := (select count(*) from public.marketplaces where codigo in ('shopee', 'mercado_livre', 'demo')) = 3
      and (select count(*) from public.categorias where slug in ('eletronicos', 'beleza', 'casa', 'moda', 'brinquedos')) = 5
      and (select count(*) from public.canais where codigo in ('telegram', 'instagram_story', 'instagram_feed')) = 3
      and (select count(*) from public.configuracoes) >= 24
      and not exists (select 1 from public.canais where modo = 'real');
    detalhe := (select count(*) from public.configuracoes) || ' configurações; todos os canais em demo';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'preço subiu além da tolerância: promoção pausada e fila cancelada';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    update public.produtos set preco_atual = 110 where id = c.prod;
    select status || ' / ' || motivo_status into s from public.promocoes where id = c.promo;
    ok := s like 'pausada / Preço subiu%' and (select status from public.fila_publicacoes where id = f) = 'cancelada';
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'preço subiu dentro da tolerância (5%): promoção continua';
  begin
    select * into c from testes.cenario();
    update public.produtos set preco_atual = 104 where id = c.prod;
    select status into s from public.promocoes where id = c.promo;
    ok := s = 'aprovada'; detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'produto indisponível: promoção pausada';
  begin
    select * into c from testes.cenario();
    update public.produtos set disponivel = false where id = c.prod;
    select status || ' / ' || motivo_status into s from public.promocoes where id = c.promo;
    ok := s = 'pausada / Produto indisponível'; detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'monitor desligado nas configurações: não pausa';
  begin
    select * into c from testes.cenario();
    update public.configuracoes set valor = 'false' where chave = 'monitor.ativo';
    update public.produtos set preco_atual = 500 where id = c.prod;
    select status into s from public.promocoes where id = c.promo;
    ok := s = 'aprovada'; detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'worker pega o próximo item da fila e marca como processando';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (c.promo, c.canal_real, c.link) returning id into f;
    select * into r from public.pegar_proximas_publicacoes(5, 10) where id = f;
    ok := r.status = 'processando' and r.tentativas = 1 and r.bloqueado_ate > now();
    detalhe := coalesce(r.status, 'não pegou') || ', tentativa ' || coalesce(r.tentativas::text, '-');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'worker não pega item agendado para o futuro e respeita a prioridade';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, agendado_para) values (c.promo, c.canal_real, c.link, now() + interval '1 hour') returning id into f;
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, prioridade) values (c.promo, c.canal_demo, c.link, 1) returning id into f2;
    select count(*) into n from public.pegar_proximas_publicacoes(5, 10) where id = f;
    ok := n = 0 and (select status from public.fila_publicacoes where id = f2) = 'processando';
    detalhe := 'futuro pego: ' || n || ', prioritário: ' || (select status from public.fila_publicacoes where id = f2);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'worker respeita o limite diário do canal';
  begin
    select * into c from testes.cenario();
    update public.canais set limite_diario = 1 where id = c.canal_real;
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso');
    select * into r from testes.outra_oferta(c.mkt);
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (r.promo, c.canal_real, r.link) returning id into f;
    select count(*) into n from public.pegar_proximas_publicacoes(5, 10) where id = f;
    ok := n = 0; detalhe := case when n = 0 then 'aguardando amanhã' else 'passou do limite' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'worker respeita o intervalo mínimo do canal';
  begin
    select * into c from testes.cenario();
    update public.canais set intervalo_minimo_min = 30 where id = c.canal_real;
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso');
    select * into r from testes.outra_oferta(c.mkt);
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id) values (r.promo, c.canal_real, r.link) returning id into f;
    select count(*) into n from public.pegar_proximas_publicacoes(5, 10) where id = f;
    ok := n = 0; detalhe := case when n = 0 then 'aguardando intervalo' else 'ignorou o intervalo' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'item travado que esgotou as tentativas vira "falhou"';
  begin
    select * into c from testes.cenario();
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, max_tentativas) values (c.promo, c.canal_real, c.link, 1) returning id into f;
    update public.fila_publicacoes set status = 'processando', tentativas = 1, bloqueado_ate = now() - interval '1 minute' where id = f;
    perform public.pegar_proximas_publicacoes(5, 10);
    select status into s from public.fila_publicacoes where id = f;
    ok := s = 'falhou'; detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'views do painel: 90 dias de métricas e cliques/comissão por oferta';
  begin
    select * into c from testes.cenario();
    perform public.registrar_clique(c.link, 'teste_real', repeat('ef', 32));
    insert into public.comissoes (marketplace_id, promocao_id, valor_comissao, data_venda) values (c.mkt, c.promo, 7.5, current_date);
    select count(*) into n from public.vw_metricas_diarias;
    select cliques || ' / ' || comissao_total || ' / ' || link_validado into v from public.vw_promocoes_painel where id = c.promo;
    ok := n = 90 and v = '1 / 7.50 / true'
          and (select cliques from public.vw_metricas_diarias order by dia desc limit 1) >= 1;
    detalhe := n || ' dias; oferta: ' || coalesce(v, '?');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
