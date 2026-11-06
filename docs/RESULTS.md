# Results and evidence

Measured results and drill outcomes: this is the part of the repo a reviewer reads to see that
things were actually run and observed. Fill in the "Observed" column from your own terminal;
do not copy my predictions. Empty cells mean "not yet run".

## Walking skeleton (P1S): the GitOps loop

| # | Proof | What I did | Observed (output / time) | Date |
|---|---|---|---|---|
| 1 | A push deploys with no manual step | Changed the placeholder page text and pushed (`docs/RUNBOOK-argocd.md` proof 1) | Push `8c64e1e` to linkforge; CI then added bump commit `046945f` to linkforge-gitops (tag `sha-8c64e1e`); Argo rolled both deployments with no manual step (api and frontend pods were replaced, migrate Job re-ran and `Completed`). Timings and the page-text check were not captured | 2026-11-06 |
| 2 | Self-heal | `kubectl scale deploy/api --replicas=5`, then deleted `deploy/frontend` (proof 2) | Extra api pods (`5kj7t`, `7n2rl`, `pt78j`) went to `Terminating` about 13 s after the scale, back to 2 api pods; they ended as `Error` at about 31 s (see note below). `deploy/frontend` deleted; a frontend pod was running again afterwards (recreation timing not captured) | 2026-11-06 |
| 3 | Bad release keeps old pods serving; `git revert` heals | Set `sha-0000000` as the frontend tag, then `git revert` (proof 3) | New pod `frontend-869849d94d-fnnh7` stuck in `ImagePullBackOff` (0/1) while old pod `frontend-7ccbb9c8f4-2tbv2` stayed `1/1 Running`; `curl http://app.localtest.me/` still returned `<h1>LinkForge</h1>`. Healed with `git revert --no-edit HEAD && git push` (commit 1b75529), no kubectl. Post-revert Argo status: not yet recorded | 2026-11-06 |

Timings worth writing down for #1: push to green CI, CI bump commit to Argo sync, sync to new
pod Ready, and the total.

## Kubernetes drills (P1S.4b/4c)

| Drill | Observed |
|---|---|
| Delete an API pod: requests keep succeeding | Loop printed `302` throughout (screenshot, 2026-11-06) |
| Scale Postgres to 0: readiness fails, no restarts | `503`s while Postgres was down, `302` again after it returned; restarts stayed 0 |
| Plain Helm upgrade with a new image tag fails on the immutable Job | Revision 4 `failed`; a new api pod in `ImagePullBackOff` while 2 old pods served; fixed with `helm rollback linkforge 3` |
| CI gate caught a new CVE on its own | `build-push` for 2ea2fc9 failed at the image-scan step (frontend); no image pushed, no gitops bump. Reproduced locally: zlib CVE-2026-85091 HIGH, fixable. Fixed with a temporary `apk upgrade zlib` layer; both images then scan clean (D54) |
| Pod right after `helm install` (no ordering): migrate Job failed once, then passed | `migrate-5pvsm` Error (DNS name for the headless Service did not exist yet), `migrate-nb524` Completed |
| Same install under Argo CD with sync waves | Application went straight to `Synced`/`Healthy`; one `migrate-f95lc` pod, `Completed`, no failed pod; `postgres-0` Running before it; `go.localtest.me/Helm01` returned `302` (data survived the handover). Ingress did not stay Progressing (Traefik `ingressEndpoint.ip` change worked) |

Note on proof 2: the scaled-up api pods were removed while still starting (`0/1`) and ended as
`Error` about 31 s later. Explained and reproduced with Docker (D55): a container's PID 1 ignores
SIGTERM until the app installs a handler, so a pod killed during Python start-up is only stopped
by SIGKILL when the grace period ends (exit 137). A READY api container stops in about 0.7 s with
exit 0 and a clean "Application shutdown complete" log. The Kubernetes-side timing was not
measured directly.

## Resource use on kind (validates ARCHITECTURE §9.3 estimates)

Paste `kubectl top pods -n linkforge` and `kubectl top nodes` here, idle and (later) under load:

Idle, walking skeleton under Argo CD (2026-11-06; Argo's own pods are not included):

```
$ kubectl top pods -n linkforge
NAME                       CPU(cores)   MEMORY(bytes)
api-7984ddfc86-26mf7       4m           57Mi
api-7984ddfc86-8frvg       4m           56Mi
frontend-9d5ccb5dd-h5pfg   1m           10Mi
postgres-0                 31m          26Mi

$ kubectl top nodes
NAME                      CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
linkforge-control-plane   410m         3%       2484Mi          31%
```

Under load: not yet measured (P8 / k6).
