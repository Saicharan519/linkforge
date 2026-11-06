# Results and evidence

Measured results and drill outcomes: this is the part of the repo a reviewer reads to see that
things were actually run and observed. Fill in the "Observed" column from your own terminal;
do not copy my predictions. Empty cells mean "not yet run".

## Walking skeleton (P1S): the GitOps loop

| # | Proof | What I did | Observed (output / time) | Date |
|---|---|---|---|---|
| 1 | A push deploys with no manual step | Changed the placeholder page text and pushed (`docs/RUNBOOK-argocd.md` proof 1) | | |
| 2 | Self-heal | `kubectl scale deploy/api --replicas=5`, then deleted `deploy/frontend` (proof 2) | | |
| 3 | Bad release keeps old pods serving; `git revert` heals | Set `sha-0000000` as the frontend tag, then `git revert` (proof 3) | | |

Timings worth writing down for #1: push to green CI, CI bump commit to Argo sync, sync to new
pod Ready, and the total.

## Kubernetes drills (P1S.4b/4c)

| Drill | Observed |
|---|---|
| Delete an API pod: requests keep succeeding | Loop printed `302` throughout (screenshot, 2026-11-06) |
| Scale Postgres to 0: readiness fails, no restarts | `503`s while Postgres was down, `302` again after it returned; restarts stayed 0 |
| Plain Helm upgrade with a new image tag fails on the immutable Job | Revision 4 `failed`; a new api pod in `ImagePullBackOff` while 2 old pods served; fixed with `helm rollback linkforge 3` |
| Pod right after `helm install` (no ordering): migrate Job failed once, then passed | `migrate-5pvsm` Error (DNS name for the headless Service did not exist yet), `migrate-nb524` Completed |

## Resource use on kind (validates ARCHITECTURE §9.3 estimates)

Paste `kubectl top pods -n linkforge` and `kubectl top nodes` here, idle and (later) under load:

```
(not yet recorded)
```
