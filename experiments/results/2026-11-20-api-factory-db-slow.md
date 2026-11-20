# api-factory-db-slow: FAIL

Every packet from Postgres to the API is delayed by 200ms (a slow network path to the database, an overloaded node). Each query needs one round trip (pgx QueryExecModeExec), so every RPC takes 200ms or more, far under the 3s request timeout: users get slow answers, not errors. rpc-latency (99% under 100ms) must page; rpc-availability and the health probe (a 1s database ping) must stay quiet. Prediction: the latency page after ~10.5 min (14x a 1% budget = 14% of the hour's RPCs slow, i.e. 8.4 min of it, plus for 2m), resolving ~5 min after the fault.

```
16:48:33 [api-factory-db-slow] hypothesis: Every packet from Postgres to the API is delayed by 200ms (a slow network path to the database, an overloaded node). Each query needs one round trip (pgx QueryExecModeExec), so every RPC takes 200ms or more, far under the 3s request timeout: users get slow answers, not errors. rpc-latency (99% under 100ms) must page; rpc-availability and the health probe (a 1s database ping) must stay quiet. Prediction: the latency page after ~10.5 min (14x a 1% budget = 14% of the hour's RPCs slow, i.e. 8.4 min of it, plus for 2m), resolving ~5 min after the fault.
16:48:33 [api-factory-db-slow] fault created in api-factory-prod: NetworkChaos/api-factory-db-delay
16:48:38 [api-factory-db-slow] ✓ fault injected (5s)
17:00:42 [api-factory-db-slow] ✗ unexpected page: ErrorBudgetBurn{namespace=storefront-dev, slo=storefront-order-intake, severity=page, long=1h, short=5m}
17:02:48 [api-factory-db-slow] fault removed after 14.2 min
17:02:48 [api-factory-db-slow] ✗ did not fire within 14m: ErrorBudgetBurn {'namespace': 'api-factory-prod', 'slo': 'api-factory-rpc-latency', 'severity': 'page', 'long': '1h'}
17:02:48 [api-factory-db-slow] FAIL
```
