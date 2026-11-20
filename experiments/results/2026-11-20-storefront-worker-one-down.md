# storefront-worker-one-down: PASS

One of the two workers fails for 10 minutes (crash loop, a lost node). The other one keeps claiming orders (FOR UPDATE SKIP LOCKED: no coordination needed); an order the failed worker held is rolled back to pending and taken by the other. One worker handles ~10 orders/s (100ms of work), dev peaks at ~6/min: the backlog stays empty. Prediction, written before the run: nothing pages — freshness, completeness and intake stay quiet.

```
20:03:03 [storefront-worker-one-down] hypothesis: One of the two workers fails for 10 minutes (crash loop, a lost node). The other one keeps claiming orders (FOR UPDATE SKIP LOCKED: no coordination needed); an order the failed worker held is rolled back to pending and taken by the other. One worker handles ~10 orders/s (100ms of work), dev peaks at ~6/min: the backlog stays empty. Prediction, written before the run: nothing pages — freshness, completeness and intake stay quiet.
20:03:03 [storefront-worker-one-down] fault created in storefront-dev: PodChaos/worker-one-down
20:03:08 [storefront-worker-one-down] ✓ fault injected (5s)
20:13:16 [storefront-worker-one-down] fault removed after 10.2 min
20:13:16 [storefront-worker-one-down] PASS
```
