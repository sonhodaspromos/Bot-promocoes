#!/usr/bin/env bash
# Concorrência REAL (várias conexões ao mesmo tempo) dos módulos da expansão. Só homologação local.
# Uso: PGHOST=/tmp/pghomolog PGPORT=54329 homologacao/concorrencia_expansao.sh [banco_base]
set -uo pipefail
BASE="${1:-homologacao}"; DB="${BASE}_conc_exp"; U="${PGUSER:-postgres}"; FALHOU=0
P() { psql -U "$U" -d "$DB" -v ON_ERROR_STOP=1 -Atq "$@"; }
ok() { echo "  ✔ $1"; }; nok() { echo "  ✖ $1"; FALHOU=1; }
dropdb -U "$U" --if-exists "$DB"; createdb -U "$U" -T "$BASE" "$DB"
P <<'SQL'
create table testes.processadas (tarefa bigint, worker text, em timestamptz default clock_timestamp());
create or replace function testes.processar_lote(p_worker text) returns int language plpgsql as $$
declare t record; n int := 0;
begin
  for t in select * from public.tarefas_pegar(array['vitrine.gerar'], 5, 1, p_worker) loop
    insert into testes.processadas (tarefa, worker) values (t.id, p_worker);
    perform pg_sleep(0.002);
    if public.tarefa_concluir(t.id, true, null, null, p_worker) then n := n + 1; end if;
  end loop;
  return n;
end $$;
select count(public.tarefa_enfileirar('vitrine.gerar', jsonb_build_object('i', g), 'carga-' || g)) from generate_series(1, 600) g;
SQL

echo "1. 8 workers simultâneos consumindo 600 tarefas"
for w in $(seq 1 8); do
  ( while [ "$(P -c "select testes.processar_lote('w$w')")" != "0" ]; do :; done ) &
done; wait
R=$(P -c "select count(*) || '|' || count(distinct tarefa) || '|' || count(distinct worker) from testes.processadas")
C=$(P -c "select count(*) from public.tarefas where tipo = 'vitrine.gerar' and status = 'concluida'")
IFS='|' read -r tot dist ws <<< "$R"
[ "$tot" = 600 ] && [ "$dist" = 600 ] && [ "$C" = 600 ] && ok "600 processadas, 0 duplicadas, $ws workers participaram" || nok "total=$tot distintas=$dist concluídas=$C"

echo "2. Worker interrompido no meio (conexão encerrada sem concluir)"
T=$(P -c "select public.tarefa_enfileirar('alertas.processar', '{}', 'interrompida')")
P -c "select count(*) from public.tarefas_pegar(array['alertas.processar'], 1, 1, 'morto')" >/dev/null &
sleep 0.5; wait
P -c "update public.tarefas set bloqueada_ate = now() - interval '1 second' where id = $T"   # o prazo da trava passou
R=$(P -c "select string_agg(worker || ':' || tentativas, ',') from public.tarefas_pegar(array['alertas.processar'], 1, 1, 'novo')")
L=$(P -c "select public.tarefa_concluir($T, true, null, null, 'morto')")
N=$(P -c "select public.tarefa_concluir($T, true, null, null, 'novo')")
[ "$R" = "novo:2" ] && [ "$L" = f ] && [ "$N" = t ] && ok "tarefa abandonada foi retomada (tentativa 2); conclusão tardia do worker morto recusada" || nok "retomada=$R tardia=$L novo=$N"

echo "3. Renovação de trava concorrendo com a retomada"
T=$(P -c "select public.tarefa_enfileirar('alertas.processar', '{}', 'renovar')")
P -c "select count(*) from public.tarefas_pegar(array['alertas.processar'], 1, 1, 'dono')" >/dev/null
( for i in $(seq 1 20); do P -c "select public.tarefa_renovar($T, 'dono', 1)" >/dev/null; done ) &
( for i in $(seq 1 20); do P -c "select count(*) from public.tarefas_pegar(array['alertas.processar'], 1, 1, 'ladrao')" >/dev/null; done ) &
wait
W=$(P -c "select worker from public.tarefas where id = $T")
[ "$W" = dono ] && ok "trava renovada nunca foi tomada por outro worker" || nok "worker atual=$W"

echo "4. Orçamento: 40 reservas simultâneas de US\$ 0,0625 com teto de US\$ 1,00"
for i in $(seq 1 40); do P -c "select public.orcamento_reservar('conc', 0.0625, 1.0)" > /tmp/orc_$i.txt & done; wait
S=$(cat /tmp/orc_*.txt | grep -c '^t$'); G=$(P -c "select gasto_usd from public.orcamento_consumo where servico = 'conc'"); rm -f /tmp/orc_*.txt
[ "$S" = 16 ] && [ "$G" = "1.0000" ] && ok "exatamente 16 aceitas, gasto US\$ $G (nunca passou do teto)" || nok "aceitas=$S gasto=$G"

echo "5. WhatsApp: 12 reservas simultâneas para a mesma pessoa (limite 3/dia), ofertas diferentes"
P <<'SQL' >/dev/null
set testes.cadastro_direto = 'sim';
create table testes.promos as
with c as (select * from testes.cenario())
select c.promo as id from c union all select (testes.outra_oferta(c.mkt)).promo from c, generate_series(1, 11);
select public.whatsapp_inscrever('+5581900001111', 'formulario', 'Quero receber ofertas pelo WhatsApp');
SQL
I=$(P -c "select id from public.whatsapp_inscritos where telefone = '+5581900001111'")
for p in $(P -c "select id from testes.promos"); do P -c "select public.whatsapp_reservar_envio($I, $p, 0.0625, 100, 3)" > /tmp/wa_$p.txt & done; wait
OKS=$(cat /tmp/wa_*.txt | grep -c '^ok$'); LIM=$(cat /tmp/wa_*.txt | grep -c '^limite_pessoa$'); rm -f /tmp/wa_*.txt
[ "$OKS" = 3 ] && [ "$LIM" = 9 ] && ok "3 reservas aceitas, 9 recusadas pelo limite por pessoa" || nok "ok=$OKS limite=$LIM"

echo "6. Webhook: a mesma mensagem entregue 10 vezes ao mesmo tempo"
for i in $(seq 1 10); do P -c "select public.whatsapp_processar_mensagem('wamid.CONC', '+5581922223333', 'entrar', now())" > /tmp/wh_$i.txt & done; wait
INS=$(cat /tmp/wh_*.txt | grep -c '^inscrito$'); DUP=$(cat /tmp/wh_*.txt | grep -c '^duplicada$'); rm -f /tmp/wh_*.txt
E=$(P -c "select count(*) from public.whatsapp_eventos e join public.whatsapp_inscritos i on i.id = e.inscrito_id where i.telefone = '+5581922223333'")
[ "$INS" = 1 ] && [ "$DUP" = 9 ] && [ "$E" = 1 ] && ok "processada 1 vez; 9 entregas repetidas ignoradas; 1 evento de opt-in" || nok "inscrito=$INS duplicada=$DUP eventos=$E"

dropdb -U "$U" "$DB"
[ $FALHOU = 0 ] && echo "CONCORRÊNCIA DA EXPANSÃO: TUDO OK" || { echo "CONCORRÊNCIA DA EXPANSÃO: FALHOU"; exit 1; }
