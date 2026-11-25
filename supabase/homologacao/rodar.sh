#!/usr/bin/env bash
# Homologação local do banco: cria um banco NOVO, aplica a simulação do Supabase, todas as
# migrations e todos os testes, e mostra o resultado. Não toca no projeto Supabase real.
# Uso: PGHOST=/tmp/pghomolog PGPORT=54329 supabase/homologacao/rodar.sh [nome_do_banco]
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${1:-homologacao}"
export PGUSER="${PGUSER:-postgres}"
psql -q -d postgres -c "drop database if exists $DB" -c "create database $DB"
P="psql -q -v ON_ERROR_STOP=1 -d $DB"
$P -f homologacao/00_simular_supabase.sql
for f in migrations/*.sql; do $P -f "$f" >/dev/null || { echo "✖ migration $f"; exit 1; }; done
for f in tests/*.sql; do $P -f "$f" >/dev/null || { echo "✖ teste $f"; exit 1; }; done
$P -tA -F " | " -c "create temp table r as select * from testes.rodar(); select 'TOTAL ' || count(*) || ' · aprovados ' || count(*) filter (where ok) || ' · reprovados ' || count(*) filter (where not ok) from r; select grupo, teste, detalhe from r where not ok"
falhas=$($P -tA -c "select count(*) filter (where not ok) from testes.rodar()")
[ "$falhas" -eq 0 ] || { echo "✖ $falhas teste(s) do banco reprovado(s)"; exit 1; }
