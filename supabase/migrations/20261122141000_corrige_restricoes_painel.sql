-- =====================================================================
-- 010 · Correção encontrada pelos testes de integridade
-- O gatilho compartilhado lia "new.validado" também na tabela promocoes
-- (PL/pgSQL não garante curto-circuito no AND). Agora cada tabela tem seu bloco.
-- =====================================================================
create or replace function privado.restricoes_do_painel()
returns trigger language plpgsql set search_path = '' as $$
begin
  if (select auth.uid()) is null then
    return new;
  end if;
  if tg_table_name = 'links_afiliados' then
    if new.validado and (tg_op = 'INSERT' or not old.validado or new.url is distinct from old.url) then
      raise exception 'A validação do link de afiliado é feita automaticamente pelo sistema.' using errcode = 'P0016';
    end if;
  elsif tg_table_name = 'promocoes' then
    if new.status = 'publicada' and (tg_op = 'INSERT' or old.status <> 'publicada') then
      raise exception 'Só o sistema marca uma oferta como publicada (depois de publicar de verdade).' using errcode = 'P0017';
    end if;
  end if;
  return new;
end $$;
