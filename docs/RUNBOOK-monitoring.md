# Runbook: monitoring stack (kube-prometheus-stack on kind)

Installed by Argo CD from `linkforge-gitops`: `argocd/apps/monitoring.yaml` (sync wave -2), values in
`platform/monitoring/values.yaml`, Grafana admin password in `platform/monitoring/extras/`.

## What runs (namespace `monitoring`, chart 92.22.0)

| Component | Image (tag, as rendered by the chart) | Measured memory (idle, 2026-11-07) |
|---|---|---|
| Prometheus | `quay.io/prometheus/prometheus:v3.16.0-distroless` | 243 Mi |
| Alertmanager | `quay.io/prometheus/alertmanager:v0.34.2` | 29 Mi |
| Grafana (+ 2 sidecars) | `docker.io/grafana/grafana:13.3.0-distroless`, `quay.io/kiwigrid/k8s-sidecar:2.14.3` | 449 Mi (pod total) |
| Prometheus Operator | `quay.io/prometheus-operator/prometheus-operator:v0.94.2` (+ config reloader) | 24 Mi |
| kube-state-metrics | `registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0` | 19 Mi |
| node-exporter | `quay.io/prometheus/node-exporter:v1.12.1-distroless` | 9 Mi |

Whole stack: about 770 Mi of the 7.6 GiB machine (the node went from 2425 Mi to 3817 Mi used in total, which also
includes the extra Prometheus scrape load on the control plane).

## Look at it (nothing is exposed through an Ingress)

```bash
kubectl --context kind-linkforge -n monitoring port-forward svc/monitoring-grafana 3000:80
kubectl --context kind-linkforge -n monitoring port-forward svc/monitoring-prometheus 9090:9090
kubectl --context kind-linkforge -n monitoring port-forward svc/monitoring-alertmanager 9093:9093
```

Grafana allows anonymous READ-ONLY (Viewer) access because it is only reachable through the port-forward. The admin
login (user `admin`) is for editing; its password lives only in the cluster Secret `grafana-admin`:

```bash
kubectl --context kind-linkforge -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
```

## Known limits (be honest about these)

- **Chart version pinning does not pin image digests.** The chart pins image TAGS; a tag can be re-pointed. We
  record what was pulled and scan it with Trivy (below), but nothing stops a re-pushed tag. Digest pinning would
  need an `image.sha` override per component.
- **No persistence on kind.** The TSDB is an emptyDir (2 d retention, 2 GB cap): a rescheduled Prometheus pod
  loses its history. Fine for a lab; EKS (P9) needs a PersistentVolume.
- **No Slack.** Alertmanager uses the chart default (everything to the `null` receiver). Alerts are checked in the
  Prometheus, Alertmanager and Grafana UIs. Slack delivery has NOT been tested.
- **No admission webhook** for PrometheusRule (it needs an Argo CD hook Job). Rules are validated with
  `promtool` instead (P8.3).
- **Redis memory** comes from cAdvisor `container_memory_working_set_bytes`, not from Redis `used_memory` (no Redis
  exporter in this phase). It is the container's working set, not exact OOM accounting.

## Trivy scan of the deployed images (2026-11-07, Trivy 0.76.2, `--severity HIGH,CRITICAL --ignore-unfixed`)

| Image | HIGH/CRITICAL with a fix available |
|---|---|
| grafana 13.3.0-distroless | 54 |
| node-exporter v1.12.1-distroless | 16 |
| kube-state-metrics v2.20.0 | 11 |
| k8s-sidecar 2.14.3 | 5 |
| alertmanager v0.34.2 | 2 |
| prometheus v3.16.0-distroless | 2 |
| prometheus-config-reloader v0.94.2 | 1 |
| prometheus-operator v0.94.2 | 1 |

These are third-party images: CI does not gate on them (it scans our own). Almost all are Go-module or stdlib
findings inside upstream binaries (`golang.org/x/net`, `x/crypto`, `x/text`, `grpc`, Go `stdlib`) that the
upstream projects fix by rebuilding. Nothing here is reachable from outside the cluster (no Ingress). Re-scan after
every chart bump and prefer the newest chart: this is the first thing to re-check in P8b.5.

Re-run: `trivy image --severity HIGH,CRITICAL --ignore-unfixed <image>` for each image in
`kubectl -n monitoring get pods -o jsonpath='{..image}'`.

## Bumping the chart

1. `helm search repo prometheus-community/kube-prometheus-stack --versions | head`
2. Change `targetRevision` in `argocd/apps/monitoring.yaml` (exact version), render offline with
   `helm template ... -f platform/monitoring/values.yaml`, check the image list.
3. Push; Argo applies with ServerSideApply (CRDs are too big for client-side apply).
4. Re-scan the images, update the table above.

## Recovery notes

- Never delete the `monitoring` Application with a cascade: the finalizer is deliberately absent, because deleting
  the CRDs deletes every ServiceMonitor and PrometheusRule in the cluster.
- The `linkforge` chart renders ServiceMonitors only when `monitoring.serviceMonitor.enabled` is true. On a LIVE
  cluster that never had the stack, push the monitoring Application first, wait for it to be Healthy, then switch
  the flag on (done that way on 2026-11-07). On a fresh cluster the wave order does it.
