#!/usr/bin/env bash
# Teste prolongado (soak) em homologação LOCAL: carga mista contínua por DURACAO_MIN minutos.
#   * cliques a TAXA/s (padrão 6/s ≈ 500 mil/dia de média), 4 conexões;
#   * 2 workers consumindo tarefas enfileiradas continuamente;
#   * reservas de orçamento/WhatsApp e mensagens de webhook (com repetições);
#   * agregação (rollup) a cada minuto e manutenção diária a cada 5 minutos.
# Registra duração efetiva, erros, latência por janela de 1 minuto, crescimento da tabela e memória.
# Uso: DURACAO_MIN=20 PGHOST=/tmp/pghomolog PGPORT=54329 homologacao/teste_prolongado.sh [banco_base] [saida.md]
set -uo pipefail
BASE="${1:-homologacao}"; DB="${BASE}_soak"; SAIDA="${2:-/tmp/teste_prolongado.md}"; U="${PGUSER:-postgres}"
MIN="${DURACAO_MIN:-20}"; TAXA="${TAXA:-6}"; SEG=$((MIN * 60))
PGBENCH="$(command -v pgbench || echo /usr/lib/postgresql/16/bin/pgbench)"
P() { psql -U "$U" -d "$DB" -v ON_ERROR_STOP=1 -Atq "$@"; }
dropdb -U "$U" --if-exists "$DB" 2>/dev/null; createdb -U "$U" -T "$BASE" "$DB"
P <<'SQL' >/dev/null
set testes.cadastro_direto = 'sim';
create table testes.soak_erros (em timestamptz default clock_timestamp(), origem text, erro text);
do $$ declare c record; p bigint; pr bigint; l bigint; begin
  select * into c from testes.cenario();
  insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso');
  for g in 2..20 loop
    insert into public.produtos (marketplace_id, id_externo, titulo, preco_atual) values (c.mkt, 'S-' || g, 'Soak ' || g, 100) returning id into p;
    insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status) values (p, 100, 150, 'aprovada') returning id into pr;
    insert into public.links_afiliados (produto_id, promocao_id, url, validado, status_http) values (p, pr, 'https://afiliado.teste.com.br/s/' || g, true, 200) returning id into l;
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (pr, c.canal_real, l, 'sucesso');
  end loop;
end $$;
create or replace function testes.soak_worker(w text) returns int language plpgsql as $$
declare t record; n int := 0;
begin
  perform public.tarefa_enfileirar('vitrine.gerar', '{}', null);
  for t in select * from public.tarefas_pegar(array['vitrine.gerar'], 3, 1, w) loop
    if public.tarefa_concluir(t.id, random() > 0.1, 'falha simulada', null, w) then n := n + 1; end if;
  end loop;
  return n;
exception when others then insert into testes.soak_erros (origem, erro) values ('worker', sqlerrm); return -1;
end $$;
create or replace function testes.soak_extra(i int) returns void language plpgsql as $$
begin
  perform public.orcamento_reservar('soak', 0.01, 1000);
  perform public.whatsapp_processar_mensagem('wamid.S' || (i % 500), '+558199' || lpad((i % 300)::text, 7, '0'),
                                             case when i % 7 = 0 then 'sair' else 'entrar' end, now());
exception when others then insert into testes.soak_erros (origem, erro) values ('extra', sqlerrm);
end $$;
SQL
L=$(P -c "select min(id) - 1 from public.links_afiliados")
cat > /tmp/soak_clique.sql <<EOS
\set link random(1, 20)
\set v random(1, 200000)
SELECT * FROM public.registrar_clique($L + :link, 'teste_real', md5(:v::text) || md5((:v + 1)::text), 'Mozilla/5.0 soak', NULL);
EOS
INICIO=$(date +%s); echo "início: $(date -u +%FT%TZ), duração planejada ${MIN} min"
rm -f /tmp/soak_log.*
"$PGBENCH" -U "$U" -n -c 4 -j 2 -T "$SEG" -R "$TAXA" -l --aggregate-interval=60 --log-prefix=/tmp/soak_log -f /tmp/soak_clique.sql "$DB" > /tmp/soak_pgbench.txt 2>&1 &
PB=$!
( for w in 1 2; do ( while kill -0 $PB 2>/dev/null; do P -c "select testes.soak_worker('w$w')" >/dev/null; sleep 0.5; done ) & done; wait ) &
( i=0; while kill -0 $PB 2>/dev/null; do i=$((i+1)); P -c "select testes.soak_extra($i)" >/dev/null; sleep 0.3; done ) &
( m=0; while kill -0 $PB 2>/dev/null; do sleep 60; m=$((m+1));
    P -c "select public.agregar_cliques(privado.dia_local(now()))" >/dev/null || P -c "insert into testes.soak_erros (origem, erro) values ('rollup', 'falhou')"
    [ $((m % 5)) = 0 ] && { P -c "select public.manutencao_diaria()" >/dev/null || P -c "insert into testes.soak_erros (origem, erro) values ('manutencao', 'falhou')"; }
    echo "$(date +%s) $(P -c "select count(*) from public.cliques") $(P -c "select pg_total_relation_size('public.cliques')") $(ps -C postgres -o rss= | awk '{s+=$1} END {print s}')" >> /tmp/soak_mon.txt
  done ) &
: > /tmp/soak_mon.txt
wait $PB; sleep 2; wait
FIM=$(date +%s)
{
echo "# Teste prolongado (homologação LOCAL, $(nproc) CPUs)"
echo
echo "- Início: $(date -u -d @$INICIO +%FT%TZ) · fim: $(date -u -d @$FIM +%FT%TZ) · **duração efetiva: $(( (FIM-INICIO)/60 )) min $(( (FIM-INICIO)%60 )) s** (planejada: ${MIN} min)"
echo "- Cliques: $(grep -E '^number of transactions actually processed' /tmp/soak_pgbench.txt | sed 's/.*: //'), falhas: $(grep -E '^number of failed' /tmp/soak_pgbench.txt | sed 's/.*: //'), $(grep -E '^latency average' /tmp/soak_pgbench.txt), $(grep -E '^tps' /tmp/soak_pgbench.txt | cut -d' ' -f1-3)"
echo "- Tarefas concluídas pelos workers: $(P -c "select count(*) from public.tarefas where status = 'concluida'") · falhas definitivas: $(P -c "select count(*) from public.tarefas where status = 'falhou'") · pendentes: $(P -c "select count(*) from public.tarefas where status in ('pendente','processando')")"
echo "- Webhook: $(P -c "select count(*) from public.whatsapp_webhook_mensagens") ids únicos processados; inscritos ativos: $(P -c "select count(*) from public.whatsapp_inscritos where descadastrado_em is null")"
echo "- Rollup x brutos hoje: diferença $(P -c "select diferenca from public.verificar_rollup(privado.dia_local(now()))") (após reagregar: $(P -c "select public.agregar_cliques(privado.dia_local(now()))" >/dev/null; P -c "select diferenca from public.verificar_rollup(privado.dia_local(now()))"))"
echo "- Erros registrados pela carga mista: $(P -c "select count(*) from testes.soak_erros") $(P -c "select coalesce(string_agg(distinct origem || ': ' || left(erro, 80), '; '), '')")"
echo
echo "| Minuto | Cliques | Latência média | Latência máx. |"
echo "|---|---|---|---|"
cat /tmp/soak_log.* 2>/dev/null | sort -n | awk -v t0="$INICIO" '{n=$2; if (n>0) printf "| %d | %d | %.1f ms | %.1f ms |\n", int(($1-t0)/60)+1, n, $3/n/1000, $6/1000}'
echo
echo "| Amostra (a cada min) | Linhas em cliques | Tamanho da tabela | RSS Postgres |"
echo "|---|---|---|---|"
awk -v t0="$INICIO" '{printf "| %d min | %d | %.1f MB | %.0f MB |\n", int(($1-t0)/60), $2, $3/1048576, $4/1024}' /tmp/soak_mon.txt
} > "$SAIDA"
dropdb -U "$U" "$DB"; rm -f /tmp/soak_log.* /tmp/soak_clique.sql
cat "$SAIDA"
