#!/usr/bin/env bash
# Homologação local (NUNCA aponte para produção): concorrência da fila, parada de emergência,
# interrupção no meio da publicação e backup + restauração completa (usuários, permissões, RLS, cofre).
# Uso: PGHOST=/tmp/pghomolog PGPORT=54329 homologacao/concorrencia_e_backup.sh [banco_ja_migrado]
set -euo pipefail
BASE="${1:-homologacao}"; DB="${BASE}_operacao"; REST="${BASE}_restaurado"; U="${PGUSER:-postgres}"
P() { psql -U "$U" -d "$1" -v ON_ERROR_STOP=1 -Atq "${@:2}"; }
falhas=0; ok() { echo "  ✔ $1"; }; erro() { echo "  ✖ $1"; falhas=$((falhas+1)); }
P "$BASE" -c "select 1 from pg_namespace where nspname='auth' and exists(select 1 from pg_tables where schemaname='auth' and tablename='flow_state')" | grep -q 1 \
  && { echo "Banco parece ser um Supabase REAL: abortado."; exit 3; }

dropdb -U "$U" --if-exists "$DB"; createdb -U "$U" -T "$BASE" "$DB"   # cópia: o banco base continua limpo
echo "Preparando 20 itens na fila (dados de teste, commitados na cópia)…"
P "$DB" <<'SQL'
set testes.cadastro_direto = 'sim';
select testes.garantir_dono();
do $$ declare c record; p bigint; pr bigint; l bigint; begin
  select * into c from testes.cenario();
  update public.canais set intervalo_minimo_min = 0, limite_diario = null where id = c.canal_real;
  insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, agendado_para)
    values (c.promo, c.canal_real, c.link, now() - interval '30 seconds');
  for g in 2..20 loop
    insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual, preco_original)
      values (c.mkt, 'T-' || g, 'Produto ' || g, 100, 150) returning id into p;
    insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status) values (p, 100, 150, 'aprovada') returning id into pr;
    insert into public.links_afiliados (produto_id, promocao_id, url, validado, status_http)
      values (p, pr, 'https://afiliado.teste.com.br/p/' || g, true, 200) returning id into l;
    perform testes.classificar(pr);
    insert into public.fila_publicacoes (promocao_id, canal_id, link_afiliado_id, agendado_para)
      values (pr, c.canal_real, l, now() - make_interval(secs => g));
  end loop;
end $$;
SQL

echo "1) Dois workers ao mesmo tempo (FOR UPDATE SKIP LOCKED):"
W='begin; select id from public.pegar_proximas_publicacoes(15, 10); select pg_sleep(1.5); commit;'
P "$DB" -c "$W" > /tmp/w1.txt & P "$DB" -c "$W" > /tmp/w2.txt & wait
grep -E '^[0-9]+$' /tmp/w1.txt | sort > /tmp/a; grep -E '^[0-9]+$' /tmp/w2.txt | sort > /tmp/b
dup=$(comm -12 /tmp/a /tmp/b | wc -l); tot=$(( $(wc -l < /tmp/a) + $(wc -l < /tmp/b) ))
[ "$dup" -eq 0 ] && ok "nenhum item pego pelos dois workers (w1=$(wc -l < /tmp/a), w2=$(wc -l < /tmp/b))" || erro "$dup item(ns) duplicados"
[ "$tot" -eq 20 ] && ok "todos os 20 itens distribuídos uma única vez" || erro "distribuídos $tot de 20"

echo "2) Interrupção no meio (worker morreu com o item em 'processando'):"
P "$DB" -c "update public.fila_publicacoes set bloqueado_ate = now() - interval '1 minute' where status='processando' and id = (select min(id) from public.fila_publicacoes)"
n=$(P "$DB" -c "select count(*) from public.pegar_proximas_publicacoes(50, 10) where tentativas = 2")
[ "$n" -eq 1 ] && ok "item travado volta para a fila depois do bloqueio (2ª tentativa)" || erro "item travado não voltou ($n)"
P "$DB" -c "update public.fila_publicacoes set bloqueado_ate = now() - interval '1 minute', tentativas = max_tentativas where id = (select min(id) from public.fila_publicacoes)"
P "$DB" -c "select count(*) from public.pegar_proximas_publicacoes(50, 10)" >/dev/null
s=$(P "$DB" -c "select status from public.fila_publicacoes where id = (select min(id) from public.fila_publicacoes)")
[ "$s" = "falhou" ] && ok "sem tentativas restantes vira 'falhou' (não reenvia sem fim)" || erro "status $s"

echo "2b) Reserva do envio disputada por dois processos (migration 018):"
P "$DB" -c "update public.fila_publicacoes set status='processando', bloqueado_ate=now()+interval '10 minutes', publicando_desde=null where id = (select max(id) from public.fila_publicacoes)"
R='select public.fila_reservar_envio((select max(id) from public.fila_publicacoes));'
P "$DB" -c "$R" > /tmp/r1.txt & P "$DB" -c "$R" > /tmp/r2.txt & wait
n=$(cat /tmp/r1.txt /tmp/r2.txt | grep -c '^t$')
[ "$n" -eq 1 ] && ok "só um processo consegue reservar o mesmo envio" || erro "$n processos reservaram o mesmo envio"

echo "3) Parada de emergência bloqueia a fila:"
P "$DB" -c "update public.fila_publicacoes set status='pendente', bloqueado_ate=null, tentativas=0 where status <> 'falhou'"
P "$DB" <<'SQL'
select set_config('request.jwt.claims', json_build_object('sub', (select user_id from public.administradores where papel='dono' limit 1), 'role','authenticated','aal','aal2')::text, false);
set role authenticated; select public.ativar_parada_emergencia('teste de homologação'); reset role;
SQL
n=$(P "$DB" -c "select count(*) from public.pegar_proximas_publicacoes(50, 10)")
[ "$n" -eq 0 ] && ok "com a parada ativa nenhum item sai" || erro "$n itens saíram com a parada ativa"

echo "4) Backup completo e restauração em banco isolado:"
P "$DB" -c "select vault.create_secret('segredo-de-homologacao', 'teste_backup', 'credencial de teste')" >/dev/null
pg_dump -U "$U" -Fc "$DB" -f /tmp/backup_homologacao.dump
dropdb -U "$U" --if-exists "$REST"; createdb -U "$U" "$REST"
pg_restore -U "$U" -d "$REST" --exit-on-error /tmp/backup_homologacao.dump && ok "pg_restore sem erros ($(stat -c %s /tmp/backup_homologacao.dump) bytes)" || erro "pg_restore falhou"
Q="select string_agg(x, '|' order by x) from (
     select 'T:'||schemaname||'.'||tablename||':'||rowsecurity from pg_tables where schemaname in ('public','privado','auth','vault')
     union all select 'C:'||table_schema||'.'||table_name||':'||(select count(*) from information_schema.columns c where c.table_schema=t.table_schema and c.table_name=t.table_name) from information_schema.tables t where table_schema='public'
     union all select 'P:'||schemaname||'.'||tablename||':'||policyname from pg_policies
     union all select 'G:'||grantee||':'||table_schema||'.'||table_name||':'||privilege_type from information_schema.role_table_grants where table_schema in ('public','privado')
     union all select 'F:'||n.nspname||'.'||p.proname||':'||p.prosecdef||':'||coalesce(array_to_string(p.proacl,','),'') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','privado')
     union all select 'R:'||tgname from pg_trigger where not tgisinternal) y(x)"
A=$(P "$DB" -c "$Q"); B=$(P "$REST" -c "$Q")
[ ${#A} -gt 1000 ] && [ "$A" = "$B" ] && ok "tabelas, RLS, políticas, permissões, funções SECURITY DEFINER e gatilhos idênticos ($(echo "$A" | tr '|' '\n' | wc -l) itens comparados)" || erro "estrutura/permissões diferentes após restaurar"
for t in auth.users public.administradores public.convites public.configuracoes public.fila_publicacoes public.historico_publicacoes public.comissoes; do
  [ "$(P "$DB" -c "select count(*) from $t")" = "$(P "$REST" -c "select count(*) from $t")" ] || erro "contagem diferente em $t"
done && ok "usuários, administradores e dados restaurados com as mesmas contagens"
v=$(P "$REST" -c "select decrypted_secret from vault.decrypted_secrets where name='teste_backup'")
[ "$v" = "segredo-de-homologacao" ] && ok "credencial do cofre recuperável no banco restaurado (com a mesma chave do cofre)" || erro "credencial não recuperada"
dropdb -U "$U" "$REST"
pg_dump -U "$U" -Fc "$BASE" -f /tmp/backup_base.dump; createdb -U "$U" "$REST"; pg_restore -U "$U" -d "$REST" --exit-on-error /tmp/backup_base.dump
r=$(P "$REST" -c "select count(*) filter (where not ok) || '/' || count(*) from testes.rodar()")
[ "${r%%/*}" -eq 0 ] && ok "bateria de testes do banco passa inteira no banco restaurado (${r#*/} testes)" || erro "$r teste(s) falharam no restaurado"
dropdb -U "$U" "$REST"; dropdb -U "$U" "$DB"; rm -f /tmp/backup_homologacao.dump /tmp/backup_base.dump
echo; [ $falhas -eq 0 ] && echo "CONCORRÊNCIA E BACKUP: TUDO OK" || { echo "CONCORRÊNCIA E BACKUP: $falhas falha(s)"; exit 1; }
