-- Testes · cadastro de oferta manual pelo painel
create or replace function testes.t_oferta_manual()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare u uuid; u2 uuid; e text; p bigint; r record;
begin
  teste := 'editor cadastra oferta manual: produto + promoção aprovada + link ainda não validado';
  begin
    perform testes.garantir_dono();
    u := testes.novo_usuario('editor@teste.com');
    insert into public.administradores (user_id, papel) values (u, 'editor');
    perform testes.como(u);
    p := public.cadastrar_oferta_manual('mercado_livre', 'Air Fryer 4L', 299.90, 'https://mercadolivre.com/sec/abc123', 499.90);
    perform testes.sair();
    select pr.status, pr.origem, pr.desconto_pct, l.validado, prod.titulo into r
      from public.promocoes pr join public.produtos prod on prod.id = pr.produto_id
      join public.links_afiliados l on l.promocao_id = pr.id where pr.id = p;
    ok := r.status = 'aprovada' and r.origem = 'manual' and round(r.desconto_pct) = 40 and r.validado = false;
    detalhe := r.status || ' / ' || r.origem || ' / ' || r.desconto_pct || '% / link validado: ' || r.validado;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'link que não é de afiliado da loja é recusado com mensagem clara';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    e := testes.erro_de($q$select public.cadastrar_oferta_manual('mercado_livre', 'Air Fryer', 299.9, 'https://www.mercadolivre.com.br/produto-qualquer')$q$);
    perform testes.sair();
    ok := e like 'P0011%mercadolivre.com%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'mesmo produto com oferta ativa não é cadastrado de novo';
  begin
    u := testes.garantir_dono();
    perform testes.como(u);
    perform public.cadastrar_oferta_manual('mercado_livre', 'Air Fryer', 299.9, 'https://mercadolivre.com/sec/x1', null, null, null, 'MLB123');
    e := testes.erro_de($q$select public.cadastrar_oferta_manual('mercado_livre', 'Air Fryer', 279.9, 'https://mercadolivre.com/sec/x2', null, null, null, 'MLB123')$q$);
    perform testes.sair();
    ok := e like 'P0022%'; detalhe := coalesce(e, 'duplicou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'leitor não cadastra; loja desativada e link sem https são recusados';
  begin
    perform testes.garantir_dono();
    u2 := testes.novo_usuario('leitor@teste.com');
    insert into public.administradores (user_id, papel) values (u2, 'leitor');
    perform testes.como(u2);
    e := testes.erro_de($q$select public.cadastrar_oferta_manual('mercado_livre', 'X', 10, 'https://mercadolivre.com/sec/a')$q$);
    perform testes.sair();
    perform testes.como(testes.garantir_dono());
    ok := e like '42501%'
      and testes.erro_de($q$select public.cadastrar_oferta_manual('loja_inexistente', 'X', 10, 'https://a.com/b')$q$) like 'P0020%'
      and testes.erro_de($q$select public.cadastrar_oferta_manual('mercado_livre', 'X', 10, 'http://mercadolivre.com/sec/a')$q$) like 'P0021%';
    perform testes.sair();
    detalhe := coalesce(e, 'leitor cadastrou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_oferta_manual() from public;
