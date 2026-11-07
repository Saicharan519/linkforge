# k6 load tests (P8.4)

Scripts: `load.js` (three separate scenarios), `limiter.js` (the rate limiter itself), `seed.sh` (test user + API key),
`k6-job.yaml` (run inside the kind cluster). k6 image: `grafana/k6` v2.3.0 pinned by digest.
Method and traps: `docs/DEVOPS_GUIDE.md` §12. Measured results: `docs/RESULTS.md` ("P8.4").

## The order that matters
1. **Raise the app's own rate limits** (otherwise you measure 429s: one k6 machine is one client IP, and `setup()`
   creates 1000 links). In `linkforge-gitops` set `config.RATE_LIMIT_REDIRECT_PER_MIN` and
   `config.RATE_LIMIT_CREATE_PER_MIN` in `envs/kind/values.yaml`, push, wait for the api and worker to roll.
2. `infra/k6/seed.sh` once (creates a throw-away user and saves one API key in `~/.linkforge-k6/api_key`, mode 600).
3. Run (below). Each scenario should be measured in its own run (`ONLY=`), because the server-side histogram in
   Prometheus cannot tell scenarios apart.
4. **Revert the limits** (`git revert` that commit) and wait for the roll. Then run `limiter.js` with the defaults.

## Inside the cluster (server-side numbers, no laptop network)
```bash
kubectl --context kind-linkforge create ns k6
kubectl --context kind-linkforge -n k6 create configmap k6-scripts --from-file=infra/k6/load.js
kubectl --context kind-linkforge -n k6 create secret generic k6-api-key --from-file=api_key=$HOME/.linkforge-k6/api_key
kubectl --context kind-linkforge apply -f infra/k6/k6-job.yaml     # edit DURATION / add ONLY, LINKS env first if needed
kubectl --context kind-linkforge -n k6 logs -f job/k6-load
```

## From this machine (CLIENT-side: includes the path laptop to Ingress; label it as such)
```bash
printf 'API_KEY=%s\nGO_URL=http://go.localtest.me\nAPP_URL=http://app.localtest.me\nONLY=redirect_warm\nLINKS=10\n' "$(cat ~/.linkforge-k6/api_key)" > /tmp/k6.env
docker run --rm --network host --env-file /tmp/k6.env -v "$PWD/infra/k6:/scripts:ro" \
  grafana/k6@sha256:3e4186aea62e11a45560bfc51e19eeafe82fc07ce069c78aff44b7ff893d9a5e run /scripts/load.js
rm /tmp/k6.env
```
On kind "from this machine" is loopback to the kind port mapping: it excludes real internet and Wi-Fi, so it is
the cheapest possible client-side number, not a user's.

## Environment variables (`load.js`)
`GO_URL`, `APP_URL`, `API_KEY` (required) · `GO_HOST`/`APP_HOST` Host-header overrides · `DURATION` (3m) · `WARM_RATE` (200/s) ·
`MIXED_RATE` (50/s) · `CREATE_PER_MIN` (20) · `LINKS` (1000) · `ONLY` (comma list of `redirect_warm,redirect_mixed,create`).
