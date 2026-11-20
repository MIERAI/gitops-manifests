# api-factory-db-partition-probes: FAIL

Same fault as api-factory-db-partition (Postgres unreachable from the API), after the probes stopped pinging the database (/livez, /readyz). Prediction, written before the run: the pods stay ready and are not restarted, so every RPC still reaches a pod and waits for a database connection until the 3s deadline. rpc-latency-severe (99.9% under 1s; 14 × 0.1% = 1.4% of the hour ≈ 1 min, plus for 2m) pages within ~4 min — before the health probe (~6.5 min). Not predicted: whether rpc-availability pages too — a request that times out is counted canceled (not bad) or deadline_exceeded (bad) depending on whether the caller or the server gives up first (both at 3s). Checked by hand afterwards: no container restarts in api-factory-prod during the fault.

```
20:30:01 [api-factory-db-partition-probes] hypothesis: Same fault as api-factory-db-partition (Postgres unreachable from the API), after the probes stopped pinging the database (/livez, /readyz). Prediction, written before the run: the pods stay ready and are not restarted, so every RPC still reaches a pod and waits for a database connection until the 3s deadline. rpc-latency-severe (99.9% under 1s; 14 × 0.1% = 1.4% of the hour ≈ 1 min, plus for 2m) pages within ~4 min — before the health probe (~6.5 min). Not predicted: whether rpc-availability pages too — a request that times out is counted canceled (not bad) or deadline_exceeded (bad) depending on whether the caller or the server gives up first (both at 3s). Checked by hand afterwards: no container restarts in api-factory-prod during the fault.
20:30:01 [api-factory-db-partition-probes] fault created in api-factory-prod: NetworkChaos/api-factory-db-partition
20:30:07 [api-factory-db-partition-probes] ✓ fault injected (5s)
20:30:22 [api-factory-db-partition-probes] ✓ fault takes effect (15s): max(probe_success{namespace="api-factory-prod", slo_probe="api-factory-health-probe"}) == 0
20:32:24 [api-factory-db-partition-probes] ✓ fired after 2.4 min: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-rpc-latency-severe', 'severity': 'page', 'long': '1h'}
20:32:39 [api-factory-db-partition-probes] ✓ fired after 2.6 min: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
20:32:54 [api-factory-db-partition-probes] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-availability, severity=page, long=1h, short=5m}
20:33:09 [api-factory-db-partition-probes] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-latency, severity=page, long=1h, short=5m}
20:38:19 [api-factory-db-partition-probes] fault removed after 8.3 min
20:43:08 [api-factory-db-partition-probes] ✓ resolved 4.8 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
20:43:23 [api-factory-db-partition-probes] ✓ resolved 5.1 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-rpc-latency-severe', 'severity': 'page', 'long': '1h'}
20:43:23 [api-factory-db-partition-probes] FAIL
```

## Reading (after the run)

The change works: **0 container restarts** (before: 5 per pod in 6.7 min), the pods stayed ready, every RPC reached
a pod. rpc-latency-severe paged first (2.4 min), then the probe (2.6 min) — the predicted order; the times are
short because Prometheus history restarted ~1h earlier (a young SLO burns faster).

The runner's FAIL: rpc-availability and rpc-latency paged too. The prediction left rpc-availability open but an
experiment cannot say "allowed" yet (only fire / quiet); both firing is correct — the in-pod SLIs now see a database
outage, which was the point. The finding is elsewhere: **one outage, four pages** (4 SLOs). The laptop's
Alertmanager grouped by slo since #81; it should group by service (namespace): one incident, one announcement.
