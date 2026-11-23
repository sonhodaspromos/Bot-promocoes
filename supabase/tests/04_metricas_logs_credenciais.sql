-- Testes · cliques, comissões, configurações, logs, auditoria e credenciais
create or replace function testes.t_metricas_logs_credenciais()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare c record; e text; e2 text; s text; n int; u uuid; u2 uuid; r record; x uuid;
        ip text := repeat('ab', 32);
begin
  teste := 'clique é registrado e devolve o link de afiliado validado';
  begin
    select * into c from testes.cenario();
    select * into r from public.registrar_clique(c.link, 'teste_real', ip, 'Mozilla', null);
    select count(*) into n from public.cliques where link_afiliado_id = c.link and promocao_id = c.promo and canal_id = c.canal_real;
    ok := n = 1 and r.url_destino = 'https://afiliado.teste.com.br/p/1' and r.oferta_ativa;
    detalhe := n || ' clique(s), destino ' || coalesce(r.url_destino, 'nulo');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'clique repetido do mesmo visitante em 30s conta uma vez só';
  begin
    select * into c from testes.cenario();
    perform public.registrar_clique(c.link, null, ip);
    perform public.registrar_clique(c.link, null, ip);
    perform public.registrar_clique(c.link, null, repeat('cd', 32));
    select count(*) into n from public.cliques where link_afiliado_id = c.link;
    ok := n = 2; detalhe := n || ' cliques (esperado 2)';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link invalidado: clique conta, mas ninguém é mandado para o link';
  begin
    select * into c from testes.cenario();
    update public.links_afiliados set validado = false where id = c.link;
    select * into r from public.registrar_clique(c.link, null, ip);
    ok := r.url_destino is null and not r.oferta_ativa; detalhe := 'destino: ' || coalesce(r.url_destino, 'nenhum');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'comissão repetida da API (mesma referência) é recusada';
  begin
    select * into c from testes.cenario();
    insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, data_venda, origem) values (c.mkt, 'PED-1', 5, current_date, 'api');
    e := testes.erro_de(format('insert into public.comissoes (marketplace_id, referencia_externa, valor_comissao, data_venda, origem) values (%s, ''PED-1'', 5, current_date, ''api'')', c.mkt));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou duplicada');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'comissão inconsistente é recusada (paga sem data, maior que a venda, API sem referência)';
  begin
    select * into c from testes.cenario();
    e := testes.erro_de(format('insert into public.comissoes (marketplace_id, valor_comissao, data_venda, status) values (%s, 5, current_date, ''paga'')', c.mkt));
    e2 := testes.erro_de(format('insert into public.comissoes (marketplace_id, valor_comissao, valor_venda, data_venda) values (%s, 50, 10, current_date)', c.mkt));
    s := testes.erro_de(format('insert into public.comissoes (marketplace_id, valor_comissao, data_venda, origem) values (%s, 5, current_date, ''api'')', c.mkt));
    ok := e like '23514%' and e2 like '23514%' and s like '23514%';
    detalhe := coalesce(e, 'aceitou paga sem data') || ' | ' || coalesce(e2, 'aceitou maior') || ' | ' || coalesce(s, 'aceitou api sem ref');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'configuração com tipo errado ou fora dos limites é recusada';
  begin
    insert into public.configuracoes (chave, valor, tipo, minimo, maximo) values ('teste.limite', '10', 'numero', 1, 50);
    e := testes.erro_de($q$update public.configuracoes set valor = '"dez"' where chave = 'teste.limite'$q$);
    e2 := testes.erro_de($q$update public.configuracoes set valor = '99' where chave = 'teste.limite'$q$);
    ok := e like '23514%' and e2 like '23514%'; detalhe := coalesce(e, 'aceitou texto') || ' | ' || coalesce(e2, 'aceitou 99');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'logs mascaram tokens e chaves automaticamente';
  begin
    insert into public.logs (nivel, modulo, mensagem, contexto) values ('ERRO', 'telegram',
      'Falha em https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendPhoto?access_token=abc123',
      '{"token": "segredo-123", "sb": "sb_secret_abcdefghijk", "ok": "valor normal"}');
    select mensagem || ' ' || contexto::text into s from public.logs order by id desc limit 1;
    ok := s not like '%AAHdqTcv%' and s not like '%abc123%' and s not like '%segredo-123%'
          and s not like '%abcdefghijk%' and s like '%valor normal%';
    detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'erro de publicação também tem o token mascarado';
  begin
    select * into c from testes.cenario();
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado, erro)
      values (c.promo, c.canal_real, c.link, 'erro', 'HTTP 401 em /bot987654321:ZZHdqTcvCH1vGWJxfSeofSAs0K5PALDxyz/sendPhoto');
    select erro into s from public.historico_publicacoes where promocao_id = c.promo order by id desc limit 1;
    ok := s not like '%ZZHdqTcv%'; detalhe := s;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'execução finalizada exige data de fim; logs são imutáveis';
  begin
    e := testes.erro_de($q$insert into public.execucoes (status) values ('sucesso')$q$);
    insert into public.logs (nivel, modulo, mensagem) values ('INFO', 'teste', 'ok');
    e2 := testes.erro_de($q$update public.logs set mensagem = 'alterado' where modulo = 'teste'$q$);
    ok := e like '23514%' and e2 like 'P0002%'; detalhe := coalesce(e, 'aceitou') || ' | ' || coalesce(e2, 'alterou log');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'apagar a execução apaga os logs dela (cascata)';
  begin
    insert into public.execucoes default values returning id into x;
    insert into public.logs (execucao_id, nivel, modulo, mensagem) values (x, 'INFO', 'teste', 'a'), (x, 'ERRO', 'teste', 'b');
    delete from public.execucoes where id = x;
    select count(*) into n from public.logs where execucao_id = x;
    ok := n = 0; detalhe := n || ' log(s) restantes';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'auditoria registra alteração feita por pessoa logada; ação do bot não entra';
  begin
    u := testes.garantir_dono();
    insert into public.categorias (nome, slug) values ('Auditada', 'auditada');
    perform testes.como(u);
    update public.categorias set ativa = false where slug = 'auditada';
    perform testes.sair();
    select count(*) into n from public.auditoria where tabela = 'categorias' and usuario_id = u and operacao = 'UPDATE';
    ok := n = 1 and not exists (select 1 from public.auditoria where tabela = 'categorias' and operacao = 'INSERT' and dados_depois ->> 'slug' = 'auditada');
    detalhe := n || ' registro(s) da pessoa; inserção do bot não auditada';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'credencial fica cifrada no Vault e só aparece a dica';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    perform public.salvar_credencial('teste_api', 'TOKEN', 'meu-token-super-secreto-1234');
    perform testes.sair();
    select * into r from public.credenciais_integracoes where integracao = 'teste_api';
    select secret into s from vault.secrets where id = r.vault_secret_id;
    ok := r.dica = '••••1234' and s <> 'meu-token-super-secreto-1234' and s not like '%super-secreto%'
          and public.obter_credencial('teste_api', 'TOKEN') = 'meu-token-super-secreto-1234';
    detalhe := 'dica ' || r.dica || ', armazenado cifrado: ' || (s <> 'meu-token-super-secreto-1234')::text;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'editor não salva credencial; usuário logado não consegue ler o valor';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    perform public.salvar_credencial('teste_api', 'TOKEN', 'valor-1234567890');
    perform testes.sair();
    u2 := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u2, 'editor');
    perform testes.como(u2);
    e := testes.erro_de($q$select public.salvar_credencial('teste_api', 'TOKEN', 'invasor')$q$);
    perform testes.sair();
    perform testes.como(u);
    e2 := testes.erro_de($q$select public.obter_credencial('teste_api', 'TOKEN')$q$);
    perform testes.sair();
    ok := e like '42501%' and e2 like '42501%'; detalhe := coalesce(e, 'editor salvou') || ' | ' || coalesce(e2, 'dono leu o valor');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'atualizar credencial troca o valor; remover apaga do Vault';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    perform public.salvar_credencial('teste_api', 'TOKEN', 'primeiro-valor-0001');
    perform public.salvar_credencial('teste_api', 'TOKEN', 'segundo-valor-0002');
    perform testes.sair();
    select vault_secret_id into x from public.credenciais_integracoes where integracao = 'teste_api';
    s := public.obter_credencial('teste_api', 'TOKEN');
    perform testes.como(u);
    perform public.remover_credencial('teste_api', 'TOKEN');
    perform testes.sair();
    ok := s = 'segundo-valor-0002' and not exists (select 1 from vault.secrets where id = x)
          and not exists (select 1 from public.credenciais_integracoes where integracao = 'teste_api');
    detalhe := 'valor após atualizar: ' || case when s = 'segundo-valor-0002' then 'novo' else 'antigo' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
