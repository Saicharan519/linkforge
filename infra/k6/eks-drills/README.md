# EKS drills (P10.2)

Run by the operator in a shell where `KUBECONFIG` is the EKS file (each script refuses anything else), with the `k6` namespace, the `k6-scripts`,
`k6-api-key` and `k6-hosts` objects and `infra/k6/k6-job-eks.yaml` prepared as in the P10.1 steps. The app's rate limits must be raised for the load.
Results and caveats: `docs/RESULTS.md` ("P10"). They assume the paths `~/linkforge` and `~/linkforge-gitops`.

| Script | Drill |
|---|---|
| `drill-pod.sh` | kill one api pod under 100 req/s |
| `drill-redis.sh` | kill `redis-0` under 100 req/s (EBS-backed AOF) |
| `drill-worker.sh` | `worker.replicas: 0` via gitops, then revert; two commits in the gitops repo |
| `drill-badtag.sh` | non-existent frontend tag via gitops, then revert; two commits in the gitops repo |
