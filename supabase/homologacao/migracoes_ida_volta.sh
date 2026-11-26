#!/usr/bin/env bash
# Aplica → reverte → reaplica as migrations 020, 021 e 027 e confere o esquema em cada passo.
# As migrations entre a 021 e a 027 só mexem em dados (configurações/cores) e não têm reversão própria.
# Só homologação local. Uso: PGHOST=/tmp/pghomolog PGPORT=54329 homologacao/migracoes_ida_volta.sh
set -euo pipefail
cd "$(dirname "$0")/.."
U="${PGUSER:-postgres}"; T=/tmp/ida_volta; mkdir -p $T
P() { psql -U "$U" -q -v ON_ERROR_STOP=1 -d "$1" "${@:2}"; }
novo() { psql -U "$U" -q -d postgres -c "drop database if exists $1" -c "create database $1"; P "$1" -f homologacao/00_simular_supabase.sql >/dev/null; }
esquema() { pg_dump -U "$U" -s -d "$1" --no-owner --no-privileges | grep -v '^--' | grep -Ev '^\\(un)?restrict' | sed '/^$/d' | sort > "$2"; }
privs() { psql -U "$U" -d "$1" -Atc "select table_name||':'||grantee||':'||privilege_type from information_schema.role_table_grants where table_schema='public' and grantee in ('anon','authenticated') order by 1" > "$2"; }
ate() { for f in migrations/*.sql; do [[ "$(basename "$f")" > "$2" ]] && break; P "$1" -f "$f" >/dev/null 2>&1 || { echo "✖ $f"; exit 1; }; done; }

novo iv_019; ate iv_019 20261125190000; esquema iv_019 $T/019.sql; privs iv_019 $T/019.priv
novo iv_020; ate iv_020 20261125200000; esquema iv_020 $T/020.sql; privs iv_020 $T/020.priv
novo iv_full; ate iv_full 20261125200001   # até a 021 (inclusive)
P iv_full -f rollback/20261125200000_auditoria_expansao.down.sql >/dev/null 2>&1
esquema iv_full $T/f_020.sql; privs iv_full $T/f_020.priv
diff -q $T/020.sql $T/f_020.sql >/dev/null && diff -q $T/020.priv $T/f_020.priv >/dev/null \
  && echo "✔ reverter 021 volta exatamente ao esquema e privilégios da 020" || { echo "✖ 021↓ difere da 020:"; diff $T/020.sql $T/f_020.sql | head -20; diff $T/020.priv $T/f_020.priv | head; exit 1; }
P iv_full -f rollback/20261125190000_expansao_modular.down.sql >/dev/null 2>&1
esquema iv_full $T/f_019.sql; privs iv_full $T/f_019.priv
# o esquema "testes" (utilitários carregados acima) não existe no banco de referência
cp $T/f_019.sql $T/f_019b.sql; cp $T/019.sql $T/019b.sql
diff -q $T/019b.sql $T/f_019b.sql >/dev/null && diff -q $T/019.priv $T/f_019.priv >/dev/null \
  && echo "✔ reverter 020 volta exatamente ao esquema e privilégios da 019" || { echo "✖ 020↓ difere da 019:"; diff $T/019b.sql $T/f_019b.sql | head -20; diff $T/019.priv $T/f_019.priv | head; exit 1; }
P iv_full -f migrations/20261125190000_expansao_modular.sql >/dev/null 2>&1
P iv_full -f migrations/20261125200000_auditoria_expansao.sql >/dev/null 2>&1
# 027: aplicar tudo → reverter a 027 → tem de voltar ao esquema da 026
novo iv_026; ate iv_026 20261126160000; esquema iv_026 $T/026.sql; privs iv_026 $T/026.priv
for f in migrations/*.sql; do [[ "$(basename "$f")" > "20261125200001" ]] && { P iv_full -f "$f" >/dev/null 2>&1 || { echo "✖ $f"; exit 1; }; }; done
P iv_full -f rollback/20261126160000_preco_loja_pix_cupom_sem_codigo.down.sql >/dev/null 2>&1
esquema iv_full $T/f_026.sql; privs iv_full $T/f_026.priv
diff -q $T/026.sql $T/f_026.sql >/dev/null && diff -q $T/026.priv $T/f_026.priv >/dev/null \
  && echo "✔ reverter 027 volta exatamente ao esquema e privilégios da 026" || { echo "✖ 027↓ difere da 026:"; diff $T/026.sql $T/f_026.sql | head -20; diff $T/026.priv $T/f_026.priv | head; exit 1; }
P iv_full -f migrations/20261126160000_preco_loja_pix_cupom_sem_codigo.sql >/dev/null 2>&1
for f in tests/*.sql; do P iv_full -f "$f" >/dev/null; done
R=$(psql -U "$U" -d iv_full -Atc "select count(*) || ' testes, ' || count(*) filter (where not ok) || ' reprovados' from testes.rodar()")
echo "✔ reaplicadas 020, 021 e as seguintes (027 incluída); bateria do banco: $R"
[[ "$R" == *" 0 reprovados" ]] || exit 1
for d in iv_019 iv_020 iv_026 iv_full; do psql -U "$U" -q -d postgres -c "drop database $d"; done
