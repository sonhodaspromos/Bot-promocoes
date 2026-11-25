-- Testes · expansão modular (migration 020)
create or replace function testes.t_expansao_modular()
returns table (teste text, ok boolean, detalhe text)
language plpgsql set search_path = pg_catalog as $$
declare e text; n int; r record; t1 bigint; t2 bigint; v boolean;
begin
  teste := 'todos os recursos novos começam DESLIGADOS';
  begin
    select count(*) into n from public.configuracoes where chave like 'recursos.%' and valor <> 'false'::jsonb;
    ok := n = 0 and (select count(*) from public.configuracoes where chave like 'recursos.%') = 8; detalhe := format('ligados: %s', n);
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'Amazon e AliExpress entram desativados; Magalu é manual (sem API pública de afiliados)';
  begin
    select string_agg(codigo || '=' || modo, ',' order by codigo) into detalhe from public.marketplaces where codigo in ('amazon', 'aliexpress', 'magalu');
    ok := detalhe = 'aliexpress=desativado,amazon=desativado,magalu=manual';
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'tarefas: idempotência pela chave e reserva exclusiva (SKIP LOCKED)';
  begin
    t1 := public.tarefa_enfileirar('vitrine.gerar', '{}', 'vitrine-2026-11-25');
    t2 := public.tarefa_enfileirar('vitrine.gerar', '{}', 'vitrine-2026-11-25');
    select count(*) into n from public.tarefas_pegar(array['vitrine.gerar'], 10, 5, 'w1');
    ok := t1 is not null and t2 is null and n = 1
          and (select count(*) from public.tarefas_pegar(array['vitrine.gerar'], 10, 5, 'w2')) = 0;
    detalhe := format('t1=%s t2=%s pegas=%s', t1, t2, n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'tarefas: falha volta com espera crescente; esgotou as tentativas, falhou; worker errado não conclui';
  begin
    t1 := public.tarefa_enfileirar('alertas.processar', '{}', null);
    update public.tarefas set max_tentativas = 2 where id = t1;
    perform public.tarefas_pegar(array['alertas.processar'], 1, 5, 'w1');
    v := public.tarefa_concluir(t1, false, 'erro de rede', null, 'w2');          -- outro worker: recusado
    ok := v = false;
    perform public.tarefa_concluir(t1, false, 'erro de rede', null, 'w1');
    select status, agendada_para > now() as espera into r from public.tarefas where id = t1;
    ok := ok and r.status = 'pendente' and r.espera;
    update public.tarefas set agendada_para = now() where id = t1;
    perform public.tarefas_pegar(array['alertas.processar'], 1, 5, 'w1');
    perform public.tarefa_concluir(t1, false, 'erro de novo', null, 'w1');
    select status into r from public.tarefas where id = t1;
    ok := ok and r.status = 'falhou'; detalhe := r.status;
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'WhatsApp: telefone em formato internacional, descadastro registra evento e é imediato';
  begin
    e := testes.erro_de($q$insert into public.whatsapp_inscritos (telefone, consentimento_em, consentimento_origem, texto_consentimento)
                           values ('81999990000', now(), 'formulario', 'Quero receber ofertas')$q$);
    insert into public.whatsapp_inscritos (telefone, consentimento_em, consentimento_origem, texto_consentimento)
      values ('+5581999990000', now(), 'mensagem_recebida', 'Quero receber ofertas pelo WhatsApp');
    v := public.whatsapp_descadastrar('+5581999990000');
    select count(*) into n from public.whatsapp_eventos where tipo = 'optout';
    ok := e like '23514%' and v and n = 1 and not public.whatsapp_descadastrar('+5581999990000');
    detalhe := format('%s / %s', coalesce(e, 'aceitou'), n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'dados pessoais (WhatsApp, alertas) fechados: anon nada; painel só o dono lê telefones';
  begin
    ok := not has_table_privilege('anon', 'public.whatsapp_inscritos', 'select')
      and not has_table_privilege('anon', 'public.alertas_preco', 'select')
      and not has_table_privilege('authenticated', 'public.whatsapp_inscritos', 'insert')
      and not has_function_privilege('authenticated', 'public.tarefas_pegar(text[], integer, integer, text)', 'execute')
      and not has_function_privilege('authenticated', 'public.whatsapp_descadastrar(text)', 'execute');
    detalhe := case when ok then 'fechado' else 'acesso indevido' end;
  exception when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'manutenção apaga telefone de quem saiu há mais de 30 dias';
  begin
    insert into public.whatsapp_inscritos (telefone, consentimento_em, consentimento_origem, texto_consentimento, descadastrado_em)
      values ('+5581988887777', now() - interval '60 days', 'formulario', 'Quero receber ofertas', now() - interval '31 days');
    r := null;
    perform privado.limpar_expansao();
    select count(*) into n from public.whatsapp_inscritos where telefone = '+5581988887777';
    ok := n = 0; detalhe := format('restantes: %s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
  teste := 'WhatsApp: destinatários respeitam descadastro, categoria, reenvio e limite diário';
  begin
    select * into r from testes.cenario();
    t1 := public.whatsapp_inscrever('+5581911112222', 'mensagem_recebida', 'QUERO OFERTAS — aceito receber ofertas');
    t2 := public.whatsapp_inscrever('+5581933334444', 'formulario', 'Aceito receber ofertas de Casa', array[999]);
    perform public.whatsapp_inscrever('+5581955556666', 'formulario', 'Aceito receber ofertas de tudo');
    perform public.whatsapp_descadastrar('+5581955556666');
    select count(*) into n from public.whatsapp_destinatarios(null, 3, r.promo);
    ok := n = 1;                                                  -- só o 1º (o 2º só quer a categoria 999; o 3º saiu)
    v := public.whatsapp_reservar(t1, r.promo);
    ok := ok and v and not public.whatsapp_reservar(t1, r.promo)
          and (select count(*) from public.whatsapp_destinatarios(null, 3, r.promo)) = 0;
    detalhe := format('destinatários=%s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;

  teste := 'vitrine: só ofertas reais publicadas com link validado; "De" só com desconto comprovado';
  begin
    select * into r from testes.cenario();
    update public.promocoes set status = 'publicada', publicada_em = now(),
           analise = '{"desconto_comprovado": false, "teste": true}'::jsonb where id = r.promo;
    select count(*), max(preco_referencia) into n, detalhe from public.ofertas_vitrine(7);
    ok := n = 1 and detalhe is null;
    update public.promocoes set demo = true where id = r.promo;
    ok := ok and (select count(*) from public.ofertas_vitrine(7)) = 0;
    detalhe := format('ofertas=%s', n);
    raise exception using errcode = 'TT000';
  exception when sqlstate 'TT000' then null; when others then ok := false; detalhe := sqlerrm; end;
  return next;
end $$;
revoke execute on function testes.t_expansao_modular() from public;
