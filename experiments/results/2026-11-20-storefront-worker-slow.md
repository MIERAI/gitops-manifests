# storefront-worker-slow: FAIL

The workers' path to the database is slow: every packet from Postgres to the workers is delayed by 2s (selected on the Postgres side: from the workers' side the packets go to the Service IP, which Chaos Mesh's pod-IP target does not match). A worker needs 4 round trips per order (begin, claim, done, commit): ~8s per order, ~15/min for two, above dev's arrivals (≤ 6/min; a 3x burst for one minute drains in about a minute). Slower, but within the objective: an order waits ≤ ~16s, far under the 60s freshness threshold and the 5 min completeness deadline. Prediction, written before the run: nothing pages (the order_processing_delay_seconds histogram shows the slowness: a dashboard, not a page). web is not delayed: intake quiet.

```
17:10:39 [storefront-worker-slow] hypothesis: The workers' path to the database is slow: every packet from Postgres to the workers is delayed by 2s (selected on the Postgres side: from the workers' side the packets go to the Service IP, which Chaos Mesh's pod-IP target does not match). A worker needs 4 round trips per order (begin, claim, done, commit): ~8s per order, ~15/min for two, above dev's arrivals (≤ 6/min; a 3x burst for one minute drains in about a minute). Slower, but within the objective: an order waits ≤ ~16s, far under the 60s freshness threshold and the 5 min completeness deadline. Prediction, written before the run: nothing pages (the order_processing_delay_seconds histogram shows the slowness: a dashboard, not a page). web is not delayed: intake quiet.
17:10:40 [storefront-worker-slow] fault created in storefront-dev: NetworkChaos/worker-db-delay
17:10:45 [storefront-worker-slow] ✓ fault injected (5s)
17:13:46 [storefront-worker-slow] ✗ must stay quiet but fired: ErrorBudgetBurn{namespace=storefront-dev, slo=storefront-order-intake, severity=page, long=6h, short=30m}
17:19:33 [storefront-worker-slow] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-dev, slo=api-factory-recovery-point, severity=page, long=1h, short=5m}
17:25:55 [storefront-worker-slow] fault removed after 15.3 min
17:25:55 [storefront-worker-slow] FAIL
```
