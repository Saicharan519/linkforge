#!/usr/bin/env bash
# Makes a throw-away TEST user on the local kind cluster and one API key for k6. Writes the key to
# ~/.linkforge-k6/api_key (mode 600, outside the repo). Nothing secret is printed.
# Re-running reuses the existing key file; delete ~/.linkforge-k6 to start over.
#
#   infra/k6/seed.sh [APP_URL]        default http://app.localtest.me (the kind Ingress)
set -euo pipefail
APP="${1:-http://app.localtest.me}"
DIR="$HOME/.linkforge-k6"
umask 077
mkdir -p "$DIR"
if [ -s "$DIR/api_key" ]; then echo "api key already present in $DIR/api_key (length $(wc -c < "$DIR/api_key"))"; exit 0; fi

email="k6-$(openssl rand -hex 4)@example.test"
pw="$(openssl rand -base64 24 | tr -d '/+=\n')"
jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT
# State-changing requests need a matching Origin (CSRF rule, ARCHITECTURE §2.4)
curl -fsS -o /dev/null -H "Origin: $APP" -H 'Content-Type: application/json' \
  -d "$(printf '{"email":"%s","password":"%s"}' "$email" "$pw")" "$APP/api/v1/auth/register"
curl -fsS -o /dev/null -c "$jar" -H "Origin: $APP" -H 'Content-Type: application/json' \
  -d "$(printf '{"email":"%s","password":"%s"}' "$email" "$pw")" "$APP/api/v1/auth/login"
csrf="$(awk '$6=="lf_csrf"{print $7}' "$jar")"
[ -n "$csrf" ] || { echo "no lf_csrf cookie after login" >&2; exit 1; }
curl -fsS -b "$jar" -H "Origin: $APP" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json' \
  -d '{"name":"k6"}' "$APP/api/v1/api-keys" | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["key"])' > "$DIR/api_key"
unset pw
echo "test user created ($email), API key saved to $DIR/api_key (length $(wc -c < "$DIR/api_key"))"
