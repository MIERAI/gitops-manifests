# storefront-worker-overloaded: PASS

Like worker-slow, but every packet from Postgres to the workers is delayed by 8s: ~32s per order, ~3.7 orders/min for two workers, below dev's daytime arrivals (4-6/min in the compressed day's hours 8-20; run it then). Every order is at least 32s old when done, and the backlog grows. Prediction, written before the run: order-freshness pages within 15 min (stale for good after a few minutes, then 4.2 min of stale samples + for 2m). The backlog grows by ~1-2 orders/min, so the oldest order stays under the 5 min completeness deadline during the run: order-completeness quiet. web is not delayed: intake quiet. (At night arrivals are below capacity: the hypothesis does not hold then — that would be a different experiment.)

```
19:12:04 [storefront-worker-overloaded] hypothesis: Like worker-slow, but every packet from Postgres to the workers is delayed by 8s: ~32s per order, ~3.7 orders/min for two workers, below dev's daytime arrivals (4-6/min in the compressed day's hours 8-20; run it then). Every order is at least 32s old when done, and the backlog grows. Prediction, written before the run: order-freshness pages within 15 min (stale for good after a few minutes, then 4.2 min of stale samples + for 2m). The backlog grows by ~1-2 orders/min, so the oldest order stays under the 5 min completeness deadline during the run: order-completeness quiet. web is not delayed: intake quiet. (At night arrivals are below capacity: the hypothesis does not hold then — that would be a different experiment.)
19:12:04 [storefront-worker-overloaded] fault created in storefront-dev: NetworkChaos/worker-db-delay-8s
19:12:10 [storefront-worker-overloaded] ✓ fault injected (5s)
19:19:46 [storefront-worker-overloaded] ✓ fired after 7.7 min: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness', 'severity': 'page', 'long': '1h'}
19:19:51 [storefront-worker-overloaded] fault removed after 7.8 min
19:25:25 [storefront-worker-overloaded] ✓ resolved 5.6 min after the fault: ErrorBudgetBurn {'namespace': 'storefront-dev', 'slo': 'storefront-order-freshness', 'severity': 'page', 'long': '1h'}
19:25:25 [storefront-worker-overloaded] PASS
```
