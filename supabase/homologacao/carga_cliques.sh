#!/usr/bin/env bash
# Teste de carga progressivo do caminho do clique (registrar_clique) e das consultas do painel,
# para 1.000, 10.000 e 100.000 cliques por dia. Roda SÓ em homologação local (nunca em produção).
#
# Premissas (documentadas no relatório):
#   * hora de pico = 15% dos cliques do dia; rajada = 3× a média da hora de pico;
#   * o teste roda a 5× a rajada (folga) por DURACAO segundos, com 8 conexões;
#   * 70% de visitantes únicos, 30% repetidos (exercita a deduplicação de 24 h);
#   * volume: 30 dias de cliques já gravados (30 mil / 300 mil / 3 milhões de linhas).
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
echo "Ambiente: PostgreSQL $(P -c 'show server_version') local, $(nproc) CPUs. Não representa o hardware do Supabase (ver relatório)."
echo
echo "| Cenário | Taxa alvo (5× a rajada) | Atingido (cliques/s) | Latência média | Falhas | Linhas em 30 dias | Tabela+índices | Painel 30 dias (vw_desempenho) | Métricas diárias | Manutenção diária |"
echo "|---|---|---|---|---|---|---|---|---|---|"
} > "$SAIDA"

for DIA in ${CENARIOS:-1000 10000 100000}; do
  RAJADA=$(python3 -c "print(round($DIA*0.15/3600*3, 3))")
  ALVO=$(python3 -c "print(max(1, round($RAJADA*5)))")
  VIS=$(python3 -c "print(max(10, int($DIA*0.7)))")
  echo "== $DIA cliques/dia: pré-carga de 30 dias e $ALVO cliques/s por ${DURACAO}s"
  P -c "truncate public.cliques, public.cliques_suspeitos"
  # 30 dias de histórico, distribuídos nas publicações, com IP pseudonimizado
  P -c "insert into public.cliques (link_afiliado_id, promocao_id, canal_id, publicacao_id, ip_hash, user_agent, ocorrido_em)
        select l.id, l.promocao_id, h.canal_id, h.id, md5(g::text) || md5((g * 7)::text), 'Mozilla/5.0 carga',
               now() - (random() * interval '30 days')
          from generate_series(1, $DIA * 30) g
          join lateral (select id, promocao_id from public.links_afiliados where id = $LINK_MIN + 1 + (g % 50)) l on true
          join lateral (select id, canal_id from public.historico_publicacoes where link_afiliado_id = l.id limit 1) h on true"
  P -c "vacuum analyze public.cliques"   # estado estável (autovacuum em dia)
  RES=$("$PGBENCH" -U "$U" -n -c 8 -j 4 -T "$DURACAO" -R "$ALVO" -D linkbase="$LINK_MIN" -D visitantes="$VIS" -f /tmp/clique.sql "$DB" 2>&1 || true)
  TPS=$(echo "$RES" | sed -n 's/^tps = \([0-9.]*\).*/\1/p' | head -1)
  LAT=$(echo "$RES" | sed -n 's/^latency average = \(.*\)$/\1/p' | head -1)
  FALHAS=$(echo "$RES" | sed -n 's/^number of failed transactions: \([0-9]*\).*/\1/p' | head -1)
  LINHAS=$(P -c "select count(*) from public.cliques")
  TAM=$(P -c "select pg_size_pretty(pg_total_relation_size('public.cliques'))")
  T1=$(P -c "\timing on" -c "select count(*), sum(cliques) from public.vw_desempenho_publicacoes where publicado_em >= now() - interval '30 days'" 2>&1 | sed -n 's/^Time: \([0-9.]*\) ms.*/\1 ms/p' | tail -1)
  T2=$(P -c "\timing on" -c "select count(*) from public.vw_metricas_diarias" 2>&1 | sed -n 's/^Time: \([0-9.]*\) ms.*/\1 ms/p' | tail -1)
  T3=$(P -c "\timing on" -c "select public.manutencao_diaria()" 2>&1 | sed -n 's/^Time: \([0-9.]*\) ms.*/\1 ms/p' | tail -1)
  echo "| $DIA/dia | $ALVO/s | ${TPS:-?} | ${LAT:-?} | ${FALHAS:-0} | $LINHAS | $TAM | ${T1:-?} | ${T2:-?} | ${T3:-?} |" >> "$SAIDA"
  echo "   atingido ${TPS:-?}/s, latência ${LAT:-?}, painel ${T1:-?}"
done
[ -n "${MANTER:-}" ] || dropdb -U "$U" "$DB"
echo; cat "$SAIDA"
