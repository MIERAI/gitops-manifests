# api-factory-db-partition-probes: FAIL

Same fault as api-factory-db-partition (Postgres unreachable from the API), after the probes stopped pinging the database (/livez, /readyz). Prediction, written before the run: the pods stay ready and are not restarted, so every RPC still reaches a pod and waits for a database connection until the 3s deadline. rpc-latency-severe (99.9% under 1s; 14 × 0.1% = 1.4% of the hour ≈ 1 min, plus for 2m) pages within ~4 min — before the health probe (~6.5 min). Not predicted: whether rpc-availability pages too — a request that times out is counted canceled (not bad) or deadline_exceeded (bad) depending on whether the caller or the server gives up first (both at 3s). Checked by hand afterwards: no container restarts in api-factory-prod during the fault.

Scheduled regression on kind (job regression-1795180832, gitops-manifests ed19bdc); log times UTC (+9h = JST). Both unexpected pages are correct in a total outage (every request SLO burns; Alertmanager groups per service: one announcement). Predictions left as written; superseded by api-factory-db-partition-probes-v2 (may_fire).

```
16:02:19 [api-factory-db-partition-probes] fault created in api-factory-prod: NetworkChaos/api-factory-db-partition
16:02:24 [api-factory-db-partition-probes] ✓ fault injected (5s)
16:02:39 [api-factory-db-partition-probes] ✓ fault takes effect (15s): max(probe_success{namespace="api-factory-prod", slo_probe="api-factory-health-probe"}) == 0
16:04:55 [api-factory-db-partition-probes] ✓ fired after 2.6 min: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-rpc-latency-severe', 'severity': 'page', 'long': '1h'}
16:04:55 [api-factory-db-partition-probes] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-availability, severity=page, long=1h, short=5m}
16:05:10 [api-factory-db-partition-probes] ✓ fired after 2.9 min: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
16:06:25 [api-factory-db-partition-probes] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-latency, severity=page, long=1h, short=5m}
16:10:32 [api-factory-db-partition-probes] fault removed after 8.2 min
16:15:48 [api-factory-db-partition-probes] ✓ resolved 5.3 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
16:16:03 [api-factory-db-partition-probes] ✓ resolved 5.5 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-rpc-latency-severe', 'severity': 'page', 'long': '1h'}
16:16:03 [api-factory-db-partition-probes] FAIL
```
