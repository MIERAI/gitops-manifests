# api-factory-db-partition: FAIL

The API cannot reach Postgres at all (network partition between the API pods and the database pods): every RPC fails, users are fully down, so a page is required within 10 minutes. Prediction, written before the run: the in-pod rpc-availability SLO does NOT page. The readiness probe (/healthz pings the database) takes every pod out of the Service within ~15s, after which RPCs are refused by the Service and never counted; the ~15s of counted failures are well under 14x the 0.1% budget over 1h. The health probe calls /healthz through the Service, sees every call fail and pages after ~6.5 min (14x a 0.5% budget = 7% of the hour, 4.2 min, plus for 2m). Also predicted: the liveness probe (same /healthz) restarts the API containers every ~1 min, so the in-pod series vanish and SLOMetricAbsent (ticket) follows after ~12 min; after the fault, restart back-off delays recovery by up to a few minutes.

Scheduled regression on kind (job regression-1795180832, gitops-manifests ed19bdc); log times UTC (+9h = JST). Superseded: its predictions describe the probes that pinged the database; since /livez, /readyz the two pages it calls unexpected are the desired behaviour (api-factory-db-partition-probes). Not run by the regression any more.

```
15:04:59 [api-factory-db-partition] fault created in api-factory-prod: NetworkChaos/api-factory-db-partition
15:05:04 [api-factory-db-partition] ✓ fault injected (5s)
15:08:36 [api-factory-db-partition] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-latency-severe, severity=page, long=1h, short=5m}
15:08:36 [api-factory-db-partition] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-availability, severity=page, long=1h, short=5m}
15:11:52 [api-factory-db-partition] ✓ fired after 6.9 min: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
15:11:57 [api-factory-db-partition] fault removed after 7.0 min
15:17:13 [api-factory-db-partition] ✓ resolved 5.3 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-health-probe', 'severity': 'page', 'long': '1h'}
15:17:13 [api-factory-db-partition] FAIL
```
