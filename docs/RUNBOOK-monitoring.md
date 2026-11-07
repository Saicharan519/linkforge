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

## The LinkForge dashboard

`LinkForge overview` (uid `linkforge-overview`) is a ConfigMap rendered by the `linkforge` chart
(`templates/dashboard.yaml`, JSON in `charts/linkforge/dashboards/linkforge.json`). Grafana's sidecar loads every
ConfigMap labelled `grafana_dashboard=1`; there is nothing to import by hand. Switch it with
`monitoring.dashboard.enabled` (on for kind). Open it through the Grafana port-forward at
`http://localhost:3000/d/linkforge-overview`.

Panels with no data are normal until the metric exists: labelled counters (`redirect_cache_total`,
`rate_limit_rejections_total`, `redis_errors_total`, `click_events_processed_total`) get their first series on the first event.
To change a panel, edit the JSON in git (an edit in the Grafana UI is lost when the sidecar reloads the ConfigMap).

## Alerts

11 rules in `charts/linkforge/alerts/linkforge-rules.yaml` (PrometheusRule `linkforge-alerts`, namespace `linkforge`):

| Alert | Fires when | For | Severity |
|---|---|---|---|
| LinkForgeRedirectP95High | redirect p95 > 250 ms | 5 m | warning |
| LinkForge5xxRatioHigh | 5xx > 2% of requests AND request rate > 0.1/s | 5 m | critical |
| ClickStreamLagHigh | `max(click_stream_lag)` > 5000 | 5 m | warning |
| ClickStreamPendingHigh | `max(click_stream_pending)` > 500 | 10 m | warning |
| ClickBacklogHigh | `max(click_stream_lag)` + `max(click_stream_pending)` > 80000 (stream cap is 100000; length alone is not alerted, D82) | none | critical |
| ClickEventsDropped | dropped-click rate > 0 | 5 m | warning |
| RedisMemoryHigh | container working set > 205 MiB (80% of maxmemory 256 MB) | 5 m | warning |
| LinkForgePodCrashLooping | a container is in CrashLoopBackOff | 5 m | critical |
| LinkForgeTargetDown | an api/worker target has `up == 0` | 2 m | critical |
| LinkForgeApiTargetMissing / LinkForgeWorkerTargetMissing | no such target exists at all | 5 m | critical |

Test them (needs Docker; promtool comes from a digest-pinned image):

```bash
charts/linkforge/alerts/run-tests.sh
```

See what is firing: Prometheus `http://localhost:9090/alerts` or Alertmanager `http://localhost:9093` (port-forwards above).
There is no Slack receiver, so nothing is delivered anywhere: `platform/monitoring/UNTESTED-seal-slack-webhook.sh` exists but has
never been run. The `Watchdog` alert (always firing, from the chart) is expected and proves the Prometheus to Alertmanager path.

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
