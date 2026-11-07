#!/usr/bin/env bash
# Dump the LinkForge database of the local kind cluster into a custom-format file (ARCHITECTURE §10).
#   scripts/pg_dump.sh [OUTFILE]      default: ~/linkforge-backups/linkforge-YYYYmmdd-HHMMSS.dump
#
# `pg_dump -Fc` = one consistent snapshot (a single transaction), compressed, restorable with pg_restore, also
# table by table. It runs INSIDE the postgres pod (same version as the server, no client needed here) and uses the
# pod's own POSTGRES_USER / POSTGRES_DB environment, so no password is typed, printed or stored.
# Safe by construction: only talks to the kubectl context `kind-linkforge`.
# A backup nobody has restored is an assumption: prove it with scripts/pg_restore_check.sh.
set -euo pipefail
CTX=kind-linkforge
OUT="${1:-$HOME/linkforge-backups/linkforge-$(date +%Y%m%d-%H%M%S).dump}"
mkdir -p "$(dirname "$OUT")"
umask 077
kubectl --context "$CTX" -n linkforge exec postgres-0 -- sh -c 'pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB:-$POSTGRES_USER}" -Fc' > "$OUT"
[ -s "$OUT" ] || { echo "empty dump: $OUT" >&2; rm -f "$OUT"; exit 1; }
echo "wrote $OUT ($(wc -c < "$OUT") bytes, sha256 $(sha256sum "$OUT" | cut -c1-16)...)"
