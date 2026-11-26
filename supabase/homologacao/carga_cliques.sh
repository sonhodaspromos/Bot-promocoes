#!/usr/bin/env bash
# Teste de carga progressivo do caminho do clique (registrar_clique) e das consultas do painel,
# para 1.000, 10.000, 100.000 e 500.000 cliques por dia. Roda SÓ em homologação local (nunca em produção).
#
# Premissas (documentadas no relatório):
#   * hora de pico = 15% dos cliques do dia; rajada = 3× a média da hora de pico;
#   * o teste roda a 5× a rajada (folga) por DURACAO segundos, com 8 conexões;
#   * 70% de visitantes únicos, 30% repetidos (exercita a deduplicação de 24 h);
#   * volume: 30 dias de cliques já gravados (30 mil / 300 mil / 3 milhões / 15 milhões de linhas);
#   * mede p50/p95/p99 (log por transação do pgbench), CPU da máquina, memória (RSS) dos processos do
#     Postgres, conexões abertas e taxa de erro; compara o painel lendo os brutos x o rollup diário.
# Uso: PGHOST=/tmp/pghomolog PGPORT=54329 homologacao/carga_cliques.sh [banco_base] [saida.md]
set -euo pipefail
BASE="${1:-homologacao}"; DB="${BASE}_carga"; SAIDA="${2:-/tmp/carga_cliques.md}"; U="${PGUSER:-postgres}"
DURACAO="${DURACAO:-20}"
P() { psql -U "$U" -d "$DB" -v ON_ERROR_STOP=1 -Atq "$@"; }
PGBENCH="$(command -v pgbench || echo /usr/lib/postgresql/16/bin/pgbench)"
psql -U "$U" -d "$BASE" -Atqc "select 1 from pg_tables where schemaname='auth' and tablename='flow_state'" | grep -q 1 \
  && { echo "Banco parece Supabase REAL: abortado."; exit 3; }
dropdb -U "$U" --if-exists "$DB"; createdb -U "$U" -T "$BASE" "$DB"

echo "Preparando 50 ofertas publicadas em canal real…"
P <<'SQL'
set testes.cadastro_direto = 'sim';
do $$ declare c record; p bigint; pr bigint; l bigint; begin
  select * into c from testes.cenario();
  update public.promocoes set status = 'publicada', publicada_em = now() where id = c.promo;
  insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (c.promo, c.canal_real, c.link, 'sucesso');
  for g in 2..50 loop
    insert into public.produtos (marketplace_id, id_externo, categoria_id, titulo, preco_atual, preco_original)
      values (c.mkt, 'C-' || g, (select id from public.categorias limit 1), 'Produto carga ' || g, 100, 150) returning id into p;
    insert into public.promocoes (produto_id, preco_promocional, preco_referencia, status) values (p, 100, 150, 'aprovada') returning id into pr;
    insert into public.links_afiliados (produto_id, promocao_id, url, validado, status_http)
      values (p, pr, 'https://afiliado.teste.com.br/c/' || g, true, 200) returning id into l;
    perform testes.classificar(pr);
    update public.promocoes set status = 'publicada', publicada_em = now() where id = pr;
    insert into public.historico_publicacoes (promocao_id, canal_id, link_afiliado_id, resultado) values (pr, c.canal_real, l, 'sucesso');
  end loop;
end $$;
SQL
LINK_MIN=$(P -c "select min(id) - 1 from public.links_afiliados")
cat > /tmp/clique.sql <<EOS
\set link random(1, 50)
\set v random(1, :visitantes)
SELECT * FROM public.registrar_clique(:linkbase + :link, 'teste_real', md5(:v::text) || md5((:v + 1)::text), 'Mozilla/5.0 (Linux; Android 14) carga', NULL);
EOS

{
echo "# Teste de carga dos cliques ($(date -u +%Y-%m-%dT%H:%MZ))"
echo
echo "Ambiente: PostgreSQL $(P -c 'show server_version' | cut -d' ' -f1) LOCAL, $(nproc) CPUs, $(free -g | awk '/Mem/{print $2}') GB RAM, shared_buffers $(P -c 'show shared_buffers'). Não é a infraestrutura do Supabase."
echo
echo "| Cenário | Alvo/s | Atingido/s | p50 | p95 | p99 | Erros | CPU média | RSS Postgres (pico) | Conexões (pico) | Linhas 30 d | Tabela+índices | Painel 30 d (brutos) | Cliques/dia 30 d (brutos) | Cliques/dia 30 d (rollup) | Rollup 30 dias (reprocessar) | Diferença rollup×brutos | Manutenção |"
echo "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
} > "$SAIDA"

tempo() { P -c "\timing on" -c "$1" 2>&1 | sed -n 's/^Time: \([0-9.]*\) ms.*/\1/p' | tail -1; }
cpu() { awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; }

for DIA in ${CENARIOS:-1000 10000 100000 500000}; do
  ALVO=$(python3 -c "print(max(1, round($DIA*0.15/3600*3*5)))")
  VIS=$(python3 -c "print(max(10, int($DIA*0.7)))")
  echo "== $DIA cliques/dia: pré-carga de 30 dias ($((DIA*30)) linhas) e $ALVO cliques/s por ${DURACAO}s"
  P -c "truncate public.cliques, public.cliques_suspeitos, public.cliques_diarios, public.cliques_agregacao"
  P -c "drop index if exists public.cliques_promocao_idx, public.cliques_link_idx, public.cliques_canal_idx, public.cliques_publicacao_idx, public.cliques_data_brin, public.cliques_ip_idx, public.cliques_publicacao_data_idx"
  # carga em ordem de tempo (como na vida real: o BRIN funciona); índices recriados depois (mais rápido)
  P -c "insert into public.cliques (link_afiliado_id, promocao_id, canal_id, publicacao_id, ip_hash, user_agent, ocorrido_em)
        select l.id, l.promocao_id, h.canal_id, h.id, md5(g::text) || md5((g * 7)::text), 'Mozilla/5.0 carga',
               now() - interval '30 days' + (g::double precision / ($DIA * 30)) * interval '30 days' - interval '1 minute'
          from generate_series(1, $DIA * 30) g
          join lateral (select id, promocao_id from public.links_afiliados where id = $LINK_MIN + 1 + (g % 50)) l on true
          join lateral (select id, canal_id from public.historico_publicacoes where link_afiliado_id = l.id limit 1) h on true"
  P -c "create index cliques_promocao_idx on public.cliques (promocao_id, ocorrido_em desc);
        create index cliques_link_idx on public.cliques (link_afiliado_id, ocorrido_em desc);
        create index cliques_canal_idx on public.cliques (canal_id, ocorrido_em desc);
        create index cliques_publicacao_idx on public.cliques (publicacao_id);
        create index cliques_data_brin on public.cliques using brin (ocorrido_em);
        create index cliques_ip_idx on public.cliques (ip_hash, ocorrido_em desc) where ip_hash is not null;
        create index cliques_publicacao_data_idx on public.cliques (publicacao_id, ocorrido_em);"
  P -c "vacuum analyze public.cliques"   # estado estável (autovacuum em dia)
  T_ROLL=$(tempo "select public.agregar_cliques_periodo(privado.dia_local(now()) - 31, privado.dia_local(now()) - 1)")
  DIF=$(P -c "select coalesce(sum(abs(diferenca)), 0) from generate_series(privado.dia_local(now()) - 31, privado.dia_local(now()) - 1, interval '1 day') d, lateral public.verificar_rollup(d::date)")

  # monitor: CPU, RSS do Postgres e conexões a cada 1 s
  MON=/tmp/mon_$DIA.txt; : > $MON
  ( while :; do echo "$(ps -C postgres -o rss= | awk '{s+=$1} END {print s}') $(P -c 'select count(*) from pg_stat_activity where datname is not null')" >> $MON; sleep 1; done ) &
  MONPID=$!
  read T0 I0 <<< "$(cpu)"
  rm -f /tmp/pgbench_log.*
  RES=$("$PGBENCH" -U "$U" -n -c 8 -j 4 -T "$DURACAO" -R "$ALVO" -l --log-prefix=/tmp/pgbench_log -D linkbase="$LINK_MIN" -D visitantes="$VIS" -f /tmp/clique.sql "$DB" 2>&1 || true)
  read T1 I1 <<< "$(cpu)"
  kill $MONPID 2>/dev/null || true; wait $MONPID 2>/dev/null || true
  CPU=$(python3 -c "t=$T1-$T0; print(f'{100*(t-($I1-$I0))/t:.0f}%' if t else '?')")
  RSS=$(awk 'BEGIN{m=0} {if ($1>m) m=$1} END {printf "%.0f MB", m/1024}' $MON)
  CON=$(awk 'BEGIN{m=0} {if ($2>m) m=$2} END {print m}' $MON)
  PCT=$(cat /tmp/pgbench_log.* | python3 -c "
import sys
v=sorted(int(l.split()[2]) for l in sys.stdin if l.strip())
p=lambda q: v[min(len(v)-1, int(q*len(v)))]/1000 if v else float('nan')
print(f'{p(.50):.1f} ms|{p(.95):.1f} ms|{p(.99):.1f} ms|{len(v)}')")
  IFS='|' read -r P50 P95 P99 NTX <<< "$PCT"
  TPS=$(echo "$RES" | sed -n 's/^tps = \([0-9.]*\).*/\1/p' | head -1)
  FALHAS=$(echo "$RES" | sed -n 's/^number of failed transactions: \([0-9]*\).*/\1/p' | head -1)
  ERRO=$(python3 -c "n=${NTX:-0}; f=${FALHAS:-0}; print(f'{100*f/(n+f):.2f}%' if n+f else '?')")
  LINHAS=$(P -c "select count(*) from public.cliques")
  TAM=$(P -c "select pg_size_pretty(pg_total_relation_size('public.cliques'))")
  T1Q=$(tempo "select count(*), sum(cliques) from public.vw_desempenho_publicacoes where publicado_em >= now() - interval '30 days'")
  T2Q=$(tempo "select (ocorrido_em at time zone 'America/Recife')::date, count(*) from public.cliques where ocorrido_em >= now() - interval '30 days' group by 1")
  T3Q=$(tempo "select dia, sum(cliques) from public.cliques_diarios where dia >= privado.dia_local(now()) - 30 group by 1")
  T4Q=$(tempo "select public.manutencao_diaria()")
  printf '| %s/dia | %s | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s ms | %s ms | %s ms | %s ms | %s | %s ms |\n' \
    "$DIA" "$ALVO" "${TPS:-0}" "$P50" "$P95" "$P99" "$ERRO" "$CPU" "$RSS" "$CON" "$LINHAS" "$TAM" "$T1Q" "$T2Q" "$T3Q" "$T_ROLL" "$DIF" "$T4Q" >> "$SAIDA"
  echo "   atingido ${TPS:-?}/s p50 $P50 p95 $P95 p99 $P99 erros $ERRO CPU $CPU; painel brutos ${T1Q} ms, rollup ${T3Q} ms"
done
[ -n "${MANTER:-}" ] || dropdb -U "$U" "$DB"
rm -f /tmp/pgbench_log.* /tmp/mon_*.txt
echo; cat "$SAIDA"
