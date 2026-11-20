# storefront-worker-one-down (night run, 16:47): PASS

One of the two workers fails for 10 minutes (crash loop, a lost node). The other one keeps claiming orders (FOR UPDATE SKIP LOCKED: no coordination needed); an order the failed worker held is rolled back to pending and taken by the other. One worker handles ~10 orders/s (100ms of work), dev peaks at ~6/min: the backlog stays empty. Prediction, written before the run: nothing pages — freshness, completeness and intake stay quiet.

```
16:46:57 [storefront-worker-one-down] hypothesis: One of the two workers fails for 10 minutes (crash loop, a lost node). The other one keeps claiming orders (FOR UPDATE SKIP LOCKED: no coordination needed); an order the failed worker held is rolled back to pending and taken by the other. One worker handles ~10 orders/s (100ms of work), dev peaks at ~6/min: the backlog stays empty. Prediction, written before the run: nothing pages — freshness, completeness and intake stay quiet.
16:46:57 [storefront-worker-one-down] fault created in storefront-dev: PodChaos/worker-one-down
16:47:02 [storefront-worker-one-down] ✓ fault injected (5s)
16:57:05 [storefront-worker-one-down] fault removed after 10.1 min
16:57:05 [storefront-worker-one-down] PASS
```
