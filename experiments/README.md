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
| api-slow | api packets +800ms (NetworkChaos) | web latency pages; web availability quiet — skipped: 800ms per packet made every call time out (superseded by api-slow-v2) |
| api-slow-v2 | api packets +300ms (NetworkChaos) | web latency pages; web availability quiet — skipped: 0.30s per call, under the threshold (superseded by -v3) |
| api-slow-v3 | api packets +600ms (NetworkChaos) | web latency pages; web availability quiet |
| api-pod-failure | one of two api pods fails 10 min | nothing pages |
| api-down | every api pod fails | web availability pages; api "SLI absent" ticket |
| sample-app-down | every sample-app pod fails | sample-app availability pages |
| quiet-traffic | none: 2h of normal traffic | nothing pages (the negative test) |
| api-factory-one-pod-down | one of two API pods fails 10 min | nothing pages |
| api-factory-db-slow | Postgres → API packets +200ms (NetworkChaos, on the Postgres side: the API uses a ClusterIP) | rpc-latency pages; rpc-availability and health-probe quiet |
| api-factory-db-partition | API ↮ Postgres partition (NetworkChaos) | health-probe pages (predicted: rpc-availability does not, readiness hides the failures) |
| api-factory-db-partition-probes-v2 | the same, probes without the database (supersedes -probes) | rpc-latency-severe and health-probe page; rpc-availability / rpc-latency may page |
| backup-store-unreachable | api-factory dev: Postgres pod → object store partitioned (NetworkChaos), with writes | recovery-point SLO pages; `PostgresWalArchivingFailing` ticket; base-backup SLO quiet |
| backup-normal-writes | none: dev's database writing (WAL archived every 5 min) | no backup page or ticket (the negative test) |
| storefront-worker-one-down | one of two workers fails 10 min | nothing pages |
| storefront-worker-down | every worker fails | order-freshness and order-completeness page; order-intake quiet |
| storefront-worker-slow | Postgres → workers +2s per packet (~8s per order, still above arrivals) | nothing pages |
| storefront-worker-overloaded | Postgres → workers +8s per packet (below daytime arrivals) | order-freshness pages; completeness quiet |
| storefront-db-unreachable | Postgres → web and workers: every packet dropped | order-intake pages; freshness "SLI absent" ticket  — skipped: the absent ticket's 10m was too tight (superseded by -v2) |
| storefront-db-unreachable-v2 | the same | the same; the absent ticket within 13m |

Several people / agents can run experiments at once: the runner marks the namespace (annotation
`gitops-platform/experiment`, the lock: one experiment per namespace) and keeps it marked 30 min after the fault
(`…-cooldown`; burn-rate alerts can fire after recovery). A page in a marked namespace belongs to that experiment;
any other page fails every running experiment. A run killed hard leaves its mark; it expires after 4h, or
`kubectl annotate ns <ns> gitops-platform/experiment-`.

**Every fault ends by itself**, because the runner can die with its machine (2026-11-20: the Mac slept for an hour
with a fault switch on; staging answered 503 all along). Chaos Mesh objects need `spec.duration` ≥ the experiment's
time (`timeout`, `within`); a fault-switch ConfigMap gets `expires` (start + that time + 5 min) from the runner, which
the application honours; any other fault is refused, and so is a load Job without `activeDeadlineSeconds` (checked
by CI too).

**A run whose time did not flow is INVALID**, neither PASS nor FAIL: the wall clock jumped more than 1 min between two
polls (machine asleep, VM paused), or Prometheus has a hole of more than 2 min in the run or in the hour before it
(the 1h burn-rate windows; also right after a Prometheus restart). It is reported and recorded as invalid and says
nothing about the alerts. On macOS the runner keeps the machine awake while it runs (`caffeinate -i -s`): that does
not stop a closed lid (or a manual sleep), and it cannot help the in-cluster regression on kind — its container
does not run on macOS, and a sleeping Mac freezes the whole cluster (the gap check catches it).

Format: `hypothesis`, `namespace`, `effect` (a PromQL query that returns data once the fault bites: Chaos Mesh's
"injected" only means the rules are in place; required for a fault that is not a Chaos Mesh object), `fault` (Chaos Mesh
objects with `spec.duration`, or a fault-switch ConfigMap; created in the namespace and always deleted),
`load` (optional Kubernetes objects the fault needs to matter, e.g. a Job that writes; created first, deleted with the
fault), `timeout`, `hold` (keep going this long even when everything expected fired), `expect.fire` (`alert`, `labels`,
`within`), `expect.quiet` (must not page; an entry naming `severity` holds that severity quiet instead: a lab namespace with a fault every day keeps its 1d / 4d slow-burn tickets firing), `expect.may_fire` (allowed, not required: like `fire` without `within`; logged "~ also fired"), `recover` (the fired alerts resolve within this after the fault), `ignore` (namespaces whose
pages notify nobody), `skip` (why the scheduled regression cannot run it) and `clusters` (only
on these, e.g. `[kind]`; default: every lab cluster).

The SLO alerts come from Pyrra: `ErrorBudgetBurn` with labels `slo`, `namespace`, `severity` (page / ticket) and the
windows `long` / `short` (fast burn: `long: 1h`, 14× the budget rate, for 2m); `SLOMetricAbsent` (ticket).
Scheduled jobs (backups, restore check, off-site copy) have time-based SLOs that only turn bad after a day (`age`
max 26h / 3h): a failed nightly run cannot be watched end to end here; what fits in 30 minutes is the WAL
recovery point.

NetworkChaos with a `target` matches the target pods' IPs. A client reaching a database through its Service sends
packets to the Service IP (translated on the node, outside the pod), so a fault on the client side towards the
database pods does not match. Measured on kind (storefront-dev, 2026-11-20): +2s selected on the workers with target
Postgres left the order delay at 0.45s (normal); +2s selected on Postgres with target the workers gave 8.1s (4 round
trips). Select the database and target the clients: the replies carry the clients' pod IPs.

## Alert value report (monthly)

Experiments show that an alert fires when it must; the alert report shows what the alerts did the rest of the time:
which fired, how often and how long, whether they would notify anyone, and whether each firing was useful
(gitops-platform-blueprint `docs/slo.md` §4: "an unactionable page is a bug"; §8: the monthly review). Read-only:
Prometheus queries and, on the laptop, the alert-speaker's log. Reports go to `reports/alerts/` (one per cluster and
month, next to the human verdicts in `labels.yaml`), not to `results/`: they are a review of every alert over a
month, not the outcome of one experiment.

```sh
# once a month, per cluster (the clusters are not reachable from GitHub: run it locally), then commit the reports
uv run --with pyyaml python3 scripts/alert-report.py --cluster laptop --since 30d -o reports/alerts/$(date +%Y-%m)-laptop.md
uv run --with pyyaml python3 scripts/alert-report.py --cluster demo   --since 30d -o reports/alerts/$(date +%Y-%m)-demo.md
```

- **Episode**: `ALERTS{alertstate="firing"}` per alert (alertname, namespace, slo, severity, long window); firing runs
  less than `--gap` (5m) apart are one episode, the merged runs are counted (flapping).
- **Notified**: the cluster's Alertmanager routes and inhibit rules (`clusters/<cluster>/kustomization.yaml`, the
  version merged when the episode started, from git, + 3 min for Argo CD) applied to the episode and to what fired
  with it. On the laptop the speaker's log is the check ("heard N×"; disagreements are listed).
- **Class**, first match wins: a human label (`reports/alerts/labels.yaml`) → **experiment** (a run in that namespace:
  `results/*.md` from the first log line to the last + 30 min cooldown, the `slo_experiment_*` metrics, the
  namespace's current mark; expected / unexpected per its `expect`) → **incident** (independent evidence in the
  namespace from 15 min before the start to 15 min after: a restart, a running pod not ready, a scrape target down, a
  failed job, a new ReplicaSet = a rollout; never the metric the alert itself reads) → **noise** (resolved in < 3 min,
  flapping, so little traffic that a handful of bad events paged, an absent-metric alert right after Prometheus
  started) → **unknown** (the data cannot tell; label it).
- **Actionable share**: incidents among the notifications outside experiments; unknowns make it a range.

Blind spots: the results files name no cluster (`--results-cluster`, default demo, is where they count); Alertmanager's
own state (silences, repeat_interval, deduplication across a Prometheus restart) is not modelled; incident evidence
is namespace-wide, so a coincidence (a routine rollout) counts — the reasons are printed for a human to disagree;
Prometheus keeps 30 days, so the report cannot look further back. Tests: `scripts/test_alert_report.py` (CI: validate).

## Reliability scorecard

One page per review (blueprint `docs/slo.md` §8): per application, per prod SLO (and the platform's own SLOs) the
objective, the error budget left over 28d (Pyrra on the laptop), the runbook anchor and whether `docs/runbook.md` has
it, the non-skipped experiments whose `expect.fire` names the SLO with their last result (`results/*.md` or kind's
`slo_experiment_*`), and pages / tickets in 7d / 28d from `ALERTS`; then the gaps: untested or unverified pages (§5),
a failing or stale (`--stale`, 7d) experiment, a missing runbook (§4), budget < 25% or exhausted (§6), more than ~2
pages a week. Read-only; reports go to `reports/scorecard/<date>.md`. Tests: `scripts/test_scorecard.py` (CI: validate).

```sh
uv run -q --with pyyaml python3 scripts/scorecard.py -o reports/scorecard/$(date +%F).md   # laptop + kind (optional)
```

## Scheduled regression

On the lab clusters (kind, the demo cluster; never the laptop) every experiment runs daily, like unit tests for
alerting: `platform/experiments` (a CronJob, 09:00 JST) clones this repository at main and runs
`scripts/experiment-regression.py --cluster kind`. One queue per namespace, the queues in parallel; within a namespace
the negative tests first, then the faults, each one only when the namespace is free (no experiment of anyone, its
cooldown over, nothing paging there, none of its expected alerts firing, no Prometheus gap in the last hour) and marked 45 min after its fault. A full run takes ~9 h (storefront-dev: 7
experiments). Not run, and said so: `skip:`, `clusters:` without this cluster, a namespace that does not exist here.
The runner refuses to start unless every node is a kind node.

```sh
uv run --with pyyaml python3 scripts/experiment-regression.py --cluster kind --kubeconfig ~/.kube/kind-gitops.yaml --dry-run   # the plan
kubectl -n slo-experiments create job --from=cronjob/experiment-regression regression-$(date +%s)                           # a run now
kubectl -n slo-experiments get configmap experiment-results -o jsonpath='{.data.api-down\.json}'                             # last result
```

Results become metrics (`experiment-exporter`, from the ConfigMap `experiment-results`, which manual runs with
`--record slo-experiments/experiment-results` update too). Labels `experiment`, `namespace`, and per expected alert
`kind` (fire / quiet), `alert`, `slo`, `severity`, `long`: they join to the SLOs on `namespace` + `slo`.

| Metric | |
|---|---|
| `slo_experiment_last_success` | 1: the last valid run passed (invalid runs do not change it) |
| `slo_experiment_last_status{status}` | the last run, valid or not: pass / fail / invalid |
| `slo_experiment_last_invalid_timestamp_seconds` | when the last invalid run ended |
| `slo_experiment_last_run_timestamp_seconds`, `…_last_success_timestamp_seconds`, `…_last_duration_seconds` | when it last ran / passed, how long |
| `slo_experiment_expectation_met` | per expectation: fired in time (fire) or stayed quiet (quiet) |
| `slo_experiment_detection_seconds`, `slo_experiment_recovery_seconds` | fault → alert fired; fault removed → alert resolved |
| `slo_experiment_regression_status{status, reason}` | each experiment in the last scheduled run: pass / fail / invalid / error / skipped (reason) |
| `slo_experiment_regression_last_start_timestamp_seconds`, `…_last_finished_timestamp_seconds`, `slo_experiment_regression_experiments{status}` | the run itself |

"Is this SLO's page verified, and when": `slo_experiment_expectation_met{kind="fire", slo="…"} == 1` with
`slo_experiment_last_run_timestamp_seconds`.

Tickets (never pages): `SLOExperimentFailed` (an experiment's last run failed: until it passes, that alert is not
verified — read its report in the ConfigMap or the job's log), `SLOExperimentRegressionStale` (no run finished for 2
days: the lab was asleep, the CronJob broke, the exporter is down), `SLOExperimentRegressionBroken` (the job itself
failed: a crash, results not recorded, killed). A run killed mid-fault removes its faults (SIGTERM); a hard kill
leaves them to their `duration` and the namespace mark to its 4 h expiry.
