-- Testes · marketplaces, categorias, produtos e histórico de preços
create or replace function testes.t_catalogo()
returns table (teste text, ok boolean, detalhe text)
language plpgsql as $$
declare b record; e text; n int; v numeric; m2 smallint; t timestamptz;
begin
  teste := 'marketplace com código inválido é recusado';
  begin
    e := testes.erro_de($q$insert into public.marketplaces (codigo, nome) values ('Shopee BR!', 'X')$q$);
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'domínio de afiliado com protocolo/maiúsculas é recusado';
  begin
    e := testes.erro_de($q$insert into public.marketplaces (codigo, nome, dominios_afiliado) values ('loja_x', 'Loja X', array['https://S.Shopee.com.br'])$q$);
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'categoria com nome repetido (maiúsculas/espaços) é recusada';
  begin
    insert into public.categorias (nome, slug) values ('Beleza Teste', 'beleza-teste');
    e := testes.erro_de($q$insert into public.categorias (nome, slug) values (' beleza teste ', 'beleza-teste-2')$q$);
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'categoria não pode ser pai dela mesma';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format('update public.categorias set categoria_pai_id = id where id = %s', b.cat));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'produto duplicado (mesmo marketplace + id externo) é recusado';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format($q$insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual) values (%s, 'T-1', 'Cópia', 10)$q$, b.mkt));
    ok := e like '23505%'; detalhe := coalesce(e, 'aceitou duplicado');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'mesmo id externo em outro marketplace é aceito';
  begin
    select * into b from testes.base();
    insert into public.marketplaces (codigo, nome) values ('teste_mkt2', 'Loja Teste 2') returning id into m2;
    e := testes.erro_de(format($q$insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual) values (%s, 'T-1', 'Outro', 10)$q$, m2));
    ok := e is null; detalhe := coalesce(e, 'aceito');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'preço zero ou negativo é recusado';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format('update public.produtos set preco_atual = 0 where id = %s', b.prod));
    ok := e like '23514%'; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'nota acima de 5 é recusada';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format('update public.produtos set nota = 5.5 where id = %s', b.prod));
    ok := e is not null; detalhe := coalesce(e, 'aceitou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'histórico de preço é criado junto com o produto';
  begin
    select * into b from testes.base();
    select count(*) into n from public.historico_precos where produto_id = b.prod;
    ok := n = 1; detalhe := n || ' registro(s)';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'mudança de preço gera histórico com a variação (%)';
  begin
    select * into b from testes.base();
    update public.produtos set preco_atual = 80 where id = b.prod;
    select variacao_pct into v from public.historico_precos where produto_id = b.prod order by id desc limit 1;
    select count(*) into n from public.historico_precos where produto_id = b.prod;
    ok := n = 2 and v = -20; detalhe := n || ' registros, variação ' || v || '%';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'atualização sem mudar preço não gera histórico';
  begin
    select * into b from testes.base();
    update public.produtos set titulo = 'Novo título' where id = b.prod;
    select count(*) into n from public.historico_precos where produto_id = b.prod;
    ok := n = 1; detalhe := n || ' registro(s)';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'produto que fica indisponível gera histórico';
  begin
    select * into b from testes.base();
    update public.produtos set disponivel = false where id = b.prod;
    select count(*) into n from public.historico_precos where produto_id = b.prod and not disponivel;
    ok := n = 1; detalhe := n || ' registro(s) de indisponível';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'histórico de preços não pode ser alterado';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format('update public.historico_precos set preco = 1 where produto_id = %s', b.prod));
    ok := e like 'P0002%'; detalhe := coalesce(e, 'alterou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'marketplace com produtos não pode ser apagado';
  begin
    select * into b from testes.base();
    e := testes.erro_de(format('delete from public.marketplaces where id = %s', b.mkt));
    ok := e like '23503%'; detalhe := coalesce(e, 'apagou');
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'apagar produto apaga o histórico dele (cascata)';
  begin
    select * into b from testes.base();
    delete from public.produtos where id = b.prod;
    select count(*) into n from public.historico_precos where produto_id = b.prod;
    ok := n = 0; detalhe := n || ' registro(s) restantes';
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'apagar categoria deixa o produto sem categoria (não apaga o produto)';
  begin
    select * into b from testes.base();
    delete from public.categorias where id = b.cat;
    ok := exists (select 1 from public.produtos where id = b.prod and categoria_id is null);
    detalhe := case when ok then 'produto mantido' else 'falhou' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'produto de marketplace demo é marcado como demo';
  begin
    insert into public.marketplaces (codigo, nome, modo) values ('teste_demo', 'Demo', 'demo') returning id into m2;
    insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual, demo) values (m2, 'D1', 'Produto demo', 10, false);
    ok := (select demo from public.produtos where marketplace_id = m2 and id_externo = 'D1');
    detalhe := case when ok then 'demo = true' else 'demo = false' end;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'atualizado_em do produto é automático';
  begin
    select * into b from testes.base();
    update public.produtos set atualizado_em = now() - interval '3 days' where id = b.prod;
    update public.produtos set marca = 'Marca' where id = b.prod;
    select atualizado_em into t from public.produtos where id = b.prod;
    ok := t >= now() - interval '1 minute'; detalhe := t::text;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
