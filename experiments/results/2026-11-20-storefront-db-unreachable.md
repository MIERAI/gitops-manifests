# storefront-db-unreachable: FAIL

Postgres cannot be reached by web and the workers (every packet from Postgres to them is dropped; Postgres itself and its probes are fine). POST /orders cannot store orders: 503 after the 2s timeout. The page (GET /) does not use the database. Prediction, written before the run: order-intake pages (100% errors: 4.2 min to reach 7% of the hour, plus for 2m: ~7 min). The freshness gauge is unset while web cannot ask the database, so order-freshness does not burn; its SLI goes absent → SLOMetricAbsent (ticket) after ~6.5 min (for 6m). order-completeness stays quiet (nothing is created, nothing is checked). web-availability and web-probe stay quiet.

```
16:57:19 [storefront-db-unreachable] hypothesis: Postgres cannot be reached by web and the workers (every packet from Postgres to them is dropped; Postgres itself and its probes are fine). POST /orders cannot store orders: 503 after the 2s timeout. The page (GET /) does not use the database. Prediction, written before the run: order-intake pages (100% errors: 4.2 min to reach 7% of the hour, plus for 2m: ~7 min). The freshness gauge is unset while web cannot ask the database, so order-freshness does not burn; its SLI goes absent → SLOMetricAbsent (ticket) after ~6.5 min (for 6m). order-completeness stays quiet (nothing is created, nothing is checked). web-availability and web-probe stay quiet.
16:57:19 [storefront-db-unreachable] fault created in storefront-dev: NetworkChaos/db-unreachable
16:57:24 [storefront-db-unreachable] ✓ fault injected (5s)
17:00:40 [storefront-db-unreachable] ✓ fired after 3.3 min: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-intake', 'severity': 'page', 'long': '1h'}
17:03:41 [storefront-db-unreachable] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-latency, severity=page, long=1h, short=5m}
17:05:42 [storefront-db-unreachable] ✓ fired after 8.4 min: SLOMetricAbsent {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness'}
17:05:47 [storefront-db-unreachable] fault removed after 8.5 min
17:08:17 [storefront-db-unreachable] ✓ resolved 2.5 min after the fault: SLOMetricAbsent {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness'}
17:10:18 [storefront-db-unreachable] ✓ resolved 4.5 min after the fault: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-intake', 'severity': 'page', 'long': '1h'}
17:10:18 [storefront-db-unreachable] FAIL
```
