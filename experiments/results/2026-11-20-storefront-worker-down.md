# storefront-worker-down: FAIL

Every worker fails (a bad release, a crash loop). Orders are still accepted (web writes them) but nothing processes them. The worker's own metrics vanish with it; the pipeline SLIs are exported by web, so they must still see it. Prediction, written before the run: order-freshness pages — the next order after the failure (dev: one every 10-20s by day, minutes at night) is 60s old ~1.5 min later (check 10s + scrape 15s + rule 15s), then 4.2 min of stale samples make 7% of the hour (14x a 0.5% budget), plus for 2m: ~8 min by day. order-completeness pages too: orders become overdue 5 min after creation, the first one already exceeds 14x a 0.1% budget, plus for 2m: ~8 min. order-intake and the page SLOs stay quiet. After the fault the backlog drains in seconds.

```
18:02:13 [storefront-worker-down] hypothesis: Every worker fails (a bad release, a crash loop). Orders are still accepted (web writes them) but nothing processes them. The worker's own metrics vanish with it; the pipeline SLIs are exported by web, so they must still see it. Prediction, written before the run: order-freshness pages — the next order after the failure (dev: one every 10-20s by day, minutes at night) is 60s old ~1.5 min later (check 10s + scrape 15s + rule 15s), then 4.2 min of stale samples make 7% of the hour (14x a 0.5% budget), plus for 2m: ~8 min by day. order-completeness pages too: orders become overdue 5 min after creation, the first one already exceeds 14x a 0.1% budget, plus for 2m: ~8 min. order-intake and the page SLOs stay quiet. After the fault the backlog drains in seconds.
18:02:13 [storefront-worker-down] fault created in storefront-dev: PodChaos/worker-all-down
18:02:18 [storefront-worker-down] ✓ fault injected (5s)
18:08:05 [storefront-worker-down] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-health-probe, severity=page, long=1h, short=5m}
18:10:21 [storefront-worker-down] ✓ fired after 8.1 min: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness', 'severity': 'page', 'long': '1h'}
18:10:36 [storefront-worker-down] ✓ fired after 8.4 min: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-completeness', 'severity': 'page', 'long': '1h'}
18:10:46 [storefront-worker-down] fault removed after 8.6 min
18:15:32 [storefront-worker-down] ✓ resolved 4.8 min after the fault: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-completeness', 'severity': 'page', 'long': '1h'}
18:15:48 [storefront-worker-down] ✓ resolved 5.0 min after the fault: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness', 'severity': 'page', 'long': '1h'}
18:15:48 [storefront-worker-down] FAIL
```
