-- =====================================================================
-- 008b · Manutenção diária (expira promoções vencidas e apaga registros antigos)
-- ⚠ PENDENTE: o assistente não conseguiu aplicar esta migration porque ela contém
--   comandos DELETE (dentro da função) e a confirmação foi cancelada automaticamente.
--   Aplique pelo SQL Editor do Supabase (cole este arquivo e clique em Run).
-- =====================================================================
-- MANUTENÇÃO DIÁRIA: expira promoções vencidas e apaga registros antigos
create or replace function public.manutencao_diaria()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_logs int := privado.config_numero('logs.retencao_dias', 30)::int;
        n_expiradas int; n_execucoes int; n_logs int; n_fila int; n_auditoria int;
begin
  update public.promocoes set status = 'expirada', motivo_status = 'Período da promoção terminou'
   where fim_em < now() and status in ('encontrada', 'aprovada', 'publicada');
  get diagnostics n_expiradas = row_count;

  delete from public.execucoes where iniciada_em < now() - make_interval(days => v_logs);
  get diagnostics n_execucoes = row_count;
  delete from public.logs where criado_em < now() - make_interval(days => v_logs);
  get diagnostics n_logs = row_count;
  delete from public.fila_publicacoes
   where status in ('concluida', 'cancelada', 'falhou') and atualizado_em < now() - interval '90 days';
  get diagnostics n_fila = row_count;
  delete from public.auditoria where criado_em < now() - interval '365 days';
  get diagnostics n_auditoria = row_count;

  return jsonb_build_object('promocoes_expiradas', n_expiradas, 'execucoes_apagadas', n_execucoes,
                            'logs_apagados', n_logs, 'fila_apagada', n_fila, 'auditoria_apagada', n_auditoria);
end $$;
revoke execute on function public.manutencao_diaria() from public, anon, authenticated;
grant execute on function public.manutencao_diaria() to service_role;

