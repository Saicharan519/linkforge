#!/usr/bin/env bash
# Prove that a dump (scripts/pg_dump.sh) can be restored: load it into a THROW-AWAY Postgres container, compare row
# counts with the live kind database, and run the real API image against the restored copy (ARCHITECTURE §10).
#   scripts/pg_restore_check.sh DUMPFILE
#
# What it does, in order (everything throw-away is removed at the end, also on failure):
#   1 starts postgres (the SAME image as the kind StatefulSet) in a container with a random password (never printed)
#   2 pg_restore --no-owner into it
#   3 counts rows per table in the restored copy and in the live kind database and prints both side by side
#   4 starts the API image (the tag the kind cluster runs) against the restored database and checks /readyz and
#     that a real short link redirects with 302 (Redis is deliberately unreachable: this also shows the Postgres
#     fallback of the redirect path)
# Exit code 0 only if every count matches and both HTTP checks pass.
set -euo pipefail
DUMP="${1:?usage: pg_restore_check.sh DUMPFILE}"
[ -s "$DUMP" ] || { echo "no such dump: $DUMP" >&2; exit 2; }
CTX=kind-linkforge
K="kubectl --context $CTX -n linkforge"
PGIMG="$($K get statefulset postgres -o jsonpath='{.spec.template.spec.containers[0].image}')"
APIIMG="$($K get deploy api -o jsonpath='{.spec.template.spec.containers[0].image}')"
NET="lf-restore-$$"
PGC="lf-restore-pg-$$"
APIC="lf-restore-api-$$"
PW="$(openssl rand -hex 16)"
TABLES="users links api_keys click_events link_daily_stats"
cleanup() { docker rm -f "$APIC" "$PGC" > /dev/null 2>&1 || true; docker network rm "$NET" > /dev/null 2>&1 || true; }
trap cleanup EXIT

echo "postgres image: $PGIMG"
echo "api image:      $APIIMG"
docker network create "$NET" > /dev/null
docker run -d --name "$PGC" --network "$NET" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_USER=restore -e POSTGRES_DB=restore "$PGIMG" > /dev/null
for _ in $(seq 1 60); do docker exec "$PGC" pg_isready -U restore -d restore > /dev/null 2>&1 && break; sleep 1; done
sleep 2
echo "== pg_restore"
docker exec -i "$PGC" pg_restore -U restore -d restore --no-owner --exit-on-error < "$DUMP"

echo "== row counts (restored copy vs live kind database)"
fail=0
printf '%-18s %12s %12s\n' table restored live
for t in $TABLES; do
  r="$(docker exec "$PGC" psql -U restore -d restore -Atc "select count(*) from $t")"
  l="$($K exec postgres-0 -- sh -c "psql -U \"\$POSTGRES_USER\" -d \"\${POSTGRES_DB:-\$POSTGRES_USER}\" -Atc 'select count(*) from $t'")"
  mark=ok; [ "$r" = "$l" ] || { mark=MISMATCH; fail=1; }
  printf '%-18s %12s %12s  %s\n' "$t" "$r" "$l" "$mark"
done
rs="$(docker exec "$PGC" psql -U restore -d restore -Atc 'select coalesce(sum(clicks),0) from link_daily_stats')"
# Rollups count only NON-bot clicks (ARCHITECTURE §2.3: bots are stored with is_bot=true and excluded from rollups).
ev="$(docker exec "$PGC" psql -U restore -d restore -Atc 'select count(*) from click_events where not is_bot')"
echo "rollup sum in the restored copy: $rs, non-bot click_events rows: $ev"; [ "$rs" = "$ev" ] || { echo "ROLLUP != NON-BOT EVENTS"; fail=1; }
CODE="$(docker exec "$PGC" psql -U restore -d restore -Atc "select code from links where is_active and (expires_at is null or expires_at > now()) order by created_at limit 1")"

echo "== the API against the restored database"
docker run -d --name "$APIC" --network "$NET" \
  -e ENV=dev -e "DATABASE_URL=postgresql+asyncpg://restore:$PW@$PGC:5432/restore" -e REDIS_URL=redis://127.0.0.1:1/0 \
  -e IP_HASH_SECRET="$(openssl rand -hex 16)" -e SHORT_URL_BASE=http://go.restore.test -e TRUSTED_PROXY_COUNT=0 \
  -e ALLOWED_ORIGINS=http://app.restore.test -e COOKIE_SECURE=false "$APIIMG" > /dev/null
ready=000
for _ in $(seq 1 40); do
  ready="$(docker exec "$APIC" python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').status)" 2>/dev/null || true)"
  [ "$ready" = 200 ] && break; sleep 1
done
echo "/readyz -> ${ready:-no answer}"; [ "$ready" = 200 ] || fail=1
red="$(docker exec "$APIC" python -c "
import http.client
c=http.client.HTTPConnection('127.0.0.1',8000)
c.request('GET','/$CODE')
r=c.getresponse()
print(r.status, r.getheader('Location'))" 2>&1 | tail -1)"
echo "GET /$CODE -> $red"; case "$red" in 302*) ;; *) fail=1 ;; esac
[ "$fail" = 0 ] && echo "RESTORE CHECK PASSED" || { echo "RESTORE CHECK FAILED"; exit 1; }
