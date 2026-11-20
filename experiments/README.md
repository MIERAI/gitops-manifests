# Fault experiments

Alerts are tested like code: each file injects one fault and states, **before** it runs, which alerts must fire
within how long, which must stay quiet, and that nothing else may page. `scripts/experiment.py` runs them and writes
a report to `results/`. Principles: gitops-platform-blueprint `docs/slo.md` §5. Faults only on our own clusters.

```sh
# kind (lab): several at once, one per namespace (they share the 1h burn-rate windows)
uv run --with pyyaml python3 scripts/experiment.py --kubeconfig ~/.kube/kind-gitops.yaml \
  experiments/api-errors.yaml experiments/api-slow.yaml experiments/api-pod-failure.yaml experiments/sample-app-down.yaml
```

| Experiment | Fault | Expected |
|---|---|---|
| api-errors | api responses replaced by 503 (HTTPChaos) | web availability pages; web latency quiet |
| api-slow | api packets +800ms (NetworkChaos) | web latency pages; web availability quiet |
| api-pod-failure | one of two api pods fails 10 min | nothing pages |
| api-down | every api pod fails | web availability pages; api "SLI absent" ticket |
| sample-app-down | every sample-app pod fails | sample-app availability pages |
| quiet-traffic | none: 2h of normal traffic | nothing pages (the negative test) |
| api-factory-one-pod-down | one of two API pods fails 10 min | nothing pages |
| api-factory-db-slow | API → Postgres packets +200ms (NetworkChaos) | rpc-latency pages; rpc-availability and health-probe quiet |
| api-factory-db-partition | API ↮ Postgres partition (NetworkChaos) | health-probe pages (predicted: rpc-availability does not, readiness hides the failures) |
| backup-store-unreachable | api-factory dev: Postgres pod → object store partitioned (NetworkChaos), with writes | recovery-point SLO pages; `PostgresWalArchivingFailing` ticket; base-backup SLO quiet |
| backup-normal-writes | none: dev's database writing (WAL archived every 5 min) | no backup page or ticket (the negative test) |
| storefront-worker-one-down | one of two workers fails 10 min | nothing pages |
| storefront-worker-down | every worker fails | order-freshness and order-completeness page; order-intake quiet |
| storefront-worker-slow | Postgres → workers +2s per packet (~8s per order, still above arrivals) | nothing pages |
| storefront-worker-overloaded | Postgres → workers +8s per packet (below daytime arrivals) | order-freshness pages; completeness quiet |
| storefront-db-unreachable | Postgres → web and workers: every packet dropped | order-intake pages; freshness "SLI absent" ticket |

Format: `hypothesis`, `namespace`, `fault` (Chaos Mesh objects, created in the namespace and always deleted),
`load` (optional Kubernetes objects the fault needs to matter, e.g. a Job that writes; created first, deleted with the
fault), `timeout`, `hold` (keep going this long even when everything expected fired), `expect.fire` (`alert`, `labels`,
`within`), `expect.quiet`, `recover` (the fired alerts resolve within this after the fault).

The SLO alerts come from Pyrra: `ErrorBudgetBurn` with labels `slo`, `namespace`, `severity` (page / ticket) and the
windows `long` / `short` (fast burn: `long: 1h`, 14× the budget rate, for 2m); `SLOMetricAbsent` (ticket).
Scheduled jobs (backups, restore check, off-site copy) have time-based SLOs that only turn bad after a day (`age`
max 26h / 3h): a failed nightly run cannot be watched end to end here; what fits in 30 minutes is the WAL
recovery point.

NetworkChaos with a `target` matches the target pods' IPs. A client reaching a database through its Service sends
packets to the Service IP (translated on the node, outside the pod), so a fault on the client side towards the
database pods may not match (to be verified): the storefront experiments select the database and target the clients
(the replies carry the clients' pod IPs), which holds either way.
